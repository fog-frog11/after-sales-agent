"""验证店铺范围、政策原文和模型引用校验。"""

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.integrations.deepseek import ModelError
from app.storage.milvus_policies import MilvusPolicies
from app.policies.search import PolicyAssistant, PolicySource, deepseek_policy_writer
from app.api import create_app


SOURCE = PolicySource("QH-03", "破损包装", "青禾模拟售后手册 §3", "请保留外箱照片供人工复核。")


class PolicySearchTests(unittest.TestCase):
    """覆盖政策检索的安全范围及生成失败时的原文回退。"""

    def test_milvus_query_filters_by_shop(self):
        """向 Milvus 传入店铺过滤条件与中文原问题。"""
        index = MilvusPolicies("http://127.0.0.1:19531")
        rows = [
            {"id": "QH-03", "shop_id": "demo-standard", "title": SOURCE.title,
             "source": SOURCE.source, "text": SOURCE.text},
            {"id": "QM-03", "shop_id": "demo-extended", "title": "他店条款",
             "source": "他店手册", "text": "他店内容"},
        ]
        with patch.object(index, "request", return_value=rows) as request:
            self.assertEqual(index.search("demo-standard", "包装破损"), [SOURCE])
        request.assert_called_once()
        path, payload = request.call_args.args
        self.assertEqual(path, "entities/search")
        self.assertEqual(payload["filter"], 'shop_id == "demo-standard"')
        self.assertEqual(payload["data"], ["包装破损"])

    def test_optional_source_retains_shop_filter_and_exact_match(self):
        """指定规则手册时，只接受店铺与手册同时匹配的条款。"""
        index = MilvusPolicies("http://127.0.0.1:19531")
        source = '手册 "2026"'
        row = {"id": "QH-03", "shop_id": "demo-standard", "title": SOURCE.title,
               "source": source, "text": SOURCE.text}
        rows = [row, dict(row, id="QH-02", source="旧手册"), dict(row, id="QM-03", shop_id="demo-extended")]
        with patch.object(index, "request", return_value=rows) as request:
            self.assertEqual([article.article_id for article in index.search(
                "demo-standard", "包装破损", source=source,
            )], ["QH-03"])
        self.assertEqual(request.call_args.args[1]["filter"], 'shop_id == "demo-standard" and source == "手册 \\"2026\\""')

    def test_no_hits_skips_the_model(self):
        """无命中时提示查无条款，并跳过生成。"""
        def unexpected_model(question, sources):
            """只有检索命中才允许调用模型。"""
            self.fail("Model must not run without articles")

        assistant = PolicyAssistant(lambda shop, question: [], unexpected_model)
        result = assistant.answer("demo-standard", "火星探测器")
        self.assertEqual(result["status"], "no_matches")
        self.assertEqual(result["sources"], [])

    def test_source_allowlist_keeps_scope_even_if_backend_returns_foreign_source(self):
        """跨平台条件输入白名单，客户端拒收范围外条款。"""
        index = MilvusPolicies("http://127.0.0.1:19531")
        base = {"id": "x", "shop_id": "target", "title": "运费", "text": "正文"}
        rows = [dict(base, id="self", source="自营"),
                dict(base, id="general", source="通用"),
                dict(base, id="market", source="第三方")]
        with patch.object(index, "request", return_value=rows) as request:
            found = index.search("target", "谁出运费", source=("自营", "通用"))
        self.assertEqual([article.article_id for article in found], ["self", "general"])
        self.assertEqual(request.call_args.args[1]["filter"], 'shop_id == "target" and source in ["自营", "通用"]')

    def test_invalid_citations_keep_sources(self):
        """未知引用、无引用和模型故障时仍返回已检索到的条款。"""
        def unavailable_model(question, sources):
            """模拟 DeepSeek 请求故障。"""
            raise ModelError("服务暂时不可用")

        for model in (lambda q, s: "答案 [OTHER-01]", lambda q, s: "没有引用", unavailable_model):
            with self.subTest(model=model):
                result = PolicyAssistant(lambda shop, question: [SOURCE], model).answer("demo-standard", "包装破损")
                self.assertEqual(result["status"], "evidence_only")
                self.assertEqual(result["sources"][0]["article_id"], SOURCE.article_id)

    def test_model_can_decline_incidental_search_results(self):
        """有相似候选但无适用条款时，直接提示人工核对。"""
        with patch("app.policies.search.deepseek_chat", return_value={
            "content": '{"support":"none","answer":"包装破损可拍照片 [QH-03]"}',
        }) as chat:
            assistant = PolicyAssistant(lambda shop, question: [SOURCE], deepseek_policy_writer("test"))
            result = assistant.answer("demo-standard", "拍完外箱后能补开电子发票吗？")
        self.assertEqual(chat.call_args.args[1]["response_format"], {"type": "json_object"})
        self.assertEqual(result["status"], "no_matches")
        self.assertEqual(result["sources"], [])
        self.assertIn("人工", result["answer"])

    def test_malformed_model_output_uses_existing_fallback(self):
        """结构化输出不可解析时，工作台仍返回可核对的候选原文。"""
        with patch("app.policies.search.deepseek_chat", return_value={"content": " "}):
            assistant = PolicyAssistant(lambda shop, question: [SOURCE], deepseek_policy_writer("test"))
            result = assistant.answer("demo-standard", "包装破损怎么核对？")
        self.assertEqual(result["status"], "evidence_only")
        self.assertEqual(result["sources"][0]["article_id"], SOURCE.article_id)

    def test_api_uses_ticket_order_shop_and_returns_citations(self):
        """由工单取得真实店铺，响应保留经过校验的引用。"""
        class TicketReader:
            """按工单号提供现有模拟订单。"""

            def get_by_number(self, ticket_no):
                """工单 1 对应青禾订单 A102。"""
                return {"order_id": "A102"} if ticket_no == 1 else None

        def search(shop_id, question):
            """核对店铺范围来自订单记录。"""
            self.assertEqual(shop_id, "demo-standard")
            self.assertEqual(question, "包装破损")
            return [SOURCE]

        assistant = PolicyAssistant(search, lambda q, s: "保留外箱照片供复核 [QH-03]。")
        with TestClient(create_app(tickets=TicketReader(), policy_assistant=assistant)) as client:
            response = client.post("/api/staff/tickets/1/policy-search",
                                   json={"question": "包装破损", "shop_id": "demo-extended"})
            missing = client.post("/api/staff/tickets/999/policy-search", json={"question": "包装破损"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "answered")
        self.assertIn("[QH-03]", response.json()["answer"])
        self.assertEqual(missing.status_code, 404)
