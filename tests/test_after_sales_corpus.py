"""验证公开语料的审计和审核示例，确保原回复不会直接进入业务提示。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.mall.agent import deepseek_mall_planner, reviewed_planning_examples
from scripts.prepare_after_sales_corpus import inspect, normalize


class AfterSalesCorpusTests(unittest.TestCase):
    """核对重复问法、风险标记与少样本提示的输入边界。"""

    def test_normalized_duplicates_and_claims_are_reported(self):
        """场景不同的相同问法也必须归为一个分组。"""
        with tempfile.TemporaryDirectory(delete=False) as temporary:
            directory = Path(temporary)
            rows = [
                dict(scene="退货", user="能退吗？", bot="可以先查看申请进度。", tone="亲切"),
                dict(scene="质量", user="能退吗?", bot="马上为您退款。", tone="安抚"),
                dict(scene="差评", user="东西不好", bot="送您5元优惠券。", tone="安抚"),
            ]
            (directory / "data.jsonl").write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), "utf-8")
            result = inspect(directory)
        self.assertEqual(result["valid_rows"], 3)
        self.assertEqual(result["unique_user_messages"], 2)
        self.assertEqual(result["duplicate_user_groups"], [[1, 2]])
        self.assertEqual(result["flag_counts"]["unverified_action_or_promise"], 1)
        self.assertEqual(normalize("能退吗？"), normalize("能退吗?"))

    def test_reviewed_examples_only_contain_utterances_and_plans(self):
        """采用工具规划示例，无原始 bot 话术或业务承诺。"""
        content = reviewed_planning_examples()
        self.assertNotIn('"bot"', content)
        self.assertNotIn("50元", content)
        self.assertIn("fit_exchange", content)
        self.assertIn("platform", content)
        with patch("app.mall.agent.request_function", return_value={}) as request:
            deepseek_mall_planner("unused", use_examples=False)("这个尺码不合适")
            baseline = request.call_args.args[1]["messages"]
            deepseek_mall_planner("unused", use_examples=True)("这个尺码不合适")
            adapted = request.call_args.args[1]["messages"]
        self.assertEqual(len(adapted), len(baseline) + 1)
        self.assertIn("经过审核", adapted[1]["content"])
