"""验证 Olist CSV 校验、历史物流规则和买家对话分支。"""

import unittest
from contextlib import redirect_stdout
from datetime import date
from io import StringIO
from unittest.mock import Mock, mock_open, patch

from fastapi.testclient import TestClient

from app.api import create_app
from app.orders.logistics import OLIST_AS_OF, LogisticsOrder, classify_logistics
from scripts.import_olist import main as import_olist, prepare_sample


HEADER = "order_id,customer_id,order_status,order_purchase_timestamp,order_delivered_customer_date,order_estimated_delivery_date\n"
SHIPPED = "01234567890000000000000000000000,unused,shipped,2018-09-01 01:00:00,,2018-10-09 00:00:00\n"
CANCELED = "A1234567890000000000000000000000,unused,canceled,2018-09-01 01:00:00,,2018-10-09 00:00:00\n"
ON_TIME = "B1234567890000000000000000000000,unused,delivered,2018-09-01 01:00:00,2018-10-09 17:00:00,2018-10-09 00:00:00\n"
LATE = "C1234567890000000000000000000000,unused,delivered,2018-09-01 01:00:00,2018-10-10 01:00:00,2018-10-09 00:00:00\n"


class OlistLogisticsTests(unittest.TestCase):
    """保护样本稳定性与原有买家查单入口。"""

    def test_sample_is_stable_and_classifies_four_scenarios(self):
        """打乱 CSV 行序后，分组和编号保持一致。"""
        first = prepare_sample(StringIO(HEADER + SHIPPED + ON_TIME + LATE + CANCELED), 1)
        second = prepare_sample(StringIO(HEADER + CANCELED + LATE + ON_TIME + SHIPPED), 1)
        self.assertEqual(first, second)
        self.assertEqual(first.source_rows, 4)
        self.assertEqual({name: len(rows) for name, rows in first.groups.items()}, {
            "logistics_delivered": 1, "logistics_delivered_late": 1,
            "logistics_overdue": 1, "logistics_cancelled": 1,
        })
        self.assertEqual(first.groups["logistics_overdue"][0].order_id, "OL-0123456789")

    def test_invalid_csv_is_rejected_before_import(self):
        """缺列、非法日期和重复源编号都会拒绝处理。"""
        for body in ("order_id\n", HEADER + SHIPPED.replace("2018-10-09", "2018-99-99"), HEADER + SHIPPED * 2):
            with self.subTest(csv=body):
                with self.assertRaises(ValueError):
                    prepare_sample(StringIO(body), 1)

    def test_cutoff_keeps_same_day_delivery_in_time(self):
        """预计日期当天的发货订单还没有超期。"""
        order = LogisticsOrder("0" * 32, "shipped", date(2018, 10, 1), OLIST_AS_OF, None)
        self.assertEqual(classify_logistics(order), "logistics_shipping")

    def test_default_cli_previews_without_accessing_database(self):
        """预览显示样本编号，整个过程无需数据库连接配置。"""
        output = StringIO()
        with (
            patch("sys.argv", ["import_olist"]),
            patch("pathlib.Path.open", mock_open(read_data=HEADER + SHIPPED)),
            patch("scripts.import_olist.local_setting") as get_setting,
            redirect_stdout(output),
        ):
            import_olist()
        get_setting.assert_not_called()
        self.assertIn("OL-0123456789", output.getvalue())

    def test_conversation_accepts_olist_number_after_followup(self):
        """追问后读取 Olist 仓储并返回超期原因和历史日期。"""
        order = prepare_sample(StringIO(HEADER + SHIPPED), 1).groups["logistics_overdue"][0]
        model = Mock(side_effect=AssertionError("查物流无需调用模型"))
        lookup = Mock(return_value=order)
        with TestClient(create_app(model=model, logistics_lookup=lookup)) as client:
            pending = client.post("/api/conversation", json={"question": "还没收到，帮我查一下"}).json()
            self.assertTrue(pending["awaiting_order_id"])
            payload = {"question": "ol-0123456789", "conversation_id": pending["conversation_id"]}
            response = client.post("/api/conversation", json=payload)
        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertEqual(result["status"], "logistics_overdue")
        self.assertEqual(result["logistics"]["snapshot_on"], "2018-10-17")
        self.assertEqual(result["logistics"]["estimated_on"], "2018-10-09")
        self.assertIsNone(result["logistics"]["delivered_on"])
        model.assert_not_called()
        lookup.assert_called_once_with("OL-0123456789")

    def test_mixed_ids_prompt_for_one_number(self):
        """同一句含两种编号时等待买家确认，避免查错单。"""
        lookup = Mock(side_effect=AssertionError("同时提供两个编号时应先追问"))
        with TestClient(create_app(logistics_lookup=lookup)) as client:
            result = client.post("/api/conversation", json={"question": "A102 和 OL-0123456789"}).json()
        self.assertTrue(result["awaiting_order_id"])
        lookup.assert_not_called()
