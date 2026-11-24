## Why

让售后系统查询可持久化的订单记录，并验证数据库变更会反映在页面。

## What Changes

- 新增 PostgreSQL 订单表和可重复执行的样本初始化 SQL。
- 配置 `ORDER_DATABASE_URL` 时通过 Psycopg 查 PostgreSQL。
- 数据库查询失败时返回可读的 HTTP 503 错误。

## Capabilities

### New Capabilities

- `postgres-order-lookup`：把数据库中的订单记录提供给售后判断。

### Modified Capabilities

无。

## Impact

涉及订单读取、HTTP 配置、初始化 SQL 和测试。已有模型工具调用、退货规则与前端继续使用原格式。
