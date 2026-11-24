"""评测缺少原文的题必须与有可核对原文的题分别统计。"""

import unittest

from scripts.evaluate_policy_pipeline import relevance_groups


class EvaluationLabelTests(unittest.TestCase):
    def test_unsupported_labels_and_before_first_edition_are_not_scored_as_supported(self):
        self.assertEqual(relevance_groups('shop','v1',[[]]),[])
        self.assertEqual(relevance_groups('shop',None,[['freight']]),[])

    def test_multiple_aspects_keep_distinct_groups_within_purchase_scope(self):
        self.assertEqual(relevance_groups('shop','v2',[[],['freight','credit'],['response']]),
                         [{'shop/v2/freight','shop/v2/credit'},{'shop/v2/response'}])
