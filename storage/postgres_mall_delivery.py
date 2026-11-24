"""保存买家确认的收件快照、店铺售后寄出和收货异常。"""

from psycopg.types.json import Jsonb

from app.mall.rules import DELIVERY_REVIEW_ACTIONS, DELIVERY_REVIEW_DECISIONS, DISPATCH_ACTIONS, MallOperation
from app.tickets.service_requests import ServiceError


OUTBOUND_ACTIONS = ("confirm_delivery_address", "confirm_delivery", "report_delivery_problem",
                    "resume_delivery", "reissue_delivery", *DISPATCH_ACTIONS, *DELIVERY_REVIEW_ACTIONS)


def display_delivery_address(details: dict | None) -> dict | None:
    """为历史收货快照中系统生成的占位名称提供常规显示形式。"""
    if not details:
        return details
    legacy = {
        ("演练收件人 A", "本地演练园区 A 区收件点"): ("周宁", "杭州星桥街道澄景苑 1 栋"),
        ("演练收件人 B", "本地演练园区 B 区收件点"): ("沈悦", "苏州观澜街道知春苑 2 栋"),
    }
    address = legacy.get((details["recipient"], details["address"]))
    return dict(details, recipient=address[0], address=address[1]) if address else details


def operate_outbound(conn, row: dict, form: MallOperation) -> dict:
    """在原售后单已锁定且操作已获授权时写入售后包裹。"""
    action = form.action
    number = row["no"]
    parcel = row["outbound"]
    if form.delivery_parcel_no != (parcel["parcel_no"] if parcel else None):
        raise ServiceError(409, "售后包裹已变化，请刷新后再操作。")
    if (action == "confirm_delivery_address") != bool(form.delivery_address):
        raise ServiceError(400, "确认收货地址时请填写收件人、地址和电话。")
    if (action == "report_delivery_problem") != bool(form.delivery_problem):
        raise ServiceError(400, "反馈售后包裹问题时请选择问题类型。")
    if action in DELIVERY_REVIEW_DECISIONS:
        if str(form.delivery_review_id) != (row["delivery_review"] or {}).get("request_id"):
            raise ServiceError(409, "平台处理申请已变化，请刷新后核对再处理。")
    elif form.delivery_review_id is not None:
        raise ServiceError(400, "此操作无需填写平台处理申请编号。")

    if action == "confirm_delivery_address":
        destination = form.delivery_address.model_dump()
        if parcel is None:
            saved = conn.execute("""
                INSERT INTO public.mall_outbound_parcels (case_no,destination,stage)
                VALUES (%s,%s,'ready') RETURNING parcel_no
            """, (number, Jsonb(destination))).fetchone()
            return {"outbound_parcel_no": str(saved["parcel_no"])}
        if destination == display_delivery_address(parcel["destination"]):
            raise ServiceError(409, "收货信息已确认。修改收件人、地址或电话后才需要提交。")
        if form.delivery_address_version != parcel["address_version"]:
            raise ServiceError(409, "收货信息已在其他页面修改，请核对最新信息后再保存。")
        conn.execute("""
            UPDATE public.mall_outbound_parcels SET destination=%s,address_version=address_version+1 WHERE parcel_no=%s
        """, (Jsonb(destination), parcel["parcel_no"]))
        return {"outbound_parcel_no": parcel["parcel_no"], "delivery_address_changed": True}

    parcel_no = parcel["parcel_no"]
    review = {}
    if action == "request_delivery_review":
        default_problem = "not_dispatched" if parcel["stage"] == "ready" else "not_received"
        review["review_problem"] = parcel["problem"] or default_problem
    elif action in DELIVERY_REVIEW_DECISIONS:
        review["review_problem"] = row["delivery_review"]["problem"]
    if action in DISPATCH_ACTIONS:
        if form.delivery_address_version != parcel["address_version"]:
            raise ServiceError(409, "收货地址已变化，请刷新并核对后再登记发货。")
        duplicate = conn.execute("""
            SELECT 1 FROM public.mall_outbound_parcels WHERE case_no=%s AND lower(tracking)=lower(%s)
            UNION ALL
            SELECT 1 FROM public.mall_case_events
            WHERE case_no=%s AND action='ship' AND lower(details->>'tracking')=lower(%s)
            LIMIT 1
        """, (number, form.tracking, number, form.tracking)).fetchone()
        if duplicate or (row["outbound_tracking"] or "").lower() == form.tracking.lower():
            raise ServiceError(409, "请填写这次售后寄出的新运单号。")
        conn.execute("""
            UPDATE public.mall_outbound_parcels
            SET stage='shipped',carrier=%s,tracking=%s,shipped_at=now(),problem=NULL,issue_at=NULL
            WHERE parcel_no=%s
        """, (form.carrier, form.tracking, parcel_no))
    elif action == "confirm_delivery":
        conn.execute("""
            UPDATE public.mall_outbound_parcels
            SET stage='received',received_at=now(),problem=NULL,issue_at=NULL WHERE parcel_no=%s
        """, (parcel_no,))
    elif action == "report_delivery_problem":
        if (form.delivery_problem == "not_dispatched") != (parcel["stage"] == "ready"):
            raise ServiceError(400, "问题类型与当前售后包裹状态不符。")
        conn.execute("UPDATE public.mall_outbound_parcels SET problem=%s,issue_at=now() WHERE parcel_no=%s",
                     (form.delivery_problem, parcel_no))
    elif action in ("resume_delivery", "platform_continue_delivery"):
        conn.execute("UPDATE public.mall_outbound_parcels SET problem=NULL,issue_at=NULL WHERE parcel_no=%s",
                     (parcel_no,))
    elif action in ("reissue_delivery", "platform_reissue"):
        conn.execute("UPDATE public.mall_outbound_parcels SET stage='superseded' WHERE parcel_no=%s", (parcel_no,))
        saved = conn.execute("""
            INSERT INTO public.mall_outbound_parcels (case_no,destination,stage)
            VALUES (%s,%s,'ready') RETURNING parcel_no
        """, (number, Jsonb(parcel["destination"]))).fetchone()
        return {"outbound_parcel_no": str(saved["parcel_no"]), "previous_outbound_parcel_no": parcel_no, **review}
    return {"outbound_parcel_no": parcel_no, **review}
