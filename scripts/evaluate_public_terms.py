"""将公开语料导入独立 Milvus 集合，评估售后条款的召回和负载。"""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import file_digest, sha256
from math import ceil
from pathlib import Path
from time import perf_counter

from app.policies.public_benchmark import (
    ACTION_SECTIONS, BENCHMARK_SHOP, RETAIL_SOURCE, SHOPTC_SOURCE, RetrievalCase,
    retail_articles, retail_cases, sample_shoptc,
)
from app.policies.search import PolicyIndexError
from app.settings import local_setting
from app.storage.milvus_policies import MilvusPolicies


TASK_SOURCE = "https://github.com/sierra-research/tau2-bench/blob/main/data/tau2/domains/retail/tasks.json"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class RetrievalResult:
    """记录每个任务的目标条款、检索顺序、延迟及错误。"""

    task_id: str
    expected_ids: tuple[str, ...]
    retrieved_ids: tuple[str, ...]
    elapsed_ms: float
    error: str | None = None


def run_query(index: MilvusPolicies, case: RetrievalCase, *, source: str | None = None) -> RetrievalResult:
    """以一条公开任务原始问题调用一次 Milvus，保留请求失败证据。"""
    started = perf_counter()
    try:
        found = index.search(BENCHMARK_SHOP, case.question, source=source)
    except PolicyIndexError as error:
        return RetrievalResult(case.task_id, case.expected_ids, (), (perf_counter() - started) * 1000, str(error))
    return RetrievalResult(
        case.task_id, case.expected_ids, tuple(article.article_id for article in found), (perf_counter() - started) * 1000
    )


def percentile(values: list[float], fraction: float) -> float | None:
    """用 nearest-rank 计算延迟分位数。"""
    return round(sorted(values)[ceil(len(values) * fraction) - 1], 2) if values else None


def summarize(results: list[RetrievalResult]) -> dict:
    """统计每题是否命中、召回比例、全命中和成功请求延迟。"""
    recall = [
        len(set(result.expected_ids).intersection(result.retrieved_ids)) / len(result.expected_ids)
        for result in results
    ]
    latency = [result.elapsed_ms for result in results if result.error is None]
    return {
        "requests": len(results),
        "failures": len(results) - len(latency),
        "hit_at_3": round(sum(score > 0 for score in recall) / len(results), 4),
        "recall_at_3": round(sum(recall) / len(results), 4),
        "all_at_3": round(sum(score == 1 for score in recall) / len(results), 4),
        "p50_ms": percentile(latency, 0.50),
        "p95_ms": percentile(latency, 0.95),
    }


def provision_index(base_url: str, articles: list[dict], *, existing_only: bool = False,
                    analyzer: str = "standard") -> MilvusPolicies:
    """按语料内容确定集合名，按需导入并核对可检索条款数。"""
    serialized = json.dumps(articles, ensure_ascii=False, sort_keys=True).encode()
    if analyzer != "standard":
        serialized = analyzer.encode() + b"\x00" + serialized
    fingerprint = sha256(serialized).hexdigest()[:16]
    index = MilvusPolicies(base_url, collection_name=f"after_sales_terms_eval_{fingerprint}", analyzer=analyzer)
    target = {"collectionName": index.collection_name}
    exists = index.request("collections/has", target)["has"]
    if existing_only:
        if not exists:
            raise PolicyIndexError(f"只读评测需要已存在的实验集合：{index.collection_name}")
        if index.count_articles(BENCHMARK_SHOP) != len(articles):
            raise PolicyIndexError(f"实验集合条款数与语料不符：{index.collection_name}")
        return index
    if exists:
        index.request("collections/load", target, timeout=30)
        if index.count_articles(BENCHMARK_SHOP) == len(articles):
            return index
    index.index_articles(articles)
    actual = index.count_articles(BENCHMARK_SHOP)
    if actual != len(articles):
        raise PolicyIndexError(f"实验索引期望 {len(articles)} 条，实际读取 {actual} 条。")
    return index


def describe_source(path: Path, url: str) -> dict:
    """记录实验输入来源和内容摘要，便于用同一版本复测。"""
    with path.open("rb") as stream:
        return {"file": path.name, "url": url, "bytes": path.stat().st_size,
                "sha256": file_digest(stream, "sha256").hexdigest()}


def evaluate_pool(index: MilvusPolicies, cases: list[RetrievalCase], *, name: str,
                  articles: int, source: str | None = None) -> dict:
    """对固定问题执行一次 BM25 评测，记录检索范围及逐题结果。"""
    eligible = articles if source is None else index.count_articles(BENCHMARK_SHOP, source=source)
    # 预热索引，首次请求耗时不混入稳定状态的延迟。
    run_query(index, cases[0], source=source)
    results = [run_query(index, case, source=source) for case in cases]
    metrics = summarize(results)
    print(json.dumps({"pool": name, "eligible_articles": eligible, **metrics}, ensure_ascii=False), flush=True)
    return {"collection": index.collection_name, "articles": articles, "eligible_articles": eligible,
            "source_filter": source, **metrics, "results": [asdict(result) for result in results]}


def parse_args() -> argparse.Namespace:
    """读取语料目录、扩容样本量和并发参数。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / ".local/datasets/after-sales-terms")
    parser.add_argument("--noise-count", type=int, default=0, help="加入检索池的 ShopTC 段落数量")
    parser.add_argument("--stress-requests", type=int, default=300, help="在最大检索池上执行的请求数")
    parser.add_argument("--workers", type=int, default=4, help="并发请求线程数")
    parser.add_argument("--existing-only", action="store_true", help="只读复用已加载的实验集合")
    args = parser.parse_args()
    if args.noise_count < 0 or args.stress_requests < 1 or args.workers < 1:
        parser.error("干扰条款数须非负，请求数与并发数须为正。")
    return args


def main() -> int:
    """依次评测基线、可选的扩容检索池和并发性能，保留逐题结果。"""
    args = parse_args()
    base_url = local_setting("MILVUS_URL")
    if not base_url:
        raise PolicyIndexError("请先在本项目的 .env 中配置 MILVUS_URL。")
    policy_path = args.data_dir / "tau-retail/policy.md"
    task_path = args.data_dir / "tau-retail/tasks.json"
    tasks = json.loads(task_path.read_text(encoding="utf-8"))
    articles = retail_articles(policy_path.read_text(encoding="utf-8"))
    cases = retail_cases(tasks, articles)
    report = {
        "retriever": "Milvus BM25",
        "analyzer": "standard",
        "published_tasks": len(tasks),
        "scored_tasks": len(cases),
        "sources": [describe_source(policy_path, RETAIL_SOURCE), describe_source(task_path, TASK_SOURCE)],
        "action_to_sections": ACTION_SECTIONS,
        "section_titles": {article["id"]: article["title"] for article in articles},
        "accuracy": {},
    }
    pools = {"baseline": articles}
    if args.noise_count:
        noise_path = args.data_dir / "shoptc/sanitized_split2.csv"
        pools["with_shoptc"] = articles + sample_shoptc(noise_path, args.noise_count)
        report["sources"].append(describe_source(noise_path, SHOPTC_SOURCE))

    for name, pool in pools.items():
        print(f"{name} 核对 {len(pool)} 条公开文本……", flush=True)
        index = provision_index(base_url, pool, existing_only=args.existing_only)
        report["accuracy"][name] = evaluate_pool(index, cases, name=name, articles=len(pool))
        if name == "with_shoptc":
            # 实验调用方事先知道订单适用的手册，整个题集使用同一来源。
            report["accuracy"]["with_shoptc_rulebook_scope"] = evaluate_pool(
                index, cases, name="with_shoptc_rulebook_scope", articles=len(pool), source=RETAIL_SOURCE,
            )

    print(f"在 {index.collection_name} 发起 {args.stress_requests} 次查询，{args.workers} 个并发。", flush=True)
    sequence = (cases[number % len(cases)] for number in range(args.stress_requests))
    started = perf_counter()
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        stress_results = list(executor.map(lambda case: run_query(index, case), sequence))
    seconds = perf_counter() - started
    stress = summarize(stress_results)
    report["stress"] = {"collection": index.collection_name, "workers": args.workers,
                        "seconds": round(seconds, 3), "requests_per_second": round(len(stress_results) / seconds, 2),
                        **stress, "errors": [asdict(result) for result in stress_results if result.error]}
    output_dir = PROJECT_ROOT / ".local/evaluations"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"terms-retrieval-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}.json"
    with output_path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps(stress, ensure_ascii=False))
    print(f"评测报告 {output_path}")
    return int(bool(stress["failures"] or any(item["failures"] for item in report["accuracy"].values())))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, PolicyIndexError) as error:
        raise SystemExit(str(error)) from None
