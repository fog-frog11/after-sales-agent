"""初始化买家查单流程使用的 PostgreSQL 检查点。"""

from app.settings import local_setting
from app.storage.dialogue_checkpoints import initialize_dialogue_schema


def main() -> None:
    """从本机配置初始化检查点表，供首次启动或升级时执行。"""
    database_url = local_setting("ORDER_DATABASE_URL")
    if not database_url:
        raise SystemExit("请先在本机配置 ORDER_DATABASE_URL。")
    initialize_dialogue_schema(database_url)
    print("买家对话检查点表已准备好。")


if __name__ == "__main__":
    main()
