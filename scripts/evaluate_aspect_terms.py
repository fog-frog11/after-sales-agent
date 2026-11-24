"""对照原生中文的固定三条与按诉求选证据，并核对最终逐项引用。"""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from app.policies.evidence import PolicyEvidencePlan
from app.policies.public_benchmark import BENCHMARK_SHOP, RetrievalCase
from app.policies.search import (
    REFERENCE, PolicyIndexError, PolicySource, deepseek_aspect_writer, deepseek_policy_writer, explain_policy,
)
from app.settings import local_setting
from app.storage.milvus_policies import MilvusPolicies
from scripts.evaluate_native_terms import SCOPES, load_scenarios
from scripts.evaluate_public_terms import PROJECT_ROOT, describe_source, percentile, provision_index
from scripts.native_terms_sources import load_articles


HOLDOUT = Path(__file__).parent / "fixtures/native-zh-aspect-holdout.json"


@dataclass(frozen=True)
class AnswerCase:
    """保存评测题、订单范围和可逐项核对的引用标签。"""

    case: RetrievalCase
    scope: str
    expected_items: list[list[str]] | None = None


def load_holdout(articles: dict[str, PolicySource]) -> list[AnswerCase]:
    """先校验留出题允许的来源，检索回调只收到问题和可信范围。"""
    result = []
    for row in json.loads(HOLDOUT.read_text(encoding="utf-8")):
        allowed = SCOPES[row["scope"]][1]
        expected = tuple(dict.fromkeys(article for item in row["expected_items"] for article in item))
        if any(articles[article].source not in allowed for article in expected):
            raise ValueError(f"留出题的标签超出渠道范围：{row['id']}")
        result.append(AnswerCase(RetrievalCase(row["id"], row["question"], expected),
                                 row["scope"], row["expected_items"]))
    return result


def evaluate_retrieval(index: MilvusPolicies, case: AnswerCase) -> tuple[dict, PolicyEvidencePlan, PolicyEvidencePlan]:
    """测量固定三条及分诉求的真实查询和候选覆盖。"""
    question = case.case.question
    expected = set(case.case.expected_ids)
    allowed_sources = SCOPES[case.scope][1]
    started = perf_counter()
    original = PolicyEvidencePlan(index.search(BENCHMARK_SHOP, question, source=allowed_sources))
    baseline_ms = round((perf_counter() - started) * 1000, 2)
    started = perf_counter()
    expanded = index.search_evidence(BENCHMARK_SHOP, question, source=allowed_sources)
    aspect_ms = round((perf_counter() - started) * 1000, 2)
    row = {"task_id": case.case.task_id, "expected": sorted(expected), "scope": case.scope,
           "parsed_aspects": len(expanded.aspects)}
    for strategy, plan, elapsed_ms in (("fixed_three", original, baseline_ms), ("by_aspect", expanded, aspect_ms)):
        candidates = [item.article_id for item in plan.sources]
        assigned = [part.article_ids for part in plan.aspects]
        visible = {article for item in assigned for article in item} if assigned else set(candidates)
        row[strategy] = {
            "elapsed_ms": elapsed_ms, "candidates": candidates, "assigned": assigned,
            "all_evidence_visible": expected <= visible,
            "out_of_scope": [article.article_id for article in plan.sources if article.source not in allowed_sources],
        }
    return row, original, expanded


def evaluate_answer(api_key: str, item: AnswerCase, strategy: str, plan: PolicyEvidencePlan) -> dict:
    """使用与工作台相同的答复和引用验证，核对各事项是否落地。"""
    shop_name, allowed_sources = SCOPES[item.scope]
    context = {"shop_name": shop_name}
    started = perf_counter()
    result = explain_policy(
        item.case.question, plan.sources, deepseek_policy_writer(api_key, order=context),
        aspects=plan.aspects, aspect_writer=deepseek_aspect_writer(api_key, order=context),
    )
    elapsed_ms = round((perf_counter() - started) * 1000, 2)
    items = result.get("items", [])
    citations = {reference for line in items for reference in line["citations"]} if items else set(
        REFERENCE.findall(result["answer"])
    )
    expected = set(item.case.expected_ids)
    expected_status = "answered" if expected else "no_matches"
    scope_wrong = any(source["source"] not in allowed_sources for source in result["sources"])
    missing = sorted(expected - citations)
    expected_items = item.expected_items
    if expected_items is not None and len(expected_items) > 1:
        if strategy == "by_aspect":
            per_item_pass = len(items) == len(expected_items) and all(
                (line["status"] == "answered" and set(required) <= set(line["citations"])) if required
                else line["status"] == "needs_review"
                for line, required in zip(items, expected_items, strict=True)
            )
        else:
            per_item_pass = not any(not required for required in expected_items) or "人工核对" in result["answer"]
    else:
        per_item_pass = True
    passed = not missing and not scope_wrong and per_item_pass and result["status"] == expected_status
    print(f"{strategy} {item.case.task_id} {result['status']} {'通过' if passed else '待复查'}", flush=True)
    return {
        "task_id": item.case.task_id, "passed": passed, "status": result["status"],
        "missing_citations": missing, "scope_wrong": scope_wrong, "per_item_pass": per_item_pass,
        "answer": result["answer"], "items": items, "generation_ms": elapsed_ms,
    }


def main() -> None:
    """只读复用现有原文集合，保存检索及可选真实答复的完整对照。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-answers", action="store_true", help="使用已配置的 DeepSeek API 验证最终答复")
    args = parser.parse_args()
    base_url = local_setting("MILVUS_URL")
    if not base_url:
        raise PolicyIndexError("请先配置 MILVUS_URL。")
    raw, sources = load_articles(PROJECT_ROOT / ".local/datasets/after-sales-terms/native-zh")
    catalog = {row["id"]: PolicySource(row["id"], row["title"], row["source"], row["text"]) for row in raw}
    previous = [AnswerCase(row.case, row.scope) for row in load_scenarios(catalog)]
    holdout = load_holdout(catalog)
    cases = previous + holdout
    index = provision_index(base_url, raw, existing_only=True, analyzer="chinese")
    evaluate_retrieval(index, cases[0])
    runs = [evaluate_retrieval(index, item) for item in cases]
    rows = [row for row, _, _ in runs]
    single = [row for row in rows if not row["parsed_aspects"]]
    multi = [row for row in rows if row["parsed_aspects"]]
    summary = {
        "queries": len(rows),
        "parsed_multi": len(rows) - len(single),
        "single_p95_ms": {strategy: percentile([row[strategy]["elapsed_ms"] for row in single], 0.95)
                          for strategy in ("fixed_three", "by_aspect")},
        "multi_p95_ms": {strategy: percentile([row[strategy]["elapsed_ms"] for row in multi], 0.95)
                          for strategy in ("fixed_three", "by_aspect")},
        "scope_errors": {strategy: sum(bool(row[strategy]["out_of_scope"]) for row in rows)
                         for strategy in ("fixed_three", "by_aspect")},
    }
    answers = {}
    if args.with_answers:
        api_key = local_setting("DEEPSEEK_API_KEY")
        if not api_key:
            raise ValueError("请先配置项目的 DeepSeek API。")
        pairs = [(item, baseline, plan) for item, (_, baseline, plan) in zip(cases, runs, strict=True)
                 if item.expected_items is not None or len(item.case.expected_ids) > 1]
        with ThreadPoolExecutor(max_workers=3) as pool:
            for strategy, position in (("fixed_three", 1), ("by_aspect", 2)):
                work = [pool.submit(evaluate_answer, api_key, pair[0], strategy, pair[position]) for pair in pairs]
                answers[strategy] = [job.result() for job in work]
    output_dir = PROJECT_ROOT / ".local/evaluations"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"native-aspects-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}.json"
    report = {
        "collection": index.collection_name,
        "sources": [describe_source(path, url) for path, url, _ in sources],
        "holdout": describe_source(HOLDOUT, "project-authored"),
        "summary": summary, "retrieval": rows, "answers": answers,
    }
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False))
    print(f"评测报告 {output}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, PolicyIndexError) as error:
        raise SystemExit(str(error)) from None
