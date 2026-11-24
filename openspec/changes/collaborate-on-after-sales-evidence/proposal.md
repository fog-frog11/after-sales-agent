## Why

买家查到订单后，可以继续问具体售后情况，并看到模型如何调用查询工具取得依据。

## What Changes

- 买家结果区新增政策提问。
- 材料整理 Agent 请求查政策，应用通过本机 MCP 调用订单和政策工具。
- 客服 Agent 用条款原文生成说明，页面展示出处及工具调用记录。

## Capabilities

### New Capabilities

- `buyer-policy-collaboration`：在订单结果上执行双角色的政策问答。

### Modified Capabilities

无。

## Impact

增加买家政策接口、双角色流程和本机 MCP 客户端入口。
