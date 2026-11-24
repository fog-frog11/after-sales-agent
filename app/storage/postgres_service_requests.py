"""持久化京东商品售后申请、办理过程和费用演练数据。"""

from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from app.settings import ROOT
from app.tickets.service_requests import ReturnShipment, ServiceApplication, ServiceError, StaffServiceUpdate, STAFF_TRANSITIONS


SELECT_REQUEST = """SELECT a.request_no::text AS request_no, a.order_id, a.item_no, a.quantity, a.buyer_id,
    a.kind, a.reason, a.description, a.status, a.created_at, a.updated_at, i.sku_id
    FROM public.after_sales_requests a JOIN public.jd_research_items i USING (order_id, item_no)"""
ACTIVE = ("submitted", "awaiting_return", "return_sent", "received")


class PostgresServiceRequests:
    """保证申请、状态和办理记录在同一事务提交。"""

    def __init__(self, database_url: str):
        """保存已配置的本机数据库地址。"""
        self.database_url = database_url

    @contextmanager
    def connect(self):
        """提供短事务并统一隐藏数据库内部错误。"""
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row, connect_timeout=3,
                                 options="-c statement_timeout=6000") as connection:
                yield connection
        except psycopg.Error:
            raise ServiceError(503, "售后服务暂不可用，请稍后重试。") from None

    def initialize(self) -> None:
        """初始化商品售后表，保留原工单和订单。"""
        with self.connect() as connection:
            connection.execute((ROOT / "sql/create_service_requests.sql").read_text(encoding="utf-8"))

    @staticmethod
    def _details(connection, number: int, buyer_id: str | None = None) -> dict:
        """按已授权身份返回请求和有序的办理时间线。"""
        row = connection.execute(SELECT_REQUEST + " WHERE a.request_no = %s AND (%s::text IS NULL OR a.buyer_id = %s)",
                                 (number, buyer_id, buyer_id)).fetchone()
        if row is None:
            raise ServiceError(404, "查不到这张售后单。")
        row["events"] = connection.execute("""
            SELECT status, note, carrier, tracking, created_at FROM public.after_sales_request_events
            WHERE request_no = %s ORDER BY array_position(
                ARRAY['submitted','awaiting_return','return_sent','received','completed','rejected']::text[], status)
        """, (number,)).fetchall()
        return row

    def get(self, number: int, buyer_id: str | None = None) -> dict:
        """读取单张售后单，买家调用必须带自身账号。"""
        with self.connect() as connection:
            return self._details(connection, number, buyer_id)

    def page(self, *, buyer_id: str | None = None, order_id: str | None = None, status: str | None = None,
             before: int | None = None, limit: int = 20) -> dict:
        """返回当前账号或客服可查的售后队列。"""
        with self.connect() as connection:
            rows = connection.execute(SELECT_REQUEST + """
                WHERE (%s::text IS NULL OR a.buyer_id = %s) AND (%s::text IS NULL OR a.order_id = %s)
                AND (%s::text IS NULL OR a.status = %s) AND (%s::bigint IS NULL OR a.request_no < %s)
                ORDER BY a.request_no DESC LIMIT %s
            """, (buyer_id, buyer_id, order_id, order_id, status, status, before, before, limit + 1)).fetchall()
        return {"requests": rows[:limit], "next_before": rows[limit - 1]["request_no"] if len(rows) > limit else None}

    def create(self, buyer_id: str, order_id: str, form: ServiceApplication) -> dict:
        """锁定商品行后验证可售后数量，避免重复提交或售后超量。"""
        with self.connect() as connection:
            item = connection.execute("""
                SELECT quantity FROM public.jd_research_items WHERE order_id = %s AND item_no = %s FOR UPDATE
            """, (order_id, form.item_no)).fetchone()
            if item is None:
                raise ServiceError(404, "这笔订单里没有所选商品。")
            known = connection.execute("SELECT * FROM public.after_sales_requests WHERE request_id = %s",
                                       (form.request_id,)).fetchone()
            if known is not None:
                if (known["buyer_id"] == buyer_id and known["order_id"] == order_id and all(
                    known[key] == value for key, value in form.model_dump(exclude={"request_id"}).items()
                )):
                    return self._details(connection, known["request_no"], buyer_id)
                raise ServiceError(409, "本次提交已使用，请重新核对申请。")
            if connection.execute("""
                SELECT 1 FROM public.after_sales_requests WHERE order_id = %s AND item_no = %s AND status = ANY(%s)
            """, (order_id, form.item_no, list(ACTIVE))).fetchone():
                raise ServiceError(409, "这件商品已有办理中的售后单，请查看进度。")
            consumed = connection.execute("""
                SELECT COALESCE(sum(quantity), 0) AS used FROM public.after_sales_requests
                WHERE order_id = %s AND item_no = %s AND status = 'completed' AND kind = 'return'
            """, (order_id, form.item_no)).fetchone()["used"]
            if not 1 <= form.quantity <= item["quantity"] - consumed:
                raise ServiceError(400, "申请件数超出这件商品剩余可申请的数量。")
            row = connection.execute("""
                INSERT INTO public.after_sales_requests (request_id, buyer_id, order_id, item_no, quantity, kind, reason, description)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (request_id) DO NOTHING RETURNING request_no
            """, (form.request_id, buyer_id, order_id, form.item_no, form.quantity, form.kind, form.reason,
                  form.description)).fetchone()
            if row is None:
                raise ServiceError(409, "本次提交已使用，请重新核对申请。")
            number = row["request_no"]
            self._event(connection, number, "submitted", buyer_id, form.description)
            return self._details(connection, number, buyer_id)

    @staticmethod
    def _event(connection, number: int, status: str, actor: str, note: str,
               carrier: str | None = None, tracking: str | None = None) -> None:
        """追加带操作者与时间的办理记录。"""
        connection.execute("""
            INSERT INTO public.after_sales_request_events (request_no, status, actor, note, carrier, tracking)
            VALUES (%s,%s,%s,%s,%s,%s)
        """, (number, status, actor, note, carrier, tracking))

    def _move(self, number: int, actor: str, previous: str, target: str, note: str, *,
              buyer_id: str | None = None, carrier: str | None = None, tracking: str | None = None) -> dict:
        """串行处理状态变更，重复提交同一操作返回现有记录。"""
        with self.connect() as connection:
            # 与新申请共用商品锁，使已退数量和进行中状态在并发操作中一致。
            row = connection.execute("""
                SELECT a.status FROM public.after_sales_requests a JOIN public.jd_research_items i USING(order_id, item_no)
                WHERE a.request_no = %s AND (%s::text IS NULL OR a.buyer_id = %s) FOR UPDATE OF i, a
            """, (number, buyer_id, buyer_id)).fetchone()
            if row is None:
                raise ServiceError(404, "查不到这张售后单。")
            current = row["status"]
            if current == target:
                event = connection.execute("""
                    SELECT actor, note, carrier, tracking FROM public.after_sales_request_events
                    WHERE request_no = %s AND status = %s
                """, (number, target)).fetchone()
                if event == {"actor": actor, "note": note, "carrier": carrier, "tracking": tracking}:
                    return self._details(connection, number, buyer_id)
            if current != previous:
                raise ServiceError(409, "当前阶段无法执行这项操作，请刷新进度。")
            connection.execute("UPDATE public.after_sales_requests SET status = %s, updated_at = now() WHERE request_no = %s",
                               (target, number))
            self._event(connection, number, target, actor, note, carrier, tracking)
            return self._details(connection, number, buyer_id)

    def ship(self, number: int, buyer_id: str, form: ReturnShipment) -> dict:
        """审核通过后登记买家填写的寄回运单。"""
        return self._move(number, buyer_id, "awaiting_return", "return_sent", "买家登记寄回包裹。",
                          buyer_id=buyer_id, carrier=form.carrier, tracking=form.tracking)

    def staff_update(self, number: int, staff_id: str, form: StaffServiceUpdate) -> dict:
        """根据当前阶段记录客服审核、收件或办结。"""
        previous, target = STAFF_TRANSITIONS[form.action]
        return self._move(number, staff_id, previous, target, form.note)

    def freight_practice(self, order_id: str, reason: str) -> dict | None:
        """按订单和买家选择的原因读取独立的费用演练数据。"""
        with self.connect() as connection:
            return connection.execute("""
                SELECT payer, quoted_fee, voucher, prepaid FROM public.after_sales_freight_practice
                WHERE order_id = %s AND reason = %s
            """, (order_id, reason)).fetchone()
