"""验证 Olist 对话的多意图、订单上下文和人工复核边界。"""

import json
import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import Mock

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from app.access import DemoAccess
from app.api import create_app
from app.orders.dialogue_tools import select_order_tools
from app.orders.logistics import LogisticsOrder
from app.orders.olist_items import OrderItem


ORDER = LogisticsOrder("002f19a65a" + "0" * 22, "shipped", date(2018, 9, 1), date(2018, 10, 9), None)
ITEM = OrderItem(1, "a" * 32, "casa", Decimal("49.90"), Decimal("13.37"))
PATH = "/api/conversation"


class BuyerOrderDialogueTests(unittest.TestCase):
    """保护统一对话的路由和账号隔离。"""

    def test_route_must_pass_whitelist(self):
        """模型带来额外参数或不允许的操作时，用本地规则处理。"""
        for arguments in ({"operations": ["items"], "order_id": "A104"}, {"operations": ["write_ticket"]}):
            router = Mock(return_value={"name": "select_order_tools", "arguments": json.dumps(arguments)})
            self.assertEqual(select_order_tools("运费多少？", router), {"source": "rules", "operations": ["items"]})

    def test_multi_intent_followup_review_and_account_isolation(self):
        """从补编号走到金额与复核，复用账号检查点。"""
        credentials = {name: f"simulation-secret-for-{name}" for name in ("buyer-a", "buyer-b", "staff")}
        access = DemoAccess(credentials)
        checkpointer = InMemorySaver()
        tickets = Mock()
        tickets.find_olist_ticket.return_value = {"ticket_no": 11, "order_id": ORDER.order_id, "status": "resolved"}
        store = Mock(return_value=[ITEM])
        router = Mock(side_effect=[{"name": "select_order_tools", "arguments": json.dumps({"operations": operations})}
                                  for operations in (["logistics", "items"], ["items"], ["human_review"])])
        app = create_app(access=access, order_checkpointer=checkpointer, logistics_lookup=lambda _: ORDER,
                         items_lookup=store, olist_router=router, tickets=tickets)
        with TestClient(app) as buyer, TestClient(app) as other:
            for client, account in ((buyer, "buyer-a"), (other, "buyer-b")):
                self.assertEqual(client.post("/api/session/login", json={
                    "account_id": account, "secret": credentials[account],
                }).status_code, 200)
            pending = buyer.post(PATH, json={"question": "怎么还没收到？我买了几件？"}).json()
            self.assertTrue(pending["awaiting_order_id"])
            cid = pending["conversation_id"]
            self.assertEqual(len(pending["turns"]), 1)
            self.assertEqual(buyer.get(f"{PATH}/{cid}").json()["turns"], pending["turns"])
            first = buyer.post(PATH, json={"question": ORDER.order_id, "conversation_id": cid}).json()
            self.assertTrue(first["can_continue"])
            self.assertEqual(first["result"]["status"], "logistics_overdue")
            self.assertEqual(first["result"]["olist_items"]["quantity"], 1)
            self.assertEqual(first["result"]["tool_plan"], {"source": "function_call", "operations": ["logistics", "items"]})
            self.assertEqual(first["turns"][:1], pending["turns"])
            self.assertEqual(first["turns"][1], {"question": ORDER.order_id, "result": first["result"]})
            self.assertEqual(buyer.post(PATH, json={"question": ORDER.order_id, "conversation_id": cid}).json()["turns"], first["turns"])
            self.assertNotIn(ORDER.order_id, router.call_args.args[0])
            self.assertEqual(other.get(f"{PATH}/{cid}").status_code, 404)
            self.assertEqual(other.post(PATH, json={"question": "运费多少？", "conversation_id": cid}).status_code, 409)
            followup = buyer.post(PATH, json={"question": "运费多少？", "conversation_id": cid}).json()
            self.assertEqual(followup["result"]["olist_items"]["freight_total"], "13.37")
            self.assertEqual(followup["result"]["order_id"], ORDER.order_id)
            self.assertEqual(buyer.get(f"{PATH}/{cid}").json()["result"], followup["result"])
            handoff = buyer.post(PATH, json={"question": "帮我转人工", "conversation_id": cid}).json()
            self.assertEqual(handoff["result"]["human_review"]["ticket_no"], "11")
            self.assertEqual([turn["question"] for turn in handoff["turns"]], [
                "怎么还没收到？我买了几件？", ORDER.order_id, "运费多少？", "帮我转人工",
            ])
            self.assertEqual(handoff["turns"][2]["result"]["olist_items"]["freight_total"], "13.37")
            self.assertEqual(buyer.get(f"{PATH}/{cid}").json()["turns"], handoff["turns"])
            tickets.create.assert_not_called()
            self.assertEqual(store.call_count, 2)

    def test_new_olist_context_is_cleared_if_explicit_order_is_not_owned(self):
        """请求另一账号明确编号时重新核对，不回退泄露上一笔订单。"""
        credentials = {name: f"simulation-secret-for-{name}" for name in ("buyer-a", "buyer-b", "staff")}
        lookup = Mock(return_value=ORDER)
        with TestClient(create_app(logistics_lookup=lookup, access=DemoAccess(credentials))) as client:
            client.post("/api/session/login", json={"account_id": "buyer-a", "secret": credentials["buyer-a"]})
            initial = client.post(PATH, json={"question": ORDER.order_id}).json()
            second = client.post(PATH, json={"question": "OL-00310B0C75 买了几件？",
                                               "conversation_id": initial["conversation_id"]}).json()
        self.assertEqual(second["result"]["status"], "order_not_found")
        self.assertFalse(second["can_continue"])
        lookup.assert_called_once_with(ORDER.order_id)
