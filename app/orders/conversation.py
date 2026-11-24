"""用 LangGraph 追问订单号并继续查询已确认的售后订单。"""

from collections.abc import Callable
from dataclasses import asdict, dataclass
from operator import add
from threading import Lock
from typing import Annotated, NamedTuple
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt
from typing_extensions import NotRequired, TypedDict

from app.orders.rules import DEMO_DATE, Order, OrderLookup, PolicyLookup, evaluate_return
from app.integrations.deepseek import ORDER_ID_PATTERN, DeepSeekTool, find_order_ids, validate_order_tool
from app.orders.logistics import HISTORICAL_ORDER_PATTERN, LogisticsLookup, LogisticsOrder, classify_logistics, logistics_result
from app.orders.dialogue_tools import HUMAN_REVIEW_ONLY, HUMAN_REVIEW_REQUEST, OrderToolRouter, select_order_tools
from app.orders.olist_items import ITEM_IDENTITY_QUESTION, ORDER_DETAILS_QUESTION, ItemsLookup, answer_items, describe_items
from app.orders.shop_questions import order_question_text, select_historical_questions, select_shop_question
from app.policies.evidence import split_aspects
from app.tickets.service_requests import RETURN_FREIGHT, SERVICE_PROGRESS, ServiceReason, detect_return_reason
from app.tickets.workflow import TicketRecord


RETRY_NODES = {("query_order",), ("apply_shop_policy",), ("query_logistics",)}


def find_query_ids(question: str) -> set[str]:
    """识别已支持的售后订单编号。"""
    return find_order_ids(question) | {value.upper() for value in HISTORICAL_ORDER_PATTERN.findall(question)}


class ConversationConflict(Exception):
    """会话无法按当前输入继续。"""


class ConversationOrderMissing(Exception):
    """入口指向了当前买家查不到的订单。"""


class ThreadPosition(NamedTuple):
    """会话最新检查点的位置，供按时间倒序分页。"""

    thread_id: str
    checkpoint_id: str
    updated_at: str


ThreadList = Callable[[str, str | None, int], list[ThreadPosition]]


def shop_order_result(order: Order, policy_lookup: PolicyLookup) -> dict:
    """以真实签收日期和店铺条款生成当前订单摘要。"""
    policy = policy_lookup(order.shop_id) if order.shop_id else None
    if policy is None:
        return {"status": "policy_unavailable", "order_id": order.order_id, "signed_on": order.signed_on,
                "message": "这笔订单缺少适用的店铺条款，暂时无法判断退货期限。"}
    result = evaluate_return(order, DEMO_DATE, policy.return_window_days)
    result.update(shop_name=policy.shop_name, policy_name=policy.policy_name, return_window_days=policy.return_window_days)
    return result


def conversation_scope(order_id: str, shop_id: str | None = None, *, seller_id: str | None = None) -> str:
    """有店铺身份的订单按店铺归组，其余订单使用独立会话。"""
    if seller_id:
        return f"olist-seller:{seller_id}"
    return f"shop:{shop_id}" if shop_id else f"order:{order_id}"


def order_conversation_scope(order: Order | LogisticsOrder) -> str:
    """从店铺订单或单卖家历史订单取得所属的服务会话。"""
    if isinstance(order, LogisticsOrder):
        return conversation_scope(order.order_id, seller_id=order.seller_id)
    return conversation_scope(order.order_id, order.shop_id)


class OrderCard(TypedDict):
    """对话附带的可核对订单摘要。"""

    order_id: str
    status: str
    items_summary: NotRequired[str]
    recorded_total: NotRequired[str]


class ConversationTurn(TypedDict):
    """与一条用户消息对应的已返回结果。"""

    question: str
    result: dict
    scope: NotRequired[str | None]
    order_card: NotRequired[OrderCard]


class ConversationState(TypedDict):
    """描述 LangGraph 各节点之间传递的问题、回复和查询结果。"""

    question: str
    reply: NotRequired[str | None]
    context_order: NotRequired[str | None]
    order: NotRequired[dict | None]
    response: NotRequired[dict | None]
    scope: NotRequired[str | None]
    opening: NotRequired[bool]
    card: NotRequired[OrderCard | None]
    turns: NotRequired[Annotated[list[ConversationTurn], add]]


@dataclass(frozen=True)
class ConversationRun:
    """保存仅在当前调用中使用的订单事实回调。"""

    on_facts: Callable[[dict], None] | None = None


class OrderConversation:
    """处理缺少订单号时的追问，以及查单后应用店铺政策的流程。"""

    def __init__(
        self, model: DeepSeekTool | None, order_lookup: OrderLookup, policy_lookup: PolicyLookup,
        *, checkpointer: BaseCheckpointSaver | None = None, logistics_lookup: LogisticsLookup | None = None,
        account_id: str | None = None, olist_router: OrderToolRouter | None = None,
        items_lookup: ItemsLookup | None = None, ticket_lookup: Callable[[str], TicketRecord | None] | None = None,
        shop_answer: Callable[[str, str], dict] | None = None, card_items_lookup: ItemsLookup | None = None,
        shop_review: Callable[[Order], dict] | None = None,
        thread_list: ThreadList | None = None, olist_answer: Callable[[str, str], dict] | None = None,
        jd_answer: Callable[[str, str], dict] | None = None,
        jd_freight_answer: Callable[[LogisticsOrder, ServiceReason | None], dict] | None = None,
        jd_service_progress: Callable[[str], dict] | None = None,
    ):
        """注入模型和查询函数，定义节点、条件分支与检查点存储。"""
        self.mode = "deepseek" if model is not None else "demo"
        self.account_id = account_id
        self.order_lookup = order_lookup
        self.logistics_lookup = logistics_lookup
        self.policy_lookup = policy_lookup
        self.card_items_lookup = card_items_lookup or items_lookup
        self.checkpointer = checkpointer if checkpointer is not None else InMemorySaver()
        self.thread_list = thread_list

        def user_text(state: ConversationState) -> str:
            """取当前轮的用户原话或填入的订单编号。"""
            return state.get("reply") or state["question"]

        def record_result(state: ConversationState, mode: str, result: dict, *, seller_id: str | None = None) -> dict:
            """把可回看的答复随同业务结果一起存入检查点。"""
            order_id = result.get("order_id")
            scope = state.get("scope")
            if order_id:
                scope = conversation_scope(order_id, (state.get("order") or {}).get("shop_id"), seller_id=seller_id)
            if card := state.get("card"):
                turns = [{"question": "发送订单", "result": result, "scope": scope, "order_card": card}]
            else:
                turns = [] if state.get("opening") else [{"question": user_text(state), "result": result, "scope": scope}]
            return {"response": {"mode": mode, "result": result}, "context_order": result.get("order_id"),
                    "scope": scope, "turns": turns}

        def current_ids(state: ConversationState) -> set[str]:
            """尊重本轮明确编号，省略编号时使用已确认的订单。"""
            reply = state.get("reply")
            identifiers = find_query_ids(reply or state["question"])
            if not identifiers and reply is None and state.get("context_order"):
                return {state["context_order"]}
            return identifiers

        def route_input(state: ConversationState) -> str:
            """根据当前消息是否只有一个订单号，决定查单还是追问。"""
            identifiers = current_ids(state)
            if len(identifiers) != 1:
                return "ask_order_id"
            return "query_logistics" if HISTORICAL_ORDER_PATTERN.fullmatch(next(iter(identifiers))) else "query_order"

        def ask_order_id(state: ConversationState) -> dict:
            """向页面提示所缺信息，暂停直到收到同一会话的新回复。"""
            current_input = user_text(state)
            if len(find_query_ids(current_input)) > 1:
                prompt = "请每次提供一个订单号。"
            else:
                prompt = "请发送订单卡，或从我的订单复制订单号。"
            reply = interrupt({"message": prompt})
            return {"reply": reply, "turns": [prompt_turn(current_input, prompt, state.get("scope"))]}

        def query_order(state: ConversationState) -> dict:
            """校验用户确认的订单号，通过已有工具和数据源获取订单。"""
            question = state["question"]
            order_id = next(iter(current_ids(state)))
            if state.get("reply"):
                # 第一轮可能给了多个编号，使用追问后确认的唯一编号。
                redacted = HISTORICAL_ORDER_PATTERN.sub("（待确认）", ORDER_ID_PATTERN.sub("（待确认）", question))
                question = f"{redacted}\n订单号：{order_id}"
            if state.get("opening"):
                return {"order": state.get("order")}
            if model is not None and (state.get("reply") or find_query_ids(state["question"])):
                order_id = validate_order_tool(question, order_id, model)
            order = order_lookup(order_id)
            return {"order": asdict(order) if order is not None else None}

        def apply_shop_policy(state: ConversationState) -> dict:
            """按订单所属店铺查政策，组装退货结论与规则依据。"""
            record = state["order"]
            if record is None:
                result = {"status": "order_not_found", "message": "查不到这笔订单。"}
            else:
                order = Order(**record)
                result = shop_order_result(order, policy_lookup)
                if not state.get("opening"):
                    text = "\n".join(filter(None, (state["question"], state.get("reply"))))
                    if ORDER_DETAILS_QUESTION.search(text):
                        result["order_detail_requested"] = True
                    if ITEM_IDENTITY_QUESTION.search(text):
                        result["items_message"] = "这笔演示订单有签收和店铺记录，商品条目暂缺。"
                        result["order_detail_requested"] = True
                    request = order_question_text(text)
                    only_review = HUMAN_REVIEW_ONLY.fullmatch(request)
                    if shop_review is not None and (only_review or HUMAN_REVIEW_REQUEST.search(request)):
                        result["human_review"] = shop_review(order)
                    if only_review:
                        return record_result(state, self.mode, result)
                    clause_question, show_summary = select_shop_question(state["question"], state.get("reply"))
                    if clause_question and shop_answer is not None:
                        result["policy_assistance"] = shop_answer(order.order_id, clause_question)
                        result["show_order_summary"] = show_summary
            return record_result(state, self.mode, result)

        def query_logistics(state: ConversationState, runtime: Runtime[ConversationRun]) -> dict:
            """核对订单归属，再执行模型或规则选出的只读操作。"""
            order_id = next(iter(current_ids(state)))
            order = None
            if logistics_lookup is None:
                result = {"status": "logistics_unavailable", "message": "当前无法查询历史配送记录。"}
            else:
                order = LogisticsOrder(**state["order"]) if state.get("opening") else logistics_lookup(order_id)
                result = logistics_result(order) if order is not None else {
                    "status": "order_not_found", "message": "查不到这个订单，请核对编号后重试。",
                }
            if "logistics" not in result or state.get("opening"):
                return record_result(state, "demo", result, seller_id=order.seller_id if order else None)

            question = "\n".join(filter(None, (state["question"], state.get("reply"))))
            question = HISTORICAL_ORDER_PATTERN.sub("", ORDER_ID_PATTERN.sub("", question)).strip()
            if ORDER_DETAILS_QUESTION.search(question):
                result["order_detail_requested"] = True
            if order.origin == "jd" and jd_service_progress is not None:
                parts = split_aspects(question) or [question]
                other = [part for part in parts if not (SERVICE_PROGRESS.search(part) and not RETURN_FREIGHT.search(part))]
                if len(other) < len(parts):
                    result["service_progress"] = jd_service_progress(order_id)
                    question = "？".join(other)
            reference_answer = jd_answer if order.origin == "jd" else olist_answer
            freight_available = order.origin == "jd" and jd_freight_answer is not None
            clause_question, order_question = (select_historical_questions(question)
                if reference_answer is not None or (freight_available and RETURN_FREIGHT.search(question)) else ("", question))
            if order.origin == "jd" and jd_freight_answer is not None:
                reason = detect_return_reason(question)
                clauses = split_aspects(clause_question) or ([clause_question] if clause_question else [])
                freight = any(RETURN_FREIGHT.search(part) for part in clauses)
                previous = state.get("turns", [])
                if not freight and reason and len(question) <= 32 and previous:
                    last = previous[-1]["result"]
                    freight = (last.get("order_id") == order_id and
                               last.get("return_freight", {}).get("status") == "needs_reason")
                    if freight:
                        order_question = ""
                if freight:
                    result["return_freight"] = jd_freight_answer(order, reason)
                    clause_question = "？".join(part for part in clauses if not RETURN_FREIGHT.search(part))
            plan = (select_order_tools(order_question or question, olist_router, prefer_facts=order.origin == "jd")
                    if order_question or (not clause_question and not {"return_freight", "service_progress"} & result.keys())
                    or HUMAN_REVIEW_REQUEST.search(question)
                    else {"source": "rules", "operations": []})
            result["tool_plan"] = plan
            if "items" in plan["operations"] and items_lookup is not None:
                evidence = answer_items(order_id, order_question or question, items_lookup(order_id) or [])
                evidence["items"] = [{**row, "price": str(row["price"]),
                                      "freight": str(row["freight"]) if row["freight"] is not None else None}
                                     for row in evidence["items"]]
                for key in ("items_total", "freight_total", "recorded_total"):
                    if evidence[key] is not None:
                        evidence[key] = str(evidence[key])
                result["olist_items"] = evidence
            if clause_question and reference_answer is not None:
                if runtime.context and runtime.context.on_facts and (
                    "logistics" in plan["operations"] or {"olist_items", "return_freight", "service_progress"} & result.keys()
                    or result.get("order_detail_requested")
                ):
                    runtime.context.on_facts(dict(result))
                result["policy_assistance"] = reference_answer(order_id, clause_question)
                result["show_order_summary"] = False
            if "human_review" in plan["operations"]:
                result["human_review"] = review_order(order_id, ticket_lookup)
            used_model = plan["source"] == "function_call" or result.get("policy_assistance", {}).get("mcp_tools")
            return record_result(state, "deepseek" if used_model else "demo", result, seller_id=order.seller_id)

        graph = StateGraph(ConversationState, context_schema=ConversationRun)
        graph.add_node("ask_order_id", ask_order_id)
        graph.add_node("query_order", query_order)
        graph.add_node("apply_shop_policy", apply_shop_policy)
        graph.add_node("query_logistics", query_logistics)
        search_nodes = ["ask_order_id", "query_order", "query_logistics"]
        graph.add_conditional_edges(START, route_input, search_nodes)
        graph.add_conditional_edges("ask_order_id", route_input, search_nodes)
        graph.add_edge("query_order", "apply_shop_policy")
        graph.add_edge("apply_shop_policy", END)
        graph.add_edge("query_logistics", END)
        self.graph = graph.compile(checkpointer=self.checkpointer)
        # 同一个会话始终使用同一把锁，避免两个请求同时恢复同一个检查点。
        self.locks = [Lock() for _ in range(32)]

    def _find_order(self, order_id: str) -> Order | LogisticsOrder | None:
        """按订单类型调用已校验归属的数据源。"""
        if HISTORICAL_ORDER_PATTERN.fullmatch(order_id):
            return self.logistics_lookup(order_id) if self.logistics_lookup is not None else None
        return self.order_lookup(order_id)

    def _scope_for(self, order_id: str) -> str | None:
        """从已校验归属的订单数据取得服务对象，忽略客户端提供的店铺名。"""
        order = self._find_order(order_id)
        return order_conversation_scope(order) if order is not None else None

    def _saved_scope(self, state: dict) -> str | None:
        """兼容尚未保存店铺范围的检查点。"""
        if "scope" in state:
            return state["scope"]
        order_id = state.get("context_order")
        return self._scope_for(order_id) if order_id else None

    def _visible_turns(self, turns: list[ConversationTurn], scope: str | None) -> list[ConversationTurn]:
        """只回放本店消息，旧混合会话按每条已确认的订单重新核对。"""
        if scope is None:
            if any("scope" not in turn for turn in turns) and any(turn["result"].get("order_id") for turn in turns):
                return []
            return turns
        resolved: dict[str, str | None] = {}
        visible = []
        for turn in turns:
            turn_scope = turn.get("scope")
            order_id = turn["result"].get("order_id")
            if order_id and "scope" not in turn:
                if order_id not in resolved:
                    resolved[order_id] = self._scope_for(order_id)
                turn_scope = resolved[order_id]
            if turn_scope == scope or ("scope" in turn and turn_scope is None):
                visible.append(turn)
        return visible

    def _shop_thread(self, scope: str) -> UUID:
        """对同一个买家和服务对象生成稳定的会话定位。"""
        return uuid5(NAMESPACE_URL, f"after-sales:merchant:{self.account_id}:{scope}")

    def _order_thread(self, order_id: str, conversation_id: UUID | None) -> tuple[Order | LogisticsOrder, str, UUID]:
        """核对归属并确定当前订单可以访问的店铺会话。"""
        order = self._find_order(order_id)
        if order is None:
            raise ConversationOrderMissing("查不到这笔订单，请核对订单号。")
        scope = order_conversation_scope(order)
        thread_id = self._shop_thread(scope)
        if conversation_id is not None and conversation_id != thread_id:
            previous = self.graph.get_state(self._checkpoint_config(conversation_id))
            if self._saved_scope(previous.values) == scope:
                thread_id = conversation_id
        return order, scope, thread_id

    def _order_card(self, order: Order | LogisticsOrder) -> OrderCard:
        """生成卡片所需的签收状态和匿名商品摘要。"""
        if isinstance(order, Order):
            return {"order_id": order.order_id, "status": "signed" if order.signed_on else "not_signed"}
        card: OrderCard = {"order_id": order.order_id, "status": classify_logistics(order)}
        items = self.card_items_lookup(order.order_id) if self.card_items_lookup is not None else None
        if items:
            card["items_summary"] = describe_items(items)
            totals = answer_items(order.order_id, "", items)
            total = totals["recorded_total"] if totals["recorded_total"] is not None else totals["items_total"]
            card["recorded_total"] = str(total)
        return card

    @staticmethod
    def _card_is_active(state: dict, order_id: str) -> bool:
        """识别已经发送且仍对应当前订单的卡片。"""
        last_card = next((turn["order_card"] for turn in reversed(state.get("turns", [])) if "order_card" in turn), None)
        return bool(last_card and last_card["order_id"] == order_id and state.get("context_order") == order_id)

    def prepare_order(self, order_id: str, conversation_id: UUID | None = None) -> dict:
        """打开店铺对话和待发送卡片，读取历史时不切换回复上下文。"""
        order, scope, thread_id = self._order_thread(order_id, conversation_id)
        with self.locks[thread_id.int % len(self.locks)]:
            snapshot = self.graph.get_state(self._checkpoint_config(thread_id))
        state = snapshot.values
        if not snapshot.next and state.get("response") and self._card_is_active(state, order_id):
            return self._finished_response(thread_id, state)
        turns = finished_turns(state) if state.get("response") else state.get("turns", [])
        result = logistics_result(order) if isinstance(order, LogisticsOrder) else shop_order_result(order, self.policy_lookup)
        return {"conversation_id": str(thread_id), "scope": scope, "awaiting_order_id": False,
                "can_continue": False, "mode": "demo", "result": result, "draft_order": self._order_card(order),
                "turns": self._visible_turns(turns, scope)}

    def open_order(self, order_id: str, conversation_id: UUID | None = None, *, share: bool = False) -> dict:
        """切换当前订单，明确发送卡片时一并写入对话。"""
        order, scope, thread_id = self._order_thread(order_id, conversation_id)
        config = self._checkpoint_config(thread_id)
        with self.locks[thread_id.int % len(self.locks)]:
            snapshot = self.graph.get_state(config)
            if share and not snapshot.next and snapshot.values.get("response") and self._card_is_active(snapshot.values, order_id):
                return self._finished_response(thread_id, snapshot.values)
            turn_input = {"question": order_id, "reply": None, "context_order": order_id,
                          "order": asdict(order),
                          "response": None, "scope": scope, "opening": True,
                          "card": self._order_card(order) if share else None}
            if snapshot.values.get("response") and "turns" not in snapshot.values:
                turn_input["turns"] = finished_turns(snapshot.values)
            output = self.graph.invoke(turn_input, config)
        return self._finished_response(thread_id, output)

    def _prompt_response(self, thread_id: UUID, message: str, state: dict, *, awaiting_retry: bool = False) -> dict:
        """生成页面所需的追问响应。"""
        scope = self._saved_scope(state)
        turns = self._visible_turns(state.get("turns", []), scope)
        if not awaiting_retry:
            turns = [*turns, prompt_turn(state.get("reply") or state["question"], message, scope)]
        return {
            "conversation_id": str(thread_id),
            "scope": scope,
            "awaiting_order_id": True,
            "mode": self.mode,
            "result": {"status": "missing_order_id", "message": message},
            "turns": turns,
        }

    def _finished_response(self, thread_id: UUID, state: dict) -> dict:
        """用同一响应格式返回新结果或已有结果。"""
        response = state["response"]
        scope = self._saved_scope(state)
        turns = self._visible_turns(finished_turns(state), scope)
        return {"conversation_id": str(thread_id), "scope": scope, "awaiting_order_id": False,
                "can_continue": bool(response["result"].get("order_id")), "turns": turns, **response}

    def _checkpoint_config(self, conversation_id: UUID) -> dict:
        """在同一个检查点仓储中按账号隔离会话。"""
        thread_id = f"{self.account_id}:{conversation_id}" if self.account_id else str(conversation_id)
        return {"configurable": {"thread_id": thread_id}}

    def _recent_positions(self, before: str | None, limit: int) -> list[ThreadPosition]:
        """生产从 PostgreSQL 查询，内存模式从已有检查点分页。"""
        prefix = f"{self.account_id}:" if self.account_id else ""
        if self.thread_list is not None:
            return self.thread_list(prefix, before, limit)
        latest: dict[str, ThreadPosition] = {}
        for entry in self.checkpointer.list(None):
            config = entry.config["configurable"]
            thread_id = config["thread_id"]
            if config.get("checkpoint_ns") or not thread_id.startswith(prefix):
                continue
            position = ThreadPosition(thread_id, config["checkpoint_id"], entry.checkpoint["ts"])
            previous = latest.get(thread_id)
            if previous is None or position.checkpoint_id > previous.checkpoint_id:
                latest[thread_id] = position
        return sorted((row for row in latest.values() if before is None or row.checkpoint_id < before),
                      key=lambda row: row.checkpoint_id, reverse=True)[:limit]

    def recent(self, before: str | None, limit: int) -> dict:
        """提供当前买家可恢复会话的标题、最近一轮摘要与时间。"""
        prefix = f"{self.account_id}:" if self.account_id else ""
        positions = self._recent_positions(before, limit + 1)
        conversations = []
        for position in positions[:limit]:
            if not position.thread_id.startswith(prefix):
                continue
            try:
                conversation_id = UUID(position.thread_id[len(prefix):])
                saved = self.restore(conversation_id)
            except (ValueError, ConversationConflict):
                continue
            if not saved["turns"]:
                continue
            last_order = next((turn["result"] for turn in reversed(saved["turns"])
                               if turn["result"].get("order_id")), {})
            current = saved["result"]
            scope = saved.get("scope") or ""
            title = current.get("shop_name") or last_order.get("shop_name")
            if title is None and scope.startswith("olist-seller:"):
                title = "历史卖家 " + scope[-8:].upper()
            order_id = current.get("order_id") or last_order.get("order_id")
            latest = saved["turns"][-1]
            preview = (f"已发送订单 {latest['order_card']['order_id']}" if latest.get("order_card")
                       else " ".join(latest["question"].split()))
            conversations.append({"conversation_id": str(conversation_id), "title": title or "售后咨询",
                                  "order_id": order_id, "preview": preview, "updated_at": position.updated_at,
                                  "awaiting_order_id": saved["awaiting_order_id"]})
        return {"conversations": conversations,
                "next_before": positions[limit - 1].checkpoint_id if len(positions) > limit else None}

    def restore(self, conversation_id: UUID) -> dict:
        """读取已保存的原问题、当前追问或最后的查单结果。"""
        config = self._checkpoint_config(conversation_id)
        with self.locks[conversation_id.int % len(self.locks)]:
            snapshot = self.graph.get_state(config)
        question = snapshot.values.get("question")
        if question and snapshot.interrupts:
            message = snapshot.interrupts[0].value["message"]
            return {"question": question, **self._prompt_response(conversation_id, message, snapshot.values)}
        retry = snapshot.values.get("reply") or (question if snapshot.values.get("context_order") else None)
        if question and snapshot.next in RETRY_NODES and retry:
            return {
                "question": question,
                "retry_reply": retry,
                **self._prompt_response(conversation_id, "上次查单未完成，请重试这次回复。",
                                        snapshot.values, awaiting_retry=True),
            }
        if question and not snapshot.next and snapshot.values.get("response"):
            return {"question": question, **self._finished_response(conversation_id, snapshot.values)}
        raise ConversationConflict("找不到可恢复的会话，请开始新查询。")

    def send(self, question: str, conversation_id: UUID | None, *, active_order_id: str | None = None,
             on_facts: Callable[[dict], None] | None = None) -> dict:
        """启动新查询，或根据会话检查点恢复追问和可重试的查单步骤。"""
        thread_id = conversation_id or uuid4()
        config = self._checkpoint_config(thread_id)
        context = ConversationRun(on_facts)
        switched_order = None
        with self.locks[thread_id.int % len(self.locks)]:
            if conversation_id is None:
                if active_order_id is not None:
                    raise ConversationConflict("请重新选择要咨询的订单后发送。")
                output = self.graph.invoke({"question": question}, config, context=context)
            else:
                snapshot = self.graph.get_state(config)
                if active_order_id is not None and snapshot.values.get("context_order") != active_order_id:
                    raise ConversationConflict("当前订单已切换，请重新选择要咨询的订单后发送。")
                scope = self._saved_scope(snapshot.values)
                identifiers = find_query_ids(question)
                if scope and len(identifiers) == 1 and next(iter(identifiers)) != snapshot.values.get("context_order"):
                    explicit_order = next(iter(identifiers))
                    target_scope = self._scope_for(explicit_order)
                    if target_scope and target_scope != scope:
                        switched_order = explicit_order
                if switched_order:
                    pass  # 离开旧会话的锁后，用目标店铺的检查点处理本轮消息。
                elif snapshot.interrupts:
                    output = self.graph.invoke(Command(resume=question), config, context=context)
                elif snapshot.next in RETRY_NODES:
                    retry = snapshot.values.get("reply") or (
                        snapshot.values.get("question") if snapshot.values.get("context_order") else None
                    )
                    if retry != question:
                        raise ConversationConflict("请重试上一次回复，或开始新查询。")
                    output = self.graph.invoke(None, config, context=context)
                elif not snapshot.next and (previous := snapshot.values.get("response")):
                    if snapshot.values.get("reply") == question:
                        return self._finished_response(thread_id, snapshot.values)
                    result = previous["result"]
                    turn_input = {
                        "question": question, "reply": None, "context_order": result.get("order_id"),
                        "order": None, "response": None, "scope": scope, "opening": False, "card": None,
                    }
                    if "turns" not in snapshot.values:
                        turn_input["turns"] = finished_turns(snapshot.values)
                    output = self.graph.invoke(turn_input, config, context=context)
                else:
                    raise ConversationConflict("查询已结束或会话已失效，请开始新查询。")

        if switched_order:
            opened = self.open_order(switched_order)
            return self.send(question, UUID(opened["conversation_id"]), on_facts=on_facts)
        pending = output.get("__interrupt__")
        if pending:
            return self._prompt_response(thread_id, pending[0].value["message"], output)
        return self._finished_response(thread_id, output)


def prompt_turn(question: str, message: str, scope: str | None) -> ConversationTurn:
    """保存一轮补单号提示及触发该提示的问题。"""
    return {"question": question, "result": {"status": "missing_order_id", "message": message}, "scope": scope}


def finished_turns(state: dict) -> list[ConversationTurn]:
    """返回逐轮结果，并兼容只存最后一次答复的旧检查点。"""
    if "turns" in state and (state["turns"] or "scope" in state):
        return state["turns"]
    return [{"question": state.get("reply") or state["question"], "result": state["response"]["result"]}]


def review_order(order_id: str, lookup: Callable[[str], TicketRecord | None] | None,
                 *, merchant_ready: bool = True) -> dict:
    """将人工复核请求转换为已有工单信息或预览入口。"""
    if lookup is None:
        return {"status": "unavailable", "message": "当前工作台未启用人工工单。"}
    ticket = lookup(order_id)
    if ticket is None:
        if not merchant_ready:
            return {"status": "merchant_first", "message": "可以先向店铺提交售后申请，需要时再申请平台客服介入。"}
        return {"status": "preview", "message": "请填写需要人工协助的问题，确认后提交给平台客服。"}
    ticket_no = str(ticket["ticket_no"])
    state = "已处理" if ticket["status"] == "resolved" else "等待人工处理"
    return {"status": "existing", "ticket_no": ticket_no, "ticket_status": ticket["status"],
            "message": f"这笔订单已有工单 #{ticket_no}，{state}。"}
