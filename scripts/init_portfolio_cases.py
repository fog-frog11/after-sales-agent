"""为商城作品集初始化四笔独立案例订单，已有办理记录可继续查看。"""

import json
from datetime import datetime, time, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from app.settings import ROOT, local_setting
from app.storage.postgres_mall import PostgresMall


CHINA = ZoneInfo("Asia/Shanghai")
CASE_FILE = ROOT / "app/portfolio_cases.json"
MISSING_SKU = "PORTFOLIO-MOUSE"
SUPPLEMENT = uuid5(NAMESPACE_URL, "after-sales-workbench/portfolio/mouse-cable")


def install(conn, now: datetime) -> int:
    """在指定店铺的版本历史中锚定案例订单，重跑保留已有记录。"""
    plan = json.loads(CASE_FILE.read_text(encoding="utf-8"))
    buyer, shop = plan["buyer"], plan["shop"]
    conn.execute("SELECT 1 FROM public.mall_shops WHERE shop_id=%s FOR UPDATE", (shop,)).fetchone()
    old = conn.execute("SELECT version FROM public.mall_terms WHERE shop_id=%s AND version=1", (shop,)).fetchone()
    revised = conn.execute("""SELECT t.version,t.effective_on,e.published_at FROM public.mall_terms t
        JOIN public.mall_shop_policy_editions e USING(shop_id,version)
        WHERE t.shop_id=%s AND e.publisher='商城开店档案'
        ORDER BY t.version LIMIT 1""", (shop,)).fetchone()
    if not old or not revised:
        raise ValueError("请先运行商城档案初始化，准备代表店铺的两个下单版本。")
    cutoff = max(datetime.combine(revised["effective_on"], time.min, CHINA), revised["published_at"])
    if cutoff > now:
        raise ValueError("代表店铺的新版条款尚未生效。")
    before = min(now - timedelta(days=3), cutoff - timedelta(minutes=1))
    known = {case["key"]: [order["id"] for order in case["orders"]] for case in plan["cases"]}
    orders = [
        (known["reship"][0], MISSING_SKU, "栖禾数码 无线鼠标", "岩灰 · 2.4G", 79, before, old["version"], True),
        (known["dispute"][0], "PORTFOLIO-HUB", "栖禾数码 桌面集线器", "银灰 · 4 口", 119, before, old["version"], True),
        (known["edition"][0], "PORTFOLIO-RETURN", "栖禾数码 编织充电线", "雾白 · 1.5 米", 29, before, old["version"], False),
        (known["edition"][1], "PORTFOLIO-RETURN", "栖禾数码 编织充电线", "雾白 · 1.5 米", 29, now, revised["version"], False),
    ]
    count = 0
    for order_id, sku, title, variant, price, placed, version, delivered in orders:
        inserted = conn.execute("""INSERT INTO public.mall_orders (order_id,buyer_id,shop_id,terms_version,ordered_at)
            VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING order_id""",
            (order_id,buyer,shop,version,placed)).fetchone()
        if not inserted:
            owner = conn.execute("SELECT buyer_id,shop_id FROM public.mall_orders WHERE order_id=%s", (order_id,)).fetchone()
            if owner != {"buyer_id": buyer, "shop_id": shop}:
                raise ValueError(f"订单 {order_id} 已被其他账号占用。")
            continue
        conn.execute("""INSERT INTO public.mall_packages (order_id,package_no,stage,carrier,tracking,delivered_at,expected_on)
            VALUES (%s,1,%s,%s,%s,%s,%s)""",
            (order_id, "delivered" if delivered else "preparing", "商城配送" if delivered else None,
             order_id.replace("CN-", "CNW") if delivered else None, now - timedelta(days=1) if delivered else None,
             now.astimezone(CHINA).date() + timedelta(days=2)))
        conn.execute("""INSERT INTO public.mall_items
            (order_id,item_no,package_no,sku,title,variant,quantity,list_unit,paid_unit,return_credit,change_of_mind,repairable)
            VALUES (%s,1,1,%s,%s,%s,1,%s,%s,0,true,false)""", (order_id,sku,title,variant,price,price))
        conn.execute("""INSERT INTO public.mall_order_checkout (order_id,shipping,payment_method) VALUES (%s,%s,'移动支付')""",
                     (order_id, Decimal("6.00")))
        conn.execute("INSERT INTO public.mall_order_destinations VALUES (%s,%s)", (order_id, Jsonb({
            "recipient": "张三", "address": "杭州市星桥街道观澜路18号", "phone": "00000410001"})))
        count += 1
    if conn.execute("""SELECT NOT EXISTS (SELECT 1 FROM public.mall_supplement_reviews WHERE shop_id=%s AND article_key=%s)
        AS needs_article""", (shop, SUPPLEMENT)).fetchone()["needs_article"]:
        placed = conn.execute("SELECT ordered_at FROM public.mall_orders WHERE order_id=%s",
                              (known["reship"][0],)).fetchone()["ordered_at"]
        conn.execute("""INSERT INTO public.mall_supplement_reviews
            (shop_id,note,author,created_at,decision,reviewer,reviewed_at,feedback,topic,title,sku,article_key)
            VALUES (%s,%s,%s,%s,'approved',%s,%s,%s,'missing',%s,%s,%s)""",
            (shop, "无线鼠标少了充电线，可以单独补寄。请注明线材名称和接口型号，已收到的鼠标可以保留。",
             plan["merchant"], placed-timedelta(days=2), plan["staff"], placed-timedelta(days=1),
             "项目案例的商品范围已核对。", "无线鼠标充电线缺件补发", MISSING_SKU, SUPPLEMENT))
    return count


if __name__ == "__main__":
    url = local_setting("ORDER_DATABASE_URL")
    if not url:
        raise SystemExit("请先配置 ORDER_DATABASE_URL。")
    with PostgresMall(url).connect() as connection:
        count = install(connection, connection.execute("SELECT clock_timestamp() AS value").fetchone()["value"])
    print(f"已准备作品集案例，新增 {count} 笔订单。")
