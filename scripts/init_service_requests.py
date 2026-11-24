"""初始化商品售后表并可选导入明确标记的运费演练记录。"""

import argparse
from decimal import Decimal

from app.settings import local_setting
from app.storage.postgres_service_requests import PostgresServiceRequests


def main() -> None:
    """在现有本机数据库创建售后表，按需写三条独立演练数据。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--practice", action="store_true")
    args = parser.parse_args()
    url = local_setting("ORDER_DATABASE_URL")
    if not url:
        raise SystemExit("请先配置本机 ORDER_DATABASE_URL。")
    store = PostgresServiceRequests(url)
    store.initialize()
    print("商品售后单和办理时间线已准备。", flush=True)
    if args.practice:
        with store.connect() as connection:
            rows = connection.execute("""
                SELECT order_id FROM public.jd_research_orders WHERE channel = 'self' AND delivered_on IS NOT NULL
                AND right(order_id,1) IN ('0','2','4','6','8','A','C','E') ORDER BY order_id LIMIT 3
            """).fetchall()
            examples = (("quality", "seller", Decimal("12.00"), Decimal("0.00"), True),
                        ("wrong_purchase", "buyer", Decimal("12.00"), Decimal("0.00"), False),
                        ("wrong_purchase", "buyer", Decimal("12.00"), Decimal("8.00"), False))
            for row, example in zip(rows, examples, strict=True):
                connection.execute("""
                    INSERT INTO public.after_sales_freight_practice VALUES (%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (order_id, reason) DO NOTHING
                """, (row["order_id"], *example))
                print(row["order_id"], example[0], "演练费用")


if __name__ == "__main__":
    main()
