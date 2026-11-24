"""商家补充条款的草稿、审核和购买时间快照。"""

import json
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, StringConstraints

from app.settings import ROOT
from app.tickets.service_requests import ServiceError


Topic = Literal["missing", "evidence", "freight", "return", "refund", "delivery", "service"]
TEMPLATE_GROUPS = {"数码配件": "digital", "家居收纳": "home", "服饰配件": "fashion"}
TEMPLATE_TOPICS = {
    "D03":"service", "D04":"service", "D06":"service", "D07":"service", "D09":"evidence", "D10":"return",
    "H04":"evidence", "H05":"evidence", "H07":"service", "H08":"return", "H10":"service",
    "F03":"freight", "F04":"evidence", "F05":"evidence", "F06":"evidence", "F07":"return",
    "F08":"return", "F10":"evidence",
    "R05":"missing", "R06":"missing",
    "R07":"evidence", "R08":"evidence", "R28":"evidence", "R29":"evidence", "R34":"evidence",
    "R09":"freight", "R10":"freight", "R11":"freight", "R12":"freight", "R38":"freight",
    "R02":"return", "R04":"return", "R14":"return", "R15":"return", "R18":"return", "R19":"return",
    "R20":"return", "R27":"return", "R33":"return", "R36":"return",
    "R13":"refund", "R21":"refund", "R24":"refund", "R25":"refund", "R26":"refund",
    "R16":"delivery", "R17":"delivery", "R22":"delivery", "R23":"delivery",
}


class SupplementDraft(BaseModel):
    """发布前填写主题、适用商品和条款正文。"""

    topic: Topic = "missing"
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=60)] | None = None
    sku: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)] | None = None
    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=250)]
    revises_id: int | None = Field(default=None, gt=0)


def supplement_templates(category: str) -> list[dict]:
    """品类示例只供起草，必须走店铺提交和平台审核。"""
    group = TEMPLATE_GROUPS.get(category)
    catalog = json.loads((ROOT / "scripts/fixtures/mall_terms_catalog.json").read_text(encoding="utf-8"))
    book = catalog["shops"].get(group, {})
    articles = book.get("articles", {}) | book.get("v2", {}) | catalog["shared"]
    return [{"title": title, "note": note, "topic": TEMPLATE_TOPICS.get(code, "missing" if not code.startswith("R") else "service"),
             "shared": code.startswith("R")}
            for code, (title, note) in articles.items()]


def applicable_supplements(conn, item: dict) -> list[dict]:
    """在本店和该 SKU 范围内，选出每条补充约定在下单时生效的版次。"""
    return conn.execute("""SELECT id,topic,title,sku,note,reviewed_at FROM (
            SELECT DISTINCT ON (article_key) id,article_key,topic,title,sku,note,reviewed_at
            FROM public.mall_supplement_reviews
            WHERE shop_id=%s AND decision='approved' AND reviewed_at<=%s AND (sku IS NULL OR sku=%s)
            ORDER BY article_key, reviewed_at DESC,id DESC
        ) AS versions
        ORDER BY (sku IS NOT NULL) DESC,reviewed_at DESC,id DESC""",
        (item["shop_id"], item["ordered_at"], item["sku"])).fetchall()


def submit_supplement(conn, shop_id: str, account: str, form: SupplementDraft) -> dict:
    """验证条款归属、商品范围及修订基准后加入审核队列。"""
    shop = conn.execute("""SELECT s.shop_id FROM public.mall_shops s JOIN public.mall_shop_staff m USING(shop_id)
        WHERE s.shop_id=%s AND m.account_id=%s FOR UPDATE OF s""", (shop_id, account)).fetchone()
    if shop is None:
        raise ServiceError(404, "查不到该店铺。")
    if form.sku and not conn.execute("""SELECT 1 FROM public.mall_items JOIN public.mall_orders USING(order_id)
                                        WHERE shop_id=%s AND sku=%s LIMIT 1""", (shop_id, form.sku)).fetchone():
        raise ServiceError(422, "该商品不在本店可选择的范围内。")
    article_key = uuid4() if form.title else None
    if form.revises_id is not None:
        base = conn.execute("""SELECT article_key,topic,sku FROM public.mall_supplement_reviews
            WHERE id=%s AND shop_id=%s AND decision='approved' AND article_key IS NOT NULL""",
            (form.revises_id, shop_id)).fetchone()
        if base is None or not form.title or base["topic"] != form.topic or base["sku"] != form.sku:
            raise ServiceError(422, "请在原条款的主题和商品范围内修订。")
        article_key = base["article_key"]
    if not form.title and (form.topic != "missing" or form.sku):
        raise ServiceError(422, "请填写补充条款标题。")
    if conn.execute("""SELECT 1 FROM public.mall_supplement_reviews
        WHERE shop_id=%s AND topic=%s AND title IS NOT DISTINCT FROM %s AND sku IS NOT DISTINCT FROM %s
          AND note=%s AND decision IS NULL""", (shop_id, form.topic, form.title, form.sku, form.note)).fetchone():
        raise ServiceError(409, "相同说明已在审核队列。")
    return conn.execute("""INSERT INTO public.mall_supplement_reviews
        (shop_id,topic,title,sku,note,author,article_key,revises_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (shop_id, form.topic, form.title, form.sku, form.note, account, article_key, form.revises_id)).fetchone()


def review_supplement(conn, number: int, account: str, decision: str, feedback: str) -> dict:
    """对同店审核串行加锁，较旧草稿不能覆盖已经更新的条款。"""
    row = conn.execute("""SELECT r.* FROM public.mall_supplement_reviews r JOIN public.mall_shops s USING(shop_id)
        WHERE r.id=%s FOR UPDATE OF s,r""", (number,)).fetchone()
    if not row or row["decision"] is not None:
        raise ServiceError(409, "条款已处理或不存在。")
    if decision == "approved" and row["revises_id"] is not None:
        latest = conn.execute("""SELECT id FROM public.mall_supplement_reviews WHERE shop_id=%s AND article_key=%s
            AND decision='approved' ORDER BY reviewed_at DESC,id DESC LIMIT 1""",
            (row["shop_id"], row["article_key"])).fetchone()
        if not latest or latest["id"] != row["revises_id"]:
            raise ServiceError(409, "原条款已有较新版本。请退回草稿，再按最新版本修订。")
    return conn.execute("""UPDATE public.mall_supplement_reviews SET decision=%s,feedback=%s,reviewer=%s,
        reviewed_at=clock_timestamp() WHERE id=%s RETURNING id,reviewed_at""", (decision, feedback, account, number)).fetchone()
