"""验证多轮追问、会话隔离、店铺政策和暂时故障重试。"""

import unittest
from datetime import date
from uuid import uuid4

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from app.orders.rules import Order
from app.integrations.deepseek import ModelError
from app.storage.postgres_orders import OrderStoreError
from app.api import create_app


class ConversationTests(unittest.TestCase):
    """通过 FastAPI 测试客户端验证售后多轮会话。"""

    def setUp(self):
        """创建独立的工作台和内存数据源。"""
        self.client = self.enterContext(TestClient(create_app()))

    def send(self, question, *, client=None, **values):
        """向多轮接口提交问题与可选的会话参数。"""
        return (client or self.client).post(
            "/api/conversation", json={"question": question, **values},
        )

    def start(self, **values):
        """发送缺少订单号的问题并返回等待补充的会话 ID。"""
        response = self.send("这单还能退吗？", **values)
        self.assertEqual(response.status_code, 200)
        pending = response.json()
        self.assertEqual(pending["result"]["status"], "missing_order_id")
        self.assertTrue(pending["awaiting_order_id"])
        return pending["conversation_id"]

    def test_uses_shop_policy_even_with_a_legacy_client_override(self):
        """验证旧客户端提交期限时，仍以订单店铺的政策为准。"""
        conversation = self.start(return_window_days=15)
        finished = self.send("A102", conversation_id=conversation, return_window_days=15).json()
        self.assertFalse(finished["awaiting_order_id"])
        self.assertEqual(finished["result"]["status"], "outside_window")
        self.assertEqual(finished["result"]["return_window_days"], 7)
        switched = self.send("A101", conversation_id=conversation).json()
        self.assertEqual(switched["result"]["order_id"], "A101")
        self.assertEqual(switched["result"]["return_window_days"], 7)

    def test_two_shops_produce_different_results_for_eight_days(self):
        """验证签收天数相同的两笔订单按各自店铺政策得到不同结论。"""
        first = self.send("A102").json()["result"]
        second = self.send("A104").json()["result"]
        self.assertEqual(first["days_since_delivery"], 8)
        self.assertEqual(second["days_since_delivery"], 8)
        self.assertEqual((first["return_window_days"], first["status"]), (7, "outside_window"))
        self.assertEqual((second["return_window_days"], second["status"]), (15, "eligible"))
        self.assertNotEqual(first["shop_name"], second["shop_name"])
        self.assertIn("7 天", first["policy_name"])
        self.assertIn("15 天", second["policy_name"])

    def test_continues_asking_after_ambiguous_reply(self):
        """回复含多个订单号时继续追问，并在明确后完成查询。"""
        conversation = self.start()
        pending = self.send("A101 或 A102", conversation_id=conversation).json()
        self.assertTrue(pending["awaiting_order_id"])
        self.assertEqual(pending["conversation_id"], conversation)
        self.assertIn("一个订单号", pending["result"]["message"])
        result = self.send("A102", conversation_id=conversation).json()
        self.assertEqual(result["result"]["status"], "outside_window")

    def test_recovers_pending_and_completed_state_in_another_app(self):
        """复用同一个检查点仓储，在另一个应用中恢复原问题与最后结果。"""
        saver = InMemorySaver()
        with TestClient(create_app(order_checkpointer=saver)) as initial:
            conversation = self.start(client=initial)
        with TestClient(create_app(order_checkpointer=saver)) as rebuilt:
            url = f"/api/conversation/{conversation}"
            pending = rebuilt.get(url)
            self.assertEqual(pending.status_code, 200)
            self.assertEqual(pending.json()["question"], "这单还能退吗？")
            self.assertTrue(pending.json()["awaiting_order_id"])
            completed = self.send("A104", client=rebuilt, conversation_id=conversation).json()
            repeated = self.send("A104", client=rebuilt, conversation_id=conversation).json()
            self.assertEqual(repeated, completed)
            restored = rebuilt.get(url).json()
        self.assertFalse(restored["awaiting_order_id"])
        self.assertEqual(restored["result"], completed["result"])
        self.assertEqual(restored["result"]["order_id"], "A104")

    def test_unknown_restore_returns_404(self):
        """旧会话无法取回时提示开始新查询。"""
        response = self.client.get(f"/api/conversation/{uuid4()}")
        self.assertEqual(response.status_code, 404)
        self.assertIn("新查询", response.json()["message"])

    def test_isolated_sessions_and_unknown_id(self):
        """验证不同会话各自继续，未知 ID 返回冲突。"""
        one = self.start()
        two = self.start()
        self.assertNotEqual(one, two)
        self.assertEqual(self.send("A102", conversation_id=one).json()["result"]["status"], "outside_window")
        self.assertEqual(self.send("A104", conversation_id=two).json()["result"]["status"], "eligible")
        self.assertEqual(self.send("A102", conversation_id=str(uuid4())).status_code, 409)

    def test_retries_same_reply_after_model_failure(self):
        """模型暂时失败后允许原回复重试，保存首轮问题。"""
        requests = []

        def model(question):
            """第一次模拟失败，重试时返回 A102 工具参数。"""
            requests.append(question)
            if len(requests) == 1:
                raise ModelError("暂时不可用")
            return {"name": "get_order", "arguments": '{"order_id":"A102"}'}

        with TestClient(create_app(model)) as client:
            conversation = self.start(client=client)
            self.assertEqual(requests, [])
            failed = self.send("A102", client=client, conversation_id=conversation)
            self.assertEqual(failed.status_code, 502)
            waiting_retry = client.get(f"/api/conversation/{conversation}").json()
            self.assertEqual(waiting_retry["retry_reply"], "A102")
            self.assertEqual(self.send("A101", client=client, conversation_id=conversation).status_code, 409)
            finished = self.send("A102", client=client, conversation_id=conversation)
        self.assertEqual(finished.json()["result"]["status"], "outside_window")
        self.assertEqual(len(requests), 2)
        self.assertTrue(all("这单还能退吗？" in message and "A102" in message for message in requests))

    def test_complete_first_turn_and_request_validation(self):
        """首轮有订单号时直接查询，并拒绝不合法的会话 ID。"""
        response = self.send("查 A103")
        self.assertEqual(response.json()["result"]["status"], "not_signed")
        self.assertFalse(response.json()["awaiting_order_id"])
        self.assertEqual(self.send("A102", conversation_id="bad-id").status_code, 400)

    def test_missing_policy_prevents_eligibility_decision(self):
        """缺少有效店铺关联时应提示无法判断。"""
        class OrdersWithoutPolicy:
            """提供没有关联店铺的订单。"""

            def get_order(self, order_id):
                """返回具备签收日期、缺少店铺编号的测试订单。"""
                return Order(order_id, date(2026, 11, 5))

            def get_policy(self, shop_id):
                """记录意外的查询，缺少店铺编号时不应执行这一方法。"""
                raise AssertionError("An order without a shop must not query a policy")

        with TestClient(create_app(orders=OrdersWithoutPolicy())) as client:
            result = self.send("A102", client=client).json()["result"]
        self.assertEqual(result["status"], "policy_unavailable")
        self.assertNotIn("return_window_days", result)

    def test_policy_failure_retries_without_rereading_the_order(self):
        """政策读取失败后从对应节点重试，复用已查询的订单。"""
        class UnavailablePolicyStore:
            """记录查单与政策读取次数，政策读取第一次失败。"""

            order_reads = 0
            policy_reads = 0

            def get_order(self, order_id):
                """返回已签收订单并记录查单次数。"""
                self.order_reads += 1
                return Order(order_id, date(2026, 11, 5), "demo-standard")

            def get_policy(self, shop_id):
                """第一次模拟暂时故障，第二次返回缺少政策。"""
                self.policy_reads += 1
                if self.policy_reads == 1:
                    raise OrderStoreError("暂时无法读取政策")
                return None

        store = UnavailablePolicyStore()
        with TestClient(create_app(orders=store)) as client:
            conversation = self.start(client=client)
            failed = self.send("A102", client=client, conversation_id=conversation)
            self.assertEqual(failed.status_code, 503)
            retried = self.send("A102", client=client, conversation_id=conversation)
        self.assertEqual(retried.json()["result"]["status"], "policy_unavailable")
        self.assertEqual((store.order_reads, store.policy_reads), (1, 2))
