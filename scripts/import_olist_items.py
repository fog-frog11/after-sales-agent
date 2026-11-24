"""从 Olist 官方 CSV 导入现有历史订单的商品明细。"""

import argparse
import csv
import re
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TextIO

from app.orders.olist_items import CENT, OrderItem
from app.settings import ROOT, local_setting
from app.storage.postgres_olist import PostgresOlistOrders
from app.storage.postgres_orders import OrderStoreError


DATA_DIR = ROOT / ".local" / "datasets" / "olist"
ITEM_FIELDS = {"order_id", "order_item_id", "product_id", "price", "freight_value"}
PRODUCT_FIELDS = {"product_id", "product_category_name"}
PRODUCT_ID = re.compile(r"[a-f0-9]{32}")


def checked_reader(stream: TextIO, fields: set[str], label: str) -> csv.DictReader:
    """检查 CSV 表头字段齐全，避免重复列覆盖数据。"""
    reader = csv.DictReader(stream, strict=True)
    names = reader.fieldnames or []
    if not fields.issubset(names) or len(names) != len(set(names)):
        raise ValueError(f"{label}缺少必填列或包含重复列。")
    return reader


def parse_money(text: str) -> Decimal:
    """拒绝无效金额，保留可写入数据库的两位十进制小数。"""
    value = Decimal(text.strip())
    if not value.is_finite() or not 0 <= value <= Decimal("9999999999.99") or value != value.quantize(CENT):
        raise ValueError("金额必须为有效的非负数，最多两位小数。")
    return value.quantize(CENT)


def parse_item(row: dict[str, str], line: int) -> OrderItem:
    """从匹配的商品行读取序号、商品编号、价格和运费。"""
    try:
        item_no = int(row["order_item_id"])
        product_id = row["product_id"].strip()
        if None in row or not 1 <= item_no <= 2_147_483_647 or not PRODUCT_ID.fullmatch(product_id):
            raise ValueError
        return OrderItem(item_no, product_id, None, parse_money(row["price"]), parse_money(row["freight_value"]))
    except (AttributeError, TypeError, ValueError, InvalidOperation):
        raise ValueError(f"商品明细 CSV 第 {line} 行的序号、商品编号或金额无效。") from None


def read_categories(stream: TextIO, product_ids: set[str]) -> dict[str, str | None]:
    """只为样本用到的商品读取品类，允许源记录缺失品类。"""
    reader = checked_reader(stream, PRODUCT_FIELDS, "商品 CSV")
    categories = {}
    for row in reader:
        product_id = row["product_id"]
        if product_id not in product_ids:
            continue
        category = row["product_category_name"]
        if None in row or category is None or product_id in categories:
            raise ValueError(f"商品 CSV 第 {reader.line_num} 行无效或重复。")
        categories[product_id] = category.strip() or None
    return categories


def prepare_items(items: TextIO, products: TextIO, order_ids: set[str]) -> list[tuple[str, OrderItem]]:
    """按既有样本订单匹配明细，再用商品编号补上源品类。"""
    reader = checked_reader(items, ITEM_FIELDS, "商品明细 CSV")
    matched: dict[tuple[str, int], OrderItem] = {}
    for row in reader:
        source_order_id = row["order_id"]
        if source_order_id in order_ids:
            item = parse_item(row, reader.line_num)
            key = source_order_id, item.item_no
            if key in matched:
                raise ValueError(f"商品明细 CSV 第 {reader.line_num} 行的订单与明细序号重复。")
            matched[key] = item
    categories = read_categories(products, {item.product_id for item in matched.values()})
    return [(source_order_id, replace(item, category=categories.get(item.product_id)))
            for (source_order_id, _), item in sorted(matched.items())]


def main() -> None:
    """默认只读预览匹配范围，收到 --apply 后提交数据库写入。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--items", type=Path, default=DATA_DIR / "olist_order_items_dataset.csv")
    parser.add_argument("--products", type=Path, default=DATA_DIR / "olist_products_dataset.csv")
    parser.add_argument("--apply", action="store_true", help="将匹配明细写入 PostgreSQL")
    args = parser.parse_args()
    database_url = local_setting("ORDER_DATABASE_URL")
    if not database_url:
        parser.error("请先配置 ORDER_DATABASE_URL 并导入 Olist 物流样本。")
    repository = PostgresOlistOrders(database_url)
    try:
        order_ids = repository.source_order_ids()
        with (args.items.open(encoding="utf-8-sig", newline="") as items,
              args.products.open(encoding="utf-8-sig", newline="") as products):
            rows = prepare_items(items, products, order_ids)
        covered = len({source_id for source_id, _ in rows})
        print(f"订单样本 {len(order_ids)} 笔，匹配明细的订单 {covered} 笔，共 {len(rows)} 件，"
              f"缺少商品明细 {len(order_ids) - covered} 笔。")
        if args.apply:
            if not rows:
                parser.error("两份 CSV 中没有与现有订单匹配的商品明细。")
            inserted = repository.import_items(rows)
            print(f"新增 {inserted} 件，已有 {len(rows) - inserted} 件。")
    except (OrderStoreError, OSError, ValueError, csv.Error) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
