"""把自然语言查单请求交给确定性的售后规则。"""

import json
import re
from collections.abc import Callable
from urllib.error import URLError
from urllib.request import Request, urlopen

from app.orders.rules import DEMO_DATE, OrderLookup, check_return, get_order


ORDER_ID_PATTERN = re.compile(r"(?<![A-Za-z0-9])A[0-9]{3}(?![A-Za-z0-9])", re.I)
DeepSeekTool = Callable[[str], dict]


class ModelError(Exception):
    """模型服务失败或返回了不可执行的工具参数。"""


def find_order_ids(question: str) -> set[str]:
    """从问题中提取唯一化的模拟订单编号，并统一为大写。"""
    return {value.upper() for value in ORDER_ID_PATTERN.findall(question)}


def deepseek_chat(api_key: str, payload: dict, *, timeout: float = 15) -> dict:
    """调用 DeepSeek，返回第一条回复，统一处理连接和响应错误。"""
    request = Request(
        "https://api.deepseek.com/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            message = json.load(response)["choices"][0]["message"]
            if isinstance(message, dict):
                return message
    except (URLError, TimeoutError, OSError, ValueError, KeyError, IndexError, TypeError):
        pass
    raise ModelError("DeepSeek 请求失败，请检查本机 API 配置或稍后重试。")


def request_function(api_key: str, payload: dict, *, timeout: float = 15) -> dict:
    """从 DeepSeek 回复中提取唯一的函数调用。"""
    message = deepseek_chat(api_key, payload, timeout=timeout)
    try:
        calls = message["tool_calls"]
        if len(calls) == 1 and calls[0]["type"] == "function":
            return calls[0]["function"]
    except (KeyError, IndexError, TypeError):
        pass
    raise ModelError("DeepSeek 返回的工具调用不符合查询要求。")


def deepseek_tool(api_key: str) -> DeepSeekTool:
    """使用本机密钥构建一个向 DeepSeek 请求查单工具的函数。"""
    def request_tool(question: str) -> dict:
        """发送工具定义和用户问题，取出模型提出的单个工具调用。"""
        payload = {
            "model": "deepseek-flash",
            "thinking": {"type": "disabled"},
            "messages": [
                {
                    "role": "system",
                    "content": "从用户输入提取唯一的售后订单号，调用 get_order。不要更改订单号。",
                },
                {"role": "user", "content": question},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_order",
                        "description": "查询模拟订单，例如 A102。",
                        "parameters": {
                            "type": "object",
                            "properties": {"order_id": {"type": "string"}},
                            "required": ["order_id"],
                            "additionalProperties": False,
                        },
                    },
                }
            ],
            "tool_choice": {"type": "function", "function": {"name": "get_order"}},
            "max_tokens": 128,
        }
        return request_function(api_key, payload)

    return request_tool


def validate_order_tool(question: str, order_id: str, model: DeepSeekTool) -> str:
    """验证工具名称、参数和订单号与程序识别的订单一致。"""
    tool = model(question)
    try:
        arguments = json.loads(tool["arguments"])
        proposed_id = arguments["order_id"]
        if (
            tool["name"] != "get_order"
            or set(arguments) != {"order_id"}
            or not isinstance(proposed_id, str)
            or proposed_id.strip().upper() != order_id
        ):
            raise ValueError
    except (KeyError, ValueError, TypeError):
        raise ModelError("模型返回的订单号或工具参数无法验证。") from None
    return proposed_id.strip().upper()


def query_after_sales(
    question: str,
    return_window_days: int,
    model: DeepSeekTool | None = None,
    *,
    order_lookup: OrderLookup = get_order,
) -> dict:
    """执行单轮查单，验证可选的模型调用，并用指定期限判断退货。"""
    order_ids = find_order_ids(question)
    order_id = next(iter(order_ids)) if len(order_ids) == 1 else ""
    mode = "demo"

    if model is not None and order_id:
        order_id = validate_order_tool(question, order_id, model)
        mode = "deepseek"

    result = check_return(order_id, DEMO_DATE, return_window_days, order_lookup=order_lookup)
    if len(order_ids) > 1:
        result["message"] = "请每次提供一个订单号。"
    return {"mode": mode, "result": result}
