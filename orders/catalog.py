"""为买家和客服提供统一的订单列表和详情数据。"""

from collections.abc import Callable
from dataclasses import asdict
from typing import Literal

from app.access import OLIST_OWNER_SUFFIXES, ORDER_OWNERS, order_owner
from app.orders.conversation import order_conversation_scope
from app.orders.logistics import (
    HISTORICAL_ORDER_PATTERN, JD_CHANNEL_LABELS, LogisticsLookup, LogisticsOrder, ReceiptStatusFilter,
    classify_logistics, logistics_result,
)
from app.orders.olist_items import ItemsLookup, answer_items
from app.orders.rules import Order, OrderLookup, PolicyLookup


OrderKind = Literal["all", "shop", "olist", "jd"]
ShopOrderList = Callable[[list[str]], list[Order]]
OlistOrderList = Callable[[str, str, int, str, ReceiptStatusFilter], list[LogisticsOrder]]


class OrderCatalog:
    """根据服务端确认的买家范围查询已存在的订单。"""

    def __init__(self, shop_lookup: OrderLookup, policy_lookup: PolicyLookup, *,
                 shop_list: ShopOrderList | None = None, logistics_lookup: LogisticsLookup | None = None,
                 olist_list: OlistOrderList | None = None, items_lookup: ItemsLookup | None = None,
                 jd_list: OlistOrderList | None = None):
        """共享现有订单、物流和商品仓储。"""
        self.shop_lookup = shop_lookup
        self.policy_lookup = policy_lookup
        self.shop_list = shop_list
        self.logistics_lookup = logistics_lookup
        self.olist_list = olist_list
        self.jd_list = jd_list
        self.items_lookup = items_lookup

    def find(self, order_id: str, buyer_id: str | None) -> Order | LogisticsOrder | None:
        """在查仓储前检查归属，未知或越权编号得到相同结果。"""
        owner = order_owner(order_id)
        if owner is None or (buyer_id is not None and owner != buyer_id):
            return None
        if HISTORICAL_ORDER_PATTERN.fullmatch(order_id):
            order = self.logistics_lookup(order_id) if self.logistics_lookup is not None else None
        else:
            order = self.shop_lookup(order_id)
        return order if order is not None and order.order_id == order_id else None

    @staticmethod
    def summary(order: Order | LogisticsOrder) -> dict:
        """生成列表必要字段，历史样本保留源物流状态和日期。"""
        historical = isinstance(order, LogisticsOrder)
        seller = None
        if historical:
            if order.origin == "jd":
                seller = JD_CHANNEL_LABELS.get(order.channel)
            elif order.seller_id:
                seller = f"卖家 {order.seller_id[-8:].upper()}"
            elif order.seller_ids:
                seller = "多卖家订单"
        return {
            "order_id": order.order_id, "buyer_id": order_owner(order.order_id),
            "kind": order.origin if historical else "shop",
            "status": classify_logistics(order) if historical else ("signed" if order.signed_on else "not_signed"),
            "purchased_on": order.purchased_on if historical else None,
            "signed_on": order.delivered_on if historical else order.signed_on,
            "seller_label": seller,
        }

    def page(self, buyer_id: str | None, *, kind: OrderKind, order_id: str | None,
             after: str, limit: int, prefix: str = "", receipt: ReceiptStatusFilter = "all") -> dict:
        """合并两种订单来源，并在已过滤的可见结果上生成下一页游标。"""
        if order_id:
            selected = self.find(order_id, buyer_id)
            if selected is None:
                return {"orders": [], "next_after": None}
            entry = self.summary(selected)
            return {"orders": [entry] if kind in ("all", entry["kind"]) else [], "next_after": None}

        entries = []
        if kind in ("all", "shop") and receipt != "cancelled":
            ids = [key for key, owner in ORDER_OWNERS.items()
                   if key > after and key.startswith(prefix) and (buyer_id is None or buyer_id == owner)]
            if ids:
                found = self.shop_list(ids) if self.shop_list is not None else [
                    order for key in ids if (order := self.shop_lookup(key)) is not None
                ]
                for order in found:
                    if order.order_id in ids:
                        entry = self.summary(order)
                        if receipt in ("all", entry["status"]):
                            entries.append(entry)
        for origin, lookup in (("olist", self.olist_list), ("jd", self.jd_list)):
            if lookup is None or kind not in ("all", origin):
                continue
            suffixes = OLIST_OWNER_SUFFIXES[buyer_id] if buyer_id else "".join(OLIST_OWNER_SUFFIXES.values())
            found = lookup(suffixes, after, limit + 1, prefix, receipt)
            entries.extend(self.summary(order) for order in found
                           if buyer_id is None or order_owner(order.order_id) == buyer_id)
        entries.sort(key=lambda entry: entry["order_id"])
        return {"orders": entries[:limit], "next_after": entries[limit - 1]["order_id"] if len(entries) > limit else None}

    def detail(self, order_id: str, buyer_id: str | None) -> dict | None:
        """提供真实日期、金额明细或店铺规则，缺失数据保持为空。"""
        order = self.find(order_id, buyer_id)
        if order is None:
            return None
        detail = self.summary(order)
        detail["conversation_scope"] = order_conversation_scope(order)
        if isinstance(order, LogisticsOrder):
            detail["logistics"] = logistics_result(order)["logistics"]
            items = self.items_lookup(order.order_id) if self.items_lookup is not None else None
            detail["items"] = answer_items(order.order_id, "", items) if items is not None else None
        elif order.shop_id:
            policy = self.policy_lookup(order.shop_id)
            detail["policy"] = asdict(policy) if policy is not None else None
        return detail
