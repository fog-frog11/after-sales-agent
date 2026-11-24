"""为三角色真实浏览器验收提供独立应用、临时订单和可控制的到期时间。"""

from contextlib import contextmanager
from datetime import datetime, timedelta
import json
import os
from secrets import token_urlsafe
from socket import AF_INET, SOCK_STREAM, socket
from unittest.mock import patch

from fastapi import HTTPException, Request
import uvicorn
from psycopg.conninfo import make_conninfo

from app.access import SESSION_COOKIE, DemoAccess
from app.api import create_app
from app.settings import ROOT, local_setting
from app.storage.mall_directory import MallDirectory
from app.storage.mall_policies import ShopPublication, publish
from app.storage.mall_supplements import SupplementDraft, applicable_supplements, review_supplement, submit_supplement
from app.storage.postgres_mall import PostgresMall
from app.integrations.deepseek import ModelError
from app.mall.agent import PLANNING_TOOL, deepseek_mall_planner, deepseek_mall_writer
from app.mall.read_loop import evidence_decider, business_decider
from app.mall.grounded_reply import grounded_reviewer, grounded_writer
from app.policies.mall_retrieval import MallRetrieval
from app.tickets.service_requests import ServiceError
from scripts.init_mall_directory import CHINA, CATEGORIES, add_order, migrate_directory_orders, populate_directory, sync_generated_recipient_names
from scripts.init_portfolio_cases import install as install_portfolio
from scripts.init_mall import create_schema


ORDERS = {"CN-9870": "CN-300003", "CN-9877": "CN-300003", "CN-9878": "CN-300003", "CN-9876": "CN-300001",
          "CN-9875": "CN-300001", "CN-9874": "CN-300001", "CN-9873": "CN-300002", "CN-9872": "CN-300002"}


def browser_planner(question: str, *, dialogue: dict | None = None) -> dict:
    """默认用固定调用；显式启用联调时仅将一个预定的自编问题发给官方模型。"""
    if question in ("物流到哪了，再看看买了几件",
                    "充电线少了一根，外盒也压扁了。线能补寄吗？照片要拍哪些？这件买了几件？"):
        raise ModelError("验收模型超时")
    compound = question == "那款啥样，款子回了没"
    history_probe = question == "我想回顾一下以往在这里下单买的东西"
    price_probe = question == "挑十一月二十号买的，里面价格最划算的那一件"
    preference_probe = question in ("我这个订单的坐垫坐着不舒服哎", "我觉得不舒服 能退货吗？")
    scope_probe = question in ("你能帮我写首关于海边的诗吗？", "这个毛刷用着效果不太好",
                               "写首诗，顺便查一下原包裹到哪了？")
    clarification_probe = os.getenv("MALL_BROWSER_JOURNEY") == "clarification" and question in (
        "这个玻璃保鲜盒打开后碎了", "不是这个原因", "买来的时候里面盒子打开已经碎了")
    fit_probe = os.getenv("MALL_BROWSER_JOURNEY") == "fit-exchange" and question in (
        "这个围巾偏小", "那就大一码")
    if (compound or history_probe or price_probe or preference_probe or scope_probe or clarification_probe or fit_probe
            ) and os.getenv("MALL_LIVE_SMOKE") == "1":
        key = local_setting("DEEPSEEK_API_KEY")
        if not key:
            raise RuntimeError("真实模型验收需要 DEEPSEEK_API_KEY")
        return deepseek_mall_planner(key)(question, dialogue=dialogue)
    topics = ["product", "refund"] if compound else ["purchase_history"] if history_probe else []
    args = {"topics": topics}
    if price_probe:
        args = {"topics": ["clarify_order"], "candidate_filter": {"month": 11, "day": 20, "price": "lowest"}}
    if preference_probe:
        args = {"topics": ["product"]}
    if question == "你能帮我写首关于海边的诗吗？":
        args = {"topics": ["out_of_scope"]}
    if clarification_probe:
        args = {"topics": ["eligibility"], "possible_reason": "quality",
                "follow_up": {"fact": "damage_timing", "question": "是拆包时就发现盒子碎了，还是使用后才碎的？"}}
    return {"name": PLANNING_TOOL, "arguments": json.dumps(args)}


def verify_future_checkout(conn) -> None:
    """在独立回滚点创建生效前后的订单，核对版本与商品运费权益快照。"""
    with conn.transaction(force_rollback=True):
        shop = conn.execute("""SELECT s.*,p.category FROM public.mall_shops s
            JOIN public.mall_shop_profiles p USING(shop_id) WHERE s.shop_id='market-001' FOR UPDATE OF s""").fetchone()
        floor = conn.execute("SELECT * FROM public.mall_platform_policies ORDER BY version DESC LIMIT 1").fetchone()
        last = conn.execute("SELECT * FROM public.mall_terms WHERE shop_id=%s ORDER BY version DESC LIMIT 1",
                            (shop["shop_id"],)).fetchone()
        now = datetime.now(CHINA)
        start = max(now.date(), last["effective_on"]) + timedelta(days=2)
        new = ShopPublication(base_version=last["version"], effective_on=start, return_days=25, fault_days=45,
            warranty_days=180, reply_hours=12, return_postage=10, item_credit=9, seller_advances=False, guidance_key="packing")
        publish(conn, shop, new, "shop-001", now.date(), floor)
        for serial, day, version in ((90120, start - timedelta(days=1), last["version"]),
                                     (90180, start, last["version"] + 1)):
            placed = datetime.combine(day, now.timetz())
            add_order(conn, placed, serial, "buyer-001", shop["shop_id"], shop["name"], CATEGORIES[0][2], True)
            item = PostgresMall._items(conn, "buyer-001", f"CN-{300001 + serial}", 1)[0]
            assert item["terms_version"] == version
            if day == start:
                assert item["return_credit"] == 9 and item["return_days"] == 25 and item["platform_version"] == floor["version"]


def verify_supplement_lifecycle(conn) -> None:
    """用同店两个商品、两个生效时点和并行起草的修订版核对隔离。"""
    with conn.transaction(force_rollback=True):
        item = PostgresMall._items(conn, "buyer-001", "CN-9877", 1)[0]
        other = PostgresMall._items(conn, "buyer-001", "CN-9877", 2)[0]
        draft = SupplementDraft(title="无线鼠标配件补发", sku=item["sku"], note="申请补发请注明缺少的充电线名称。")
        first = submit_supplement(conn, item["shop_id"], "shop-001", draft)["id"]
        assert first not in {row["id"] for row in applicable_supplements(conn, item)}
        published = review_supplement(conn, first, "staff", "approved", "该商品范围已核实。")
        v1_order = {**item, "ordered_at": published["reviewed_at"]}
        assert first in {row["id"] for row in applicable_supplements(conn, v1_order)}
        assert first not in {row["id"] for row in applicable_supplements(conn, item)}
        assert first not in {row["id"] for row in applicable_supplements(conn, {
            **v1_order, "sku": other["sku"]})}
        assert first not in {row["id"] for row in applicable_supplements(conn, {
            **v1_order, "shop_id": "market-002"})}
        revision = draft.model_copy(update={"revises_id":first, "note":"补发请注明接口类型和线长。"})
        second = submit_supplement(conn, item["shop_id"], "shop-001", revision)["id"]
        stale = submit_supplement(conn, item["shop_id"], "shop-001",
                                 revision.model_copy(update={"note":"补发时请选择需要的颜色。"}))["id"]
        newer = review_supplement(conn, second, "staff", "approved", "更新了所需信息。")
        try:
            review_supplement(conn, stale, "staff", "approved", "另一份改稿。")
        except ServiceError as error:
            assert error.status_code == 409
        else:
            raise AssertionError("旧修订覆盖了新版本")
        second_order = {**item, "ordered_at": newer["reviewed_at"]}
        current = {row["id"] for row in applicable_supplements(conn, second_order)}
        assert second in current and first not in current and stale not in current
        assert first in {row["id"] for row in applicable_supplements(conn, v1_order)}
        photos = SupplementDraft(topic="evidence", title="外箱照片", note="破损外盒拍全貌和被压位置。")
        third = submit_supplement(conn, item["shop_id"], "shop-001", photos)["id"]
        extra = review_supplement(conn, third, "staff", "approved", "可作为包装留证说明。")
        now = {**item, "ordered_at": extra["reviewed_at"]}
        assert {second,third} <= {row["id"] for row in applicable_supplements(conn, now)}


def run() -> None:
    """在回滚事务中运行验收服务，只允许测试订单推进时钟。"""
    database = local_setting("ORDER_DATABASE_URL")
    if os.getenv('MALL_ACCEPTANCE_DB')=='1':
        database=make_conninfo(database,dbname='after_sales_v3_release_20261122')
    if not database:
        raise SystemExit("请先启动 PostgreSQL 并配置 ORDER_DATABASE_URL。")
    store = PostgresMall(database, browser_planner)
    if os.getenv("MALL_MODEL_DIALOGUE") == "1":
        api_key = local_setting("DEEPSEEK_API_KEY")
        if not api_key:
            raise SystemExit("完整模型对话验收需要 API 配置")
        store = PostgresMall(database, deepseek_mall_planner(api_key, semantic=True),
                             model_dialogue=True, composer=grounded_writer(api_key),reviewer=grounded_reviewer(api_key),read_decider=evidence_decider(api_key),business_decider=business_decider(api_key),
                             retrieval=MallRetrieval(local_setting("MILVUS_URL"),persistent_dense=os.getenv('MALL_TEST_PERSISTENT_DENSE')=='1',
                                 window_rerank=os.getenv('MALL_TEST_WINDOW_RERANK')=='1')
                                 if os.getenv("MALL_TEST_RETRIEVAL") == "1" else None)
    if os.getenv("MALL_LIVE_WRITER") == "1":
        api_key = local_setting("DEEPSEEK_API_KEY")
        if not api_key:
            raise SystemExit("缺少 DeepSeek API 配置。")
        writer = deepseek_mall_writer(api_key)

        def write_test_reply(facts: dict) -> str:
            """为找单与范围引导使用真实模型，其余保持固定浏览器断言。"""
            return writer(facts) if facts["任务"] in ("查找买家订单", "引导回售后") else ""

        store.writer = write_test_reply
    passcode = token_urlsafe(24)
    accounts = {name: passcode for name in ("staff", "buyer-001", "shop-001")}
    with store.connect() as conn, conn.transaction(force_rollback=True):
        if os.getenv("MALL_BROWSER_JOURNEY") == "portfolio":
            create_schema(conn)
        conn.execute((ROOT / "sql/add_mall_platform_reviews.sql").read_text(encoding="utf-8"))
        conn.execute((ROOT / "sql/add_mall_outbound_delivery.sql").read_text(encoding="utf-8"))
        conn.execute((ROOT / "sql/add_mall_package_returns.sql").read_text(encoding="utf-8"))
        populate_directory(conn, datetime.now(CHINA))
        if os.getenv("MALL_BROWSER_JOURNEY") == "portfolio":
            as_of = conn.execute("SELECT clock_timestamp() AS value").fetchone()["value"]
            install_portfolio(conn, as_of)
            assert install_portfolio(conn, as_of + timedelta(days=1)) == 0
            conn.execute("UPDATE public.mall_items SET paid_unit=19 WHERE order_id='CN-410004'")
            conn.execute("""UPDATE public.mall_orders SET ordered_at=make_timestamptz(
                EXTRACT(YEAR FROM clock_timestamp())::int,11,20,10,0,0,'Asia/Shanghai')
                WHERE order_id='CN-410004'""")
        verify_future_checkout(conn)
        migrate_directory_orders(conn)
        sync_generated_recipient_names(conn)
        access = DemoAccess(accounts, names=MallDirectory.account_names(conn))
        app = create_app(mall=store, access=access)
        multi_intent = os.getenv("MALL_BROWSER_JOURNEY") == "multi-intent"
        orders = ORDERS | ({"CN-9868": "CN-300003", "CN-9869": "CN-300006"} if multi_intent else {})
        preference_journey = os.getenv("MALL_BROWSER_JOURNEY") == "product-experience"
        if preference_journey:
            orders = orders | {"CN-9866": "CN-300003"}
        for order_id, source in orders.items():
            conn.execute("""
                INSERT INTO public.mall_orders
                SELECT %s,'buyer-001',shop_id,terms_version,now()-interval '2 days'
                FROM public.mall_orders WHERE order_id=%s
            """, (order_id, source))
            conn.execute("""
                INSERT INTO public.mall_packages
                SELECT %s,package_no,stage,
                    CASE WHEN stage<>'preparing' THEN '浏览器验收' END,
                    CASE WHEN stage<>'preparing' THEN 'TEST9877' END,
                    CASE WHEN stage='delivered' THEN now()-interval '1 day' END,current_date
                FROM public.mall_packages WHERE order_id=%s
            """, (order_id, source))
            conn.execute("""
                INSERT INTO public.mall_items
                SELECT %s,item_no,package_no,sku,title,variant,quantity,list_unit,paid_unit,return_credit,change_of_mind,repairable
                FROM public.mall_items WHERE order_id=%s
            """, (order_id, source))
            conn.execute("""
                INSERT INTO public.mall_order_destinations
                SELECT %s,details FROM public.mall_order_destinations WHERE order_id='CN-300001'
            """, (order_id,))

        if preference_journey:
            conn.execute("""UPDATE public.mall_items SET title='折叠坐垫', change_of_mind=true
                            WHERE order_id='CN-9866' AND item_no=1""")
        if os.getenv('MALL_BROWSER_JOURNEY')=='task-actions':
            conn.execute("UPDATE public.mall_items SET quantity=3,title='桌面集线器' WHERE order_id='CN-9877' AND item_no=1")
        if os.getenv("MALL_BROWSER_JOURNEY") == "conversation-scope":
            conn.execute("""UPDATE public.mall_items SET title='手持梳毛刷', change_of_mind=true
                            WHERE order_id='CN-9870' AND item_no=1""")
        if os.getenv("MALL_BROWSER_JOURNEY") == "clarification":
            conn.execute("""UPDATE public.mall_items SET title='玻璃保鲜盒'
                            WHERE order_id='CN-9870' AND item_no=1""")
        if os.getenv("MALL_BROWSER_JOURNEY") in ("fit-exchange", "semantic"):
            conn.execute("""UPDATE public.mall_items SET title='针织围巾', change_of_mind=true
                            WHERE order_id='CN-9870' AND item_no=1""")

        # 给同一家店的两笔验收单绑定不同条款，验证新版费率不会替换旧单快照。
        conn.execute("""
            WITH revised AS (
                INSERT INTO public.mall_terms
                SELECT t.shop_id, (SELECT max(version)+1 FROM public.mall_terms WHERE shop_id=t.shop_id),
                       (SELECT max(effective_on)+1 FROM public.mall_terms WHERE shop_id=t.shop_id),
                       t.title, t.return_days, t.fault_days, t.warranty_days, t.reply_hours,
                       t.return_postage+7, t.seller_advances
                FROM public.mall_orders o JOIN public.mall_terms t ON t.shop_id=o.shop_id AND t.version=o.terms_version
                WHERE o.order_id='CN-9870' RETURNING shop_id,version
            )
            UPDATE public.mall_orders o SET terms_version=revised.version FROM revised
            WHERE o.order_id='CN-9870' AND o.shop_id=revised.shop_id
        """)
        conn.execute("""UPDATE public.mall_packages SET stage='in_transit',carrier='配送',tracking='TRANSIT123'
                        WHERE order_id IN ('CN-9875','CN-9874')""")
        conn.execute("UPDATE public.mall_items SET quantity=2 WHERE order_id='CN-9874'")
        conn.execute("""INSERT INTO public.mall_order_checkout (order_id, shipping, payment_method)
                        VALUES ('CN-9873',0,'移动支付'), ('CN-9872',6,'移动支付')""")
        conn.execute("UPDATE public.mall_items SET return_credit=item_no + 1 WHERE order_id='CN-9872'")
        verify_supplement_lifecycle(conn)

        if multi_intent:
            conn.execute("""UPDATE public.mall_packages SET stage='delivered',delivered_at=now()-interval '1 day',
                carrier='浏览器验收',tracking='TEST9868'
                WHERE order_id IN ('CN-9868','CN-9869')""")
            for order_id, note in (("CN-9868","甲店补发时请写清配件型号。"), ("CN-9869","乙店补发时请标明缺件位置。")):
                conn.execute("""INSERT INTO public.mall_supplement_reviews
                    (shop_id,note,author,decision,reviewer,reviewed_at)
                    SELECT shop_id,%s,'test-shop','approved','test-staff',ordered_at-interval '1 day'
                    FROM public.mall_orders WHERE order_id=%s""", (note, order_id))
            conn.execute("""INSERT INTO public.mall_supplement_reviews
                (shop_id,note,author,decision,reviewer,reviewed_at)
                SELECT shop_id,'下单后批准的说明不应出现','test-shop','approved','test-staff',ordered_at+interval '1 hour'
                FROM public.mall_orders WHERE order_id='CN-9868'""")

        @app.post("/_test/copy-purchase-after-review")
        def copy_purchase(request: Request) -> dict:
            """仅在回滚夹具中创建同款的新订单，验证审核通过后的购买时间。"""
            account = access.account(request.cookies.get(SESSION_COOKIE))
            if not account or account.account_id != "buyer-001":
                raise HTTPException(403)
            conn.execute("""INSERT INTO public.mall_orders
                SELECT 'CN-9867',buyer_id,shop_id,terms_version,clock_timestamp()
                FROM public.mall_orders WHERE order_id='CN-9877'""")
            conn.execute("""INSERT INTO public.mall_packages
                SELECT 'CN-9867',package_no,stage,carrier,tracking,delivered_at,expected_on
                FROM public.mall_packages WHERE order_id='CN-9877'""")
            conn.execute("""INSERT INTO public.mall_items
                SELECT 'CN-9867',item_no,package_no,sku,title,variant,quantity,list_unit,paid_unit,return_credit,change_of_mind,repairable
                FROM public.mall_items WHERE order_id='CN-9877'""")
            return {"order_id": "CN-9867"}

        @app.post("/_test/expire/{number}")
        def expire(number: int, request: Request) -> dict:
            """只为验收买家模拟临时订单上的店铺待办超时。"""
            account = access.account(request.cookies.get(SESSION_COOKIE))
            if not account or account.role != "buyer":
                raise HTTPException(403)
            saved = conn.execute("""
                UPDATE public.mall_cases SET due_at=now()-interval '1 hour'
                WHERE case_no=%s AND order_id=ANY(%s) AND status IN ('awaiting_return','return_sent','awaiting_outcome','received')
                RETURNING case_no
            """, (number, list(ORDERS))).fetchone()
            if not saved:
                raise HTTPException(404)
            return {"expired": True}

        @contextmanager
        def borrowed_connection():
            """所有请求使用同一事务，临时数据在测试结束后回滚。"""
            yield conn

        with patch.object(store, "connect", borrowed_connection), socket(AF_INET, SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            print(f"PORT={listener.getsockname()[1]}", flush=True)
            print(f"TEST_PASSWORD={passcode}", flush=True)
            uvicorn.Server(uvicorn.Config(app, log_level="warning")).run(sockets=[listener])


if __name__ == "__main__":
    run()
