"""验证混合候选的去重、排名和证据窗口。"""

import unittest
from unittest.mock import Mock

from app.policies.public_benchmark import BENCHMARK_SHOP, RETAIL_SOURCE, RetrievalCase
from app.policies.retrieval_ranking import article_text, evidence_windows, reciprocal_rank_fusion
from app.policies.search import PolicySource
from scripts.evaluate_hybrid_terms import CANDIDATE_LIMIT, compare_query, metrics_for


class RetrievalRankingTests(unittest.TestCase):
    """验证候选范围和计算评测分数时的边界。"""

    def test_fusion_accumulates_agreement_without_double_counting_one_route(self):
        """共同召回的条款获得双路贡献，路内重复条款只计一次。"""
        self.assertEqual(
            reciprocal_rank_fusion(["a", "a", "agreed"], ["b", "agreed", "c"]),
            ["agreed", "a", "b", "c"],
        )
        self.assertEqual(
            reciprocal_rank_fusion(["a", "agreed"], ["b", "agreed", "c"], weights=(4, 1)),
            ["agreed", "a", "b", "c"],
        )
        self.assertEqual(reciprocal_rank_fusion(["a"], ["b"], weights=(1, 4)), ["b", "a"])

    def test_windows_keep_title_and_include_evidence_near_the_end(self):
        """长条款尾部的例外条款仍会进入局部证据窗口。"""
        source = PolicySource("a", "退货", "-", "退货\n可申请退货并保留包装，但生鲜商品需要人工复核。")
        self.assertEqual(article_text(source), source.text)
        windows = evidence_windows(source, size=12, overlap=4)
        self.assertTrue(all(window.startswith("退货\n") for window in windows))
        self.assertTrue(windows[-1].endswith("人工复核。"))

    def test_comparison_uses_same_rulebook_and_records_candidates_for_review(self):
        """关键词路来自已知手册，重排仅接收融合后的条款编号。"""
        index = Mock()
        index.search.return_value = [PolicySource("a", "-", "-", "-"), PolicySource("b", "-", "-", "-")]
        semantic = Mock()
        semantic.dense_ranking.return_value = ["b", "c", "d", "e"]
        semantic.window_ranking.return_value = ["c", "b", "a", "d", "e"]
        case = RetrievalCase("public", "Can I send the item back?", ("c", "e"))
        runs = [compare_query(index, semantic, case)]
        index.search.assert_called_once_with(
            BENCHMARK_SHOP, case.question, source=RETAIL_SOURCE, limit=CANDIDATE_LIMIT,
        )
        self.assertEqual(set(semantic.window_ranking.call_args.args[1]), {"a", "b", "c", "d", "e"})
        report = metrics_for([case], runs, "rrf_window")
        self.assertEqual(report["recall_at_3"], 0.5)
        self.assertEqual(report["mrr_at_3"], 0.5)
        self.assertEqual(report["candidate_recall"], 1.0)
        self.assertEqual(len(report["results"][0]["candidate_order"]), 5)
