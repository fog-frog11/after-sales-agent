"""验证提供给 MCP 的只读查询与店铺范围。"""

import unittest
from datetime import date

from app.orders.rules import get_order, get_policy
from app.integrations.mcp.queries import OrderTools
from app.policies.search import PolicySource


ARTICLE = PolicySource("QH-03", "包装破损", "青禾模拟售后手册 §3", "保留外箱和商品照片。")


class OrderToolsTests(unittest.TestCase):
    """从模拟仓储验证工具响应和无效输入。"""

    def setUp(self):
        """为每个用例装配独立的只读工具。"""
        self.tools = OrderTools(get_order, get_policy, self.search)
        self.searches = []

    def search(self, shop_id, question):
        """记录检索接到的真实店铺编号和问题。"""
        self.searches.append((shop_id, question))
        return [ARTICLE]

    def test_order_summary_uses_existing_policy(self):
        """返回签收日期、店铺名称和适用期限。"""
        order = self.tools.get_order(" a102 ")
        self.assertEqual(order.order_id, "A102")
        self.assertEqual(order.signed_on, date(2026, 11, 5))
        self.assertEqual(order.shop_name, "青禾生活馆")
        self.assertEqual(order.return_window_days, 7)

    def test_search_uses_shop_from_order(self):
        """从订单确定检索范围，并返回原文。"""
        result = self.tools.search_shop_policy("A102", " 包装破损怎么处理？  ")
        self.assertEqual(self.searches, [("demo-standard", "包装破损怎么处理？")])
        self.assertEqual(result.sources, [ARTICLE])

    def test_invalid_or_unknown_order_does_not_search(self):
        """格式错误、订单不存在和空政策问题都不发起检索。"""
        for order_id, question in (("坏编号", "包装"), ("A999", "包装"), ("A102", " ")):
            with self.subTest(order_id=order_id, question=question), self.assertRaises(ValueError):
                self.tools.search_shop_policy(order_id, question)
        self.assertEqual(self.searches, [])
