"""通过标准输入和标准输出提供本机售后 MCP 服务。"""

from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from app.integrations.mcp.queries import OrderSummary, OrderTools, PolicyMatches
from app.orders.logistics import JD_ORDER_PATTERN, LogisticsOrder
from app.policies.jd_reference import JD_REFERENCE_COLLECTION
from app.policies.olist_reference import OLIST_REFERENCE_COLLECTION
from app.storage.milvus_policies import MilvusPolicies
from app.storage.postgres_olist import PostgresOlistOrders
from app.storage.postgres_jd import PostgresJDOrders
from app.storage.postgres_orders import PostgresOrders
from app.settings import local_setting


def create_mcp_server(tools: OrderTools) -> FastMCP:
    """把现有售后查询注册为两个可发现的 MCP 工具。"""
    server = FastMCP("after-sales-agent")
    read_only = ToolAnnotations(readOnlyHint=True, openWorldHint=False)

    @server.tool(annotations=read_only)
    def get_order(order_id: Annotated[str, Field(description="当前已确认的订单号")]) -> OrderSummary:
        """查询指定订单的签收日期、所属店铺和适用退货期限。"""
        return tools.get_order(order_id)

    @server.tool(annotations=read_only)
    def search_shop_policy(
        order_id: Annotated[str, Field(description="用来确定规则范围的订单号")],
        question: Annotated[str, Field(description="需要核对的政策问题", min_length=1, max_length=300)],
    ) -> PolicyMatches:
        """按订单的可信范围检索适用规则和出处。"""
        return tools.search_shop_policy(order_id, question)

    return server


def main() -> None:
    """读取本机数据库配置并启动 stdio 服务。"""
    database_url = local_setting("ORDER_DATABASE_URL")
    milvus_url = local_setting("MILVUS_URL")
    if not database_url or not milvus_url:
        raise SystemExit("请先在 .env 配置 ORDER_DATABASE_URL 和 MILVUS_URL。")
    orders = PostgresOrders(database_url)
    logistics = PostgresOlistOrders(database_url)
    jd_orders = PostgresJDOrders(database_url)
    index = MilvusPolicies(milvus_url)
    reference = MilvusPolicies(milvus_url, collection_name=OLIST_REFERENCE_COLLECTION)
    jd_reference = MilvusPolicies(milvus_url, collection_name=JD_REFERENCE_COLLECTION)

    def read_historical(order_id: str) -> LogisticsOrder | None:
        """读与订单号对应的已导入证据。"""
        return jd_orders.get_order(order_id) if JD_ORDER_PATTERN.fullmatch(order_id) else logistics.get_order(order_id)

    tools = OrderTools(orders.get_order, orders.get_policy, index.search, plan_lookup=index.search_evidence,
                       logistics_lookup=read_historical, olist_plan_lookup=reference.search_evidence,
                       jd_plan_lookup=jd_reference.search_jd_reference)
    create_mcp_server(tools).run(transport="stdio")


if __name__ == "__main__":
    main()
