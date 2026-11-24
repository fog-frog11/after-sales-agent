"""验证商品售后入口、运费依据与跨订单追问的边界。"""

import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal
from unittest.mock import Mock

from fastapi.testclient import TestClient

from app.access import DemoAccess
from app.api import create_app
from app.orders.logistics import LogisticsOrder
from app.tickets.service_requests import assess_return_freight


SELF_ORDER = LogisticsOrder("ee0000660002" + "2" * 52, "delivered", date(2018, 3, 1), date(2018, 3, 2),
                            date(2018, 3, 2), origin="jd", channel="self")
OTHER_ORDER = replace(SELF_ORDER, source_order_id="ee0000660004" + "4" * 52, channel="market")


class ServiceRequestTests(unittest.TestCase):
    """保护售后问答的输入绑定和服务单 API 授权。"""

    def setUp(self):
        """配置两个测试账号和两个真实结构的京东订单对象。"""
        self.credentials = {name: f"long-local-password-for-{name}" for name in ("buyer-a", "buyer-b")}
        self.repository = Mock()
        self.repository.freight_practice.return_value = None
        self.repository.page.return_value = {"requests": [], "next_before": None}
        orders = {order.order_id: order for order in (SELF_ORDER, OTHER_ORDER)}
        self.client = self.enterContext(TestClient(create_app(
            access=DemoAccess(self.credentials), logistics_lookup=orders.get, service_requests=self.repository,
        )))
        self.login("buyer-a")

    def login(self, account: str) -> None:
        """使用当前浏览器切换本机买家。"""
        response = self.client.post("/api/session/login", json={"account_id": account, "secret": self.credentials[account]})
        self.assertEqual(response.status_code, 200)

    def test_unknown_freight_prompts_and_does_not_carry_reason_across_orders(self):
        """缺信息时追问，历史订单本身的费用保持未核实。"""
        endpoint = "/api/conversation"
        opened = self.client.post(endpoint + "/share-order", json={"order_id": SELF_ORDER.order_id}).json()
        context = {"conversation_id": opened["conversation_id"], "active_order_id": SELF_ORDER.order_id}
        first = self.client.post(endpoint, json={**context, "question": "退货的话运费怎么搞？"}).json()
        self.assertEqual(first["result"]["return_freight"]["status"], "needs_reason")
        second = self.client.post(endpoint, json={**context, "question": "商品有质量问题"}).json()
        verdict = second["result"]["return_freight"]
        self.assertEqual((verdict["status"], verdict["practice"], verdict["reason"]), ("needs_review", False, "quality"))
        next_order = self.client.post(endpoint + "/share-order", json={"order_id": OTHER_ORDER.order_id}).json()
        self.assertNotEqual(next_order["conversation_id"], opened["conversation_id"])
        third = self.client.post(endpoint, json={"conversation_id": next_order["conversation_id"],
                                                "question": "退货运费谁承担？"}).json()
        self.assertEqual(third["result"]["return_freight"]["status"], "needs_reason")

    def test_buyer_progress_read_uses_current_account(self):
        """明细和状态通过同一归属检查，越权请求不读仓储。"""
        response = self.client.get(f"/api/buyer/service-requests?order_id={SELF_ORDER.order_id}")
        self.assertEqual(response.status_code, 200)
        query = {"buyer_id": "buyer-a", "order_id": SELF_ORDER.order_id, "before": None}
        self.repository.page.assert_called_once_with(**query)
        self.login("buyer-b")
        response = self.client.get(f"/api/buyer/service-requests?order_id={SELF_ORDER.order_id}")
        self.assertEqual(response.status_code, 404)
        self.repository.page.assert_called_once_with(**query)

    def test_practice_quote_handles_voucher_and_advance_payment(self):
        """优惠和垫付属于明确标记的演练条件。"""
        practice = {"payer": "buyer", "quoted_fee": Decimal("12.00"), "voucher": Decimal("8.00"), "prepaid": False}
        quote = assess_return_freight(SELF_ORDER, "wrong_purchase", practice)
        self.assertTrue(quote["practice"])
        self.assertIn("预计自付 ¥4.00", quote["message"])
        quote = assess_return_freight(SELF_ORDER, "quality", {**practice, "payer": "seller", "prepaid": True})
        self.assertIn("最终自付 ¥0.00", quote["message"])
        self.assertIn("先垫付 ¥12.00", quote["message"])
