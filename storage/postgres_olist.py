"""在 PostgreSQL 独立样本表中导入与读取 Olist 订单。"""

from collections.abc import Iterable

import psycopg

from app.orders.logistics import LogisticsOrder, ReceiptStatusFilter
from app.orders.olist_items import OrderItem
from app.settings import ROOT
from app.storage.postgres_orders import OrderStoreError


INSERT_ITEMS = (
    "INSERT INTO public.olist_order_items (source_order_id, item_no, product_id, category, price, freight) "
    "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (source_order_id, item_no) DO NOTHING"
)


def item_values(rows: Iterable[tuple[str, OrderItem]]):
    """按同一 SQL 列顺序生成明细参数。"""
    return ((source_id, item.item_no, item.product_id, item.category, item.price, item.freight)
            for source_id, item in rows)


class PostgresOlistOrders:
    """按稳定查询编号读取样本，并以源订单编号去重。"""

    def __init__(self, database_url: str):
        """保存连接配置，实际查询与导入分别开启短连接。"""
        self.database_url = database_url

    def get_order(self, order_id: str) -> LogisticsOrder | None:
        """查找历史样本，数据库故障时返回可读错误。"""
        try:
            with psycopg.connect(
                self.database_url, connect_timeout=3,
                options="-c statement_timeout=4000 -c default_transaction_read_only=on",
            ) as connection:
                row = connection.execute(
                    "SELECT source_order_id, source_status, purchased_on, estimated_on, delivered_on, "
                    "COALESCE(to_jsonb(o)->'seller_ids', '[]'::jsonb) "
                    "FROM public.olist_orders AS o WHERE order_id = %s", (order_id,),
                ).fetchone()
        except psycopg.errors.UndefinedTable:
            raise OrderStoreError("请先导入 Olist 物流样本。") from None
        except psycopg.Error:
            raise OrderStoreError("暂时无法查询 Olist 样本，请检查订单数据库。") from None
        return LogisticsOrder(*row[:5], tuple(row[5])) if row is not None else None

    def source_order_ids(self) -> set[str]:
        """读取现有物流样本的源订单号，限定明细导入范围。"""
        try:
            with psycopg.connect(
                self.database_url, connect_timeout=3,
                options="-c statement_timeout=4000 -c default_transaction_read_only=on",
            ) as connection:
                return {row[0] for row in connection.execute("SELECT source_order_id FROM public.olist_orders")}
        except psycopg.Error:
            raise OrderStoreError("无法读取现有物流样本，请先导入 Olist 订单。") from None

    def list_orders(self, suffixes: str, after: str, limit: int, prefix: str = "",
                    receipt: ReceiptStatusFilter = "all") -> list[LogisticsOrder]:
        """在游标分页前应用订单归属、编号和签收状态。"""
        try:
            with psycopg.connect(
                self.database_url, connect_timeout=3,
                options="-c statement_timeout=4000 -c default_transaction_read_only=on",
            ) as connection:
                rows = connection.execute(
                    "SELECT source_order_id, source_status, purchased_on, estimated_on, delivered_on, "
                    "COALESCE(to_jsonb(o)->'seller_ids', '[]'::jsonb) "
                    'FROM public.olist_orders AS o WHERE order_id COLLATE "C" > %s AND right(order_id, 1) = ANY(%s) '
                    'AND starts_with(order_id, %s) '
                    "AND CASE %s "
                    "WHEN 'all' THEN TRUE "
                    "WHEN 'signed' THEN source_status = 'delivered' AND delivered_on IS NOT NULL "
                    "WHEN 'not_signed' THEN source_status = 'shipped' AND delivered_on IS NULL "
                    "WHEN 'unknown' THEN source_status = 'delivered' AND delivered_on IS NULL "
                    "WHEN 'cancelled' THEN source_status = 'canceled' ELSE FALSE END "
                    'ORDER BY order_id COLLATE "C" LIMIT %s',
                    (after, list(suffixes), prefix, receipt, limit),
                ).fetchall()
        except psycopg.errors.UndefinedTable:
            return []
        except psycopg.Error:
            raise OrderStoreError("暂时无法读取 Olist 订单列表。") from None
        return [LogisticsOrder(*row[:5], tuple(row[5])) for row in rows]

    def get_items(self, order_id: str, *, allow_unimported: bool = False) -> list[OrderItem] | None:
        """一次连接查询订单及其明细，区分未知订单和缺少明细。"""
        try:
            with psycopg.connect(
                self.database_url, connect_timeout=3,
                options="-c statement_timeout=4000 -c default_transaction_read_only=on",
            ) as connection:
                rows = connection.execute(
                    "SELECT i.item_no, i.product_id, i.category, i.price, i.freight "
                    "FROM public.olist_orders AS o LEFT JOIN public.olist_order_items AS i USING (source_order_id) "
                    "WHERE o.order_id = %s ORDER BY i.item_no", (order_id,),
                ).fetchall()
        except psycopg.errors.UndefinedTable:
            if allow_unimported:
                return []
            raise OrderStoreError("请先导入 Olist 商品明细。") from None
        except psycopg.Error:
            raise OrderStoreError("暂时无法查询 Olist 商品明细，请检查订单数据库。") from None
        return [OrderItem(*row) for row in rows if row[0] is not None] if rows else None

    def import_items(self, rows: Iterable[tuple[str, OrderItem]]) -> int:
        """在同一事务内准备商品表并写入尚未导入的明细。"""
        schema = (ROOT / "sql" / "create_olist_order_items.sql").read_text(encoding="utf-8")
        try:
            with psycopg.connect(
                self.database_url, connect_timeout=3, options="-c statement_timeout=15000",
            ) as connection:
                # 同一时间只允许一个明细导入进程负责初始化并写入。
                connection.execute("SELECT pg_advisory_xact_lock(508607651983)")
                connection.execute(schema)
                with connection.cursor() as cursor:
                    cursor.executemany(INSERT_ITEMS, item_values(rows))
                    return cursor.rowcount
        except psycopg.Error:
            raise OrderStoreError("Olist 商品明细导入失败，事务已回滚。") from None

    def import_sellers(self, groups: dict[str, set[str]]) -> int:
        """根据源商品行补充已有订单的卖家关系，返回有变化的订单数。"""
        schema = (ROOT / "sql" / "add_olist_sellers.sql").read_text(encoding="utf-8")
        rows = [{"sellers": sorted(sellers), "source_id": source_id}
                for source_id, sellers in sorted(groups.items()) if sellers]
        try:
            with psycopg.connect(self.database_url, connect_timeout=3, options="-c statement_timeout=15000") as connection:
                connection.execute("SELECT pg_advisory_xact_lock(508607651983)")
                connection.execute(schema)
                with connection.cursor() as cursor:
                    cursor.executemany(
                        "UPDATE public.olist_orders SET seller_ids = %(sellers)s WHERE source_order_id = %(source_id)s "
                        "AND seller_ids IS DISTINCT FROM %(sellers)s", rows,
                    )
                    return cursor.rowcount
        except psycopg.Error:
            raise OrderStoreError("无法补充 Olist 卖家关系，事务已回滚。") from None

    def import_orders(self, orders: Iterable[LogisticsOrder]) -> int:
        """在同一事务中建表和插入样本，返回实际新增行数。"""
        schema = (ROOT / "sql" / "create_olist_orders.sql").read_text(encoding="utf-8")
        try:
            with psycopg.connect(
                self.database_url, connect_timeout=3, options="-c statement_timeout=15000",
            ) as connection:
                connection.execute(schema)
                with connection.cursor() as cursor:
                    cursor.executemany(
                        "INSERT INTO public.olist_orders "
                        "(source_order_id, order_id, source_status, purchased_on, estimated_on, delivered_on) "
                        "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (source_order_id) DO NOTHING",
                        ((order.source_order_id, order.order_id, order.source_status, order.purchased_on,
                          order.estimated_on, order.delivered_on) for order in orders),
                    )
                    return cursor.rowcount
        except psycopg.Error:
            raise OrderStoreError("Olist 样本导入失败，事务已回滚。请检查数据库连接和样本编号。") from None

    def import_dataset(self, orders: list[LogisticsOrder], items: list[tuple[str, OrderItem]]) -> tuple[int, int]:
        """把所有可处理订单、卖家和商品明细一次提交，保留现有订单与工单关联。"""
        schemas = [(ROOT / "sql" / name).read_text(encoding="utf-8") for name in (
            "create_olist_orders.sql", "add_olist_sellers.sql", "create_olist_order_items.sql",
        )]
        try:
            with psycopg.connect(self.database_url, connect_timeout=3, options="-c statement_timeout=30000") as connection:
                connection.execute("SELECT pg_advisory_xact_lock(508607651983)")
                for schema in schemas:
                    connection.execute(schema)
                with connection.cursor() as cursor:
                    cursor.executemany(
                        "INSERT INTO public.olist_orders AS o "
                        "(source_order_id, order_id, source_status, purchased_on, estimated_on, delivered_on, seller_ids) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (source_order_id) "
                        "DO UPDATE SET seller_ids = EXCLUDED.seller_ids "
                        "WHERE cardinality(EXCLUDED.seller_ids) > 0 AND o.seller_ids IS DISTINCT FROM EXCLUDED.seller_ids",
                        ((order.source_order_id, order.order_id, order.source_status, order.purchased_on,
                          order.estimated_on, order.delivered_on, list(order.seller_ids)) for order in orders),
                    )
                    changed = cursor.rowcount
                    cursor.executemany(INSERT_ITEMS, item_values(items))
                    return changed, cursor.rowcount
        except psycopg.Error:
            raise OrderStoreError("Olist 数据集未提交，请检查本机数据库和表约束。") from None
