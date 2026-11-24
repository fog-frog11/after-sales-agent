"""在 PostgreSQL 保存工单、读取进度并记录人工处理。"""

from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.orders.logistics import OLIST_ORDER_PATTERN
from app.settings import ROOT
from app.tickets.handoff import TicketHandoff
from app.tickets.workflow import (
    OrderTicketRecord, TicketDetailRecord, TicketRecord, TicketSearchRecord, TicketStatusFilter, TicketStoreError,
)


TICKET_COLUMNS = "ticket_no, COALESCE(olist_order_id, order_id) AS order_id, reason, status"
DETAIL_COLUMNS = TICKET_COLUMNS + ", resolution, resolved_at"
SELECT_TICKET = f"""SELECT {TICKET_COLUMNS} FROM public.support_tickets
    WHERE confirmation_id = %s OR ticket_no = (
        SELECT ticket_no FROM public.support_ticket_confirmation_aliases WHERE confirmation_id = %s
    )"""


def initialize_olist_ticket_schema(connection: psycopg.Connection) -> None:
    """在同一事务内准备 Olist 证据表和工单关联，支持重新运行。"""
    connection.execute("SELECT pg_advisory_xact_lock(508607651983)")
    for name in ("create_olist_orders.sql", "create_olist_order_items.sql", "link_olist_support_tickets.sql",
                 "create_ticket_confirmation_aliases.sql", "add_ticket_handoff.sql"):
        connection.execute((ROOT / "sql" / name).read_text(encoding="utf-8"))


class PostgresTickets:
    """管理工单查询、幂等创建和待处理状态的更新。"""

    def __init__(self, database_url: str):
        """保存售后数据库的连接地址，供每次操作使用。"""
        self.database_url = database_url

    def _connect(self):
        """建立短生命周期连接，为连接和 SQL 执行设置超时。"""
        return psycopg.connect(
            self.database_url, connect_timeout=3, options="-c statement_timeout=4000",
            row_factory=dict_row,
        )

    def get(self, confirmation_id: UUID) -> TicketRecord | None:
        """按确认 ID 读取持久化工单，供重复确认时复用。"""
        try:
            with self._connect() as connection:
                return connection.execute(SELECT_TICKET, (confirmation_id, confirmation_id)).fetchone()
        except psycopg.Error:
            raise TicketStoreError("暂时无法读取人工工单，请稍后重试。") from None

    def find_olist_ticket(self, order_id: str) -> TicketRecord | None:
        """优先返回待处理工单，否则返回最新一张已记录的工单。"""
        return self.find_order_ticket(order_id)

    @staticmethod
    def _find_order_ticket(connection, order_id: str, pending_only: bool) -> OrderTicketRecord | None:
        """按订单外键取一张工单，同状态优先取最新的记录。"""
        column = "olist_order_id" if OLIST_ORDER_PATTERN.fullmatch(order_id) else "order_id"
        return connection.execute(
            f"""SELECT {DETAIL_COLUMNS}, created_at FROM public.support_tickets
                WHERE {column} = %s AND (NOT %s OR status = 'pending')
                ORDER BY (status = 'pending') DESC, created_at DESC, ticket_no DESC LIMIT 1""",
            (order_id, pending_only),
        ).fetchone()

    def find_order_ticket(self, order_id: str, *, pending_only: bool = False) -> OrderTicketRecord | None:
        """读取买家订单的当前平台处理进度。"""
        try:
            with self._connect() as connection:
                return self._find_order_ticket(connection, order_id, pending_only)
        except psycopg.Error:
            raise TicketStoreError("暂时无法查找已有平台工单，请稍后重试。") from None

    def _reuse_order_ticket(self, connection, confirmation_id: UUID, order_id: str,
                            is_olist: bool) -> TicketRecord | None:
        """串行确认同一订单，复用已有工单并登记可重试的确认 ID。"""
        table = "olist_orders" if is_olist else "orders"
        connection.execute(f"SELECT order_id FROM public.{table} WHERE order_id = %s FOR NO KEY UPDATE", (order_id,))
        known = connection.execute(SELECT_TICKET, (confirmation_id, confirmation_id)).fetchone()
        if known is not None:
            return known
        existing = self._find_order_ticket(connection, order_id, pending_only=not is_olist)
        if existing is not None:
            connection.execute("INSERT INTO public.support_ticket_confirmation_aliases (confirmation_id, ticket_no) "
                               "VALUES (%s, %s)", (confirmation_id, existing["ticket_no"]))
        return existing

    def create(self, confirmation_id: UUID, order_id: str, reason: str,
               *, handoff: TicketHandoff | None = None) -> TicketRecord:
        """写入一张待处理工单，遇到重复确认时查询原记录。"""
        is_olist = OLIST_ORDER_PATTERN.fullmatch(order_id) is not None
        demo_id, olist_id = (None, order_id) if is_olist else (order_id, None)
        try:
            with self._connect() as connection:
                existing = self._reuse_order_ticket(connection, confirmation_id, order_id, is_olist)
                if existing is not None:
                    return existing
                record = connection.execute(
                    f"""INSERT INTO public.support_tickets (confirmation_id, order_id, olist_order_id, reason, handoff)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (confirmation_id) DO NOTHING
                        RETURNING {TICKET_COLUMNS}""",
                    (confirmation_id, demo_id, olist_id, reason, Jsonb(handoff) if handoff is not None else None),
                ).fetchone()
                if record is None:
                    # 新 SQL 语句可以读取另一事务刚提交的同一个确认 ID。
                    record = connection.execute(SELECT_TICKET, (confirmation_id, confirmation_id)).fetchone()
                if record is None:
                    raise TicketStoreError("暂时无法确认工单是否提交，请用原预览重试。")
                return record
        except psycopg.Error:
            raise TicketStoreError("暂时无法提交人工工单，请稍后重试。") from None

    def get_by_number(self, ticket_no: int) -> TicketDetailRecord | None:
        """按工单号读取原始申请与已保存的处理结果。"""
        try:
            with self._connect() as connection:
                return connection.execute(
                    f"""SELECT {DETAIL_COLUMNS}, handoff,
                        (SELECT actor_id FROM public.support_ticket_audit WHERE ticket_no = t.ticket_no) AS resolved_by
                        FROM public.support_tickets AS t WHERE t.ticket_no = %s""",
                    (ticket_no,),
                ).fetchone()
        except psycopg.Error:
            raise TicketStoreError("暂时无法读取工单进度，请稍后重试。") from None

    def list_pending(self) -> list[TicketRecord]:
        """按提交顺序读取待处理队列。"""
        try:
            with self._connect() as connection:
                return connection.execute(
                    f"""SELECT {TICKET_COLUMNS} FROM public.support_tickets
                        WHERE status = 'pending' ORDER BY ticket_no"""
                ).fetchall()
        except psycopg.Error:
            raise TicketStoreError("暂时无法读取待处理工单，请稍后重试。") from None

    def search(self, *, status: TicketStatusFilter, order_id: str | None,
               before: int | None, limit: int) -> list[TicketSearchRecord]:
        """按创建时间和工单号倒序读取一页，所有筛选值使用绑定参数。"""
        conditions = ["TRUE"]
        params: list[str | int] = []
        if status != "all":
            conditions.append("t.status = %s")
            params.append(status)
        if order_id is not None:
            conditions.append("t.olist_order_id = %s" if OLIST_ORDER_PATTERN.fullmatch(order_id) else "t.order_id = %s")
            params.append(order_id)
        if before is not None:
            conditions.append("(t.created_at, t.ticket_no) < ("
                              "SELECT created_at, ticket_no FROM public.support_tickets WHERE ticket_no = %s)")
            params.append(before)
        params.append(limit)
        where = " AND ".join(conditions)
        try:
            with self._connect() as connection:
                return connection.execute(
                    f"""SELECT t.ticket_no, COALESCE(t.olist_order_id, t.order_id) AS order_id,
                               t.reason, t.status, t.created_at
                        FROM public.support_tickets AS t WHERE {where}
                        ORDER BY t.created_at DESC, t.ticket_no DESC LIMIT %s""",
                    params,
                ).fetchall()
        except psycopg.Error:
            raise TicketStoreError("暂时无法查询工单历史，请稍后重试。") from None

    def resolve(self, ticket_no: int, resolution: str, *, actor: str | None = None) -> TicketDetailRecord | None:
        """在同一事务保存处理结果和客服审计记录。"""
        try:
            with self._connect() as connection:
                # 并发处理同一行时，后到的事务会再检查 pending 条件。
                record = connection.execute(
                    f"""UPDATE public.support_tickets
                        SET status = 'resolved', resolution = %s, resolved_at = CURRENT_TIMESTAMP
                        WHERE ticket_no = %s AND status = 'pending'
                        RETURNING {DETAIL_COLUMNS}""",
                    (resolution, ticket_no),
                ).fetchone()
                if record is not None and actor is not None:
                    connection.execute(
                        "INSERT INTO public.support_ticket_audit (ticket_no, actor_id, action) VALUES (%s, %s, 'resolve')",
                        (ticket_no, actor),
                    )
                return record
        except psycopg.Error:
            raise TicketStoreError("暂时无法保存工单处理结果，请稍后重试。") from None
