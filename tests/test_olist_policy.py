"""验证导入订单的公共规则范围与买家、客服入口。"""

from datetime import date
from decimal import Decimal
import unittest
from unittest.mock import Mock

from fastapi.testclient import TestClient

from app.access import DemoAccess
from app.api import create_app
from app.integrations.mcp.queries import OrderTools
from app.orders.logistics import LogisticsOrder
from app.orders.olist_items import OrderItem
from app.policies.evidence import PolicyEvidencePlan, PolicySource
from app.policies.olist_reference import OLIST_REFERENCE_SCOPE
from app.policies.search import PolicyAssistant


ORDER = LogisticsOrder("c1f9bef75e" + "1" * 22, "delivered", date(2018, 4, 1),
                       date(2018, 4, 11), date(2018, 4, 9), ("a" * 32,))
ARTICLE = PolicySource("BR-CDC-49", "公开退货规则", "公开资料", "网购签收后七日内可以撤回。")


class OlistPolicyTests(unittest.TestCase):
    """使用有匿名卖家归属的历史订单覆盖路由和权限边界。"""

    def test_mcp_uses_a_distinct_reference_scope(self):
        """Olist 只交给巴西规则索引，未知订单不执行检索。"""
        reference = Mock(return_value=PolicyEvidencePlan([ARTICLE]))
        store = Mock(return_value=None)
        tools = OrderTools(store, store, store, logistics_lookup={ORDER.order_id: ORDER}.get,
                           olist_plan_lookup=reference)
        summary = tools.get_order(ORDER.order_id.lower())
        matches = tools.search_shop_policy(ORDER.order_id, "无理由退货")
        self.assertEqual(summary.signed_on, ORDER.delivered_on)
        self.assertIsNone(summary.return_window_days)
        self.assertEqual((matches.shop_id, matches.sources), (OLIST_REFERENCE_SCOPE, [ARTICLE]))
        with self.assertRaises(ValueError):
            tools.search_shop_policy("OL-0000000002", "无理由退货")
        reference.assert_called_once_with(OLIST_REFERENCE_SCOPE, "无理由退货")
        store.assert_not_called()

    def test_chat_preserves_items_and_protects_the_direct_api(self):
        """混合问法同时查询商品和规则，非本人订单不能进入取证。"""
        assistant = PolicyAssistant(Mock(return_value=[ARTICLE]))
        workflow = Mock()
        workflow.run.return_value = {"status": "answered", "answer": "按公开规则核对。", "sources": []}
        item = OrderItem(1, "e" * 32, "utilidades_domesticas", Decimal("13.00"), Decimal("5.00"))
        accounts = {"buyer-a": "buyer-alpha-test-secret", "buyer-b": "buyer-bravo-test-secret"}
        app = create_app(logistics_lookup={ORDER.order_id: ORDER}.get, items_lookup=lambda _: [item],
                         olist_policy_assistant=assistant, buyer_workflow=workflow, access=DemoAccess(accounts))
        with TestClient(app) as client:
            client.post("/api/session/login", json={"account_id": "buyer-a", "secret": accounts["buyer-a"]})
            chat = "/api/conversation"
            opened = client.post(chat + "/share-order", json={"order_id": ORDER.order_id}).json()
            payload = {"conversation_id": opened["conversation_id"], "active_order_id": ORDER.order_id,
                       "question": "这笔订单买了几件？另外依法无理由退货的退货运费谁承担？"}
            result = client.post(chat, json=payload).json()["result"]
            self.assertEqual(result["olist_items"]["quantity"], 1)
            self.assertEqual(result["policy_assistance"]["status"], "answered")
            args = workflow.run.call_args.args
            self.assertEqual(args, (ORDER.order_id, "依法无理由退货的退货运费谁承担"))
            client.post("/api/session/login", json={"account_id": "buyer-b", "secret": accounts["buyer-b"]})
            response = client.post(f"/api/orders/{ORDER.order_id}/policy-assistance", json={"question": "几天能退货"})
            self.assertEqual(response.status_code, 404)
        workflow.run.assert_called_once()

    def test_staff_uses_scope_from_ticket_order(self):
        """客服工单只能由已关联的 Olist 订单取得公开规则。"""
        reference = Mock(return_value=[ARTICLE])
        assistant = PolicyAssistant(reference, lambda *_: "七日内提出 [BR-CDC-49]")
        tickets = Mock()
        tickets.get_by_number.return_value = {"order_id": ORDER.order_id}
        app = create_app(tickets=tickets, logistics_lookup={ORDER.order_id: ORDER}.get,
                         olist_policy_assistant=assistant)
        with TestClient(app) as client:
            result = client.post("/api/staff/tickets/1/policy-search", json={"question": "多久能退货？"}).json()
        self.assertTrue(result["answer"].startswith("巴西电商公开规则"))
        self.assertEqual(result["sources"][0]["article_id"], "BR-CDC-49")
        reference.assert_called_once_with(OLIST_REFERENCE_SCOPE, "多久能退货？")
