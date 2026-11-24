"""使用已下载的 Olist CSV 扩充订单、商品和真实匿名卖家关系。"""

import argparse
import csv
from collections import Counter
from dataclasses import replace
from pathlib import Path

from app.orders.logistics import classify_logistics
from app.settings import local_setting
from app.storage.postgres_olist import PostgresOlistOrders
from app.storage.postgres_orders import OrderStoreError
from scripts.import_olist import read_orders, valid_logistics_dates
from scripts.import_olist_items import DATA_DIR, prepare_items
from scripts.import_olist_sellers import prepare_sellers


def main() -> None:
    """默认预览所有可用记录，收到 --apply 后一次提交。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--apply", action="store_true", help="写入现有本机订单库")
    args = parser.parse_args()
    try:
        directory = args.data_dir
        with (directory / "olist_orders_dataset.csv").open(encoding="utf-8-sig", newline="") as stream:
            source = read_orders(stream)
        orders = [order for order in source if valid_logistics_dates(order) and classify_logistics(order) != "logistics_unknown"]
        if not orders or len(orders) != len({order.order_id for order in orders}):
            raise ValueError("没有可导入订单，或生成的工作台查询编号冲突。")
        ids = {order.source_order_id for order in orders}
        items_file = directory / "olist_order_items_dataset.csv"
        with (items_file.open(encoding="utf-8-sig", newline="") as items,
              (directory / "olist_products_dataset.csv").open(encoding="utf-8-sig", newline="") as products):
            rows = prepare_items(items, products, ids)
        with items_file.open(encoding="utf-8-sig", newline="") as items:
            sellers = prepare_sellers(items, ids)
        orders = [replace(order, seller_ids=tuple(sorted(sellers.get(order.source_order_id, ())))) for order in orders]
        seller_count = len({seller for order in orders for seller in order.seller_ids})
        print(f"源订单 {len(source):,} 笔，可导入 {len(orders):,} 笔，排除 {len(source) - len(orders):,} 笔。", flush=True)
        print(f"匹配商品明细 {len(rows):,} 行，匿名卖家 {seller_count:,} 个，"
              f"有卖家关联的订单 {len(sellers):,} 笔。", flush=True)
        print(dict(Counter(map(classify_logistics, orders))), flush=True)
        if args.apply:
            url = local_setting("ORDER_DATABASE_URL")
            if not url:
                raise ValueError("请先配置本机 ORDER_DATABASE_URL。")
            changed, items = PostgresOlistOrders(url).import_dataset(orders, rows)
            print(f"新增或更新卖家关系 {changed:,} 笔订单，新增商品明细 {items:,} 行。")
    except (OrderStoreError, OSError, ValueError, csv.Error) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
