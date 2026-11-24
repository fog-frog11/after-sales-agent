"""使用同批京东订单测量首条结果、完整答复和已记录的各阶段耗时。"""

import argparse
import json
import socket
from datetime import UTC, datetime
from http.cookiejar import CookieJar
from math import ceil
from statistics import median
from threading import Thread
from time import perf_counter, sleep
from types import SimpleNamespace
from urllib.request import HTTPCookieProcessor, Request, build_opener
from unittest.mock import patch

import psycopg
import uvicorn
from langgraph.checkpoint.memory import InMemorySaver

from app.access import load_credentials
from app.api import configured_app
from app.settings import ROOT, local_setting
from app.storage.postgres_jd import PostgresJDOrders


FACT_QUESTION = "这单买了几件？"
RULE_QUESTIONS = {
    "self": ("网购七天无理由退货，拆开包装只为检查商品，算影响完好吗？", "SAMR-", "JDM-"),
    "market": ("第三方店铺的包裹由我在订单中指定的室友代签，售后条款怎么认定签收？", "JDM-2.2A", "JDS-"),
}


def choose_orders(url: str) -> list[tuple[str, str]]:
    """按渠道稳定选择两个已签收且归当前测试账号的订单。"""
    with psycopg.connect(url, options="-c default_transaction_read_only=on") as connection:
        return connection.execute("""
            SELECT DISTINCT ON (channel) channel, order_id FROM public.jd_research_orders
            WHERE delivered_on IS NOT NULL AND channel IN ('self', 'market')
              AND right(order_id, 1) IN ('0','2','4','6','8','A','C','E')
            ORDER BY channel, order_id
        """).fetchall()


def elapsed(start: float) -> float:
    """以毫秒返回时间测量结果。"""
    return round((perf_counter() - start) * 1000, 2)


def run_cases(address: str, orders: list[tuple[str, str]], repeat: int, progressive: bool) -> list[dict]:
    """请求真正的键盘发送入口并测量客户端首次拿到业务结果的时间。"""
    opener = build_opener(HTTPCookieProcessor(CookieJar()))

    def post(path: str, payload: dict):
        """在独立的本地买家登录会话发送 JSON。"""
        return opener.open(Request(address + path, data=json.dumps(payload).encode(),
                                   headers={"Content-Type": "application/json"}), timeout=70)

    with post("/api/session/login", {"account_id": "buyer-a", "secret": load_credentials()["buyer-a"]}):
        pass
    rows = []
    for channel, order_id in orders:
        rule, expected, forbidden = RULE_QUESTIONS[channel]
        with post("/api/conversation/share-order", {"order_id": order_id}) as response:
            thread = json.load(response)["conversation_id"]
        for _ in range(repeat):
            for kind, text in (("facts", FACT_QUESTION), ("policy", rule), ("mixed", FACT_QUESTION + "另外" + rule)):
                start = perf_counter()
                first = None
                path = "/api/conversation/stream" if progressive else "/api/conversation"
                with post(path, {"question": text, "conversation_id": thread, "active_order_id": order_id}) as response:
                    if progressive:
                        for line in response:
                            event = json.loads(line)
                            if event["type"] == "partial" and first is None:
                                first = elapsed(start)
                            elif event["type"] == "final":
                                final = event["data"]
                                break
                            elif event["type"] == "error":
                                raise RuntimeError(event["message"])
                        else:
                            raise RuntimeError("响应缺少最终结果。")
                    else:
                        final = json.load(response)
                complete = elapsed(start)
                result = final["result"]
                policy = result.get("policy_assistance", {})
                citations = [source["article_id"] for source in policy.get("sources", [])]
                facts_ok = kind == "policy" or result.get("olist_items", {}).get("quantity", 0) > 0
                policy_ok = kind == "facts" or (policy.get("status") == "answered" and
                    any(ref.startswith(expected) for ref in citations) and not any(ref.startswith(forbidden) for ref in citations))
                row = {"channel": channel, "order_id": order_id, "kind": kind,
                       "first_ms": complete if first is None else first, "complete_ms": complete, "partial": first is not None,
                       "facts_ok": facts_ok, "policy_ok": policy_ok, "tool_plan": result.get("tool_plan"),
                       "citations": citations, "policy_trace": policy.get("trace", {})}
                rows.append(row)
                print(channel, kind, f"{row['first_ms']:.0f}/{complete:.0f} ms", facts_ok and policy_ok, flush=True)
    return rows


def main() -> None:
    """用临时内存会话启动测速服务，写出逐条测量与中位数。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--progressive", action="store_true")
    parser.add_argument("--repeat", type=int, choices=range(1, 6), default=2)
    args = parser.parse_args()
    orders = choose_orders(local_setting("ORDER_DATABASE_URL"))
    if len(orders) != 2:
        raise SystemExit("需要各一笔可查的自营和第三方京东订单。")
    temporary = SimpleNamespace(saver=InMemorySaver(), lifespan=None, list_threads=None)
    durations = []
    connection = socket.socket()
    connection.bind(("127.0.0.1", 0))
    port = connection.getsockname()[1]
    original_order, original_items = PostgresJDOrders.get_order, PostgresJDOrders.get_items

    def measure(method, name):
        """在测量进程内给数据库调用计时，不改生产服务。"""
        def timed(repository, order_id):
            """记录本次数据库请求完成耗时。"""
            start = perf_counter()
            try:
                return method(repository, order_id)
            finally:
                durations.append((name, elapsed(start)))
        return timed

    with (patch("app.api.DialogueCheckpoints", return_value=temporary), patch("app.api.write_policy_trace"),
          patch.object(PostgresJDOrders, "get_order", new=measure(original_order, "get_order")),
          patch.object(PostgresJDOrders, "get_items", new=measure(original_items, "get_items"))):
        service = uvicorn.Server(uvicorn.Config(configured_app(), log_level="error", timeout_graceful_shutdown=5))
        thread = Thread(target=service.run, kwargs={"sockets": [connection]}, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if service.started or not thread.is_alive():
                    break
                sleep(0.05)
            if not service.started:
                raise RuntimeError("测速服务未启动。")
            rows = run_cases(f"http://127.0.0.1:{port}", orders, args.repeat, args.progressive)
        finally:
            service.should_exit = True
            thread.join(timeout=7)
            connection.close()
    summary = {}
    for kind in ("facts", "policy", "mixed"):
        group = [row for row in rows if row["kind"] == kind]
        summary[kind] = {"count": len(group), "checks_ok": sum(row["facts_ok"] and row["policy_ok"] for row in group),
                         "partial_count": sum(row["partial"] for row in group),
                         **{key: round(median(row[key] for row in group), 2) for key in ("first_ms", "complete_ms")}}
        for key in ("first_ms", "complete_ms"):
            summary[kind][f"p95_{key}"] = sorted(row[key] for row in group)[ceil(0.95 * len(group)) - 1]
    output = ROOT / ".local/evaluations" / f"jd-chat-latency-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump({"progressive": args.progressive, "summary": summary, "database_ms": durations, "rows": rows},
                  stream, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    print(output)


if __name__ == "__main__":
    main()
