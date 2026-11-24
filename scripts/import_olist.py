"""预览或导入 Olist 官方订单 CSV 的物流样本。"""

import argparse
import csv
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TextIO

from app.orders.logistics import OLIST_AS_OF, LogisticsOrder, classify_logistics
from app.settings import ROOT, local_setting
from app.storage.postgres_olist import PostgresOlistOrders
from app.storage.postgres_orders import OrderStoreError


DEFAULT_CSV = ROOT / ".local" / "datasets" / "olist" / "olist_orders_dataset.csv"
REQUIRED_COLUMNS = {
    "order_id", "order_status", "order_purchase_timestamp",
    "order_estimated_delivery_date", "order_delivered_customer_date",
}
SOURCE_STATUSES = {"approved", "canceled", "created", "delivered", "invoiced", "processing", "shipped", "unavailable"}
SCENARIOS = {
    "logistics_delivered": "按期签收",
    "logistics_delivered_late": "延迟签收",
    "logistics_overdue": "已发货且超期未签收",
    "logistics_cancelled": "已取消",
}


@dataclass(frozen=True)
class SamplePreview:
    """记录分组后的样本以及读取和排除的源记录数。"""

    source_rows: int
    excluded_rows: int
    groups: dict[str, list[LogisticsOrder]]


def parse_order(row: dict[str, str], line: int) -> LogisticsOrder:
    """校验订单 ID、状态和日期，不读取客户信息。"""
    def order_date(column: str) -> date | None:
        """把源时间戳转换为当地日历日期，保留缺失值。"""
        value = row[column].strip()
        return datetime.fromisoformat(value).date() if value else None

    try:
        source_id = row["order_id"].strip().lower()
        status = row["order_status"].strip()
        purchased = order_date("order_purchase_timestamp")
        if not re.fullmatch(r"[a-f0-9]{32}", source_id) or status not in SOURCE_STATUSES or purchased is None:
            raise ValueError
        return LogisticsOrder(source_id, status, purchased,
                              order_date("order_estimated_delivery_date"), order_date("order_delivered_customer_date"))
    except (AttributeError, ValueError):
        raise ValueError(f"第 {line} 行的订单编号、状态或日期无效。") from None


def read_orders(stream: TextIO) -> list[LogisticsOrder]:
    """校验官方订单文件并保留源记录。"""
    reader = csv.DictReader(stream, strict=True)
    if not REQUIRED_COLUMNS.issubset(reader.fieldnames or []):
        raise ValueError("CSV 缺少 Olist 订单必填列。")
    seen: set[str] = set()
    orders = []
    for row in reader:
        order = parse_order(row, reader.line_num)
        if order.source_order_id in seen:
            raise ValueError(f"第 {reader.line_num} 行的源订单号重复。")
        seen.add(order.source_order_id)
        orders.append(order)
    return orders


def valid_logistics_dates(order: LogisticsOrder) -> bool:
    """保留日期完整并符合历史快照时序的订单。"""
    return (order.purchased_on <= OLIST_AS_OF and order.estimated_on is not None
            and order.estimated_on >= order.purchased_on
            and (order.delivered_on is None or order.purchased_on <= order.delivered_on <= OLIST_AS_OF))


def prepare_sample(stream: TextIO, per_scenario: int) -> SamplePreview:
    """按场景和源编号抽取稳定的一批样本。"""
    if not 1 <= per_scenario <= 250:
        raise ValueError("每类样本数应为 1 至 250。")
    orders = read_orders(stream)
    groups: dict[str, list[LogisticsOrder]] = {status: [] for status in SCENARIOS}
    excluded = 0
    for order in orders:
        status = classify_logistics(order)
        if status not in groups or not valid_logistics_dates(order):
            excluded += 1
            continue
        groups[status].append(order)
    selected = {status: sorted(group, key=lambda order: order.source_order_id)[:per_scenario]
                for status, group in groups.items()}
    aliases = [order.order_id for group in selected.values() for order in group]
    if len(aliases) != len(set(aliases)):
        raise ValueError("样本查询编号有冲突，导入已取消。")
    return SamplePreview(len(orders), excluded, selected)


def main() -> None:
    """默认输出预览，收到 --apply 时才写入 PostgreSQL。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="Olist 订单 CSV 路径")
    parser.add_argument("--per-scenario", type=int, default=50, help="每类最多抽取的订单数")
    parser.add_argument("--apply", action="store_true", help="将预览样本写入 PostgreSQL")
    args = parser.parse_args()
    try:
        with args.csv.open(encoding="utf-8-sig", newline="") as stream:
            preview = prepare_sample(stream, args.per_scenario)
    except (OSError, ValueError, csv.Error) as error:
        parser.error(str(error))
    orders = [order for group in preview.groups.values() for order in group]
    print(f"源记录 {preview.source_rows:,} 笔，未纳入目标场景 {preview.excluded_rows:,} 笔，抽取 {len(orders)} 笔。")
    for status, group in preview.groups.items():
        example = group[0].order_id if group else "—"
        print(f"{SCENARIOS[status]} {len(group)} 笔，示例 {example}")
    if not args.apply:
        return
    database_url = local_setting("ORDER_DATABASE_URL")
    if not database_url or not orders:
        parser.error("请先配置 ORDER_DATABASE_URL，并提供有可导入样本的 CSV。")
    try:
        inserted = PostgresOlistOrders(database_url).import_orders(orders)
    except OrderStoreError as error:
        parser.error(str(error))
    print(f"新增 {inserted} 笔，已有 {len(orders) - inserted} 笔。")


if __name__ == "__main__":
    main()
