"""在 PostgreSQL 中保存商城订单、店铺归属、售后状态和平台介入记录。"""

import re
from collections.abc import Callable
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from time import perf_counter
from zoneinfo import ZoneInfo

import psycopg
from fastapi.encoders import jsonable_encoder
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.access import Account
from app.integrations.deepseek import ModelError
from app.storage.mall_policies import current_policy, platform_lines
from app.storage.mall_supplements import applicable_supplements
from app.storage.postgres_mall_delivery import OUTBOUND_ACTIONS, display_delivery_address, operate_outbound
from app.mall.agent import MallAgent, Planner
from app.mall.dialogue_state import merge_fallback, snapshot
from app.mall.grounded_reply import reply_review_needed, reply_facts, validate_reply
from app.mall.tool_facts import build_facts
from app.mall.dialogue_state import task_signature
from app.mall.semantic_consultation import execute as execute_semantic
from app.policies.mall_retrieval import MallRetrieval, scope_key
from app.mall.consultation import (
    ORDER_CODE_IN_TEXT, REJECT_REASON, answer, asks_purchase_history, consultation_context,
    consultation_evidence, drops_pending_order, is_direct_question,
    narrow_order_candidates, order_search_term, question_topics, refers_to_listed_order, reported_reason, split_order_questions,
)
from app.mall.dialogue import other_query_parts, resolve_citation_query
from app.mall.offers import offer_choices, prepare_offer, refundable
from app.mall.photos import PHOTO_ACTIONS, decode_photos, fingerprints
from app.mall.refund_summary import product_refund_amount, refund_summary
from app.mall.rules import (
    ACTIVE, DELIVERY_KINDS, DELIVERY_PHASES, DELIVERY_REVIEW_ACTIONS, DISPATCH_ACTIONS, KINDS, PROMPT_STATES, REASONS,
    REFUND_KINDS, REFUND_PROGRESS, SELLER_PHASES, STATES, MallApplication, MallOperation, MallQuestion,
    MallPackageApplication, assess_eligibility, available_actions, delivery_reply_pending, delivery_review_open, freight_quote, handling_kind, next_status,
    identify_reason, platform_intervention, policy_lines, preparing_outbound, review_phase,
)
from app.tickets.service_requests import ServiceError


CHINA = ZoneInfo("Asia/Shanghai")
ROLE_LABELS = {"buyer": "买家", "merchant": "店铺客服", "staff": "平台客服"}
REFUND_TOTALS = """CROSS JOIN LATERAL (
    SELECT COALESCE(sum(r.amount),0) AS amount, COALESCE(sum(r.closed_quantity),0) AS quantity
    FROM public.mall_refund_book r WHERE r.order_id=i.order_id AND r.item_no=i.item_no
) book"""
PARCEL_STAGE = """CASE WHEN p.stage IN ('preparing','in_transit') AND NOT EXISTS (
    SELECT 1 FROM public.mall_items i WHERE i.order_id=p.order_id AND i.package_no=p.package_no
      AND i.quantity > COALESCE((SELECT sum(r.closed_quantity) FROM public.mall_refund_book r
                                WHERE r.order_id=i.order_id AND r.item_no=i.item_no),0)
    ) THEN 'refunded' ELSE p.stage END"""
ITEMS = f"""SELECT i.order_id, i.item_no, i.package_no, i.sku, i.title, i.variant, i.quantity, i.list_unit, i.paid_unit,
    i.change_of_mind, i.repairable, p.stage, p.carrier, p.tracking, p.delivered_at, p.expected_on,
    o.buyer_id, o.shop_id, o.ordered_at, o.terms_version, s.name AS shop_name,
    t.title AS terms_title, t.effective_on, t.return_days, t.fault_days, t.warranty_days,
    t.reply_hours, t.return_postage, t.seller_advances, ed.platform_version, ed.item_credit, ed.guidance_key, ed.guidance,
    i.quantity - book.quantity AS remaining_quantity, book.amount AS refunded_amount,
    GREATEST(i.quantity * i.paid_unit - book.amount,0) AS refund_balance,
    GREATEST(i.return_credit - used.credit, 0) AS return_credit
    FROM public.mall_items i JOIN public.mall_packages p USING (order_id, package_no)
    JOIN public.mall_orders o USING (order_id)
    JOIN public.mall_shops s USING (shop_id)
    JOIN public.mall_terms t ON t.shop_id=o.shop_id AND t.version=o.terms_version
    LEFT JOIN public.mall_shop_policy_editions ed ON ed.shop_id=o.shop_id AND ed.version=o.terms_version
    {REFUND_TOTALS}
    CROSS JOIN LATERAL (
        SELECT COALESCE(sum(CASE WHEN e.details ? 'credit_by_item'
            THEN (e.details->'credit_by_item'->>i.item_no::text)::numeric
            ELSE (e.details->>'credit_used')::numeric END),0) AS credit
        FROM public.mall_cases c JOIN public.mall_case_events e ON e.case_no=c.case_no AND e.action='ship'
        WHERE c.order_id=i.order_id AND (c.item_no=i.item_no OR e.details->'credit_by_item' ? i.item_no::text)
    ) used"""
CASES = f"""SELECT c.case_no AS no, c.case_no::text AS case_no, c.submission_id, c.order_id, c.item_no,
    c.quantity, c.kind, c.reason, c.description, c.status, c.created_at, c.updated_at, deadline.effective_due AS due_at,
    o.shop_id, s.name AS shop_name, o.buyer_id, o.ordered_at, o.terms_version,
    i.title AS product, i.variant, i.paid_unit, i.change_of_mind, i.repairable,
    i.quantity - book.quantity AS remaining_quantity,
    GREATEST(i.quantity * i.paid_unit - book.amount,0) AS refund_balance,
    p.package_no, p.stage AS package_stage, p.tracking AS outbound_tracking, p.delivered_at, p.expected_on,
    submission.details->>'transit_request' AS transit_request,
    (SELECT jsonb_agg(line.value || jsonb_build_object('paid_unit', ordered_item.paid_unit::text) ORDER BY ordered_item.item_no)
     FROM jsonb_array_elements(submission.details->'package_lines') line(value)
     JOIN public.mall_items ordered_item ON ordered_item.order_id=c.order_id
         AND ordered_item.item_no=(line.value->>'item_no')::integer) AS package_lines,
    interception.details AS interception_failure,
    EXISTS (SELECT 1 FROM public.mall_case_events e WHERE e.case_no=c.case_no AND e.action='carrier_return')
      AND rd.case_no IS NULL AS carrier_returned,
    t.title AS terms_title, t.effective_on, t.return_days, t.fault_days, t.warranty_days,
    t.reply_hours, t.return_postage, t.seller_advances, ed.platform_version, ed.item_credit, ed.guidance,
    d.opened_at AS dispute_opened_at, d.previous_status AS dispute_previous_status, d.decision, d.decision_at,
    rd.details AS return_details, (rd.case_no IS NOT NULL AND rd.details IS NULL) AS return_details_pending,
    review.review_no AS active_review_no, review.phase AS active_review_phase,
    shipment.outbound, delivery_review.details AS delivery_review, delivery_reply.details AS delivery_reply,
    destination.details AS default_delivery_address,
    prompt.kind AS prompt_kind, prompt.note AS prompt_note, prompt.operation_id AS prompt_id,
    CASE WHEN proposal.operation_id IS NOT NULL THEN jsonb_build_object(
        'id', proposal.operation_id, 'kind', proposal.kind, 'quantity', proposal.quantity,
        'amount', proposal.amount::text, 'return_instructions', proposal.return_instructions
    ) END AS pending_offer,
    accepted.agreement, posted.amount AS recorded_refund, posted.recorded_at AS refund_recorded_at,
    refund_attempt.details AS refund_attempt,
    handling.role AS owner_role, fees.reimbursement_due, ship_freight.details AS ship_freight,
    checkout.shipping AS original_shipping,
    EXISTS (SELECT 1 FROM public.mall_case_events refusal
        WHERE refusal.case_no=c.case_no AND refusal.action='reject_offer' AND refusal.event_no > COALESCE((
            SELECT max(revision.event_no) FROM public.mall_case_events revision
            WHERE revision.case_no=c.case_no AND revision.action='revise'
        ), 0)) AS declined_offer
    FROM public.mall_cases c JOIN public.mall_orders o USING (order_id)
    JOIN public.mall_items i ON i.order_id=c.order_id AND i.item_no=c.item_no
    JOIN public.mall_packages p ON p.order_id=i.order_id AND p.package_no=i.package_no
    JOIN public.mall_shops s ON s.shop_id=o.shop_id
    JOIN public.mall_terms t ON t.shop_id=o.shop_id AND t.version=o.terms_version
    LEFT JOIN public.mall_shop_policy_editions ed ON ed.shop_id=o.shop_id AND ed.version=o.terms_version
    {REFUND_TOTALS}
    LEFT JOIN public.mall_disputes d ON d.case_no=c.case_no
    LEFT JOIN public.mall_case_events submission ON submission.case_no=c.case_no AND submission.operation_id=c.submission_id
    LEFT JOIN public.mall_order_checkout checkout ON checkout.order_id=c.order_id
    LEFT JOIN LATERAL (
        SELECT jsonb_build_object('note', e.note, 'at', e.created_at) AS details
        FROM public.mall_case_events e
        WHERE e.case_no=c.case_no AND e.action='carrier_interception_failed'
        ORDER BY e.event_no DESC LIMIT 1
    ) interception ON c.status='awaiting_outcome' AND p.stage='in_transit'
    LEFT JOIN public.mall_return_details rd ON rd.case_no=c.case_no
    LEFT JOIN public.mall_dispute_reviews review ON review.case_no=c.case_no AND review.decision IS NULL
    LEFT JOIN public.mall_case_prompts prompt ON prompt.case_no=c.case_no AND prompt.answered_at IS NULL
    LEFT JOIN public.mall_case_offers proposal ON proposal.case_no=c.case_no AND proposal.operation_id=prompt.operation_id
    LEFT JOIN LATERAL (
        SELECT sum(amount) AS amount, max(recorded_at) AS recorded_at
        FROM public.mall_refund_book WHERE case_no=c.case_no
    ) posted ON TRUE
    LEFT JOIN public.mall_order_destinations destination ON destination.order_id=c.order_id
    LEFT JOIN LATERAL (
        SELECT jsonb_build_object('id', e.operation_id, 'amount', e.details->>'refund_amount',
            'quantity', e.details->'refund_quantity', 'started_at', e.created_at,
            'state', COALESCE(result.details->>'refund_result','pending'),
            'result_at', result.created_at, 'result_note', result.note) AS details
        FROM public.mall_case_events e
        LEFT JOIN LATERAL (
            SELECT r.details,r.created_at,r.note FROM public.mall_case_events r
            WHERE r.case_no=e.case_no AND r.action='record_refund_result'
                AND r.details->>'refund_attempt_id'=e.operation_id::text
            ORDER BY r.event_no DESC LIMIT 1
        ) result ON TRUE
        WHERE e.case_no=c.case_no AND e.action='refund' AND e.details ? 'refund_quantity'
        ORDER BY e.event_no DESC LIMIT 1
    ) refund_attempt ON TRUE
    LEFT JOIN LATERAL (
        SELECT jsonb_build_object('parcel_no', parcel_no::text, 'stage', stage, 'destination', destination,
            'address_version', address_version, 'carrier', carrier, 'tracking', tracking, 'problem', problem,
            'shipped_at', shipped_at, 'received_at', received_at) AS outbound
        FROM public.mall_outbound_parcels WHERE case_no=c.case_no ORDER BY parcel_no DESC LIMIT 1
    ) shipment ON TRUE
    LEFT JOIN LATERAL (
        SELECT e.action, e.event_no, jsonb_build_object('action', e.action,
            'request_id', COALESCE(e.details->>'delivery_review_id',e.operation_id::text),
            'parcel_no', e.details->>'delivery_parcel_no', 'result_parcel_no', e.details->>'outbound_parcel_no',
            'problem', e.details->>'review_problem',
            'at', e.created_at, 'note', e.note) AS details
        FROM public.mall_case_events e WHERE e.case_no=c.case_no
            AND e.action IN ('request_delivery_review','platform_dispatch','platform_reissue','platform_continue_delivery')
        ORDER BY e.event_no DESC LIMIT 1
    ) delivery_review ON true
    LEFT JOIN LATERAL (
        SELECT jsonb_build_object('note', e.note, 'at', e.created_at) AS details
        FROM public.mall_case_events e WHERE e.case_no=c.case_no AND e.action='resume_delivery'
            AND shipment.outbound->>'stage'='shipped'
            AND e.details->>'outbound_parcel_no'=shipment.outbound->>'parcel_no'
            AND e.event_no>COALESCE(delivery_review.event_no,0)
        ORDER BY e.event_no DESC LIMIT 1
    ) delivery_reply ON true
    LEFT JOIN LATERAL (
        SELECT jsonb_build_object('id', offer.operation_id, 'kind', offer.kind, 'quantity', offer.quantity,
            'amount', offer.amount::text, 'return_instructions', offer.return_instructions,
            'accepted_at', original.answered_at) AS agreement
        FROM public.mall_case_offers offer JOIN public.mall_case_prompts original USING (case_no,operation_id)
        WHERE offer.case_no=c.case_no AND original.response_action='accept_offer'
        ORDER BY original.answered_at DESC LIMIT 1
    ) accepted ON TRUE
    CROSS JOIN LATERAL (
        SELECT c.status IN ('received','awaiting_outcome')
            AND COALESCE(accepted.agreement->>'kind',c.kind) IN ('exchange','repair','reship') AS active
    ) delivery
    CROSS JOIN LATERAL (
        SELECT CASE WHEN delivery.active AND shipment.outbound IS NULL THEN NULL
            WHEN c.status='awaiting_outcome' AND submission.details ? 'transit_request'
            AND (submission.details->>'transit_request'='partial' OR p.stage='delivered') THEN NULL
            ELSE c.due_at END AS effective_due
    ) deadline
    LEFT JOIN LATERAL (
        SELECT e.details FROM public.mall_case_events e
        WHERE e.case_no=c.case_no AND e.action='ship' ORDER BY e.event_no DESC LIMIT 1
    ) ship_freight ON TRUE
    CROSS JOIN LATERAL (
        SELECT CASE WHEN NOT EXISTS (
            SELECT 1 FROM public.mall_case_events e WHERE e.case_no=c.case_no AND e.action='reimburse'
        ) THEN (ship_freight.details->>'advance_amount')::numeric END AS reimbursement_due
    ) fees
    CROSS JOIN LATERAL (
        SELECT CASE WHEN c.status='disputed' OR delivery_review.action='request_delivery_review' THEN 'staff'
            WHEN c.status='awaiting_return' AND rd.case_no IS NOT NULL AND rd.details IS NULL THEN 'merchant'
            WHEN c.status='awaiting_outcome' AND submission.details ? 'transit_request'
              AND (submission.details->>'transit_request'='partial' OR p.stage='delivered') THEN 'buyer'
            WHEN delivery.active THEN CASE
                WHEN shipment.outbound IS NULL THEN 'buyer'
                WHEN shipment.outbound->>'stage'='ready' OR shipment.outbound->>'problem' IS NOT NULL THEN 'merchant'
                ELSE 'buyer' END
            WHEN prompt.kind IS NOT NULL OR c.status IN ('awaiting_return','rejected') THEN 'buyer'
            WHEN c.status IN ('submitted','return_sent','received','awaiting_outcome')
              OR (c.status IN ('refunded','replacement_sent','repaired','closed') AND fees.reimbursement_due>0) THEN 'merchant'
            END AS role
    ) handling"""
CASE_SCOPE = {
    "buyer": "o.buyer_id=%s",
    "merchant": "EXISTS (SELECT 1 FROM public.mall_shop_staff m WHERE m.shop_id=o.shop_id AND m.account_id=%s)",
    "staff": "(d.case_no IS NOT NULL OR delivery_review.action IS NOT NULL)",
}


class PostgresMall:
    """使用事务读取账号对应的数据，并串行推进同一张售后单。"""

    def __init__(self, database_url: str, planner: Planner | None = None, *, writer: Callable[[dict], str] | None = None,
                 model_dialogue: bool = False, composer: Callable[[dict], str] | None = None,
                 retrieval: MallRetrieval | None = None, reviewer: Callable[[dict], dict] | None = None,
                 read_decider: Callable[[dict], dict] | None = None,
                 business_decider: Callable[[dict], list] | None = None):
        """保存数据库地址，并为本工作台编译一次商品咨询图。"""
        self.database_url = database_url
        self.consult_agent = MallAgent(planner)
        self.writer = writer
        self.model_dialogue = model_dialogue
        self.composer = composer
        self.retrieval = retrieval
        self.reviewer = reviewer
        self.read_decider = read_decider
        self.business_decider = business_decider

    @contextmanager
    def connect(self):
        """约束数据库访问时间，对外只返回业务错误。"""
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row, connect_timeout=4,
                                 options="-c statement_timeout=7000") as conn:
                yield conn
        except psycopg.Error:
            raise ServiceError(503, "商城服务暂不可用，请稍后重试。") from None

    def orders(self, buyer: str, prefix: str, after: str, stage: str) -> dict:
        """在本人订单内按编号片段、店名、商品名或规格搜索，可叠加包裹状态。"""
        term = prefix.strip()
        with self.connect() as conn:
            rows = conn.execute(f"""
                WITH page AS (
                SELECT o.order_id, o.ordered_at, s.name AS shop_name, o.terms_version,
                    array_agg(DISTINCT parcel.stage ORDER BY parcel.stage) AS stages
                FROM public.mall_orders o JOIN public.mall_shops s USING (shop_id)
                JOIN public.mall_packages p USING (order_id)
                CROSS JOIN LATERAL (
                    SELECT {PARCEL_STAGE} AS stage
                ) parcel
                WHERE o.buyer_id=%s AND o.order_id>%s AND (
                    strpos(lower(concat_ws(' ',o.order_id,s.name)),lower(%s))>0 OR EXISTS (
                        SELECT 1 FROM public.mall_items item WHERE item.order_id=o.order_id
                        AND strpos(lower(concat_ws(' ',item.title,item.variant)),lower(%s))>0))
                GROUP BY o.order_id,s.name
                HAVING (%s='' OR %s=ANY(array_agg(parcel.stage)))
                ORDER BY o.order_id LIMIT 13
                )
                SELECT page.*, goods.* FROM page CROSS JOIN LATERAL (
                    SELECT (array_agg(i.title ORDER BY i.item_no))[1] AS first_product,
                           count(*) AS lines, sum(i.quantity) AS units,
                           sum(i.quantity * i.paid_unit) AS goods_paid
                    FROM public.mall_items i WHERE i.order_id=page.order_id
                ) goods ORDER BY page.order_id
            """, (buyer, after, term, term, stage, stage)).fetchall()
        return {"orders": rows[:12], "next_after": rows[11]["order_id"] if len(rows) > 12 else None}

    @staticmethod
    def _items(conn, buyer: str, order_id: str, item_no: int | None = None, *, lock: bool = False) -> list[dict]:
        """绑定数据库买家和订单商品；提交时锁定该商品行。"""
        rows = conn.execute(ITEMS + """
            WHERE i.order_id=%s AND o.buyer_id=%s AND (%s::integer IS NULL OR i.item_no=%s)
            ORDER BY i.item_no
        """ + (" FOR UPDATE OF i,p" if lock else ""), (order_id, buyer, item_no, item_no)).fetchall()
        if not rows:
            raise ServiceError(404, "查不到这笔订单或商品。")
        return rows

    def order(self, buyer: str, order_id: str) -> dict:
        """返回订单商品、按原单绑定的条款和买家自己的售后列表。"""
        with self.connect() as conn:
            return self.order_details(conn, Account(buyer, "buyer"), buyer, order_id)

    def order_details(self, conn, account: Account, buyer: str, order_id: str) -> dict:
        """把订单购买、结算和物流明细合并，售后和收货信息按查看者范围返回。"""
        items = self._items(conn, buyer, order_id)
        cases = self._page(conn, account, order_id=order_id, exact=True)
        checkout = conn.execute("SELECT shipping,payment_method FROM public.mall_order_checkout WHERE order_id=%s",
                                (order_id,)).fetchone()
        destination = conn.execute("SELECT details FROM public.mall_order_destinations WHERE order_id=%s",
                                   (order_id,)).fetchone() if account.role == "buyer" else None
        first = items[0]
        current = current_policy(conn, first["shop_id"])
        floor = current["floor"] if current else None
        safe_items = [{key: row[key] for key in (
            "item_no", "package_no", "sku", "title", "variant", "quantity", "list_unit", "paid_unit", "return_credit",
            "stage", "carrier", "tracking", "expected_on", "delivered_at", "change_of_mind", "repairable", "remaining_quantity",
            "refunded_amount", "refund_balance",
        )} for row in items]
        parcel_remaining = {row["package_no"] for row in items if row["remaining_quantity"] > 0}
        for item in safe_items:
            if item["carrier"] == "商城演练物流":
                item["carrier"] = "商城配送"
            if item["stage"] in ("preparing","in_transit") and item["package_no"] not in parcel_remaining:
                item["stage"] = "refunded"
        item_total = sum((item["paid_unit"] * item["quantity"] for item in items), Decimal("0"))
        list_total = sum((item["list_unit"] * item["quantity"] for item in items), Decimal("0"))
        return {"order_id": order_id, "shop_name": first["shop_name"], "ordered_at": first["ordered_at"],
                "terms_version": first["terms_version"], "terms_title": first["terms_title"],
                "effective_on": first["effective_on"], "terms": policy_lines(first), "items": safe_items,
                "current_policy": ({"version": current["version"], "effective_on": current["effective_on"],
                                    "terms": policy_lines(current), "platform_version": floor["version"],
                                    "platform": platform_lines(floor)} if floor else None),
                "delivery_address": display_delivery_address(destination["details"] if destination else None),
                "total": item_total, "list_total": list_total, "discount_total": list_total - item_total,
                "shipping": checkout["shipping"] if checkout else None, "payment_method": checkout["payment_method"] if checkout else None,
                "paid_total": item_total + (checkout["shipping"] if checkout else Decimal("0")),
                "refunded_total": sum((item["refunded_amount"] for item in items), Decimal("0")),
                "cases": cases["cases"], "next_before": cases["next_before"], "package_returns": True}

    @staticmethod
    def _scope(account: Account) -> tuple:
        """将经过身份验证的账号作为查询条件，平台只进入已有争议。"""
        return CASE_SCOPE[account.role], (() if account.role == "staff" else (account.account_id,))

    def _case(self, conn, account: Account, number: int, *, lock: bool = False) -> dict:
        """按角色读取原单，写操作共享同一行锁。"""
        scope, args = self._scope(account)
        query = CASES + f" WHERE c.case_no=%s AND {scope}"
        row = conn.execute(query, (number, *args)).fetchone()
        if row is None:
            raise ServiceError(404, "查不到这张售后单。")
        if lock:
            conn.execute("SELECT case_no FROM public.mall_cases WHERE case_no=%s FOR UPDATE", (number,))
            # 获取锁后重新读取协商和费用子表，避免用等待锁前的状态执行操作。
            return self._case(conn, account, number)
        return row

    @staticmethod
    def _display(row: dict, account: Account) -> dict:
        """返回进度和下一步，隐藏仓储内部授权字段。"""
        status = row["status"]
        due = row["due_at"]
        now = datetime.now(timezone.utc)
        overdue = due is not None and due <= now
        deadline = "overdue" if overdue else "soon" if due and due <= now + timedelta(hours=4) else None
        owner = ROLE_LABELS.get(row["owner_role"])
        response = {key: row[key] for key in (
            "case_no", "order_id", "item_no", "quantity", "kind", "reason", "status", "created_at", "updated_at",
            "due_at", "shop_name", "product", "variant", "decision",
        )}
        lines = row["package_lines"]
        response["item_nos"] = [line["item_no"] for line in lines] if lines else [row["item_no"]]
        if lines:
            response.update(product=f"包裹 {row['package_no']} · {len(lines)} 种商品",
                            variant="", quantity=sum(line["quantity"] for line in lines))
        kind = handling_kind(row)
        label = "已登记部分退款" if status == "refunded" and kind == "partial_refund" else STATES[status]
        refund_attempt = row["refund_attempt"]
        if row["transit_request"] and status == "awaiting_outcome":
            label = "待核对原包裹退回" if row["transit_request"] == "whole" else "部分商品待签收后寄回"
            if row["interception_failure"]:
                label = "拦截失败 · 包裹继续配送"
            elif row["package_stage"] == "delivered":
                label = "物流已签收 · 待买家核对"
        if row["carrier_returned"] and status == "return_sent":
            label = "原包裹退回中 · 待店铺收件"
        if refund_attempt:
            label = REFUND_PROGRESS[refund_attempt["state"]]
            if kind == "partial_refund" and refund_attempt["state"] == "succeeded":
                label = "部分退款成功"
        if status == "awaiting_return" and row["return_details_pending"]:
            label = "待店铺补充寄回信息"
        elif status == "disputed" and row["active_review_no"] is not None:
            label = "平台复核中"
        parcel = row["outbound"]
        if parcel is not None:
            parcel = dict(parcel, destination=display_delivery_address(parcel["destination"]))
        review = row["delivery_review"] or {}
        delivery_decision = review.get("action") if parcel and review.get("result_parcel_no") == parcel["parcel_no"] else None
        if preparing_outbound(row):
            if delivery_review_open(row):
                label = "平台处理中 · 售后包裹"
            elif parcel is None:
                label = "待买家确认收货信息"
            elif parcel["stage"] == "ready" and delivery_decision in ("platform_dispatch", "platform_reissue"):
                label = "待店铺按平台要求重寄" if delivery_decision == "platform_reissue" else "待店铺按平台要求发货"
            elif parcel["problem"]:
                label = "售后包裹异常待店铺处理"
            elif delivery_reply_pending(row):
                label = "待买家核对店铺回复"
            else:
                label = "待店铺发出新包裹" if parcel["stage"] == "ready" else "待买家确认收货"
        elif parcel and parcel["stage"] == "received":
            label = {"exchange": "已确认收到换货商品", "reship": "已确认收到补发商品", "repair": "已确认收到修后商品"}[kind]
        response.update(status_label=PROMPT_STATES.get(row["prompt_kind"], label),
                        kind_label="在途退回退款" if row["transit_request"] else KINDS[row["kind"]], owner=owner, overdue=overdue, deadline=deadline,
                        transit_request=row["transit_request"], carrier_returned=row["carrier_returned"],
                        interception_failed=bool(row["interception_failure"]),
                        is_active=status in ACTIVE,
                        return_reimbursement_due=row["reimbursement_due"] or None,
                        handling_label="在途退回退款" if row["transit_request"] else KINDS[kind], agreement=row["agreement"], recorded_refund=row["recorded_refund"],
                        return_details=row["return_details"], return_details_pending=row["return_details_pending"],
                        outbound=parcel, outbound_pending=preparing_outbound(row), delivery_review=row["delivery_review"],
                        delivery_reply=row["delivery_reply"] if delivery_reply_pending(row) else None,
                        active_review_phase=row["active_review_phase"],
                        refund_recorded_at=row["refund_recorded_at"], refund_attempt=refund_attempt,
                        prompt={"kind": row["prompt_kind"], "note": row["prompt_note"],
                                "id": row["prompt_id"], "offer": row["pending_offer"]} if row["prompt_kind"] else None,
                        actions=available_actions(row, account.role, overdue=overdue))
        if account.role == "buyer":
            response["intervention"] = platform_intervention(row, overdue=overdue)
        return response

    def _details(self, conn, row: dict, account: Account) -> dict:
        """补足三方共享的操作、凭证记录、包裹和条款快照。"""
        response = self._display(row, account)
        events = conn.execute("""
            SELECT e.actor_role, e.action, e.status, e.note, e.details - 'photo_hashes' - 'offer_input' AS details, e.created_at,
                COALESCE((SELECT jsonb_agg(p.photo_id::text ORDER BY p.photo_id)
                    FROM public.mall_case_photos p WHERE p.event_no=e.event_no), '[]') AS photos
            FROM public.mall_case_events e WHERE e.case_no=%s ORDER BY e.event_no
        """, (row["no"],)).fetchall()
        refund = product_refund_amount(row)
        choices = offer_choices(row) if account.role == "merchant" and "negotiate" in response["actions"] else ()
        response.update(
            reason_label="取消在途商品" if row["transit_request"] and row["reason"] == "cancel" else REASONS[row["reason"]],
            description=row["description"], terms_title=row["terms_title"],
            terms_version=row["terms_version"], effective_on=row["effective_on"], terms=policy_lines(row),
            package_lines=row["package_lines"], refundable_amount=refund,
            interception_failure=row["interception_failure"],
            refund_balance=Decimal("0") if row["package_lines"] and row["status"] == "refunded" else
            refund if row["package_lines"] else row["refund_balance"],
            offer_options={kind: KINDS[kind] for kind in choices},
            offer_max_quantity=min(row["quantity"], row["remaining_quantity"]), offer_unit_price=row["paid_unit"],
            package_stage=row["package_stage"], outbound_tracking=row["outbound_tracking"],
            outbound_history=conn.execute("""
                SELECT parcel_no::text AS parcel_no, stage, destination, carrier, tracking, shipped_at, received_at, problem
                FROM public.mall_outbound_parcels WHERE case_no=%s ORDER BY parcel_no
            """, (row["no"],)).fetchall(),
            default_delivery_address=display_delivery_address(row["default_delivery_address"]) if account.role == "buyer" else None,
            events=events, money=self._money(row, refund),
            refund_summary=refund_summary(row, refund),
        )
        return response

    def detail(self, account: Account, number: int) -> dict:
        """读取经授权的售后详情。"""
        with self.connect() as conn:
            return self._details(conn, self._case(conn, account, number), account)

    def photo(self, account: Account, number: int, photo_id: int) -> tuple[str, bytes]:
        """校验售后范围，再从这张单的事件读取指定照片。"""
        with self.connect() as conn:
            self._case(conn, account, number)
            row = conn.execute("""
                SELECT p.mime, p.image FROM public.mall_case_photos p
                JOIN public.mall_case_events e USING (event_no)
                WHERE e.case_no=%s AND p.photo_id=%s
            """, (number, photo_id)).fetchone()
            if row is None:
                raise ServiceError(404, "查不到这张照片。")
            return row["mime"], row["image"]

    @staticmethod
    def _money(row: dict, refund: Decimal | None) -> list[dict]:
        """分别列出商品、寄回费用与卖家返还，不用单一金额代替不同账目。"""
        entries = []
        if refund is not None:
            state = "已登记" if row["status"] == "refunded" else "待处理" if row["status"] in ACTIVE else "未退款"
            if row["refund_attempt"]:
                state = REFUND_PROGRESS[row["refund_attempt"]["state"]]
            entries.append({"label": "商品退款", "amount": refund, "state": state})
        ship = row["ship_freight"] or {}
        for key, label in (("return_postage_due", "寄回运费（抵扣后）"), ("return_postage_fee", "寄回运费"),
                           ("advance_amount", "买家垫付")):
            if key in ship:
                entries.append({"label": label, "amount": ship[key], "state": "已登记寄回"})
        if Decimal(ship.get("advance_amount", "0")) > 0:
            entries.append({"label": "垫付返还", "amount": ship["advance_amount"],
                            "state": "已登记" if row["reimbursement_due"] is None else "待店铺登记"})
        return entries

    def _page(self, conn, account: Account, *, order_id: str = "", before: int | None = None,
              queue: str = "all", item_no: int | None = None, exact: bool = False) -> dict:
        """分页读取自己的订单、当前店铺或平台争议下的售后单。"""
        scope, args = self._scope(account)
        rows = conn.execute(CASES + f""" WHERE {scope}
            AND (CASE WHEN %s THEN c.order_id=%s ELSE
                strpos(lower(concat_ws(' ',c.order_id,'#'||c.case_no,s.name,i.title,i.variant)),lower(%s))>0
                OR EXISTS (SELECT 1 FROM jsonb_array_elements(submission.details->'package_lines') line
                    JOIN public.mall_items part ON part.order_id=c.order_id AND part.item_no=(line->>'item_no')::int
                    WHERE strpos(lower(concat_ws(' ',part.title,part.variant)),lower(%s))>0) END)
            AND (%s::bigint IS NULL OR c.case_no<%s)
            AND (%s::integer IS NULL OR c.item_no=%s
                OR submission.details->'package_lines' @> jsonb_build_array(jsonb_build_object('item_no',%s::integer)))
            AND (%s='all' OR (handling.role=%s AND CASE %s
                WHEN 'soon' THEN deadline.effective_due>now() AND deadline.effective_due<=now()+interval '4 hours'
                WHEN 'overdue' THEN deadline.effective_due<=now()
                ELSE TRUE END))
            ORDER BY c.case_no DESC LIMIT 21
        """, (*args, exact, order_id, order_id.strip(), order_id.strip(), before, before, item_no, item_no, item_no,
              queue, account.role, queue)).fetchall()
        cases = [self._display(row, account) for row in rows[:20]]
        if item_no is not None:
            for case, row in zip(cases, rows):
                case["refund_summary"] = refund_summary(row, product_refund_amount(row))
        return {"cases": cases,
                "next_before": rows[19]["case_no"] if len(rows) > 20 else None}

    def page(self, account: Account, order_id: str, before: int | None, queue: str) -> dict:
        """提供三类工作台的待办和所有记录筛选。"""
        with self.connect() as conn:
            return self._page(conn, account, order_id=order_id, before=before, queue=queue)

    def submit(self, account: Account, order_id: str, form: MallApplication | MallPackageApplication,
               *, package_no: int | None = None, connection=None) -> dict:
        """锁定所属包裹后办理单商品或整包申请，避免并发提交重叠的商品。"""
        whole = isinstance(form, MallPackageApplication)
        photos = decode_photos(form.photos)
        hashes = fingerprints(photos)
        with (self.connect() if connection is None else nullcontext(connection)) as conn:
            package = conn.execute("""
                SELECT p.package_no FROM public.mall_packages p JOIN public.mall_orders o USING (order_id)
                WHERE p.order_id=%s AND o.buyer_id=%s AND p.package_no=COALESCE(%s::integer, (
                    SELECT package_no FROM public.mall_items WHERE order_id=p.order_id AND item_no=%s
                )) FOR NO KEY UPDATE OF p
            """, (order_id, account.account_id, package_no, None if whole else form.item_no)).fetchone()
            if package is None:
                raise ServiceError(404, "查不到这笔订单或包裹。")
            items = self._items(conn, account.account_id, order_id, None if whole else form.item_no)
            items = [item for item in items if item["package_no"] == package["package_no"]]
            if not items:
                raise ServiceError(404, "查不到这个包裹的商品。")
            item = items[0]
            existing = conn.execute("SELECT * FROM public.mall_cases WHERE submission_id=%s",
                                    (form.submission_id,)).fetchone()
            if existing is not None:
                matches = existing["order_id"] == order_id and all(
                    existing[field] == value for field, value in form.model_dump(exclude={"submission_id", "photos"}).items()
                )
                if not matches:
                    raise ServiceError(409, "这次提交编号已使用，请重新核对申请。")
                old = conn.execute("""
                    SELECT details FROM public.mall_case_events WHERE case_no=%s AND operation_id=%s
                """, (existing["case_no"], form.submission_id)).fetchone()
                details = (old or {}).get("details", {})
                if (hashes != details.get("photo_hashes", []) or whole != ("package_no" in details)
                        or whole and package_no != details["package_no"]):
                    raise ServiceError(409, "这次提交编号已使用，请重新核对申请。")
                return self._details(conn, self._case(conn, account, existing["case_no"]), account)
            if conn.execute("""
                SELECT 1 FROM public.mall_cases c
                LEFT JOIN public.mall_case_events e ON e.case_no=c.case_no AND e.operation_id=c.submission_id
                WHERE c.order_id=%s AND c.status=ANY(%s) AND (
                    c.item_no=ANY(%s) OR EXISTS (
                        SELECT 1 FROM jsonb_to_recordset(e.details->'package_lines') AS line(item_no integer)
                        WHERE line.item_no=ANY(%s)
                    )
                )
            """, (order_id, list(ACTIVE), [line["item_no"] for line in items],
                  [line["item_no"] for line in items])).fetchone():
                raise ServiceError(409, "申请中有商品已在办理售后，请先查看原售后单。")
            kind = "return_refund" if whole else form.kind
            quantity = item["quantity"] if whole else form.quantity
            if quantity > item["remaining_quantity"]:
                raise ServiceError(400, "申请件数超过剩余可申请数量。")
            today = datetime.now(CHINA).date()
            eligibility = assess_eligibility(item, form.reason, today)
            if whole and (not eligibility.transit or any(line["quantity"] != line["remaining_quantity"] for line in items)):
                raise ServiceError(409, "仅运输中且商品未办理退款的包裹可以整包申请，请刷新订单核对。")
            if kind not in eligibility.kinds:
                message = eligibility.message
                if eligibility.kinds:
                    message += " 请从当前可办理的方式中重新选择。"
                elif eligibility.next_step:
                    message += eligibility.next_step
                raise ServiceError(409, message)
            saved = conn.execute("""
                INSERT INTO public.mall_cases
                    (submission_id,order_id,item_no,quantity,kind,reason,description,status,due_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,'submitted',now() + make_interval(hours => %s))
                ON CONFLICT (submission_id) DO NOTHING RETURNING case_no
            """, (form.submission_id, order_id, item["item_no"], quantity, kind, form.reason,
                  form.description, item["reply_hours"])).fetchone()
            if saved is None:
                raise ServiceError(409, "这次提交编号已使用，请重新核对申请。")
            number = saved["case_no"]
            details = {"photo_hashes": hashes} if hashes else {}
            if whole:
                details.update(transit_request="whole", package_no=package_no, package_lines=[
                    {"item_no": line["item_no"], "title": line["title"], "variant": line["variant"],
                     "quantity": line["quantity"], "amount": f"{refundable(line, line['quantity']):.2f}"}
                    for line in items
                ])
            elif eligibility.transit:
                units = conn.execute("SELECT sum(quantity) AS units FROM public.mall_items WHERE order_id=%s AND package_no=%s",
                                     (order_id, item["package_no"])).fetchone()["units"]
                details["transit_request"] = "whole" if units == quantity else "partial"
            self._event(conn, number, form.submission_id, account, "submit", "submitted", form.description,
                        details, photos=photos)
            return self._details(conn, self._case(conn, account, number), account)

    @staticmethod
    def _event(conn, number, operation_id, account, action, status, note, details, *, photos=()):
        """把操作去重编号、处理人和对各方共享的说明原子追加到时间线。"""
        event = conn.execute("""
            INSERT INTO public.mall_case_events (case_no,operation_id,actor_id,actor_role,action,status,note,details)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING event_no
        """, (number, operation_id, account.account_id, account.role, action, status, note, Jsonb(details))).fetchone()
        for mime, image in photos:
            conn.execute("INSERT INTO public.mall_case_photos (event_no,mime,image) VALUES (%s,%s,%s)",
                         (event["event_no"], mime, image))

    def operate(self, account: Account, number: int, form: MallOperation, *, connection=None) -> dict:
        """在锁定并重新授权后执行动作，平台决定始终关联原商品单。"""
        action = form.action
        if form.photos and action not in PHOTO_ACTIONS:
            raise ServiceError(400, "请在举证、补证或申请介入时提交照片。")
        if form.offer is not None and action != "negotiate":
            raise ServiceError(400, "方案内容只能由店铺在提出协商方案时提交。")
        photos = decode_photos(form.photos)
        hashes = fingerprints(photos)
        with (self.connect() if connection is None else nullcontext(connection)) as conn:
            row = self._case(conn, account, number, lock=True)
            input_fields = {"carrier", "tracking", "prompt_id", "return_details", "delivery_address",
                            "delivery_problem", "delivery_parcel_no", "delivery_address_version", "delivery_review_id",
                            "refund_attempt_id", "refund_result"}
            payload = form.model_dump(include=input_fields, mode="json", exclude_none=True)
            if form.offer is not None:
                payload["offer_input"] = form.offer.model_dump(mode="json", exclude_none=True)
                if form.offer.amount is not None:
                    payload["offer_input"]["amount"] = f"{form.offer.amount:.2f}"
            if hashes:
                payload["photo_hashes"] = hashes
            previous = conn.execute("""
                SELECT actor_id, action, note, details FROM public.mall_case_events WHERE case_no=%s AND operation_id=%s
            """, (number, form.operation_id)).fetchone()
            if previous is not None:
                if (previous["actor_id"], previous["action"], previous["note"]) == (account.account_id, form.action, form.note) \
                        and all(previous["details"].get(key) == payload.get(key)
                                for key in (*input_fields, "photo_hashes", "offer_input")):
                    return self._details(conn, row, account)
                raise ServiceError(409, "这次操作编号已使用，请刷新进度。")
            view = self._display(row, account)
            if form.action not in view["actions"]:
                raise ServiceError(409, "当前角色或状态不能执行这项操作。")
            filling_details = (action == "provide_return_details" or
                               (action == "support_buyer" and row["active_review_phase"] == "return_details"))
            if filling_details and form.return_details is None:
                raise ServiceError(400, "请填写收件人、寄回地址与联系电话。")
            if not filling_details and form.return_details is not None:
                raise ServiceError(400, "此操作无需填写寄回信息。")
            responds = action in ("accept_offer", "reject_offer", "provide_evidence", "close_prompt")
            if responds and form.prompt_id != row["prompt_id"]:
                raise ServiceError(409, "店铺已更新方案或补证要求，请刷新后再确认。")
            if action == "record_refund_result":
                if form.refund_attempt_id is None or form.refund_result is None:
                    raise ServiceError(400, "请核对本次退款编号，并选择退款结果。")
                if str(form.refund_attempt_id) != row["refund_attempt"]["id"]:
                    raise ServiceError(409, "退款已更新，请核对当前尝试后再操作。")
            elif form.refund_attempt_id is not None or form.refund_result is not None:
                raise ServiceError(400, "此操作无需填写退款结果。")
            if action in ("negotiate", "accept_offer", "refund", "record_refund_result"):
                conn.execute("SELECT item_no FROM public.mall_items WHERE order_id=%s AND item_no=%s FOR UPDATE",
                             (row["order_id"], row["item_no"]))
                row = self._case(conn, account, number)
            offer = prepare_offer(row, form.offer) if action == "negotiate" else None
            if offer is not None:
                payload["offer"] = offer
            if action == "accept_offer" and row["pending_offer"] is not None:
                selection = row["pending_offer"]
                self._verify_offer_balance(row, selection)
                payload["offer"] = selection
            status = next_status(row, form)
            if action in OUTBOUND_ACTIONS:
                payload.update(operate_outbound(conn, row, form))
            elif any(value is not None for value in (form.delivery_address, form.delivery_problem,
                                                     form.delivery_parcel_no, form.delivery_address_version,
                                                     form.delivery_review_id)):
                raise ServiceError(400, "此操作无需填写售后寄出信息。")
            awaiting_details = row["return_details_pending"]
            if action == "buyer_received":
                conn.execute("""UPDATE public.mall_packages SET stage='delivered', delivered_at=now()
                                WHERE order_id=%s AND package_no=%s AND stage='in_transit'""",
                             (row["order_id"], row["package_no"]))
                conn.execute("INSERT INTO public.mall_return_details (case_no) VALUES (%s) ON CONFLICT DO NOTHING", (number,))
                awaiting_details = True
            if filling_details:
                conn.execute("""
                    UPDATE public.mall_return_details SET details=%s,provided_at=now() WHERE case_no=%s
                """, (Jsonb(payload["return_details"]), number))
                awaiting_details = False
            new_prompt = {"request_evidence": "evidence", "negotiate": "offer"}.get(action)
            refund = None
            if responds or (action in ("withdraw", "dispute") and row["prompt_id"]):
                conn.execute("""
                    UPDATE public.mall_case_prompts SET answered_at=now(),response_action=%s
                    WHERE case_no=%s AND operation_id=%s
                """, (action, number, row["prompt_id"]))
            if action == "dispute":
                conn.execute("INSERT INTO public.mall_disputes (case_no, previous_status) VALUES (%s,%s)",
                             (number, row["status"]))
            elif action == "review":
                phase = review_phase(row)
                conn.execute("INSERT INTO public.mall_dispute_reviews (case_no,phase) VALUES (%s,%s)", (number, phase))
                payload["review_phase"] = phase
            elif action in ("support_buyer", "support_seller"):
                if row["active_review_no"] is not None:
                    conn.execute("UPDATE public.mall_dispute_reviews SET decision=%s,decision_at=now() WHERE review_no=%s",
                                 (action, row["active_review_no"]))
                    payload["review_phase"] = row["active_review_phase"]
                else:
                    conn.execute("UPDATE public.mall_disputes SET decision=%s, decision_at=now() WHERE case_no=%s",
                                 (action, number))
                    if (action == "support_buyer" and status == "awaiting_return" and not row["return_details"]
                            and not (row["agreement"] or {}).get("return_instructions")):
                        conn.execute("INSERT INTO public.mall_return_details (case_no) VALUES (%s) ON CONFLICT DO NOTHING",
                                     (number,))
                        awaiting_details = True
            elif action == "refund" or form.refund_result == "succeeded":
                amount, quantity = self._refund_values(row)
                attempt = row["refund_attempt"]
                if attempt and (amount, quantity) != (Decimal(attempt["amount"]), attempt["quantity"]):
                    raise ServiceError(409, "可退款金额或件数已变化，请核对原退款。")
                payload.update(refund_amount=f"{amount:.2f}", refund_quantity=quantity)
                if action == "record_refund_result":
                    refund = (amount, quantity)
            elif action == "reimburse":
                payload["reimbursement_amount"] = str(row["reimbursement_due"])
            elif action == "ship":
                item = self._items(conn, account.account_id, row["order_id"], row["item_no"])[0]
                reason = "wrong_purchase" if row["transit_request"] and row["reason"] == "cancel" else row["reason"]
                items = [item]
                if row["package_lines"]:
                    selected = {line["item_no"] for line in row["package_lines"]}
                    items = [entry for entry in self._items(conn, account.account_id, row["order_id"])
                             if entry["item_no"] in selected]
                available_credit = sum((entry["return_credit"] for entry in items), Decimal("0"))
                quote = freight_quote({**item, "return_credit": available_credit}, reason, kind=handling_kind(row))
                if quote is not None and reason == "wrong_purchase":
                    payload.update(credit_used=str(quote["credit"]), return_postage_due=str(quote["due"]))
                    if row["package_lines"]:
                        # 整包只收一份寄回报价，抵扣按商品顺序分摊并锁定在寄件事件中。
                        remaining = quote["credit"]
                        credits = {}
                        for entry in items:
                            used = min(remaining, entry["return_credit"])
                            if used > 0:
                                credits[str(entry["item_no"])] = str(used)
                                remaining -= used
                        payload["credit_by_item"] = credits
                elif quote is not None:
                    payload.update(advance_amount=str(quote["advance"]), return_postage_fee=str(item["return_postage"]))
            new_delivery_deadline = action == "confirm_delivery_address" and not row["outbound"]
            report_shipped = action == "report_delivery_problem" and form.delivery_problem != "not_dispatched"
            reschedule_delivery = action in (*DISPATCH_ACTIONS, *DELIVERY_REVIEW_ACTIONS, "resume_delivery",
                                             "reissue_delivery") or new_delivery_deadline or report_shipped
            refund_progress_changed = action in ("refund", "record_refund_result")
            if status != row["status"] or new_prompt or responds or filling_details or reschedule_delivery or refund_progress_changed:
                shop_deadline = status in SELLER_PHASES or (status == "awaiting_return" and awaiting_details)
                hours = (48 if new_prompt else row["reply_hours"] if shop_deadline else
                         168 if status == "awaiting_return" else 48 if status == "disputed" else None)
                effective_kind = (row["pending_offer"] or {}).get("kind", handling_kind(row)) if action == "accept_offer" else handling_kind(row)
                if status in DELIVERY_PHASES and effective_kind in DELIVERY_KINDS:
                    if action == "request_delivery_review":
                        hours = 48
                    elif action in (*DISPATCH_ACTIONS, "resume_delivery", "platform_continue_delivery"):
                        hours = None
                    elif new_delivery_deadline or report_shipped or action in ("reissue_delivery", "platform_reissue", "platform_dispatch"):
                        hours = row["reply_hours"]
                    elif not row["outbound"] or (row["outbound"]["stage"] == "shipped" and not row["outbound"]["problem"]):
                        hours = None
                if refund_progress_changed:
                    hours = row["reply_hours"] if form.refund_result == "failed" else None
                due = datetime.now(timezone.utc) + timedelta(hours=hours) if hours is not None else None
                conn.execute("UPDATE public.mall_cases SET status=%s,updated_at=now(),due_at=%s WHERE case_no=%s",
                             (status, due, number))
            else:
                conn.execute("UPDATE public.mall_cases SET updated_at=now() WHERE case_no=%s", (number,))
            self._event(conn, number, form.operation_id, account, action, status, form.note, payload, photos=photos)
            if refund is not None:
                conn.execute("""
                    INSERT INTO public.mall_item_refunds (case_no,operation_id,amount,closed_quantity)
                    VALUES (%s,%s,%s,%s)
                """, (number, form.operation_id, *refund))
            if new_prompt:
                conn.execute("""
                    INSERT INTO public.mall_case_prompts (case_no,operation_id,kind,note) VALUES (%s,%s,%s,%s)
                """, (number, form.operation_id, new_prompt, form.note))
                if offer is not None:
                    conn.execute("""
                        INSERT INTO public.mall_case_offers
                            (case_no,operation_id,kind,quantity,amount,return_instructions)
                        VALUES (%s,%s,%s,%s,%s,%s)
                    """, (number, form.operation_id, offer["kind"], offer["quantity"], offer["amount"],
                          offer["return_instructions"]))
            return self._details(conn, self._case(conn, account, number), account)

    @staticmethod
    def _verify_offer_balance(row: dict, offer: dict) -> None:
        """在确认和退款时再次核对该快照的数量和锁定金额。"""
        if (offer["quantity"] > row["remaining_quantity"] or
                (offer["amount"] is not None and Decimal(offer["amount"]) > refundable(row, offer["quantity"]))):
            raise ServiceError(409, "商品剩余数量或可退款金额已变化，请核对这张单。")

    @classmethod
    def _refund_values(cls, row: dict) -> tuple[Decimal, int]:
        """读取已同意的退款金额，核对商品剩余金额和可关闭件数。"""
        if row.get("package_lines"):
            return (sum((Decimal(line["amount"]) for line in row["package_lines"]), Decimal("0")),
                    sum(line["quantity"] for line in row["package_lines"]))
        agreement = row["agreement"]
        if agreement:
            cls._verify_offer_balance(row, agreement)
            quantity = 0 if agreement["kind"] == "partial_refund" else agreement["quantity"]
            return Decimal(agreement["amount"]), quantity
        if row["quantity"] > row["remaining_quantity"]:
            raise ServiceError(409, "商品剩余数量已变化，请核对这张单。")
        return refundable(row, row["quantity"]), row["quantity"]

    def consult(self, buyer: str, order_id: str, item_no: int, form: MallQuestion, *,
                timings: dict[str, float] | None = None, _retry: bool = True) -> dict:
        """先验证模型请求的商品归属；网络等待期间不占用数据库连接或锁。"""
        started = perf_counter()
        other_order = any(code.upper() != order_id for code in ORDER_CODE_IN_TEXT.findall(form.question))
        split = split_order_questions(form.question) if form.record and not form.reason and not other_order else None
        planning_question = split[0] if split else form.question
        use_model = (self.consult_agent.planner is not None and form.record and form.reason is None
                     and (self.model_dialogue or not is_direct_question(planning_question) or REJECT_REASON.fullmatch(planning_question))
                     and not other_order)
        dialogue = {}
        recent = []
        prepared_item = None
        if use_model:
            with self.connect() as conn:
                if self.model_dialogue:
                    prepared_item = self._items(conn, buyer, order_id, item_no)[0]
                    prepared_item["supplements"] = applicable_supplements(conn, prepared_item)
                    recent = conn.execute("""SELECT turn_no,question,result,context FROM public.mall_consultations
                        WHERE buyer_id=%s AND order_id=%s AND item_no=%s ORDER BY turn_no DESC LIMIT 6""",
                                          (buyer, order_id, item_no)).fetchall()
                    dialogue = snapshot(recent[0]["context"] if recent else {}, recent, prepared_item,
                                        recent[0]["turn_no"] if recent else None)
                else:
                    dialogue = self._planning_context(conn, buyer, order_id, item_no)
        planned_at = perf_counter()

        def respond(topics: list[str], route: str, candidate_filter: dict | None = None, *, follow_up: dict | None = None,
                    semantic_plan: dict | None = None) -> dict:
            """模型和检索在事务外完成，执行阶段重新核对商品状态与会话版本。"""
            resolved_at = perf_counter()
            business_trace=None
            if semantic_plan is not None and route!='fallback' and self.business_decider and prepared_item:
                from app.mall.read_loop import BUSINESS_READS
                if set(topics)&BUSINESS_READS:
                    with self.connect() as conn:
                        page=self._page(conn,Account(buyer,'buyer'),order_id=order_id,item_no=item_no,exact=True)
                    current=next((c for c in page['cases'] if c.get('is_active') or c.get('outbound_pending')),next(iter(page['cases']),None))
                    observed={key:current.get(key) for key in ('case_no','status','status_label','owner','actions','return_details_pending','outbound_pending')} if current else None
                    try:
                        additions=self.business_decider({'question':planning_question,'selected_topics':topics,'observed_case':observed})
                        if not isinstance(additions,list) or len(additions)>3 or any(t not in BUSINESS_READS for t in additions):raise ValueError('无效只读工具')
                        topics=list(dict.fromkeys([*topics,*additions]))
                        business_trace={'observed_case':observed,'added_topics':additions,'status':'completed'}
                    except Exception as error:
                        business_trace={'status':'fallback','error':type(error).__name__}
            args = (buyer, order_id, item_no, form, topics, route, other_order)
            if semantic_plan is not None and route != "fallback":
                evidence = None
                purchase_scope = not (set(topics) & {"shop_current", "platform_floor"}
                                      or "basis" in topics and dialogue.get("policy_reference"))
                if (self.retrieval and prepared_item and purchase_scope and set(topics) &
                        {"basis", "eligibility", "fit_exchange", "freight", "photos", "shop_guidance", "return_window", "warranty"}):
                    from app.mall.read_loop import retrieve_with_followup
                    evidence = retrieve_with_followup(self.retrieval,prepared_item,
                        semantic_plan.get("evidence_query") or planning_question,self.read_decider,user_question=planning_question)
                result = self._answer_consultation(*args, candidate_filter=semantic_plan.get("candidate_filter"),
                    semantic_plan=semantic_plan, planned_version=dialogue.get("version"), recent=recent,
                    retrieved=evidence)
            elif follow_up:
                result = self._answer_consultation(*args, follow_up=follow_up, planned_dialogue=dialogue)
            elif candidate_filter:
                result = self._answer_consultation(*args, candidate_filter=candidate_filter)
            else:
                result = self._answer_consultation(*args)
            if timings is not None:
                timings.update(auth=(planned_at - started) * 1000,
                               plan=(resolved_at - planned_at) * 1000,
                               order=(perf_counter() - resolved_at) * 1000)
            if business_trace is not None:
                result['business_read_loop']=business_trace
                if result.get('_writer_turn'):
                    with self.connect() as conn:
                        conn.execute('UPDATE public.mall_consultations SET result=result || %s WHERE turn_no=%s',
                            (Jsonb({'business_read_loop':business_trace}),result['_writer_turn']))
            return result

        try:
            result = self.consult_agent.run(
                planning_question, use_model, respond, dialogue=dialogue, respond_filtered=respond if not split else None,
                respond_with_follow_up=lambda topics, route, follow_up: respond(topics, route, follow_up=follow_up),
                respond_semantic=(lambda topics, route, plan: respond(topics, route, semantic_plan=plan))
                    if self.model_dialogue and use_model else None,
            )
        except ServiceError as error:
            if self.model_dialogue and error.status_code == 409 and _retry:
                return self.consult(buyer, order_id, item_no, form, timings=timings, _retry=False)
            raise
        turn_no = result.pop("_writer_turn", None)
        if turn_no is not None:
            write_started = perf_counter()
            if self.model_dialogue and self.composer and result.get("route") != "fallback":
                facts = reply_facts(result)
                attempt = 0
                try:
                    generation_dialogue={**dialogue,'recent':[{'user':row['user']} for row in dialogue.get('recent',[])]}
                    payload = {"question": form.question, "dialogue": generation_dialogue,
                                         "facts": facts, "actions": list(result.get("kinds", {})),
                                         "case_exists":bool(result.get("case_no")),
                                         "reason_options":REASONS,
                                         "queried_topics":result.get("topics",[]),
                                         "application_options_checked":result.get("eligibility") is not None,
                                         "evidence": result.get("evidence", [])}
                    for attempt in range(1, 3):
                        remaining=12-(perf_counter()-write_started)
                        if remaining<.5:
                            raise TimeoutError('回复生成预算耗尽')
                        payload={**payload,'_timeout_s':min(6,remaining)}
                        raw = self.composer(payload)
                        try:
                            wording = validate_reply(raw, facts, prior_answer=recent[0]["result"].get("answer", "") if recent else "",
                                allowed_actions=list(result.get("kinds", {})) if result.get('eligibility') is not None else None,
                                case_exists=bool(result.get("case_no")))
                            if self.reviewer and reply_review_needed(facts):
                                remaining=12-(perf_counter()-write_started)
                                if remaining<.5:
                                    raise TimeoutError('回复核对预算耗尽')
                                review=self.reviewer({'question':form.question,'facts':facts,'reply':wording,'_timeout_s':min(4,remaining)})
                                if not review.get('supported'):
                                    raise ValueError('事实语义核对：'+'；'.join(review.get('issues',[]))[:400])
                            break
                        except ValueError as error:
                            if attempt == 2:
                                raise
                            payload = {**payload, "validation_feedback":str(error)}
                    patch = {"answer": wording, "answer_source": "model", "generation_status": "grounded",
                             "generation_attempts":attempt,"generation_reviewed":bool(self.reviewer and reply_review_needed(facts))}
                    with self.connect() as conn:
                        conn.execute("UPDATE public.mall_consultations SET result=result || %s WHERE turn_no=%s",
                                     (Jsonb(patch), turn_no))
                    result.update(patch)
                except (ModelError, TimeoutError, ValueError, TypeError, KeyError, ServiceError) as error:
                    patch = {"generation_status": "fallback", "answer_source": "verified_fallback",
                             "generation_attempts":attempt,
                             "generation_error": str(error) if type(error) is ValueError else type(error).__name__}
                    with self.connect() as conn:
                        conn.execute("UPDATE public.mall_consultations SET result=result || %s WHERE turn_no=%s",
                                     (Jsonb(patch), turn_no))
                    result.update(patch)
            else:
                self._write_consultation(turn_no, form.question, result)
            if timings is not None:
                timings["compose"] = (perf_counter() - write_started) * 1000
        if timings is not None:
            timings["consult"] = (perf_counter() - started) * 1000
        return result

    @staticmethod
    def _planning_context(conn, buyer: str, order_id: str, item_no: int) -> dict:
        """兼容旧版咨询器的最小上下文读取。"""
        owned = conn.execute("""SELECT last_turn.context
                    FROM public.mall_items i JOIN public.mall_orders o USING(order_id)
                    LEFT JOIN LATERAL (
                        SELECT context FROM public.mall_consultations
                        WHERE buyer_id=o.buyer_id AND order_id=i.order_id AND item_no=i.item_no
                          AND (NOT (result ? 'order_candidates') OR result ? 'current_question')
                        ORDER BY turn_no DESC LIMIT 1
                    ) last_turn ON true
                    WHERE i.order_id=%s AND i.item_no=%s AND o.buyer_id=%s""",
                    (order_id, item_no, buyer)).fetchone()
        if not owned:
            raise ServiceError(404, "查不到此订单商品。")
        return consultation_context(owned.get("context") or {})

    def _write_consultation(self, turn_no: int, question: str, result: dict) -> None:
        """在查询事务结束后撰写回复；失败时沿用该轮已保存的业务答复。"""
        if self.writer is None or result["route"] == "fallback" or result.get("purchase_history") or result.get("pending_question"):
            return
        redirect = result.get("response_kind") == "out_of_scope"
        if (result.get("response_kind") and not redirect) or result.get("select_order"):
            return
        term = order_search_term(question)
        if redirect:
            facts = {"任务": "引导回售后", "可协助": ["物流", "退换货", "退款进度"]}
        elif term is not None and result.get("order_candidates"):
            facts = {"任务": "查找买家订单", "关键词": term, "有匹配": True,
                     "候选商品": list(dict.fromkeys(row["title"] for row in result["order_candidates"]))}
        elif result["topics"] == ["product"] and not any(result.get(field) for field in
                ("reason", "case_no", "evidence_requested", "reset_suggestion", "quick_replies", "select_order",
                 "eligibility", "freight", "intervention")):
            facts = {"任务": "介绍当前商品", "当前商品": result["reference"]["title"], "核对结果": result["answer"]}
        else:
            return
        try:
            wording = self.writer(facts).strip()
            allowed_numbers = set(re.findall(r"\d+(?:\.\d+)?", str(facts)))
            if (not wording or len(wording) > 180 or not set(re.findall(r"\d+(?:\.\d+)?", wording)) <= allowed_numbers
                    or re.search(r"已退款|已提交|已取消|已补发|已为你办理|已申请|保证|赔偿|商品卡|点击|下方|按钮|哪个商品|哪笔订单|提供订单号", wording)):
                return
            if not redirect and re.search(r"[？?]|请问", wording):
                return
            if redirect and (len(wording) > 90 or "售后" not in wording or not re.search(r"物流|退换货|退款进度", wording)):
                return
            patch = {"answer": wording, "answer_source": "model"}
            with self.connect() as conn:
                conn.execute("UPDATE public.mall_consultations SET result=result || %s WHERE turn_no=%s",
                             (Jsonb(patch), turn_no))
        except (ModelError, TimeoutError, ServiceError):
            return
        result.update(patch)

    @staticmethod
    def _search_buyer_items(conn, buyer: str, term: str, *, recent: bool = False) -> dict:
        """按登录买家查找可咨询的商品行，每张卡绑定实际订单与商品。"""
        limit = 6 if recent else 50
        rows = conn.execute("""SELECT o.order_id,i.item_no,i.title,i.variant,s.name AS shop_name,i.paid_unit::text AS paid_unit,
                (o.ordered_at AT TIME ZONE 'Asia/Shanghai')::date AS purchased
            FROM public.mall_orders o JOIN public.mall_shops s USING (shop_id)
            JOIN public.mall_items i USING (order_id)
            WHERE o.buyer_id=%s AND strpos(lower(concat_ws(' ',o.order_id,s.name,i.title,i.variant)),lower(%s))>0
            ORDER BY o.ordered_at DESC,o.order_id DESC,i.item_no LIMIT %s""", (buyer, term, limit + 1)).fetchall() if term or recent else []
        if recent:
            message = "这是你最近买过的商品。完整记录可以在「我的订单」里看。" if rows else "你的账号还没有购买记录。"
        elif not term:
            message = "你想找哪件商品？可以发「找订单：无线鼠标」，也可以填店名或订单号尾号。"
        elif not rows:
            message = "你的订单里没找到匹配商品。可以换个关键词试试，比如「找订单：鼠标」。"
        elif len(rows) > limit:
            message = f"还有更多匹配结果，先列出最近的 {limit} 件。可换更具体的关键词，或在我的订单中查看全部。"
        else:
            message = "找到了匹配的商品。"
        return {"answer": message, "order_candidates": rows[:limit], "purchase_history": recent,
                "search_has_more": len(rows) > limit}

    @staticmethod
    def _filter_buyer_candidates(conn, buyer: str, candidates: list[dict], filters: dict) -> list[dict]:
        """用买家名下真实成交单价和购买日期筛选，等价结果全部保留。"""
        if not candidates:
            return []
        records = conn.execute("""SELECT o.order_id,i.item_no,i.paid_unit,
                (o.ordered_at AT TIME ZONE 'Asia/Shanghai')::date AS purchased
            FROM unnest(%s::text[],%s::integer[]) wanted(order_id,item_no)
            JOIN public.mall_orders o USING(order_id)
            JOIN public.mall_items i ON i.order_id=wanted.order_id AND i.item_no=wanted.item_no
            WHERE o.buyer_id=%s""",
            ([row["order_id"] for row in candidates], [row["item_no"] for row in candidates], buyer)).fetchall()
        facts = {(row["order_id"], row["item_no"]): row for row in records
                 if (not filters.get("year") or row["purchased"].year == filters["year"])
                 and (not filters.get("month") or row["purchased"].month == filters["month"])
                 and (not filters.get("day") or row["purchased"].day == filters["day"])}
        if facts and filters.get("price"):
            extreme = min if filters["price"] == "lowest" else max
            price = extreme(row["paid_unit"] for row in facts.values())
            facts = {key: row for key, row in facts.items() if row["paid_unit"] == price}
        return [{**row, "paid_unit": str(facts[key]["paid_unit"]), "purchased": facts[key]["purchased"]}
                for row in candidates if (key := (row["order_id"], row["item_no"])) in facts]

    def _answer_consultation(self, buyer: str, order_id: str, item_no: int, form: MallQuestion,
                             topics: list[str], route: str, other_order: bool, *, connection=None,
                             candidate_filter: dict | None = None, follow_up: dict | None = None,
                             planned_dialogue: dict | None = None, semantic_plan: dict | None = None,
                             planned_version: int | None = None, recent: list[dict] | None = None,
                             retrieved: dict | None = None) -> dict:
        """按当前账号、商品和下单条款核对只读事项，记录实际引用与路由。"""
        split = split_order_questions(form.question) if form.record and not form.reason and not other_order else None
        question = split[0] if split else form.question
        if split:
            topics = [topic for topic in topics if topic not in {"purchase_history", "clarify_order"}]
        with (self.connect() if connection is None else nullcontext(connection)) as conn:
            if form.record and self.model_dialogue:
                conn.execute('''SELECT p.package_no FROM public.mall_packages p
                    JOIN public.mall_orders o USING(order_id) JOIN public.mall_items i USING(order_id,package_no)
                    WHERE p.order_id=%s AND o.buyer_id=%s AND i.item_no=%s FOR NO KEY UPDATE OF p''',
                    (order_id,buyer,item_no)).fetchone()
            item = self._items(conn, buyer, order_id, item_no, lock=form.record)[0]
            if not other_order and ("original_shipping" in topics or "amount_followup" in topics):
                checkout = conn.execute("SELECT shipping FROM public.mall_order_checkout WHERE order_id=%s",
                                        (order_id,)).fetchone()
                item["shipping"] = checkout["shipping"] if checkout else None
            key = (buyer, order_id, item_no)
            previous = conn.execute("""
                SELECT context,result,turn_no FROM public.mall_consultations WHERE buyer_id=%s AND order_id=%s AND item_no=%s
                  AND (NOT (result ? 'order_candidates') OR result ? 'current_question')
                ORDER BY turn_no DESC LIMIT 1
            """, key).fetchone() if form.record else None
            context = previous["context"] if previous else {}
            if semantic_plan is not None:
                actual = conn.execute("""SELECT turn_no FROM public.mall_consultations
                    WHERE buyer_id=%s AND order_id=%s AND item_no=%s ORDER BY turn_no DESC LIMIT 1""", key).fetchone()
                if (actual["turn_no"] if actual else None) != planned_version:
                    raise ServiceError(409, "对话已更新，正在按最新消息重新核对。")
            if follow_up and planned_dialogue != consultation_context(context):
                follow_up = None
            search_term = order_search_term(form.question) if form.record and not form.reason else None
            if semantic_plan and semantic_plan.get("search_query"):
                search_term = semantic_plan["search_query"]
            recent_search = form.record and not form.reason and not other_order and (
                asks_purchase_history(form.question) or "purchase_history" in topics)
            looking_up = search_term is not None or recent_search
            needs_selection = (not split and not looking_up and not other_order and form.record and not form.reason
                               and (refers_to_listed_order(form.question) or "clarify_order" in topics))
            last_result = {}
            if form.record:
                last = conn.execute("""SELECT result FROM public.mall_consultations
                    WHERE buyer_id=%s AND order_id=%s AND item_no=%s ORDER BY turn_no DESC LIMIT 1""",
                    key).fetchone()
                last_result = last["result"] if last else {}
            candidates = last_result.get("order_candidates", [])
            pending_question = last_result.get("pending_question")
            dropped = bool(pending_question and not split and drops_pending_order(question))
            current_item_query = not (looking_up or needs_selection or other_order)
            citation_query = current_item_query and not form.reason and resolve_citation_query(question, topics, context)
            if current_item_query and (semantic_plan is not None or citation_query or context.get("reason") == "missing"
                                    or reported_reason(question) == "missing" or form.reason == "missing"):
                supplements = applicable_supplements(conn, item)
                item["supplements"] = supplements
                # 旧版整店补发说明继续用于自动答复，有标题的细分条款按问点检索。
                item["missing_supplement"] = next((row for row in supplements if row["title"] is None), None)
            if current_item_query and ("shop_current" in topics or "platform_floor" in topics or
                                    "basis" in topics and context.get("policy_reference")):
                version = context.get("policy_reference") if "basis" in topics and not {"shop_current", "platform_floor"}.intersection(topics) else None
                item["current_policy"] = current_policy(conn, item["shop_id"], version)
            if looking_up:
                result = self._search_buyer_items(conn, buyer, search_term or "", recent=recent_search and search_term is None)
                topics = ["purchase_history" if result["purchase_history"] else "search_buyer_orders"]
            elif dropped and not topics:
                result = {"answer": "好，先不查另一笔订单。", "order_candidates": []}
            elif needs_selection:
                narrowed = narrow_order_candidates(question, candidates, datetime.now(CHINA).date())
                filter_pool = candidates or last_result.get("filter_pool", [])
                if candidate_filter and narrowed is None:
                    narrowed = self._filter_buyer_candidates(conn, buyer, filter_pool, candidate_filter)
                if narrowed is not None:
                    candidates = narrowed
                result = {
                    "answer": (
                        "是这件商品吗？请点卡片确认。" if narrowed is not None and len(candidates) == 1 else
                        "找到几笔符合的订单，你记得是哪天买的吗？" if narrowed is not None and candidates else
                        "这批候选里没找到符合的订单，可以用商品名或订单号重新查找。" if narrowed is not None else
                        "你指哪一笔？点对应的商品卡进入那笔订单，再继续问。" if candidates else
                        "你指哪一笔订单？可以发「找订单：商品名」，或者从我的订单选择。"
                    ),
                    "order_candidates": candidates,
                    "select_order": not candidates,
                }
                if candidate_filter:
                    result["candidate_filter"] = candidate_filter
                    if not narrowed:
                        result["filter_pool"] = filter_pool
                topics = ["clarify_order"]
            elif other_order:
                result = {"answer": f"当前咨询的是 {order_id} 的{item['title']}。你提到了其他订单，请从「我的订单」选择要咨询的商品。",
                          "select_order": True, "evidence": [], "quick_replies": [],
                          "terms_title": item["terms_title"], "terms_version": item["terms_version"]}
                topics = []
            else:
                only_product = (topics == ["product"] and not form.reason and not identify_reason(question)
                                and not context.get("awaiting") and semantic_plan is None)
                citation_only = citation_query and not other_query_parts(question) and semantic_plan is None
                cases = [] if only_product or citation_only else self._page(conn, Account(buyer, "buyer"), order_id=order_id,
                                                           item_no=item_no, exact=True)["cases"]
                active_case = next((case for case in cases if case["status"] in ACTIVE), None)
                if semantic_plan is not None:
                    if retrieved and retrieved["scope"] != scope_key(item):
                        raise ServiceError(409, "适用条款已更新，正在重新核对。")
                    result, context = execute_semantic(item, question, context, cases, topics, semantic_plan,
                        datetime.now(CHINA).date(), active_case=active_case, recent=recent or [], retrieval=retrieved)
                    result['planned_topics']=topics
                    topics=result.get('executed_topics',topics)
                    result["tool_trace"] = [{"tool": topic, "scope": "current_item", "status": "completed"} for topic in topics]
                else:
                    result, updated_context = answer(item, question, form.reason, context, cases[0] if cases else None,
                                             datetime.now(CHINA).date(), active_case=active_case, topics=topics,
                                             last_result=previous["result"] if previous else None, follow_up=follow_up)
                    context = merge_fallback(context, updated_context, result) if self.model_dialogue else updated_context
                if "evidence" not in result:
                    result["evidence"] = consultation_evidence(item, topics, result)
                if form.record:
                    result["current_question"] = question
            if not dropped and (split or pending_question):
                result["pending_question"] = split[1] if split else pending_question
                result.setdefault("order_candidates", candidates)
                result["order_candidates"] = [row for row in result["order_candidates"] if row["order_id"] != order_id]
                result.setdefault("select_order", not result["order_candidates"])
            result["reference"] = {"order_id": order_id, "item_no": item_no, "title": item["title"],
                                   "package_no": item["package_no"], "shop_id": item["shop_id"],
                                   "terms_version": item["terms_version"]}
            result["route"] = route
            result["topics"] = topics
            if semantic_plan is not None:
                result.setdefault('task_signature',task_signature(context))
                if 'tool_facts' not in result:
                    result['tool_facts']=build_facts(item,result,context,topics,[],datetime.now(CHINA).date())
            if form.record:
                saved = conn.execute("""
                    INSERT INTO public.mall_consultations (buyer_id,order_id,item_no,question,result,context)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    RETURNING turn_no
                """, (*key, form.question, Jsonb(jsonable_encoder(result)), Jsonb(context)))
                turn_no = saved.fetchone()["turn_no"] if result.get("pending_question") or self.writer or self.composer or result.get('action_draft') else None
                if (result.get('action_draft') or {}).get('state')=='ready':
                    result['action_draft']['turn_no']=str(turn_no)
                    conn.execute('UPDATE public.mall_consultations SET result=result || %s WHERE turn_no=%s',
                        (Jsonb({'action_draft':result['action_draft']}),turn_no))
                if result.get("pending_question"):
                    result["pending_turn"] = str(turn_no)
                if self.writer is not None or self.composer is not None:
                    result["_writer_turn"] = turn_no
            return result

    def confirm_chat_action(self,buyer: str,order_id: str,item_no: int,draft_id,form) -> dict:
        """确认卡与现有业务接口共享一个事务，不让模型执行写库。"""
        from app.storage.mall_chat_actions import confirm
        return confirm(self,buyer,order_id,item_no,draft_id,form)

    def continue_consultation(self, buyer: str, order_id: str, item_no: int, pending_turn: int,
                              target_order: str, target_item: int) -> dict:
        """在同一事务里接着回答目标订单，并完成来源待办。"""
        if order_id == target_order:
            raise ServiceError(400, "请选择另一笔订单。")
        target = {"order_id": target_order, "item_no": target_item}
        with self.connect() as conn:
            # 两个会话按固定顺序获得商品锁，避免反向续问互相等待。
            for code, no in sorted(((order_id, item_no), (target_order, target_item))):
                self._items(conn, buyer, code, no, lock=True)
            rows = conn.execute("""SELECT turn_no,result FROM public.mall_consultations
                WHERE buyer_id=%s AND order_id=%s AND item_no=%s
                AND (turn_no=%s OR turn_no=(SELECT max(turn_no) FROM public.mall_consultations
                    WHERE buyer_id=%s AND order_id=%s AND item_no=%s))
                ORDER BY turn_no DESC""", (buyer, order_id, item_no, pending_turn, buyer, order_id, item_no)).fetchall()
            requested = next((row["result"] for row in rows if row["turn_no"] == pending_turn), {})
            if requested.get("continued_to") == target:
                return target
            question = requested.get("pending_question")
            if not question or rows[0]["turn_no"] != pending_turn:
                raise ServiceError(409, "这条待办已变化，请刷新对话后再选择。")
            if not any(all(row[key] == value for key, value in target.items()) for row in requested["order_candidates"]):
                raise ServiceError(409, "该商品不在当前候选里，请重新查找。")
            self._answer_consultation(buyer, target_order, target_item, MallQuestion(question=question),
                                      question_topics(question), "rules", False, connection=conn)
            conn.execute("""UPDATE public.mall_consultations SET result=result || %s WHERE turn_no=%s""",
                         (Jsonb({"pending_question": None, "continued_to": target}), pending_turn))
            return target

    def consultation_history(self, buyer: str, order_id: str, item_no: int) -> dict:
        """只恢复当前买家和商品下最近三十条咨询。"""
        with self.connect() as conn:
            self._items(conn, buyer, order_id, item_no)
            rows = conn.execute("""
                SELECT turn_no,question,result FROM public.mall_consultations WHERE buyer_id=%s AND order_id=%s AND item_no=%s
                ORDER BY turn_no DESC LIMIT 30
            """, (buyer, order_id, item_no)).fetchall()
            from app.mall.chat_actions import case_signature
            cases=self._page(conn,Account(buyer,'buyer'),order_id=order_id,item_no=item_no,exact=True)['cases']
            active=next((case for case in cases if case.get('is_active') or case.get('outbound_pending')),next(iter(cases),None))
            current={key:active.get(key) for key in ('case_no','status','status_label','owner','actions','is_active','outbound_pending')} if active else None
            if current:current['is_active']=bool(active.get('is_active') or active.get('outbound_pending'))
            turns = []
            for row in reversed(rows):
                result = dict(row["result"])
                draft=result.get('action_draft')
                if draft and draft.get('state')=='ready' and not result.get('action_execution'):
                    original=next((case for case in cases if str(case['case_no'])==str(draft.get('case_no'))),None)
                    stale=(draft['action']=='submit' and active and active.get('is_active') or
                           draft['action']!='submit' and (not original or case_signature(original)!=draft.get('case_signature')))
                    if stale:result['action_draft']={**draft,'state':'outdated'}
                if result.get("pending_question"):
                    result["pending_turn"] = str(row["turn_no"])
                turns.append({"question": row["question"], "result": result})
            return {"turns": turns,'current_case':current}


