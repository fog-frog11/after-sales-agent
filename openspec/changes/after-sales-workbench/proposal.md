## Why

学习者需要一套能本地运行的售后查询项目，从页面请求一直跟到订单和规则判断。

## What Changes

- 新增本地售后查询台，提供示例订单、文本输入、模拟退货期限和结果依据。
- 支持 DeepSeek 工具调用，缺少密钥时提供本地演示。
- 覆盖退货期限边界、订单号缺失、订单不存在、未签收和模型失败。

## Capabilities

### New Capabilities

- `after-sales-query`：根据模拟订单和店铺规则，查询退货期限并展示依据。

### Modified Capabilities

无。

## Impact

新增 Python 本地 HTTP 入口、网页和测试。沿用 `after_sales.py` 中的订单查询与规则判断。
