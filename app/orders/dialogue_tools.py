"""为历史订单问题选择只读查询或人工服务入口。"""

import json
import re
from collections.abc import Callable
from typing import Literal

from typing_extensions import TypedDict

from app.integrations.deepseek import ModelError, request_function
from app.orders.olist_items import ITEM_IDENTITY_QUESTION, ORDER_DETAILS_QUESTION
from app.policies.evidence import split_aspects


Operation = Literal["logistics", "items", "human_review"]
OrderToolRouter = Callable[[str], dict]
AVAILABLE_OPERATIONS = ("logistics", "items", "human_review")
OPERATIONS_TOOL = "select_order_tools"
FACT_PREFIX = r"(?:(?:请|帮我|我|麻烦|想问|查一下|看一下|这单|这笔订单|这笔|该订单|订单|的)\s*)*"
FACT_END = r"(?:呢|吗|呀|么)?"
FACT_QUESTIONS = (
    ("items", re.compile(FACT_PREFIX + r"(?:买了?(?:几件|多少件|什么|啥|哪些商品)|商品(?:明细|清单|数量)|"
     r"(?:商品)?(?:一共|总共)?(?:几件|多少件|多少钱)|(?:成交)?单价(?:多少|是多少)|"
     r"(?:商品)?(?:金额|总价|件数)(?:多少|是多少)?|(?:运费|配送费)(?:多少|多少钱|是多少))" + FACT_END)),
    ("logistics", re.compile(FACT_PREFIX + r"(?:物流(?:进度|状态)?|配送(?:进度|状态)?|"
     r"(?:什么时候|哪天)(?:签收|送达|发货)(?:的)?|(?:签收|送达|发货)了(?:没有)?|"
     r"(?:是否|有没有)(?:签收|送达|发货)|(?:送|发|到|寄)到哪(?:里|儿)?了?)" + FACT_END)),
)
HUMAN_REVIEW_REQUEST = re.compile(r"转(?:接)?人工|接(?:入|通)人工|人工客服|(?:找|联系|要|叫)人工|平台(?:客服|介入)|人工(?:复核|处理)|^人工[\s。！!？?]*$")
HUMAN_REVIEW_ONLY = re.compile(
    r"(?:请|帮我|麻烦|我想|我要|可以|想|能不能|给我|\s)*(?:转(?:接)?人工(?:客服)?|接通人工|人工(?:客服)?|"
    r"(?:找|联系)(?:个)?(?:平台)?(?:人工)?客服|(?:找|联系)人工|申请(?:平台介入|人工复核|平台客服介入))"
    r"(?:吗|吧|一下)?[\s。！!？?]*"
)


class OrderToolPlan(TypedDict):
    """保存后端已验证的操作列表及其来源。"""

    source: Literal["function_call", "rules"]
    operations: list[Operation]


def explicit_order_operations(question: str) -> list[Operation]:
    """仅快走完整匹配的查件数、金额、物流和人工请求。"""
    operations = []
    for text in split_aspects(question) or [question]:
        text = text.strip(" ，,。！？!?；;")
        operation = ("human_review" if HUMAN_REVIEW_ONLY.fullmatch(text) else
                     next((name for name, pattern in FACT_QUESTIONS if pattern.fullmatch(text)), None))
        if operation is None:
            return []
        if operation not in operations:
            operations.append(operation)
    return operations


def deepseek_order_router(api_key: str) -> OrderToolRouter:
    """用一次 Function Calling 请求识别订单问题里的多项操作。"""
    def choose(question: str) -> dict:
        """只要求模型选择操作，订单编号和查询参数由后端提供。"""
        return request_function(api_key, {
            "model": "deepseek-flash",
            "thinking": {"type": "disabled"},
            "messages": [
                {"role": "system", "content": (
                    "你是售后问题分类器。请调用 select_order_tools，选择问题明确涉及的全部操作。"
                    "物流进度、是否送达、取消状态用 logistics；商品、品类、件数、单价、金额、运费用 items；"
                    "请求人工、客服、提交工单或查询人工进度用 human_review。"
                    "例如‘还没收到，我买了几件？’同时选 logistics 和 items。普通查单默认 logistics。"
                )},
                {"role": "user", "content": question},
            ],
            "tools": [{"type": "function", "function": {
                "name": OPERATIONS_TOOL,
                "description": "选择问题涉及的订单查询和人工复核入口，可选多项。",
                "parameters": {"type": "object", "properties": {
                    "operations": {"type": "array", "items": {"type": "string", "enum": list(AVAILABLE_OPERATIONS)}},
                }, "required": ["operations"], "additionalProperties": False},
            }}],
            "tool_choice": {"type": "function", "function": {"name": OPERATIONS_TOOL}},
            "max_tokens": 160,
            "temperature": 0,
        })
    return choose


def select_order_tools(question: str, router: OrderToolRouter | None, *, prefer_facts: bool = False) -> OrderToolPlan:
    """校验函数调用，遇到模型错误时从当前问题提取本地操作。"""
    if prefer_facts and (known := explicit_order_operations(question)):
        return {"source": "rules", "operations": known}
    item_details = bool(ITEM_IDENTITY_QUESTION.search(question) or ORDER_DETAILS_QUESTION.search(question))
    human_review = bool(HUMAN_REVIEW_REQUEST.search(question) or HUMAN_REVIEW_ONLY.fullmatch(question))
    if router is not None and question.strip():
        try:
            tool = router(question)
            arguments = json.loads(tool["arguments"])
            operations = arguments["operations"]
            if (tool["name"] == OPERATIONS_TOOL and set(arguments) == {"operations"}
                    and isinstance(operations, list) and operations
                    and all(operation in AVAILABLE_OPERATIONS for operation in operations)):
                if item_details and "items" not in operations:
                    operations = [*operations, "items"]
                if human_review and "human_review" not in operations:
                    operations = [*operations, "human_review"]
                return {"source": "function_call", "operations": list(dict.fromkeys(operations))}
        except (ModelError, KeyError, TypeError, ValueError):
            pass
    rules = (
        ("logistics", r"物流|送达|收到|签收|发货|超期|取消|到哪|还没到"),
        ("items", r"商品|明细|买了|品类|几[件个]|件数|数量|价格|单价|多少钱|金额|运费|邮费|配送费"),
        ("human_review", r"人工|客服|工单|复核"),
    )
    operations = [name for name, pattern in rules
                  if re.search(pattern, question) or (name == "items" and item_details)
                  or (name == "human_review" and human_review)]
    return {"source": "rules", "operations": operations or ["logistics"]}
