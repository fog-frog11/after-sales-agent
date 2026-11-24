"""用 LangGraph 预览工单，收到明确确认后调用工单仓储。"""

from collections.abc import Callable
from datetime import datetime
from threading import Lock
from typing import Literal, Protocol
from uuid import UUID, uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from typing_extensions import NotRequired, TypedDict

from app.orders.logistics import HISTORICAL_ORDER_PATTERN, LogisticsLookup, logistics_result
from app.orders.rules import OrderLookup
from app.tickets.handoff import TicketHandoff, TicketHandoffUnavailable, build_ticket_handoff


class TicketStoreError(Exception):
    """工单数据库查询或写入失败。"""


class TicketPreviewUnavailable(Exception):
    """预览已经失效，用户需要重新核对提交内容。"""


class TicketOrderNotFound(Exception):
    """预览关联的订单不存在。"""


class TicketRecord(TypedDict):
    """确认后返回给页面的工单记录。"""

    ticket_no: int
    order_id: str
    reason: str
    status: str


class TicketDetailRecord(TicketRecord):
    """包含处理说明和时间的工单记录，待处理时这两项可为空。"""

    resolution: NotRequired[str | None]
    resolved_at: NotRequired[datetime | None]
    resolved_by: NotRequired[str | None]
    handoff: NotRequired[TicketHandoff | None]


class TicketSearchRecord(TypedDict):
    """客服列表所需的编号、状态和提交时间。"""

    ticket_no: int
    order_id: str
    reason: str
    status: str
    created_at: datetime


class OrderTicketRecord(TicketDetailRecord):
    """订单进度栏需要的工单创建时间和处理结果。"""

    created_at: datetime


TicketStatusFilter = Literal["all", "pending", "resolved"]


class TicketStore(Protocol):
    """定义工单流程所需的持久化接口，测试可注入模拟仓储。"""

    def get(self, confirmation_id: UUID) -> TicketRecord | None:
        """按确认 ID 查找之前提交的工单。"""
        ...

    def create(self, confirmation_id: UUID, order_id: str, reason: str,
               *, handoff: TicketHandoff | None = None) -> TicketRecord:
        """创建工单，重复的确认 ID 返回已存在的记录。"""
        ...

    def find_olist_ticket(self, order_id: str) -> TicketRecord | None:
        """查找可复用的历史物流异常工单。"""
        ...

    def find_order_ticket(self, order_id: str, *, pending_only: bool = False) -> OrderTicketRecord | None:
        """优先读取当前待处理的工单，其次读取最近已处理工单。"""
        ...

    def get_by_number(self, ticket_no: int) -> TicketDetailRecord | None:
        """按工单号读取人工处理进度。"""
        ...

    def list_pending(self) -> list[TicketRecord]:
        """按工单号列出等待处理的工单。"""
        ...

    def search(self, *, status: TicketStatusFilter, order_id: str | None,
               before: int | None, limit: int) -> list[TicketSearchRecord]:
        """按筛选条件和排序位置读取客服列表。"""
        ...

    def resolve(self, ticket_no: int, resolution: str, *, actor: str | None = None) -> TicketDetailRecord | None:
        """保存第一次人工处理，未命中待处理记录时返回空值。"""
        ...


class TicketState(TypedDict):
    """保存正在等待确认的订单和原因，以及提交后的结果。"""

    confirmation_id: str
    order_id: str
    reason: str
    logistics: NotRequired[dict]
    handoff: NotRequired[TicketHandoff]
    ticket: NotRequired[TicketRecord]


class SupportTicketFlow:
    """管理预览暂停、确认恢复和已提交工单的重试。"""

    def __init__(self, order_lookup: OrderLookup, store: TicketStore,
                 *, owns_order: Callable[[str], bool] | None = None,
                 logistics_lookup: LogisticsLookup | None = None,
                 conversation_lookup: Callable[[UUID], dict] | None = None):
        """注入现有查单函数和工单仓储，编译工单流程。"""
        self.order_lookup = order_lookup
        self.store = store
        self.owns_order = owns_order
        self.logistics_lookup = logistics_lookup
        self.conversation_lookup = conversation_lookup
        self.locks = [Lock() for _ in range(32)]

        def wait_for_confirmation(state: TicketState) -> dict:
            """将订单与原因交给页面，暂停等待用户确认。"""
            preview = {
                "confirmation_id": state["confirmation_id"],
                "order_id": state["order_id"],
                "reason": state["reason"],
            }
            if "logistics" in state:
                preview["logistics"] = state["logistics"]
            if "handoff" in state:
                preview["handoff"] = state["handoff"]
            interrupt(preview)
            return {}

        def create_ticket(state: TicketState) -> dict:
            """在确认后写入检查点里预览过的订单和原因。"""
            args = (UUID(state["confirmation_id"]), state["order_id"], state["reason"])
            ticket = store.create(*args, handoff=state["handoff"]) if "handoff" in state else store.create(*args)
            return {"ticket": ticket}

        graph = StateGraph(TicketState)
        graph.add_node("wait_for_confirmation", wait_for_confirmation)
        graph.add_node("create_ticket", create_ticket)
        graph.add_edge(START, "wait_for_confirmation")
        graph.add_edge("wait_for_confirmation", "create_ticket")
        graph.add_edge("create_ticket", END)
        self.graph = graph.compile(checkpointer=InMemorySaver())

    def preview(self, order_id: str, reason: str, *, conversation_id: UUID | None = None) -> dict:
        """验证订单存在，再创建只保存在会话内存里的预览。"""
        order_id = order_id.strip().upper()
        confirmation_id = str(uuid4())
        state: TicketState = {"confirmation_id": confirmation_id, "order_id": order_id, "reason": reason}
        conversation = None
        signed_on = None
        if HISTORICAL_ORDER_PATTERN.fullmatch(order_id):
            logistics = self.logistics_lookup(order_id) if self.logistics_lookup is not None else None
            if logistics is None:
                raise TicketOrderNotFound("查不到这笔订单，请核对订单号。")
            evidence = logistics_result(logistics)
            state.update(order_id=logistics.order_id, logistics=evidence["logistics"])
            existing = (self.store.find_olist_ticket(logistics.order_id) if logistics.origin == "olist"
                        else self.store.find_order_ticket(logistics.order_id, pending_only=True))
        else:
            order = self.order_lookup(order_id)
            if order is None:
                raise TicketOrderNotFound("查不到这笔订单，请核对订单号。")
            state["order_id"] = order.order_id
            signed_on = order.signed_on
            existing = self.store.find_order_ticket(order.order_id, pending_only=True)
        if conversation_id is not None:
            if self.conversation_lookup is None:
                raise TicketHandoffUnavailable("当前无法读取对话，请重新查单后预览。")
            conversation = self.conversation_lookup(conversation_id)
        if conversation is not None or "logistics" in state:
            state["handoff"] = build_ticket_handoff(state["order_id"], reason, state.get("logistics"), conversation,
                                                    signed_on=signed_on)
        if existing is not None:
            result = {"order_id": state["order_id"], "reason": existing["reason"],
                      "existing_ticket_no": str(existing["ticket_no"]), "existing_ticket_status": existing["status"]}
            if "logistics" in state:
                result["logistics"] = state["logistics"]
            return result
        config = {"configurable": {"thread_id": confirmation_id}}
        output = self.graph.invoke(state, config)
        return output["__interrupt__"][0].value

    def confirm(self, confirmation_id: UUID) -> TicketRecord:
        """返回已提交工单，或恢复本次预览并执行写入。"""
        config = {"configurable": {"thread_id": str(confirmation_id)}}
        # 进程内同一份预览串行恢复，数据库唯一约束处理写入重试。
        with self.locks[confirmation_id.int % len(self.locks)]:
            existing = self.store.get(confirmation_id)
            if existing is not None:
                if self.owns_order is not None and not self.owns_order(existing["order_id"]):
                    raise TicketPreviewUnavailable("工单预览已失效，请重新预览后提交。")
                return existing

            checkpoint = self.graph.get_state(config)
            if checkpoint.interrupts:
                output = self.graph.invoke(Command(resume=True), config)
            elif checkpoint.next == ("create_ticket",):
                output = self.graph.invoke(None, config)
            else:
                raise TicketPreviewUnavailable("工单预览已失效，请重新预览后提交。")
            return output["ticket"]
