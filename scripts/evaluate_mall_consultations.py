"""逐件核对商城商品咨询与原购物记录、下单条款，不保存聊天或调用外部模型。"""

import json
from contextlib import nullcontext

from app.settings import local_setting
from app.storage.postgres_mall import PostgresMall
from app.mall.rules import MallQuestion


class SnapshotMall(PostgresMall):
    """让所有咨询共享一份 PostgreSQL 强制只读的一致性快照。"""

    def __init__(self, database: str, connection):
        """复用普通商城咨询图，只有连接来源切换到评测事务。"""
        super().__init__(database)
        self.connection = connection

    def connect(self):
        """借用事务，咨询结束后仍由外层管理连接。"""
        return nullcontext(self.connection)


def evaluate(database: str, *, snapshot=None) -> dict:
    """使用只读快照或验收事务中的连接，对照购买记录和店铺发布记录。"""
    failures = []
    cited = 0
    quotes = 0
    current_checks = 0
    commitments = set()
    form = MallQuestion(question="这件商品什么规格，买了几件，每件实付多少？买错了退货邮费咋算？", record=False)
    with (nullcontext(snapshot) if snapshot is not None else PostgresMall(database).connect()) as conn:
        if snapshot is None:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        rows = conn.execute("""
            SELECT o.order_id, o.buyer_id, o.shop_id, o.terms_version, i.item_no, i.title, i.variant,
                   i.quantity, i.paid_unit, t.title AS terms_title, t.return_days, t.return_postage
            FROM public.mall_orders o JOIN public.mall_items i USING (order_id)
            JOIN public.mall_terms t ON t.shop_id=o.shop_id AND t.version=o.terms_version
            WHERE o.order_id BETWEEN 'CN-300001' AND 'CN-300300' ORDER BY o.order_id, i.item_no
        """).fetchall()
        if not rows:
            raise ValueError("购物档案中没有可核对的订单")
        store = SnapshotMall(database, conn)
        published = conn.execute("""SELECT DISTINCT ON (t.shop_id) t.*, e.item_credit,e.guidance,e.platform_version,
                f.return_days AS min_returns,f.fault_days AS min_fault,f.warranty_days AS min_warranty,
                f.reply_hours AS max_hours,f.max_postage
            FROM public.mall_terms t JOIN public.mall_shop_policy_editions e USING(shop_id,version)
            JOIN public.mall_platform_policies f ON f.version=e.platform_version
            WHERE t.effective_on<=(now() AT TIME ZONE 'Asia/Shanghai')::date ORDER BY t.shop_id,t.effective_on DESC""").fetchall()
        sample_by_shop = {row["shop_id"]: row for row in rows}
        for policy in published:
            row = sample_by_shop.get(policy["shop_id"])
            if not row:
                continue
            current_checks += 1
            commitments.add(tuple(policy[k] for k in ("return_days", "fault_days", "warranty_days", "reply_hours",
                                                       "return_postage", "seller_advances", "item_credit", "guidance")))
            reply = store.consult(row["buyer_id"], row["order_id"], row["item_no"],
                                  MallQuestion(question="这家店现在的退换期限是几天？", record=False))
            if (f"店铺第 {policy['version']} 版" not in reply["answer"] or f"签收后 {policy['return_days']} 天" not in reply["answer"]
                or reply["reference"]["terms_version"] != row["terms_version"] or policy["return_days"] < policy["min_returns"]
                or policy["fault_days"] < policy["min_fault"] or policy["warranty_days"] < policy["min_warranty"]
                or policy["reply_hours"] > policy["max_hours"] or policy["return_postage"] > policy["max_postage"]):
                failures.append({"shop": policy["shop_id"], "check": "店铺现行版、平台底线或旧单版本"})
        if current_checks != len(sample_by_shop):
            failures.append({"check": "部分店铺缺少独立发布的现行版"})
        for row in rows:
            result = store.consult(row["buyer_id"], row["order_id"], row["item_no"], form)
            ref = result["reference"]
            details = (row["title"], row["variant"], f"买了 {row['quantity']} 件", f"每件实付 ¥{row['paid_unit']:.2f}")
            checks = {
                "订单、商品或店铺": all(ref[key] == row[key] for key in ("order_id", "item_no", "shop_id", "terms_version")),
                "条款标题及版本": result["terms_title"] == row["terms_title"] and result["terms_version"] == row["terms_version"],
                "购买明细": all(value in result["answer"] for value in details),
            }
            for clause in result["evidence"]:
                if clause["id"] == "freight":
                    cited += 1
                    checks["引用的寄回报价"] = f"¥{row['return_postage']:.2f}" in clause["text"]
                elif clause["id"] == "window":
                    checks["引用的退换期限"] = f"签收后 {row['return_days']} 天" in clause["text"]
            if quote := result["freight"]:
                quotes += 1
                checks["运费明细"] = (quote["fee"] == row["return_postage"]
                                   and 0 <= quote["credit"] <= quote["fee"]
                                   and quote["credit"] + quote["due"] == quote["fee"])
            for check, passed in checks.items():
                if not passed:
                    failures.append({"order": row["order_id"], "item": row["item_no"], "check": check})
    return {"orders": len({r["order_id"] for r in rows}), "shops": len({r["shop_id"] for r in rows}),
            "buyers": len({r["buyer_id"] for r in rows}), "versions": sorted({r["terms_version"] for r in rows}),
            "item_questions": len(rows), "freight_citations": cited, "eligible_quotes": quotes,
            "published_shops_checked": current_checks, "distinct_current_commitments": len(commitments),
            "failed_checks": len(failures), "first_errors": failures[:10]}


if __name__ == "__main__":
    report = evaluate(local_setting("ORDER_DATABASE_URL"))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(int(bool(report["failed_checks"])))
