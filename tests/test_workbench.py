"""验证单轮售后、网页资源、输入校验和接口错误映射。"""

import unittest
from datetime import date

from fastapi.testclient import TestClient

from app.integrations.deepseek import ModelError, query_after_sales
from app.orders.rules import Order
from app.storage.postgres_orders import OrderStoreError
from app.api import create_app


class AgentTests(unittest.TestCase):
    """覆盖确定性售后判断、数据源注入和模型工具验证。"""

    def test_same_order_under_two_policies(self):
        """同一订单按 7 天与 15 天期限查询时给出不同结论。"""
        short = query_after_sales("A102 可以退吗？", 7)
        long = query_after_sales("A102 可以退吗？", 15)
        self.assertEqual(short["result"]["days_since_delivery"], 8)
        self.assertEqual(short["result"]["status"], "outside_window")
        self.assertEqual(long["result"]["status"], "eligible")

    def test_local_order_outcomes(self):
        """覆盖缺编号、未知订单、未签收和正常订单。"""
        for question, expected in (
            ("还能退吗？", "missing_order_id"),
            ("A101 和 A102 呢？", "missing_order_id"),
            ("查 A999", "order_not_found"),
            ("A103 还能退吗？", "not_signed"),
            ("订单 a101", "eligible"),
        ):
            with self.subTest(question=question):
                response = query_after_sales(question, 7)
                self.assertEqual(response["mode"], "demo")
                self.assertEqual(response["result"]["status"], expected)

    def test_model_calls_lookup_and_python_decides(self):
        """模型只提供订单号，最终资格由现有规则计算。"""
        def model(question):
            """为测试问题返回一个合法的查单工具调用。"""
            self.assertEqual(question, "帮我看 A102")
            return {"name": "get_order", "arguments": '{"order_id":"A102"}'}

        response = query_after_sales("帮我看 A102", 7, model)
        self.assertEqual(response["mode"], "deepseek")
        self.assertEqual(response["result"]["status"], "outside_window")

    def test_invalid_model_tools(self):
        """拒绝未知工具、篡改的订单号和无效的工具参数。"""
        for tool in (
            {"name": "issue_refund", "arguments": '{"order_id":"A102"}'},
            {"name": "get_order", "arguments": '{"order_id":"A101"}'},
            {"name": "get_order", "arguments": '{"order_id":"A102","eligible":true}'},
            {"name": "get_order", "arguments": "invalid"},
        ):
            with self.subTest(tool=tool), self.assertRaises(ModelError):
                query_after_sales("查 A102", 7, lambda _: tool)

    def test_order_date_comes_from_supplied_lookup(self):
        """确认计算使用注入的签收日期。"""
        def lookup(order_id):
            """返回签收已过六天的测试订单。"""
            self.assertEqual(order_id, "A102")
            return Order(order_id, date(2026, 11, 7))

        response = query_after_sales("查 A102", 7, order_lookup=lookup)
        self.assertEqual(response["result"]["days_since_delivery"], 6)
        self.assertEqual(response["result"]["status"], "eligible")


class HttpTests(unittest.TestCase):
    """覆盖 FastAPI 页面、请求校验和依赖失败时的 HTTP 行为。"""

    def setUp(self):
        """创建独立的 FastAPI 测试客户端。"""
        self.client = self.enterContext(TestClient(create_app()))

    def test_home_and_mode(self):
        """验证页面资源可访问，并报告内存演示模式。"""
        home = self.client.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertIn("售后工作台", home.text)
        for asset in ("/styles.css", "/app.js"):
            with self.subTest(asset=asset):
                self.assertEqual(self.client.get(asset).status_code, 200)
        config = self.client.get("/api/config").json()
        self.assertEqual(config["mode"], "demo")
        self.assertEqual(config["order_source"], "memory")

    def test_openapi_documents_query_and_error_statuses(self):
        """验证接口文档记录实际返回的单轮查询状态码。"""
        self.assertEqual(self.client.get("/docs").status_code, 200)
        schema = self.client.get("/openapi.json").json()
        query = schema["paths"]["/api/query"]["post"]
        self.assertIn("application/json", query["requestBody"]["content"])
        self.assertEqual(set(query["responses"]), {"200", "400", "502", "503"})

    def test_query_returns_result(self):
        """验证指定期限与默认期限产生预期结果。"""
        response = self.client.post("/api/query", json={"question": "A102", "return_window_days": 15})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"]["status"], "eligible")
        default = self.client.post("/api/query", json={"question": "A102"})
        self.assertEqual(default.json()["result"]["status"], "outside_window")

    def test_api_rejects_invalid_payloads(self):
        """拒绝无效问题、错误类型及不支持的期限。"""
        for payload in (
            [], {"question": 5}, {"question": " "},
            {"question": "A102", "return_window_days": True},
            {"question": "A102", "return_window_days": "7"},
            {"question": "A102", "return_window_days": 7.0},
            {"question": "A102", "return_window_days": 99},
            {"question": "A" * 301},
        ):
            with self.subTest(payload=payload):
                response = self.client.post("/api/query", json=payload)
                self.assertEqual(response.status_code, 400)
                self.assertIn("message", response.json())

    def test_api_rejects_malformed_or_oversized_json(self):
        """验证无效 JSON 和超限请求返回 HTTP 400。"""
        for body in (b"{", b'{"question":"A102"}' + b" " * 8192):
            with self.subTest(length=len(body)):
                response = self.client.post(
                    "/api/query", content=body, headers={"Content-Type": "application/json"},
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("message", response.json())

    def test_database_failure_returns_503(self):
        """模拟订单数据源故障并验证 HTTP 503。"""
        class UnavailableOrders:
            """模拟连接不可用的订单与政策数据源。"""

            def get_order(self, order_id):
                """抛出可向用户显示的数据库故障。"""
                raise OrderStoreError("订单数据库不可用，请检查连接和订单表。")

            def get_policy(self, shop_id):
                """确认查单失败时不进入政策查询。"""
                raise AssertionError("Order lookup should fail first")

        with TestClient(create_app(orders=UnavailableOrders())) as client:
            self.assertEqual(client.get("/api/config").json()["order_source"], "postgresql")
            response = client.post("/api/query", json={"question": "A102"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("订单数据库不可用", response.json()["message"])

    def test_model_failure_returns_502(self):
        """模拟 DeepSeek 故障并验证 HTTP 502。"""
        def unavailable_model(question):
            """用业务异常模拟模型服务暂时不可用。"""
            raise ModelError("DeepSeek 请求失败。")

        with TestClient(create_app(model=unavailable_model)) as client:
            response = client.post("/api/query", json={"question": "A102"})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json(), {"message": "DeepSeek 请求失败。"})
