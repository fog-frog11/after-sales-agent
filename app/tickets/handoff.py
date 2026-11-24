"""从已核对的订单对话整理一份可随工单保存的交接快照。"""

from datetime import date

from typing_extensions import TypedDict

from app.integrations.deepseek import ORDER_ID_PATTERN
from app.orders.logistics import HISTORICAL_ORDER_PATTERN


class TicketHandoffUnavailable(Exception):
    """所选对话不能用于当前工单。"""


class HandoffTurn(TypedDict):
    """客服可以核对的一轮问题与答复。"""

    question: str
    answer: str


class TicketHandoff(TypedDict):
    """买家确认后随工单保存的结构化内容。"""

    buyer_request: str
    verified_facts: list[str]
    review_task: str
    turns: list[HandoffTurn]


def related_turns(order_id: str, turns: list[dict]) -> list[dict]:
    """只保留最近同一订单的片段和能对应到该订单的补单号前文。"""
    selected = []
    for turn in reversed(turns):
        result = turn["result"]
        if result.get("order_id") == order_id:
            if not turn.get("order_card"):
                selected.append(turn)
        elif selected and result["status"] == "missing_order_id":
            question = turn["question"]
            identifiers = ORDER_ID_PATTERN.findall(question) + HISTORICAL_ORDER_PATTERN.findall(question)
            if {value.upper() for value in identifiers} <= {order_id}:
                selected.append(turn)
                continue
            break
        else:
            break
    return list(reversed(selected[:4]))


def handoff_answer(result: dict) -> str:
    """按实际查询结果保留当轮可见答复。"""
    operations = result.get("tool_plan", {}).get("operations", [])
    parts = [result["message"]] if not operations or "logistics" in operations else []
    if items := result.get("olist_items"):
        parts.append(items["answer"])
    if freight := result.get("return_freight"):
        parts.append(("费用演练 · " if freight["practice"] else "") + freight["message"])
    if policy := result.get("policy_assistance"):
        parts.append(policy["answer"])
    if review := result.get("human_review"):
        parts.append(review["message"])
    return "\n".join(parts) or result["message"]


def build_ticket_handoff(order_id: str, reason: str, logistics: dict | None, conversation: dict | None,
                         *, signed_on: date | None = None) -> TicketHandoff:
    """用订单记录和授权对话生成交接，区分已签收和缺少签收记录。"""
    turns = []
    if conversation is not None:
        if conversation["result"].get("order_id") != order_id:
            raise TicketHandoffUnavailable("所选对话与当前订单不匹配，请重新查单后预览。")
        turns = related_turns(order_id, conversation["turns"])
    facts = []
    if logistics:
        if estimated := logistics.get("estimated_on"):
            facts.append(f"预计送达日 {estimated}。")
        if delivered := logistics.get("delivered_on"):
            facts.append(f"实际签收日 {delivered}。")
        else:
            facts.append(f"截至 {logistics['snapshot_on']}，物流记录中没有签收日期。")
    else:
        facts.append(f"签收日期 {signed_on}。" if signed_on else "订单暂无签收记录。")
    for turn in reversed(turns):
        items = turn["result"].get("olist_items")
        if items and items["status"] == "found":
            facts.append(f"已核对订单商品，共 {items['quantity']} 件。")
            break
    return {"buyer_request": reason, "verified_facts": facts,
            "review_task": "核对订单和买家诉求，并记录处理结果。",
            "turns": [{"question": turn["question"], "answer": handoff_answer(turn["result"])} for turn in turns]}
