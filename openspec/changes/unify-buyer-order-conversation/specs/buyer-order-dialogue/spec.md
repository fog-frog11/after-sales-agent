## ADDED Requirements

### Requirement: 买家可以在同一入口继续询问自己的 Olist 订单
系统 SHALL 在验证订单归属后按对话上下文查询物流、商品明细或人工复核信息。

#### Scenario: 补充订单号后查询多项事实
- **WHEN** 买家同时询问物流和买了多少件，随后补充其所属订单的编号
- **THEN** 系统 SHALL 返回物流结论和商品件数

#### Scenario: 继续询问运费
- **WHEN** 买家在同一会话继续询问运费且未给新编号
- **THEN** 系统 SHALL 使用已核对的订单查询运费

#### Scenario: 查询人工复核
- **WHEN** 买家请求转人工且已有工单
- **THEN** 系统 SHALL 返回已有工单和查看入口

#### Scenario: 非所属买家访问已有会话
- **WHEN** 另一账号提交此会话 ID 或尝试恢复它
- **THEN** 系统 SHALL 拒绝提供内容
