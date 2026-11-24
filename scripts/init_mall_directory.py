"""创建商城档案、账号、条款和可办理售后的订单。"""

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from secrets import token_urlsafe
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from app.mall_accounts import BUYER_CODES, DIRECTORY_ACCOUNT_FILE, DIRECTORY_ROLES, SHOP_CODES
from app.settings import ROOT, local_setting
from app.storage.postgres_mall import PostgresMall
from scripts.init_shop_policies import install as install_shop_policies
from scripts.init_portfolio_cases import install as install_portfolio_cases


CHINA = ZoneInfo("Asia/Shanghai")
BRANDS = ("栖禾", "云杉", "禾雨", "星渡", "岚川", "知野", "青岑", "松沐", "白屿", "木槿")
CITIES = ("杭州", "苏州", "成都", "武汉", "宁波", "厦门", "南京", "重庆", "青岛", "长沙",
          "合肥", "佛山", "温州", "嘉兴", "济南", "福州", "西安", "南昌", "郑州", "无锡")
SURNAMES = ("林", "周", "许", "沈", "陈", "陆", "程", "顾", "叶", "唐")
GIVEN_NAMES = ("知宁", "雨晴", "子涵", "立言", "安禾")
BUYER_PSEUDONYMS = ("张三", "李四", "王五", "赵六", "钱七", "孙八")
CATEGORIES = (
    ("数码配件", "数码", (("无线鼠标", "岩灰 · 2.4G", "79"), ("编织充电线", "雾白 · 1.5 米", "29"), ("桌面集线器", "银灰 · 4 口", "119")), True),
    ("家居收纳", "家居", (("叠放收纳盒", "透明 · 8 L", "49"), ("抽屉分隔板", "米白 · 4 片", "26"), ("棉麻收纳篮", "原麻色 · 中号", "55")), False),
    ("服饰配件", "衣物", (("棉质棒球帽", "海军蓝 · 可调节", "59"), ("斜挎帆布包", "卡其 · 3 L", "89"), ("针织围巾", "燕麦色 · 180 cm", "76")), False),
    ("运动户外", "户外", (("折叠坐垫", "苔绿 · 2 片装", "38"), ("运动水壶", "深蓝 · 750 mL", "68"), ("露营灯", "暖光 · 可充电", "129")), True),
    ("文具手账", "文具", (("方格笔记本", "浅紫 · A5", "28"), ("中性笔套装", "黑色 · 0.5 mm · 5 支", "25"), ("桌面文件夹", "烟灰 · A4", "39")), False),
    ("厨具餐饮", "厨作", (("玻璃保鲜盒", "透明 · 900 mL", "52"), ("硅胶锅铲", "豆绿 · 28 cm", "32"), ("便携餐具盒", "米白 · 三件套", "44")), False),
    ("灯具照明", "照明", (("桌面阅读灯", "暖白 · 三档调光", "139"), ("感应壁灯", "雾白 · 20 cm", "79"), ("磁吸小夜灯", "柔黄 · 充电款", "59")), True),
    ("宠物周边", "宠物", (("宠物饮水碗", "浅蓝 · 450 mL", "36"), ("手持梳毛刷", "浅绿 · 小号", "45"), ("宠物外出包", "莓红 · 40 cm", "159")), False),
    ("园艺工具", "园艺", (("自吸水花盆", "陶白 · 16 cm", "35"), ("长嘴浇水壶", "墨绿 · 1.2 L", "46"), ("换盆工具组", "原木柄 · 3 件", "58")), False),
    ("居家织物", "布艺", (("吸水棉毛巾", "浅灰 · 两条装", "52"), ("沙发抱枕套", "蜜橙 · 45 cm", "59"), ("防滑地垫", "深咖 · 40×60 cm", "68")), False),
)


def populate_directory(conn, now: datetime) -> None:
    """增加档案、商品和订单，重跑时沿用已有单据及办理状态。"""
    conn.execute((ROOT / "sql/add_mall_directory.sql").read_text(encoding="utf-8"))
    conn.execute((ROOT / "sql/add_mall_policy_editions.sql").read_text(encoding="utf-8"))
    conn.execute((ROOT / "sql/add_mall_supplement_articles.sql").read_text(encoding="utf-8"))
    buyers = BUYER_CODES
    for index, account in enumerate(buyers):
        original = SURNAMES[index // 5] + GIVEN_NAMES[index % 5]
        name = BUYER_PSEUDONYMS[index] if index < len(BUYER_PSEUDONYMS) else original
        joined = date(2023, 1, 1) + timedelta(days=11 * index)
        conn.execute("""
            INSERT INTO public.mall_buyer_profiles AS profile VALUES (%s,%s,%s,%s)
            ON CONFLICT (buyer_id) DO UPDATE SET display_name=EXCLUDED.display_name
            WHERE profile.display_name=%s AND profile.display_name<>EXCLUDED.display_name
        """, (account, name, CITIES[index % len(CITIES)], joined, original))
    for index, account in enumerate(SHOP_CODES):
        category, suffix, products, repairable = CATEGORIES[index % len(CATEGORIES)]
        name = BRANDS[index // len(CATEGORIES)] + suffix
        shop_id = f"market-{index + 1:03d}"
        conn.execute("INSERT INTO public.mall_shops VALUES (%s,%s) ON CONFLICT DO NOTHING", (shop_id, name))
        conn.execute("INSERT INTO public.mall_shop_staff VALUES (%s,%s) ON CONFLICT DO NOTHING", (account, shop_id))
        joined = date(2023, 4, 1) + timedelta(days=5 * index)
        conn.execute("INSERT INTO public.mall_shop_profiles VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                     (shop_id, category, CITIES[index % len(CITIES)], joined, "主营" + "、".join(p[0] for p in products)))
        conn.execute("""
            INSERT INTO public.mall_terms VALUES (%s,1,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING
        """, (shop_id, joined, name + "售后服务条款", 7 if index % 3 else 15, 30, 180 if repairable else 30,
              24 if index % 2 else 48, Decimal("9.00") + index % 4, repairable))
        for slot in range(3):
            serial = index * 3 + slot
            buyer = buyers[serial % len(buyers)]
            add_order(conn, now, serial, buyer, shop_id, name, products, repairable)
    install_shop_policies(conn)


def add_order(conn, now: datetime, serial: int, buyer: str, shop_id: str, shop_name: str,
              products: tuple, repairable: bool) -> None:
    """按订单生成购买金额、不同商品行、收货地址和可追踪包裹。"""
    order_id = f"CN-{300001 + serial}"
    mode = serial % 5
    placed = now - timedelta(days=(0, 2, 4, 6, 19)[mode], hours=serial % 12)
    order = conn.execute("""
        INSERT INTO public.mall_orders
        SELECT %s,%s,t.shop_id,t.version,%s FROM public.mall_terms t
        LEFT JOIN public.mall_shop_policy_editions e USING(shop_id,version)
        WHERE t.shop_id=%s AND t.effective_on<=%s AND (e.published_at IS NULL OR e.published_at<=%s)
        ORDER BY t.effective_on DESC LIMIT 1
        ON CONFLICT DO NOTHING RETURNING order_id,terms_version
    """, (order_id, buyer, placed, shop_id, placed.astimezone(CHINA).date(), placed)).fetchone()
    if order is None:
        return
    benefits = conn.execute("SELECT item_credit FROM public.mall_shop_policy_editions WHERE shop_id=%s AND version=%s",
                            (shop_id, order["terms_version"])).fetchone()

    line_count = 1 + serial % 3
    phases = {0: ("preparing",), 1: ("in_transit",), 2: ("delivered",), 3: ("delivered", "in_transit"),
              4: ("delivered", "delivered")}[mode][:line_count]
    for package, stage in enumerate(phases, start=1):
        conn.execute("INSERT INTO public.mall_packages VALUES (%s,%s,%s,%s,%s,%s,%s)", (
            order_id, package, stage, None if stage == "preparing" else "商城配送",
            None if stage == "preparing" else f"CNW{300001 + serial}{package:02d}",
            placed + timedelta(days=2) if stage == "delivered" else None,
            placed.date() + timedelta(days=3 if stage != "in_transit" else 7),
        ))

    paid_total = Decimal("0")
    for offset in range(line_count):
        sku_no = (serial + offset) % len(products)
        title, variant, list_text = products[sku_no]
        list_price = Decimal(list_text)
        paid = list_price - (Decimal("5") if serial % 4 else Decimal("0"))
        quantity = 1 + (serial + offset) % 3
        paid_total += paid * quantity
        conn.execute("""
            INSERT INTO public.mall_items VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (order_id, offset + 1, offset % len(phases) + 1, f"{shop_id.upper()}-{sku_no + 1:02d}",
              shop_name + " " + title, variant, quantity, list_price, paid,
              max(benefits["item_credit"] if benefits else 0, Decimal("6") if serial % 4 == 0 else Decimal("0")),
              True, repairable and sku_no == 2))

    shipping = Decimal("0") if paid_total >= 99 else Decimal("6") + serial % 3
    payment = ("移动支付", "银行卡", "钱包余额")[serial % 3]
    conn.execute("INSERT INTO public.mall_order_checkout VALUES (%s,%s,%s)", (order_id, shipping, payment))
    conn.execute("INSERT INTO public.mall_order_destinations VALUES (%s,%s)",
                 (order_id, directory_destination(conn, buyer, serial)))


def directory_destination(conn, buyer: str, serial: int) -> Jsonb:
    """根据买家档案生成可随归属更新的商城收件资料。"""
    profile = conn.execute("SELECT display_name,city FROM public.mall_buyer_profiles WHERE buyer_id=%s", (buyer,)).fetchone()
    return Jsonb({"recipient": profile["display_name"], "address": f"{profile['city']}市星桥街道观澜路{20 + serial % 80}号",
                  "phone": f"000000{serial:05d}"})


def migrate_directory_orders(conn) -> int:
    """只迁移新批次里误分给旧买家的订单，不改其他订单或历史售后。"""
    conn.execute("LOCK TABLE public.mall_cases, public.mall_consultations IN SHARE ROW EXCLUSIVE MODE")
    rows = conn.execute("""
        SELECT order_id FROM public.mall_orders
        WHERE order_id BETWEEN 'CN-300001' AND 'CN-300300' AND buyer_id IN ('buyer-a','buyer-b')
        ORDER BY order_id FOR UPDATE
    """).fetchall()
    for row in rows:
        order_id = row["order_id"]
        dependent = conn.execute("""
            SELECT EXISTS(SELECT 1 FROM public.mall_cases WHERE order_id=%s)
                OR EXISTS(SELECT 1 FROM public.mall_consultations WHERE order_id=%s) AS present
        """, (order_id, order_id)).fetchone()["present"]
        if dependent:
            raise RuntimeError(f"{order_id} 已有咨询或售后记录，需要人工确认归属。")
        serial = int(order_id[3:]) - 300001
        buyer = BUYER_CODES[serial % len(BUYER_CODES)]
        conn.execute("UPDATE public.mall_orders SET buyer_id=%s WHERE order_id=%s", (buyer, order_id))
        conn.execute("""
            INSERT INTO public.mall_order_destinations VALUES (%s,%s)
            ON CONFLICT (order_id) DO UPDATE SET details=EXCLUDED.details
        """, (order_id, directory_destination(conn, buyer, serial)))
    return len(rows)


def sync_generated_recipient_names(conn) -> int:
    """同步原先随订单生成的收件人化名，保留另行填写的收件信息。"""
    updated = 0
    for index, name in enumerate(BUYER_PSEUDONYMS):
        buyer = BUYER_CODES[index]
        original = SURNAMES[index // 5] + GIVEN_NAMES[index % 5]
        rows = conn.execute("""
            SELECT o.order_id,d.details FROM public.mall_orders o
            JOIN public.mall_order_destinations d USING (order_id)
            WHERE o.order_id BETWEEN 'CN-300001' AND 'CN-300300' AND o.buyer_id=%s
              AND d.details->>'recipient'=%s
            FOR UPDATE OF d
        """, (buyer, original)).fetchall()
        for row in rows:
            order_id = row["order_id"]
            serial = int(order_id[3:]) - 300001
            destination = directory_destination(conn, buyer, serial).obj
            if destination["recipient"] == name and row["details"] == {**destination, "recipient": original}:
                conn.execute("UPDATE public.mall_order_destinations SET details=%s WHERE order_id=%s",
                             (Jsonb(destination), order_id))
                updated += 1
    return updated


def main() -> None:
    """提交商城数据并为新建的独立账号签发随机登录口令。"""
    database = local_setting("ORDER_DATABASE_URL")
    if not database:
        raise SystemExit("请先配置 ORDER_DATABASE_URL。")
    with PostgresMall(database).connect() as conn:
        populate_directory(conn, datetime.now(CHINA))
        migrated = migrate_directory_orders(conn)
        synced = sync_generated_recipient_names(conn)
        showcases = install_portfolio_cases(conn, conn.execute("SELECT clock_timestamp() AS value").fetchone()["value"])
    DIRECTORY_ACCOUNT_FILE.parent.mkdir(exist_ok=True)
    try:
        with DIRECTORY_ACCOUNT_FILE.open("x", encoding="utf-8") as output:
            json.dump({name: token_urlsafe(24) for name in DIRECTORY_ROLES}, output, indent=2)
    except FileExistsError:
        pass
    print(f"商城档案已准备，迁移 {migrated} 笔订单，更新 {synced} 笔收件姓名，新增 {showcases} 笔案例订单。")


if __name__ == "__main__":
    main()
