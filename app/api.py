"""用 FastAPI 提供本机售后工作台和查询接口。"""

import asyncio
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from functools import partial
from threading import Event
from typing import Annotated, Literal
from uuid import UUID

import psycopg
import uvicorn
from fastapi import FastAPI, Path as PathParameter, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from langgraph.checkpoint.base import BaseCheckpointSaver
from pydantic import BaseModel, Field, StringConstraints, field_validator
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import Lifespan

from app.mall_accounts import DIRECTORY_ACCOUNT_FILE, DIRECTORY_ROLES
from app.access import (
    ACCOUNT_ROLES, MERCHANT_ACCOUNT_FILE, MERCHANT_SHOPS, OLIST_EXAMPLES, SESSION_COOKIE, SESSION_SECONDS,
    Account, DemoAccess, load_credentials, order_owner,
)
from app.orders.rules import DEMO_DATE, Order, OrderLookup, evaluate_return, get_order, get_policy
from app.integrations.deepseek import DeepSeekTool, ModelError, deepseek_tool, query_after_sales
from app.policies.assistance import BuyerPolicyWorkflow
from app.orders.conversation import (
    ConversationConflict, ConversationOrderMissing, OrderCard, OrderConversation, ThreadList, review_order,
)
from app.orders.catalog import OlistOrderList, OrderCatalog, OrderKind
from app.orders.dialogue_tools import OrderToolPlan, OrderToolRouter
from app.orders.logistics import HISTORICAL_ORDER_PATTERN, JD_ORDER_PATTERN, LogisticsLookup, LogisticsOrder, ReceiptStatusFilter, logistics_result
from app.orders.olist_items import ItemsLookup, answer_items
from app.storage.dialogue_checkpoints import DialogueCheckpoints
from app.integrations.mcp.client import McpEvidenceError
from app.integrations.mcp.queries import OrderSummary
from app.storage.milvus_policies import MilvusPolicies
from app.policies.search import PolicyAssistant, PolicyIndexError, deepseek_aspect_writer, deepseek_policy_writer
from app.policies.jd_reference import jd_policy_scope, label_jd_reference
from app.policies.olist_reference import OLIST_REFERENCE_SCOPE, label_olist_reference
from app.policies.trace import PolicyRequestTrace, PolicyTraceRecord, PolicyTraceSnapshot, write_policy_trace
from app.policies.verification import deepseek_policy_reviewer
from app.storage.postgres_orders import OrderStoreError, PostgresOrders
from app.storage.postgres_service_requests import PostgresServiceRequests
from app.storage.postgres_merchant_requests import PostgresMerchantRequests
from app.storage.postgres_tickets import PostgresTickets
from app.settings import ROOT, local_setting
from app.tickets.handoff import TicketHandoff, TicketHandoffUnavailable
from app.tickets.service_api import service_router
from app.mall.api import MALL_HOME, mall_router
from app.mall.photos import MAX_PHOTO_JSON
from app.storage.postgres_mall import PostgresMall
from app.storage.mall_directory import MallDirectory
from app.mall.agent import deepseek_mall_planner, deepseek_mall_writer
from app.mall.read_loop import evidence_decider, business_decider
from app.mall.grounded_reply import grounded_reviewer, grounded_writer
from app.policies.mall_retrieval import MallRetrieval
from app.tickets.service_requests import ServiceError, ServiceReason, assess_return_freight
from app.tickets.workflow import (
    SupportTicketFlow, TicketDetailRecord, TicketOrderNotFound, TicketPreviewUnavailable,
    TicketStatusFilter, TicketStore, TicketStoreError,
)


WEB = ROOT / "web"
INVALID_QUERY = "请输入 1 至 300 个字符，并选择 7 天或 15 天规则。"
INVALID_CONVERSATION = "请输入 1 至 300 个字符，并检查会话 ID。"
INVALID_TICKET = "请核对订单号、工单原因或确认 ID。工单原因需要 1 至 300 个字符。"
INVALID_RESOLUTION = "请核对工单号，并填写 1 至 300 个字符的处理说明。"
INVALID_BUYER_POLICY = "请核对订单号，并输入 1 至 300 个字符的政策问题。"
INVALID_OLIST_ITEMS = "请核对 Olist 样本编号，并输入 1 至 300 个字符的商品问题。"
OLIST_ITEMS_PATH = re.compile(r"/api/olist/orders/[^/]+/items")
TICKET_ORDER_PATTERN = r"^(?:[aA][0-9]{3}|[oO][lL]-[0-9a-fA-F]{10}|[jJ][dD]-[0-9a-fA-F]{12})$"
BODY_ERRORS = {
    "/api/query": INVALID_QUERY,
    "/api/conversation": INVALID_CONVERSATION,
    "/api/conversation/stream": INVALID_CONVERSATION,
    "/api/conversation/open-order": "请核对订单号和会话 ID。",
    "/api/conversation/prepare-order": "请核对订单号和会话 ID。",
    "/api/conversation/share-order": "请核对订单号和会话 ID。",
    "/api/tickets/preview": INVALID_TICKET,
    "/api/tickets/confirm": INVALID_TICKET,
    "/api/session/login": "请填写账号和登录口令。",
}
Mode = Literal["demo", "deepseek"]
ROLE_HOME = {"buyer": "/orders", "staff": "/staff", "merchant": "/merchant"}
TicketNumber = Annotated[int, PathParameter(gt=0, le=2**63 - 1)]
OrderNumber = Annotated[str, PathParameter(pattern=r"^[aA][0-9]{3}$")]
OlistOrderNumber = Annotated[str, PathParameter(pattern=r"^[oO][lL]-[0-9a-fA-F]{10}$")]
CatalogOrderNumber = Annotated[str, PathParameter(pattern=TICKET_ORDER_PATTERN)]
CatalogOrderFilter = Annotated[str | None, Query(pattern=TICKET_ORDER_PATTERN)]
BuyerOrderSearch = Annotated[str | None, Query(
    pattern=r"^(?:[aA][0-9]{0,3}|[oO][lL](?:-[0-9a-fA-F]{0,10})?|[jJ][dD](?:-[0-9a-fA-F]{0,12})?)$",
    max_length=15, description="完整订单号或订单号前缀",
)]
CatalogPageSize = Annotated[int, Query(ge=1, le=50)]


class QuestionRequest(BaseModel):
    """定义问答接口共用的自然语言输入。"""

    question: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=300)]


class QueryRequest(QuestionRequest):
    """单轮比较接口，允许指定 7 天或 15 天演示期限。"""

    return_window_days: Literal[7, 15] = 7

    @field_validator("return_window_days", mode="before")
    @classmethod
    def require_integer_days(cls, value: object) -> object:
        """拒绝字符串和布尔值，保留整数期限的接口约定。"""
        if type(value) is not int:
            raise ValueError("退货期限应为整数")
        return value


class ConfigResponse(BaseModel):
    """页面初始化时读取的模型模式、演示日期和功能状态。"""

    mode: Mode
    demo_date: date
    order_source: Literal["memory", "postgresql"]
    ticket_enabled: bool
    merchant_enabled: bool
    policy_search_enabled: bool
    olist_policy_search_enabled: bool = False
    jd_policy_search_enabled: bool = False
    buyer_policy_enabled: bool
    access_enabled: bool
    logistics_examples: list[str]
    mall_enabled: bool = False


class LoginRequest(BaseModel):
    """接收本机演示账号和登录口令。"""

    account_id: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=40)]
    secret: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=160)]


class ConversationRequest(QuestionRequest):
    """接收首轮问题或带会话 ID 的后续回复。"""

    conversation_id: UUID | None = None
    active_order_id: Annotated[str, StringConstraints(strict=True, pattern=TICKET_ORDER_PATTERN)] | None = None


class OpenConversationRequest(BaseModel):
    """从订单详情打开对话，不提交用户消息。"""

    order_id: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, pattern=TICKET_ORDER_PATTERN)]
    conversation_id: UUID | None = None


class LogisticsFacts(BaseModel):
    """说明物流结论使用的数据来源、快照日期和订单日期。"""

    source: Literal["Olist", "京东 M&SOM"]
    snapshot_on: date
    purchased_on: date
    estimated_on: date | None
    delivered_on: date | None


class OlistItemResponse(BaseModel):
    """保留源品类及中文标签，原始金额继续使用 Decimal。"""

    item_no: int
    product_id: str
    category: str | None
    category_label: str | None = None
    price: Decimal
    freight: Decimal | None
    quantity: int = 1


class OlistItemsResponse(BaseModel):
    """返回带货币单位的商品明细答复，金额按十进制传回。"""

    status: Literal["found", "no_items"]
    order_id: str
    currency: Literal["BRL", "CNY"]
    answer: str
    quantity: int | None
    items_total: Decimal | None
    freight_total: Decimal | None
    recorded_total: Decimal | None
    items: list[OlistItemResponse]


class CatalogOrderResponse(BaseModel):
    """展示列表中可查看的订单归属、来源和状态。"""

    order_id: str
    buyer_id: str
    kind: Literal["shop", "olist", "jd"]
    status: str
    purchased_on: date | None
    signed_on: date | None
    seller_label: str | None = None


class CatalogPageResponse(BaseModel):
    """按订单号浏览的列表页与下一页游标。"""

    orders: list[CatalogOrderResponse]
    next_after: str | None


class CatalogPolicyResponse(BaseModel):
    """展示店铺和本笔订单关联的退货期限。"""

    shop_name: str
    policy_name: str
    return_window_days: int


class CatalogDetailResponse(CatalogOrderResponse):
    """已确认可访问订单的完整可用详情。"""

    policy: CatalogPolicyResponse | None = None
    logistics: LogisticsFacts | None = None
    items: OlistItemsResponse | None = None
    conversation_scope: str


class OrderResult(BaseModel):
    """售后判断和页面所需的签收日期、期限及政策依据。"""

    status: str
    message: str
    order_id: str | None = None
    signed_on: date | None = None
    days_since_delivery: int | None = None
    return_window_days: int | None = None
    shop_name: str | None = None
    policy_name: str | None = None


class HumanReviewAction(BaseModel):
    """提示如何继续人工复核，并标明已有工单和状态。"""

    status: Literal["existing", "preview", "merchant_first", "unavailable"]
    message: str
    ticket_no: str | None = None
    ticket_status: str | None = None


class PolicySourceResponse(BaseModel):
    """返回一条可以直接核对的条款原文。"""

    article_id: str
    title: str
    source: str
    text: str


class PolicyAnswerItemResponse(BaseModel):
    """返回单个诉求的答复状态和可核对的引用。"""

    question: str
    status: Literal["answered", "needs_review"]
    answer: str
    citations: list[str]


class PolicySearchResponse(BaseModel):
    """包含模型说明、检索状态和对应的出处列表。"""

    status: Literal["answered", "no_matches", "evidence_only"]
    answer: str
    sources: list[PolicySourceResponse]
    items: list[PolicyAnswerItemResponse] = Field(default_factory=list)


class ConversationPolicyResponse(PolicySearchResponse):
    """将当轮条款答复和工具追踪保存进对话。"""

    status: Literal["answered", "no_matches", "evidence_only", "unavailable"]
    trace: PolicyTraceSnapshot | None = None
    function_call: str | None = None
    mcp_tools: list[str] = Field(default_factory=list)


class ConversationOrderResult(OrderResult):
    """在查单对话结果中同时支持历史物流依据。"""

    logistics: LogisticsFacts | None = None
    olist_items: OlistItemsResponse | None = None
    human_review: HumanReviewAction | None = None
    tool_plan: OrderToolPlan | None = None
    policy_assistance: ConversationPolicyResponse | None = None
    return_freight: dict | None = None
    service_progress: dict | None = None
    show_order_summary: bool = True
    order_detail_requested: bool = False
    items_message: str | None = None


class QueryResponse(BaseModel):
    """包装一次售后判断结果及使用的模型模式。"""

    mode: Mode
    result: OrderResult


class ErrorResponse(BaseModel):
    """统一向前端返回可显示的错误说明。"""

    message: str


class ConversationTurnResponse(BaseModel):
    """向对话界面返回同一轮的问题与答复。"""

    question: str
    result: ConversationOrderResult
    order_card: OrderCard | None = None


class ConversationResponse(QueryResponse):
    """在查询结果之外返回会话 ID 和是否继续追问。"""

    conversation_id: UUID
    scope: str | None = None
    awaiting_order_id: bool
    can_continue: bool = False
    result: ConversationOrderResult
    turns: list[ConversationTurnResponse]
    draft_order: OrderCard | None = None


class ConversationRestoreResponse(ConversationResponse):
    """在恢复会话时返回首轮问题和可重试的订单号回复。"""

    question: str
    retry_reply: str | None = None


class BuyerConversationSummary(BaseModel):
    """显示当前买家可打开的会话预览。"""

    conversation_id: UUID
    title: str
    order_id: str | None
    preview: str
    updated_at: datetime
    awaiting_order_id: bool


class BuyerConversationPage(BaseModel):
    """保存会话概要和下一页游标。"""

    conversations: list[BuyerConversationSummary]
    next_before: UUID | None


class TicketPreviewRequest(BaseModel):
    """接收用户准备提交的订单号和人工复核原因。"""

    order_id: Annotated[str, StringConstraints(
        strict=True, strip_whitespace=True, pattern=TICKET_ORDER_PATTERN,
    )]
    reason: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=300)]
    conversation_id: UUID | None = None


class TicketPreviewResponse(BaseModel):
    """返回供用户核对的工单内容和对应确认 ID。"""

    confirmation_id: UUID | None = None
    order_id: str
    reason: str
    logistics: LogisticsFacts | None = None
    existing_ticket_no: str | None = None
    existing_ticket_status: str | None = None
    handoff: TicketHandoff | None = None


class TicketConfirmRequest(BaseModel):
    """只接收确认 ID，提交内容由后端检查点提供。"""

    confirmation_id: UUID


class TicketResponse(BaseModel):
    """返回已创建的工单编号、提交内容和当前状态。"""

    ticket_no: int
    order_id: str
    reason: str
    status: str


class TicketResolutionRequest(BaseModel):
    """接收客服确认保存的处理说明。"""

    resolution: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=300)]


class MerchantRequestInput(BaseModel):
    """接收买家准备交给店铺的申请说明。"""

    reason: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=300)]


class MerchantRequestResponse(BaseModel):
    """展示所申请的订单以及店铺处理结果。"""

    order_id: str
    reason: str
    submitted_at: datetime
    resolution: str | None
    resolved_at: datetime | None


class MerchantRequestPage(BaseModel):
    """返回独立店铺队列的一页。"""

    requests: list[MerchantRequestResponse]
    next_after: str | None
    shop_name: str | None


class TicketProgressResponse(BaseModel):
    """向买家返回工单状态和处理结果，供工作台显示进度。"""

    ticket_no: int
    status: str
    resolution: str | None = None
    resolved_at: datetime | None = None


class OrderPlatformProgress(BaseModel):
    """向买家展示同一订单的平台工单和处理结果。"""

    ticket_no: str
    status: Literal["pending", "resolved"]
    reason: str
    created_at: datetime
    resolution: str | None = None
    resolved_at: datetime | None = None


class OrderAfterSalesResponse(BaseModel):
    """汇总订单详情所需的店铺和平台进度。"""

    merchant: MerchantRequestResponse | None
    platform: OrderPlatformProgress | None
    services: list[dict] | None = None


class StaffTicketResponse(TicketProgressResponse):
    """向客服返回工单原始内容及当前订单和政策信息。"""

    order_id: str
    reason: str
    evidence: ConversationOrderResult
    merchant_request: MerchantRequestResponse | None = None
    olist_items: OlistItemsResponse | None = None
    resolved_by: str | None = None
    handoff: TicketHandoff | None = None


class StaffTicketListItem(BaseModel):
    """客服查询结果用文本编号向前端传递完整的整数精度。"""

    ticket_no: str
    order_id: str
    reason: str
    status: str
    created_at: datetime


class StaffTicketPage(BaseModel):
    """一页匹配的工单及下一页的边界编号。"""

    tickets: list[StaffTicketListItem]
    next_before: str | None


class BuyerPolicyResponse(PolicySearchResponse):
    """在有出处的答复之外，返回订单和本次实际调用的工具。"""

    order: OrderSummary
    function_call: str
    mcp_tools: list[str]
    trace: PolicyTraceSnapshot


class BuyerPolicyErrorResponse(ErrorResponse):
    """为进入问答流程后发生的错误附上同一条追踪记录。"""

    trace: PolicyTraceSnapshot


QUERY_ERRORS = {
    400: {"model": ErrorResponse, "description": "请求无效"},
    502: {"model": ErrorResponse, "description": "模型调用失败"},
    503: {"model": ErrorResponse, "description": "订单数据库不可用"},
}
TICKET_READ_ERRORS = {
    400: {"model": ErrorResponse, "description": "工单号无效"},
    404: {"model": ErrorResponse, "description": "工单不存在"},
    503: {"model": ErrorResponse, "description": "工单服务不可用"},
}


@dataclass(frozen=True)
class BuyerWorkbench:
    """保存使用同一订单归属范围的查单、对话和工单流程。"""

    order_lookup: OrderLookup
    conversation: OrderConversation
    tickets: SupportTicketFlow | None


def create_app(
    model: DeepSeekTool | None = None,
    *,
    orders: PostgresOrders | None = None,
    logistics_lookup: LogisticsLookup | None = None,
    olist_list: OlistOrderList | None = None,
    jd_list: OlistOrderList | None = None,
    catalog_items_lookup: ItemsLookup | None = None,
    items_lookup: ItemsLookup | None = None,
    olist_router: OrderToolRouter | None = None,
    tickets: TicketStore | None = None,
    merchant_requests: PostgresMerchantRequests | None = None,
    policy_assistant: PolicyAssistant | None = None,
    olist_policy_assistant: PolicyAssistant | None = None,
    jd_policy_assistant: PolicyAssistant | None = None,
    service_requests: PostgresServiceRequests | None = None,
    mall: PostgresMall | None = None,
    buyer_workflow: BuyerPolicyWorkflow | None = None,
    trace_sink: Callable[[PolicyTraceRecord], None] | None = None,
    order_checkpointer: BaseCheckpointSaver | None = None,
    conversation_thread_list: ThreadList | None = None,
    lifespan: Lifespan[FastAPI] | None = None,
    access: DemoAccess | None = None,
) -> FastAPI:
    """装配网页、查询和工单接口，允许测试注入模拟依赖。"""
    app = FastAPI(title="电商售后 Agent", lifespan=lifespan)
    order_lookup = orders.get_order if orders is not None else get_order
    policy_lookup = orders.get_policy if orders is not None else get_policy
    catalog = OrderCatalog(
        order_lookup, policy_lookup, shop_list=getattr(orders, "list_orders", None),
        logistics_lookup=logistics_lookup, olist_list=olist_list, items_lookup=catalog_items_lookup or items_lookup,
        jd_list=jd_list,
    )

    def answer_shop_question(order_id: str, question: str) -> dict:
        """用会话已验证的订单回答条款，失败时仍保存当轮和订单。"""
        if buyer_workflow is None:
            return {"status": "unavailable", "answer": "店铺条款问答暂不可用，请稍后重试。", "sources": []}
        trace = PolicyRequestTrace()
        status_code = 500
        try:
            result = buyer_workflow.run(order_id, question, trace=trace)
            status_code = 200
        except ModelError:
            status_code = 502
        except (McpEvidenceError, OrderStoreError, PolicyIndexError):
            status_code = 503
        finally:
            snapshot = trace.snapshot()
            if trace_sink is not None:
                trace_sink({"http_status": status_code, **snapshot})
        if status_code != 200:
            return {"status": "unavailable", "answer": "这次未能查到店铺条款，请在当前对话再发一次问题。",
                    "sources": [], "trace": snapshot}
        # 工具结果中的订单模型不进入检查点，保留可序列化的答复和出处。
        fields = ("status", "answer", "sources", "items", "function_call", "mcp_tools")
        return {**{key: result[key] for key in fields if key in result}, "trace": snapshot}

    def build_buyer(account_id: str | None) -> BuyerWorkbench:
        """为买家构建订单过滤和隔离的 LangGraph 会话。"""
        def owns(order_id: str) -> bool:
            """允许此账号使用其分配到的订单。"""
            return account_id is None or order_owner(order_id) == account_id

        def find_order(order_id: str) -> Order | None:
            """先核对归属，再读取订单和签收记录。"""
            return order_lookup(order_id) if owns(order_id) else None

        def find_logistics(order_id: str) -> LogisticsOrder | None:
            """先核对归属，再读取 Olist 样本。"""
            return logistics_lookup(order_id) if logistics_lookup is not None and owns(order_id) else None

        def review_shop_order(order: Order) -> dict:
            """从店铺申请和平台工单判断当前可用的人工服务入口。"""
            if tickets is None:
                return review_order(order.order_id, None)
            merchant_ready = merchant_requests is None or bool(order.shop_id and
                                                                   merchant_requests.get(order.shop_id, order.order_id))
            return review_order(order.order_id, partial(tickets.find_order_ticket, pending_only=True),
                                merchant_ready=merchant_ready)

        def freight_answer(order: LogisticsOrder, reason: ServiceReason | None) -> dict:
            """用可信费用记录和明确的用户原因回答这笔单的运费。"""
            try:
                practice = service_requests.freight_practice(order.order_id, reason) if service_requests and reason else None
            except ServiceError:
                practice = None
            return assess_return_freight(order, reason, practice)

        def service_progress(order_id: str) -> dict:
            """按已验证的订单和当前买家读取最新商品售后单。"""
            try:
                return {"requests": service_requests.page(buyer_id=account_id, order_id=order_id, limit=3)["requests"]}
            except ServiceError:
                return {"requests": [], "unavailable": True}

        conversation = OrderConversation(
            model, find_order, policy_lookup, checkpointer=order_checkpointer,
            logistics_lookup=find_logistics if logistics_lookup is not None else None, account_id=account_id,
            olist_router=olist_router, items_lookup=items_lookup,
            ticket_lookup=tickets.find_olist_ticket if tickets is not None and logistics_lookup is not None else None,
            shop_answer=answer_shop_question, card_items_lookup=catalog_items_lookup, shop_review=review_shop_order,
            thread_list=conversation_thread_list,
            olist_answer=answer_shop_question if olist_policy_assistant is not None else None,
            jd_answer=answer_shop_question if jd_policy_assistant is not None else None,
            jd_freight_answer=freight_answer if service_requests is not None else None,
            jd_service_progress=service_progress if service_requests is not None else None,
        )
        def read_conversation(conversation_id: UUID) -> dict:
            """通过当前买家的检查点读取已授权的对话。"""
            try:
                return conversation.restore(conversation_id)
            except ConversationConflict as error:
                raise TicketHandoffUnavailable("找不到可交接的对话，请重新查单后预览。") from error

        flow = SupportTicketFlow(find_order, tickets, owns_order=owns, logistics_lookup=find_logistics,
                                 conversation_lookup=read_conversation) if tickets is not None else None
        return BuyerWorkbench(find_order, conversation, flow)

    buyers = ({name: build_buyer(name) for name, role in ACCOUNT_ROLES.items() if role == "buyer"}
              if access is not None else {None: build_buyer(None)})

    def current_account(request: Request) -> Account | None:
        """从服务器会话读取当前身份。"""
        return access.account(request.cookies.get(SESSION_COOKIE)) if access is not None else None

    def require_catalog_account(request: Request, role: str) -> Account:
        """订单目录始终验证账号和角色，包含无登录配置的实例。"""
        account = request.state.account
        if account is None:
            raise HTTPException(status_code=401, detail="请先登录售后工作台。")
        if account.role != role:
            raise HTTPException(status_code=403, detail="当前账号没有此操作权限。")
        return account

    app.include_router(service_router(service_requests, catalog, require_catalog_account))
    if mall is not None:
        app.include_router(mall_router(mall, require_catalog_account))

    @app.exception_handler(ServiceError)
    async def service_error(_request: Request, error: ServiceError):
        """给买家或客服返回办理失败原因。"""
        return JSONResponse({"message": str(error)}, status_code=error.status_code)

    def catalog_detail(order_id: str, buyer_id: str | None) -> dict:
        """返回经过归属过滤的订单详情。"""
        detail = catalog.detail(order_id.upper(), buyer_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="查不到这笔订单，请核对订单号。")
        return detail

    def current_buyer(request: Request) -> BuyerWorkbench:
        """选择已由请求中间件验证身份的买家工作流。"""
        account = request.state.account
        workbench = buyers.get(account.account_id if account is not None else None)
        if workbench is None:
            raise HTTPException(status_code=403, detail="请前往「我的订单」查看该账号的购买记录。")
        return workbench

    def require_ticket_flow(request: Request) -> SupportTicketFlow:
        """提供已启用的工单流程，缺少仓储时返回可读错误。"""
        ticket_flow = current_buyer(request).tickets
        if ticket_flow is None:
            raise HTTPException(status_code=503, detail="请连接 PostgreSQL 后再提交人工工单。")
        return ticket_flow

    def require_ticket_store() -> TicketStore:
        """取得工单仓储，数据库模式下才能查询和处理工单。"""
        if tickets is None:
            raise HTTPException(status_code=503, detail="请连接 PostgreSQL 后再查看和处理工单。")
        return tickets

    def read_ticket(ticket_no: int) -> TicketDetailRecord:
        """按工单号读取工单，缺少记录时返回 HTTP 404。"""
        ticket = require_ticket_store().get_by_number(ticket_no)
        if ticket is None:
            raise HTTPException(status_code=404, detail="查不到这张工单，请核对工单号。")
        return ticket

    def require_merchant_requests() -> PostgresMerchantRequests:
        """在缺少本机店铺售后初始化时提示用户。"""
        if merchant_requests is None:
            raise HTTPException(status_code=503, detail="店铺售后暂不可用。")
        return merchant_requests

    def buyer_shop_order(request: Request, order_id: str) -> Order:
        """在读取或提交申请之前确定订单归属和服务店铺。"""
        require_catalog_account(request, "buyer")
        order = current_buyer(request).order_lookup(order_id.upper())
        if order is None or order.shop_id is None:
            raise HTTPException(status_code=404, detail="查不到可联系店铺的订单。")
        return order

    @app.middleware("http")
    async def workbench_headers(request: Request, call_next):
        """限制请求体大小，并让浏览器每次加载最新的工作台内容。"""
        path = request.url.path
        request.state.account = current_account(request)
        if access is not None and path.startswith("/api/"):
            origin = request.headers.get("origin")
            if request.method == "POST" and origin is not None and origin != str(request.base_url).rstrip("/"):
                return JSONResponse({"message": "请求来源与工作台不一致。"}, status_code=403,
                                    headers={"Cache-Control": "no-store"})
            if path not in {"/api/config", "/api/session", "/api/session/login", "/api/session/logout"}:
                account = request.state.account
                role = "staff" if path.startswith("/api/staff/") else (
                    "merchant" if path.startswith("/api/merchant/") else "buyer"
                )
                if account is None or account.role != role:
                    status = 401 if account is None else 403
                    message = "请先登录售后工作台。" if account is None else "当前账号没有此操作权限。"
                    return JSONResponse({"message": message}, status_code=status, headers={"Cache-Control": "no-store"})
        if mall is not None:
            old_order_staff = path == "/api/staff/orders" or path.startswith("/api/staff/orders/")
            old_service_staff = path == "/api/staff/service-requests" or path.startswith("/api/staff/service-requests/")
            old_order_buyer = path in ("/api/buyer/orders", "/api/buyer/service-requests") or path.startswith(
                ("/api/buyer/orders/", "/api/buyer/service-requests/", "/api/olist/orders/"))
            old_write = (request.method == "POST" and (
                path in {"/api/tickets/preview", "/api/tickets/confirm"}
                or path.startswith(("/api/buyer/merchant-requests/", "/api/merchant/requests/"))
                or re.fullmatch(r"/api/buyer/(?:orders/[^/]+/service-requests|service-requests/[^/]+/shipment)", path)
                or re.fullmatch(r"/api/staff/tickets/[^/]+/resolve", path)
            ))
            if old_order_staff or old_service_staff or old_order_buyer or old_write:
                return JSONResponse({"message": "请在商城工作台查询订单和办理售后。"}, status_code=410,
                                    headers={"Cache-Control": "no-store"})
        invalid_body = BODY_ERRORS.get(path)
        max_body_bytes = 8192
        if "/mall/" in path:
            invalid_body = "请核对商城查询条件或售后表单。"
            if re.fullmatch(r"/api/(?:buyer/mall/orders/[^/]+/cases|(?:buyer|merchant)/mall/cases/[^/]+/actions)", path):
                max_body_bytes = MAX_PHOTO_JSON
            if re.fullmatch(r'/api/buyer/mall/orders/[^/]+/items/[0-9]+/consult/actions/[^/]+/confirm',path):
                max_body_bytes = MAX_PHOTO_JSON
        if "service-requests" in path:
            invalid_body = "请核对商品、件数和售后表单。"
        if path.startswith(("/api/buyer/merchant-requests/", "/api/merchant/requests/")):
            invalid_body = "请核对订单号，并填写 1 至 300 个字符的说明。"
        if re.fullmatch(r"/api/staff/tickets/[^/]+/resolve", request.url.path):
            invalid_body = INVALID_RESOLUTION
        if re.fullmatch(r"/api/staff/tickets/[^/]+/policy-search", request.url.path):
            invalid_body = "请输入 1 至 300 个字符的政策问题。"
        if re.fullmatch(r"/api/orders/[^/]+/policy-assistance", request.url.path):
            invalid_body = INVALID_BUYER_POLICY
        if OLIST_ITEMS_PATH.fullmatch(path):
            invalid_body = INVALID_OLIST_ITEMS
        if request.method == "POST" and invalid_body:
            try:
                length = int(request.headers.get("content-length", "0"))
            except ValueError:
                length = 0
            if not 0 < length <= max_body_bytes:
                return JSONResponse({"message": invalid_body}, status_code=400,
                                    headers={"Cache-Control": "no-store"})
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, _error: RequestValidationError):
        """将请求校验错误转换成页面使用的 HTTP 400 消息。"""
        path = request.url.path
        message = BODY_ERRORS.get(path, INVALID_QUERY)
        if "/mall/" in path:
            message = "请核对商城订单编号、筛选条件和表单内容。"
            if path.endswith("/actions"):
                contact_messages = {
                    "recipient": "请输入 1 至 80 个字符的收件人。",
                    "address": "请输入 5 至 240 个字符的详细地址。",
                    "phone": "请输入 5 至 30 位联系电话，可包含数字、空格、加号、括号和短横线。",
                }
                fields = {}
                for error in _error.errors():
                    location = error["loc"]
                    if (len(location) == 3 and location[0] == "body"
                            and location[1] in ("return_details", "delivery_address")
                            and location[2] in contact_messages):
                        fields[".".join(location[1:])] = contact_messages[location[2]]
                if fields:
                    return JSONResponse({"message": "请修正标出的收件信息后再提交。", "field_errors": fields},
                                        status_code=400)
        if "service-requests" in path:
            message = "请核对商品、件数、运单号或填写的说明。"
        if path.startswith(("/api/buyer/merchant-requests/", "/api/merchant/requests")):
            message = "请核对订单号、分页参数或申请说明。"
        if path.startswith(("/api/staff/tickets/", "/api/tickets/")) and path not in BODY_ERRORS:
            message = INVALID_RESOLUTION if path.endswith("/resolve") else "请输入有效的工单号。"
        if path == "/api/staff/tickets/search":
            message = "请核对订单号、工单状态或分页参数。"
        if path.startswith(("/api/buyer/orders", "/api/staff/orders")):
            message = "请核对订单号、买家账号或订单筛选条件。"
        if path.endswith("/policy-search"):
            message = "请核对工单号，并输入 1 至 300 个字符的政策问题。"
        if path.endswith("/policy-assistance"):
            message = INVALID_BUYER_POLICY
        if OLIST_ITEMS_PATH.fullmatch(path):
            message = INVALID_OLIST_ITEMS
        if path.startswith("/api/conversation/") and path not in BODY_ERRORS:
            message = "会话 ID 无效，请开始新查询。"
        if path == "/api/buyer/conversations":
            message = "会话分页参数无效，请返回第一页。"
        return JSONResponse({"message": message}, status_code=400)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException):
        """保留 HTTP 状态码，并为未匹配的路由提供中文提示。"""
        message = error.detail
        if error.status_code == 404 and error.detail == "Not Found":
            message = "接口不存在。" if request.method == "POST" else "页面不存在。"
        return JSONResponse({"message": message}, status_code=error.status_code, headers=error.headers)

    @app.exception_handler(ModelError)
    async def model_error(_request: Request, error: ModelError):
        """将 DeepSeek 请求错误转换成 HTTP 502。"""
        return JSONResponse({"message": str(error)}, status_code=502)

    @app.exception_handler(OrderStoreError)
    @app.exception_handler(TicketStoreError)
    @app.exception_handler(PolicyIndexError)
    @app.exception_handler(McpEvidenceError)
    async def data_service_error(
        _request: Request, error: OrderStoreError | TicketStoreError | PolicyIndexError | McpEvidenceError,
    ):
        """将数据库、索引或 MCP 工具服务故障转换成 HTTP 503。"""
        return JSONResponse({"message": str(error)}, status_code=503)

    @app.exception_handler(psycopg.Error)
    async def checkpoint_database_error(_request: Request, _error: psycopg.Error):
        """在检查点查询失败时提示重试，避免暴露数据库连接细节。"""
        return JSONResponse({"message": "暂时无法读取会话，请稍后重试。"}, status_code=503)

    @app.get("/", include_in_schema=False)
    def home(request: Request) -> Response:
        """返回售后工作台主页。"""
        account = request.state.account
        if access is not None and account is None:
            return RedirectResponse("/login", status_code=303)
        if account is not None and account.role != "buyer":
            return RedirectResponse((MALL_HOME if mall is not None else ROLE_HOME)[account.role], status_code=303)
        return FileResponse(WEB / "index.html", media_type="text/html")

    @app.get("/conversations", include_in_schema=False)
    def buyer_conversations_page(request: Request) -> Response:
        """买家从会话列表选择一条历史对话。"""
        account = request.state.account
        if access is not None and account is None:
            return RedirectResponse("/login", status_code=303)
        if account is not None and account.role != "buyer":
            return RedirectResponse(ROLE_HOME[account.role], status_code=303)
        return FileResponse(WEB / "conversations.html", media_type="text/html")

    @app.get("/conversations.js", include_in_schema=False)
    def buyer_conversations_javascript() -> FileResponse:
        """提供售后会话列表的读取和分页。"""
        return FileResponse(WEB / "conversations.js", media_type="text/javascript")

    @app.get("/login", include_in_schema=False)
    def login_page() -> FileResponse:
        """显示买家和客服共用的登录表单。"""
        return FileResponse(WEB / "login.html", media_type="text/html")

    @app.get("/session.js", include_in_schema=False)
    def session_javascript() -> FileResponse:
        """返回登录和退出交互脚本。"""
        return FileResponse(WEB / "session.js", media_type="text/javascript")

    @app.get("/api/session")
    def session_info(request: Request) -> dict:
        """提供当前界面所需的身份和角色。"""
        account = request.state.account
        return {"account_id": account.account_id if account else None, "role": account.role if account else None,
                "display_name": account.display_name if account else None,
                "shop_policy_workbench": bool(mall is not None and account and account.role == "merchant")}

    @app.post("/api/session/login")
    def login(request: Request, form: LoginRequest, response: Response) -> dict:
        """验证本机随机口令，签发仅供浏览器会话使用的 Cookie。"""
        if access is None:
            raise HTTPException(status_code=503, detail="账号服务暂不可用。")
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise HTTPException(status_code=415, detail="登录请求需要 JSON 格式。")
        session = access.login(form.account_id, form.secret, request.cookies.get(SESSION_COOKIE))
        if session is None:
            raise HTTPException(status_code=401, detail="账号或口令不正确。")
        token, account = session
        response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_SECONDS, httponly=True,
                            samesite="strict", secure=request.url.scheme == "https")
        result = {"account_id": account.account_id, "role": account.role, "display_name": account.display_name}
        if mall is not None:
            result["home"] = MALL_HOME[account.role]
        return result

    @app.post("/api/session/logout")
    def logout(request: Request, response: Response) -> dict:
        """撤销此浏览器会话，清除页面使用的登录 Cookie。"""
        if access is not None:
            access.logout(request.cookies.get(SESSION_COOKIE))
        response.delete_cookie(SESSION_COOKIE, httponly=True, samesite="strict")
        return {"account_id": None, "role": None}

    @app.get("/styles.css", include_in_schema=False)
    def stylesheet() -> FileResponse:
        """返回工作台样式文件。"""
        return FileResponse(WEB / "styles.css", media_type="text/css")

    @app.get("/app.js", include_in_schema=False)
    def javascript() -> FileResponse:
        """返回处理页面交互的脚本。"""
        return FileResponse(WEB / "app.js", media_type="text/javascript")

    @app.get("/ticket-handoff.js", include_in_schema=False)
    def handoff_javascript() -> FileResponse:
        """返回买家预览和客服详情共用的交接展示。"""
        return FileResponse(WEB / "ticket-handoff.js", media_type="text/javascript")

    @app.get("/buyer-merchant.js", include_in_schema=False)
    def buyer_merchant_javascript() -> FileResponse:
        """供买家提交并查看店铺售后。"""
        return FileResponse(WEB / "buyer-merchant.js", media_type="text/javascript")

    @app.get("/staff", include_in_schema=False)
    def staff_page(request: Request) -> Response:
        """返回本机客服处理页面。"""
        account = request.state.account
        if access is not None and account is None:
            return RedirectResponse("/login", status_code=303)
        if account is not None and account.role != "staff":
            return RedirectResponse((MALL_HOME if mall is not None else ROLE_HOME)[account.role], status_code=303)
        if mall is not None:
            return RedirectResponse("/staff/disputes", status_code=303)
        return FileResponse(WEB / "staff.html", media_type="text/html")

    @app.get("/staff.js", include_in_schema=False)
    def staff_javascript() -> FileResponse:
        """返回客服页面的队列、详情和处理脚本。"""
        return FileResponse(WEB / "staff.js", media_type="text/javascript")

    @app.get("/merchant", include_in_schema=False)
    def merchant_page(request: Request) -> Response:
        """打开所属店铺的售后处理页。"""
        account = request.state.account
        if account is None:
            return RedirectResponse("/login", status_code=303)
        if account.role != "merchant":
            return RedirectResponse((MALL_HOME if mall is not None else ROLE_HOME)[account.role], status_code=303)
        if mall is not None:
            return RedirectResponse("/merchant/mall", status_code=303)
        return FileResponse(WEB / "merchant.html", media_type="text/html")

    @app.get("/merchant.js", include_in_schema=False)
    def merchant_javascript() -> FileResponse:
        """提供店铺售后队列和答复交互。"""
        return FileResponse(WEB / "merchant.js", media_type="text/javascript")

    def catalog_page(request: Request, role: str) -> Response:
        """根据角色打开相应订单页面。"""
        account = request.state.account
        if account is None:
            return RedirectResponse("/login", status_code=303)
        if account.role != role:
            return RedirectResponse(ROLE_HOME[account.role], status_code=303)
        page = "staff-orders.html" if role == "staff" else "orders.html"
        return FileResponse(WEB / page, media_type="text/html")

    @app.get("/orders", include_in_schema=False)
    @app.get("/orders/{order_id}", include_in_schema=False)
    def buyer_orders_page(request: Request) -> Response:
        """返回买家的订单列表或可直接打开的详情页面。"""
        if mall is not None:
            return RedirectResponse("/mall/orders", status_code=303)
        return catalog_page(request, "buyer")

    @app.get("/staff/orders", include_in_schema=False)
    @app.get("/staff/orders/{order_id}", include_in_schema=False)
    def staff_orders_page(request: Request) -> Response:
        """返回客服的订单查询工作区。"""
        if mall is not None:
            return RedirectResponse("/staff/mall/catalog", status_code=303)
        return catalog_page(request, "staff")

    @app.get("/order-catalog.js", include_in_schema=False)
    def catalog_javascript() -> FileResponse:
        """返回两个订单页面共用的列表与详情脚本。"""
        return FileResponse(WEB / "order-catalog.js", media_type="text/javascript")

    @app.get("/order-items.js", include_in_schema=False)
    def order_items_javascript() -> FileResponse:
        """返回订单页和买家对话使用的商品明细组件。"""
        return FileResponse(WEB / "order-items.js", media_type="text/javascript")

    @app.get("/service-requests", include_in_schema=False)
    @app.get("/staff/service-requests", include_in_schema=False)
    def service_page(request: Request) -> Response:
        """展示当前登录角色的商品售后工作台。"""
        if mall is not None:
            target = "/staff/disputes" if request.url.path.startswith("/staff/") else "/mall/after-sales"
            return RedirectResponse(target, status_code=303)
        account = request.state.account
        role = "staff" if request.url.path.startswith("/staff/") else "buyer"
        if account is None:
            return RedirectResponse("/login", status_code=303)
        if account.role != role:
            return RedirectResponse(ROLE_HOME[account.role], status_code=303)
        return FileResponse(WEB / "service-requests.html", media_type="text/html")

    @app.get("/service-requests.js", include_in_schema=False)
    def service_javascript() -> FileResponse:
        """返回售后申请和分阶段办理的浏览器脚本。"""
        return FileResponse(WEB / "service-requests.js", media_type="text/javascript")

    @app.get("/api/buyer/orders", response_model=CatalogPageResponse)
    def list_buyer_orders(request: Request, kind: OrderKind = "all", order_id: BuyerOrderSearch = None,
                          receipt: ReceiptStatusFilter = "all", after: CatalogOrderFilter = None,
                          limit: CatalogPageSize = 12) -> dict:
        """在买家本人订单中按编号和签收状态搜索，再返回当前页。"""
        account = require_catalog_account(request, "buyer")
        return catalog.page(account.account_id, kind=kind, order_id=None, prefix=(order_id or "").upper(),
                            after=(after or "").upper(), limit=limit, receipt=receipt)

    @app.get("/api/buyer/orders/{order_id}", response_model=CatalogDetailResponse)
    def read_buyer_order(order_id: CatalogOrderNumber, request: Request) -> dict:
        """买家只能打开属于自己的订单。"""
        return catalog_detail(order_id, require_catalog_account(request, "buyer").account_id)

    @app.get("/api/buyer/orders/{order_id}/after-sales", response_model=OrderAfterSalesResponse,
             response_model_exclude_unset=True)
    def read_order_after_sales(order_id: CatalogOrderNumber, request: Request) -> dict:
        """校验归属后，按订单读取店铺申请及平台工单。"""
        account = require_catalog_account(request, "buyer")
        order = catalog.find(order_id.upper(), account.account_id)
        if order is None:
            raise HTTPException(status_code=404, detail="查不到这笔订单，请核对订单号。")
        merchant = (merchant_requests.get(order.shop_id, order.order_id)
                    if merchant_requests is not None and isinstance(order, Order) and order.shop_id else None)
        ticket = tickets.find_order_ticket(order.order_id) if tickets is not None else None
        result = {"merchant": merchant, "platform": {**ticket, "ticket_no": str(ticket["ticket_no"])} if ticket else None}
        if service_requests is not None and isinstance(order, LogisticsOrder) and order.origin == "jd":
            result["services"] = service_requests.page(buyer_id=account.account_id, order_id=order.order_id, limit=3)["requests"]
        return result

    @app.get("/api/staff/orders", response_model=CatalogPageResponse)
    def list_staff_orders(request: Request, kind: OrderKind = "all",
                          buyer_id: Literal["buyer-a", "buyer-b"] | None = None,
                          order_id: CatalogOrderFilter = None, after: CatalogOrderFilter = None,
                          limit: CatalogPageSize = 12) -> dict:
        """客服可组合买家账号、准确订单号与来源查询。"""
        require_catalog_account(request, "staff")
        return catalog.page(buyer_id, kind=kind, order_id=order_id.upper() if order_id else None,
                            after=(after or "").upper(), limit=limit)

    @app.get("/api/staff/orders/{order_id}", response_model=CatalogDetailResponse)
    def read_staff_order(order_id: CatalogOrderNumber, request: Request) -> dict:
        """客服可以独立于工单核对订单详情。"""
        require_catalog_account(request, "staff")
        return catalog_detail(order_id, None)

    @app.get("/api/config")
    def get_config(request: Request) -> ConfigResponse:
        """向页面说明当前使用的数据源及是否支持工单。"""
        account = request.state.account
        return ConfigResponse(
            mode="deepseek" if model is not None else "demo",
            demo_date=DEMO_DATE,
            order_source="postgresql" if orders is not None else "memory",
            ticket_enabled=tickets is not None,
            merchant_enabled=merchant_requests is not None,
            policy_search_enabled=policy_assistant is not None,
            olist_policy_search_enabled=olist_policy_assistant is not None,
            jd_policy_search_enabled=jd_policy_assistant is not None,
            buyer_policy_enabled=buyer_workflow is not None,
            access_enabled=access is not None,
            mall_enabled=mall is not None,
            logistics_examples=[order for order in OLIST_EXAMPLES if access is None
                                or (account is not None and order_owner(order) == account.account_id)],
        )

    @app.post(
        "/api/query",
        response_model=QueryResponse,
        response_model_exclude_unset=True,
        responses=QUERY_ERRORS,
    )
    def query(request: QueryRequest, http_request: Request) -> dict:
        """执行一次指定期限的单轮订单查询。"""
        return query_after_sales(
            request.question, request.return_window_days, model, order_lookup=current_buyer(http_request).order_lookup,
        )

    @app.post(
        "/api/conversation",
        response_model=ConversationResponse,
        response_model_exclude_unset=True,
        responses={**QUERY_ERRORS, 409: {"model": ErrorResponse, "description": "会话无法继续"}},
    )
    def send_message(request: ConversationRequest, http_request: Request) -> dict:
        """将当前问题或后续回复交给 LangGraph。"""
        try:
            return current_buyer(http_request).conversation.send(
                request.question, request.conversation_id,
                active_order_id=request.active_order_id.upper() if request.active_order_id else None,
            )
        except ConversationConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/conversation/stream", response_class=StreamingResponse)
    async def stream_message(request: ConversationRequest, http_request: Request) -> StreamingResponse:
        """在原有会话流程中流式发送订单事实和最终结果。"""
        dialogue = current_buyer(http_request).conversation
        active_order = request.active_order_id.upper() if request.active_order_id else None

        async def events():
            """用请求独立的队列桥接同步图执行和浏览器流。"""
            loop = asyncio.get_running_loop()
            queue = asyncio.Queue()
            disconnected = Event()

            def publish(event: dict) -> None:
                """只向当前仍在等待的连接发送消息。"""
                if not disconnected.is_set():
                    loop.call_soon_threadsafe(queue.put_nowait, event)

            def publish_facts(result: dict) -> None:
                """发送可独立展示的已验证订单事实。"""
                data = ConversationOrderResult.model_validate(result).model_dump(mode="json", exclude_unset=True)
                publish({"type": "partial", "data": data})

            def run() -> None:
                """执行一次对话，输出一条最终结果或可读错误。"""
                try:
                    result = dialogue.send(request.question, request.conversation_id,
                                           active_order_id=active_order, on_facts=publish_facts)
                    data = ConversationResponse.model_validate(result).model_dump(mode="json", exclude_unset=True)
                    publish({"type": "final", "data": data})
                except (ConversationConflict, ModelError, OrderStoreError, TicketStoreError, PolicyIndexError, McpEvidenceError) as error:
                    publish({"type": "error", "message": str(error)})
                except psycopg.Error:
                    publish({"type": "error", "message": "暂时无法读取会话，请稍后重试。"})
                except Exception:
                    logging.getLogger(__name__).exception("分段对话执行失败。")
                    publish({"type": "error", "message": "查询暂时未完成，请重试。"})

            worker = asyncio.create_task(asyncio.to_thread(run))
            try:
                while True:
                    event = await queue.get()
                    yield json.dumps(event, ensure_ascii=False) + "\n"
                    if event["type"] != "partial":
                        await worker
                        break
            finally:
                disconnected.set()

        return StreamingResponse(events(), media_type="application/x-ndjson", headers={"X-Accel-Buffering": "no"})

    @app.post("/api/conversation/open-order", response_model=ConversationResponse, response_model_exclude_unset=True)
    def open_order_conversation(request: OpenConversationRequest, http_request: Request) -> dict:
        """核对订单归属并打开对应店铺的会话。"""
        try:
            return current_buyer(http_request).conversation.open_order(request.order_id.upper(), request.conversation_id)
        except ConversationOrderMissing as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/conversation/prepare-order", response_model=ConversationResponse, response_model_exclude_unset=True)
    def prepare_order_conversation(request: OpenConversationRequest, http_request: Request) -> dict:
        """先显示店铺历史和待发送订单，读取期间不切换订单上下文。"""
        try:
            return current_buyer(http_request).conversation.prepare_order(request.order_id.upper(), request.conversation_id)
        except ConversationOrderMissing as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/conversation/share-order", response_model=ConversationResponse, response_model_exclude_unset=True)
    def share_conversation_order(request: OpenConversationRequest, http_request: Request) -> dict:
        """确认发送授权订单卡片，供后续咨询绑定该订单。"""
        try:
            return current_buyer(http_request).conversation.open_order(request.order_id.upper(), request.conversation_id,
                                                                      share=True)
        except ConversationOrderMissing as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/buyer/conversations", response_model=BuyerConversationPage,
             responses={400: {"model": ErrorResponse, "description": "会话分页参数无效"}})
    def list_buyer_conversations(request: Request, before: UUID | None = None,
                                 limit: Annotated[int, Query(ge=1, le=30)] = 12) -> dict:
        """由服务端会话身份决定可浏览的历史，不接受客户端指定买家。"""
        return current_buyer(request).conversation.recent(str(before) if before else None, limit)

    @app.get(
        "/api/conversation/{conversation_id}", response_model=ConversationRestoreResponse,
        response_model_exclude_unset=True,
        responses={
            400: {"model": ErrorResponse, "description": "会话 ID 无效"},
            404: {"model": ErrorResponse, "description": "会话不可恢复"},
            503: {"model": ErrorResponse, "description": "会话数据库不可用"},
        },
    )
    def restore_conversation(conversation_id: UUID, request: Request) -> dict:
        """取回原问题和上次停留位置，供刷新后的工作台显示。"""
        try:
            return current_buyer(request).conversation.restore(conversation_id)
        except ConversationConflict as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/buyer/merchant-requests/{order_id}", response_model=MerchantRequestResponse | None)
    def read_merchant_request(order_id: OrderNumber, request: Request) -> dict | None:
        """买家查看自己的店铺售后申请或店铺答复。"""
        order = buyer_shop_order(request, order_id)
        return require_merchant_requests().get(order.shop_id, order.order_id)

    @app.post("/api/buyer/merchant-requests/{order_id}", response_model=MerchantRequestResponse)
    def submit_merchant_request(order_id: OrderNumber, form: MerchantRequestInput, request: Request) -> dict:
        """经买家主动提交后，按订单写入所属店铺的队列。"""
        order = buyer_shop_order(request, order_id)
        return require_merchant_requests().submit(order.shop_id, order.order_id, form.reason)

    @app.get("/api/merchant/requests", response_model=MerchantRequestPage)
    def list_merchant_requests(request: Request, resolved: bool = False, after: CatalogOrderFilter = None,
                               limit: CatalogPageSize = 20) -> dict:
        """店铺只能查看本店申请，待答复与已答复分开查询。"""
        account = require_catalog_account(request, "merchant")
        shop_id = MERCHANT_SHOPS[account.account_id]
        rows = require_merchant_requests().list(shop_id, resolved=resolved, after=(after or "").upper(), limit=limit + 1)
        policy = policy_lookup(shop_id)
        return {"shop_name": policy.shop_name if policy else None, "requests": rows[:limit],
                "next_after": rows[limit - 1]["order_id"] if len(rows) > limit else None}

    @app.get("/api/merchant/requests/{order_id}/order", response_model=CatalogDetailResponse)
    def read_merchant_request_order(order_id: OrderNumber, request: Request) -> dict:
        """店铺账号从售后申请中核对本店的订单资料。"""
        account = require_catalog_account(request, "merchant")
        normalized = order_id.upper()
        shop_id = MERCHANT_SHOPS[account.account_id]
        order = order_lookup(normalized)
        if order is None or order.shop_id != shop_id or require_merchant_requests().get(shop_id, normalized) is None:
            raise HTTPException(status_code=404, detail="查不到本店申请对应的订单。")
        return catalog_detail(normalized, None)

    @app.post("/api/merchant/requests/{order_id}/resolve", response_model=MerchantRequestResponse)
    def resolve_merchant_request(order_id: OrderNumber, form: TicketResolutionRequest, request: Request) -> dict:
        """由所属店铺保存一次可供买家和平台查看的处理说明。"""
        account = require_catalog_account(request, "merchant")
        shop_id = MERCHANT_SHOPS[account.account_id]
        store = require_merchant_requests()
        normalized = order_id.upper()
        resolved = store.resolve(shop_id, normalized, form.resolution, account.account_id)
        if resolved is not None:
            return resolved
        if store.get(shop_id, normalized) is None:
            raise HTTPException(status_code=404, detail="查不到本店的这笔售后申请。")
        raise HTTPException(status_code=409, detail="申请已有店铺答复，请刷新后查看。")

    @app.post(
        "/api/tickets/preview",
        response_model=TicketPreviewResponse,
        response_model_exclude_unset=True,
        responses={
            400: {"model": ErrorResponse, "description": "工单信息无效"},
            404: {"model": ErrorResponse, "description": "订单不存在"},
            409: {"model": ErrorResponse, "description": "店铺申请或会话未满足转人工条件"},
            503: {"model": ErrorResponse, "description": "工单服务不可用"},
        },
    )
    def preview_ticket(request: TicketPreviewRequest, http_request: Request) -> dict:
        """创建等待用户确认的工单预览。"""
        if merchant_requests is not None and not HISTORICAL_ORDER_PATTERN.fullmatch(request.order_id.upper()):
            order = buyer_shop_order(http_request, request.order_id)
            if merchant_requests.get(order.shop_id, order.order_id) is None:
                raise HTTPException(status_code=409, detail="请先向店铺提交售后申请，需要时再申请平台客服介入。")
        try:
            return require_ticket_flow(http_request).preview(
                request.order_id, request.reason, conversation_id=request.conversation_id,
            )
        except TicketOrderNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except TicketHandoffUnavailable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/api/tickets/confirm",
        response_model=TicketResponse,
        responses={
            400: {"model": ErrorResponse, "description": "确认 ID 无效"},
            409: {"model": ErrorResponse, "description": "工单预览已失效"},
            503: {"model": ErrorResponse, "description": "工单服务不可用"},
        },
    )
    def confirm_ticket(request: TicketConfirmRequest, http_request: Request) -> dict:
        """使用确认 ID 提交预览，或返回先前已创建的工单。"""
        try:
            return require_ticket_flow(http_request).confirm(request.confirmation_id)
        except TicketPreviewUnavailable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get(
        "/api/tickets/{ticket_no}/progress", response_model=TicketProgressResponse,
        responses=TICKET_READ_ERRORS,
    )
    def ticket_progress(ticket_no: TicketNumber, request: Request) -> dict:
        """按工单号返回买家查询所需的状态和处理说明。"""
        ticket = read_ticket(ticket_no)
        account = request.state.account
        if account is not None and order_owner(ticket["order_id"]) != account.account_id:
            raise HTTPException(status_code=404, detail="查不到这张工单，请核对工单号。")
        return ticket

    @app.get(
        "/api/staff/tickets", response_model=list[TicketResponse],
        responses={503: TICKET_READ_ERRORS[503]},
    )
    def pending_tickets() -> list:
        """返回客服待处理队列。"""
        return require_ticket_store().list_pending()

    @app.get(
        "/api/staff/tickets/search", response_model=StaffTicketPage,
        responses={
            400: {"model": ErrorResponse, "description": "筛选或分页参数无效"},
            401: {"model": ErrorResponse, "description": "需要登录"},
            403: {"model": ErrorResponse, "description": "需要客服权限"},
            503: TICKET_READ_ERRORS[503],
        },
    )
    def search_staff_tickets(
        status: TicketStatusFilter = "all",
        order_id: Annotated[str | None, Query(pattern=TICKET_ORDER_PATTERN)] = None,
        before: Annotated[int | None, Query(gt=0, le=2**63 - 1)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 10,
    ) -> dict:
        """按状态和订单返回客服历史，多读一条判断是否还有下一页。"""
        rows = require_ticket_store().search(status=status, order_id=order_id.upper() if order_id else None,
                                             before=before, limit=limit + 1)
        tickets = [{**row, "ticket_no": str(row["ticket_no"])} for row in rows[:limit]]
        return {"tickets": tickets, "next_before": tickets[-1]["ticket_no"] if len(rows) > limit else None}

    @app.get(
        "/api/staff/tickets/{ticket_no}", response_model=StaffTicketResponse,
        response_model_exclude_unset=True, responses=TICKET_READ_ERRORS,
    )
    def staff_ticket(ticket_no: TicketNumber) -> dict:
        """合并工单、当前订单及适用政策，供客服核对。"""
        ticket = read_ticket(ticket_no)
        order_id = ticket["order_id"]
        if HISTORICAL_ORDER_PATTERN.fullmatch(order_id):
            logistics = logistics_lookup(order_id) if logistics_lookup is not None else None
            if logistics is None:
                return {**ticket, "evidence": {"status": "order_not_found", "message": "查不到关联的历史物流样本。"}}
            detail = {**ticket, "evidence": logistics_result(logistics)}
            items = items_lookup(order_id) if items_lookup is not None else None
            if items is not None:
                detail["olist_items"] = answer_items(order_id, "买了什么？买了几件？", items)
            return detail
        order = order_lookup(order_id)
        policy = policy_lookup(order.shop_id) if order is not None and order.shop_id else None
        if order is None:
            evidence = {"status": "order_not_found", "message": "查不到关联订单。"}
        elif policy is None:
            evidence = {"status": "policy_unavailable", "message": "暂无适用的店铺政策。"}
            if order.signed_on is not None:
                evidence["signed_on"] = order.signed_on
        else:
            evidence = evaluate_return(order, DEMO_DATE, policy.return_window_days)
            evidence.update(
                shop_name=policy.shop_name, policy_name=policy.policy_name,
                return_window_days=policy.return_window_days,
            )
        detail = {**ticket, "evidence": evidence}
        if merchant_requests is not None and order is not None and order.shop_id:
            detail["merchant_request"] = merchant_requests.get(order.shop_id, order_id)
        return detail

    @app.post(
        "/api/staff/tickets/{ticket_no}/resolve", response_model=TicketProgressResponse,
        responses={**TICKET_READ_ERRORS, 409: {"model": ErrorResponse, "description": "工单已处理"}},
    )
    def resolve_ticket(ticket_no: TicketNumber, request: TicketResolutionRequest, http_request: Request) -> dict:
        """将待处理工单改为已处理，重复提交时保留原记录。"""
        store = require_ticket_store()
        account = http_request.state.account
        if account is None:
            resolved = store.resolve(ticket_no, request.resolution)
        else:
            resolved = store.resolve(ticket_no, request.resolution, actor=account.account_id)
        if resolved is not None:
            return resolved
        read_ticket(ticket_no)
        raise HTTPException(status_code=409, detail="这张工单已处理，请刷新查看结果。")

    @app.post(
        "/api/staff/tickets/{ticket_no}/policy-search", response_model=PolicySearchResponse,
        responses={**TICKET_READ_ERRORS, 409: {"model": ErrorResponse, "description": "工单缺少店铺信息"}},
    )
    def search_ticket_policy(ticket_no: TicketNumber, request: QuestionRequest) -> dict:
        """从工单找到店铺，在该店铺范围内检索并解释政策。"""
        ticket = read_ticket(ticket_no)
        if JD_ORDER_PATTERN.fullmatch(ticket["order_id"]):
            if jd_policy_assistant is None:
                raise HTTPException(status_code=409, detail="这笔订单暂无可查的国内售后规则。")
            order = catalog.find(ticket["order_id"], None)
            if not isinstance(order, LogisticsOrder):
                raise HTTPException(status_code=409, detail="工单关联的订单无法查询。")
            return label_jd_reference(jd_policy_assistant.answer(jd_policy_scope(order), request.question))
        if HISTORICAL_ORDER_PATTERN.fullmatch(ticket["order_id"]):
            if olist_policy_assistant is None:
                raise HTTPException(status_code=409, detail="这笔订单暂无可查的公开售后规则。")
            if catalog.find(ticket["order_id"], None) is None:
                raise HTTPException(status_code=409, detail="工单关联的订单无法查询。")
            return label_olist_reference(olist_policy_assistant.answer(OLIST_REFERENCE_SCOPE, request.question))
        if policy_assistant is None:
            raise HTTPException(status_code=503, detail="请先启动 Milvus 并导入政策条款。")
        order = order_lookup(ticket["order_id"])
        if order is None or not order.shop_id:
            raise HTTPException(status_code=409, detail="工单缺少可用于检索的店铺信息。")
        return policy_assistant.answer(order.shop_id, request.question)

    @app.post(
        "/api/orders/{order_id}/policy-assistance", response_model=BuyerPolicyResponse,
        responses={
            400: QUERY_ERRORS[400],
            404: {"model": BuyerPolicyErrorResponse, "description": "订单不存在"},
            502: {"model": BuyerPolicyErrorResponse, "description": "模型调用失败"},
            503: {"model": BuyerPolicyErrorResponse, "description": "MCP 或订单服务不可用"},
        },
    )
    def answer_order_policy(order_id: CatalogOrderNumber, request: QuestionRequest, response: Response,
                            http_request: Request) -> dict | Response:
        """对已存在的订单执行政策问答，并将运行记录返回给页面。"""
        trace = PolicyRequestTrace()
        headers = {"X-Request-ID": trace.request_id}
        status_code = 500
        try:
            if buyer_workflow is None:
                raise HTTPException(status_code=503, detail="请先配置本机 MCP 和 DeepSeek。")
            order_id = order_id.upper()
            with trace.stage("order_check"):
                if HISTORICAL_ORDER_PATTERN.fullmatch(order_id):
                    account = http_request.state.account
                    order = catalog.find(order_id, account.account_id if account is not None else None)
                else:
                    order = current_buyer(http_request).order_lookup(order_id)
                if order is None:
                    raise HTTPException(status_code=404, detail="查不到这笔订单，请核对订单号。")
            result = buyer_workflow.run(order_id, request.question, trace=trace)
            status_code = 200
        except HTTPException as error:
            status_code, message = error.status_code, error.detail
        except ModelError as error:
            status_code, message = 502, str(error)
        except (McpEvidenceError, OrderStoreError) as error:
            status_code, message = 503, str(error)
        finally:
            snapshot = trace.snapshot()
            if trace_sink is not None:
                trace_sink({"http_status": status_code, **snapshot})
        if status_code != 200:
            return JSONResponse({"message": message, "trace": snapshot}, status_code=status_code, headers=headers)
        response.headers.update(headers)
        return {**result, "trace": snapshot}

    @app.post(
        "/api/olist/orders/{order_id}/items", response_model=OlistItemsResponse,
        responses={
            400: {"model": ErrorResponse, "description": "样本编号或商品问题无效"},
            401: {"model": ErrorResponse, "description": "需要登录"},
            403: {"model": ErrorResponse, "description": "需要买家权限"},
            404: {"model": ErrorResponse, "description": "样本不可查询"},
            503: {"model": ErrorResponse, "description": "商品明细服务不可用"},
        },
    )
    def lookup_olist_items(order_id: OlistOrderNumber, question: QuestionRequest, request: Request) -> dict:
        """验证订单归属，再查商品记录并生成精确金额。"""
        order_id = order_id.upper()
        account = request.state.account
        not_found = "查不到这个 Olist 样本编号，请核对后重试。"
        if account is not None and order_owner(order_id) != account.account_id:
            raise HTTPException(status_code=404, detail=not_found)
        if items_lookup is None:
            raise HTTPException(status_code=503, detail="当前工作台没有接入 Olist 商品明细。")
        items = items_lookup(order_id)
        if items is None:
            raise HTTPException(status_code=404, detail=not_found)
        return answer_items(order_id, question.question, items)

    default_openapi = app.openapi

    def workbench_openapi() -> dict:
        """让 OpenAPI 文档使用工作台实际返回的校验状态码。"""
        schema = default_openapi()
        # 请求校验由 invalid_request 返回 400，文档与实际状态码保持一致。
        for path in BODY_ERRORS:
            schema["paths"][path]["post"]["responses"].pop("422", None)
        for path, method in (
            ("/api/tickets/{ticket_no}/progress", "get"),
            ("/api/staff/tickets/{ticket_no}", "get"),
            ("/api/staff/tickets/search", "get"),
            ("/api/staff/tickets/{ticket_no}/resolve", "post"),
            ("/api/staff/tickets/{ticket_no}/policy-search", "post"),
            ("/api/orders/{order_id}/policy-assistance", "post"),
            ("/api/olist/orders/{order_id}/items", "post"),
            ("/api/conversation/{conversation_id}", "get"),
            ("/api/buyer/conversations", "get"),
            ("/api/buyer/orders", "get"),
            ("/api/buyer/orders/{order_id}", "get"),
            ("/api/buyer/orders/{order_id}/after-sales", "get"),
            ("/api/staff/orders", "get"),
            ("/api/staff/orders/{order_id}", "get"),
        ):
            schema["paths"][path][method]["responses"].pop("422", None)
        return schema

    app.openapi = workbench_openapi
    return app


def configured_app() -> FastAPI:
    """为本机服务接上配置中的 DeepSeek、订单和工单仓储。"""
    api_key = local_setting("DEEPSEEK_API_KEY")
    database_url = local_setting("ORDER_DATABASE_URL")
    credentials = load_credentials()
    directory_credentials = (load_credentials(DIRECTORY_ACCOUNT_FILE, accounts=DIRECTORY_ROLES)
                             if database_url and DIRECTORY_ACCOUNT_FILE.is_file() else {})
    if directory_credentials:
        credentials = {"staff": credentials["staff"]}
        merchant_credentials = {}
    else:
        merchant_credentials = (load_credentials(MERCHANT_ACCOUNT_FILE, accounts=MERCHANT_SHOPS)
                                if MERCHANT_ACCOUNT_FILE.is_file() else {})
    milvus_url = local_setting("MILVUS_URL")
    checkpoints = DialogueCheckpoints(database_url) if database_url else None
    mall = PostgresMall(database_url, deepseek_mall_planner(api_key, semantic=True) if api_key else None,
                        writer=deepseek_mall_writer(api_key) if api_key else None,
                        model_dialogue=bool(api_key), composer=grounded_writer(api_key) if api_key else None,
                        retrieval=MallRetrieval(milvus_url,persistent_dense=local_setting('MALL_PERSISTENT_DENSE')=='1',
                            window_rerank=local_setting('MALL_WINDOW_RERANK')=='1') if api_key else None,
                        reviewer=grounded_reviewer(api_key) if api_key else None,read_decider=evidence_decider(api_key) if api_key else None,business_decider=business_decider(api_key) if api_key else None) if database_url else None
    names = {}
    if mall is not None:
        try:
            with mall.connect() as conn:
                names = MallDirectory.account_names(conn)
        except ServiceError:
            # 账号编号登录仍可用，档案查询恢复后重启即可加载名称。
            pass
    policy_assistant = None
    if milvus_url and database_url:
        index = MilvusPolicies(milvus_url)
        writer = deepseek_policy_writer(api_key) if api_key else None
        reviewer = deepseek_policy_reviewer(api_key) if api_key else None
        aspect_writer = deepseek_aspect_writer(api_key) if api_key else None
        policy_assistant = PolicyAssistant(
            index.search, writer, plan_lookup=index.search_evidence, aspect_writer=aspect_writer, reviewer=reviewer,
        )
    app = create_app(
        model=deepseek_tool(api_key) if api_key else None,
        orders=PostgresOrders(database_url) if database_url else None,
        tickets=PostgresTickets(database_url) if database_url else None,
        mall=mall,
        merchant_requests=PostgresMerchantRequests(database_url) if database_url and merchant_credentials else None,
        policy_assistant=policy_assistant,
        trace_sink=write_policy_trace,
        order_checkpointer=checkpoints.saver if checkpoints else None,
        conversation_thread_list=checkpoints.list_threads if checkpoints else None,
        lifespan=checkpoints.lifespan if checkpoints else None,
        access=DemoAccess({**credentials, **merchant_credentials, **directory_credentials}, names=names),
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    return app


def main() -> None:
    """默认监听本机；容器显式设置绑定地址并只映射宿主本机端口。"""
    uvicorn.run(configured_app(), host=local_setting("AFTER_SALES_BIND_HOST") or "127.0.0.1", port=8765)
