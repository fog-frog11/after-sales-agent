"""导入京东 M&SOM 脱敏订单、匿名 SKU 与历史配送记录。"""

import argparse
import csv
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
from pathlib import Path

from app.orders.logistics import LogisticsOrder
from app.settings import ROOT, local_setting
from app.storage.postgres_jd import PostgresJDOrders
from app.storage.postgres_orders import OrderStoreError
from scripts.import_olist_items import checked_reader


DATA_DIR = ROOT / ".local/datasets/jd-msom-2020"
ORDER_FIELDS = {"order_ID", "sku_ID", "order_date", "order_time", "type", "promise", "quantity", "final_unit_price"}
DELIVERY_FIELDS = {"order_ID", "package_ID", "type", "arr_time"}
ORDER_CHANNEL = {"1": "self", "2": "market"}
DELIVERY_CHANNEL = {"1": "self", "0": "market"}


def unit_price(raw: str) -> Decimal:
    """把成交记录按人民币两位小数展示，保留源文件的负值。"""
    amount = Decimal(raw)
    if not amount.is_finite() or abs(amount) > Decimal("9999999999.99"):
        raise ValueError("京东订单金额超出可存储范围。")
    return amount.quantize(Decimal(".01"), rounding=ROUND_HALF_UP)


def promised_date(raw: str, placed: datetime) -> date | None:
    """从研究说明的时效档计算到日，缺承诺时保持空白。"""
    if raw in ("", "-"):
        return None
    days = int(raw)
    if days > 1:
        return placed.date() + timedelta(days=days)
    if days == 1 and placed.hour < 23:
        return placed.date() + timedelta(days=int(placed.hour >= 11))
    return None


def delivery_dates(path: Path) -> tuple[dict, date]:
    """按订单和经营渠道记录最晚已送达的包裹日期。"""
    delivered = defaultdict(dict)
    snapshot = date.min
    packages = set()
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in checked_reader(stream, DELIVERY_FIELDS, "配送 CSV"):
            key = row["order_ID"], row["package_ID"]
            if not all(key) or key in packages:
                raise ValueError("配送 CSV 包裹编号缺失或重复。")
            packages.add(key)
            arrival = datetime.fromisoformat(row["arr_time"]).date()
            channel = DELIVERY_CHANNEL[row["type"]]
            order = delivered[row["order_ID"]]
            order[channel] = max(order.get(channel, date.min), arrival)
            snapshot = max(snapshot, arrival)
    if not delivered:
        raise ValueError("配送 CSV 没有可读取的记录。")
    return delivered, snapshot


def prepare_dataset(directory: Path) -> tuple[list[LogisticsOrder], list[tuple], int]:
    """核对匿名 SKU，合并订单明细与可匹配的配送记录。"""
    with (directory / "JD_sku_data.csv").open(encoding="utf-8-sig", newline="") as stream:
        skus = {row["sku_ID"] for row in checked_reader(stream, {"sku_ID"}, "SKU CSV")}
    deliveries, snapshot = delivery_dates(directory / "JD_delivery_data.csv")
    groups = defaultdict(list)
    missing_skus = 0
    with (directory / "JD_order_data.csv").open(encoding="utf-8-sig", newline="") as stream:
        for row in checked_reader(stream, ORDER_FIELDS, "订单 CSV"):
            order_id, sku = row["order_ID"], row["sku_ID"]
            placed = datetime.fromisoformat(row["order_time"])
            quantity = int(row["quantity"])
            if not order_id or not sku or quantity <= 0 or placed.date().isoformat() != row["order_date"]:
                raise ValueError("京东订单的编号、数量或下单日期无效。")
            missing_skus += sku not in skus
            groups[order_id].append((placed.date(), promised_date(row["promise"], placed), ORDER_CHANNEL[row["type"]],
                                     sku, quantity, unit_price(row["final_unit_price"])))
    orders = []
    items = []
    for source_id, group in groups.items():
        days = {row[0] for row in group}
        if len(days) != 1:
            raise ValueError("同一订单包含多个下单日期。")
        placed = next(iter(days))
        promised = [row[1] for row in group]
        estimated = max(promised) if all(promised) else None
        channels = {row[2] for row in group}
        arrival = deliveries.get(source_id, {})
        # 第三方配送可能未被收录。混合订单只在两侧均有记录时报告送达。
        received = max(arrival[channel] for channel in channels) if channels <= arrival.keys() else None
        if received and received < placed:
            received = None
        source_key = sha256(f"jd-msom-2020:{source_id}".encode()).hexdigest()
        order = LogisticsOrder(source_key, "delivered" if received else "unknown", placed, estimated, received,
                               origin="jd", snapshot_on=snapshot,
                               channel=next(iter(channels)) if len(channels) == 1 else "mixed")
        orders.append(order)
        items.extend((order.order_id, no, sku, quantity, price, channel)
                     for no, (_, _, channel, sku, quantity, price) in enumerate(group, 1))
    if not orders or len({order.order_id for order in orders}) != len(orders):
        raise ValueError("国内订单查询编号重复或没有可导入的数据。")
    return orders, items, missing_skus


def main() -> None:
    """默认预览，收到 --apply 后在现有本机 PostgreSQL 内写入。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        orders, items, unknown_sku = prepare_dataset(args.data_dir)
        print(f"订单 {len(orders):,} 笔，商品明细 {len(items):,} 行，有完整匹配配送记录 {sum(order.delivered_on is not None for order in orders):,} 笔。", flush=True)
        print(f"渠道 {dict(Counter(order.channel for order in orders))}，SKU 档案未匹配行 {unknown_sku:,}。", flush=True)
        if args.apply:
            url = local_setting("ORDER_DATABASE_URL")
            if not url:
                raise ValueError("请先配置本机 ORDER_DATABASE_URL。")
            written_orders, written_items = PostgresJDOrders(url).import_records(orders, items)
            print(f"新增 {written_orders:,} 笔订单和 {written_items:,} 行商品明细。")
    except (OrderStoreError, OSError, ValueError, csv.Error, KeyError, ArithmeticError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
