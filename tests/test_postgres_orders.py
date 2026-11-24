"""检查 PostgreSQL 订单数据源对连接错误的处理。"""

import unittest
from unittest.mock import patch

import psycopg

from app.storage.postgres_orders import OrderStoreError, PostgresOrders


class PostgresOrdersTests(unittest.TestCase):
    """验证订单仓储出错时向上层返回的内容。"""

    def test_connection_error_does_not_expose_connection_details(self):
        """连接异常应返回通用错误，不暴露底层连接细节。"""
        with patch("psycopg.connect", side_effect=psycopg.OperationalError("private connection details")):
            with self.assertRaises(OrderStoreError) as raised:
                PostgresOrders("postgresql://example").get_order("A102")
        self.assertNotIn("private", str(raised.exception))
