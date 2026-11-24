## Why

买家在被追问订单号后刷新页面或遇到服务重启，可以继续原来的查询。

## What Changes

- 用 PostgreSQL 持久化买家查单的 LangGraph 检查点。
- 浏览器在同一标签页保存待补充的会话 ID。
- 提供恢复接口，读取原问题、追问或已经计算的结果。

## Capabilities

### New Capabilities

- `resume-order-dialogue`：恢复等待订单号的售后查询。

### Modified Capabilities

无。

## Impact

增加 PostgreSQL 检查点初始化脚本和恢复接口，涉及买家页面状态及服务启动说明。
