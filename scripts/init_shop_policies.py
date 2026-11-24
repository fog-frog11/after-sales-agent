"""为现有商城发布平台规则和首批独立店铺条款，仅追加缺少的版本。"""

from datetime import timedelta
from decimal import Decimal

from app.settings import ROOT, local_setting
from app.storage.mall_policies import CATEGORY_NOTES, ShopPublication, publish
from app.storage.postgres_mall import PostgresMall


def install(conn) -> int:
    """按品类与店铺经营档案起草承诺，通过和商家页面相同的发布校验。"""
    conn.execute((ROOT / "sql/add_mall_policy_editions.sql").read_text(encoding="utf-8"))
    floor = conn.execute("SELECT * FROM public.mall_platform_policies ORDER BY version DESC LIMIT 1").fetchone()
    today = conn.execute("SELECT (now() AT TIME ZONE 'Asia/Shanghai')::date AS d").fetchone()["d"]
    shops = conn.execute("""SELECT s.*,p.category FROM public.mall_shops s JOIN public.mall_shop_profiles p USING(shop_id)
        WHERE s.shop_id ~ '^market-[0-9]{3}$' ORDER BY s.shop_id FOR UPDATE OF s""").fetchall()
    count = 0
    for shop in shops:
        if conn.execute("SELECT 1 FROM public.mall_shop_policy_editions WHERE shop_id=%s LIMIT 1", (shop["shop_id"],)).fetchone():
            continue
        old = conn.execute("SELECT * FROM public.mall_terms WHERE shop_id=%s ORDER BY version DESC LIMIT 1",
                           (shop["shop_id"],)).fetchone()
        profile, category = divmod(int(shop["shop_id"].split("-")[1]) - 1, 10)
        fee = min(old["return_postage"], Decimal(8 + category % 4))
        form = ShopPublication(base_version=old["version"], effective_on=max(today, old["effective_on"] + timedelta(days=1)),
            return_days=max(old["return_days"], (7, 10, 15, 21, 30)[profile % 5]),
            fault_days=(30, 45, 60)[category % 3], warranty_days=max(old["warranty_days"], (60, 90, 180)[category % 3]),
            reply_hours=(12, 18, 24, 30, 36)[profile % 5], return_postage=fee,
            item_credit=min(fee, Decimal((profile + category) % 7)), seller_advances=old["seller_advances"],
            guidance_key="category" if shop["category"] in CATEGORY_NOTES else "packing")
        publish(conn, shop, form, "商城开店档案", today, floor)
        count += 1
    return count


if __name__ == "__main__":
    with PostgresMall(local_setting("ORDER_DATABASE_URL")).connect() as connection:
        count = install(connection)
    print(f"新增店铺条款版本 {count} 份。")
