## ADDED Requirements

### Requirement: 客服能够筛选并找回历史工单
系统 SHALL 支持客服按状态和订单号筛选工单，按提交时间倒序分页，并用工单号打开处理详情。

#### Scenario: 找回 Olist 已处理工单
- **WHEN** 客服搜索订单号并筛选已处理工单
- **THEN** 列表 SHALL 返回对应的已处理记录
- **AND** 点开工单 SHALL 显示申请、订单依据、处理说明以及已保存的操作人和时间

#### Scenario: 刷新后恢复
- **WHEN** 客服在已有筛选并打开工单详情的页面刷新
- **THEN** 页面 SHALL 恢复筛选条件和选中的详情

#### Scenario: 买家尝试读取列表
- **WHEN** 买家请求客服工单列表
- **THEN** 系统 SHALL 拒绝访问
