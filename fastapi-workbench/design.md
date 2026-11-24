## Context

售后应用目前用 Python 标准库接收请求。模型和数据库调用都是同步 I/O，前端使用同源 HTTP 接口。

## Goals / Non-Goals

- 继续使用本机 8765 端口、原有启动命令和网页资源 URL。
- 用 Pydantic 校验查询体，通过 OpenAPI 展示字段和错误状态。
- 本次范围限 HTTP 接口迁移和兼容性验收。

## Decisions

- `create_app()` 创建 FastAPI 实例并注入现有模型函数和订单数据源。
- 查询路由使用同步函数，FastAPI 将阻塞 I/O 调度到线程池。
- Pydantic 校验错误映射到原有的 HTTP 400 与 `message` 字段。
- 显式注册网页和两个静态资源路径。以 Uvicorn 运行，只监听本机。

## Risks / Trade-offs

- [FastAPI 默认校验状态为 422] → 保留客户端使用的 400，并同步修正 OpenAPI 文档。
- [旧版进程仍占用端口] → 先在临时端口验收，确认进程身份后再切换。
