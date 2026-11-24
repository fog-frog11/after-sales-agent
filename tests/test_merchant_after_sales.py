"""验证店铺对话隔离与买家、店铺、平台三方售后流程。"""

import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from io import StringIO
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.access import DemoAccess
from app.api import create_app
from app.orders.logistics import LogisticsOrder
from app.orders.olist_items import OrderItem
from scripts.import_olist_sellers import prepare_sellers


CHAT = "/api/conversation"
SHOP = "/api/buyer/merchant-requests/A102"
SELLER = "a" * 32
OTHER_SELLER = "b" * 32
FIRST = LogisticsOrder("000E906B78" + "0" * 22, "shipped", date(2018, 9, 1), date(2018, 10, 9), None, (SELLER,))
SECOND = LogisticsOrder("00010242FE" + "0" * 22, "delivered", date(2018, 8, 1), None, date(2018, 8, 9), (OTHER_SELLER,))
THIRD = LogisticsOrder("0000000002" + "0" * 22, "shipped", date(2018, 9, 1), None, None, (SELLER,))
MULTIPLE = LogisticsOrder("0000000004" + "0" * 22, "shipped", date(2018, 9, 1), None, None, (SELLER, OTHER_SELLER))


class MerchantAfterSalesTests(unittest.TestCase):
    """用接口回归截图中的流程以及三种角色的访问边界。"""

    def setUp(self):
        """提供两笔历史订单、两家店铺与平台客服。"""
        self.credentials = {name: f"merchant-test-credential-{name}"
                            for name in ("buyer-a", "buyer-b", "shop-qh", "shop-qm", "staff")}
        self.merchants = Mock()
        self.tickets = Mock()
        self.tickets.get.return_value = None
        self.tickets.find_order_ticket.return_value = None
        olist = {entry.order_id: entry for entry in (FIRST, SECOND, THIRD, MULTIPLE)}
        item = OrderItem(1, "c" * 32, "utilidades_domesticas", Decimal("21.90"), Decimal("12.69"))
        self.client = self.enterContext(TestClient(create_app(
            access=DemoAccess(self.credentials), logistics_lookup=olist.get, items_lookup=lambda _order: [item],
            merchant_requests=self.merchants, tickets=self.tickets,
        )))
        self.login("buyer-a")

    def login(self, account_id):
        """以本场景的角色登录并切换会话 Cookie。"""
        response = self.client.post("/api/session/login", json={
            "account_id": account_id, "secret": self.credentials[account_id],
        })
        self.assertEqual(response.status_code, 200)

    def open(self, order_id, active_id=None):
        """执行订单详情入口并返回当前范围的历史。"""
        response = self.client.post(CHAT + "/open-order", json={"order_id": order_id, "conversation_id": active_id})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def send(self, question, active_id=None):
        """模拟买家在当前对话发送一条消息。"""
        response = self.client.post(CHAT, json={"question": question, "conversation_id": active_id})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_order_navigation_cannot_answer_previous_prompt(self):
        """从详情进入新订单时忽略此前尚待补号的发票问题。"""
        pending = self.send("能改公司发票抬头吗？")
        selected = self.open(FIRST.order_id, pending["conversation_id"])
        self.assertEqual(selected["result"]["order_id"], FIRST.order_id)
        self.assertEqual(selected["turns"], [])
        self.assertNotEqual(selected["conversation_id"], pending["conversation_id"])
        old = self.client.get(CHAT + "/" + pending["conversation_id"]).json()
        self.assertTrue(old["awaiting_order_id"])
        answer = self.send("什么时候送到？", selected["conversation_id"])
        self.assertEqual(answer["turns"][0]["question"], "什么时候送到？")
        reopened = self.open(FIRST.order_id, selected["conversation_id"])
        self.assertEqual(reopened["turns"], answer["turns"])

    def test_same_shop_keeps_history_and_other_scope_starts_fresh(self):
        """同店换单后继续旧历史，跨范围新问句不继承旧店上下文。"""
        opened = self.open("A102")
        previous = self.send("多久能退？", opened["conversation_id"])
        same = self.open("A101")
        self.assertEqual(same["conversation_id"], opened["conversation_id"])
        self.assertEqual(same["turns"], previous["turns"])
        next_turn = self.send("还能退吗？", same["conversation_id"])
        self.assertEqual(next_turn["result"]["order_id"], "A101")
        other = self.open(FIRST.order_id, same["conversation_id"])
        self.assertEqual(other["turns"], [])
        switch = self.send(f"{SECOND.order_id} 查物流", other["conversation_id"])
        self.assertNotEqual(switch["conversation_id"], other["conversation_id"])
        self.assertEqual(len(switch["turns"]), 1)
        self.assertEqual(self.open("A102", switch["conversation_id"])["turns"], next_turn["turns"])
        self.assertEqual(self.client.post(CHAT + "/open-order", json={"order_id": "A104"}).status_code, 404)
        self.login("buyer-b")
        self.assertEqual(self.client.get(CHAT + "/" + same["conversation_id"]).status_code, 404)

    def test_two_stores_and_two_historical_orders_with_the_same_seller(self):
        """同一个买家跨真实卖家关系切换时，只有同卖家订单继承历史。"""
        with patch.dict("app.access.ORDER_OWNERS", {"A104": "buyer-a"}):
            first_shop = self.open("A102")
            self.send("只能退货吗？", first_shop["conversation_id"])
            other_shop = self.open("A104", first_shop["conversation_id"])
            self.assertEqual(other_shop["turns"], [])
        first = self.open(FIRST.order_id)
        history = self.send("什么时候送到？", first["conversation_id"])["turns"]
        same = self.open(THIRD.order_id)
        self.assertEqual(same["conversation_id"], first["conversation_id"])
        self.assertEqual(same["turns"], history)
        self.assertEqual(same["result"]["order_id"], THIRD.order_id)
        self.assertEqual(self.open(SECOND.order_id, first["conversation_id"])["turns"], [])
        multiple = self.open(MULTIPLE.order_id, first["conversation_id"])
        self.assertEqual(multiple["scope"], "order:" + MULTIPLE.order_id)
        self.assertEqual(multiple["turns"], [])

    def test_seller_import_matches_existing_orders_and_keeps_both_sellers(self):
        """导入时去重同卖家行，保留多卖家且忽略其他订单。"""
        source = StringIO(f"order_id,seller_id\nsample,{SELLER}\nsample,{SELLER}\n"
                          f"sample,{OTHER_SELLER}\nother,{OTHER_SELLER}\n")
        self.assertEqual(prepare_sellers(source, {"sample"}), {"sample": {SELLER, OTHER_SELLER}})

    def test_shop_requests_are_role_scoped_and_enable_platform_ticket(self):
        """店铺答复由本店写入，平台只能在买家完成店铺申请后介入。"""
        preview_path = "/api/tickets/preview"
        platform_reason = {"order_id": "A102", "reason": "仍需要平台复核"}
        submitted = {"order_id": "A102", "reason": "外箱破损", "submitted_at": datetime.now(UTC),
                     "resolution": None, "resolved_at": None}
        answered = {**submitted, "resolution": "请上传包装与商品照片。", "resolved_at": datetime.now(UTC)}
        self.merchants.get.return_value = None
        self.assertEqual(self.client.post(preview_path, json=platform_reason).status_code, 409)
        self.assertEqual(self.client.post(SHOP, json={"reason": " "}).status_code, 400)
        self.assertEqual(self.client.get("/api/buyer/merchant-requests/A104").status_code, 404)
        self.merchants.submit.return_value = submitted
        self.assertEqual(self.client.post(SHOP, json={"reason": "外箱破损"}).status_code, 200)
        self.merchants.submit.assert_called_once_with("demo-standard", "A102", "外箱破损")

        self.login("shop-qm")
        queue = "/api/merchant/requests"
        resolve = queue + "/A102/resolve"
        self.merchants.list.return_value = []
        self.assertEqual(self.client.get(queue).json()["requests"], [])
        self.merchants.list.assert_called_with("demo-extended", resolved=False, after="", limit=21)
        self.merchants.resolve.return_value = None
        form = {"resolution": answered["resolution"]}
        self.assertEqual(self.client.post(resolve, json=form).status_code, 404)
        self.merchants.resolve.assert_called_with("demo-extended", "A102", form["resolution"], "shop-qm")

        self.login("shop-qh")
        self.assertEqual(self.client.get("/merchant").status_code, 200)
        self.assertEqual(self.client.get(SHOP).status_code, 403)
        self.merchants.resolve.return_value = answered
        self.assertEqual(self.client.post(resolve, json=form).json()["resolution"], form["resolution"])
        self.merchants.resolve.assert_called_with("demo-standard", "A102", form["resolution"], "shop-qh")

        self.login("buyer-a")
        self.merchants.get.return_value = answered
        self.assertEqual(self.client.get(SHOP).json()["resolution"], form["resolution"])
        preview = self.client.post(preview_path, json=platform_reason)
        self.assertEqual(preview.status_code, 200, preview.text)
        confirmation = {"confirmation_id": preview.json()["confirmation_id"]}
        ticket = {**platform_reason, "ticket_no": 34, "status": "pending"}
        self.tickets.create.return_value = ticket
        self.assertEqual(self.client.post("/api/tickets/confirm", json=confirmation).json()["ticket_no"], 34)

        self.login("staff")
        self.tickets.get_by_number.return_value = ticket
        detail = self.client.get("/api/staff/tickets/34")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["merchant_request"]["resolution"], form["resolution"])

    def test_buyer_order_progress_combines_records_and_checks_ownership(self):
        """订单号直接返回本人的店铺答复和平台结论，忽略客服审计等内部字段。"""
        now = datetime.now(UTC)
        progress = "/api/buyer/orders/A102/after-sales"
        self.merchants.get.return_value = None
        self.assertEqual(self.client.get(progress).json(), {"merchant": None, "platform": None})
        self.merchants.get.return_value = {"order_id": "A102", "reason": "外箱破损", "submitted_at": now,
                                           "resolution": "请补充图片", "resolved_at": now}
        self.tickets.find_order_ticket.return_value = {
            "ticket_no": 2**63 - 1, "status": "resolved", "reason": "请平台复核", "created_at": now,
            "resolution": "建议办理换货", "resolved_at": now, "handoff": {"internal": True}, "resolved_by": "staff",
        }
        data = self.client.get(progress).json()
        self.assertEqual(data["merchant"]["resolution"], "请补充图片")
        self.assertEqual(data["platform"]["resolution"], "建议办理换货")
        self.assertEqual(data["platform"]["ticket_no"], str(2**63 - 1))
        self.assertNotIn("handoff", data["platform"])
        self.assertNotIn("resolved_by", data["platform"])
        self.tickets.find_order_ticket.reset_mock()
        self.merchants.get.reset_mock()
        self.login("buyer-b")
        self.assertEqual(self.client.get(progress).status_code, 404)
        self.tickets.find_order_ticket.assert_not_called()
        self.merchants.get.assert_not_called()

    def test_order_card_prepares_without_sending_and_binds_on_confirmation(self):
        """显式发送后才保存卡片，下一问使用卡片里那笔订单。"""
        prepare = CHAT + "/prepare-order"
        share = CHAT + "/share-order"
        request = {"order_id": FIRST.order_id}
        draft = self.client.post(prepare, json=request).json()
        self.assertEqual(draft["turns"], [])
        self.assertEqual(draft["draft_order"]["items_summary"], "1 件家居日用品")
        self.assertFalse(draft["can_continue"])
        self.assertEqual(self.client.get(CHAT + "/" + draft["conversation_id"]).status_code, 404)
        result = self.client.post(share, json={**request, "conversation_id": draft["conversation_id"]}).json()
        self.assertTrue(result["can_continue"])
        self.assertEqual(result["turns"][0]["order_card"], draft["draft_order"])
        self.assertEqual(self.client.post(share, json=request).json()["turns"], result["turns"])
        answer = self.send("这单买的什么？", result["conversation_id"])
        self.assertIn("1 件家居日用品", answer["result"]["olist_items"]["answer"])
        self.assertEqual(self.client.get(CHAT + "/" + result["conversation_id"]).json()["turns"], answer["turns"])
        self.assertNotIn("draft_order", self.client.post(prepare, json=request).json())
        self.login("buyer-b")
        self.assertEqual(self.client.post(share, json=request).status_code, 404)

    def test_preparing_another_order_at_same_shop_does_not_replace_active_order(self):
        """同一店铺保留历史，待发送卡片只在明确发送后替换当前订单。"""
        previous = self.client.post(CHAT + "/share-order", json={"order_id": "A102"}).json()
        thread = previous["conversation_id"]
        draft = self.client.post(CHAT + "/prepare-order", json={"order_id": "A101", "conversation_id": thread}).json()
        self.assertEqual(draft["turns"], previous["turns"])
        self.assertEqual(draft["draft_order"]["order_id"], "A101")
        self.assertEqual(self.send("还能退吗？", thread)["result"]["order_id"], "A102")
        current = self.client.post(CHAT + "/share-order", json={"order_id": "A101", "conversation_id": thread}).json()
        self.assertEqual(current["turns"][-1]["order_card"]["order_id"], "A101")
        stale = self.client.post(CHAT, json={"question": "还能退吗？", "conversation_id": thread, "active_order_id": "A102"})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.send("发一下订单详情", thread)["result"]["order_detail_requested"], True)

    def test_shop_can_read_only_orders_linked_to_its_requests(self):
        """店铺人员可以点开申请所附订单，其他店铺得到 404。"""
        path = "/api/merchant/requests/A102/order"
        self.login("shop-qh")
        self.merchants.get.return_value = None
        self.assertEqual(self.client.get(path).status_code, 404)
        self.merchants.get.return_value = {"order_id": "A102"}
        detail = self.client.get(path).json()
        self.assertEqual(detail["order_id"], "A102")
        self.assertEqual(detail["policy"]["shop_name"], "青禾生活馆")
        self.login("shop-qm")
        self.assertEqual(self.client.get(path).status_code, 404)

    def test_shop_chat_handoff_uses_merchant_request_gate_and_current_order(self):
        """有店铺渠道的订单在聊天中先申请店铺售后，再把该单对话交给平台。"""
        self.merchants.get.return_value = None
        chat = self.client.post(CHAT + "/share-order", json={"order_id": "A102"}).json()
        conversation_id = chat["conversation_id"]
        self.assertEqual(self.send("帮我转人工", conversation_id)["result"]["human_review"]["status"], "merchant_first")
        payload = {"order_id": "A102", "reason": "请平台核对包装问题", "conversation_id": conversation_id}
        self.assertEqual(self.client.post("/api/tickets/preview", json=payload).status_code, 409)
        self.merchants.get.return_value = {"order_id": "A102"}
        review = self.send("找人工客服", conversation_id)["result"]["human_review"]
        self.assertEqual(review["status"], "preview")
        preview = self.client.post("/api/tickets/preview", json=payload).json()
        self.assertIn("签收日期 2026-11-05。", preview["handoff"]["verified_facts"])
        mismatch = self.client.post("/api/tickets/preview", json={**payload, "order_id": "A101"})
        self.assertEqual(mismatch.status_code, 409)

    def test_conversation_list_is_scoped_and_each_saved_thread_opens_independently(self):
        """买家切换历史时打开指定对话，跨账号查询和未发送订单预览不进列表。"""
        pending = self.send("上一条售后问题")
        shared = self.client.post(CHAT + "/share-order", json={"order_id": FIRST.order_id}).json()
        self.client.post(CHAT + "/prepare-order", json={"order_id": SECOND.order_id})
        listing = "/api/buyer/conversations"
        page = self.client.get(listing, params={"limit": 1}).json()
        self.assertEqual(page["conversations"][0]["conversation_id"], shared["conversation_id"])
        self.assertEqual(page["conversations"][0]["order_id"], FIRST.order_id)
        second = self.client.get(listing, params={"limit": 1, "before": page["next_before"]}).json()
        self.assertEqual(second["conversations"][0]["conversation_id"], pending["conversation_id"])
        self.assertTrue(second["conversations"][0]["awaiting_order_id"])
        self.assertIsNone(second["next_before"])
        self.assertTrue(self.client.get(CHAT + "/" + pending["conversation_id"]).json()["awaiting_order_id"])
        self.assertIn('id="conversation-list"', self.client.get("/conversations").text)
        self.assertIn('href="/conversations"', self.client.get("/orders").text)
        self.assertEqual(self.client.get(listing, params={"before": "invalid"}).status_code, 400)
        self.login("buyer-b")
        self.assertEqual(self.client.get(listing, params={"buyer_id": "buyer-a"}).json()["conversations"], [])
        self.assertEqual(self.client.get(CHAT + "/" + shared["conversation_id"]).status_code, 404)
