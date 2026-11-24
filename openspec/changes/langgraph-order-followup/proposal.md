## Why

用户可以先提出售后问题，再补发订单号。后端需要在两次请求之间保存问题和退货期限。

## What Changes

- 新增 `POST /api/conversation` 支持追问和恢复。
- 页面在等待订单号时保留当前对话，并提供开始新查询的操作。
- 保留单轮 `POST /api/query` 和现有查单逻辑。

## Capabilities

### New Capabilities

- `order-followup`：在同一个售后对话中补齐订单号并得到原问题的结论。

### Modified Capabilities

无。

## Impact

新增 LangGraph 流程、对话路由和页面状态。继续使用当前 DeepSeek、订单查询和退货判断。
