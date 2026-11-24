"""只为已有商城补充条款审核记录增加主题、商品范围和历史版本字段。"""

from app.settings import ROOT, local_setting
from app.storage.postgres_mall import PostgresMall


def main() -> None:
    """执行可重跑的补充条款结构迁移。"""
    with PostgresMall(local_setting("ORDER_DATABASE_URL")).connect() as conn:
        conn.execute((ROOT / "sql/add_mall_supplement_articles.sql").read_text(encoding="utf-8"))
    print("补充条款主题和修订历史结构已就绪。")


if __name__ == "__main__":
    main()
