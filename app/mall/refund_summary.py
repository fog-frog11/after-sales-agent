"""从商品退款账、结算记录和寄回事件生成页面与咨询共用的退款明细。"""

from decimal import Decimal

from app.mall.offers import refundable
from app.mall.rules import ACTIVE, REFUND_KINDS, RETURN_KINDS, handling_kind


def product_refund_amount(case: dict) -> Decimal | None:
    """优先读取已入账金额、渠道尝试和已确认方案，最后才计算申请金额。"""
    if case["recorded_refund"] is not None:
        return case["recorded_refund"]
    if case["refund_attempt"]:
        return Decimal(case["refund_attempt"]["amount"])
    if handling_kind(case) not in REFUND_KINDS:
        return None
    if case["package_lines"]:
        return sum((Decimal(line["amount"]) for line in case["package_lines"]), Decimal("0"))
    if case["agreement"]:
        return Decimal(case["agreement"]["amount"])
    return refundable(case, case["quantity"])


def _product_summary(case: dict, amount: Decimal | None) -> dict:
    """保留原商品明细，区分申请、渠道确认和退款入账。"""
    attempt = case["refund_attempt"] or {}
    pending_offer = case["pending_offer"] if not case["agreement"] else None
    awaiting_confirmation = (pending_offer and pending_offer["amount"] is not None
                             and case["status"] in ACTIVE and not attempt)
    details = {"started_at": attempt.get("started_at"), "result_at": attempt.get("result_at") or case["refund_recorded_at"]}
    if case["recorded_refund"] is not None:
        state, label = "succeeded", "退款成功" if attempt else "已登记退款"
        message = f"{label}，已登记商品退款 ¥{amount:.2f}。"
    elif attempt.get("state") == "pending":
        state, label = "pending", "退款处理中"
        message = f"已发起商品退款 ¥{amount:.2f}，退款正在处理。"
    elif attempt.get("state") == "failed":
        state, label = "failed", "退款失败 · 待店铺重试"
        message = f"商品退款 ¥{amount:.2f} 本次处理失败，等待店铺按原金额重试。"
        details["failure_reason"] = attempt.get("result_note")
    elif awaiting_confirmation:
        amount = Decimal(pending_offer["amount"])
        state, label = "awaiting_confirmation", "待确认退款方案"
        message = f"店铺提出商品退款 ¥{amount:.2f}，金额仍在等待买家确认。"
    elif amount is not None and case["status"] in ACTIVE:
        state, label = "not_started", "商品退款待发起"
        message = f"本次商品退款金额为 ¥{amount:.2f}，商家尚未发起退款。"
    else:
        return {"state": "unrecorded", "label": "未登记商品退款", "message": "这张单暂无商品退款登记。",
                "amount": None, "lines": [], **details}
    if case["package_lines"]:
        lines = case["package_lines"]
    else:
        selection = pending_offer if awaiting_confirmation else case["agreement"]
        lines = [{"item_no": case["item_no"], "title": case["product"], "variant": case["variant"],
                  "quantity": selection["quantity"] if selection else case["quantity"],
                  "paid_unit": case["paid_unit"], "amount": amount}]
    return {"state": state, "label": label, "message": message, "amount": amount, "lines": lines, **details}


def _original_shipping(paid: Decimal | None) -> dict:
    """已登记的商家退款按商品入账，展示订单实际收取的配送费。"""
    if paid is None:
        return {"paid": None, "message": "原配送费实付金额待核实，当前售后单暂无配送费退款记录。"}
    if paid == 0:
        return {"paid": paid, "message": "原订单免配送费。"}
    return {"paid": paid, "message": f"原配送费实付 ¥{paid:.2f}，本次商品退款未包含这笔配送费。"}


def _return_shipping(case: dict) -> dict:
    """使用寄出时保存的条款报价，承运方费用等待人工核实。"""
    ship = case["ship_freight"] or {}
    if "return_postage_due" in ship:
        credit = Decimal(ship["credit_used"])
        due = Decimal(ship["return_postage_due"])
        quote = credit + due
        return {"state": "quoted", "message": f"寄回运费由买家承担。条款报价 ¥{quote:.2f}，权益抵扣 ¥{credit:.2f}，"
                f"预计自付 ¥{due:.2f}。"}
    if "advance_amount" in ship:
        advance = Decimal(ship["advance_amount"])
        quote = Decimal(ship["return_postage_fee"])
        if advance > 0:
            status = "已登记返还" if case["reimbursement_due"] is None else "待店铺登记返还"
            return {"state": "quoted", "message": f"寄回运费由店铺承担。条款报价 ¥{quote:.2f}，"
                    f"买家预计垫付 ¥{advance:.2f}，该笔垫付{status}。"}
        return {"state": "quoted", "message": f"寄回运费由店铺承担，条款报价 ¥{quote:.2f}，买家无需垫付。"}
    if case["carrier_returned"] or (case["transit_request"] == "whole" and case["package_stage"] == "in_transit"
                                   and case["status"] in ACTIVE):
        return {"state": "unverified", "message": "拦截或拒收费用待核实，由店铺向承运方确认。"}
    if ship or (handling_kind(case) in RETURN_KINDS and case["status"] in ACTIVE):
        return {"state": "unverified", "message": "寄回运费待核实，金额以本单实际寄件记录和条款为准。"}
    return {"state": "unrecorded", "message": "本单暂无寄回运费记录。"}


def refund_summary(case: dict, amount: Decimal | None) -> dict:
    """返回商品退款、原配送费和寄回运费三项独立账目。"""
    return {"product": _product_summary(case, amount), "original_shipping": _original_shipping(case["original_shipping"]),
            "return_shipping": _return_shipping(case)}
