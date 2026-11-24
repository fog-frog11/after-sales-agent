"""验证国内订单的渠道过滤、真实数量与缺失物流信息。"""

import unittest
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import Mock

from app.integrations.mcp.queries import OrderTools
from app.orders.conversation import order_conversation_scope
from app.orders.logistics import LogisticsOrder, logistics_result
from app.orders.olist_items import OrderItem, answer_items
from app.orders.shop_questions import select_historical_questions
from app.policies.evidence import PolicyEvidencePlan
from app.policies.jd_reference import JD_MARKET_URL, JD_SELF_URL, SAMR_URL, jd_policy_scope
from app.storage.milvus_policies import MilvusPolicies
from scripts.import_jd_dataset import promised_date, unit_price


ORDER = LogisticsOrder("e" * 64, "delivered", date(2018, 3, 1), date(2018, 3, 2), date(2018, 3, 2),
                       origin="jd", snapshot_on=date(2018, 4, 7), channel="self")


class JDOrderTests(unittest.TestCase):
    """核对缺失字段不会被当成实际物流、价格或商家承诺。"""

    def test_jd_amount_uses_quantity_and_does_not_infer_freight(self):
        """国内订单以人民币按件计价，缺少的运费保留为空。"""
        item = OrderItem(1, "f" * 10, None, Decimal("12.30"), None, 3, "匿名商品")
        result = answer_items(ORDER.order_id, "这单买了几件？合计多少？运费多少？", [item])
        self.assertEqual((result["currency"], result["quantity"], result["items_total"]), ("CNY", 3, Decimal("36.90")))
        self.assertIsNone(result["freight_total"])
        self.assertIsNone(result["recorded_total"])
        self.assertIn("源数据未记录运费", result["answer"])

    def test_unknown_delivery_and_merchant_keep_unknown_order_scope(self):
        """无有效配送证据时不判未签收，匿名商家按订单建会话。"""
        unknown = replace(ORDER, source_status="unknown", delivered_on=None)
        self.assertEqual(logistics_result(unknown)["status"], "logistics_unknown")
        self.assertEqual(order_conversation_scope(ORDER), f"order:{ORDER.order_id}")
        self.assertEqual(jd_policy_scope(unknown), "jd-general")

    def test_jd_mcp_selects_source_from_confirmed_channel(self):
        """请求里没有可信渠道参数，索引只使用数据库确认的来源集合。"""
        index = MilvusPolicies("http://localhost")
        index.search_evidence = Mock(return_value=PolicyEvidencePlan([]))
        for channel, allowed in (("self", (JD_SELF_URL, SAMR_URL)), ("market", (JD_MARKET_URL, SAMR_URL)),
                                 ("mixed", (SAMR_URL,))):
            order = replace(ORDER, channel=channel)
            tools = OrderTools(Mock(), Mock(), Mock(), logistics_lookup={order.order_id: order}.get,
                               jd_plan_lookup=index.search_jd_reference)
            tools.search_shop_policy(order.order_id, "退货运费")
            self.assertEqual(index.search_evidence.call_args.kwargs["source"], allowed)

    def test_import_preserves_missing_promises_and_rounded_source_price(self):
        """按发布说明计算承诺日期，并保留源金额的精度与符号。"""
        placed = datetime(2018, 3, 1, 12)
        self.assertEqual(promised_date("1", placed), date(2018, 3, 2))
        self.assertIsNone(promised_date("-", placed))
        self.assertEqual(unit_price("53.900000000000006"), Decimal("53.90"))
        self.assertEqual(unit_price("-1.33333333333333"), Decimal("-1.33"))

    def test_colloquial_return_cost_uses_policy_lookup(self):
        """问寄回商品的钱时查询规则，保留独立的件数问题。"""
        rule, order = select_historical_questions("这笔买了几件？我改变主意想退，寄回的钱算哪一方？")
        self.assertIn("寄回的钱", rule)
        self.assertIn("买了几件", order)
