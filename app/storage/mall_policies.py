"""店铺条款发布、平台底线校验及订单版本查询。"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.tickets.service_requests import ServiceError
from app.storage.mall_supplements import applicable_supplements, supplement_templates


CATEGORY_NOTES = {
    "数码配件": "接口或连接异常时，可以拍接口近照并附上使用的设备型号。",
    "家居收纳": "收纳件变形时，可以拍正面和侧面；套装缺件可标出缺少的部件。",
    "服饰配件": "尺码或颜色发错时，可以拍吊牌和商品全貌，方便核对款式。",
    "运动户外": "配件松脱时，可以拍连接位置及配件全貌。",
    "文具手账": "套装缺件时，可以把已收到的文具放在一起拍照，说明少了哪件。",
    "厨具餐饮": "玻璃或陶瓷破损时，可保持原状拍照，避免徒手清理碎片。",
    "灯具照明": "灯具异常时，可记录型号和异常表现；有焦味请先断电。",
    "宠物周边": "碗体、梳齿或背带损坏时，可以拍受损位置和商品整体。",
    "园艺工具": "园艺工具漏水或变形时，可以拍接口或受损部件。",
    "居家织物": "织物开线或尺寸不符时，可以拍走线或商品与量尺的对照图。",
}


class ShopPublication(BaseModel):
    """店铺可自主编写的结构化承诺；平台约束由发布事务单独核对。"""
    model_config = ConfigDict(extra="forbid")
    base_version: int = Field(ge=1)
    effective_on: date
    return_days: int = Field(ge=1, le=365)
    fault_days: int = Field(ge=1, le=730)
    warranty_days: int = Field(ge=1, le=3650)
    reply_hours: int = Field(ge=1, le=168)
    return_postage: Decimal = Field(ge=0, max_digits=9, decimal_places=2)
    item_credit: Decimal = Field(ge=0, max_digits=9, decimal_places=2)
    seller_advances: bool
    guidance_key: Literal["category", "packing", "missing"]


def guidance_options(category: str) -> dict[str, str]:
    """给出本经营类目可发布的办理指引，举证建议均为可选。"""
    return {"category": CATEGORY_NOTES.get(category, "可以拍商品全貌和问题位置，供店铺核对。"),
            "packing": "寄回时建议把配件放在一起，另加外包装保护商品。",
            "missing": "若只少了配件，可以先告诉店铺部件名称和数量，核对后可申请补发。"}


def platform_lines(floor: dict) -> list[str]:
    """把平台自身采用的最低保障转换成买家可读的说明。"""
    return [f"支持无理由退换的商品，期限至少 {floor['return_days']} 天。",
            f"质量、发错和少件的申请期至少 {floor['fault_days']} 天；支持维修的商品维修期至少 {floor['warranty_days']} 天。",
            f"店铺须在 {floor['reply_hours']} 小时内响应；无理由寄回报价最高 ¥{floor['max_postage']:.2f}。"]


def validate_commitments(form: ShopPublication, floor: dict) -> None:
    """逐项拦截低于平台保障或彼此冲突的承诺。"""
    for key, label in (("return_days", "无理由退换"), ("fault_days", "质量售后"), ("warranty_days", "维修")):
        if getattr(form, key) < floor[key]:
            raise ServiceError(422, f"{label}期限至少为 {floor[key]} 天。")
    if not form.return_days <= form.fault_days <= form.warranty_days:
        raise ServiceError(422, "质量售后期限应覆盖无理由退换期限，维修期限应覆盖质量售后期限。")
    if form.reply_hours > floor["reply_hours"] or form.return_postage > floor["max_postage"]:
        raise ServiceError(422, f"响应不能超过 {floor['reply_hours']} 小时，寄回报价不能超过 ¥{floor['max_postage']:.2f}。")
    if form.item_credit > form.return_postage:
        raise ServiceError(422, "每件运费权益不能超过本店寄回报价。")


POLICIES = """SELECT t.*, e.platform_version,e.item_credit,e.guidance_key,e.guidance,e.published_at
              FROM public.mall_terms t LEFT JOIN public.mall_shop_policy_editions e USING(shop_id,version)"""


def current_policy(conn, shop_id: str, version: int | None = None) -> dict | None:
    """按上海日期读取已生效的店铺版本，追问时可复核先前引用的那一版。"""
    row = conn.execute(POLICIES + """ WHERE t.shop_id=%s AND e.platform_version IS NOT NULL
            AND t.effective_on<=(now() AT TIME ZONE 'Asia/Shanghai')::date
            AND (%s::integer IS NULL OR t.version=%s) ORDER BY t.effective_on DESC LIMIT 1""", (shop_id, version, version)).fetchone()
    if not row:
        return None
    row["floor"] = conn.execute("SELECT * FROM public.mall_platform_policies WHERE version=%s",
                                (row["platform_version"],)).fetchone()
    return row


def publish(conn, shop: dict, form: ShopPublication, actor: str, today: date, floor: dict) -> dict:
    """调用方已锁定店铺；只追加新版本，并以基准版本防止双击或并发覆盖。"""
    last = conn.execute("SELECT version,effective_on FROM public.mall_terms WHERE shop_id=%s ORDER BY version DESC LIMIT 1",
                        (shop["shop_id"],)).fetchone()
    if last["version"] != form.base_version:
        raise ServiceError(409, "已有新版本，请重新载入后核对。")
    if form.effective_on < max(today, last["effective_on"] + timedelta(days=1)):
        raise ServiceError(422, "生效日须从今天起，且晚于本店上一版的生效日。")
    validate_commitments(form, floor)
    version = last["version"] + 1
    conn.execute("""INSERT INTO public.mall_terms
        (shop_id,version,effective_on,title,return_days,fault_days,warranty_days,reply_hours,return_postage,seller_advances)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (shop["shop_id"], version, form.effective_on, shop["name"] + "售后服务条款", form.return_days, form.fault_days,
         form.warranty_days, form.reply_hours, form.return_postage, form.seller_advances))
    conn.execute("""INSERT INTO public.mall_shop_policy_editions
        (shop_id,version,platform_version,item_credit,guidance_key,guidance,publisher) VALUES (%s,%s,%s,%s,%s,%s,%s)""",
        (shop["shop_id"], version, floor["version"], form.item_credit, form.guidance_key,
         guidance_options(shop["category"])[form.guidance_key], actor))
    return {"version": version, "effective_on": form.effective_on}


class MallPolicyBook:
    """商家只操作自己负责的店铺，平台底线只读。"""

    def __init__(self, store):
        """绑定应用提供的事务连接。"""
        self.store = store

    @staticmethod
    def shops(conn, account: str) -> list[dict]:
        """根据登录身份取得店铺，不从请求体信任店铺归属。"""
        return conn.execute("""SELECT s.shop_id,s.name,p.category FROM public.mall_shop_staff m
            JOIN public.mall_shops s USING(shop_id) JOIN public.mall_shop_profiles p USING(shop_id)
            WHERE m.account_id=%s ORDER BY s.shop_id""", (account,)).fetchall()

    def read(self, account: str) -> dict:
        """返回本店各次承诺以及与下一次发布时间对应的底线。"""
        with self.store.connect() as conn:
            shops = self.shops(conn, account)
            floor = conn.execute("SELECT * FROM public.mall_platform_policies ORDER BY version DESC LIMIT 1").fetchone()
            today = conn.execute("SELECT (now() AT TIME ZONE 'Asia/Shanghai')::date AS d").fetchone()["d"]
            for shop in shops:
                shop["editions"] = conn.execute(POLICIES + " WHERE t.shop_id=%s ORDER BY t.version DESC",
                                                (shop["shop_id"],)).fetchall()
                first = shop["editions"][0]
                shop["first_date"] = max(today, first["effective_on"] + timedelta(days=1))
                shop["guidance_options"] = guidance_options(shop["category"])
                shop["supplements"] = conn.execute("""SELECT id,note,decision,feedback,title,topic,sku,reviewed_at,
                    decision='approved' AND article_key IS NOT NULL AND NOT EXISTS (
                        SELECT 1 FROM public.mall_supplement_reviews newer
                        WHERE newer.shop_id=r.shop_id AND newer.article_key=r.article_key
                          AND newer.decision='approved' AND (newer.reviewed_at,newer.id)>(r.reviewed_at,r.id)
                    ) AS can_revise FROM public.mall_supplement_reviews r
                    WHERE shop_id=%s ORDER BY id DESC""",(shop["shop_id"],)).fetchall()
                shop["supplement_templates"] = supplement_templates(shop["category"])
                shop["products"] = conn.execute("""SELECT sku,min(i.title) AS title
                    FROM public.mall_items i JOIN public.mall_orders o USING(order_id)
                    WHERE o.shop_id=%s GROUP BY sku ORDER BY sku""", (shop["shop_id"],)).fetchall()
            return {"shops": shops, "platform": platform_lines(floor), "platform_version": floor["version"], "today": today}

    def preview_item(self, account: str, shop_id: str, sku: str) -> dict:
        """以此刻为购买时间，只预览本店相应商品的已生效条款。"""
        with self.store.connect() as conn:
            owned = conn.execute("""SELECT 1 FROM public.mall_shop_staff
                WHERE account_id=%s AND shop_id=%s""", (account, shop_id)).fetchone()
            product = owned and conn.execute("""SELECT 1 FROM public.mall_items i
                JOIN public.mall_orders o USING(order_id) WHERE o.shop_id=%s AND i.sku=%s LIMIT 1""",
                (shop_id, sku)).fetchone()
            if not product:
                raise ServiceError(404, "查不到本店的这件商品。")
            now = conn.execute("SELECT clock_timestamp() AS bought").fetchone()["bought"]
            item = conn.execute(POLICIES + """WHERE t.shop_id=%s
                AND t.effective_on<=(%s AT TIME ZONE 'Asia/Shanghai')::date
                AND (e.published_at IS NULL OR e.published_at<=%s)
                ORDER BY t.effective_on DESC,t.version DESC LIMIT 1""", (shop_id, now, now)).fetchone()
            if not item:
                raise ServiceError(404, "这家店没有已生效的下单条款。")
            item.update(sku=sku, ordered_at=now, terms_version=item["version"])
            item["supplements"] = applicable_supplements(conn, item)
            item["missing_supplement"] = next((row for row in item["supplements"] if row["title"] is None), None)
            return item

    def release(self, account: str, shop_id: str, form: ShopPublication) -> dict:
        """用同一事务检查账号、平台版本、同店并发和新承诺。"""
        with self.store.connect() as conn:
            shop = conn.execute("""SELECT s.*,p.category FROM public.mall_shops s JOIN public.mall_shop_profiles p USING(shop_id)
                JOIN public.mall_shop_staff m USING(shop_id) WHERE m.account_id=%s AND s.shop_id=%s FOR UPDATE OF s""",
                (account, shop_id)).fetchone()
            if not shop:
                raise ServiceError(404, "查不到该店铺。")
            floor = conn.execute("SELECT * FROM public.mall_platform_policies ORDER BY version DESC LIMIT 1 FOR SHARE").fetchone()
            today = conn.execute("SELECT (now() AT TIME ZONE 'Asia/Shanghai')::date AS d").fetchone()["d"]
            return publish(conn, shop, form, account, today, floor)
