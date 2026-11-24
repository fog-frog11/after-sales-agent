"""在下载语料的留出问法上比较规划器；保留原行号和独立项目标签。"""

import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import perf_counter

from app.mall.agent import MallAgent, deepseek_mall_planner
from app.mall.consultation import is_direct_question, reported_reason
from app.settings import ROOT, local_setting
from scripts.prepare_after_sales_corpus import DIRECTORY, normalize


def load_review(*, final: bool = False) -> tuple[dict, list[dict]]:
    """检查源文件和分组独立性，重复问句只保留一个留出题。"""
    review = json.loads((ROOT / "scripts/fixtures/mall_qingshan_review.json").read_text("utf-8"))
    content = (DIRECTORY / "data.jsonl").read_bytes()
    if hashlib.sha256(content).hexdigest() != review["sha256"]:
        raise ValueError("评测所用数据版本已变化")
    rows = [json.loads(line) for line in content.decode("utf-8-sig").splitlines()]
    example_keys = {normalize(rows[row["line"] - 1]["user"]) for row in review["examples"]}
    if final:
        example_keys.update(normalize(rows[row["line"] - 1]["user"]) for row in review["holdout"])
    seen = set()
    cases = []
    for case in review["final_holdout" if final else "holdout"]:
        question = rows[case["line"] - 1]["user"]
        key = normalize(question)
        if key in example_keys:
            raise ValueError("示例问句及其重复版本进入了留出集")
        if key not in seen:
            cases.append({**case, "question": question})
            seen.add(key)
    return review, cases


def main() -> None:
    """真实模型只读取用户问句，源 bot 回复不参与推理或评分。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--with-examples", action="store_true")
    parser.add_argument("--final", action="store_true")
    parser.add_argument("--semantic", action="store_true", help="每题用新版语义规划和原话事实提取")
    args = parser.parse_args()
    key = local_setting("DEEPSEEK_API_KEY")
    if not key:
        parser.error("缺少 DeepSeek 配置")
    review, cases = load_review(final=args.final)
    agent = MallAgent(deepseek_mall_planner(key, use_examples=args.with_examples, semantic=args.semantic))

    def score(case: dict) -> dict:
        start = perf_counter()
        plan = agent.select_queries({"question": case["question"], "use_model": args.semantic or not is_direct_question(case["question"]),
                                     "semantic":args.semantic})
        topics = set(plan["topics"])
        reason = (plan.get('observation') or {}).get('reason') if args.semantic else reported_reason(case["question"])
        expected_reason = case.get("reason")
        passed = (set(case["required"]) <= topics and "out_of_scope" not in topics
                  and (not expected_reason or reason == expected_reason or "confirm_" + expected_reason in topics))
        return {**case, **plan, "passed": passed, "ms": round((perf_counter() - start) * 1000)}

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(score, cases))
    report = {"dataset": review["dataset"], "sha256": review["sha256"], "with_examples": args.with_examples,
              "split": "final" if args.final else "development", "semantic":args.semantic,
              "total": len(results), "passed": sum(row["passed"] for row in results),
              "fallbacks": sum(row["route"] == "fallback" for row in results), "rows": results}
    with args.out.open("x", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
    print(json.dumps({key: value for key, value in report.items() if key != "rows"}, ensure_ascii=False))
    print(json.dumps([row for row in results if not row["passed"]], ensure_ascii=False))


if __name__ == "__main__":
    main()
