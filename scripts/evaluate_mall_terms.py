"""以商城的店铺和下单日期为范围，对照四种检索策略并保留逐题结果。"""

import argparse
import json
import math
import sys
from datetime import date
from pathlib import Path
from statistics import mean
from time import perf_counter

from app.policies.evidence import PolicySource, split_aspects
from app.policies.mall_rank import BM25
from app.policies.retrieval_ranking import article_text, reciprocal_rank_fusion
from app.policies.semantic_terms import E5Encoder, SemanticTerms


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).with_name("fixtures")
LIMIT = 12
TOP = 3
LEXICAL_STRATEGIES = ("bm25_whole", "bm25_aspects")
SEMANTIC_STRATEGIES = ("hybrid_aspects", "hybrid_window_rerank")


def load_corpus() -> tuple[dict, dict]:
    """把平台共用章和店铺章组装成可追溯的购买版本。"""
    catalog = json.loads((FIXTURES / "mall_terms_catalog.json").read_text(encoding="utf-8"))
    editions = {version: date.fromisoformat(when) for version, when in catalog["editions"].items()}
    scopes = {}
    for shop, book in catalog["shops"].items():
        for version in editions:
            articles = catalog["shared"] | book["articles"] | book.get(version, {})
            scopes[(shop, version)] = [
                PolicySource(f"{shop}/{version}/{key}", title, f"{book['name']} · {version}", text)
                for key, (title, text) in articles.items()
            ]
    return editions, scopes


def score(question: str, lexical: BM25, semantic: SemanticTerms | None) -> dict:
    """共享分问、候选上限和每问引用预算，比较融合与重排的增量。"""
    start = perf_counter()
    whole = [lexical.rank(question)]
    paths = {"bm25_whole": (whole, perf_counter() - start)}
    queries = split_aspects(question) or [question]
    start = perf_counter()
    words = [lexical.rank(part) for part in queries]
    lexical_time = perf_counter() - start
    paths["bm25_aspects"] = (words, lexical_time)
    if semantic is not None:
        start = perf_counter()
        vectors = [semantic.query_vector(part) for part in queries]
        fused = [reciprocal_rank_fusion(sparse, semantic.dense_ranking(vector, LIMIT))[:LIMIT]
                 for sparse, vector in zip(words, vectors, strict=True)]
        hybrid_time = lexical_time + perf_counter() - start
        paths["hybrid_aspects"] = (fused, hybrid_time)
        start = perf_counter()
        ranked = [reciprocal_rank_fusion(head, semantic.window_ranking(vector, head))
                  for vector, head in zip(vectors, fused, strict=True)]
        paths["hybrid_window_rerank"] = (ranked, hybrid_time + perf_counter() - start)
    return paths


def evaluate(encoder: E5Encoder | None, *, expand: bool = False) -> dict:
    """保留每个范围、候选顺序、漏召回与漏排的逐题记录。"""
    editions, scopes = load_corpus()
    lex = {scope: BM25(articles, LIMIT, expand=expand) for scope, articles in scopes.items()}
    start = perf_counter()
    dense = {scope: SemanticTerms(encoder, articles, window_size=65, window_overlap=20)
             for scope, articles in scopes.items()} if encoder is not None else {}
    index_ms = round((perf_counter() - start) * 1000)
    rows, seen = [], set()
    fixture = json.loads((FIXTURES / "mall_terms_questions.json").read_text(encoding="utf-8"))
    strategies = LEXICAL_STRATEGIES + (SEMANTIC_STRATEGIES if encoder is not None else ())
    for case_id, shop, bought, question, labels in fixture["cases"]:
        purchase = date.fromisoformat(bought)
        version = max((key for key, when in editions.items() if when <= purchase), key=editions.__getitem__, default=None)
        scope = (shop, version)
        allowed = {article.article_id for article in scopes.get(scope, [])}
        expected = [[f"{shop}/{version}/{label}" for label in alternatives] for alternatives in labels]
        if case_id in seen or not labels or any(set(group) - allowed for group in expected):
            raise ValueError(f"题目重复或标签不属于下单范围：{case_id}")
        version_check = fixture["version_checks"].get(case_id)
        if version_check is not None:
            expected_version, phrase = version_check
            gold_ids = {key for group in expected for key in group}
            if version != expected_version or (phrase and not any(
                article.article_id in gold_ids and phrase in article.text for article in scopes.get(scope, [])
            )):
                raise ValueError(f"下单版本或条款原文不符合独立标注：{case_id}")
        seen.add(case_id)
        paths = score(question, lex[scope], dense.get(scope)) if version is not None else {
            name: ([], 0.0) for name in strategies}
        row = {"id": case_id, "shop": shop, "purchased_on": bought, "edition": version,
               "question": question, "expected": expected, "candidate_count": len(allowed),
               "version_check": version_check, "strategies": {}}
        for strategy, (probes, elapsed) in paths.items():
            hits = {key for ranking in probes for key in ranking[:TOP]}
            union = {key for ranking in probes for key in ranking}
            groups = [set(group) for group in expected if group]
            ranks = [min((i for ranking in probes for i, key in enumerate(ranking, 1) if key in group), default=0)
                     for group in groups]
            row["strategies"][strategy] = {
                "rankings": probes, "missing_top3": [sorted(group) for group in groups if not group & hits],
                "missing_candidates": [sorted(group) for group in groups if not group & union],
                "ranks": ranks, "out_of_scope": sorted(union - allowed),
                "unsupported_has_candidates": bool(not groups and hits),
                "ms": round(elapsed * 1000, 3),
            }
        rows.append(row)
    metrics = {}
    for strategy in strategies:
        runs = [row["strategies"][strategy] for row in rows]
        with_gold = [run for run in runs if run["ranks"]]
        ranks = [rank for run in with_gold for rank in run["ranks"]]
        elapsed = sorted(run["ms"] for run in runs)
        metrics[strategy] = {
            "gold_groups_in_top3": sum(0 < rank <= TOP for rank in ranks), "gold_groups": len(ranks),
            "all_evidence_in_top3": sum(not run["missing_top3"] for run in with_gold),
            "supported_questions": len(with_gold),
            "mrr": round(mean(1 / rank if rank else 0 for rank in ranks), 4),
            "out_of_scope_questions": sum(bool(run["out_of_scope"]) for run in runs),
            "unsupported_with_candidates": sum(run["unsupported_has_candidates"] for run in runs),
            "version_questions_with_gold_in_top3": sum(
                bool(row["version_check"] and row["edition"]
                     and not row["strategies"][strategy]["missing_top3"]) for row in rows
            ),
            "p95_ms": elapsed[math.ceil(len(elapsed) * 0.95) - 1],
        }
    all_articles = [article for book in scopes.values() for article in book]
    return {
        "questions": len(rows), "editions": len(scopes), "clauses_per_edition": sorted({len(book) for book in scopes.values()}),
        "indexed_snapshots": len(all_articles), "distinct_texts": len({article_text(a) for a in all_articles}),
        "dense_index_ms": index_ms, "metrics": metrics, "rows": rows,
    }


def main() -> None:
    """默认用本机 E5 做完整对照，可以只跑需要标准 Python 的词法基线。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lexical-only", action="store_true")
    parser.add_argument("--expand", action="store_true", help="启用商城五金缺件扩词")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    encoder = None
    if not args.lexical_only:
        sys.path.append(str(ROOT / ".local/retrieval-deps"))
        encoder = E5Encoder(ROOT / ".local/models/multilingual-e5-small")
    report = evaluate(encoder, expand=args.expand)
    if args.out:
        with args.out.open("x", encoding="utf-8") as output:
            json.dump(report, output, ensure_ascii=False, indent=2)
    print(json.dumps({key: value for key, value in report.items() if key != "rows"}, ensure_ascii=False, indent=2))
    if any(result["out_of_scope_questions"] for result in report["metrics"].values()):
        raise SystemExit("发现越界候选")


if __name__ == "__main__":
    main()
