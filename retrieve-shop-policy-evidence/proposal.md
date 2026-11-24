## Why

客服处理工单时需要找到适用于关联订单的条款原文和出处。

## What Changes

- 导入两家模拟店铺的政策条款到 Milvus。
- 客服从工单详情查询政策，看到条款原文和出处。
- DeepSeek 根据检索结果生成带引用的说明。

## Capabilities

### New Capabilities

- `shop-policy-retrieval`：按订单所属店铺检索并解释政策。

### Modified Capabilities

无。

## Impact

新增独立 Milvus 服务、条款索引脚本、政策检索接口及客服界面。
