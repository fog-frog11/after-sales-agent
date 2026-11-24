"""验证评测会记录单项失败，并正确识别错误的政策出处。"""

import unittest

from scripts.evaluate_workbench import EvaluationCase, EvaluationFailure, check_policy_result, run_cases


class EvaluationTests(unittest.TestCase):
    """覆盖评测执行器和政策评测的失败判定。"""

    def test_failed_case_does_not_stop_later_case(self):
        """某个检查失败后，保留原因并继续执行其他用例。"""
        def broken():
            """模拟业务结果不符合预期。"""
            raise EvaluationFailure("预期 7 天，实际 15 天。")

        outcomes = run_cases([
            EvaluationCase("A", "期限检查", broken),
            EvaluationCase("B", "后续检查", lambda: "成功"),
        ])
        self.assertEqual([item.passed for item in outcomes], [False, True])
        self.assertIn("15 天", outcomes[0].detail)
        self.assertEqual(outcomes[1].detail, "成功")

    def test_policy_case_requires_correct_source_and_citation(self):
        """出处来自错误店铺或答复错引时，评测应该失败。"""
        result = {
            "status": "answered",
            "function_call": "search_shop_policy",
            "mcp_tools": ["get_order", "search_shop_policy"],
            "order": {"order_id": "A102"},
            "answer": "保留外箱照片 [QH-03]。",
            "sources": [{
                "article_id": "QH-03", "source": "青禾手册 §3", "text": "请保留外箱照片。",
            }],
        }
        self.assertIn("QH-03", check_policy_result(result, "A102", "QH-03", "保留外箱"))
        with self.assertRaises(EvaluationFailure):
            check_policy_result({**result, "answer": "保留照片 [QM-03]。"}, "A102", "QH-03", "保留外箱")
        with self.assertRaises(EvaluationFailure):
            check_policy_result({**result, "sources": result["sources"] + [{"article_id": "QM-03"}]},
                                "A102", "QH-03", "保留外箱")
