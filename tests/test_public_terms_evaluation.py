"""检查公开条款评测的原文解析、动作映射和指标。"""

import unittest
from unittest.mock import Mock, patch

from app.policies.public_benchmark import BENCHMARK_SHOP, RetrievalCase, clip_utf8, retail_articles, retail_cases
from app.policies.search import PolicyIndexError, PolicySource
from scripts.evaluate_public_terms import RetrievalResult, provision_index, run_query, summarize


class PublicTermsEvaluationTests(unittest.TestCase):
    """验证评测输入与分数的含义可独立复查。"""

    def test_task_uses_original_question_and_all_mapped_sections(self):
        """换货和改地址同时出现在目标中，问题仍使用原始文本。"""
        articles = retail_articles(
            "# Retail agent policy\nCheck identity first.\n\n## Blank\n\n"
            "## Exchange delivered order\nExchange is allowed.\n\n"
            "## Modify pending order\nEdit the delivery address."
        )
        self.assertEqual(len(articles), 3)
        tasks = [{
            "id": "public-1",
            "user_scenario": {"instructions": {"reason_for_call": "I need two things done."}},
            "evaluation_criteria": {"actions": [{"name": "exchange_delivered_order_items"},
                                                {"name": "modify_pending_order_address"}]},
        }]
        case, = retail_cases(tasks, articles)
        self.assertEqual(case.question, "I need two things done.")
        self.assertEqual(set(case.expected_ids), {article["id"] for article in articles[1:]})

    def test_missing_source_section_raises(self):
        """源文件变更导致条款映射失效时，评测明确失败。"""
        articles = retail_articles("# Retail agent policy\nCheck identity first.")
        task = {"id": "public-1", "user_scenario": {"instructions": {"reason_for_call": "Cancel it."}},
                "evaluation_criteria": {"actions": [{"name": "cancel_pending_order"}]}}
        with self.assertRaisesRegex(ValueError, "Cancel pending order"):
            retail_cases([task], articles)

    def test_utf8_clipping_keeps_complete_characters(self):
        """按索引字节上限裁剪时仍保留合法的 UTF-8。"""
        self.assertEqual(clip_utf8("售后条款", 7), "售后")

    def test_query_extracts_ids_from_project_retrieval_results(self):
        """从现有条款检索接口读取真实字段并保持排序。"""
        index = Mock()
        index.search.return_value = [PolicySource("a", "Return", "Example", "Keep package.")]
        result = run_query(index, RetrievalCase("first", "Can I send it back?", ("a",)))
        index.search.assert_called_once_with(BENCHMARK_SHOP, "Can I send it back?", source=None)
        self.assertEqual(result.retrieved_ids, ("a",))

    @patch("scripts.evaluate_public_terms.MilvusPolicies")
    def test_read_only_mode_never_provisions_a_mismatched_collection(self, factory):
        """集合数据与预期不符时停止评测，避免创建、加载或补写。"""
        index = factory.return_value
        index.request.return_value = {"has": True}
        index.count_articles.return_value = 0
        with self.assertRaisesRegex(PolicyIndexError, "条款数与语料不符"):
            provision_index("http://127.0.0.1:19531", [{"id": "public"}], existing_only=True)
        self.assertEqual(index.request.call_count, 1)
        self.assertEqual(index.request.call_args.args[0], "collections/has")
        index.index_articles.assert_not_called()

    def test_metrics_distinguish_partial_matches_and_service_errors(self):
        """部分命中只计入部分召回，失败请求计入分母且不污染延迟。"""
        report = summarize([
            RetrievalResult("first", ("a", "b"), ("a", "noise"), 2.0),
            RetrievalResult("second", ("c",), ("c",), 5.0),
            RetrievalResult("third", ("d",), (), 999.0, "connection refused"),
        ])
        self.assertEqual(report["failures"], 1)
        self.assertAlmostEqual(report["hit_at_3"], 2 / 3, places=4)
        self.assertEqual(report["recall_at_3"], 0.5)
        self.assertAlmostEqual(report["all_at_3"], 1 / 3, places=4)
        self.assertEqual(report["p95_ms"], 5.0)
