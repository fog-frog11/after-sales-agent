## ADDED Requirements

### Requirement: 买家政策问答提供可核对的请求记录
系统 SHALL 为每次进入买家政策问答的请求生成唯一 ID，记录步骤、状态和耗时，并让页面和本地日志使用相同记录。

#### Scenario: 买家查到订单后查看入口
- **WHEN** 买家查到订单且政策问答已启用
- **THEN** 政策输入框下方 SHALL 显示「查看工具调用」，并在提交政策问题前提示何时生成记录

#### Scenario: 成功答复买家
- **WHEN** 买家提交有效政策问题且取得答复
- **THEN** API 和页面 SHALL 提供请求 ID、总耗时及相关步骤的状态和耗时
- **AND** 本地日志 SHALL 保存同一 ID 和调用记录

#### Scenario: MCP 查询超时
- **WHEN** MCP 检索超时
- **THEN** API SHALL 返回 HTTP 503 和该次请求的 ID，并将 MCP 阶段标记为失败
- **AND** 页面 SHALL 展开调用记录，日志 SHALL 保存相同的失败阶段

#### Scenario: 本机日志的字段
- **WHEN** 工作台写入买家政策问答日志
- **THEN** 日志 SHALL 只记录时间、请求 ID、HTTP 状态、耗时和步骤状态
