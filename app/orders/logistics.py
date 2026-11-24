"""按来源和历史快照解释订单的签收、延误及其他状态。"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Literal


OLIST_AS_OF = date(2018, 10, 17)
ReceiptStatusFilter = Literal["all", "signed", "not_signed", "cancelled", "unknown"]
JD_CHANNEL_LABELS = {"self": "京东自营", "market": "京东第三方", "mixed": "自营与第三方混合"}
OLIST_ORDER_PATTERN = re.compile(r"(?<![A-Za-z0-9])OL-[0-9A-F]{10}(?![A-Za-z0-9])", re.I)
JD_ORDER_PATTERN = re.compile(r"(?<![A-Za-z0-9])JD-[0-9A-F]{12}(?![A-Za-z0-9])", re.I)
HISTORICAL_ORDER_PATTERN = re.compile(r"(?<![A-Za-z0-9])(?:OL-[0-9A-F]{10}|JD-[0-9A-F]{12})(?![A-Za-z0-9])", re.I)
LOGISTICS_MESSAGES = {
    "logistics_delivered": "订单已有签收记录。",
    "logistics_delivered_late": "订单已签收，实际签收日晚于预计送达日。",
    "logistics_overdue": "订单已发货，截至历史查询日仍无签收记录，且已超过预计送达日。",
    "logistics_shipping": "订单已发货，历史快照中还没有签收记录。",
    "logistics_cancelled": "源数据记录这笔订单已取消。",
    "logistics_unknown": "这笔订单缺少可用于确认送达状态的记录。",
}


@dataclass(frozen=True)
class LogisticsOrder:
    """保存历史物流查询所需的源订单字段。"""

    source_order_id: str
    source_status: str
    purchased_on: date
    estimated_on: date | None
    delivered_on: date | None
    seller_ids: tuple[str, ...] = ()
    origin: Literal["olist", "jd"] = "olist"
    snapshot_on: date = OLIST_AS_OF
    channel: Literal["self", "market", "mixed"] | None = None

    @property
    def order_id(self) -> str:
        """根据源订单编号生成稳定的工作台查询编号。"""
        return "JD-" + self.source_order_id[:12].upper() if self.origin == "jd" else "OL-" + self.source_order_id[:10].upper()

    @property
    def seller_id(self) -> str | None:
        """仅在商品全部关联同一卖家时确定售后对象。"""
        return self.seller_ids[0] if len(self.seller_ids) == 1 else None


LogisticsLookup = Callable[[str], LogisticsOrder | None]


def classify_logistics(order: LogisticsOrder) -> str:
    """从源状态与日期得到可解释的物流判定。"""
    if order.source_status == "canceled":
        return "logistics_cancelled"
    if order.source_status == "delivered" and order.delivered_on is not None:
        if order.estimated_on is not None and order.delivered_on > order.estimated_on:
            return "logistics_delivered_late"
        return "logistics_delivered"
    if order.source_status == "shipped" and order.delivered_on is None:
        if order.estimated_on is not None and order.estimated_on < order.snapshot_on:
            return "logistics_overdue"
        return "logistics_shipping"
    return "logistics_unknown"


def logistics_result(order: LogisticsOrder) -> dict:
    """返回买家工作台所需的状态、所用日期与数据来源。"""
    status = classify_logistics(order)
    message = LOGISTICS_MESSAGES[status]
    if order.origin == "jd" and status == "logistics_unknown":
        message = "这笔京东历史订单缺少完整的配送签收记录。"
    return {
        "status": status,
        "message": message,
        "order_id": order.order_id,
        "logistics": {
            "source": "京东 M&SOM" if order.origin == "jd" else "Olist",
            "snapshot_on": order.snapshot_on,
            "purchased_on": order.purchased_on,
            "estimated_on": order.estimated_on,
            "delivered_on": order.delivered_on,
        },
    }
