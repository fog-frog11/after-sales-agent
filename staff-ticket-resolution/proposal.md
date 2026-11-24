## Why

售后人员需要处理已提交的人工工单，买家需要凭工单号查看进度。

## What Changes

- 新增本机客服页面，列出待处理工单并展示当前订单和政策依据。
- 客服保存处理说明，买家在原工作台查看进度。
- PostgreSQL 保存处理说明和时间，禁止重复覆盖处理结果。

## Capabilities

### New Capabilities

- `staff-ticket-resolution`：提供人工工单处理和进度查询。

### Modified Capabilities

无。

## Impact

增加客服页面、三个客服接口、一个进度接口和工单增量 SQL。
