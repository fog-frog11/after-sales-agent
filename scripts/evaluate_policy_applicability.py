"""对照条款查询改写和适用性复核，输出逐题依据、答复和耗时。"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from app.integrations.deepseek import ModelError
from app.policies.evidence import PolicyEvidencePlan, PolicySource
from app.policies.search import (
    PolicyIndexError, PolicyReviewer, deepseek_aspect_writer, deepseek_policy_writer, explain_policy,
)
from app.policies.verification import deepseek_policy_reviewer
from app.settings import local_setting
from scripts.evaluate_retrieval_holdout import (
    PROJECT_ROOT, SCOPES, AnswerCase, RetrievalCase, describe_source, evaluate_answer, evaluate_search,
    load_articles, load_cases, percentile, provision_index, score_answer,
)


FIXTURE = Path(__file__).parent / "fixtures/native-zh-policy-applicability.json"
PREVIOUS_SAMPLES = {"R10", "R15", "R21"}


def read_new_cases(articles: dict[str, PolicySource]) -> list[AnswerCase]:
    """加载新增问题，并校验标注条款属于本题渠道。"""
    cases = []
    for row in json.loads(FIXTURE.read_text(encoding="utf-8")):
        allowed = SCOPES[row["scope"]][1]
        expected = tuple(dict.fromkeys(article for group in row["items"] for article in group))
        if not row["items"] or any(articles[ref].source not in allowed for ref in expected):
            raise ValueError(f"适用性标注超出本题渠道范围：{row['id']}")
        cases.append(AnswerCase(RetrievalCase(row["id"], row["question"], expected), row["scope"], row["items"]))
    return cases


def summarize_retrieval(rows: list[dict], variant: str) -> dict:
    """统计字段级覆盖和存在依据的多问题全覆盖。"""
    reports = [row[variant] for row in rows]
    groups = [[item["covered"] for item in row["items"] if item["covered"] is not None] for row in reports]
    multi = [group for group, row in zip(groups, reports, strict=True) if group and len(row["items"]) > 1]
    return {"covered": sum(sum(group) for group in groups), "supported": sum(map(len, groups)),
            "complete_multi": sum(map(all, multi)), "multi": len(multi),
            "scope_violations": sum(bool(row["out_of_scope"]) for row in reports),
            "p95_ms": round(percentile([row["elapsed_ms"] for row in reports], 0.95), 2)}


def summarize_answers(rows: list[dict], variant: str) -> dict:
    """分开统计拒答是否正确和原来有依据的事项是否仍能答复。"""
    correct_supported = correct_unsupported = supported = unsupported = passed = 0
    times = []
    for row in rows:
        answer = row.get(variant)
        if answer is None:
            continue
        labels = row["expected"]
        checks = answer["per_item_checks"] or [False] * len(labels)
        for expected, correct in zip(labels, checks, strict=True):
            if expected:
                supported += 1
                correct_supported += correct
            else:
                unsupported += 1
                correct_unsupported += correct
        passed += all(checks)
        times.append(answer["generation_ms"])
    return {"passed_questions": passed, "questions": len(times),
            "supported_correct": correct_supported, "supported": supported,
            "unsupported_referred": correct_unsupported, "unsupported": unsupported,
            "p50_ms": round(percentile(times, 0.5), 2) if times else None,
            "p95_ms": round(percentile(times, 0.95), 2) if times else None}


def compare_same_draft(api_key: str, item: AnswerCase, plan: PolicyEvidencePlan,
                       reviewer: PolicyReviewer) -> dict:
    """只生成一次草稿，重放后单独测量复核对答复的影响。"""
    context = {"shop_name": SCOPES[item.scope][0]}
    question = item.case.question
    started = perf_counter()
    draft = None
    failure = None
    try:
        if plan.sources and plan.aspects:
            draft = deepseek_aspect_writer(api_key, order=context)(question, plan.sources, plan.aspects)
        elif plan.sources:
            draft = deepseek_policy_writer(api_key, order=context)(question, plan.sources)
    except ModelError as error:
        failure = error

    def replay(*_):
        """把同一份草稿或生成失败重放给现有答复流程。"""
        if failure is not None:
            raise failure
        return draft

    before = explain_policy(question, plan.sources, replay, aspects=plan.aspects, aspect_writer=replay)
    base_ms = (perf_counter() - started) * 1000
    started = perf_counter()
    after = explain_policy(question, plan.sources, replay, aspects=plan.aspects, aspect_writer=replay, reviewer=reviewer)
    after_ms = (perf_counter() - started) * 1000
    return {"expanded_answer": score_answer(item, before, base_ms),
            "reviewed_answer": score_answer(item, after, base_ms + after_ms), "review_ms": round(after_ms, 2)}


def main() -> None:
    """只读复用已有 Milvus 语料，按需对同一份候选运行真实 DeepSeek 对照。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-answers", action="store_true", help="使用已配置的 DeepSeek API")
    args = parser.parse_args()
    url = local_setting("MILVUS_URL")
    key = local_setting("DEEPSEEK_API_KEY") if args.with_answers else ""
    if not url or (args.with_answers and not key):
        raise ValueError("请检查本机 Milvus 和 DeepSeek 配置。")
    sys.path.append(str(PROJECT_ROOT / ".local/retrieval-deps"))
    raw, sources = load_articles(PROJECT_ROOT / ".local/datasets/after-sales-terms/native-zh")
    articles = {row["id"]: PolicySource(row["id"], row["title"], row["source"], row["text"]) for row in raw}
    index = provision_index(url, raw, existing_only=True, analyzer="chinese")
    previous = load_cases(articles)
    new = read_new_cases(articles)
    rows = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = []
        reviewer = deepseek_policy_reviewer(key) if key else None
        for item, sample in [*previous, *((case, True) for case in new)]:
            base_plan, baseline = evaluate_search(index, articles, None, item, "bm25_aspects")
            plan, expanded = evaluate_search(index, articles, None, item, "bm25_aspects", expand_queries=True)
            row = {"id": item.case.task_id, "scope": item.scope, "question": item.case.question,
                   "expected": item.expected_items, "baseline_retrieval": baseline, "expanded_retrieval": expanded}
            rows.append(row)
            if key and (sample or item.case.task_id in PREVIOUS_SAMPLES):
                jobs.append((row, "baseline_answer", pool.submit(evaluate_answer, key, item, base_plan)))
                jobs.append((row, None, pool.submit(compare_same_draft, key, item, plan, reviewer)))
        print(f"已对照 {len(rows)} 道题的检索，待核对 {len(jobs)} 份实际草稿。", flush=True)
        for row, variant, job in jobs:
            if variant is None:
                row.update(job.result())
            else:
                row[variant] = job.result()

    report = {"collection": index.collection_name, "fixture": describe_source(FIXTURE, "project-authored"),
              "sources": [describe_source(path, link) for path, link, _ in sources], "cases": rows,
              "summary": {"retrieval": {variant: summarize_retrieval(rows, variant)
                                        for variant in ("baseline_retrieval", "expanded_retrieval")},
                          "answers": {variant: summarize_answers(rows, variant)
                                      for variant in ("baseline_answer", "expanded_answer", "reviewed_answer")}}}
    output = PROJECT_ROOT / ".local/evaluations" / f"policy-applicability-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps(report["summary"], ensure_ascii=False))
    print(f"评测报告 {output}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, PolicyIndexError) as error:
        raise SystemExit(str(error)) from None
