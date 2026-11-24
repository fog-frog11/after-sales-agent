"""验证商品样本联接、金额计算和买家查询范围。"""

import unittest
from decimal import Decimal
from io import StringIO
from unittest.mock import Mock

from fastapi.testclient import TestClient

from app.access import DemoAccess
from app.api import create_app
from app.orders.dialogue_tools import select_order_tools
from app.orders.olist_items import OrderItem, answer_items
from scripts.import_olist_items import prepare_items


SOURCE = "0123456788" + "0" * 22
PRODUCT = "a" * 32
OTHER_SOURCE = "1" * 32
ALIAS = "OL-0123456788"
ITEM_HEADER = "order_id,order_item_id,product_id,price,freight_value\n"
PRODUCT_HEADER = "product_id,product_category_name\n"
ENDPOINT = "/api/olist/orders/" + ALIAS + "/items"


class OlistItemsTests(unittest.TestCase):
    """保护选定订单的件数、金额和访问范围。"""

    def test_colloquial_item_question_answers_with_category_and_count(self):
        """截图中的口语提问以及相似说法可以直接命中商品明细。"""
        item = OrderItem(1, PRODUCT, "utilidades_domesticas", Decimal("21.90"), Decimal("12.69"))
        router = Mock(return_value={"name": "select_order_tools", "arguments": '{"operations":["logistics"]}'})
        for question in ("这单买的什么？", "这笔订单买的是啥？", "商品叫什么？"):
            with self.subTest(question=question):
                self.assertIn("items", select_order_tools(question, None)["operations"])
                self.assertIn("items", select_order_tools(question, router)["operations"])
                result = answer_items(ALIAS, question, [item])
                self.assertEqual(result["answer"], "这单有 1 件家居日用品。")
                self.assertEqual(result["items"][0]["category"], "utilidades_domesticas")
                self.assertEqual(result["items"][0]["category_label"], "家居日用品")

    def test_import_keeps_repeated_products_as_separate_items(self):
        """同一商品的两条明细都计数，其他订单的商品行被跳过。"""
        stream = StringIO(ITEM_HEADER + f"{SOURCE},1,{PRODUCT},0.10,0.20\n"
                          + f"{SOURCE},2,{PRODUCT},0.20,0.30\n" + f"{OTHER_SOURCE},1,{PRODUCT},4.00,5.00\n")
        products = StringIO(PRODUCT_HEADER + f"{PRODUCT},informatica_acessorios\n")
        rows = prepare_items(stream, products, {SOURCE})
        items = [item for _, item in rows]
        self.assertEqual(len(items), 2)
        self.assertEqual([item.category for item in items], ["informatica_acessorios"] * 2)
        answer = answer_items(ALIAS, "买了几件？运费多少？", items)
        self.assertEqual(answer["quantity"], 2)
        self.assertEqual(answer["items_total"], Decimal("0.30"))
        self.assertEqual(answer["freight_total"], Decimal("0.50"))
        self.assertIn("2 件", answer["answer"])
        self.assertIn("0.50", answer["answer"])

    def test_bad_prices_or_duplicate_lines_rejected(self):
        """匹配的源记录不能使用非有限、负数或超过两位小数的金额。"""
        for price in ("NaN", "-1", "0.001"):
            with self.subTest(price=price):
                items = StringIO(ITEM_HEADER + f"{SOURCE},1,{PRODUCT},{price},0\n")
                with self.assertRaises(ValueError):
                    prepare_items(items, StringIO(PRODUCT_HEADER), {SOURCE})
        items = StringIO(ITEM_HEADER + f"{SOURCE},1,{PRODUCT},1.00,0.00\n" * 2)
        with self.assertRaises(ValueError):
            prepare_items(items, StringIO(PRODUCT_HEADER), {SOURCE})

    def test_owned_order_only_and_missing_detail(self):
        """验证先核对归属，再区分有明细、缺明细和订单不存在。"""
        credentials = {name: f"simulation-secret-for-{name}" for name in ("buyer-a", "buyer-b", "staff")}
        item = OrderItem(1, PRODUCT, "informatica_acessorios", Decimal("5.10"), Decimal("1.20"))
        lookup = Mock(return_value=[item])
        app = create_app(items_lookup=lookup, access=DemoAccess(credentials))
        with TestClient(app) as client:
            self.assertEqual(client.post(ENDPOINT, json={"question": "买了什么？"}).status_code, 401)
            client.post("/api/session/login", json={"account_id": "buyer-a", "secret": credentials["buyer-a"]})
            result = client.post(ENDPOINT, json={"question": "买了什么？"}).json()
            self.assertEqual(result["currency"], "BRL")
            self.assertEqual(result["items_total"], "5.10")
            self.assertEqual(result["items"][0]["category"], "informatica_acessorios")
            lookup.return_value = []
            result = client.post(ENDPOINT, json={"question": "买了什么？"}).json()
            self.assertEqual(result["status"], "no_items")
            self.assertIsNone(result["items_total"])
            lookup.return_value = None
            self.assertEqual(client.post(ENDPOINT, json={"question": "买了什么？"}).status_code, 404)
            lookup.reset_mock()
            client.post("/api/session/login", json={"account_id": "buyer-b", "secret": credentials["buyer-b"]})
            self.assertEqual(client.post(ENDPOINT, json={"question": "买了什么？"}).status_code, 404)
            client.post("/api/session/login", json={"account_id": "staff", "secret": credentials["staff"]})
            self.assertEqual(client.post(ENDPOINT, json={"question": "买了什么？"}).status_code, 403)
            lookup.assert_not_called()
