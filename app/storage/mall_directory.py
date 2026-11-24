"""按账号范围读取商城店铺、买家和购买订单。"""

from app.access import Account
from app.storage.postgres_mall import PARCEL_STAGE, PostgresMall
from app.tickets.service_requests import ServiceError


PAGE_SIZE = 20
SHOP_SCOPE = """EXISTS (SELECT 1 FROM public.mall_shop_staff m
                       WHERE m.shop_id=o.shop_id AND m.account_id=%s)"""

DIRECTORY_QUERIES = {
    "shops": """SELECT s.shop_id AS code,s.name,p.category,p.city,p.joined_on,p.description,
        (SELECT count(*) FROM public.mall_orders o WHERE o.shop_id=s.shop_id) AS order_count,
        (SELECT string_agg(m.account_id,'、' ORDER BY m.account_id)
            FROM public.mall_shop_staff m WHERE m.shop_id=s.shop_id) AS accounts
        FROM public.mall_shops s LEFT JOIN public.mall_shop_profiles p USING (shop_id)""",
    "buyers": """SELECT b.buyer_id AS code,b.display_name AS name,b.city,b.joined_on,
        (SELECT count(*) FROM public.mall_orders o WHERE o.buyer_id=b.buyer_id) AS order_count
        FROM public.mall_buyer_profiles b""",
}


class MallDirectory:
    """为平台提供档案查询，为所属商家提供订单列表。"""

    def __init__(self, store: PostgresMall):
        self.store = store

    @staticmethod
    def account_names(conn) -> dict[str, str]:
        """用已登记的姓名和店铺名建立登录名映射，避免为多店铺账号擅自指定店铺。"""
        initialized = conn.execute("SELECT to_regclass('public.mall_buyer_profiles') IS NOT NULL AS ready").fetchone()
        if not initialized["ready"]:
            return {}
        rows = conn.execute("""
            SELECT buyer_id AS account_id,btrim(display_name) AS name FROM public.mall_buyer_profiles
            UNION ALL
            SELECT m.account_id,btrim(min(s.name)) FROM public.mall_shop_staff m
            JOIN public.mall_shops s USING (shop_id) GROUP BY m.account_id HAVING count(*)=1
        """).fetchall()
        return {row["account_id"]: row["name"] for row in rows}

    def page(self, account: Account, kind: str, search: str, page: int) -> dict:
        """使用相同搜索条件获取分页总数及当前页。"""
        if account.role != "staff" and not (account.role == "merchant" and kind == "orders"):
            raise ServiceError(403, "当前账号没有此操作权限。")
        search = search.strip()
        if kind == "orders":
            query = f"""SELECT o.order_id AS code,o.buyer_id,o.shop_id,
                COALESCE(b.display_name,o.buyer_id) AS buyer_name,s.name AS shop_name,o.ordered_at,
                totals.line_count,totals.units,totals.goods_paid+c.shipping AS paid_total_with_shipping,
                totals.goods_paid,delivery.stages
                FROM public.mall_orders o JOIN public.mall_shops s USING (shop_id)
                LEFT JOIN public.mall_buyer_profiles b ON b.buyer_id=o.buyer_id
                LEFT JOIN public.mall_order_checkout c USING (order_id)
                CROSS JOIN LATERAL (
                    SELECT count(*) AS line_count,sum(quantity) AS units,sum(quantity*paid_unit) AS goods_paid
                    FROM public.mall_items WHERE order_id=o.order_id
                ) totals
                CROSS JOIN LATERAL (
                    SELECT array_agg(DISTINCT current_stage.stage ORDER BY current_stage.stage) AS stages
                    FROM public.mall_packages p CROSS JOIN LATERAL (SELECT {PARCEL_STAGE} AS stage) current_stage
                    WHERE p.order_id=o.order_id
                ) delivery"""
            scope, args = self._order_scope(account)
            active = "o.shop_id NOT IN ('pine-digital','cotton-home')"
            if account.role == "staff":
                active += " OR o.order_id=%s"
                args = (*args, search.upper())
            query += f" WHERE {scope} AND ({active})"
            fields = "code,buyer_id,buyer_name,shop_id,shop_name"
            ordering = "ordered_at DESC,code DESC"
        else:
            query, args = DIRECTORY_QUERIES[kind], ()
            query += (" WHERE s.shop_id NOT IN ('pine-digital','cotton-home')" if kind == "shops"
                      else " WHERE b.buyer_id NOT IN ('buyer-a','buyer-b')")
            fields = "code,name,city" + (",category,accounts" if kind == "shops" else "")
            ordering = "code"

        sql = f"FROM ({query}) records WHERE strpos(lower(concat_ws(' ',{fields})),lower(%s))>0"
        params = (*args, search)
        if kind == "orders":
            sql += """ OR EXISTS (SELECT 1 FROM public.mall_items item WHERE item.order_id=records.code
                AND strpos(lower(concat_ws(' ',item.title,item.variant)),lower(%s))>0)"""
            params = (*params, search)
        with self.store.connect() as conn:
            count = conn.execute("SELECT count(*) AS total " + sql, params).fetchone()["total"]
            rows = conn.execute(f"SELECT * {sql} ORDER BY {ordering} LIMIT %s OFFSET %s",
                                (*params, PAGE_SIZE, (page - 1) * PAGE_SIZE)).fetchall()
            counts = (conn.execute("""SELECT
                (SELECT count(*) FROM public.mall_shops WHERE shop_id NOT IN ('pine-digital','cotton-home')) AS shops,
                (SELECT count(*) FROM public.mall_buyer_profiles WHERE buyer_id NOT IN ('buyer-a','buyer-b')) AS buyers,
                (SELECT count(*) FROM public.mall_orders WHERE shop_id NOT IN ('pine-digital','cotton-home')) AS orders
            """).fetchone() if account.role == "staff" else None)
        return {"rows": rows, "total": count, "page": page, "page_size": PAGE_SIZE, "counts": counts}

    def order(self, account: Account, order_id: str) -> dict:
        """通过所属店铺判断权限，再取完整订单；平台与店铺视图不返回收货人信息。"""
        scope, args = self._order_scope(account)
        with self.store.connect() as conn:
            order = conn.execute(f"""SELECT o.buyer_id,COALESCE(b.display_name,o.buyer_id) AS buyer_name
                FROM public.mall_orders o LEFT JOIN public.mall_buyer_profiles b ON b.buyer_id=o.buyer_id
                WHERE o.order_id=%s AND {scope}""", (order_id, *args)).fetchone()
            if order is None:
                raise ServiceError(404, "查不到这笔订单。")
            result = self.store.order_details(conn, account, order["buyer_id"], order_id)
        return {**result, **order}

    @staticmethod
    def _order_scope(account: Account) -> tuple[str, tuple]:
        """店铺账号只能查授权店铺；平台账号可查商城订单目录。"""
        if account.role == "staff":
            return "TRUE", ()
        if account.role == "merchant":
            return SHOP_SCOPE, (account.account_id,)
        raise ServiceError(403, "当前账号没有此操作权限。")
