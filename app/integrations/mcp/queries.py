"""复用现有仓储，为 MCP 提供结构化的只读售后查询。"""

from datetime import date
from time import perf_counter

from pydantic import BaseModel, Field

from app.orders.logistics import HISTORICAL_ORDER_PATTERN, LogisticsLookup, LogisticsOrder
from app.orders.rules import Order, OrderLookup, PolicyLookup as ShopPolicyLookup
from app.integrations.deepseek import ORDER_ID_PATTERN
from app.policies.evidence import PolicyAspect, PolicyEvidencePlan, PolicyPlanLookup
from app.policies.jd_reference import jd_policy_scope
from app.policies.olist_reference import OLIST_REFERENCE_NAME, OLIST_REFERENCE_SCOPE
from app.policies.search import PolicyLookup as ArticleLookup, PolicySource


class OrderSummary(BaseModel):
    """提供订单号、签收日期、店铺和适用退货期限。"""

    order_id: str
    signed_on: date | None
    shop_name: str | None = None
    policy_name: str | None = None
    return_window_days: int | None = None


class PolicyMatches(BaseModel):
    """提供订单所属店铺和检索找到的原文及出处。"""

    order_id: str
    shop_id: str
    sources: list[PolicySource]
    aspects: list[PolicyAspect] = Field(default_factory=list)
    retrieval_ms: int | None = None


class OrderTools:
    """组合订单读取、政策匹配和原文检索。"""

    def __init__(
        self, order_lookup: OrderLookup, policy_lookup: ShopPolicyLookup, article_lookup: ArticleLookup,
        *, plan_lookup: PolicyPlanLookup | None = None, logistics_lookup: LogisticsLookup | None = None,
        olist_plan_lookup: PolicyPlanLookup | None = None,
        jd_plan_lookup: PolicyPlanLookup | None = None,
    ):
        """注入现有仓储的只读查询函数。"""
        self.order_lookup = order_lookup
        self.policy_lookup = policy_lookup
        self.article_lookup = article_lookup
        self.plan_lookup = plan_lookup
        self.logistics_lookup = logistics_lookup
        self.olist_plan_lookup = olist_plan_lookup
        self.jd_plan_lookup = jd_plan_lookup

    def _require_order(self, order_id: str) -> Order | LogisticsOrder:
        """规范化订单号并取得订单，缺少记录时给出可读错误。"""
        normalized = order_id.strip().upper()
        if HISTORICAL_ORDER_PATTERN.fullmatch(normalized):
            order = self.logistics_lookup(normalized) if self.logistics_lookup is not None else None
        elif ORDER_ID_PATTERN.fullmatch(normalized):
            order = self.order_lookup(normalized)
        else:
            raise ValueError("订单号格式不正确，请从我的订单选择。")
        if order is None or order.order_id != normalized:
            raise ValueError("查不到这笔订单，请核对订单号。")
        return order

    def get_order(self, order_id: str) -> OrderSummary:
        """查询签收记录，并补充所属店铺的政策和退货期限。"""
        order = self._require_order(order_id)
        if isinstance(order, LogisticsOrder):
            policy_name = "中国电商公开规则" if order.origin == "jd" else OLIST_REFERENCE_NAME
            return OrderSummary(order_id=order.order_id, signed_on=order.delivered_on, policy_name=policy_name)
        policy = self.policy_lookup(order.shop_id) if order.shop_id else None
        return OrderSummary(
            order_id=order.order_id,
            signed_on=order.signed_on,
            shop_name=policy.shop_name if policy else None,
            policy_name=policy.policy_name if policy else None,
            return_window_days=policy.return_window_days if policy else None,
        )

    def search_shop_policy(self, order_id: str, question: str) -> PolicyMatches:
        """在订单所属店铺范围内检索政策条款。"""
        query = question.strip()
        if not 1 <= len(query) <= 300:
            raise ValueError("请输入 1 至 300 个字符的政策问题。")
        order = self._require_order(order_id)
        if isinstance(order, LogisticsOrder):
            if order.origin == "jd":
                lookup, scope = self.jd_plan_lookup, jd_policy_scope(order)
            else:
                lookup, scope = self.olist_plan_lookup, OLIST_REFERENCE_SCOPE
            if lookup is None:
                raise ValueError("当前未启用公开售后规则。")
            started = perf_counter()
            plan = lookup(scope, query)
            return PolicyMatches(order_id=order.order_id, shop_id=scope,
                                 sources=plan.sources, aspects=plan.aspects,
                                 retrieval_ms=round((perf_counter() - started) * 1000))
        if not order.shop_id:
            raise ValueError("这笔订单缺少店铺信息，无法检索政策。")
        started = perf_counter()
        plan = (self.plan_lookup(order.shop_id, query) if self.plan_lookup is not None
                else PolicyEvidencePlan(self.article_lookup(order.shop_id, query)))
        return PolicyMatches(
            order_id=order.order_id, shop_id=order.shop_id,
            sources=plan.sources, aspects=plan.aspects, retrieval_ms=round((perf_counter() - started) * 1000),
        )
