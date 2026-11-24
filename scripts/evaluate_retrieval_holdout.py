"""只读对照四种条款检索策略，保留逐题候选、分配和答复核对结果。"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from app.policies.evidence import PolicyEvidencePlan, PolicySource, plan_evidence
from app.policies.public_benchmark import BENCHMARK_SHOP, RetrievalCase
from app.policies.retrieval_ranking import reciprocal_rank_fusion
from app.policies.search import (
    REFERENCE, PolicyIndexError, PolicyReviewer, deepseek_aspect_writer, deepseek_policy_writer, explain_policy,
)
from app.policies.semantic_terms import E5Encoder, SemanticTerms
from app.settings import local_setting
from scripts.evaluate_aspect_terms import AnswerCase, HOLDOUT
from scripts.evaluate_hybrid_terms import CANDIDATE_LIMIT, MODEL_SOURCE, RRF_K
from scripts.evaluate_native_terms import FIXTURE, SCOPES
from scripts.evaluate_public_terms import PROJECT_ROOT, describe_source, percentile, provision_index
from scripts.native_terms_sources import load_articles


CASES = Path(__file__).parent / "fixtures/native-zh-retrieval-holdout.json"
STRATEGIES = ("bm25_whole", "bm25_aspects", "hybrid_aspects", "hybrid_rerank_aspects")


def load_cases(articles: dict[str, PolicySource]) -> list[tuple[AnswerCase, bool]]:
    """核对题目和适用范围，拒绝已有题集中的重复问法。"""
    previous = {row["question"] for path in (FIXTURE, HOLDOUT) for row in json.loads(path.read_text(encoding="utf-8"))}
    ids = set()
    cases = []
    for row in json.loads(CASES.read_text(encoding="utf-8")):
        task_id, question, scope, items = row["id"], row["question"], row["scope"], row["items"]
        allowed = SCOPES[scope][1]
        expected = tuple(dict.fromkeys(article for group in items for article in group))
        if task_id in ids or question in previous or not items or any(articles[ref].source not in allowed for ref in expected):
            raise ValueError(f"留出题重复或标签超出适用范围：{task_id}")
        ids.add(task_id)
        previous.add(question)
        cases.append((AnswerCase(RetrievalCase(task_id, question, expected), scope, items), row.get("check_answer", False)))
    return cases


def rank_hybrid(lexical: list[str], semantic: SemanticTerms, question: str, *, rerank: bool) -> list[str]:
    """融合两路名次，按策略选择是否用局部证据窗口重排前八条。"""
    vector = semantic.query_vector(question)
    dense = semantic.dense_ranking(vector, CANDIDATE_LIMIT)
    fused = reciprocal_rank_fusion(lexical, dense, k=RRF_K)
    if not rerank:
        return fused
    head = fused[:CANDIDATE_LIMIT]
    return reciprocal_rank_fusion(head, semantic.window_ranking(vector, head), k=RRF_K) + fused[CANDIDATE_LIMIT:]


def score_evidence(required: list[list[str]], plan: PolicyEvidencePlan, probes: list[dict]) -> list[dict]:
    """区分未进入候选、已召回但未分配和没有原文依据的事项。"""
    candidates = {article for probe in probes for article in probe["ranked_ids"]}
    selected = [source.article_id for source in plan.sources]
    assigned = [item.article_ids for item in plan.aspects]
    split_matches = len(assigned) == len(required)
    results = []
    for index, labels in enumerate(required):
        evidence = assigned[index] if split_matches else selected
        missing = set(labels) - set(evidence)
        results.append({"required": labels, "assigned": evidence, "covered": not missing if labels else None,
                        "missed_candidate": sorted(missing - candidates),
                        "missed_selection": sorted(missing & candidates)})
    return results


def evaluate_search(index, articles, semantic, item: AnswerCase, strategy: str, *,
                    expand_queries: bool = False) -> tuple[PolicyEvidencePlan, dict]:
    """让后三种策略使用同一个分诉求流程和给模型的证据预算。"""
    allowed = SCOPES[item.scope][1]
    probes = []

    def search(query: str, limit: int, follow_up: bool) -> list[PolicySource]:
        """在固定渠道内记录每次查询的候选和保留的前几条。"""
        hybrid = strategy.startswith("hybrid")
        hits = index.search(BENCHMARK_SHOP, query, source=allowed, limit=CANDIDATE_LIMIT if hybrid else limit,
                            timeout=2 if follow_up else 8)
        ids = [source.article_id for source in hits]
        if hybrid:
            ids = rank_hybrid(ids, semantic, query, rerank=strategy == "hybrid_rerank_aspects")
        probes.append({"query": query, "ranked_ids": ids, "used_ids": ids[:limit]})
        return [articles[article_id] for article_id in ids[:limit]]

    question = item.case.question
    started = perf_counter()
    plan = (PolicyEvidencePlan(search(question, 3, False)) if strategy == "bm25_whole"
            else plan_evidence(question, search, expand_queries=expand_queries))
    elapsed = round((perf_counter() - started) * 1000, 2)
    return plan, {"elapsed_ms": elapsed, "selected": [source.article_id for source in plan.sources],
                  "parsed_aspects": [aspect.question for aspect in plan.aspects], "probes": probes,
                  "out_of_scope": sorted({article for probe in probes for article in probe["ranked_ids"]
                                          if articles[article].source not in allowed}),
                  "items": score_evidence(item.expected_items, plan, probes)}


def evaluate_answer(api_key: str, item: AnswerCase, plan: PolicyEvidencePlan, *,
                    reviewer: PolicyReviewer | None = None) -> dict:
    """使用应用的答复与出处校验，标记无依据事项和漏引。"""
    shop, _ = SCOPES[item.scope]
    context = {"shop_name": shop}
    started = perf_counter()
    result = explain_policy(item.case.question, plan.sources, deepseek_policy_writer(api_key, order=context),
                            aspects=plan.aspects, aspect_writer=deepseek_aspect_writer(api_key, order=context),
                            reviewer=reviewer)
    return score_answer(item, result, (perf_counter() - started) * 1000)


def score_answer(item: AnswerCase, result: dict, elapsed_ms: float) -> dict:
    """按每个事项的标注核对同一份模型答复。"""
    _, allowed = SCOPES[item.scope]
    items = result.get("items", [])
    citations = set(REFERENCE.findall(result["answer"]))
    required = item.expected_items
    missing = sorted(set(item.case.expected_ids) - citations)
    wrong_scope = any(source["source"] not in allowed for source in result["sources"])
    item_checks = None
    if len(items) == len(required):
        item_checks = [set(labels) <= set(line["citations"]) and line["status"] == "answered"
                       if labels else line["status"] == "needs_review"
                       for labels, line in zip(required, items, strict=True)]
    elif len(required) == 1:
        item_checks = [not missing and result["status"] == ("answered" if required[0] else "no_matches")]
    expected_status = "answered" if item.case.expected_ids else "no_matches"
    return {"status": result["status"], "answer": result["answer"], "citations": sorted(citations), "items": items,
            "missing_citations": missing, "out_of_scope": wrong_scope, "per_item_checks": item_checks,
            "unsupported_checks": [passed for labels, passed in zip(required, item_checks, strict=True) if not labels]
            if item_checks is not None else [],
            "citation_and_status_pass": not missing and not wrong_scope and result["status"] == expected_status,
            "generation_ms": round(elapsed_ms, 2)}


def summarize(rows: list[dict], strategy: str) -> dict:
    """分别汇总所有有依据事项、多问、越界和模型样本。"""
    reports = [row["strategies"][strategy] for row in rows]
    covered = [item["covered"] for report in reports for item in report["items"] if item["covered"] is not None]
    multi = [r for r in reports if len(r["items"]) > 1 and any(item["required"] for item in r["items"])]
    answers = [r["answer"] for r in reports if "answer" in r]
    per_item = [c for answer in answers if (c := answer["per_item_checks"]) is not None]
    unsupported = [passed for answer in answers for passed in answer["unsupported_checks"]]
    return {"covered_items": sum(covered), "supported_items": len(covered),
            "multi_all_covered": sum(all(i["covered"] for i in r["items"] if i["required"]) for r in multi),
            "multi_questions_with_evidence": len(multi), "out_of_scope_questions": sum(bool(r["out_of_scope"]) for r in reports),
            "retrieval_p95_ms": round(percentile([r["elapsed_ms"] for r in reports], 0.95), 2),
            "answer_sample": len(answers), "citation_and_status_pass": sum(a["citation_and_status_pass"] for a in answers),
            "per_item_questions_checked": len(per_item), "per_item_questions_pass": sum(all(c) for c in per_item),
            "unsupported_items_checked": len(unsupported), "unsupported_items_referred": sum(unsupported)}


def main() -> None:
    """运行独立题集，使用已导入的 Milvus 集合并写入新的报告。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-answers", action="store_true", help="对预先选定的题使用已配置的 DeepSeek API")
    args = parser.parse_args()
    base_url = local_setting("MILVUS_URL")
    if not base_url:
        raise PolicyIndexError("请先配置 MILVUS_URL。")
    api_key = local_setting("DEEPSEEK_API_KEY") if args.with_answers else ""
    if args.with_answers and not api_key:
        raise ValueError("答复验收需要项目的 DeepSeek API 配置。")
    sys.path.append(str(PROJECT_ROOT / ".local/retrieval-deps"))
    raw, sources = load_articles(PROJECT_ROOT / ".local/datasets/after-sales-terms/native-zh")
    articles = {row["id"]: PolicySource(row["id"], row["title"], row["source"], row["text"]) for row in raw}
    cases = load_cases(articles)
    index = provision_index(base_url, raw, existing_only=True, analyzer="chinese")
    model_dir = PROJECT_ROOT / ".local/models/multilingual-e5-small"
    encoder = E5Encoder(model_dir)
    semantic = {scope: SemanticTerms(encoder, [clause for clause in articles.values() if clause.source in allowed])
                for scope, (_, allowed) in SCOPES.items()}
    # 预热使用原有题集，正式留出题只按预先标注评分。
    warmup = AnswerCase(RetrievalCase("warmup", "快递寄回，取件费在哪看？", ()), "self", [[]])
    for strategy in STRATEGIES:
        evaluate_search(index, articles, semantic[warmup.scope], warmup, strategy)
    rows = []
    generation = []
    for item, sample_answer in cases:
        row = {"id": item.case.task_id, "question": item.case.question, "scope": item.scope, "strategies": {}}
        for strategy in STRATEGIES:
            plan, report = evaluate_search(index, articles, semantic[item.scope], item, strategy)
            row["strategies"][strategy] = report
            if api_key and sample_answer:
                generation.append((item, plan, report))
        rows.append(row)
    print(f"已测 {len(rows)} 道题的四种检索策略，待核对模型答复 {len(generation)} 份。", flush=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        work = [pool.submit(evaluate_answer, api_key, item, plan) for item, plan, _ in generation]
        for (_, _, report), job in zip(generation, work, strict=True):
            report["answer"] = job.result()
    summary = {strategy: summarize(rows, strategy) for strategy in STRATEGIES}
    output_dir = PROJECT_ROOT / ".local/evaluations"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"retrieval-holdout-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}.json"
    report = {
        "fixture": describe_source(CASES, "project-authored"),
        "collection": index.collection_name,
        "sources": [describe_source(path, url) for path, url, _ in sources],
        "model_files": [describe_source(model_dir / name, MODEL_SOURCE) for name in ("model_int8.onnx", "tokenizer.json")],
        "summary": summary,
        "cases": rows,
    }
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False))
    print(f"评测报告 {output}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ImportError, KeyError, ValueError, PolicyIndexError) as error:
        raise SystemExit(str(error)) from None
