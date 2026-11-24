"""聊天履约草稿须有本轮原话、当前原单权限及原业务格式校验。"""
import unittest
from datetime import date
from app.mall.chat_actions import prepare_action
from app.mall.dialogue_state import apply_observation,validate_observation,task_signature
from test_task_actions import item

class FulfillmentChatTests(unittest.TestCase):
    def memory(self,action='ship',**values):
        return {'case_action':action,'fulfillment_case':'8','fulfillment':values}
    def case(self,**changes):
        return {'case_no':8,'is_active':True,'actions':['ship','confirm_delivery_address'],'quantity':1,'kind':'exchange',**changes}
    def card(self,memory,case=None):
        return prepare_action(item(),memory,[case or self.case()],date(2026,11,24),requested=True)
    def test_partial_tracking_only_asks_missing_carrier(self):
        self.assertEqual(self.card(self.memory(tracking='SF12345678'))['missing'],['carrier'])
    def test_fields_are_scoped_to_current_case(self):
        memory=self.memory(carrier='顺丰快递',tracking='SF12345678');memory['fulfillment_case']='7'
        self.assertEqual(self.card(memory)['state'],'needs_input')
    def test_validated_tracking_and_destination_payloads(self):
        self.assertEqual(self.card(self.memory(carrier='顺丰快递',tracking='SF12345678'))['payload']['tracking'],'SF12345678')
        card=self.card(self.memory('confirm_delivery_address',recipient='李四',address='杭州市西湖区示例街道十号',phone='13800000000'))
        self.assertEqual(card['payload']['delivery_address']['recipient'],'李四')
        self.assertIsNone(card['payload']['delivery_parcel_no'])
    def test_invalid_phone_and_forbidden_stage_cannot_prepare_ready_card(self):
        self.assertEqual(self.card(self.memory('confirm_delivery_address',recipient='李四',address='杭州市西湖区示例街道十号',phone='abc'))['state'],'needs_input')
        self.assertEqual(self.card(self.memory(),self.case(actions=['evidence']))['state'],'unavailable')
    def test_only_literal_current_turn_fields_are_merged(self):
        observation={'quote':'顺丰快递','fulfillment':{'carrier':{'value':'顺丰快递','quote':'顺丰快递'}}}
        validate_observation(observation)
        memory,_=apply_observation({},observation,'我用顺丰快递寄回',[])
        self.assertEqual(memory['fulfillment']['carrier'],'顺丰快递')
        self.assertNotEqual(task_signature(memory),task_signature({}))
        with self.assertRaises(ValueError):validate_observation({'quote':'顺丰快递','fulfillment':{'carrier':{'value':'中通快递','quote':'顺丰快递'}}})
    def test_tracking_prefix_is_not_a_confirmed_carrier(self):
        quote='单号SF1234567890'
        memory,_=apply_observation({}, {'quote':quote,'fulfillment':{
            'tracking':{'value':'SF1234567890','quote':quote},'carrier':{'value':'SF','quote':quote}}},quote,[])
        self.assertNotIn('carrier',memory['fulfillment'])
        memory,_=apply_observation({}, {'quote':quote,'fulfillment':{'carrier':{'value':'SF','quote':quote}}},quote,[])
        self.assertNotIn('carrier',memory.get('fulfillment',{}))
