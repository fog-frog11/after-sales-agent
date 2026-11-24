"""初始化和连接买家对话专用的 PostgreSQL 检查点。"""

from contextlib import asynccontextmanager

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.orders.conversation import ThreadPosition


SCHEMA = "after_sales_dialogue"
CONNECT_OPTIONS = {
    "autocommit": True,
    "prepare_threshold": 0,
    "row_factory": dict_row,
    "connect_timeout": 3,
    "options": f"-c search_path={SCHEMA} -c statement_timeout=8000",
}


def initialize_dialogue_schema(database_url: str) -> None:
    """创建专用 schema，并执行官方检查点的版本化初始化。"""
    with psycopg.connect(database_url, **CONNECT_OPTIONS) as connection:
        connection.execute(psycopg.sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(psycopg.sql.Identifier(SCHEMA)))
        PostgresSaver(connection).setup()


class DialogueCheckpoints:
    """将同步的 PostgreSQL 检查点连接池绑定到 FastAPI 的生命周期。"""

    def __init__(self, database_url: str):
        """构建延迟开启的连接池和 LangGraph 保存器。"""
        self.pool = ConnectionPool(database_url, kwargs=CONNECT_OPTIONS, min_size=0, max_size=4, timeout=5, open=False)
        self.saver = PostgresSaver(self.pool)

    @asynccontextmanager
    async def lifespan(self, _app):
        """在服务启动和停止时分别开启和关闭连接池。"""
        self.pool.open()
        try:
            yield
        finally:
            self.pool.close()

    def list_threads(self, prefix: str, before: str | None, limit: int) -> list[ThreadPosition]:
        """按买家和根命名空间读取各会话最新检查点，包含已有对话。"""
        with self.pool.connection() as connection:
            rows = connection.execute(
                """WITH latest AS (
                    SELECT DISTINCT ON (thread_id) thread_id, checkpoint_id, checkpoint->>'ts' AS updated_at
                      FROM checkpoints
                     WHERE checkpoint_ns = '' AND starts_with(thread_id, %s)
                     ORDER BY thread_id, checkpoint_id DESC
                )
                SELECT thread_id, checkpoint_id, updated_at FROM latest
                 WHERE %s::text IS NULL OR checkpoint_id < %s
                 ORDER BY checkpoint_id DESC LIMIT %s""", (prefix, before, before, limit),
            ).fetchall()
        return [ThreadPosition(row["thread_id"], row["checkpoint_id"], row["updated_at"]) for row in rows]
