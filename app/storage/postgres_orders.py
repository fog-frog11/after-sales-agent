"""从 PostgreSQL 读取售后订单。"""

from app.orders.rules import Order, ReturnPolicy


class OrderStoreError(Exception):
    """订单数据源不可用。"""


class PostgresOrders:
    """提供只读的 PostgreSQL 订单及店铺政策查询。"""

    def __init__(self, database_url: str):
        """保存数据库连接地址，查询时再建立短生命周期连接。"""
        self.database_url = database_url

    def get_order(self, order_id: str) -> Order | None:
        """按订单号查询签收日期和所属店铺，缺少记录时返回空值。"""
        record = self._fetch_one(
            "SELECT order_id, signed_on, shop_id FROM public.orders WHERE order_id = %s",
            order_id,
        )
        return Order(*record) if record is not None else None

    def list_orders(self, order_ids: list[str]) -> list[Order]:
        """批量读取已由服务层确定归属的店铺订单。"""
        rows = self._fetch_rows(
            "SELECT order_id, signed_on, shop_id FROM public.orders WHERE order_id = ANY(%s)",
            (order_ids,),
        )
        return [Order(*row) for row in rows]

    def get_policy(self, shop_id: str) -> ReturnPolicy | None:
        """按店铺编号读取政策名称和退货期限。"""
        record = self._fetch_one(
            "SELECT shop_name, policy_name, return_window_days "
            "FROM public.shop_return_policies WHERE shop_id = %s",
            shop_id,
        )
        return ReturnPolicy(*record) if record is not None else None

    def _fetch_one(self, sql: str, value: str) -> tuple | None:
        """读取按唯一编号查询的结果。"""
        return next(iter(self._fetch_rows(sql, (value,))), None)

    def _fetch_rows(self, sql: str, parameters: tuple) -> list[tuple]:
        """在只读连接执行参数化查询，并把数据库错误转成业务错误。"""
        try:
            import psycopg
        except ImportError:
            raise OrderStoreError("缺少 PostgreSQL 驱动，请安装项目依赖。") from None

        try:
            with psycopg.connect(
                self.database_url,
                connect_timeout=3,
                options="-c statement_timeout=4000 -c default_transaction_read_only=on",
            ) as connection:
                return connection.execute(sql, parameters).fetchall()
        except psycopg.Error:
            raise OrderStoreError("订单数据库不可用，请检查连接和订单表。") from None
