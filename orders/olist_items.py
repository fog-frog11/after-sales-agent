"""按历史订单的已知商品、数量和金额生成答复。"""

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass
from decimal import Decimal

from app.orders.olist_categories import CATEGORY_LABELS


CENT = Decimal("0.01")
ITEM_IDENTITY_QUESTION = re.compile(
    r"品类|类别|买(?:了|的(?:是)?|过)?(?:些|个)?(?:什么|啥|哪些|哪[几种件])"
    r"|(?:什么|哪些|啥)商品|商品(?:明细|清单|名(?:称|字)?|叫什么)"
)
ORDER_DETAILS_QUESTION = re.compile(r"(?:订单|这单|这笔)(?:的)?(?:详情|资料)")


@dataclass(frozen=True)
class OrderItem:
    """保存一件商品对应的源明细序号、品类和金额。"""

    item_no: int
    product_id: str
    category: str | None
    price: Decimal
    freight: Decimal | None
    quantity: int = 1
    label: str | None = None

    @property
    def category_label(self) -> str:
        """保留原标签作为未知品类的回退。"""
        return self.label or (CATEGORY_LABELS.get(self.category, self.category) if self.category else "品类未记录")


# None 表示订单不存在，空列表表示订单存在且源数据缺少明细。
ItemsLookup = Callable[[str], list[OrderItem] | None]


def describe_items(items: list[OrderItem]) -> str:
    """按商品行计数，生成带品类名的简短中文描述。"""
    counts = Counter()
    for item in items:
        counts[item.category_label] += item.quantity
    return "、".join(f"{count} 件{label}" if label != "品类未记录" else f"{count} 件（品类未记录）"
                    for label, count in counts.items())


def answer_items(order_id: str, question: str, items: list[OrderItem]) -> dict:
    """根据有据可查的商品行回答买家，并返回明细供核对。"""
    yuan = order_id.startswith("JD-")
    currency = "CNY" if yuan else "BRL"
    symbol = "¥" if yuan else "R$ "
    if not items:
        return {"status": "no_items", "order_id": order_id, "currency": currency,
                "answer": "这笔订单暂无商品明细。", "items": [],
                "quantity": None, "items_total": None, "freight_total": None, "recorded_total": None}

    quantity = sum(item.quantity for item in items)
    items_total = sum((item.price * item.quantity for item in items), Decimal("0.00"))
    freight_total = (sum((item.freight for item in items), Decimal("0.00"))
                     if all(item.freight is not None for item in items) else None)
    recorded_total = items_total + freight_total if freight_total is not None else None
    total_message = f"商品金额 {symbol}{items_total:.2f}"
    if recorded_total is not None:
        total_message += f"，含运费的记录合计 {symbol}{recorded_total:.2f}"
    shipping_message = (f"记录的运费共 {symbol}{freight_total:.2f}。" if freight_total is not None
                        else "源数据未记录运费。")
    parts = []
    if ITEM_IDENTITY_QUESTION.search(question):
        parts.append(f"这单有 {describe_items(items)}。")
    if re.search(r"几[件个]|多少[件个]|件数|数量", question):
        parts.append(f"订单里有 {quantity} 件商品。")
    unit_price = re.search(r"单价|每[件个](?:多少钱|价格)|一[件个]多少钱|价格", question)
    if unit_price:
        prices = dict.fromkeys(f"{symbol}{item.price:.2f}" for item in items)
        parts.append("商品单价为 " + "、".join(prices) + "。")
    shipping = re.search(r"运费|邮费|配送费", question)
    if shipping:
        parts.append(shipping_message)
    total = re.search(r"总价|合计|一共|总共|金额|花了多少", question)
    if total or ("多少钱" in question and not (unit_price or shipping)):
        parts.append(total_message + ("。" if recorded_total is not None else "。" + shipping_message))
    answer = "".join(parts) if parts else f"这单有 {describe_items(items)}，商品金额 {symbol}{items_total:.2f}。{shipping_message}"
    rows = [{**asdict(item), "category_label": item.category_label} for item in items]
    return {"status": "found", "order_id": order_id, "currency": currency, "answer": answer, "items": rows,
            "quantity": quantity, "items_total": items_total, "freight_total": freight_total,
            "recorded_total": recorded_total}
