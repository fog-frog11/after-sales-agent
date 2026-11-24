## 为什么做

买家工作台需要覆盖已签收、送达延误、未签收超期和取消订单等真实状态。Olist 订单数据提供可复现的测试输入。

## 改动

- 提供 Olist CSV 的校验、分场景抽样、预览和 PostgreSQL 导入。
- 用独立的 Olist 样本表保存查询所需字段，为样本生成稳定的查询编号。
- 买家查单流程识别 Olist 编号，页面展示状态、实际和预计送达日期及历史快照日期。

## Capabilities

### New Capabilities

- `olist-logistics`：导入 Olist 订单样本并查询历史物流状态。
