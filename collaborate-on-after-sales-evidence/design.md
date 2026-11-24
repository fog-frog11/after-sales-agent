## Context

买家会话已能追问订单号；客服政策索引和两个只读 MCP 工具均可调用。

## Goals / Non-Goals

- 以已查到的订单为范围询问政策，展示原文与引用。
- 验证模型提出工具请求、程序通过 MCP 执行和客服 Agent 生成回答三个环节。
- 用户可以继续使用工单流程。

## Decisions

- 使用一个 LangGraph 图编排材料整理和客服答复两个节点。
- 材料整理 Agent 使用 DeepSeek Function Calling 请求 `search_shop_policy`；应用校验订单号和问题后通过官方 SDK 客户端调用该工具和 `get_order`。
- 客服 Agent 复用政策引用校验逻辑，使用返回的原文生成说明。
- MCP 客户端沿用项目独立 Python 环境，启动一次 stdio 会话执行两个只读查询。
- 页面展示工具名、适用订单和政策原文。模型调用失败时返回可读错误或已有原文。

## Risks / Trade-offs

- [MCP 子进程或本机索引暂时不可用] → 提示重试，保留原查单结果。
- [模型给出不同订单或政策问题] → 拒绝未经验证的调用参数。
