"""从独立的 MCP 环境读取订单与条款，并把结构化结果交给网页服务。"""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel

from app.integrations.mcp.queries import OrderSummary, PolicyMatches
from app.settings import ROOT


MCP_PYTHON = ROOT / ".local" / "mcp-env" / "Scripts" / "python.exe"
EVIDENCE_TOOLS = ("get_order", "search_shop_policy")


class McpEvidenceError(Exception):
    """MCP 连接、调用或结果校验失败。"""


class PolicyEvidence(BaseModel):
    """材料整理 Agent 交给客服 Agent 的订单信息和条款原文。"""

    order: OrderSummary
    policies: PolicyMatches


def fetch_mcp_evidence(order_id: str, question: str, python: Path = MCP_PYTHON) -> PolicyEvidence:
    """用项目 MCP 环境运行客户端，读取两条工具结果。"""
    payload = json.dumps({"order_id": order_id, "question": question}, ensure_ascii=False)
    try:
        client = subprocess.run(
            [str(python), "-X", "utf8", "-B", "-m", "app.integrations.mcp.client"],
            input=payload, encoding="utf-8", capture_output=True,
            cwd=ROOT, check=True, timeout=26,
        )
        evidence = PolicyEvidence.model_validate_json(client.stdout)
    except subprocess.TimeoutExpired:
        raise McpEvidenceError("MCP 工具查询超时，请稍后重试。") from None
    except (subprocess.SubprocessError, OSError, ValueError):
        raise McpEvidenceError("暂时无法通过 MCP 读取订单和条款，请稍后重试。") from None
    if evidence.order.order_id != order_id or evidence.policies.order_id != order_id:
        raise McpEvidenceError("MCP 返回的订单与当前查询不一致。")
    return evidence


async def collect_from_mcp(order_id: str, question: str) -> PolicyEvidence:
    """通过 stdio 启动 MCP 服务，握手后并行调用两个只读工具。"""
    from anyio import fail_after
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    server = StdioServerParameters(
        command=sys.executable, args=["-X", "utf8", "-B", "-m", "app.integrations.mcp.server"], cwd=ROOT,
    )
    with fail_after(20):
        async with stdio_client(server) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                order, policies = await asyncio.gather(
                    session.call_tool(EVIDENCE_TOOLS[0], {"order_id": order_id}),
                    session.call_tool(EVIDENCE_TOOLS[1], {"order_id": order_id, "question": question}),
                )
    if order.isError or policies.isError:
        raise McpEvidenceError("MCP 工具返回查询错误。")
    return PolicyEvidence.model_validate({
        "order": order.structuredContent, "policies": policies.structuredContent,
    })


def main() -> None:
    """接收网页进程的请求，向其输出一个 JSON 对象。"""
    request = json.load(sys.stdin)
    evidence = asyncio.run(collect_from_mcp(request["order_id"], request["question"]))
    print(evidence.model_dump_json())


if __name__ == "__main__":
    main()
