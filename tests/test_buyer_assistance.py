"""验证买家政策问答中模型、MCP 和答复之间的交接。"""

import json
import subprocess
import unittest
from datetime import date
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.integrations.deepseek import ModelError
from app.policies.assistance import BuyerPolicyWorkflow
from app.integrations.mcp.client import PolicyEvidence, fetch_mcp_evidence
from app.integrations.mcp.queries import OrderSummary, PolicyMatches
from app.policies.search import PolicySource
from app.api import create_app


CLAUSE = PolicySource("QH-03", "包装破损", "青禾手册 §3", "请保留外箱照片。")


class BuyerAssistanceTests(unittest.TestCase):
    """检查双角色是否通过已验证的工具调用共享原文。"""

    def setUp(self):
        """记录每个角色和查询函数接到的参数。"""
        self.calls = []

    def choose(self, order_id, question):
        """模拟模型提出正确的函数调用。"""
        self.calls.append(("function", order_id, question))
        return {"name": "search_shop_policy", "arguments": json.dumps({
            "order_id": order_id, "question": question,
        })}

    def read(self, order_id, question):
        """模拟 MCP 读取订单和政策，返回结构化材料。"""
        self.calls.append(("mcp", order_id, question))
        return PolicyEvidence(
            order=OrderSummary(order_id=order_id, signed_on=date(2026, 11, 5), return_window_days=7),
            policies=PolicyMatches(order_id=order_id, shop_id="demo-standard", sources=[CLAUSE]),
        )

    def write(self, question, sources, order):
        """模拟客服用命中的一条原文给出答复。"""
        self.calls.append(("reply", question, order.order_id, sources[0].article_id))
        return "请保留外箱照片 [QH-03]。"

    def test_api_runs_each_role_and_returns_original_sources(self):
        """在 API 中依次请求工具、读取 MCP 材料并生成引用。"""
        workflow = BuyerPolicyWorkflow(self.choose, self.read, self.write)
        with TestClient(create_app(buyer_workflow=workflow)) as client:
            self.assertTrue(client.get("/api/config").json()["buyer_policy_enabled"])
            response = client.post("/api/orders/A102/policy-assistance", json={"question": "包装破损"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.calls, [
            ("function", "A102", "包装破损"), ("mcp", "A102", "包装破损"),
            ("reply", "包装破损", "A102", "QH-03"),
        ])
        self.assertEqual(response.json()["sources"][0]["text"], CLAUSE.text)
        self.assertEqual(response.json()["function_call"], "search_shop_policy")
        self.assertEqual(response.json()["mcp_tools"], ["get_order", "search_shop_policy"])

    def test_changed_tool_input_is_rejected_before_execution(self):
        """模型篡改订单号或问题时不调用 MCP。"""
        for changed in (
            {"order_id":"A104", "question":"包装破损"},
            {"order_id":"A102", "question":"退货运费"},
        ):
            with self.subTest(changed=changed):
                workflow = BuyerPolicyWorkflow(
                    lambda order, q: {"name": "search_shop_policy", "arguments": json.dumps(changed)},
                    self.read, self.write,
                )
                with self.assertRaises(ModelError):
                    workflow.run("A102", "包装破损")
        self.assertEqual(self.calls, [])

    def test_success_trace_matches_local_record(self):
        """成功时按顺序记录每一步，并让 API 和日志共用请求记录。"""
        records = []
        workflow = BuyerPolicyWorkflow(self.choose, self.read, self.write)
        with TestClient(create_app(buyer_workflow=workflow, trace_sink=records.append)) as client:
            response = client.post("/api/orders/A102/policy-assistance", json={"question": "包装破损"})
            another = client.post("/api/orders/A102/policy-assistance", json={"question": "包装破损"})
        self.assertEqual(response.status_code, 200)
        trace = response.json()["trace"]
        self.assertEqual(response.headers["X-Request-ID"], trace["request_id"])
        self.assertNotEqual(trace["request_id"], another.json()["trace"]["request_id"])
        self.assertEqual(records[0], {"http_status": 200, **trace})
        self.assertEqual([(step["name"], step["status"]) for step in trace["stages"]], [
            ("order_check", "ok"), ("model_tool_request", "ok"), ("tool_validation", "ok"),
            ("mcp_lookup", "ok"), ("reply_generation", "ok"),
        ])
        for step in trace["stages"]:
            self.assertLessEqual(0, step["duration_ms"])
            self.assertLessEqual(step["duration_ms"], trace["duration_ms"])
        self.assertNotIn("包装破损", str(records))
        self.assertNotIn("A102", str(records))

    def test_mcp_timeout_trace_stops_before_reply(self):
        """MCP 超时时在 API 和日志定位同一失败阶段。"""
        records = []
        workflow = BuyerPolicyWorkflow(self.choose, fetch_mcp_evidence, self.write)
        with (
            patch("app.integrations.mcp.client.subprocess.run", side_effect=subprocess.TimeoutExpired("mcp", 26)),
            TestClient(create_app(buyer_workflow=workflow, trace_sink=records.append)) as client,
        ):
            response = client.post("/api/orders/A102/policy-assistance", json={"question": "包装破损"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("超时", response.json()["message"])
        trace = response.json()["trace"]
        self.assertEqual(response.headers["X-Request-ID"], trace["request_id"])
        self.assertEqual(records, [{"http_status": 503, **trace}])
        self.assertEqual(trace["stages"][-1]["name"], "mcp_lookup")
        self.assertEqual(trace["stages"][-1]["status"], "failed")
        self.assertEqual([call[0] for call in self.calls], ["function"])

    def test_reply_without_citation_marks_fallback(self):
        """客服答复缺失引用时，原文仍可用且阶段标记为降级。"""
        def uncited(question, sources, order):
            """模拟模型给出未注明出处的回复。"""
            return "需要核对。"

        workflow = BuyerPolicyWorkflow(self.choose, self.read, uncited)
        with TestClient(create_app(buyer_workflow=workflow)) as client:
            result = client.post("/api/orders/A102/policy-assistance", json={"question": "包装破损"}).json()
        self.assertEqual(result["status"], "evidence_only")
        self.assertEqual(result["trace"]["stages"][-1]["status"], "fallback")

    def test_no_sources_does_not_call_reply_agent(self):
        """MCP 查无条款时直接提示，并跳过答复模型。"""
        def empty(order_id, question):
            """返回一个没有条款的有效订单。"""
            data = self.read(order_id, question)
            data.policies.sources = []
            return data

        workflow = BuyerPolicyWorkflow(self.choose, empty, self.write)
        with TestClient(create_app(buyer_workflow=workflow)) as client:
            result = client.post("/api/orders/A102/policy-assistance", json={"question": "火星探测器"}).json()
        self.assertEqual(result["status"], "no_matches")
        self.assertEqual(result["trace"]["stages"][-1], {
            "name": "reply_generation", "status": "skipped", "duration_ms": 0,
        })
        self.assertEqual([call[0] for call in self.calls], ["function", "mcp"])
