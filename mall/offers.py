"""校验可执行的店铺方案，核算这笔售后能使用的退款余额。"""

from decimal import Decimal
from zoneinfo import ZoneInfo

from app.mall.rules import KINDS, REFUND_KINDS, RETURN_KINDS, MallOffer, assess_eligibility
from app.tickets.service_requests import ServiceError


def refundable(case: dict, quantity: int) -> Decimal:
    """按申请件数和已经使用过的商品退款余额取较小值。"""
    return min(case["paid_unit"] * quantity, case["refund_balance"])


def offer_choices(case: dict) -> tuple[str, ...]:
    """由原申请和包裹事实确定可供买家确认的替代办理方式。"""
    eligible = assess_eligibility({**case, "stage": case["package_stage"]}, case["reason"],
                                  case["created_at"].astimezone(ZoneInfo("Asia/Shanghai")).date())
    choices = tuple(dict.fromkeys((case["kind"], *eligible.kinds)))
    if (case["package_stage"] == "delivered" and case["reason"] in ("quality", "wrong_item", "missing")
            and refundable(case, case["quantity"]) > Decimal("0.01")):
        return (*choices, "partial_refund")
    return choices


def prepare_offer(case: dict, offer: MallOffer | None) -> dict:
    """锁定方案金额、件数和寄回说明，退换等动作共享该快照。"""
    if offer is None:
        raise ServiceError(400, "请填写处理方式、件数和相应的方案信息。")
    if offer.kind not in offer_choices(case):
        raise ServiceError(409, "当前申请原因和包裹状态不支持该方案。")
    if offer.quantity > min(case["quantity"], case["remaining_quantity"]):
        raise ServiceError(400, "方案件数不能超过本次申请及剩余商品件数。")
    if (offer.kind in RETURN_KINDS) != bool(offer.return_instructions):
        raise ServiceError(400, "需要寄回的方案请填写寄回说明，其他方案无需寄回说明。")
    maximum = refundable(case, offer.quantity)
    if offer.kind == "partial_refund":
        if offer.amount is None or not 0 < offer.amount < maximum:
            raise ServiceError(400, f"留货部分退款金额须大于 0 元，且低于当前可退上限 ¥{maximum:.2f}。")
        amount = offer.amount
    else:
        if offer.amount is not None:
            raise ServiceError(400, "仅留货部分退款需手填金额。退货退款和仅退款按剩余可退款金额核算。")
        amount = maximum if offer.kind in REFUND_KINDS else None
    return {"kind": offer.kind, "quantity": offer.quantity, "amount": f"{amount:.2f}" if amount is not None else None,
            "return_instructions": offer.return_instructions}
