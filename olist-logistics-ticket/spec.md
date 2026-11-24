## ADDED Requirements

### Requirement: 买家可为 Olist 订单发起人工处理
系统 SHALL 允许订单所属买家为 Olist 订单预览并确认工单，签收状态用于展示订单证据。

#### Scenario: 买家确认预览
- **WHEN** 所属买家为 Olist 订单填写原因并预览
- **THEN** 系统 SHALL 显示对应订单编号及可用的送达和签收日期
- **AND** 重复提交同一确认 ID SHALL 返回原工单

#### Scenario: 另一买家尝试访问
- **WHEN** 其他账号试图预览、确认或查看该工单
- **THEN** 系统 SHALL 拒绝返回此工单的数据

### Requirement: 客服查看物流与商品依据并保存处理结果
客服工单详情 SHALL 提供历史物流事实和可用商品明细，处理结果与客服审计 SHALL 一起持久化。

#### Scenario: 客服处理物流工单
- **WHEN** 客服保存处理说明
- **THEN** 系统 SHALL 保存处理说明、操作人和时间
- **AND** 订单所属买家 SHALL 能查询处理进度和说明
