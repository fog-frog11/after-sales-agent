"""运行本机售后业务评测，并将逐项结果保存为 JSON。"""

import argparse
import json
import re
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

import psycopg

from app.access import load_credentials, order_owner
from app.settings import ROOT, local_setting


BASE_URL = "http://127.0.0.1:8765"
POLICY_QUESTION = "包装破损怎么处理？"
MULTI_POLICY_QUESTION = "我买错了想退货，退货运费谁承担？包装破损要拍哪些照片？"
PARTIAL_POLICY_QUESTION = "包装破损要保留哪些照片？另外能改我的公司发票抬头吗？"
MAX_TICKET_NUMBER = 2**63 - 1


class EvaluationFailure(Exception):
    """某个业务断言或评测依赖失败。"""

    def __init__(self, message: str, *, status_code: int | None = None):
        """保留服务拒绝请求时的 HTTP 状态，供越权用例核对。"""
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class EvaluationCase:
    """描述固定业务用例及其要执行的检查。"""

    case_id: str
    name: str
    check: Callable[[], str]


@dataclass(frozen=True)
class EvaluationOutcome:
    """记录一次检查的结果、说明与耗时。"""

    case_id: str
    name: str
    passed: bool
    detail: str
    duration_ms: int


class WorkbenchClient:
    """按当前售后 API 格式请求本机正在运行的服务。"""

    def __init__(self):
        """为每个评测账号保留独立 Cookie。"""
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))

    def login(self, account_id: str) -> None:
        """读本机演示口令并登录，报告只记录账号名。"""
        try:
            credentials = load_credentials()
        except RuntimeError as error:
            raise EvaluationFailure(str(error)) from None
        session = self.request("/api/session/login", {"account_id": account_id, "secret": credentials[account_id]})
        expect_equal("评测账号", session["account_id"], account_id)

    def request(self, path: str, payload: dict | None = None) -> dict | list:
        """发送 GET 或 POST，并把连接或响应错误写成可读评测信息。"""
        request = Request(
            BASE_URL + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Content-Type": "application/json"},
        )
        # 政策问答包含两次模型请求和本机 MCP 查询。
        timeout = 65 if path.endswith("/policy-assistance") else 24
        try:
            with self.opener.open(request, timeout=timeout) as response:
                result = json.load(response)
        except HTTPError as error:
            with error:
                raise EvaluationFailure(f"{path} 返回 HTTP {error.code}。", status_code=error.code) from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise EvaluationFailure(f"{path} 连接失败或未返回有效 JSON。") from None
        if not isinstance(result, (dict, list)):
            raise EvaluationFailure(f"{path} 的响应格式不符合预期。")
        return result


def expect_equal(label: str, actual, expected) -> None:
    """核对单项结果，失败时列明预期值和实际值。"""
    if actual != expected:
        raise EvaluationFailure(f"{label}预期为 {expected}，实际为 {actual}。")


def verify_runtime(client: WorkbenchClient) -> bool:
    """运行前检查真实评测所需的服务功能已经启用。"""
    config = client.request("/api/config")
    expect_equal("模型模式", config.get("mode"), "deepseek")
    expect_equal("订单来源", config.get("order_source"), "postgresql")
    expect_equal("买家政策问答", config.get("buyer_policy_enabled"), True)
    expect_equal("工单功能", config.get("ticket_enabled"), True)
    return config.get("access_enabled", False)


def check_order_result(response: dict, order_id: str, days: int, status: str) -> str:
    """验证同为签收 8 天的订单受各自店铺政策约束。"""
    expect_equal("模型模式", response["mode"], "deepseek")
    result = response["result"]
    expect_equal("订单号", result["order_id"], order_id)
    expect_equal("签收已过", result["days_since_delivery"], 8)
    expect_equal("退货期限", result["return_window_days"], days)
    expect_equal("判定", result["status"], status)
    label = "在期限内" if status == "eligible" else "已超期"
    return f"{order_id} 签收已过 8 天，{days} 天政策，{label}。"


def check_order(client: WorkbenchClient, order_id: str, days: int, status: str) -> str:
    """从真实工作台按单号查询并核对计算结果。"""
    response = client.request("/api/conversation", {"question": order_id})
    return check_order_result(response, order_id, days, status)


def check_unknown_order(client: WorkbenchClient) -> str:
    """验证工作台能明确拒绝不存在的模拟订单。"""
    response = client.request("/api/conversation", {"question": "A999"})
    expect_equal("判定", response["result"]["status"], "order_not_found")
    return "A999 返回订单不存在。"


def check_resumed_order(client: WorkbenchClient) -> str:
    """先让工作台追问，再取回检查点并继续查询。"""
    question = "这单还能退吗？"
    first = client.request("/api/conversation", {"question": question})
    expect_equal("是否追问", first["awaiting_order_id"], True)
    conversation_id = first["conversation_id"]
    restored = client.request(f"/api/conversation/{conversation_id}")
    expect_equal("恢复的问题", restored["question"], question)
    expect_equal("恢复后的追问", restored["result"]["status"], "missing_order_id")
    response = client.request("/api/conversation", {"question": "A104", "conversation_id": conversation_id})
    expect_equal("是否还在追问", response["awaiting_order_id"], False)
    return "原会话已恢复。" + check_order_result(response, "A104", 15, "eligible")


def check_policy_result(result: dict, order_id: str, article_id: str, evidence_phrase: str) -> str:
    """核对真实工具调用和店铺出处，允许模型答复用不同措辞。"""
    expect_equal("答复状态", result["status"], "answered")
    expect_equal("模型请求的工具", result["function_call"], "search_shop_policy")
    expect_equal("MCP 执行的工具", result["mcp_tools"], ["get_order", "search_shop_policy"])
    expect_equal("订单号", result["order"]["order_id"], order_id)
    sources = result["sources"]
    expect_equal("返回条款编号", [source["article_id"] for source in sources], [article_id])
    expect_equal("答复引用", sorted(set(re.findall(r"\[([^\[\]\n]+)\]", result["answer"]))), [article_id])
    if not sources[0]["source"] or evidence_phrase not in sources[0]["text"]:
        raise EvaluationFailure(f"{article_id} 的原文或手册出处不完整。")
    return f"{order_id} 引用 {article_id}，已返回匹配原文和出处。"


def check_policy(client: WorkbenchClient, order_id: str, article_id: str, evidence_phrase: str) -> str:
    """从买家问答入口运行 DeepSeek、MCP 和 Milvus 查询。"""
    result = client.request(f"/api/orders/{order_id}/policy-assistance", {"question": POLICY_QUESTION})
    return check_policy_result(result, order_id, article_id, evidence_phrase)


def check_no_policy(client: WorkbenchClient) -> str:
    """覆盖查无候选和候选只匹配问题背景两种情况。"""
    for question in ("火星探测器", "包装破损后还能申请更换电子发票抬头吗？"):
        result = client.request("/api/orders/A102/policy-assistance", {"question": question})
        expect_equal("答复状态", result["status"], "no_matches")
        expect_equal("出处", result["sources"], [])
        if "人工核对" not in result["answer"]:
            raise EvaluationFailure("无适用条款时缺少人工核对提示。")
    return "无候选或只有背景相关的候选时，均提示查无适用条款。"


def check_policy_aspects(client: WorkbenchClient) -> str:
    """核对同一买家入口的双引用，以及单项需要人工核对的结果。"""
    for question, expected in (
        (MULTI_POLICY_QUESTION, [("answered", ["QH-02"]), ("answered", ["QH-03"])]),
        (PARTIAL_POLICY_QUESTION, [("answered", ["QH-03"]), ("needs_review", [])]),
    ):
        result = client.request("/api/orders/A102/policy-assistance", {"question": question})
        expect_equal("整体答复", result["status"], "answered")
        expect_equal("逐项答复与引用", [(item["status"], item["citations"]) for item in result.get("items", [])],
                     expected)
        visible = sorted(source["article_id"] for source in result["sources"])
        expect_equal("可核对出处", visible, sorted(ref for _, references in expected for ref in references))
    return "两项依据均可追溯，部分缺依据时标记对应事项。"


def check_shop_conversation(client: WorkbenchClient) -> str:
    """复现补号、多问、刷新、换单和越权后同一会话继续。"""
    path = "/api/conversation"
    pending = client.request(path, {"question": MULTI_POLICY_QUESTION})
    expect_equal("首轮追问", pending["awaiting_order_id"], True)
    cid = pending["conversation_id"]
    bound = client.request(path, {"question": "A102", "conversation_id": cid})
    expect_equal("补号后继续", bound["can_continue"], True)
    detail = bound["result"]["policy_assistance"]
    expect_equal("补号答原问题", [item["citations"] for item in detail["items"]], [["QH-02"], ["QH-03"]])
    expect_equal("刷新恢复记录", client.request(f"{path}/{cid}")["turns"], bound["turns"])

    followup = client.request(path, {"question": "包装破损要保留快递面单吗？", "conversation_id": cid})
    expect_equal("继续沿用订单", followup["result"]["order_id"], "A102")
    expect_equal("继续问不追号", followup["awaiting_order_id"], False)
    expect_equal("追问条款出处", [source["article_id"] for source in followup["result"]["policy_assistance"]["sources"]],
                 ["QH-03"])
    changed = client.request(path, {"question": "换到 A101", "conversation_id": cid})
    expect_equal("显式切换订单", changed["result"]["order_id"], "A101")
    expect_equal("切换保留会话", changed["turns"][:-1], followup["turns"])
    foreign = client.request(path, {"question": "A104 退货运费怎么算？", "conversation_id": cid})
    expect_equal("拒绝他人订单", foreign["result"]["status"], "order_not_found")
    expect_equal("他人订单没有条款", foreign["result"].get("policy_assistance"), None)
    expect_equal("缺少当前订单时重新确认", client.request(
        path, {"question": "运费怎么算？", "conversation_id": cid},
    )["awaiting_order_id"], True)
    return "补号回答完整原问题，刷新后保留引用，继续提问沿用订单，换单与账号隔离生效。"


def demo_ticket_confirmation(ticket_no: int) -> tuple[str, str]:
    """只读获取已提交演示工单的确认 ID，用来重放确认。"""
    url = local_setting("ORDER_DATABASE_URL")
    if not url:
        raise EvaluationFailure("缺少 ORDER_DATABASE_URL，无法核对演示工单。")
    try:
        with psycopg.connect(
            url, connect_timeout=3, options="-c statement_timeout=4000 -c default_transaction_read_only=on",
        ) as connection:
            row = connection.execute(
                "SELECT confirmation_id, COALESCE(olist_order_id, order_id) "
                "FROM public.support_tickets WHERE ticket_no = %s", (ticket_no,),
            ).fetchone()
    except psycopg.Error:
        raise EvaluationFailure("无法从 PostgreSQL 读取演示工单。") from None
    if row is None:
        raise EvaluationFailure(f"找不到演示工单 #{ticket_no}，可用 --demo-ticket 指定已提交的演示工单。")
    return str(row[0]), row[1]


def check_ticket_replay(buyer_a: WorkbenchClient, buyer_b: WorkbenchClient, ticket_no: int) -> str:
    """重复确认同一张已提交的工单，并比较前后状态。"""
    confirmation_id, order_id = demo_ticket_confirmation(ticket_no)
    client = buyer_b if order_owner(order_id) == "buyer-b" else buyer_a
    url = f"/api/tickets/{ticket_no}/progress"
    before = client.request(url)
    payload = {"confirmation_id": confirmation_id}
    first = client.request("/api/tickets/confirm", payload)
    second = client.request("/api/tickets/confirm", payload)
    expect_equal("工单号", first["ticket_no"], ticket_no)
    expect_equal("重复确认响应", second, first)
    expect_equal("确认前后进度", client.request(url), before)
    return f"重复确认均返回原工单 #{ticket_no}，处理状态和结果一致。"


def workbench_cases(client: WorkbenchClient, buyer_b: WorkbenchClient, ticket_no: int) -> list[EvaluationCase]:
    """定义一组可重跑的真实业务输入和预期。"""
    return [
        EvaluationCase("ORDER-01", "青禾 7 天期限", lambda: check_order(client, "A102", 7, "outside_window")),
        EvaluationCase("ORDER-02", "栖木 15 天期限", lambda: check_order(buyer_b, "A104", 15, "eligible")),
        EvaluationCase("ORDER-03", "未知订单", lambda: check_unknown_order(client)),
        EvaluationCase("DIALOGUE-01", "追问后恢复会话", lambda: check_resumed_order(buyer_b)),
        EvaluationCase("POLICY-01", "青禾包装破损", lambda: check_policy(client, "A102", "QH-03", "快递面单")),
        EvaluationCase("POLICY-02", "栖木包装破损", lambda: check_policy(buyer_b, "A104", "QM-03", "上门取件")),
        EvaluationCase("POLICY-03", "无匹配条款", lambda: check_no_policy(client)),
        EvaluationCase("POLICY-04", "多诉求逐项答复", lambda: check_policy_aspects(client)),
        EvaluationCase("DIALOGUE-02", "店铺订单同会话续聊", lambda: check_shop_conversation(client)),
        EvaluationCase("TICKET-01", "重复确认已有工单", lambda: check_ticket_replay(client, buyer_b, ticket_no)),
    ]


def check_olist(client: WorkbenchClient, order_id: str, status: str, question: str, followup: bool = False) -> str:
    """用真实样本核对结论和日期，可经过一轮缺单号追问。"""
    if followup:
        pending = client.request("/api/conversation", {"question": question})
        expect_equal("追问订单号", pending["awaiting_order_id"], True)
        payload = {"question": order_id, "conversation_id": pending["conversation_id"]}
    else:
        payload = {"question": f"{order_id} {question}"}
    reply = client.request("/api/conversation", payload)
    expect_equal("已完成查询", reply["awaiting_order_id"], False)
    result = reply["result"]
    expect_equal("物流状态", result["status"], status)
    expect_equal("样本编号", result["order_id"], order_id)
    dates = result["logistics"]
    expect_equal("数据来源", dates["source"], "Olist")
    expect_equal("历史查询日", dates["snapshot_on"], "2018-10-17")
    if status == "logistics_delivered" and not dates["delivered_on"] <= dates["estimated_on"]:
        raise EvaluationFailure("按期样本的签收时间晚于预计送达日。")
    if status == "logistics_delivered_late" and not dates["delivered_on"] > dates["estimated_on"]:
        raise EvaluationFailure("延迟样本的签收时间应晚于预计送达日。")
    if status == "logistics_overdue" and not (dates["delivered_on"] is None and dates["estimated_on"] < dates["snapshot_on"]):
        raise EvaluationFailure("超期未签收样本的日期依据与状态不符。")
    return f"{order_id} 的状态和历史日期依据正确。"


def olist_cases(client: WorkbenchClient, buyer_b: WorkbenchClient) -> list[EvaluationCase]:
    """定义默认 Olist 订单 CSV 导入后可重跑的历史物流评测。"""
    return [
        EvaluationCase("OLIST-01", "按期签收", lambda: check_olist(
            client, "OL-00010242FE", "logistics_delivered", "查一下签收日期")),
        EvaluationCase("OLIST-02", "延迟签收", lambda: check_olist(
            client, "OL-000E906B78", "logistics_delivered_late", "这单送晚了吗？")),
        EvaluationCase("OLIST-03", "追问后确认超期未签收", lambda: check_olist(
            client, "OL-002F19A65A", "logistics_overdue", "这单还没收到，超期了吗？", followup=True)),
        EvaluationCase("OLIST-04", "订单取消", lambda: check_olist(
            buyer_b, "OL-00310B0C75", "logistics_cancelled", "这笔订单取消了吗？")),
    ]


def check_olist_items(client: WorkbenchClient) -> str:
    """验证同一商品的两件记录与商品、运费合计。"""
    order_id = "OL-0008288AA4"
    result = client.request(f"/api/olist/orders/{order_id}/items", {"question": "买了几件？每件多少钱？运费多少？"})
    expect_equal("明细查询状态", result["status"], "found")
    expect_equal("订单号", result["order_id"], order_id)
    expect_equal("币种", result["currency"], "BRL")
    expect_equal("商品件数", result["quantity"], 2)
    expect_equal("明细行数", len(result["items"]), 2)
    expect_equal("商品编号", len({item["product_id"] for item in result["items"]}), 1)
    expect_equal("商品金额", result["items_total"], "99.80")
    expect_equal("运费", result["freight_total"], "26.74")
    expect_equal("记录合计", result["recorded_total"], "126.54")
    return f"{order_id} 保留同一商品的两条明细，金额和运费准确。"


def check_olist_without_items(client: WorkbenchClient) -> str:
    """确认缺少源明细的真实订单不显示虚假件数或金额。"""
    result = client.request("/api/olist/orders/OL-00B1CB0320/items", {"question": "买了几件？"})
    expect_equal("缺少明细状态", result["status"], "no_items")
    expect_equal("返回空明细", result["items"], [])
    expect_equal("无法确认件数", result["quantity"], None)
    expect_equal("无法确认合计", result["recorded_total"], None)
    return "取消订单缺少源明细，页面可显示明确提示。"


def check_order_catalog(buyer: WorkbenchClient, staff: WorkbenchClient) -> str:
    """确认买家仅查本人，客服可按账号和单号独立核对。"""
    mine = buyer.request("/api/buyer/orders?kind=shop")
    expect_equal("买家订单", [row["order_id"] for row in mine["orders"]], ["A101", "A102"])
    expect_rejected(buyer, "/api/buyer/orders/A104", 404)
    expect_rejected(buyer, "/api/staff/orders", 403)
    result = staff.request("/api/staff/orders?buyer_id=buyer-b&order_id=A104")
    expect_equal("客服按账号和单号筛选", [row["order_id"] for row in result["orders"]], ["A104"])
    detail = staff.request("/api/staff/orders/A104")
    expect_equal("客服详情的买家", detail["buyer_id"], "buyer-b")
    expect_equal("客服详情的期限", detail["policy"]["return_window_days"], 15)
    return "买家和客服通过各自入口读取其可访问的订单。"


def check_catalog_items(buyer: WorkbenchClient) -> str:
    """使用真实 Olist 商品行校验目录显示的金额和缺失提示。"""
    found = buyer.request("/api/buyer/orders/OL-0008288AA4")["items"]
    expect_equal("目录的订单件数", found["quantity"], 2)
    expect_equal("目录的订单含运费合计", found["recorded_total"], "126.54")
    missing = buyer.request("/api/buyer/orders/OL-00B1CB0320")["items"]
    expect_equal("目录的缺失商品状态", missing["status"], "no_items")
    expect_equal("目录无法确认的金额", missing["recorded_total"], None)
    return "订单详情显示现有真实金额，未提供商品行的订单可提示缺失。"


def expect_rejected(client: WorkbenchClient, path: str, status: int, payload: dict | None = None) -> None:
    """验证越权或重复处理请求返回预期状态。"""
    try:
        client.request(path, payload)
    except EvaluationFailure as error:
        expect_equal("拒绝状态", error.status_code, status)
        return
    raise EvaluationFailure(f"{path} 应返回 HTTP {status}。")


def check_buyer_dialogue(buyer: WorkbenchClient, other: WorkbenchClient) -> str:
    """在同一会话验证真实多意图路由、运费追问和已有工单。"""
    path = "/api/conversation"
    order_id = "OL-002F19A65A"
    initial = buyer.request(path, {"question": "这单怎么还没收到？我买了几件？"})
    expect_equal("追问订单号", initial["awaiting_order_id"], True)
    cid = initial["conversation_id"]
    context = {"conversation_id": cid}
    first = buyer.request(path, {**context, "question": order_id})
    expect_equal("同一会话", first["conversation_id"], cid)
    expect_equal("能继续询问", first["can_continue"], True)
    expect_equal("物流状态", first["result"]["status"], "logistics_overdue")
    plan = first["result"]["tool_plan"]
    expect_equal("使用真实函数调用", plan["source"], "function_call")
    expect_equal("物流与商品同时查询", set(plan["operations"]), {"logistics", "items"})
    baseline = buyer.request(f"/api/olist/orders/{order_id}/items", {"question": "买了几件？运费多少？"})
    expect_equal("商品件数", first["result"]["olist_items"]["quantity"], baseline["quantity"])
    followup = buyer.request(path, {**context, "question": "运费多少？"})
    expect_equal("追问沿用订单", followup["result"]["order_id"], order_id)
    expect_equal("追问运费", followup["result"]["olist_items"]["freight_total"], baseline["freight_total"])
    restored = buyer.request(path + "/" + cid)
    expect_equal("恢复同一运费结果", restored["result"], followup["result"])
    expect_equal("恢复已完成的全部对话", restored["turns"], followup["turns"])
    expect_rejected(other, path + "/" + cid, 404)
    expect_rejected(other, path, 409, {**context, "question": "运费多少？"})
    handoff = buyer.request(path, {**context, "question": "帮我转人工"})
    review = handoff["result"]["human_review"]
    turns = handoff["turns"]
    expect_equal("完整对话顺序", [turn["question"] for turn in turns], [
        "这单怎么还没收到？我买了几件？", order_id, "运费多少？", "帮我转人工",
    ])
    expect_equal("上一轮保留商品与物流答复", turns[1]["result"], first["result"])
    expect_equal("最后一轮关联工单入口", turns[-1]["result"]["human_review"], review)
    expect_equal("刷新保留全部四轮", buyer.request(path + "/" + cid)["turns"], turns)
    expect_equal("显示已有复核工单", review["status"], "existing")
    progress = buyer.request(f"/api/tickets/{review['ticket_no']}/progress")
    expect_equal("已有工单状态", progress["status"], review["ticket_status"])
    return f"沿用 {order_id}，买了 {baseline['quantity']} 件，运费 R$ {baseline['freight_total']}，已有工单 #{review['ticket_no']}。"


def check_staff_ticket_history(staff: WorkbenchClient, buyer: WorkbenchClient, ticket_no: int) -> str:
    """从真实订单找回已处理工单，并验证权限和相邻两页。"""
    search_path = "/api/staff/tickets/search"
    expect_rejected(buyer, search_path, 403)
    ticket = staff.request(f"/api/staff/tickets/{ticket_no}")
    order_id = ticket["order_id"]
    expect_equal("验收工单状态", ticket["status"], "resolved")
    if not all(ticket.get(key) for key in ("resolution", "resolved_by", "resolved_at")):
        raise EvaluationFailure("历史工单应有处理说明、处理人和处理时间。")
    query = {"order_id": order_id, "status": "resolved", "limit": 100}
    visited = set()
    while True:
        history_page = staff.request(search_path + "?" + urlencode(query))
        if any(row["ticket_no"] == str(ticket_no) and row["order_id"] == order_id
               for row in history_page["tickets"]):
            break
        cursor = history_page["next_before"]
        if cursor is None:
            raise EvaluationFailure("按订单筛选已处理记录时找不到验收工单。")
        if cursor in visited:
            raise EvaluationFailure("工单历史查询重复返回同一个分页位置。")
        visited.add(cursor)
        query["before"] = cursor
    first = staff.request(search_path + "?status=all&limit=1")
    if first["next_before"] is None:
        raise EvaluationFailure("至少需要两张工单验证分页。")
    second = staff.request(search_path + "?" + urlencode({"limit": 1, "before": first["next_before"]}))
    expect_equal("下一页记录数", len(second["tickets"]), 1)
    newest, previous = first["tickets"][0], second["tickets"][0]
    if ((datetime.fromisoformat(newest["created_at"]), int(newest["ticket_no"]))
            <= (datetime.fromisoformat(previous["created_at"]), int(previous["ticket_no"]))):
        raise EvaluationFailure("相邻两页工单应按提交时间和编号倒序返回。")
    return f"按 {order_id} 找到已处理工单 #{ticket_no}，两页无重复，买家被拒绝。"


def check_dialogue_handoff(order_id: str, buyer: WorkbenchClient, other: WorkbenchClient, staff: WorkbenchClient) -> str:
    """用新样本验证确认版本保真、客服接手和买家进度。"""
    path = "/api/conversation"
    question = "这单怎么还没收到？我买了几件？"
    cid = buyer.request(path, {"question": question})["conversation_id"]
    context = {"conversation_id": cid}
    first = buyer.request(path, {**context, "question": order_id})["result"]
    expect_equal("超期未签收", first["status"], "logistics_overdue")
    expect_equal("查到商品事实", first["olist_items"]["status"], "found")
    request = {**context, "order_id": order_id, "reason": "交接流程验收，请核对这一笔订单的实际签收情况。"}
    foreign_id = other.request(path, {"question": "帮我查询物流"})["conversation_id"]
    expect_rejected(buyer, "/api/tickets/preview", 409, {**request, "conversation_id": foreign_id})
    expect_rejected(other, "/api/tickets/preview", 404, request)
    preview = buyer.request("/api/tickets/preview", request)
    if "confirmation_id" not in preview:
        raise EvaluationFailure("请选择一笔当前尚无工单的超期样本进行交接验收。")
    snapshot = preview["handoff"]
    expect_equal("预览诉求", snapshot["buyer_request"], request["reason"])
    expect_equal("预览保留原始问题", snapshot["turns"][0]["question"], question)
    if not any(str(first["logistics"]["estimated_on"]) in fact for fact in snapshot["verified_facts"]):
        raise EvaluationFailure("交接缺少核对过的预计送达日。")
    if not any(f"{first['olist_items']['quantity']} 件" in fact for fact in snapshot["verified_facts"]):
        raise EvaluationFailure("交接缺少核对过的商品件数。")
    followup = buyer.request(path, {**context, "question": "运费多少？"})
    expect_equal("已产生预览之后的新对话", followup["turns"][-1]["question"], "运费多少？")
    confirmation = {"confirmation_id": preview["confirmation_id"]}
    ticket = buyer.request("/api/tickets/confirm", confirmation)
    expect_equal("重复确认的工单", buyer.request("/api/tickets/confirm", confirmation), ticket)
    number = ticket["ticket_no"]
    staff_path = f"/api/staff/tickets/{number}"
    progress_path = f"/api/tickets/{number}/progress"
    detail = staff.request(staff_path)
    expect_equal("客服读取原交接版本", detail["handoff"], snapshot)
    resolution = "已查看订单物流和随附对话，本次人工复核验收完成。"
    staff.request(staff_path + "/resolve", {"resolution": resolution})
    progress = buyer.request(progress_path)
    expect_equal("买家查到已处理", progress["status"], "resolved")
    expect_equal("买家看到处理结果", progress["resolution"], resolution)
    if "handoff" in progress:
        raise EvaluationFailure("买家进度返回了客服交接内容。")
    expect_rejected(other, progress_path, 404)
    with psycopg.connect(local_setting("ORDER_DATABASE_URL"), connect_timeout=3,
                         options="-c statement_timeout=4000 -c default_transaction_read_only=on") as connection:
        records = connection.execute(
            "SELECT t.ticket_no, t.handoff, a.actor_id FROM public.support_tickets t "
            "LEFT JOIN public.support_ticket_audit a USING (ticket_no) WHERE t.olist_order_id = %s", (order_id,),
        ).fetchall()
    expect_equal("保存一张工单及其交接", records, [(number, snapshot, "staff")])
    return f"工单 #{number} 交接版本与预览一致，客服已处理，原买家可查进度。"


def check_olist_ticket(buyer: WorkbenchClient, other: WorkbenchClient, staff: WorkbenchClient) -> str:
    """提交一张真实超期样本的验收工单，并核对处理与审计。"""
    order_id = "OL-002F19A65A"
    preview_path = "/api/tickets/preview"
    confirm_path = "/api/tickets/confirm"
    request = {"order_id": order_id, "reason": "物流工单流程验收，请复核历史样本的预计送达日。"}
    expect_rejected(other, preview_path, 404, request)
    preview = buyer.request(preview_path, request)
    expect_equal("预览订单号", preview["order_id"], order_id)
    logistics = preview["logistics"]
    expect_equal("历史查询日", logistics["snapshot_on"], "2018-10-17")
    expect_equal("实际签收", logistics["delivered_on"], None)
    if not logistics["estimated_on"] < logistics["snapshot_on"]:
        raise EvaluationFailure("预览中的预计送达日应早于历史查询日。")
    existing_id = preview.get("existing_ticket_no")
    if existing_id is not None:
        if "confirmation_id" in preview:
            raise EvaluationFailure("已有工单的物流预览不应再生成确认 ID。")
        repeat = buyer.request(preview_path, {**request, "reason": "再次申请核对同一笔历史物流"})
        expect_equal("重复申请复用的工单", repeat.get("existing_ticket_no"), existing_id)
        progress_path = f"/api/tickets/{existing_id}/progress"
        expect_rejected(other, progress_path, 404)
        progress = buyer.request(progress_path)
        expect_equal("已有工单状态", progress["status"], preview["existing_ticket_status"])
        detail = staff.request(f"/api/staff/tickets/{existing_id}")
        expect_equal("已有工单物流依据", detail["evidence"]["logistics"], logistics)
        return f"两次申请复用工单 #{existing_id}，买家可查询进度。"
    payload = {"confirmation_id": preview["confirmation_id"]}
    expect_rejected(other, confirm_path, 409, payload)
    ticket = buyer.request(confirm_path, payload)
    ticket_no = ticket["ticket_no"]
    expect_equal("重复确认同一张工单", buyer.request(confirm_path, payload), ticket)
    expect_rejected(other, confirm_path, 409, payload)

    staff_path = f"/api/staff/tickets/{ticket_no}"
    progress_path = f"/api/tickets/{ticket_no}/progress"
    expect_rejected(other, progress_path, 404)
    expect_rejected(buyer, staff_path, 403)
    queue = staff.request("/api/staff/tickets")
    expect_equal("客服队列包含工单", any(row["ticket_no"] == ticket_no for row in queue), True)
    detail = staff.request(staff_path)
    expect_equal("客服物流依据", detail["evidence"]["logistics"], logistics)
    expect_equal("客服物流状态", detail["evidence"]["status"], "logistics_overdue")
    expect_equal("客服商品明细状态", detail["olist_items"]["status"], "found")
    if not detail["olist_items"]["items"]:
        raise EvaluationFailure("真实超期样本缺少可显示的商品行。")
    resolution = "已核对该 Olist 历史样本的预计送达日和缺失签收记录。"
    staff.request(staff_path + "/resolve", {"resolution": resolution})
    expect_rejected(staff, staff_path + "/resolve", 409, {"resolution": "不应覆盖原处理"})
    progress = buyer.request(progress_path)
    expect_equal("工单已处理", progress["status"], "resolved")
    expect_equal("买家读到处理说明", progress["resolution"], resolution)
    expect_equal("客服身份", staff.request(staff_path)["resolved_by"], "staff")
    with psycopg.connect(local_setting("ORDER_DATABASE_URL"), connect_timeout=3,
                         options="-c statement_timeout=4000 -c default_transaction_read_only=on") as connection:
        rows = connection.execute(
            "SELECT t.order_id, t.olist_order_id, a.actor_id, a.action, a.occurred_at IS NOT NULL "
            "FROM public.support_tickets t JOIN public.support_ticket_audit a USING (ticket_no) WHERE t.ticket_no = %s",
            (ticket_no,),
        ).fetchall()
    expect_equal("只记录一次且关联 Olist 订单的审计", rows, [(None, order_id, "staff", "resolve", True)])
    return f"验收工单 #{ticket_no} 已处理，买家可查结果，数据库记录一次客服操作。"


def run_cases(cases: Iterable[EvaluationCase]) -> list[EvaluationOutcome]:
    """逐项运行并计时，某项失败后继续跑剩余用例。"""
    results = []
    for case in cases:
        started = perf_counter()
        try:
            detail = case.check()
            passed = True
        except EvaluationFailure as error:
            detail = str(error)
            passed = False
        except Exception as error:
            detail = f"评测发生意外错误（{type(error).__name__}）。"
            passed = False
        elapsed = round((perf_counter() - started) * 1000)
        results.append(EvaluationOutcome(case.case_id, case.name, passed, detail, elapsed))
    return results


def write_report(results: list[EvaluationOutcome], ticket_no: int) -> Path:
    """写入新的结果文件，便于和后续评测对照。"""
    now = datetime.now(timezone.utc)
    folder = ROOT / ".local" / "evaluations"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"after-sales-{now.strftime('%Y%m%dT%H%M%S%fZ')}.json"
    with path.open("x", encoding="utf-8") as file:
        json.dump({
            "recorded_at": now.isoformat(),
            "endpoint": BASE_URL,
            "demo_ticket": ticket_no,
            "total": len(results),
            "passed": sum(item.passed for item in results),
            "cases": [asdict(item) for item in results],
        }, file, ensure_ascii=False, indent=2)
        file.write("\n")
    return path


def main() -> int:
    """读取演示工单参数、执行评测并返回适合终端检查的退出码。"""
    parser = argparse.ArgumentParser(description="评测本机售后工作台的固定业务用例。")
    parser.add_argument("--demo-ticket", type=int, default=1, help="已提交的演示工单号，默认 1。")
    parser.add_argument("--with-olist", action="store_true", help="同时检查默认导入的 Olist 历史物流样本。")
    parser.add_argument("--with-olist-items", action="store_true", help="同时检查 Olist 商品明细和缺失样本。")
    parser.add_argument("--with-olist-ticket", action="store_true", help="验收已有 Olist 工单的复用，或首次创建验收工单。")
    parser.add_argument("--history-ticket", type=int, help="对指定的已处理工单验收历史查询与分页。")
    parser.add_argument("--with-buyer-dialogue", action="store_true", help="验收 Olist 多轮对话、真实工具选择和已有工单。")
    parser.add_argument("--handoff-order", type=str.upper, help="对指定的未提交超期样本创建并处理一张交接验收单。")
    args = parser.parse_args()
    if not 1 <= args.demo_ticket <= MAX_TICKET_NUMBER:
        parser.error("请输入有效的正整数工单号。")
    if args.history_ticket is not None and not 1 <= args.history_ticket <= MAX_TICKET_NUMBER:
        parser.error("历史工单号必须为有效的正整数。")
    if args.handoff_order is not None and not re.fullmatch(r"OL-[0-9A-F]{10}", args.handoff_order):
        parser.error("请输入有效的 Olist 样本编号。")
    client = WorkbenchClient()
    buyer_b = client
    staff = None
    try:
        access_enabled = verify_runtime(client)
        if access_enabled:
            client.login("buyer-a")
            buyer_b = WorkbenchClient()
            buyer_b.login("buyer-b")
            staff = WorkbenchClient()
            staff.login("staff")
        if args.with_buyer_dialogue and not access_enabled:
            raise EvaluationFailure("买家对话评测需要启用演示账号权限。")
        if (args.with_olist_ticket or args.history_ticket is not None or args.handoff_order is not None) and not access_enabled:
            raise EvaluationFailure("物流工单评测需要启用演示账号权限。")
    except EvaluationFailure as error:
        print(f"启动检查失败。{error}")
        return 1

    cases = workbench_cases(client, buyer_b, args.demo_ticket)
    if staff is not None:
        cases.append(EvaluationCase("CATALOG-01", "买家与客服订单权限", lambda: check_order_catalog(client, staff)))
    if args.with_olist:
        cases.extend(olist_cases(client, buyer_b))
    if args.with_olist_items:
        cases.extend([
            EvaluationCase("ITEM-01", "同商品多件与金额核对", lambda: check_olist_items(client)),
            EvaluationCase("ITEM-02", "无商品明细的取消订单", lambda: check_olist_without_items(client)),
        ])
        if access_enabled:
            cases.append(EvaluationCase("CATALOG-02", "Olist 订单详情", lambda: check_catalog_items(client)))
    if staff is not None and args.with_olist_ticket:
        cases.append(EvaluationCase("TICKET-OLIST-01", "超期未签收转人工", lambda: check_olist_ticket(client, buyer_b, staff)))
    if staff is not None and args.history_ticket is not None:
        cases.append(EvaluationCase("TICKET-HISTORY-01", "找回已处理工单与分页", lambda: check_staff_ticket_history(
            staff, client, args.history_ticket,
        )))
    if args.with_buyer_dialogue:
        cases.append(EvaluationCase("DIALOGUE-OLIST-01", "多意图、运费追问和转人工", lambda: check_buyer_dialogue(client, buyer_b)))
    if staff is not None and args.handoff_order is not None:
        owner, other = (client, buyer_b) if order_owner(args.handoff_order) == "buyer-a" else (buyer_b, client)
        cases.append(EvaluationCase("TICKET-HANDOFF-01", "会话交接与人工接手", lambda: check_dialogue_handoff(
            args.handoff_order, owner, other, staff,
        )))
    outcomes = run_cases(cases)
    for item in outcomes:
        label = "通过" if item.passed else "失败"
        print(f"{label} {item.case_id} {item.name}（{item.duration_ms} ms） {item.detail}")
    print(f"通过 {sum(item.passed for item in outcomes)}/{len(outcomes)}。")
    print(f"报告 {write_report(outcomes, args.demo_ticket)}")
    return 0 if all(item.passed for item in outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
