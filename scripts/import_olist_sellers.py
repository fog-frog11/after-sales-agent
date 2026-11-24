"""从已有 Olist 商品明细补充历史订单与匿名卖家的关系。"""

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path
from typing import TextIO

from app.settings import local_setting
from app.storage.postgres_olist import PostgresOlistOrders
from app.storage.postgres_orders import OrderStoreError
from scripts.import_olist_items import DATA_DIR, checked_reader


SELLER_ID = re.compile(r"[0-9a-f]{32}")


def prepare_sellers(stream: TextIO, sample_ids: set[str]) -> dict[str, set[str]]:
    """只汇总已导入订单中的卖家，保留多卖家关系。"""
    reader = checked_reader(stream, {"order_id", "seller_id"}, "商品明细 CSV")
    groups = defaultdict(set)
    for row in reader:
        order_id = row["order_id"]
        if order_id not in sample_ids:
            continue
        seller = row["seller_id"] or ""
        if None in row or not SELLER_ID.fullmatch(seller):
            raise ValueError(f"商品明细 CSV 第 {reader.line_num} 行的匿名卖家编号无效。")
        groups[order_id].add(seller)
    return dict(groups)


def main() -> None:
    """默认先预览有效关联，收到 --apply 时才补充数据库。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--items", type=Path, default=DATA_DIR / "olist_order_items_dataset.csv")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    database_url = local_setting("ORDER_DATABASE_URL")
    if not database_url:
        parser.error("请先配置 ORDER_DATABASE_URL 并导入 Olist 订单样本。")
    repository = PostgresOlistOrders(database_url)
    try:
        sample_ids = repository.source_order_ids()
        with args.items.open(encoding="utf-8-sig", newline="") as source:
            groups = prepare_sellers(source, sample_ids)
        single = sum(len(sellers) == 1 for sellers in groups.values())
        print(f"样本 {len(sample_ids)} 笔，单卖家 {single} 笔，多卖家 {len(groups) - single} 笔，"
              f"缺少关联 {len(sample_ids) - len(groups)} 笔。")
        if args.apply:
            print(f"更新 {repository.import_sellers(groups)} 笔订单的卖家关系。")
    except (OrderStoreError, OSError, ValueError, csv.Error) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
