## ADDED Requirements

### Requirement: 按业务模块组织 Python 实现
项目 SHALL 按订单、工单、政策、外部集成和存储组织 Python 模块。独立运维脚本 SHALL 有明确的模块入口。

#### Scenario: 启动售后工作台
- **WHEN** 用户从项目目录运行 `python -X utf8 -B server.py`
- **THEN** 工作台 SHALL 读取根目录配置和静态资源，继续提供现有 API 和业务页面

#### Scenario: 启动 MCP 子进程
- **WHEN** 工作台或独立验收脚本启动 MCP 服务
- **THEN** MCP SHALL 从项目目录运行，并使用专用 Python 环境提供原有的两个只读工具

#### Scenario: 运行真实业务评测
- **WHEN** 开发者执行 `python -X utf8 -B -m scripts.evaluate_workbench`
- **THEN** 评测 SHALL 核对订单、对话恢复、政策问答和工单，结果继续写入项目的 `.local/evaluations/`
