"""将店铺售后申请和答复保存在 PostgreSQL。"""

import psycopg
from psycopg.rows import dict_row

from app.tickets.workflow import TicketStoreError


FIELDS = "order_id, reason, submitted_at, resolution, resolved_at"
FIND = f"SELECT {FIELDS} FROM public.merchant_requests WHERE shop_id = %s AND order_id = %s"


class PostgresMerchantRequests:
    """把店铺售后申请与平台工单分别存储。"""

    def __init__(self, database_url: str):
        """保存订单数据库的连接配置。"""
        self.database_url = database_url

    def _connect(self):
        """为一次查询或事务建立带超时的连接。"""
        return psycopg.connect(self.database_url, connect_timeout=3,
                               options="-c statement_timeout=4000", row_factory=dict_row)

    def get(self, shop_id: str, order_id: str) -> dict | None:
        """读取指定店铺受理的订单申请。"""
        try:
            with self._connect() as connection:
                return connection.execute(FIND, (shop_id, order_id)).fetchone()
        except psycopg.Error:
            raise TicketStoreError("暂时无法读取店铺售后申请。") from None

    def submit(self, shop_id: str, order_id: str, reason: str) -> dict:
        """提交一笔店铺申请，重复提交时返回已有记录。"""
        try:
            with self._connect() as connection:
                request = connection.execute(
                    f"""INSERT INTO public.merchant_requests (shop_id, order_id, reason)
                    VALUES (%s, %s, %s) ON CONFLICT (order_id) DO NOTHING RETURNING {FIELDS}""",
                    (shop_id, order_id, reason),
                ).fetchone()
                if request is None:
                    request = connection.execute(FIND, (shop_id, order_id)).fetchone()
                if request is not None:
                    return request
        except psycopg.Error:
            raise TicketStoreError("暂时无法提交店铺售后申请。") from None
        raise TicketStoreError("订单的售后申请与当前店铺资料不一致。")

    def list(self, shop_id: str, *, resolved: bool, after: str, limit: int) -> list[dict]:
        """按店铺和处理状态分页读取申请。"""
        try:
            with self._connect() as connection:
                return connection.execute(
                    f"""SELECT {FIELDS} FROM public.merchant_requests
                    WHERE shop_id = %s AND (resolved_at IS NOT NULL) = %s AND order_id > %s
                    ORDER BY order_id LIMIT %s""",
                    (shop_id, resolved, after, limit),
                ).fetchall()
        except psycopg.Error:
            raise TicketStoreError("暂时无法读取店铺售后队列。") from None

    def resolve(self, shop_id: str, order_id: str, resolution: str, actor: str) -> dict | None:
        """只允许所属店铺为待处理申请保存一次答复。"""
        try:
            with self._connect() as connection:
                return connection.execute(
                    f"""UPDATE public.merchant_requests SET resolution = %s, resolved_at = now(), resolved_by = %s
                    WHERE shop_id = %s AND order_id = %s AND resolved_at IS NULL RETURNING {FIELDS}""",
                    (resolution, actor, shop_id, order_id),
                ).fetchone()
        except psycopg.Error:
            raise TicketStoreError("暂时无法保存店铺处理结果。") from None
