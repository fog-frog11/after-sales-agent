"""初始化本机店铺售后表并生成独立的演示店铺账号。"""

import json
from secrets import token_urlsafe

import psycopg

from app.access import MERCHANT_ACCOUNT_FILE, MERCHANT_SHOPS, load_credentials
from app.settings import ROOT, local_setting


def main() -> None:
    """使用已有订单数据库初始化店铺队列，多次运行时复用账号。"""
    database_url = local_setting("ORDER_DATABASE_URL")
    if not database_url:
        raise SystemExit("请先配置 ORDER_DATABASE_URL。")
    schema = (ROOT / "sql" / "create_merchant_requests.sql").read_text(encoding="utf-8")
    try:
        with psycopg.connect(database_url, connect_timeout=3) as connection:
            connection.execute("SELECT pg_advisory_xact_lock(508607651984)")
            connection.execute(schema)
    except psycopg.Error:
        raise SystemExit("无法初始化店铺售后表，请确认本机 PostgreSQL 和店铺订单表可用。") from None
    MERCHANT_ACCOUNT_FILE.parent.mkdir(exist_ok=True)
    try:
        with MERCHANT_ACCOUNT_FILE.open("x", encoding="utf-8") as output:
            json.dump({name: token_urlsafe(24) for name in MERCHANT_SHOPS}, output, indent=2)
            output.write("\n")
    except FileExistsError:
        load_credentials(MERCHANT_ACCOUNT_FILE, accounts=MERCHANT_SHOPS)
    print("店铺售后已准备好，店铺账号口令保存在 .local/merchant-accounts.json。")


if __name__ == "__main__":
    main()
