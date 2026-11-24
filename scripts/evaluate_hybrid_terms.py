"""在同一适用手册内，对照关键词、语义、融合和证据窗口重排。"""

import argparse
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from time import perf_counter

from app.policies.public_benchmark import (
    BENCHMARK_SHOP, RETAIL_SOURCE, SHOPTC_SOURCE, RetrievalCase, retail_articles, retail_cases, sample_shoptc,
)
from app.policies.retrieval_ranking import reciprocal_rank_fusion
from app.policies.search import PolicyIndexError, PolicySource
from app.policies.semantic_terms import E5Encoder, SemanticTerms
from app.settings import local_setting
from app.storage.milvus_policies import MilvusPolicies
from scripts.evaluate_public_terms import (
    PROJECT_ROOT, TASK_SOURCE, RetrievalResult, describe_source, provision_index, summarize,
)


MODEL_SOURCE = "https://huggingface.co/Xenova/multilingual-e5-small/tree/main"
CANDIDATE_LIMIT = 8
TOP_K = 3
RRF_K = 60


def compare_query(index: MilvusPolicies, semantic: SemanticTerms, case: RetrievalCase, *,
                  lexical_weight: float | None = None, source: str | tuple[str, ...] = RETAIL_SOURCE) -> dict:
    """每路检索只执行一次，记录各策略最终名次和单请求用时。"""
    started = perf_counter()
    bm25 = [article.article_id for article in index.search(
        BENCHMARK_SHOP, case.question, source=source, limit=CANDIDATE_LIMIT,
    )]
    bm25_ms = (perf_counter() - started) * 1000

    started = perf_counter()
    query_vector = semantic.query_vector(case.question)
    dense = semantic.dense_ranking(query_vector, CANDIDATE_LIMIT)
    dense_ms = (perf_counter() - started) * 1000

    started = perf_counter()
    fused = reciprocal_rank_fusion(bm25, dense, k=RRF_K)
    fusion_ms = (perf_counter() - started) * 1000
    # 第二阶段只在融合后的候选中寻找与问题最相关的证据窗口。
    started = perf_counter()
    candidates = fused[:CANDIDATE_LIMIT]
    window_order = semantic.window_ranking(query_vector, candidates)
    reranked = reciprocal_rank_fusion(candidates, window_order, k=RRF_K)
    rerank_ms = (perf_counter() - started) * 1000

    fusion_total = bm25_ms + dense_ms + fusion_ms
    results = {
        "bm25": (bm25, bm25_ms),
        "dense": (dense, dense_ms),
        "rrf": (fused, fusion_total),
        "rrf_window": (reranked, fusion_total + rerank_ms),
    }
    if lexical_weight is not None:
        started = perf_counter()
        weighted = reciprocal_rank_fusion(bm25, dense, k=RRF_K, weights=(lexical_weight, 1.0))
        results["weighted_rrf"] = (weighted, bm25_ms + dense_ms + (perf_counter() - started) * 1000)
    return results


def metrics_for(cases: list[RetrievalCase], runs: list[dict], strategy: str) -> dict:
    """在统一的前三条口径上评分，并保留前三条之外的候选。"""
    results = []
    rankings = []
    candidate_recall = []
    for case, run in zip(cases, runs, strict=True):
        ranking, elapsed_ms = run[strategy]
        rankings.append(ranking)
        results.append(RetrievalResult(case.task_id, case.expected_ids, tuple(ranking[:TOP_K]), elapsed_ms))
        candidate_recall.append(len(set(case.expected_ids).intersection(ranking)) / len(case.expected_ids))
    reciprocal_ranks = [
        next((1 / rank for rank, article in enumerate(result.retrieved_ids, start=1)
              if article in result.expected_ids), 0.0)
        for result in results
    ]
    return {
        **summarize(results),
        "mrr_at_3": round(sum(reciprocal_ranks) / len(cases), 4),
        "candidate_recall": round(sum(candidate_recall) / len(cases), 4),
        "mean_candidate_count": round(sum(map(len, rankings)) / len(cases), 2),
        "results": [{**asdict(result), "candidate_order": ranking}
                    for result, ranking in zip(results, rankings, strict=True)],
    }


def parse_args() -> argparse.Namespace:
    """读取已经下载的数据、模型和依赖目录。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / ".local/datasets/after-sales-terms")
    parser.add_argument("--model-dir", type=Path, default=PROJECT_ROOT / ".local/models/multilingual-e5-small")
    parser.add_argument("--deps-dir", type=Path, default=PROJECT_ROOT / ".local/retrieval-deps")
    parser.add_argument("--noise-count", type=int, default=10_000, help="复用已加载的对应规模实验集合")
    parser.add_argument("--lexical-weight", type=float, help="可选，评估 BM25 相对向量一路的 RRF 权重")
    args = parser.parse_args()
    if args.noise_count < 0:
        parser.error("干扰条款数须非负。")
    if args.lexical_weight is not None and (not isfinite(args.lexical_weight) or args.lexical_weight <= 0):
        parser.error("BM25 权重须为有限正数。")
    return args


def main() -> None:
    """复用已加载集合，以真实问题运行消融对照并写入一份评测报告。"""
    args = parse_args()
    base_url = local_setting("MILVUS_URL")
    if not base_url:
        raise PolicyIndexError("请先为项目配置 MILVUS_URL。")
    if args.deps_dir.is_dir():
        sys.path.append(str(args.deps_dir.resolve()))
    policy_path = args.data_dir / "tau-retail/policy.md"
    task_path = args.data_dir / "tau-retail/tasks.json"
    articles = retail_articles(policy_path.read_text(encoding="utf-8"))
    tasks = json.loads(task_path.read_text(encoding="utf-8"))
    cases = retail_cases(tasks, articles)
    sources = [describe_source(policy_path, RETAIL_SOURCE), describe_source(task_path, TASK_SOURCE)]
    pool = articles
    if args.noise_count:
        noise_path = args.data_dir / "shoptc/sanitized_split2.csv"
        pool = articles + sample_shoptc(noise_path, args.noise_count)
        sources.append(describe_source(noise_path, SHOPTC_SOURCE))
    index = provision_index(base_url, pool, existing_only=True)
    if index.count_articles(BENCHMARK_SHOP, source=RETAIL_SOURCE) != len(articles):
        raise PolicyIndexError("Milvus 中适用手册的条款数与源文件不符。")

    encoder = E5Encoder(args.model_dir)
    semantic = SemanticTerms(encoder, [
        PolicySource(row["id"], row["title"], row["source"], row["text"]) for row in articles
    ])
    compare_query(index, semantic, cases[0], lexical_weight=args.lexical_weight)
    print(f"开始评测 {len(cases)} 道公开零售题，适用手册共 {len(articles)} 条。", flush=True)
    runs = [compare_query(index, semantic, case, lexical_weight=args.lexical_weight) for case in cases]
    experiments = {strategy: metrics_for(cases, runs, strategy) for strategy in runs[0]}
    report = {
        "collection": index.collection_name,
        "indexed_articles": len(pool),
        "eligible_articles": len(articles),
        "source_filter": RETAIL_SOURCE,
        "sources": sources,
        "model_files": [describe_source(args.model_dir / name, MODEL_SOURCE)
                        for name in ("model_int8.onnx", "tokenizer.json")],
        "candidate_limit": CANDIDATE_LIMIT,
        "rrf_k": RRF_K,
        "exploratory_bm25_weight": args.lexical_weight,
        "section_titles": {row["id"]: row["title"] for row in articles},
        "accuracy": experiments,
    }
    output_dir = PROJECT_ROOT / ".local/evaluations"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"hybrid-terms-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}.json"
    with output_path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    for strategy, result in experiments.items():
        print(json.dumps({"strategy": strategy, **{k: v for k, v in result.items() if k != "results"}}))
    print(f"评测报告 {output_path}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, ImportError, PolicyIndexError) as error:
        raise SystemExit(str(error)) from None
