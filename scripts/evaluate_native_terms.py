"""用原生中文官方原文对照召回、条款适用范围和最终引用。"""

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from app.policies.public_benchmark import BENCHMARK_SHOP, RetrievalCase
from app.policies.search import PolicyIndexError, PolicySource, deepseek_policy_writer, explain_policy
from app.policies.semantic_terms import E5Encoder, SemanticTerms
from app.settings import local_setting
from app.storage.milvus_policies import MilvusPolicies
from scripts.evaluate_hybrid_terms import compare_query, metrics_for
from scripts.evaluate_public_terms import PROJECT_ROOT, describe_source, provision_index
from scripts.native_terms_sources import JD_MARKET, JD_SELF, LAW_PDF, load_articles


FIXTURE = Path(__file__).parent / "fixtures/native-zh-cases.json"
SCOPES = {
    "self": ("京东自营订单（评测）", (JD_SELF, LAW_PDF)),
    "market": ("京东第三方订单（评测）", (JD_MARKET, LAW_PDF)),
    "general": ("通用网购情形（评测）", (LAW_PDF,)),
}
STRATEGIES = ("bm25_unscoped", "bm25", "rrf", "rrf_window")
ANSWER_STRATEGIES = ("bm25", "rrf")


@dataclass(frozen=True)
class Scenario:
    """保存已知订单范围、检索题目和可选的答复验证词组。"""

    case: RetrievalCase
    scope: str
    answer_checks: list[list[str]] | None


def load_scenarios(articles: dict[str, PolicySource]) -> list[Scenario]:
    """先验证标注范围，评测时仅将原问题传入检索。"""
    scenarios = []
    for row in json.loads(FIXTURE.read_text(encoding="utf-8")):
        allowed = SCOPES[row["scope"]][1]
        expected = tuple(row["expected"])
        if any(articles[article_id].source not in allowed for article_id in expected):
            raise ValueError(f"目标条款超出订单适用范围：{row['id']}")
        scenarios.append(Scenario(RetrievalCase(row["id"], row["question"], expected),
                                  row["scope"], row.get("answer_checks")))
    if len({item.case.task_id for item in scenarios}) != len(scenarios):
        raise ValueError("中文评测编号重复。")
    return scenarios


def compare_scoped_query(index: MilvusPolicies, semantic: SemanticTerms, item: Scenario) -> dict:
    """记录无筛选 BM25，随后对照强制适用范围内的三种排序。"""
    started = perf_counter()
    unscoped = [source.article_id for source in index.search(BENCHMARK_SHOP, item.case.question)]
    unscoped_ms = (perf_counter() - started) * 1000
    run = compare_query(index, semantic, item.case, source=SCOPES[item.scope][1])
    return {**run, "bm25_unscoped": (unscoped, unscoped_ms)}


def evaluate_retrieval(scenarios: list[Scenario], runs: list[dict], articles: dict[str, PolicySource]) -> dict:
    """按题统计证据覆盖、跨范围召回及反例的候选情况。"""
    positives = [(item.case, run) for item, run in zip(scenarios, runs, strict=True) if item.case.expected_ids]
    cases, positive_runs = zip(*positives, strict=True)
    multi = [i for i, case in enumerate(cases) if len(case.expected_ids) > 1]
    experiments = {}
    for strategy in STRATEGIES:
        report = metrics_for(cases, positive_runs, strategy)
        multi_report = metrics_for([cases[i] for i in multi], [positive_runs[i] for i in multi], strategy)
        out_of_scope = [
            item.case.task_id for item, run in zip(scenarios, runs, strict=True)
            if any(articles[article_id].source not in SCOPES[item.scope][1] for article_id in run[strategy][0][:3])
        ]
        negative_hits = [
            item.case.task_id for item, run in zip(scenarios, runs, strict=True)
            if not item.case.expected_ids and run[strategy][0]
        ]
        experiments[strategy] = {
            **report, "multi_clause_tasks": len(multi), "all_at_3_multi_clause": multi_report["all_at_3"],
            "out_of_scope_questions": out_of_scope, "no_evidence_with_candidates": negative_hits,
        }
        print(json.dumps({"strategy": strategy, **{k: v for k, v in experiments[strategy].items()
                                                   if k != "results"}}, ensure_ascii=False), flush=True)
    return experiments


def matches_answer_details(answer: str, checks: list[list[str]]) -> bool:
    """统一空白和括号形式，再检查每个必须回答的要点。"""
    normalized = re.sub(r"[\s（）()]+", "", answer)
    return all(any(text in normalized for text in alternatives) for alternatives in checks)


def evaluate_answers(scenarios: list[Scenario], runs: list[dict], articles: dict[str, PolicySource]) -> dict:
    """用工作台同一生成函数，对比引用齐全、出处范围和无依据行为。"""
    api_key = local_setting("DEEPSEEK_API_KEY")
    if not api_key:
        raise ValueError("实际答复验收需要项目的 DeepSeek API 配置。")
    results = {}
    for strategy in ANSWER_STRATEGIES:
        outcomes = []
        for item, run in zip(scenarios, runs, strict=True):
            if item.answer_checks is None:
                continue
            shop_name, allowed = SCOPES[item.scope]
            candidates = [articles[article_id] for article_id in run[strategy][0][:3]]
            writer = deepseek_policy_writer(api_key, order={"shop_name": shop_name})
            started = perf_counter()
            result = explain_policy(item.case.question, candidates, writer)
            elapsed_ms = round((perf_counter() - started) * 1000, 2)
            citations = set(re.findall(r"\[([^\[\]\n]+)\]", result["answer"]))
            expected = set(item.case.expected_ids)
            missed = sorted(expected - citations)
            wrong_scope = sorted(article_id for article_id in citations
                                 if article_id not in articles or articles[article_id].source not in allowed)
            details_match = matches_answer_details(result["answer"], item.answer_checks)
            expected_status = "answered" if expected else "no_matches"
            success = not missed and not wrong_scope and details_match and result["status"] == expected_status
            outcomes.append({
                "task_id": item.case.task_id, "passed": success, "status": result["status"],
                "citations": sorted(citations), "missing_citations": missed, "wrong_scope": wrong_scope,
                "details_match": details_match, "answer": result["answer"], "generation_ms": elapsed_ms,
            })
            print(f"{strategy} {item.case.task_id} {result['status']} {'通过' if success else '待复查'}", flush=True)
        results[strategy] = outcomes
    return results


def main() -> None:
    """载入本机官方快照，在独立集合上运行评测并写新报告。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true", help="导入独立实验集合")
    parser.add_argument("--with-answers", action="store_true", help="使用项目 DeepSeek API 比较最终答复")
    args = parser.parse_args()
    base_url = local_setting("MILVUS_URL")
    if not base_url:
        raise PolicyIndexError("请先配置 MILVUS_URL。")
    sys.path.append(str(PROJECT_ROOT / ".local/retrieval-deps"))
    root = PROJECT_ROOT / ".local/datasets/after-sales-terms/native-zh"
    raw_articles, sources = load_articles(root)
    articles = {row["id"]: PolicySource(row["id"], row["title"], row["source"], row["text"]) for row in raw_articles}
    scenarios = load_scenarios(articles)
    index = provision_index(base_url, raw_articles, existing_only=not args.prepare, analyzer="chinese")
    encoder = E5Encoder(PROJECT_ROOT / ".local/models/multilingual-e5-small")
    semantic = {scope: SemanticTerms(encoder, [clause for clause in articles.values() if clause.source in allowed])
                for scope, (_, allowed) in SCOPES.items()}
    compare_scoped_query(index, semantic[scenarios[0].scope], scenarios[0])
    runs = [compare_scoped_query(index, semantic[item.scope], item) for item in scenarios]
    report = {
        "collection": index.collection_name,
        "sources": [{**describe_source(path, url), "version_date": date,
                     "version_date_kind": "revision" if url == LAW_PDF else ("effective" if date else None),
                     "clauses": sum(article.source == url for article in articles.values())}
                    for path, url, date in sources],
        "judge_annotations": describe_source(FIXTURE, "project-authored"),
        "article_catalog": {article_id: {"title": clause.title, "source": clause.source}
                            for article_id, clause in articles.items()},
        "retrieval": evaluate_retrieval(scenarios, runs, articles),
        "answers": evaluate_answers(scenarios, runs, articles) if args.with_answers else {},
    }
    output_dir = PROJECT_ROOT / ".local/evaluations"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"native-terms-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}.json"
    with path.open("x", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
    print(f"评测报告 {path}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ImportError, KeyError, ValueError, PolicyIndexError) as error:
        raise SystemExit(str(error)) from None
