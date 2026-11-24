"""读写脱敏京东历史订单，接入现有查单和工单。"""

import psycopg

from app.orders.logistics import JD_CHANNEL_LABELS, LogisticsOrder, ReceiptStatusFilter
from app.orders.olist_items import OrderItem
from app.settings import ROOT
from app.storage.postgres_orders import OrderStoreError


COLUMNS = "source_key, source_status, purchased_on, estimated_on, delivered_on, snapshot_on, channel"


class PostgresJDOrders:
    """使用参数化 SQL 按编号或买家分配范围查询国内历史订单。"""

    def __init__(self, database_url: str):
        """保存连接地址，按查询开启只读连接。"""
        self.database_url = database_url

    def _read(self, sql: str, args: tuple) -> list[tuple]:
        """在默认只读事务中读取，允许尚未导入数据的安装。"""
        try:
            with psycopg.connect(self.database_url, connect_timeout=3,
                                 options="-c statement_timeout=4000 -c default_transaction_read_only=on") as conn:
                return conn.execute(sql, args).fetchall()
        except psycopg.errors.UndefinedTable:
            return []
        except psycopg.Error:
            raise OrderStoreError("暂时无法查询京东历史订单。") from None

    @staticmethod
    def _order(row: tuple) -> LogisticsOrder:
        """用已存储的来源、可证实日期和渠道重建订单。"""
        return LogisticsOrder(*row[:5], origin="jd", snapshot_on=row[5], channel=row[6])

    def get_order(self, order_id: str) -> LogisticsOrder | None:
        """用本地稳定编号查询一笔订单。"""
        rows = self._read(f"SELECT {COLUMNS} FROM public.jd_research_orders WHERE order_id = %s", (order_id,))
        return self._order(rows[0]) if rows else None

    def list_orders(self, suffixes: str, after: str, limit: int, prefix: str = "",
                    receipt: ReceiptStatusFilter = "all") -> list[LogisticsOrder]:
        """在游标分页前应用本机买家归属、订单搜索和状态过滤。"""
        rows = self._read(
            f'SELECT {COLUMNS} FROM public.jd_research_orders WHERE order_id COLLATE "C" > %s '
            'AND right(order_id, 1) = ANY(%s) AND starts_with(order_id, %s) '
            "AND CASE %s WHEN 'all' THEN TRUE WHEN 'signed' THEN delivered_on IS NOT NULL "
            "WHEN 'unknown' THEN delivered_on IS NULL ELSE FALSE END "
            'ORDER BY order_id COLLATE "C" LIMIT %s',
            (after, list(suffixes), prefix, receipt, limit),
        )
        return [self._order(row) for row in rows]

    def get_items(self, order_id: str) -> list[OrderItem] | None:
        """读取匿名 SKU、数量和人民币成交价，缺少运费时保留为空。"""
        rows = self._read(
            "SELECT i.item_no, i.sku_id, i.quantity, i.unit_price, i.sale_channel "
            "FROM public.jd_research_orders o LEFT JOIN public.jd_research_items i USING (order_id) "
            "WHERE o.order_id = %s ORDER BY i.item_no", (order_id,),
        )
        if not rows:
            return None
        return [OrderItem(no, sku, None, price, None, qty, f"商品编号 {sku.upper()} · {JD_CHANNEL_LABELS[channel]}")
                for no, sku, qty, price, channel in rows if no is not None]

    def import_records(self, orders: list[LogisticsOrder], items: list[tuple]) -> tuple[int, int]:
        """原子导入主订单、配送证据和明细，重复运行保留现有工单。"""
        schema = (ROOT / "sql/create_jd_orders.sql").read_text(encoding="utf-8")
        try:
            with psycopg.connect(self.database_url, connect_timeout=3, options="-c statement_timeout=30000") as conn:
                conn.execute("SELECT pg_advisory_xact_lock(260211970522)")
                conn.execute(schema)
                with conn.cursor() as cursor:
                    cursor.executemany("INSERT INTO public.orders(order_id, signed_on) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                                       ((order.order_id, order.delivered_on) for order in orders))
                    cursor.executemany(
                        f"INSERT INTO public.jd_research_orders (order_id, {COLUMNS}) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (source_key) DO NOTHING",
                        ((order.order_id, order.source_order_id, order.source_status, order.purchased_on, order.estimated_on,
                          order.delivered_on, order.snapshot_on, order.channel) for order in orders),
                    )
                    inserted = cursor.rowcount
                    cursor.executemany(
                        "INSERT INTO public.jd_research_items (order_id, item_no, sku_id, quantity, unit_price, sale_channel) "
                        "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (order_id,item_no) DO NOTHING", items,
                    )
                    return inserted, cursor.rowcount
        except psycopg.Error:
            raise OrderStoreError("京东数据未提交，请检查数据库与记录约束。") from None
