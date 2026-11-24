"""聊天确认接口的身份、客户端可写字段和编号精度。"""

import unittest
from unittest.mock import Mock
from uuid import uuid4

from fastapi.testclient import TestClient

from app.access import DemoAccess
from app.api import create_app
from app.mall.chat_actions import ChatConfirmation
from app.tickets.service_requests import ServiceError


class ChatActionApiTests(unittest.TestCase):
    def setUp(self):
        self.store=Mock()
        self.store.confirm_chat_action.return_value={'case_no':'10','status':'submitted'}
        self.secrets={name:'unit-secret-'+name for name in ('buyer-001','shop-001','staff')}
        self.client=TestClient(create_app(mall=self.store,access=DemoAccess(self.secrets)))
        self.path=f'/api/buyer/mall/orders/CN-300003/items/1/consult/actions/{uuid4()}/confirm'

    def login(self,name):
        self.client.post('/api/session/login',json={'account_id':name,'secret':self.secrets[name]})

    def test_only_the_signed_in_buyer_can_confirm(self):
        self.assertEqual(self.client.post(self.path,json={'turn_no':'1'}).status_code,401)
        for name in ('shop-001','staff'):
            self.login(name)
            self.assertEqual(self.client.post(self.path,json={'turn_no':'1'}).status_code,403)
        self.store.confirm_chat_action.assert_not_called()
        self.login('buyer-001')
        self.assertEqual(self.client.post(self.path,json={'turn_no':'1'}).status_code,200)
        self.assertEqual(self.store.confirm_chat_action.call_args.args[0],'buyer-001')

    def test_client_cannot_replace_action_amount_or_buyer(self):
        self.login('buyer-001')
        for field,value in (('action','refund'),('quantity',2),('amount','999'),('buyer_id','buyer-002')):
            self.assertEqual(self.client.post(self.path,json={'turn_no':'1',field:value}).status_code,400)
        self.store.confirm_chat_action.assert_not_called()

    def test_stale_conflict_reaches_the_chat_client(self):
        self.login('buyer-001')
        self.store.confirm_chat_action.side_effect=ServiceError(409,'原单方案已更新，请核对最新确认卡。')
        response=self.client.post(self.path,json={'turn_no':'1'})
        self.assertEqual(response.status_code,409)
        self.assertIn('原单方案已更新',response.json()['message'])

    def test_large_turn_number_preserves_exact_value(self):
        self.assertEqual(ChatConfirmation(turn_no='9007199254740993').turn_no,9007199254740993)
        self.login('buyer-001')
        response=self.client.post(self.path,json={'turn_no':'9007199254740993'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.store.confirm_chat_action.call_args.args[-1].turn_no,9007199254740993)
