"""验证买家订单隔离、客服独立查询、分页和真实明细字段。"""

import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import Mock

from fastapi.testclient import TestClient

from app.access import DemoAccess
from app.api import create_app
from app.orders.logistics import LogisticsOrder, classify_logistics
from app.orders.olist_items import OrderItem


FIRST = LogisticsOrder("0000000000" + "0" * 22, "shipped", date(2018, 9, 1), date(2018, 10, 9), None)
OTHER = LogisticsOrder("0000000001" + "0" * 22, "canceled", date(2018, 9, 2), None, None)
ITEM = OrderItem(1, "a" * 32, "casa", Decimal("19.90"), Decimal("3.13"))
BUYER_PATH = "/api/buyer/orders"
STAFF_PATH = "/api/staff/orders"


class OrderCatalogTests(unittest.TestCase):
    """从 HTTP 检查页面与权限，避免靠前端隐藏跨角色数据。"""

    def setUp(self):
        """装配独立应用、演示订单和两个 Olist 归属。"""
        self.credentials = {name: f"catalog-test-secret-for-{name}" for name in ("buyer-a", "buyer-b", "staff")}
        self.olist = {entry.order_id: entry for entry in (FIRST, OTHER)}
        self.logistics = Mock(side_effect=self.olist.get)
        self.items = Mock(return_value=[ITEM])
        self.client = self.enterContext(TestClient(create_app(
            access=DemoAccess(self.credentials), logistics_lookup=self.logistics,
            olist_list=self.list_olist, items_lookup=self.items,
        )))

    def list_olist(self, suffixes, after, limit, prefix="", receipt="all"):
        """模拟仓储在分页前应用归属、编号和游标条件。"""
        statuses = {
            "signed": {"logistics_delivered", "logistics_delivered_late"},
            "not_signed": {"logistics_shipping", "logistics_overdue"},
            "cancelled": {"logistics_cancelled"},
        }
        return [row for key, row in sorted(self.olist.items())
                if key[-1] in suffixes and key > after and key.startswith(prefix)
                and (receipt == "all" or classify_logistics(row) in statuses[receipt])][:limit]

    def login(self, account_id):
        """使用指定角色发放新的 Web 会话。"""
        result = self.client.post("/api/session/login", json={
            "account_id": account_id, "secret": self.credentials[account_id],
        })
        self.assertEqual(result.status_code, 200)

    def test_buyer_uses_session_scope_for_list_and_detail(self):
        """伪造筛选归属和直接访问别人的单号都不能看到资料。"""
        self.assertEqual(self.client.get(BUYER_PATH).status_code, 401)
        self.login("buyer-a")
        orders = self.client.get(BUYER_PATH + "?buyer_id=buyer-b").json()["orders"]
        self.assertEqual([entry["order_id"] for entry in orders], ["A101", "A102", FIRST.order_id])
        self.assertTrue(all(entry["buyer_id"] == "buyer-a" for entry in orders))
        self.assertEqual(self.client.get(BUYER_PATH + "/A104").status_code, 404)
        self.assertEqual(self.client.get(BUYER_PATH + "/" + OTHER.order_id).status_code, 404)
        self.logistics.assert_not_called()
        self.items.assert_not_called()
        own = self.client.get(BUYER_PATH + "/a102").json()
        self.assertEqual(own["policy"]["return_window_days"], 7)
        self.assertIsNone(own["items"])
        self.assertEqual(self.client.get(STAFF_PATH).status_code, 403)

    def test_pagination_keeps_order_ownership_and_source_boundary(self):
        """跨店铺与 Olist 数据源翻页，订单不会重复或漏读。"""
        self.login("buyer-a")
        first = self.client.get(BUYER_PATH + "?limit=2").json()
        self.assertEqual(first["next_after"], "A102")
        following = self.client.get(BUYER_PATH + "?limit=2&after=A102").json()
        self.assertEqual([row["order_id"] for row in following["orders"]], [FIRST.order_id])
        self.assertIsNone(following["next_after"])
        olist_only = self.client.get(BUYER_PATH + "?kind=olist").json()
        self.assertEqual(olist_only["orders"], following["orders"])
        self.assertEqual(self.client.get(BUYER_PATH + "?limit=51").status_code, 400)
        self.assertEqual(self.client.get(BUYER_PATH + "?after=wrong").status_code, 400)

    def test_staff_search_and_details_work_without_ticket_store(self):
        """客服按账户和准确编号查单，商品金额走已有 Decimal 计算。"""
        self.login("staff")
        first = self.client.get(STAFF_PATH + "?buyer_id=buyer-b").json()
        self.assertEqual([entry["order_id"] for entry in first["orders"]], ["A103", "A104", OTHER.order_id])
        exact = self.client.get(STAFF_PATH + "?buyer_id=buyer-a&order_id=a102").json()
        self.assertEqual([entry["order_id"] for entry in exact["orders"]], ["A102"])
        self.assertEqual(self.client.get(STAFF_PATH + "?buyer_id=buyer-b&order_id=A102").json()["orders"], [])
        order = self.client.get(STAFF_PATH + "/" + FIRST.order_id).json()
        self.assertEqual(order["buyer_id"], "buyer-a")
        self.assertEqual(order["logistics"]["purchased_on"], "2018-09-01")
        self.assertEqual(order["items"]["recorded_total"], "23.03")
        self.assertEqual(order["items"]["items"][0]["category"], "casa")
        self.assertEqual(self.client.get(BUYER_PATH).status_code, 403)

    def test_buyer_order_search_respects_ownership_and_source(self):
        """完整编号和编号前缀都只匹配本人订单，并可叠加来源。"""
        self.login("buyer-a")
        exact = self.client.get(BUYER_PATH + "?order_id=a102").json()
        self.assertEqual([row["order_id"] for row in exact["orders"]], ["A102"])
        prefix = self.client.get(BUYER_PATH + "?order_id=a10&kind=shop").json()
        self.assertEqual([row["order_id"] for row in prefix["orders"]], ["A101", "A102"])
        for query in ("order_id=A104&buyer_id=buyer-b", "order_id=a10&kind=olist", f"order_id={OTHER.order_id}"):
            with self.subTest(query=query):
                self.assertEqual(self.client.get(BUYER_PATH + "?" + query).json(), {"orders": [], "next_after": None})
        self.assertEqual(self.client.get(BUYER_PATH + "?order_id=OL-%25").status_code, 400)

    def test_buyer_olist_prefix_filters_before_pagination(self):
        """先搜索完整订单范围，再给出匹配结果的翻页游标。"""
        self.login("buyer-a")
        extra = [LogisticsOrder(key + "0" * 22, "shipped", date(2018, 9, 3), None, None)
                 for key in ("0000000020", "0000000022")]
        self.olist.update({entry.order_id: entry for entry in extra})
        query = BUYER_PATH + "?order_id=ol-000000002&limit=1"
        first = self.client.get(query).json()
        self.assertEqual([row["order_id"] for row in first["orders"]], [extra[0].order_id])
        self.assertEqual(first["next_after"], extra[0].order_id)
        following = self.client.get(query + "&after=" + first["next_after"]).json()
        self.assertEqual([row["order_id"] for row in following["orders"]], [extra[1].order_id])
        self.assertIsNone(following["next_after"])

    def test_receipt_filter_combines_both_sources_before_pagination(self):
        """已签收包含延迟签收，未签收包含运输和超期，取消单可独立查。"""
        self.login("buyer-a")
        signed = LogisticsOrder("0000000002" + "0" * 22, "delivered", date(2018, 9, 2),
                                date(2018, 9, 9), date(2018, 9, 10))
        cancelled = LogisticsOrder("0000000004" + "0" * 22, "canceled", date(2018, 9, 2), None, None)
        self.olist.update({order.order_id: order for order in (signed, cancelled)})
        first = self.client.get(BUYER_PATH, params={"receipt": "signed", "limit": 2}).json()
        self.assertEqual([row["order_id"] for row in first["orders"]], ["A101", "A102"])
        next_page = self.client.get(BUYER_PATH, params={"receipt": "signed", "after": first["next_after"]}).json()
        self.assertEqual([row["order_id"] for row in next_page["orders"]], [signed.order_id])
        for receipt, order in (("not_signed", FIRST), ("cancelled", cancelled)):
            with self.subTest(receipt=receipt):
                response = self.client.get(BUYER_PATH, params={"receipt": receipt, "order_id": "OL-000"}).json()
                self.assertEqual([row["order_id"] for row in response["orders"]], [order.order_id])
        self.assertEqual(self.client.get(BUYER_PATH, params={"receipt": "shipped"}).status_code, 400)

    def test_role_pages_and_direct_urls_are_separate(self):
        """两个页面有各自导航，详情 URL 刷新后仍能进入对应角色页面。"""
        self.assertEqual(self.client.get("/orders", follow_redirects=False).headers["location"], "/login")
        self.login("buyer-a")
        buyer = self.client.get("/orders/A102")
        self.assertIn("我的订单", buyer.text)
        self.assertIn('name="order_id"', buyer.text)
        self.assertIn("签收状态", buyer.text)
        self.assertNotIn('name="kind"', buyer.text)
        self.assertIn("售后进度", buyer.text)
        self.assertIn("就这笔订单咨询售后", buyer.text)
        conversation = self.client.get("/").text
        self.assertIn('id="after-sales-link"', conversation)
        self.assertIn('src="/order-items.js"', conversation)
        self.assertNotIn('id="logistics-examples"', conversation)
        self.assertNotIn('id="progress-form"', conversation)
        self.assertEqual(self.client.get("/staff/orders/A102", follow_redirects=False).headers["location"], "/orders")
        self.login("staff")
        staff = self.client.get("/staff/orders/A102")
        self.assertIn("买家账号", staff.text)
        self.assertIn("查看这笔订单的工单", staff.text)
        self.assertEqual(self.client.get("/orders/A102", follow_redirects=False).headers["location"], "/staff")
        self.assertEqual(self.client.get("/order-catalog.js").status_code, 200)
