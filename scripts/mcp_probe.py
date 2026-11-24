"""作为独立 MCP 客户端，验收握手、工具发现和真实售后查询。"""

import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.settings import ROOT


async def main() -> None:
    """启动本机 stdio 服务并验证两家店铺的查询结果。"""
    server = StdioServerParameters(
        command=sys.executable,
        args=["-X", "utf8", "-B", "-m", "app.integrations.mcp.server"],
        cwd=ROOT,
    )
    async with stdio_client(server) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            handshake = await session.initialize()
            discovered = (await session.list_tools()).tools
            print("MCP protocol:", handshake.protocolVersion)
            print("Tools:", ", ".join(tool.name for tool in discovered))
            assert {tool.name for tool in discovered} == {"get_order", "search_shop_policy"}
            assert all(tool.outputSchema and tool.annotations.readOnlyHint for tool in discovered)

            order = await session.call_tool("get_order", {"order_id": "A102"})
            assert not order.isError, order.content
            summary = order.structuredContent
            assert summary is not None
            assert summary["shop_name"] == "青禾生活馆" and summary["return_window_days"] == 7
            print(json.dumps(summary, ensure_ascii=False))

            for order_id, expected in (("A102", "QH-03"), ("A104", "QM-03")):
                matches = await session.call_tool(
                    "search_shop_policy", {"order_id": order_id, "question": "包装破损怎么处理？"},
                )
                assert not matches.isError, matches.content
                found = matches.structuredContent
                assert found is not None
                assert [article["article_id"] for article in found["sources"]] == [expected], found
                print(json.dumps(found, ensure_ascii=False))

            missing = await session.call_tool("get_order", {"order_id": "A999"})
            assert missing.isError
            print("Unknown order returns a tool error.")


if __name__ == "__main__":
    asyncio.run(main())
