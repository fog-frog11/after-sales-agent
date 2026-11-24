"""验证退货订单的绑定、条款问答续聊与跨订单隔离。"""

import unittest
from unittest.mock import Mock

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from app.access import DemoAccess
from app.api import create_app
from app.integrations.mcp.client import McpEvidenceError
from app.orders.shop_questions import select_shop_question


PATH = "/api/conversation"
FIRST = "我买错了想退货，退货运费谁承担？包装破损要拍哪些照片？"
SOURCE = {"article_id": "QH-03", "title": "包装破损", "text": "保留外箱照片。", "source": "青禾 §3"}
POLICY = {"status": "answered", "answer": "保留外箱照片 [QH-03]。", "sources": [SOURCE],
          "function_call": "search_shop_policy", "mcp_tools": ["get_order", "search_shop_policy"]}


class ShopConversationTests(unittest.TestCase):
    """用对话 API 覆盖首问补单、多轮追问和失败后的恢复。"""

    def send(self, client, question, conversation_id=None):
        """向同一会话提交消息，检查 HTTP 响应后返回数据。"""
        payload = {"question": question}
        if conversation_id is not None:
            payload["conversation_id"] = conversation_id
        response = client.post(PATH, json=payload)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_order_reply_runs_the_original_question_and_refresh_restores_answer(self):
        """用户的补号不取代两项原问题，答复、出处和上下文可继续恢复。"""
        saver = InMemorySaver()
        workflow = Mock()
        workflow.run.return_value = POLICY
        model = Mock(return_value={"name": "get_order", "arguments": '{"order_id":"A102"}'})
        with TestClient(create_app(model, buyer_workflow=workflow, order_checkpointer=saver)) as initial:
            ask = self.send(initial, FIRST)
            self.assertTrue(ask["awaiting_order_id"])
            workflow.run.assert_not_called()
            cid = ask["conversation_id"]
            bound = self.send(initial, "A102", cid)
            self.assertEqual(bound["result"]["status"], "outside_window")
            self.assertFalse(bound["result"]["show_order_summary"])
            self.assertTrue(bound["can_continue"])
            self.assertEqual(bound["result"]["policy_assistance"]["sources"], [SOURCE])
            self.assertEqual(workflow.run.call_args.args, ("A102", FIRST.rstrip("？")))
        with TestClient(create_app(model, buyer_workflow=workflow, order_checkpointer=saver)) as refreshed:
            restored = refreshed.get(f"{PATH}/{cid}").json()
            self.assertEqual(restored["turns"], bound["turns"])
            answer = self.send(refreshed, "包装破损还需要快递面单吗？", cid)
            self.assertEqual(answer["conversation_id"], cid)
            self.assertEqual(answer["result"]["order_id"], "A102")
            self.assertFalse(answer["awaiting_order_id"])
            self.assertEqual(workflow.run.call_args.args, ("A102", "包装破损还需要快递面单吗"))
            self.assertEqual(len(answer["turns"]), 3)
        model.assert_called_once()

    def test_explicit_switch_and_foreign_order_never_reuse_an_old_order(self):
        """可显式换到本人另一单，越权查询后要求重选且不读其他店铺。"""
        credentials = {name: f"simulation-secret-for-{name}" for name in ("buyer-a", "buyer-b", "staff")}
        workflow = Mock()
        workflow.run.return_value = POLICY
        with TestClient(create_app(access=DemoAccess(credentials), buyer_workflow=workflow)) as client:
            self.assertEqual(client.post("/api/session/login", json={
                "account_id": "buyer-a", "secret": credentials["buyer-a"],
            }).status_code, 200)
            cid = self.send(client, "A102")["conversation_id"]
            self.assertEqual(self.send(client, "换到 A101", cid)["result"]["order_id"], "A101")
            self.assertEqual(self.send(client, "运费谁承担？", cid)["result"]["order_id"], "A101")
            self.assertEqual(workflow.run.call_args.args[0], "A101")
            foreign = self.send(client, "A104 包装破损怎么处理？", cid)
            self.assertEqual(foreign["result"]["status"], "order_not_found")
            self.assertNotIn("policy_assistance", foreign["result"])
            pending = self.send(client, "运费谁承担？", cid)
            self.assertTrue(pending["awaiting_order_id"])
            self.assertEqual(pending["turns"][:4], foreign["turns"])
        workflow.run.assert_called_once()

    def test_deadline_and_policy_are_separate_parts_within_the_same_turn(self):
        """纯期限查询无需 RAG，混合查询同时保留判断与相应条款。"""
        workflow = Mock()
        workflow.run.return_value = POLICY
        with TestClient(create_app(buyer_workflow=workflow)) as client:
            status = self.send(client, "A102 这单还能退吗？")
            cid = status["conversation_id"]
            workflow.run.assert_not_called()
            result = self.send(client, "这单还能退吗？包装破损要拍哪些照片？", cid)["result"]
        self.assertTrue(result["show_order_summary"])
        self.assertIn("policy_assistance", result)
        self.assertEqual(workflow.run.call_args.args, ("A102", "包装破损要拍哪些照片"))

    def test_clause_failure_saves_active_order_for_retry(self):
        """MCP 发生暂时错误时保存订单及提示，重问可以继续回答。"""
        workflow = Mock()
        workflow.run.side_effect = [McpEvidenceError("模拟故障"), POLICY]
        with TestClient(create_app(buyer_workflow=workflow)) as client:
            cid = self.send(client, "A102")["conversation_id"]
            failed = self.send(client, "包装破损怎么处理？", cid)
            self.assertTrue(failed["can_continue"])
            self.assertEqual(failed["result"]["policy_assistance"]["status"], "unavailable")
            retry = self.send(client, "包装破损怎么处理？", cid)
        self.assertEqual(retry["result"]["policy_assistance"]["status"], "answered")
        self.assertEqual(retry["result"]["order_id"], "A102")

    def test_refund_timing_is_not_treated_as_return_eligibility(self):
        """退款到账时效是条款问题，退货期限仍使用订单规则。"""
        for question in ("退款多久能到账？", "多久能收到退款？", "退货以后退款要几天？"):
            with self.subTest(question=question):
                self.assertEqual(select_shop_question(question, None), (question.rstrip("？"), False))
        self.assertEqual(select_shop_question("签收后几天能退？", None), (None, True))
