## Why

用户对售后查询结果有异议时，可以确认订单和原因，再提交人工处理工单。

## What Changes

- 工作台提供工单预览和确认。
- LangGraph 等待确认，确认后由 PostgreSQL 保存工单。
- 重复确认返回原工单号。
- 补齐源码文件、类和函数的中文说明。

## Capabilities

### New Capabilities

- `human-support-ticket`：对已查到的订单提交人工处理工单。

### Modified Capabilities

无。

## Impact

增加工单表、工单流程和 API。工作台结果区增加工单入口。
