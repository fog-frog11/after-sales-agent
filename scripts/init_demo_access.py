"""生成本机演示账号，并准备 PostgreSQL 工单关联和审计。"""

import json
from secrets import token_urlsafe

import psycopg

from app.access import ACCOUNT_FILE, ACCOUNT_ROLES, load_credentials
from app.settings import ROOT, local_setting


def main() -> None:
    """准备账号、工单关联和审计表，重复运行时复用已有口令。"""
    database_url = local_setting("ORDER_DATABASE_URL")
    if database_url:
        try:
            with psycopg.connect(database_url, connect_timeout=3) as connection:
                connection.execute("SELECT pg_advisory_xact_lock(508607651983)")
                connection.execute("ALTER TABLE public.support_tickets ADD COLUMN IF NOT EXISTS olist_order_id TEXT")
                for name in ("create_ticket_confirmation_aliases.sql", "add_ticket_handoff.sql", "create_ticket_audit.sql"):
                    connection.execute((ROOT / "sql" / name).read_text(encoding="utf-8"))
        except psycopg.Error:
            raise SystemExit("无法准备工单关联或审计表，请先启动 PostgreSQL 并完成工单表初始化。") from None
    ACCOUNT_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with ACCOUNT_FILE.open("x", encoding="utf-8") as output:
            json.dump({name: token_urlsafe(24) for name in ACCOUNT_ROLES}, output, indent=2)
            output.write("\n")
    except FileExistsError:
        load_credentials()
    print(f"演示账号已准备好。口令保存在 {ACCOUNT_FILE}")


if __name__ == "__main__":
    main()
