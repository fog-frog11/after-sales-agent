"""为买家政策问答计时，并在本机保存可按请求 ID 查询的日志。"""

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import Literal
from uuid import uuid4

from typing_extensions import NotRequired, TypedDict

from app.settings import ROOT


StageStatus = Literal["ok", "failed", "skipped", "fallback"]
TRACE_LOG = ROOT / ".local" / "logs" / "policy-requests.jsonl"
_log_lock = Lock()


class PolicyTraceStage(TypedDict):
    """描述一个已执行或明确跳过的阶段。"""

    name: str
    status: StageStatus
    duration_ms: int


class PolicyTraceSnapshot(TypedDict):
    """让页面和日志使用同一份请求耗时记录。"""

    request_id: str
    duration_ms: int
    stages: list[PolicyTraceStage]
    details_ms: NotRequired[dict[str, int]]


class PolicyTraceRecord(PolicyTraceSnapshot):
    """在请求记录中附上最终 HTTP 状态。"""

    http_status: int


class PolicyRequestTrace:
    """管理单次政策问答的请求 ID 和阶段计时。"""

    def __init__(self) -> None:
        """分配独立记录，便于同时处理不同买家的请求。"""
        self.request_id = str(uuid4())
        self._started = perf_counter()
        self._stages: list[PolicyTraceStage] = []
        self.details_ms: dict[str, int] = {}

    @contextmanager
    def stage(self, name: str) -> Iterator[PolicyTraceStage]:
        """计量阶段耗时，在异常继续传播前记录失败。"""
        started = perf_counter()
        entry: PolicyTraceStage = {"name": name, "status": "ok", "duration_ms": 0}
        try:
            yield entry
        except Exception:
            entry["status"] = "failed"
            raise
        finally:
            entry["duration_ms"] = round((perf_counter() - started) * 1000)
            self._stages.append(entry)

    def skip(self, name: str) -> None:
        """记录因当前业务结果而跳过的阶段。"""
        self._stages.append({"name": name, "status": "skipped", "duration_ms": 0})

    @contextmanager
    def measure(self, name: str) -> Iterator[None]:
        """记录当前可见阶段内部的独立耗时。"""
        started = perf_counter()
        try:
            yield
        finally:
            self.details_ms[name] = round((perf_counter() - started) * 1000)

    def snapshot(self) -> PolicyTraceSnapshot:
        """在业务结束后计算总耗时，返回可序列化的记录。"""
        result: PolicyTraceSnapshot = {
            "request_id": self.request_id,
            "duration_ms": round((perf_counter() - self._started) * 1000),
            "stages": list(self._stages),
        }
        if self.details_ms:
            result["details_ms"] = dict(self.details_ms)
        return result


def write_policy_trace(record: PolicyTraceRecord, path: Path = TRACE_LOG) -> None:
    """把阶段数据追加到本地日志，按线程顺序写入整行。"""
    line = json.dumps({"timestamp": datetime.now(UTC).isoformat(), **record}, ensure_ascii=False)
    try:
        with _log_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as output:
                output.write(line + "\n")
    except OSError:
        logging.getLogger(__name__).warning("无法写入政策问答追踪日志，请求 ID %s", record["request_id"])
