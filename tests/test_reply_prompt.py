"""按场景装配不能让无关办理信息污染物流答复，也不能放松事实边界。"""
import unittest
import json
from unittest.mock import patch
from app.mall.reply_prompt import build_reply_prompt
from app.mall.grounded_reply import validate_reply,grounded_writer,grounded_reviewer

class ReplyPromptTests(unittest.TestCase):
    def test_logistics_excludes_finance_and_application_sections(self):
        prompt=build_reply_prompt({'facts':{'F1':{'kind':'original_logistics','data':{}}}})
        self.assertIn('承运商实时轨迹',prompt)
        self.assertNotIn('银行到账',prompt)
        self.assertNotIn('needs_input',prompt)
        self.assertIn('只有成功回执',prompt)

    def test_partial_evidence_and_missing_inputs_are_explicit(self):
        prompt=build_reply_prompt({'facts':{'F1':{'kind':'policy_evidence'},'F2':{'kind':'action_preview'}}})
        self.assertIn('partial不能说全部有依据',prompt)
        self.assertIn('只追问缺失字段',prompt)

    def test_legacy_facts_keep_common_boundaries(self):
        self.assertIn('业务结论只用facts',build_reply_prompt({'facts':{'F1':'商品字段'}}))

    def test_customer_report_does_not_prove_seller_verification(self):
        facts={'F1':{'kind':'eligibility','data':{'requires_seller_verification':True}}}
        with self.assertRaisesRegex(ValueError,'尚待店铺核实'):
            validate_reply(json.dumps({'sentences':[{'text':'少件情况已经核实属实。','facts':['F1']}]},ensure_ascii=False),facts)

    def test_generation_and_review_respect_remaining_budget(self):
        with patch('app.mall.grounded_reply.deepseek_chat',return_value={'content':'{"supported":true,"issues":[]}'}) as call:
            grounded_writer('unused')({'facts':{},'_timeout_s':1.5})
            self.assertEqual(call.call_args.kwargs['timeout'],1.5)
            grounded_reviewer('unused')({'facts':{},'_timeout_s':.7})
            self.assertEqual(call.call_args.kwargs['timeout'],.7)
            grounded_writer('unused')({'facts':{},'_timeout_s':100})
            self.assertEqual(call.call_args.kwargs['timeout'],6)

    def test_multiple_causes_cannot_be_described_as_one_cause(self):
        facts={'F1':{'kind':'customer_task','data':{'issues':[
            {'reason':'quality','state':'active'},{'reason':'missing','state':'active'}]}}}
        with self.assertRaisesRegex(ValueError,'并存问题'):
            validate_reply(json.dumps({'sentences':[{'text':'这两项都属于质量问题。','facts':['F1']}]},ensure_ascii=False),facts)
