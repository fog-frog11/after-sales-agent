"""用公开零售任务的中文改写验证多诉求覆盖和无依据检索。"""

import argparse
import json
import re
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from app.policies.public_benchmark import BENCHMARK_SHOP, RetrievalCase, retail_articles, retail_cases
from app.policies.search import PolicyIndexError, PolicySource
from app.policies.semantic_terms import E5Encoder, SemanticTerms
from app.settings import local_setting
from app.storage.milvus_policies import MilvusPolicies
from scripts.evaluate_hybrid_terms import compare_query, metrics_for
from scripts.evaluate_public_terms import PROJECT_ROOT, describe_source, provision_index


BUNDLE_SOURCE = "https://github.com/sierra-research/tau2-bench/blob/v2.0.0/data/tau2/multilingual/zh/regional/retail.json"
FIXTURE = Path(__file__).parent / "fixtures/tau-retail-zh.json"
BREAK = re.compile(r"[？?；;]|[，。！!、,]\s*(?:另外|顺便|还有|此外)\s*[，,]?")


def load_cases(bundle_path: Path) -> tuple[list[dict], list[RetrievalCase], list[RetrievalCase], dict]:
    """核对官方包版本，把中文问法与原任务及原动作标注对应。"""
    info = describe_source(bundle_path, BUNDLE_SOURCE)
    localized = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if info["sha256"] != localized["bundle_sha256"]:
        raise ValueError("中文问法与此版本的零售包不匹配。")
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    original = retail_articles(bundle["policies"]["policy"])
    articles = []
    for row in original:
        title, text = localized["rules"][row["title"]]
        articles.append(dict(row, title=title, text=f"{title}\n{text}", source=BUNDLE_SOURCE))
    originals = retail_cases(bundle["tasks"], original)
    questions = localized["questions"]
    positives = [replace(case, question=questions[task_id])
                 for case in originals if (task_id := case.task_id.split("_", 1)[0]) in questions]
    if len(positives) != len(questions):
        raise ValueError("部分中文题找不到对应的公开任务或动作标注。")
    negatives = [RetrievalCase(task_id, question, ()) for task_id, question in localized["no_evidence"]]
    return articles, positives, negatives, info


def cover_aspects(index: MilvusPolicies, case: RetrievalCase, bm25: tuple[list[str], float]) -> tuple[list[str], float]:
    """每个明确拆出的诉求先占用一个位置，再补回整句的候选。"""
    parts = list(dict.fromkeys(part.strip(" ，。") for part in BREAK.split(case.question)
                               if len(part.strip(" ，。")) >= 6))
    if not 2 <= len(parts) <= 3:
        return bm25
    started = perf_counter()
    first_hits = []
    for part in parts:
        found = index.search(BENCHMARK_SHOP, part, source=BUNDLE_SOURCE, limit=1)
        if found:
            first_hits.append(found[0].article_id)
    ranking = list(dict.fromkeys([*first_hits, *bm25[0]]))
    return ranking, bm25[1] + (perf_counter() - started) * 1000


def main() -> None:
    """运行同一中文语料下的各策略，单独统计无证据问题的误召回。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true", help="导入已获授权的独立中文评测集合")
    args = parser.parse_args()
    base_url = local_setting("MILVUS_URL")
    if not base_url:
        raise PolicyIndexError("请先配置 MILVUS_URL。")
    bundle_path = PROJECT_ROOT / ".local/datasets/after-sales-terms/tau-retail-zh/retail.json"
    articles, positives, negatives, bundle_info = load_cases(bundle_path)
    index = provision_index(base_url, articles, existing_only=not args.prepare, analyzer="chinese")
    deps_dir = PROJECT_ROOT / ".local/retrieval-deps"
    sys.path.append(str(deps_dir))
    semantic = SemanticTerms(E5Encoder(PROJECT_ROOT / ".local/models/multilingual-e5-small"), [
        PolicySource(article["id"], article["title"], article["source"], article["text"]) for article in articles
    ])
    compare_query(index, semantic, positives[0], source=BUNDLE_SOURCE)
    runs = []
    for case in [*positives, *negatives]:
        run = compare_query(index, semantic, case, lexical_weight=4, source=BUNDLE_SOURCE)
        run["bm25_by_aspect"] = cover_aspects(index, case, run["bm25"])
        runs.append(run)
    multi = [i for i, case in enumerate(positives) if len(case.expected_ids) > 1]
    experiments = {}
    for strategy in runs[0]:
        metrics = metrics_for(positives, runs[:len(positives)], strategy)
        multi_metrics = metrics_for([positives[i] for i in multi], [runs[i] for i in multi], strategy)
        wrong = [case.task_id for case, run in zip(negatives, runs[len(positives):], strict=True)
                 if run[strategy][0]]
        experiments[strategy] = {
            **metrics, "all_at_3_multi_clause": multi_metrics["all_at_3"], "multi_clause_tasks": len(multi),
            "no_evidence_tasks": len(negatives), "no_evidence_with_candidates": wrong,
        }
        print(json.dumps({"strategy": strategy, **{k: v for k, v in experiments[strategy].items() if k != "results"}}))
    report = {
        "collection": index.collection_name,
        "analyzer": "chinese",
        "official_scenarios": bundle_info,
        "chinese_annotations": describe_source(FIXTURE, "project-authored"),
        "section_titles": {article["id"]: article["title"] for article in articles},
        "accuracy": experiments,
    }
    output_dir = PROJECT_ROOT / ".local/evaluations"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"chinese-terms-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}.json"
    with output_path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(f"评测报告 {output_path}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ImportError, KeyError, ValueError, PolicyIndexError) as error:
        raise SystemExit(str(error)) from None
