"""验证多诉求检索分配、引用隔离和失败回退。"""

import unittest
from unittest.mock import Mock

from app.policies.evidence import PolicyAspect, PolicyIndexError, PolicySource, plan_evidence, query_aspect, split_aspects
from app.policies.search import explain_policy
from app.storage.milvus_policies import MilvusPolicies


A = PolicySource("A", "退货运费", "适用手册", "买家原因的退货由买家承担。")
B = PolicySource("B", "取件费用", "适用手册", "有费用时从退款中扣除。")
C = PolicySource("C", "参考规则", "适用手册", "保留寄回凭证。")
QUESTION = "退货谁出运费？另外上门取件费用怎么扣？"


class PolicyAspectTests(unittest.TestCase):
    """检查单问成本和多问引用绑定。"""

    def test_single_question_only_searches_once(self):
        """普通语句带条件或逗号时仍走一次三条检索。"""
        search = Mock(return_value=[A])
        question = "如果订单已经签收，退货要我付费吗？"
        self.assertFalse(plan_evidence(question, search).aspects)
        search.assert_called_once_with(question, 3, False)

    def test_explicit_questions_preserve_both_intents(self):
        """支持两个问号、明确连接词及一句中的多个疑问。"""
        self.assertEqual(split_aspects(QUESTION), ["退货谁出运费", "上门取件费用怎么扣"])
        self.assertEqual(split_aspects("能申请平台介入吗，介入后凭证怎么交？"),
                         ["能申请平台介入吗", "介入后凭证怎么交"])

    def test_plan_prioritizes_each_intent_and_preserves_source_scope(self):
        """整问和补查使用同一来源白名单，先给每个诉求预留结果。"""
        index = MilvusPolicies("http://127.0.0.1:19531")
        index.search = Mock(side_effect=[[C, A, B], [A], [B]])
        plan = index.search_evidence("verified-shop", QUESTION, source=("自营", "通用"))
        self.assertEqual([source.article_id for source in plan.sources], ["C", "A", "B"])
        self.assertEqual([aspect.article_ids[0] for aspect in plan.aspects], ["A", "B"])
        self.assertEqual([call.kwargs["limit"] for call in index.search.call_args_list], [5, 2, 2])
        self.assertTrue(all(call.args[0] == "verified-shop" for call in index.search.call_args_list))
        self.assertTrue(all(call.kwargs["source"] == ("自营", "通用") for call in index.search.call_args_list))

    def test_original_top_three_remain_available_to_every_intent(self):
        """整句搜到的必要规则不会因新分组候选被隐藏。"""
        local = [PolicySource(word, word, "适用手册", word) for word in ("D", "E", "F", "G")]
        search = Mock(side_effect=[[A, B, C], local[:2], local[2:]])
        plan = plan_evidence(QUESTION, search)
        self.assertEqual([entry.article_id for entry in plan.sources], ["A", "B", "C", "D", "F"])
        self.assertTrue(all({"A", "B", "C"} <= set(item.article_ids) for item in plan.aspects))
        self.assertEqual(query_aspect("能到付吗"), "能到付吗 货到付款")
        self.assertEqual(query_aspect("能到付吗", context="寄回这单"), "能到付吗 货到付款 退换货 寄回 运费")

    def test_supplement_error_falls_back_to_original_results(self):
        """补查异常时直接使用已查到的结果，避免整条请求失败。"""
        search = Mock(side_effect=[[A, B, C], PolicyIndexError("failed")])
        plan = plan_evidence(QUESTION, search)
        self.assertEqual(plan.sources, [A, B, C])
        self.assertEqual(plan.aspects, [])

    def test_expansion_uses_the_same_budget_and_original_questions(self):
        """补入手册用语后仍沿用原来的多问次数、分组和用户原文。"""
        question = "买的东西拆封还能退吗？店家附送的支架要跟着寄回吗？"
        search = Mock(return_value=[A])
        plan = plan_evidence(question, search, expand_queries=True)
        self.assertEqual([call.args[1] for call in search.call_args_list], [5, 2, 2])
        self.assertEqual(search.call_args_list[0].args[0], question)
        self.assertIn("赠品", search.call_args_list[2].args[0])
        self.assertEqual(plan.aspects[1].question, "店家附送的支架要跟着寄回吗")

    def test_wrong_aspect_citation_only_falls_back_for_that_item(self):
        """条款存在于本次请求但不属于该事项时，保留另一项的有效答复。"""
        aspects = [PolicyAspect("第一项", ["A"]), PolicyAspect("第二项", ["B"])]
        writer = Mock(return_value=[
            {"number": 2, "answer": "错误引用", "citations": ["A"]},
            {"number": 1, "answer": "买家承担", "citations": ["A"]},
        ])
        result = explain_policy(QUESTION, [A, B], None, aspects=aspects, aspect_writer=writer)
        self.assertEqual(result["status"], "answered")
        self.assertEqual([item["status"] for item in result["items"]], ["answered", "needs_review"])
        self.assertEqual([row["article_id"] for row in result["sources"]], ["A"])
        self.assertNotIn("错误引用", result["answer"])

    def test_model_can_mark_every_intent_as_unsupported(self):
        """所有事项缺少依据时，返回空出处和逐项人工核对状态。"""
        aspects = [PolicyAspect("第一项", ["A"]), PolicyAspect("第二项", [])]
        writer = Mock(return_value=[{"number": i, "answer": "", "citations": []} for i in (1, 2)])
        result = explain_policy(QUESTION, [A], None, aspects=aspects, aspect_writer=writer)
        self.assertEqual(result["status"], "no_matches")
        self.assertEqual(result["sources"], [])
        self.assertTrue(all(item["status"] == "needs_review" for item in result["items"]))

    def test_reference_without_an_answer_requires_review(self):
        """只有条款编号时不把它当作实际结论。"""
        aspects = [PolicyAspect("第一项", ["A"]), PolicyAspect("第二项", [])]
        writer = Mock(return_value=[
            {"number": 1, "answer": "[A]", "citations": ["A"]},
            {"number": 2, "answer": "", "citations": []},
        ])
        result = explain_policy(QUESTION, [A], None, aspects=aspects, aspect_writer=writer)
        self.assertEqual(result["status"], "evidence_only")
        self.assertEqual([item["citations"] for item in result["items"]], [[], []])
