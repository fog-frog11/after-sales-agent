"""定义本地商城条款、可选售后、处理阶段和买家咨询所用的规则。"""

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.mall.photos import MAX_PHOTOS, PhotoInput
from app.tickets.service_requests import ServiceError


Kind = Literal["refund_only", "return_refund", "exchange", "repair", "reship"]
OfferKind = Literal["partial_refund", "refund_only", "return_refund", "exchange", "repair", "reship"]
Reason = Literal["wrong_purchase", "quality", "wrong_item", "missing", "not_received", "cancel"]
Action = Literal["approve", "reject", "ship", "receive", "refund", "record_refund_result", "replace", "repair_complete", "reship",
                 "negotiate", "request_evidence", "provide_evidence", "accept_offer", "reject_offer", "close_prompt",
                 "revise", "withdraw", "evidence", "dispute", "review", "provide_return_details", "support_buyer",
                 "support_seller", "reimburse", "confirm_delivery_address", "confirm_delivery",
                 "report_delivery_problem", "resume_delivery", "reissue_delivery", "request_delivery_review",
                 "platform_dispatch", "platform_reissue", "platform_continue_delivery",
                 "carrier_return", "carrier_interception_failed", "buyer_received"]
KINDS = {"refund_only": "仅退款", "return_refund": "退货退款", "exchange": "换货", "repair": "维修", "reship": "补发",
         "partial_refund": "留货部分退款"}
REASONS = {"wrong_purchase": "买错或不想要", "quality": "质量问题", "wrong_item": "发错商品", "missing": "少件漏发",
           "not_received": "没有收到", "cancel": "取消未发货商品"}
STATES = {"submitted": "待店铺响应", "awaiting_return": "待买家寄回", "return_sent": "待店铺确认收件",
          "received": "待店铺处理结果", "awaiting_outcome": "待店铺执行方案", "rejected": "店铺已拒绝",
          "disputed": "平台处理中", "refunded": "已登记退款", "replacement_sent": "已寄出换货商品",
          "reshipped": "已补发", "repaired": "已寄回维修商品", "closed": "争议已关闭", "withdrawn": "买家已撤回"}
ACTIVE = ("submitted", "awaiting_return", "return_sent", "received", "awaiting_outcome", "rejected", "disputed")
RETURN_KINDS = ("return_refund", "exchange", "repair")
REFUND_KINDS = ("refund_only", "return_refund", "partial_refund")
DELIVERY_KINDS = ("exchange", "repair", "reship")
DELIVERY_PHASES = ("received", "awaiting_outcome")
DISPATCH_ACTIONS = ("replace", "repair_complete", "reship")
DELIVERY_REVIEW_DECISIONS = ("platform_dispatch", "platform_reissue", "platform_continue_delivery")
DELIVERY_REVIEW_ACTIONS = ("request_delivery_review", *DELIVERY_REVIEW_DECISIONS)
DeliveryProblem = Literal["not_dispatched", "not_received", "wrong_item", "damaged"]
DELIVERY_PROBLEMS = {"not_dispatched": "店铺逾期未发", "not_received": "未收到售后包裹",
                     "wrong_item": "收到的商品发错", "damaged": "收到的商品破损"}
SELLER_PHASES = ("submitted", "return_sent", "received", "awaiting_outcome")
RESULTS = {"refund_only": "refund", "return_refund": "refund", "partial_refund": "refund", "exchange": "replace", "repair": "repair_complete",
           "reship": "reship"}
OUTCOMES = {"replace": "replacement_sent", "repair_complete": "repaired", "reship": "reshipped"}
REFUND_PROGRESS = {"pending": "退款处理中", "failed": "退款失败 · 待店铺重试", "succeeded": "退款成功"}
PROMPT_STATES = {"evidence": "待买家补充凭证", "offer": "待买家确认方案"}
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
ParcelCode = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=40,
                                             pattern=r"^[a-zA-Z0-9-]+$")]
INTENT_REASONS = (
    ("missing", r"少了|少发|漏发|少件|缺件|(?:充电线|鼠标线|配件)没给我"), ("not_received", r"没收到|未收到|没有收到|未到货"),
    ("wrong_item", r"发错|寄错|型号不对"), ("quality", r"质量|坏了|破损|断裂|开裂|已经裂了|故障|不通电|开不了机|瑕疵"),
    ("wrong_purchase", r"买错|拍错|拍多了|下错单|买重了|选错了|不想要|不想等|不取件|拒收|拦截|不喜欢|无理由"), ("cancel", r"取消|没发货|未发货"),
)


class MallApplication(BaseModel):
    """记录买家确认的商品、申请事项、数量和凭证。"""

    submission_id: UUID
    item_no: Annotated[int, Field(strict=True, gt=0, le=2**31 - 1)]
    quantity: Annotated[int, Field(strict=True, gt=0, le=2**31 - 1)]
    kind: Kind
    reason: Reason
    description: Text
    photos: list[PhotoInput] = Field(default_factory=list, max_length=MAX_PHOTOS)


class MallPackageApplication(BaseModel):
    """买家只选包裹及原因，申请商品和退款上限由服务器核对。"""

    model_config = ConfigDict(extra="forbid")
    submission_id: UUID
    reason: Literal["wrong_purchase", "cancel"]
    description: Text
    photos: list[PhotoInput] = Field(default_factory=list, max_length=MAX_PHOTOS)


class MallOffer(BaseModel):
    """店铺提出的处理类型、适用件数和必要履约信息。"""

    model_config = ConfigDict(extra="forbid")
    kind: OfferKind
    quantity: Annotated[int, Field(strict=True, gt=0, le=2**31 - 1)]
    amount: Annotated[Decimal, Field(gt=0, max_digits=20, decimal_places=2)] | None = None
    return_instructions: Text | None = None


class ReturnDetails(BaseModel):
    """一段寄件的收件信息，用于填写对应运单。"""

    model_config = ConfigDict(extra="forbid")
    recipient: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    address: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=240)]
    phone: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[0-9+ ()-]{5,30}$")]


class MallOperation(BaseModel):
    """表示重复请求可识别的协商、流转或争议操作。"""

    model_config = ConfigDict(extra="forbid")
    operation_id: UUID
    action: Action
    note: Text
    carrier: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=30)] | None = None
    tracking: ParcelCode | None = None
    photos: list[PhotoInput] = Field(default_factory=list, max_length=MAX_PHOTOS)
    prompt_id: UUID | None = None
    offer: MallOffer | None = None
    return_details: ReturnDetails | None = None
    delivery_address: ReturnDetails | None = None
    delivery_problem: DeliveryProblem | None = None
    delivery_parcel_no: Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]{0,18}$")] | None = None
    delivery_address_version: Annotated[int, Field(strict=True, gt=0)] | None = None
    delivery_review_id: UUID | None = None
    refund_attempt_id: UUID | None = None
    refund_result: Literal["succeeded", "failed"] | None = None


class MallQuestion(BaseModel):
    """表示绑定订单商品的买家咨询及已经确认的理由。"""

    question: Text
    reason: Reason | None = None
    record: bool = True


def policy_clauses(item: dict) -> dict[str, str]:
    """按事项标识生成下单版本的正文，订单展示和咨询引用共用一份。"""
    clauses = {
        "window": f"签收后 {item['return_days']} 天内，参与无理由退换的商品可申请退货或换货。"
        f"质量问题、发错或少件在签收后 {item['fault_days']} 天内可申请对应售后。",
        "service": f"支持维修的商品在签收后 {item['warranty_days']} 天内可申请维修。"
        "取消未发货商品与在途超期未收到，可申请仅退款。",
        "freight": f"无理由退换由买家承担寄回运费，商城寄回报价为 ¥{item['return_postage']:.2f}，"
        "该笔商品未使用的退货运费权益优先抵扣。核实为商品质量或发错时由店铺承担。",
        "response": f"店铺办理阶段的响应期限为 {item['reply_hours']} 小时。商家拒绝或有待办事项超过期限，"
        "买家可说明理由并申请平台介入。",
    }
    if item.get("platform_version"):
        clauses["credit"] = f"每条订单商品明细至少附 ¥{item['item_credit']:.2f} 寄回运费权益，使用后按剩余权益核对。"
        clauses["guidance"] = item["guidance"]
    return clauses


def policy_lines(item: dict) -> list[str]:
    """依照订单页的原有顺序返回本店本版的完整条款。"""
    return list(policy_clauses(item).values())


@dataclass(frozen=True)
class Eligibility:
    """办理方式、当前原因的判断及用户可以采取的下一步。"""

    kinds: tuple[str, ...]
    message: str
    next_step: str | None = None
    transit: bool = False

    @property
    def choices(self) -> dict[str, str]:
        """按推荐顺序返回应用表单需要的办理方式。"""
        return {kind: "在途退回退款" if self.transit else KINDS[kind] for kind in self.kinds}


def assess_eligibility(item: dict, reason: str, today: date) -> Eligibility:
    """统一为售后咨询、表单预检和正式提交核对商品资格。"""
    if item.get("remaining_quantity", item["quantity"]) <= 0:
        return Eligibility((), "这件商品已全部登记退款。", "可在订单详情查看原售后进度。")
    stage = item["stage"]
    if stage == "preparing":
        if reason in ("wrong_purchase", "cancel"):
            return Eligibility(("refund_only",), "这件商品尚未发货，可以申请仅退款，无需寄回。")
        return Eligibility((), "这件商品尚未发货，暂不能按收货后的问题办理。",
                           "如希望取消购买，请选择「取消未发货商品」。")
    if stage == "in_transit":
        expected = item["expected_on"]
        if reason in ("wrong_purchase", "cancel"):
            return Eligibility(("return_refund",), "尚未签收，可以申请在途退回退款。",
                               "店铺先核对包裹。整包不要时可联系快递拦截或明确拒收；仅不取件不会确认退回。"
                               "快递受理后沿原运单退回，店铺收件后办理退款。只退部分商品请先联系店铺，勿整包拒收。", True)
        if reason == "not_received" and expected and today > expected:
            return Eligibility(("refund_only", "reship"), f"包裹已超过预计送达日 {expected}，可申请仅退款或补发。")
        if reason == "not_received" and expected:
            return Eligibility((), f"预计 {expected} 送达，截至 {today} 尚未超期。",
                               "可先查看物流，超过预计日期仍未收到时再核对售后。")
        return Eligibility((), "这件商品已发货，包裹仍在运输中。",
                           "可先查看物流。收到后可按商品情况核对对应售后。")

    delivered = item["delivered_at"]
    signed_on = delivered.astimezone(ZoneInfo("Asia/Shanghai")).date() if delivered else None
    if not signed_on or signed_on > today:
        return Eligibility((), "这件商品缺少可用于核算的签收记录。", "请先在订单详情核对包裹状态。")
    if reason == "cancel":
        return Eligibility((), "这件商品已签收，取消未发货商品的流程已结束。",
                           "如果是买错了，可以改选「买错或不想要」核对退换期限。")

    fault_limit = signed_on + timedelta(days=item["fault_days"])
    if reason == "wrong_purchase":
        last_day = signed_on + timedelta(days=item["return_days"])
        if not item["change_of_mind"]:
            return Eligibility((), "本商品下单时未承诺无理由退换。",
                               "如实际存在质量问题、发错或少件，可按对应原因单独核对。")
        if today > last_day:
            next_step = ("如商品确实有质量问题，可单独核对维修或其他质量售后。"
                         if item["repairable"] else "如实际存在质量问题，可按该原因单独核对适用期限。")
            return Eligibility((), f"商品于 {signed_on} 签收。本单条款支持签收后 {item['return_days']} 天内无理由退换，"
                                   f"已于 {last_day} 截止。", next_step)
        return Eligibility(("return_refund", "exchange"), f"本单无理由退换期限至 {last_day}，可申请退货退款或换货。")

    if reason == "quality":
        repair_limit = signed_on + timedelta(days=item["warranty_days"]) if item["repairable"] else None
        repair = ("repair",) if repair_limit and today <= repair_limit else ()
        if today <= fault_limit:
            return Eligibility(("return_refund", "exchange", *repair),
                               f"本单的质量问题退换期限至 {fault_limit}，可以向店铺提交申请。")
        if repair:
            return Eligibility(repair, f"质量问题退换已于 {fault_limit} 截止。本商品在 {repair_limit} 前仍可申请维修。")
        expiry = f"维修期限已于 {repair_limit} 截止。" if repair_limit else "该商品的下单条款未包含维修。"
        return Eligibility((), f"质量问题退换已于 {fault_limit} 截止。{expiry}")

    if today > fault_limit:
        return Eligibility((), f"本单该原因的售后申请期限已于 {fault_limit} 截止。")
    if reason == "wrong_item":
        return Eligibility(("return_refund", "exchange"), f"在 {fault_limit} 前可就发错商品申请退货退款或换货。")
    if reason == "missing":
        return Eligibility(("reship", "refund_only"), f"在 {fault_limit} 前可就少件漏发申请补发或仅退款。")
    return Eligibility(("refund_only", "reship"), f"包裹有签收记录。如实际仍未收到，可在 {fault_limit} 前申请仅退款或补发。")


def freight_quote(item: dict, reason: str, *, kind: str = "return_refund") -> dict | None:
    """用条款费率及本单商品权益算寄回报价，责任类原因留待核实。"""
    if kind not in RETURN_KINDS or item["stage"] != "delivered" or reason not in ("wrong_purchase", "quality", "wrong_item"):
        return None
    fee = item["return_postage"]
    if reason == "wrong_purchase":
        credit = min(item["return_credit"], fee)
        return {"fee": fee, "credit": credit, "due": fee - credit,
                "message": f"买家承担。寄回报价 ¥{fee:.2f}，本单运费权益抵扣 ¥{credit:.2f}，预计自付 ¥{fee - credit:.2f}。"}
    advance = fee if item["seller_advances"] else Decimal("0.00")
    upfront = (f"按当前寄回报价，先垫付 ¥{advance:.2f}，店铺在办理时登记返还。"
               if advance else "使用店铺提供的寄回方式，买家无需垫付。")
    return {"advance": advance, "message": "若店铺核实该原因属实，寄回运费由店铺承担。" + upfront}


def identify_reason(question: str) -> str | None:
    """按中文短句匹配原因，忽略明确否定的原因并保留多义问题。"""
    found = set()
    for clause in re.split(r"[、，,。；;！!？?]|而是|但是", question):
        for reason, pattern in INTENT_REASONS:
            for match in re.finditer(pattern, clause):
                prefix = clause[max(0, match.start() - 5):match.start()]
                if not re.search(r"(?:不是|并非|没有|没|非|不)\s*(?:因为)?$", prefix):
                    found.add(reason)
    return next(iter(found)) if len(found) == 1 else None


def handling_kind(case: dict) -> str:
    """有买家确认的方案时，执行方案类型，否则沿用原申请。"""
    return (case.get("agreement") or case)["kind"]


def preparing_outbound(case: dict) -> bool:
    """判断原单是否还在换货、补发或修后返还的发出阶段。"""
    return case["status"] in DELIVERY_PHASES and handling_kind(case) in DELIVERY_KINDS


def delivery_review_open(case: dict) -> bool:
    """区分新包裹的待处理申请与已记载的结果。"""
    return (case.get("delivery_review") or {}).get("action") == "request_delivery_review"


def delivery_reply_pending(case: dict) -> bool:
    """判断本次未收件的店铺回复是否仍可由买家确认或申请核对。"""
    parcel = case.get("outbound")
    return bool(case.get("delivery_reply") and parcel and parcel["stage"] == "shipped"
                and parcel["problem"] in (None, "not_received"))


def review_phase(case: dict) -> str | None:
    """判断哪项店铺待办适用已裁定售后单的超时复核。"""
    if case.get("decision") != "support_buyer":
        return None
    if case["status"] == "awaiting_return" and case.get("return_details_pending"):
        return "return_details"
    if case["status"] == "return_sent":
        return "receipt"
    return None


def platform_intervention(case: dict, *, overdue: bool) -> dict[str, str]:
    """让聊天、按钮和提交校验使用同一份平台介入状态。"""
    status = case["status"]
    refund_state = (case.get("refund_attempt") or {}).get("state")
    if refund_state in ("pending", "failed"):
        message = ("退款已发起，等待处理结果。" if refund_state == "pending"
                   else "退款失败，当前由店铺重试。可在原单查看失败原因。")
        return {"state": "waiting", "message": message}
    if delivery_review_open(case):
        return {"state": "reviewing", "message": "平台客服正在核对这次售后包裹。买家和店铺可在原单补充说明或凭证。"}
    if status == "disputed":
        if case.get("active_review_no") is not None:
            return {"state": "reviewing", "message": "平台正在复核原单的执行情况，可继续补充说明和凭证。"}
        return {"state": "reviewing", "message": "平台客服正在处理这张售后单，可在原单补充凭证。"}
    if preparing_outbound(case):
        parcel = case.get("outbound")
        if parcel is None:
            return {"state": "waiting", "message": "请先确认本次售后寄出需要使用的收货信息。"}
        if overdue and (parcel["stage"] == "ready" or parcel["problem"]):
            return {"state": "delivery_review_available", "message": "店铺办理已超时，可在原单就这次包裹申请平台处理。"}
        if delivery_reply_pending(case):
            return {"state": "delivery_review_available", "message": "店铺已回复物流核查。若仍未收到，可直接申请平台核对。"}
        if parcel["problem"]:
            return {"state": "waiting", "message": "店铺正在处理这次包裹问题。超过处理期限后，可申请平台处理。"}
        if parcel["stage"] == "ready":
            return {"state": "waiting", "message": "店铺正在准备发出新包裹。超过发货期限后，可申请平台处理。"}
        return {"state": "waiting", "message": "店铺已发出新包裹。收到后可确认收货，出现问题可在原单反馈。"}
    if case.get("dispute_opened_at"):
        phase = review_phase(case)
        if phase:
            step = "补充寄回信息" if phase == "return_details" else "确认收件"
            if overdue:
                return {"state": "review_available", "message": f"店铺已超过{step}的期限，可从原单提交凭证并申请平台复核。"}
            return {"state": "waiting", "message": f"平台结论正在执行，当前等店铺{step}。若店铺超时，可在原单申请平台复核。"}
        return {"state": "decided", "message": "平台已处理过这张售后单，可查看原单时间线中的平台结论。"}
    if status not in ACTIVE:
        return {"state": "closed", "message": "这张售后单已结束，可打开原单查看处理结果。"}
    if status == "rejected":
        return {"state": "available", "message": "店铺已拒绝申请。你可以在原单填写说明，申请平台介入。"}
    if status == "awaiting_outcome" and case.get("transit_request") and (
            case["transit_request"] == "partial" or case.get("package_stage") == "delivered"):
        return {"state":"waiting", "message":"请核对实际收货情况。收到包裹后，可在原单选择「我已签收，改为自行寄回」。"}
    if status == "submitted" and case.get("declined_offer"):
        return {"state": "available", "message": "你已拒绝店铺方案。现在可以在原单填写说明，申请平台介入。"}
    if case.get("prompt_kind") == "offer":
        return {"state": "waiting", "message": "店铺有一份方案待你确认。如果不同意，可先在原单拒绝，再申请平台介入。"}
    if case.get("prompt_kind") == "evidence":
        return {"state": "waiting", "message": "店铺正在等你补充凭证。请先在原单补充说明或照片。"}
    if case.get("transit_request") and status == "awaiting_return" and case.get("return_details_pending"):
        if overdue:
            return {"state": "available", "message": "店铺超时未提供寄回地址，可从原单申请平台介入。"}
        return {"state": "waiting", "message": "店铺正在补充寄回地址。超时后可从原单申请平台介入。"}
    if status in SELLER_PHASES:
        if overdue:
            return {"state": "available", "message": "店铺已超过当前办理期限。你可以在原单填写说明，申请平台介入。"}
        return {"state": "waiting", "message": "店铺还在办理期限内。店铺拒绝申请、你拒绝店铺方案，或店铺办理超时后，可申请平台介入。"}
    return {"state": "waiting", "message": "当前步骤需要你按原单说明寄回商品。请先完成寄回操作。"}


def available_actions(case: dict, role: str, *, overdue: bool) -> list[str]:
    """给同一个业务阶段按角色提供可执行的动作。"""
    status = case["status"]
    if delivery_review_open(case):
        if role != "staff":
            return ["evidence"]
        if case["outbound"]["stage"] == "ready":
            return ["platform_dispatch"]
        return ["platform_reissue", "platform_continue_delivery"]
    if role == "staff":
        return ["support_buyer", "support_seller"] if status == "disputed" else []
    if case.get("prompt_kind"):
        if role == "merchant":
            return ["close_prompt", "evidence"]
        response = ["provide_evidence"] if case["prompt_kind"] == "evidence" else ["accept_offer", "reject_offer"]
        response.append("withdraw")
        if platform_intervention(case, overdue=overdue)["state"] == "available":
            response.append("dispute")
        return response
    if status not in ACTIVE:
        return ["reimburse"] if role == "merchant" and case.get("reimbursement_due") else []
    refund_state = (case.get("refund_attempt") or {}).get("state")
    if refund_state in ("pending", "failed"):
        if role == "merchant":
            action = "record_refund_result" if refund_state == "pending" else "refund"
            return [action, "evidence"] + (["reimburse"] if case.get("reimbursement_due") else [])
        return ["evidence"]
    if status == "disputed":
        return ["evidence"]
    if case.get("transit_request") and status == "submitted" and role == "merchant":
        return ["approve", "request_evidence", "reject"]
    if case.get("transit_request") and status == "awaiting_outcome":
        if role == "merchant":
            can_intercept = case["transit_request"] == "whole" and case.get("package_stage") == "in_transit"
            if not can_intercept:
                return ["evidence"]
            if case.get("interception_failure"):
                return ["carrier_return", "evidence"]
            return ["carrier_return", "carrier_interception_failed", "evidence"]
        result = ["buyer_received", "evidence"]
        if platform_intervention(case, overdue=overdue)["state"] == "available":
            result.append("dispute")
        return result
    if case.get("carrier_returned") and status == "return_sent" and role == "buyer":
        escalation = platform_intervention(case, overdue=overdue)["state"]
        extra = {"available":["dispute"], "review_available":["review"]}.get(escalation, [])
        return ["buyer_received", "evidence", *extra]
    if status == "awaiting_return" and case.get("return_details_pending"):
        if role == "merchant":
            return ["provide_return_details", "evidence"]
        state = platform_intervention(case, overdue=overdue)["state"]
        escalation = {"available": ["dispute"], "review_available": ["review"]}.get(state, [])
        return [*escalation, "evidence", "withdraw"]
    if preparing_outbound(case):
        return delivery_actions(case, role, overdue=overdue)
    if role == "buyer":
        buyer_actions = {
            "submitted": ["withdraw"], "awaiting_return": ["ship", "withdraw"],
            "rejected": ["revise", "withdraw"],
        }.get(status, []).copy()
        state = platform_intervention(case, overdue=overdue)["state"]
        if state == "available":
            buyer_actions.append("dispute")
        elif state == "review_available":
            buyer_actions.append("review")
        return [*buyer_actions, "evidence"]
    result = RESULTS[handling_kind(case)]
    return {
        "submitted": ["approve", "request_evidence", "negotiate", "reject"], "return_sent": ["receive", "evidence"],
        "received": [result, "evidence"] + (["reimburse"] if case.get("reimbursement_due") else []),
        "awaiting_outcome": [result, "evidence"],
        "rejected": ["evidence"], "awaiting_return": ["evidence"],
    }.get(status, [])


def delivery_actions(case: dict, role: str, *, overdue: bool) -> list[str]:
    """根据最新售后包裹，决定买家确认、店铺发出或处理异常的动作。"""
    parcel = case.get("outbound")
    reimburse = ["reimburse"] if case["status"] == "received" and case.get("reimbursement_due") else []
    if parcel is None:
        return ["confirm_delivery_address", "evidence"] if role == "buyer" else ["evidence", *reimburse]
    if role == "merchant":
        if parcel["stage"] == "ready":
            return [RESULTS[handling_kind(case)], "evidence", *reimburse]
        if parcel["problem"] == "not_received":
            return ["resume_delivery", "reissue_delivery", "evidence", *reimburse]
        if parcel["problem"]:
            return ["reissue_delivery", "evidence", *reimburse]
        return ["evidence", *reimburse]
    if parcel["stage"] == "ready":
        actions = ["confirm_delivery_address", "evidence"]
        if overdue and not parcel["problem"]:
            actions.append("report_delivery_problem")
    elif not parcel["problem"]:
        actions = ["confirm_delivery", "report_delivery_problem", "evidence"]
    elif parcel["problem"] == "not_received":
        actions = ["confirm_delivery", "evidence"]
    else:
        actions = ["evidence"]
    if platform_intervention(case, overdue=overdue)["state"] == "delivery_review_available":
        actions.append("request_delivery_review")
    return actions


def next_status(case: dict, operation: MallOperation) -> str:
    """校验物流结果的必需字段，并确定操作后的状态。"""
    action = operation.action
    if action in ("ship", "replace", "reship", "repair_complete"):
        if not (operation.carrier and operation.tracking):
            raise ServiceError(400, "请填写快递公司和运单号。")
    elif operation.carrier or operation.tracking:
        raise ServiceError(400, "此操作无需填写寄件信息。")
    if action in DISPATCH_ACTIONS:
        return case["status"]
    if case.get("transit_request"):
        if action == "approve" or (action == "support_buyer" and not case.get("active_review_phase")
                                   and case["dispute_previous_status"] in ("submitted","rejected")):
            return "awaiting_outcome"
        if action == "carrier_return":
            return "return_sent"
        if action == "buyer_received":
            return "awaiting_return"
    if action == "record_refund_result" and operation.refund_result == "succeeded":
        return "refunded"
    if action == "confirm_delivery":
        return OUTCOMES[RESULTS[handling_kind(case)]]
    if action in ("approve", "accept_offer"):
        selection = case.get("pending_offer") if action == "accept_offer" else None
        kind = selection["kind"] if selection else handling_kind(case)
        return "awaiting_return" if kind in RETURN_KINDS else "awaiting_outcome"
    if action == "support_buyer":
        if case.get("active_review_phase") == "receipt":
            return "received"
        if case.get("active_review_phase") == "return_details":
            return "awaiting_return"
        previous = case["dispute_previous_status"]
        if previous in ("submitted", "rejected"):
            return "awaiting_return" if handling_kind(case) in RETURN_KINDS else "awaiting_outcome"
        return previous
    return {"reject": "rejected", "ship": "return_sent", "receive": "received", "revise": "submitted",
            "withdraw": "withdrawn", "dispute": "disputed", "review": "disputed", "support_seller": "closed",
            **OUTCOMES}.get(action, case["status"])

