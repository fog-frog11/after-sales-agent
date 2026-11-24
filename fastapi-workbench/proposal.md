## Why

用 FastAPI 管理售后工作台的请求校验、路由和接口文档，为后续扩展对话流程提供清楚的入口。

## What Changes

- 用 FastAPI 提供原有页面和查询接口。
- 新增本机 OpenAPI 文档与可交互的 Swagger UI。
- 保留原有请求、响应和错误格式，覆盖回归测试。

## Capabilities

### New Capabilities

- `fastapi-workbench`：以 FastAPI 提供本机售后工作台的 HTTP 接口和接口文档。

### Modified Capabilities

无。

## Impact

涉及 `server.py`、测试、依赖和启动说明。已有模型配置和数据库地址继续沿用。
