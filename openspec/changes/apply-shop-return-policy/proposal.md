## Why

工作台根据订单所属店铺选择模拟退货政策，并显示判断使用的政策及期限。

## What Changes

- 新增店铺政策表和演示订单 A104。
- 多轮查询在补齐订单号后查订单、查政策并计算退货期限。
- 页面由系统选择政策，结果卡展示店铺和政策名称。

## Capabilities

### New Capabilities

- `shop-return-policy`：给售后会话提供基于订单的退货政策。

### Modified Capabilities

无。

## Impact

涉及数据库增量脚本、LangGraph、查询响应和前端。
