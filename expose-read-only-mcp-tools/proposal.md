## Why

让独立的本机 MCP 客户端调用现有订单和政策查询工具。

## What Changes

- 提供通过 stdio 运行的 MCP 服务。
- 提供结构化的订单查询和按订单店铺检索原文两个工具。
- 编写独立客户端，验证握手、发现工具及实际调用。

## Capabilities

### New Capabilities

- `after-sales-mcp-tools`：以 MCP 协议暴露本机模拟售后查询。

### Modified Capabilities

无。

## Impact

增加 MCP 服务、客户端和可选运行依赖，沿用项目的 PostgreSQL 与 Milvus 数据源。
