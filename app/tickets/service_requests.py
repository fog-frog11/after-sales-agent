"""定义商品售后申请、阶段变更和退货费用答复。"""

import re
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

from app.orders.logistics import LogisticsOrder
from app.policies.jd_reference import JD_SELF_URL


ServiceKind = Literal["return", "exchange", "repair"]
ServiceReason = Literal["wrong_purchase", "quality", "wrong_item", "other"]
ServiceStatus = Literal["submitted", "awaiting_return", "return_sent", "received", "completed", "rejected"]
StaffAction = Literal["approve", "reject", "receive", "complete"]
STAFF_TRANSITIONS = {
    "approve": ("submitted", "awaiting_return"), "reject": ("submitted", "rejected"),
    "receive": ("return_sent", "received"), "complete": ("received", "completed"),
}
RETURN_FREIGHT = re.compile(r"(?:退[货换件]|换货|寄回|退回).{0,30}(?:运费|邮费|快递费|寄.{0,6}钱|谁[出付]|垫付)|"
                            r"(?:运费|邮费|快递费).{0,16}(?:退货|退件|寄回)|(?:寄回|退回).{0,8}(?:钱|费用)")
SERVICE_PROGRESS = re.compile(r"(?:售后|退货|换货|维修)(?:申请|单)?(?:的)?(?:进度|状态|办到哪|处理到哪|办好了|处理好了)")
REASON_WORDS = (("wrong_item", r"发错|寄错|发的不对|少发|型号不对"),
                ("quality", r"质量|坏了|不通电|破损|瑕疵|故障"),
                ("wrong_purchase", r"买错|拍错|不想要|改变主意|无理由|不喜欢"))


class ServiceError(Exception):
    """携带可在买家及客服页面显示的业务错误。"""

    def __init__(self, status_code: int, message: str):
        """保留 HTTP 状态码和中文错误信息。"""
        super().__init__(message)
        self.status_code = status_code


class ServiceApplication(BaseModel):
    """接收买家已选的商品、件数、服务类型与原因。"""

    request_id: UUID
    item_no: Annotated[int, Field(strict=True, gt=0)]
    quantity: Annotated[int, Field(strict=True, gt=0)]
    kind: ServiceKind
    reason: ServiceReason
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class ReturnShipment(BaseModel):
    """记录买家提供的承运商和运单号。"""

    carrier: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=30)]
    tracking: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=40, pattern=r"^[A-Za-z0-9-]+$")]


class StaffServiceUpdate(BaseModel):
    """客服选择下一阶段并填写对买家可见的说明。"""

    action: StaffAction
    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


def detect_return_reason(question: str) -> ServiceReason | None:
    """只采集明确的退货原因，多个原因仍由用户选择。"""
    matched = [reason for reason, pattern in REASON_WORDS if re.search(pattern, question)]
    return matched[0] if len(matched) == 1 else None


def assess_return_freight(order: LogisticsOrder, reason: ServiceReason | None, practice: dict | None = None) -> dict:
    """按可证实的数据说明承担与垫付，并把演练费用显式标注。"""
    if practice is not None:
        fee = practice["quoted_fee"]
        voucher = min(fee, practice["voucher"])
        seller_pays = practice["payer"] == "seller"
        due = Decimal("0.00") if seller_pays else fee - voucher
        if seller_pays:
            upfront = f"需要先垫付 ¥{fee:.2f}，办结时按演练约定返还。" if practice["prepaid"] else "无需先垫付。"
            answer = "商家承担，预计最终自付 ¥0.00。" + upfront
        else:
            answer = f"买家承担，预计自付 ¥{due:.2f}。寄回费 ¥{fee:.2f}，券抵扣 ¥{voucher:.2f}。"
        return {"status": "quoted", "message": answer, "practice": True, "reason": reason}
    if reason is None:
        return {"status": "needs_reason", "message": "这次是买错了、商品有质量问题，还是商家发错了？确认原因后继续判断。",
                "practice": False, "reason": None}
    if order.channel == "self" and order.delivered_on and reason == "quality":
        return {"status": "needs_review", "message": "如果审核确认属于商品质量问题并适用自营售后规则，预计由京东承担。寄回方式和是否垫付待审核确认。",
                "practice": False, "reason": reason, "source_url": JD_SELF_URL}
    message = ("运费待核对。历史记录缺少该单的售后约定、会员权益和寄回报价。"
               if order.channel == "self" else "运费待核对。需要确认店铺售后约定、这笔订单的权益和寄回报价。")
    return {"status": "needs_review", "message": message, "practice": False, "reason": reason}
