"""一个问点有依据不能掩盖另一问点无依据；基础权限不能被补充条款覆盖。"""
import unittest
from unittest.mock import patch
from app.mall.dialogue import checked_mall_citations
from app.policies.evidence import PolicySource

class CitationCoverageTests(unittest.TestCase):
    def test_model_rewritten_question_list_keeps_two_independent_aspects(self):
        article=PolicySource('one','规则','基础','规则原文')
        assessment=[]
        with patch('app.mall.dialogue.order_policy_sources',return_value=('基础',[article])):
            checked_mall_citations({},'会员是否免邮、寄回运费由谁承担',
                rank=lambda q,articles:('运费问题',article) if '谁承担' in q else ('会员',None),assessment=assessment)
        self.assertEqual([a['supported'] for a in assessment],[False,True])

    def test_mixed_question_records_each_final_decision(self):
        article=PolicySource('one','安装说明','补充','螺丝可单独补发')
        assessment=[]
        with patch('app.mall.dialogue.order_policy_sources',return_value=('基础',[article])):
            _,evidence,_=checked_mall_citations({},'螺丝能补吗？会员终身包邮吗？',
                rank=lambda q,articles:('配件',article) if '螺丝' in q else ('会员权益',None),assessment=assessment)
        self.assertEqual([r['supported'] for r in assessment],[True,False])
        self.assertEqual([r['id'] for r in evidence],['one'])

    def test_supplement_cannot_grant_base_financial_right(self):
        article=PolicySource('one','补充','补充','寄回运费全部由商家承担')
        assessment=[]
        with patch('app.mall.dialogue.order_policy_sources',return_value=('基础',[article])):
            _,evidence,_=checked_mall_citations({},'寄回运费谁承担',rank=lambda *a:('寄回运费',article),assessment=assessment)
        self.assertFalse(assessment[0]['supported'])
        self.assertEqual(evidence,[])
