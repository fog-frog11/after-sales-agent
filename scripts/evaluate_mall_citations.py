"""使用独立问题验收候选内容是否足以引用，记录误引与漏引。"""

import argparse
import json
import sys
from pathlib import Path

from app.policies.mall_citations import find_citation
from app.policies.mall_rank import BM25
from app.policies.semantic_terms import E5Encoder, SemanticTerms
from scripts.evaluate_mall_terms import FIXTURES, ROOT, load_corpus, score


def evaluate(encoder: E5Encoder, dataset: Path, *, expand: bool = False) -> dict:
    """对比排序第一条直接引用与通过内容校验后再引用。"""
    _, scopes = load_corpus()
    lex = {scope: BM25(articles, expand=expand) for scope, articles in scopes.items()}
    dense = {scope: SemanticTerms(encoder, articles) for scope, articles in scopes.items()}
    rows = []
    labels = json.loads(dataset.read_text(encoding="utf-8"))
    for key, shop, edition, question, expected in labels:
        scope = (shop, edition)
        valid = {article.article_id: article for article in scopes[scope]}
        gold = [f"{shop}/{edition}/{code}" for code in expected or []]
        if not set(gold) <= valid.keys():
            raise ValueError(f"标注违反店铺和版本范围：{key}")
        rankings, _ = score(question, lex[scope], dense[scope])["hybrid_aspects"]
        candidates = list(dict.fromkeys(ref for ranking in rankings for ref in ranking))
        label, article = find_citation(question, [valid[ref] for ref in candidates])
        lexical_ids = lex[scope].rank(question)
        _, lexical_citation = find_citation(question, [valid[ref] for ref in lexical_ids])
        rows.append({"id": key, "question": question, "shop": shop, "edition": edition, "gold": gold,
                     "expected_status": "clarify" if expected is None else "citation" if expected else "no_evidence",
                     "top_ranked": candidates[0] if candidates else None, "candidates": candidates,
                     "lexical_citation": lexical_citation.article_id if lexical_citation else None,
                     "lexical_candidates": lexical_ids,
                     "chosen": article.article_id if article else None,
                     "status": "citation" if article else "clarify" if label == "clarify" else "no_evidence"})
    positive = [row for row in rows if row["gold"]]
    negative = [row for row in rows if row["expected_status"] == "no_evidence"]
    return {"questions": len(rows), "supported": len(positive), "unsupported": len(negative),
            "baseline_wrong_unsupported": sum(bool(row["top_ranked"]) for row in negative),
            "guard_wrong_unsupported": sum(bool(row["chosen"]) for row in negative),
            "baseline_correct_supported": sum(row["top_ranked"] in row["gold"] for row in positive),
            "guard_correct_supported": sum(row["chosen"] in row["gold"] for row in positive),
            "lexical_correct_supported": sum(row["lexical_citation"] in row["gold"] for row in positive),
            "lexical_wrong_supported": sum(bool(row["lexical_citation"] and row["lexical_citation"] not in row["gold"]) for row in positive),
            "lexical_wrong_unsupported": sum(bool(row["lexical_citation"]) for row in negative),
            "hybrid_only_correct": [row["id"] for row in positive
                                    if row["chosen"] in row["gold"] and row["lexical_citation"] not in row["gold"]],
            "guard_wrong_supported": sum(bool(row["chosen"] and row["chosen"] not in row["gold"]) for row in positive),
            "guard_clarifications": sum(row["status"] == "clarify" and row["expected_status"] == "clarify" for row in rows),
            "rows": rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--dataset", type=Path, default=FIXTURES / "mall_citation_holdout.json")
    parser.add_argument("--expand", action="store_true")
    args = parser.parse_args()
    sys.path.append(str(ROOT / ".local/retrieval-deps"))
    report = evaluate(E5Encoder(ROOT / ".local/models/multilingual-e5-small"), args.dataset, expand=args.expand)
    if args.out:
        with args.out.open("x", encoding="utf-8") as output:
            json.dump(report, output, ensure_ascii=False, indent=2)
    print(json.dumps({k:v for k,v in report.items() if k != "rows"}, ensure_ascii=False, indent=2))
