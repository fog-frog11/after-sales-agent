"""把公开零售任务转成可复跑的售后条款检索评测。"""

import csv
import random
import re
from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path


RETAIL_SOURCE = "https://github.com/sierra-research/tau2-bench/blob/main/data/tau2/domains/retail/policy.md"
SHOPTC_SOURCE = "https://huggingface.co/datasets/eltsai/ShopTC-100K"
BENCHMARK_SHOP = "tau-retail-benchmark"
ACTION_SECTIONS = {
    "cancel_pending_order": ("Cancel pending order",),
    "modify_pending_order_address": ("Modify pending order",),
    "modify_pending_order_items": ("Modify pending order", "Modify items"),
    "modify_pending_order_payment": ("Modify pending order", "Modify payment"),
    "return_delivered_order_items": ("Return delivered order",),
    "exchange_delivered_order_items": ("Exchange delivered order",),
    "modify_user_address": ("Retail agent policy",),
    "transfer_to_human_agents": ("Retail agent policy",),
}
RELATED_WORDS = re.compile(r"\b(?:returns?|refunds?|exchanges?|cancellations?|cancell?ed|replacements?|warranty)\b", re.I)


@dataclass(frozen=True)
class RetrievalCase:
    """保存公开任务编号、原始问题和由动作标注映射的相关条款。"""

    task_id: str
    question: str
    expected_ids: tuple[str, ...]


def retail_articles(markdown: str) -> list[dict]:
    """保留原文标题和正文，按 Markdown 标题拆成独立条款。"""
    matches = list(re.finditer(r"^#{1,6} ([^\n]+)\s*$", markdown, flags=re.MULTILINE))
    result = []
    for index, heading in enumerate(matches):
        stop = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        body = markdown[heading.end():stop].strip()
        if not body:
            continue
        title = heading[1].strip()
        identifier = "TAU-" + sha256(title.encode()).hexdigest()[:12]
        text = f"{title}\n{body}"
        if len(text.encode()) > 4096:
            raise ValueError(f"零售条款长度超出索引限制：{title}")
        result.append({"id": identifier, "shop_id": BENCHMARK_SHOP, "title": title,
                       "source": RETAIL_SOURCE, "text": text})
    if not result or len({row["id"] for row in result}) != len(result):
        raise ValueError("公开零售条款没有可检索内容或标题重复。")
    return result


def retail_cases(tasks: Iterable[dict], articles: list[dict]) -> list[RetrievalCase]:
    """使用任务原始来电原因和公开动作标注，不把参考动作混入检索问题。"""
    section_ids = {article["title"]: article["id"] for article in articles}
    results = []
    for task in tasks:
        instructions = (task.get("user_scenario") or {}).get("instructions") or {}
        question = instructions.get("reason_for_call", "").strip()
        actions = (task.get("evaluation_criteria") or {}).get("actions") or []
        names = {title for action in actions for title in ACTION_SECTIONS.get(action["name"], ())}
        if question and names:
            try:
                expected = tuple(sorted(section_ids[title] for title in names))
            except KeyError as error:
                raise ValueError(f"数据版本与条款映射不匹配：{error.args[0]}") from None
            results.append(RetrievalCase(str(task["id"]), question, expected))
    if not results or len({case.task_id for case in results}) != len(results):
        raise ValueError("公开任务没有可评测样本，或任务编号重复。")
    return results


def clip_utf8(value: str, maximum: int) -> str:
    """按 Milvus 字节长度限制截取文本，避免保留半个字符。"""
    return value.encode()[:maximum].decode("utf-8", errors="ignore")


def sample_shoptc(path: Path, count: int, *, seed: int = 20261116) -> list[dict]:
    """扫描公开网页条款，从售后相关段落中按固定种子抽取干扰样本。"""
    if count < 1:
        raise ValueError("抽样量应为正整数。")
    rng = random.Random(seed)
    reservoir = []
    seen: set[str] = set()
    previous_limit = csv.field_size_limit(1_000_000)
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if set(reader.fieldnames or []) != {"url", "paragraph"}:
                raise ValueError("ShopTC 页面条款 CSV 列名与公开格式不符。")
            for row in reader:
                paragraph = " ".join(row["paragraph"].split())
                if len(paragraph) < 40 or not RELATED_WORDS.search(paragraph):
                    continue
                paragraph = clip_utf8(paragraph, 4096)
                identifier = "ST-" + sha256(paragraph.encode()).hexdigest()[:24]
                if identifier in seen:
                    continue
                seen.add(identifier)
                sample = {"id": identifier, "shop_id": BENCHMARK_SHOP, "title": "ShopTC-100K web term",
                          "source": clip_utf8(row["url"], 255) or SHOPTC_SOURCE, "text": paragraph}
                if len(reservoir) < count:
                    reservoir.append(sample)
                elif (index := rng.randrange(len(seen))) < count:
                    reservoir[index] = sample
    finally:
        csv.field_size_limit(previous_limit)
    if len(reservoir) != count:
        raise ValueError(f"只找到 {len(reservoir)} 条相关网页条款，少于请求的 {count} 条。")
    return sorted(reservoir, key=lambda row: row["id"])
