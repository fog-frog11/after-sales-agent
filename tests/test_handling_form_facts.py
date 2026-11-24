"""寄回与新收货资料属于不同待办，不能催错角色或冒充聊天已提交。"""
import unittest
from datetime import date
from app.mall.tool_facts import build_facts
from test_mall_agent import shipped_product

class HandlingFormFactsTests(unittest.TestCase):
    def form(self,topic,**changes):
        case={'case_no':1,'status':'awaiting_return','status_label':'等待寄回','is_active':True,**changes}
        facts=build_facts(shipped_product(),{'case_no':1},{},[topic],[case],date(2026,11,24))
        return next(row['data'] for row in facts.values() if row['kind']=='handling_form')
    def test_missing_shop_address_does_not_request_tracking(self):
        form=self.form('return',return_details_pending=True)
        self.assertEqual(form['waiting_for'],'shop_return_address')
        self.assertEqual(form['required_fields'],[])
    def test_return_tracking_is_not_outbound_destination(self):
        self.assertEqual(self.form('return',actions=['ship'])['required_fields'],['carrier','tracking'])
        form=self.form('outbound',outbound_pending=True)
        self.assertEqual(form['required_fields'],['recipient','address','phone'])
        self.assertTrue(form['chat_submission_supported'])
    def test_submitted_case_must_not_request_premature_tracking(self):
        form=self.form('return',status='submitted',actions=['evidence'])
        self.assertFalse(form['submit_allowed'])
        self.assertEqual(form['required_fields'],[])
    def test_ready_outbound_is_not_an_unconfirmed_address(self):
        facts=build_facts(shipped_product(),{'case_no':1},{},['outbound'],[{
            'case_no':1,'status':'received','status_label':'待店铺发货','outbound_pending':True,
            'outbound':{'parcel_no':'2','stage':'ready'}}],date(2026,11,24))
        parcel=next(row['data'] for row in facts.values() if row['kind']=='outbound_parcel')
        self.assertFalse(parcel['awaiting_address_confirmation'])
        self.assertFalse(any(row['kind']=='handling_form' for row in facts.values()))
