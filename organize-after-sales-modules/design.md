## 目录和依赖

- `app/orders/`、`app/tickets/` 和 `app/policies/` 放业务规则和工作流。`app/integrations/` 放 DeepSeek 和 MCP，`app/storage/` 放 PostgreSQL、Milvus 及持久化检查点。
- `app/api.py` 负责装配依赖和提供现有路由。根目录的 `server.py` 调用同一个入口。
- 从项目根目录运行模块，MCP 两层子进程也由各自明确的 Python 环境和工作目录启动。用项目根路径读取 `.env`、静态文件、日志和评测报告。
- 延用当前数据库、索引和测试样本，整理前后的真实业务评测对照结果。
