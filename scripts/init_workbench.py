"""配置或初始化售后工作台，重复运行时复用已有账号和业务记录。"""

import argparse
import os
from pathlib import Path
from secrets import token_urlsafe


def prepare_config() -> None:
    """为新工作区生成本机配置，已有配置无需改写。"""
    root = Path(__file__).resolve().parents[1]
    port = int(os.environ.get("AFTER_SALES_POSTGRES_PORT") or "55432")
    if not 1 <= port <= 65535:
        raise SystemExit("PostgreSQL 端口必须在 1 到 65535 之间。")
    password = token_urlsafe(30)
    try:
        with (root / ".env").open("x", encoding="utf-8") as config:
            config.write("DEEPSEEK_API_KEY=\nMILVUS_URL=\n"
                         f"AFTER_SALES_POSTGRES_PORT={port}\n"
                         f"AFTER_SALES_POSTGRES_PASSWORD={password}\n"
                         f"ORDER_DATABASE_URL=postgresql://after_sales:{password}@127.0.0.1:{port}/after_sales\n"
                         "MALL_PERSISTENT_DENSE=1\nMALL_WINDOW_RERANK=1\n")
    except FileExistsError:
        print("沿用现有 .env。")
    else:
        print("已生成 .env。")


def main() -> None:
    """按数据库依赖顺序准备账号、表结构、档案和作品集案例。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-config", action="store_true")
    if parser.parse_args().prepare_config:
        prepare_config()
        return
    from app.settings import local_setting
    from scripts import init_demo_access, init_mall, init_mall_directory, merge_mall_logins

    if not local_setting("ORDER_DATABASE_URL"):
        parser.error("请先配置 ORDER_DATABASE_URL 并启动 PostgreSQL。")
    # 独立空库不经过 Docker 的首次卷初始化，也应能按同一命令启动。
    import psycopg
    root=Path(__file__).resolve().parents[1]
    with psycopg.connect(local_setting('ORDER_DATABASE_URL'),autocommit=True) as conn:
        if conn.execute("SELECT to_regclass('public.support_tickets')").fetchone()[0] is None:
            for name in ('init_orders.sql','apply_shop_policies.sql','create_support_tickets.sql','add_ticket_resolution.sql'):
                conn.execute((root/'sql'/name).read_text('utf-8'))
    for step in (init_demo_access, init_mall, init_mall_directory, merge_mall_logins):
        step.main()


if __name__ == "__main__":
    main()
