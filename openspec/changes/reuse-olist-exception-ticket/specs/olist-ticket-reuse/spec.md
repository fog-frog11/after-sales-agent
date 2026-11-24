## ADDED Requirements

### Requirement: 同一 Olist 历史物流异常复用已记录工单
重复提交同一历史物流异常 SHALL 返回已有工单供所属买家查看。

#### Scenario: 已处理后再申请
- **WHEN** 买家再次为已处理的 Olist 历史异常申请人工复核
- **THEN** 预览 SHALL 给出已有工单号和处理状态，不发放新的确认 ID

#### Scenario: 双预览分别确认
- **WHEN** 同一订单有两个确认 ID 并被先后或并行确认
- **THEN** 两次确认 SHALL 返回同一张工单，两个确认 ID 都可重试
