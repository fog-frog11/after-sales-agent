"""回归分段答复的实时顺序、会话复用和账号隔离。"""

import json
import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import Mock

from fastapi.testclient import TestClient

from app.access import DemoAccess
from app.api import create_app
from app.orders.conversation import OrderConversation
from app.orders.logistics import LogisticsOrder
from app.orders.olist_items import OrderItem
from app.orders.rules import get_order, get_policy
from app.orders.shop_questions import select_historical_questions
from app.policies.search import PolicyAssistant


ORDER = LogisticsOrder("eeeeeeeeeeee" + "2" * 52, "delivered", date(2018, 3, 1), date(2018, 3, 2),
                       date(2018, 3, 2), origin="jd", channel="self")
ITEM = OrderItem(1, "e" * 10, None, Decimal("12.30"), None, 3)
ASK = "这单买了几件，退货运费谁出？"
PATH = "/api/conversation/stream"


class ChatStreamTests(unittest.TestCase):
    """验证新入口复用完整结果的数据约束。"""

    def setUp(self):
        """为两位买家提供隔离登录和可验证的国内订单。"""
        self.credentials = {name: f"test-secret-for-user-{name}" for name in ("buyer-a", "buyer-b")}
        self.workflow = Mock()
        self.workflow.run.return_value = {"status": "answered", "answer": "已核对条款。", "sources": []}
        self.items = Mock(return_value=[ITEM])
        self.router = Mock(side_effect=AssertionError("明确的查件数不应调用模型路由"))
        self.client = self.enterContext(TestClient(create_app(
            access=DemoAccess(self.credentials), logistics_lookup={ORDER.order_id: ORDER}.get,
            items_lookup=self.items, jd_policy_assistant=PolicyAssistant(Mock()), buyer_workflow=self.workflow,
            olist_router=self.router,
        )))
        self.login("buyer-a")

    def login(self, name: str) -> None:
        """切换当前客户端的买家。"""
        response = self.client.post("/api/session/login", json={"account_id": name, "secret": self.credentials[name]})
        self.assertEqual(response.status_code, 200)

    def events(self, **payload) -> list[dict]:
        """解析浏览器所使用的按行消息。"""
        response = self.client.post(PATH, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("application/x-ndjson", response.headers["content-type"])
        return [json.loads(line) for line in response.iter_lines()]

    def test_facts_are_published_before_policy_executes(self):
        """订单事实就绪后发事件，条款函数再开始执行。"""
        order = {ORDER.order_id: ORDER}.get
        evidence_order = []

        def policy(_order_id, _question):
            """模拟比订单查得更慢的条款答复。"""
            self.assertEqual(evidence_order, ["facts"])
            return self.workflow.run.return_value

        dialogue = OrderConversation(None, get_order, get_policy, logistics_lookup=order,
                                     items_lookup=self.items, jd_answer=policy, olist_router=self.router)
        result = dialogue.send(ORDER.order_id + " " + ASK, None, on_facts=lambda _: evidence_order.append("facts"))
        self.assertIn("policy_assistance", result["result"])
        self.router.assert_not_called()

    def test_followup_stream_emits_facts_then_saves_one_complete_turn(self):
        """补单号后发出的事实和最终答复沿用原来的订单以及问题。"""
        prompt = self.events(question=ASK)
        self.assertEqual([event["type"] for event in prompt], ["final"])
        self.assertTrue(prompt[0]["data"]["awaiting_order_id"])
        thread = prompt[0]["data"]["conversation_id"]
        partial, final = self.events(question=ORDER.order_id, conversation_id=thread)
        self.assertEqual((partial["type"], final["type"]), ("partial", "final"))
        self.assertEqual(partial["data"]["olist_items"]["quantity"], 3)
        self.assertNotIn("policy_assistance", partial["data"])
        self.assertEqual(final["data"]["result"]["policy_assistance"]["status"], "answered")
        self.assertEqual(len(final["data"]["turns"]), 2)
        restored = self.client.get("/api/conversation/" + thread).json()
        self.assertEqual(restored["turns"], final["data"]["turns"])
        self.assertEqual(self.workflow.run.call_args.args, (ORDER.order_id, "退货运费谁出"))
        self.router.assert_not_called()

    def test_explicit_facts_and_unowned_orders_do_not_open_policy(self):
        """查库结果只有一条最终答复，跨账号查询不会流出订单数据。"""
        events = self.events(question=ORDER.order_id + " 这单买了几件？")
        self.assertEqual([event["type"] for event in events], ["final"])
        self.assertEqual(events[0]["data"]["result"]["olist_items"]["quantity"], 3)
        self.login("buyer-b")
        events = self.events(question=ORDER.order_id + " " + ASK)
        self.assertEqual([event["type"] for event in events], ["final"])
        self.assertEqual(events[0]["data"]["result"]["status"], "order_not_found")
        self.assertNotIn("olist_items", events[0]["data"]["result"])
        self.workflow.run.assert_not_called()
        self.router.assert_not_called()

    def test_split_keeps_all_policy_questions(self):
        """已明确的数量问题拆开后，多个售后诉求仍留给条款环节。"""
        self.assertEqual(select_historical_questions("这单买了几件，退货运费谁出？另外能改发票吗？"),
                         ("退货运费谁出？能改发票吗", "这单买了几件"))
