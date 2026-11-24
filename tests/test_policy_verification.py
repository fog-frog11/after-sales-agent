"""覆盖错误适用、伪造摘录、跨条款摘录和复核超时。"""

import unittest
from unittest.mock import Mock, patch

from app.integrations.deepseek import ModelError
from app.policies.evidence import PolicyAspect, PolicySource
from app.policies.search import explain_policy
from app.policies.verification import deepseek_policy_reviewer


REFUND = PolicySource("REFUND", "退款给谁", "店铺条款", "退款发起时按原支付方式退回。\n另有约定的，从其约定。")
GIFT = PolicySource("GIFT", "赠品退回", "店铺条款", "退货时赠品应随原商品一并寄回。")


class PolicyVerificationTests(unittest.TestCase):
    """验证新增复核步骤保留有依据的事项。"""

    def test_mixed_answer_with_real_but_inapplicable_reference(self):
        """真实引用不代表适用，逐项复核可保留正确退款规则。"""
        first = "还没退款，可以换成购物券吗？"
        second = "退款到账后能转给别人吗？"
        aspects = [PolicyAspect(part, ["REFUND"]) for part in (first, second)]
        writer = Mock(return_value=[
            {"number": 1, "answer": "按原支付方式退款，另有约定的从约定。", "citations": ["REFUND"]},
            {"number": 2, "answer": "不能转给别人。", "citations": ["REFUND"]},
        ])
        decisions = {"items": [
            {"number": 1, "decision": "supported", "evidence": [
                {"article_id": "REFUND", "quote": "退款发起时按原支付方式退回。 另有约定的，从其约定。"},
            ]},
        ]}
        with patch("app.policies.verification.request_policy_json", return_value=decisions) as request:
            result = explain_policy(first + second, [REFUND], None, aspects=aspects, aspect_writer=writer,
                                    reviewer=deepseek_policy_reviewer("test"))
        self.assertEqual([item["status"] for item in result["items"]], ["answered", "needs_review"])
        self.assertEqual(result["items"][1]["citations"], [])
        self.assertNotIn("不能转给别人", result["answer"])
        request.assert_called_once()
        self.assertEqual([claim["number"] for claim in request.call_args.args[2]["claims"]], [1])

    def test_reviewer_rejects_quote_for_an_unrelated_action(self):
        """客服草稿引用真实原文，复核判定条款未覆盖办理动作。"""
        decisions = {"items": [{"number": 1, "decision": "needs_review", "evidence": []}]}
        with patch("app.policies.verification.request_policy_json", return_value=decisions):
            result = explain_policy("店铺能替我恢复购物账号吗？", [REFUND], lambda *_: "可以 [REFUND]",
                                    reviewer=deepseek_policy_reviewer("test"))
        self.assertEqual(result["status"], "no_matches")

    def test_cannot_accept_fabricated_or_misattributed_quote(self):
        """程序校验摘录属于拟答复真正引用的条款。"""
        for article, quote in [("REFUND", "退款到账后不得转账。"),
                               ("REFUND", GIFT.text), ("GIFT", GIFT.text)]:
            with self.subTest(article=article, quote=quote):
                decisions = {"items": [{"number": 1, "decision": "supported",
                                        "evidence": [{"article_id": article, "quote": quote}]}]}
                with patch("app.policies.verification.request_policy_json", return_value=decisions):
                    result = explain_policy("能转吗？", [REFUND, GIFT], lambda *_: "不能转账 [REFUND]",
                                            reviewer=deepseek_policy_reviewer("test"))
                self.assertEqual((result["status"], result["sources"]), ("no_matches", []))

    def test_timeout_withholds_draft_and_empty_draft_skips_review(self):
        """复核失败时降级，生成阶段已经弃答时节省模型请求。"""
        reviewer = Mock(side_effect=ModelError("timeout"))
        first = explain_policy("问退款", [REFUND], lambda *_: "按原支付方式 [REFUND]", reviewer=reviewer)
        second = explain_policy("问无关操作", [REFUND], lambda *_: None, reviewer=reviewer)
        self.assertEqual((first["status"], second["status"]), ("no_matches", "no_matches"))
        reviewer.assert_called_once()

    def test_received_refund_transfer_cannot_use_original_refund_clause(self):
        """即使模型认可引文，发起退款的条款也不支持到账后转出。"""
        with patch("app.policies.verification.request_policy_json") as request:
            result = explain_policy("退款到了，能把这笔钱自动转进家人的公交卡吗？", [REFUND],
                                    lambda *_: "不能转 [REFUND]", reviewer=deepseek_policy_reviewer("test"))
        self.assertEqual(result["status"], "no_matches")
        request.assert_not_called()

    def test_explicit_post_refund_term_can_still_be_reviewed(self):
        """条款原文明示到账后的转账时，仍按实际条款交给复核器。"""
        term = PolicySource("TRANSFER", "到账后转账", "钱包规则", "退款到账后，账户余额可转给指定亲属。")
        evidence = {"items": [{"number": 1, "decision": "supported",
                               "evidence": [{"article_id": term.article_id, "quote": term.text}]}]}
        with patch("app.policies.verification.request_policy_json", return_value=evidence) as request:
            result = explain_policy("退款到账后，能转给亲属吗？", [term], lambda *_: "可以转给指定亲属 [TRANSFER]",
                                    reviewer=deepseek_policy_reviewer("test"))
        self.assertEqual(result["status"], "answered")
        request.assert_called_once()
