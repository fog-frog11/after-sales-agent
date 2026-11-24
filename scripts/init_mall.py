"""初始化商城所需的数据库结构并应用历史数据迁移。"""

from app.settings import ROOT, local_setting
from app.storage.postgres_mall import PostgresMall


def create_schema(connection) -> None:
    """在调用方事务内按依赖顺序创建商城表。"""
    sql_files = (
        "create_mall.sql",
        "add_mall_case_collaboration.sql",
        "add_mall_executable_offers.sql",
        "add_mall_platform_reviews.sql",
        "add_mall_outbound_delivery.sql",
        "add_mall_directory.sql",
        "add_mall_package_returns.sql",
        "add_mall_policy_editions.sql",
        "add_mall_supplement_articles.sql",
    )
    for name in sql_files:
        connection.execute((ROOT / "sql" / name).read_text(encoding="utf-8"))


def main() -> None:
    """读取数据库配置并初始化商城表。"""
    url = local_setting("ORDER_DATABASE_URL")
    if not url:
        raise SystemExit("请先配置 ORDER_DATABASE_URL。")
    with PostgresMall(url).connect() as connection:
        create_schema(connection)
    print("商城数据库结构已准备。")


if __name__ == "__main__":
    main()
