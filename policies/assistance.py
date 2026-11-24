"""编排材料整理 Agent 和客服 Agent 共同回答买家的政策问题。"""

import json
from collections.abc import Callable

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from typing_extensions import NotRequired, TypedDict

from app.integrations.deepseek import ModelError, request_function
from app.integrations.mcp.client import EVIDENCE_TOOLS, PolicyEvidence
from app.integrations.mcp.queries import OrderSummary
from app.policies.evidence import PolicyAspect
from app.policies.jd_reference import JD_REFERENCE_SOURCES, label_jd_reference
from app.policies.olist_reference import OLIST_REFERENCE_SCOPE, label_olist_reference
from app.policies.search import PolicyClaim, PolicyReviewer, PolicySource, deepseek_aspect_writer, deepseek_policy_writer, explain_policy
from app.policies.trace import PolicyRequestTrace


ToolDecision = Callable[[str, str], dict]
EvidenceLookup = Callable[[str, str], PolicyEvidence]
CustomerWriter = Callable[[str, list[PolicySource], OrderSummary], str | None]
CustomerAspectWriter = Callable[[str, list[PolicySource], list[PolicyAspect], OrderSummary], list[dict]]


def deepseek_evidence_agent(api_key: str) -> ToolDecision:
    """创建材料整理 Agent，让模型提交政策检索的函数参数。"""
    def choose(order_id: str, question: str) -> dict:
        """让 DeepSeek 按当前订单和原问题请求检索工具。"""
        return request_function(api_key, {
            "model": "deepseek-flash",
            "thinking": {"type": "disabled"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是售后材料整理助手。调用 search_shop_policy 查询售后规则，"
                        "逐字使用 JSON 中的 order_id 和 question 字段，保留问题的标点。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps({"order_id": order_id, "question": question}, ensure_ascii=False),
                },
            ],
            "tools": [{
                "type": "function",
                "function": {
                    "name": "search_shop_policy",
                    "description": "按已确认订单的适用范围检索售后规则。",
                    "parameters": {
                        "type": "object",
                        "properties": {"order_id": {"type": "string"}, "question": {"type": "string"}},
                        "required": ["order_id", "question"],
                        "additionalProperties": False,
                    },
                },
            }],
            "tool_choice": {"type": "function", "function": {"name": "search_shop_policy"}},
            "temperature": 0,
            "max_tokens": 700,
        })

    return choose


def deepseek_customer_agent(api_key: str) -> CustomerWriter:
    """创建客服 Agent，使用整理完的订单和原文回答买家。"""
    def write(question: str, sources: list[PolicySource], order: OrderSummary) -> str | None:
        """交接订单上下文和条款，日期与期限由页面的订单卡展示。"""
        writer = deepseek_policy_writer(api_key, order={
            "order_id": order.order_id,
            "shop_name": order.shop_name,
            "policy_name": order.policy_name,
        })
        return writer(question, sources)

    return write


def deepseek_customer_aspect_agent(api_key: str) -> CustomerAspectWriter:
    """为同一个 MCP 订单和逐项证据创建客服生成角色。"""
    def write(question: str, sources: list[PolicySource], aspects: list[PolicyAspect],
              order: OrderSummary) -> list[dict]:
        """把订单背景和各事项可用的原文交给同一次模型调用。"""
        writer = deepseek_aspect_writer(api_key, order={"order_id": order.order_id, "shop_name": order.shop_name,
                                                      "policy_name": order.policy_name})
        return writer(question, sources, aspects)

    return write


class AssistanceState(TypedDict):
    """保存两名 Agent 在节点之间交接的问题、订单和依据。"""

    order_id: str
    question: str
    evidence: NotRequired[PolicyEvidence]
    response: NotRequired[dict]


class BuyerPolicyWorkflow:
    """先调用模型和 MCP 收集材料，再据此生成客服答复。"""

    def __init__(self, choose: ToolDecision, lookup: EvidenceLookup, write: CustomerWriter, *,
                 write_aspects: CustomerAspectWriter | None = None, reviewer: PolicyReviewer | None = None):
        """接入模型角色、适用性复核和 MCP 客户端，编译问答流程。"""
        def collect_evidence(state: AssistanceState, runtime: Runtime[PolicyRequestTrace]) -> dict:
            """验证模型提出的函数调用，再通过 MCP 获取材料。"""
            order_id, question = state["order_id"], state["question"]
            trace = runtime.context
            with trace.stage("model_tool_request"):
                call = choose(order_id, question)
            with trace.stage("tool_validation"):
                try:
                    arguments = json.loads(call["arguments"])
                    if call["name"] != EVIDENCE_TOOLS[1] or arguments != {"order_id": order_id, "question": question}:
                        raise ValueError
                except (KeyError, TypeError, ValueError):
                    raise ModelError("材料整理 Agent 给出的工具参数与当前问题不一致，请重试。") from None
            with trace.stage("mcp_lookup"):
                evidence = lookup(arguments["order_id"], arguments["question"])
            if evidence.policies.retrieval_ms is not None:
                trace.details_ms["milvus_retrieval"] = evidence.policies.retrieval_ms
            return {"evidence": evidence}

        def answer_buyer(state: AssistanceState, runtime: Runtime[PolicyRequestTrace]) -> dict:
            """让客服 Agent 根据材料答复，保留实际调用和原文。"""
            evidence = state["evidence"]
            trace = runtime.context

            def write_with_order(question: str, sources: list[PolicySource]) -> str | None:
                """把同一笔 MCP 订单也送入已有的引用答复流程。"""
                with trace.measure("deepseek_draft"):
                    return write(question, sources, evidence.order)

            def write_aspects_with_order(question: str, sources: list[PolicySource],
                                         aspects: list[PolicyAspect]) -> list[dict]:
                """让逐项客服角色使用本次工具核验过的订单。"""
                assert write_aspects is not None
                with trace.measure("deepseek_draft"):
                    return write_aspects(question, sources, aspects, evidence.order)

            def review_with_timing(question: str, claims: list[PolicyClaim]) -> set[int]:
                """区分复核与起草所用的时间。"""
                assert reviewer is not None
                with trace.measure("answer_verification"):
                    return reviewer(question, claims)

            sources = evidence.policies.sources
            if sources:
                with trace.stage("reply_generation") as stage:
                    result = explain_policy(
                        state["question"], sources, write_with_order, aspects=evidence.policies.aspects,
                        aspect_writer=write_aspects_with_order if write_aspects is not None else None,
                        reviewer=review_with_timing if reviewer is not None else None,
                    )
                    if result["status"] in {"evidence_only", "no_matches"}:
                        stage["status"] = "fallback"
            else:
                trace.skip("reply_generation")
                result = explain_policy(state["question"], sources, None)
            if evidence.policies.shop_id == OLIST_REFERENCE_SCOPE:
                result = label_olist_reference(result)
            elif evidence.policies.shop_id in JD_REFERENCE_SOURCES:
                result = label_jd_reference(result)
            return {"response": {
                **result,
                "order": evidence.order,
                "function_call": EVIDENCE_TOOLS[1],
                "mcp_tools": list(EVIDENCE_TOOLS),
            }}

        graph = StateGraph(AssistanceState, context_schema=PolicyRequestTrace)
        graph.add_node("collect_evidence", collect_evidence)
        graph.add_node("answer_buyer", answer_buyer)
        graph.add_edge(START, "collect_evidence")
        graph.add_edge("collect_evidence", "answer_buyer")
        graph.add_edge("answer_buyer", END)
        self.graph = graph.compile()

    def run(self, order_id: str, question: str, trace: PolicyRequestTrace | None = None) -> dict:
        """对一笔已知订单执行一次政策问答。"""
        return self.graph.invoke(
            {"order_id": order_id, "question": question}, context=trace or PolicyRequestTrace(),
        )["response"]
