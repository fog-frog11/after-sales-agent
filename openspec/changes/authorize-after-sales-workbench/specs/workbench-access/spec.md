## ADDED Requirements

### Requirement: 区分买家和客服访问权限
售后工作台 SHALL 基于服务器确认的身份限制订单、对话和工单访问。

#### Scenario: 买家查询他人的订单或工单
- **WHEN** 买家提交归属于另一账号的订单号、会话 ID 或工单号
- **THEN** 系统 SHALL 不返回该订单、会话或工单内容

#### Scenario: 未登录或买家访问客服接口
- **WHEN** 未登录的访问者或买家访问客服工单接口
- **THEN** 系统 SHALL 拒绝访问

### Requirement: 保存客服处理记录
系统 SHALL 在客服第一次成功处理工单的同一事务记录操作人和时间。

#### Scenario: 对同一张工单重复提交处理
- **WHEN** 客服对已处理工单再次提交说明
- **THEN** 系统 SHALL 保留原说明及唯一操作记录
