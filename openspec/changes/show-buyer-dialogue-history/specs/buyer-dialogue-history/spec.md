## ADDED Requirements

### Requirement: 买家可回看同一会话的物流与售后答复
系统 SHALL 依次展示已提交的问题、追问和每轮返回的事实，并标出可继续追问的当前订单。

#### Scenario: 继续询问运费
- **WHEN** 买家补充订单号获得物流和商品答复，并继续询问运费
- **THEN** 新答复 SHALL 出现在前一轮下方，订单编号持续可见

#### Scenario: 询问人工复核
- **WHEN** 当轮返回已有工单
- **THEN** 该答复 SHALL 显示工单号、状态及查看进度的入口

#### Scenario: 刷新或换单
- **WHEN** 所属买家刷新或选择开始查询另一笔订单
- **THEN** 刷新 SHALL 恢复原有会话，换单 SHALL 清空当前对话视图
