"""生成商城中文名、登录账号和口令文件位置的索引。"""

import json
from app.access import build_account_book
from app.mall_accounts import ACCOUNT_BOOK_FILE
from app.settings import local_setting
from app.storage.mall_directory import MallDirectory
from app.storage.postgres_mall import PostgresMall


def main() -> None:
    """从商城档案生成索引，不读取口令文件。"""
    database = local_setting("ORDER_DATABASE_URL")
    if not database:
        raise SystemExit("请先配置 ORDER_DATABASE_URL。")
    with PostgresMall(database).connect() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        names = MallDirectory.account_names(conn)
    book = build_account_book(names)
    payload = json.dumps(book, ensure_ascii=False, indent=2) + "\n"
    ACCOUNT_BOOK_FILE.write_text(payload, encoding="utf-8")
    summary = "、".join(f"{len(rows)} 个{group}账号" for group, rows in book.items())
    print(f"已生成 {ACCOUNT_BOOK_FILE.name}，含 {summary}。")


if __name__ == "__main__":
    main()
