"""复跑商城意图评测，逐题记录查询计划和实际耗时。"""

import argparse
import json
from pathlib import Path
from time import perf_counter

from app.mall.agent import MallAgent, deepseek_mall_planner
from app.mall.consultation import is_direct_question
from app.settings import ROOT, local_setting


def main() -> None:
    """读取独立标注，分别测规则模式和配置模型后的模式。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--out", type=Path)
    options = parser.parse_args()
    key = local_setting("DEEPSEEK_API_KEY") if options.live else None
    if options.live and not key:
        parser.error("真实模型评测需要 DEEPSEEK_API_KEY。")
    agent = MallAgent(deepseek_mall_planner(key) if key else None)
    source = ROOT / "scripts/fixtures/mall_agent_intents.json"
    rows = []
    for case in json.loads(source.read_text(encoding="utf-8")):
        question = case["question"]
        start = perf_counter()
        plan = agent.select_queries({"question": question, "use_model": not is_direct_question(question)})
        topics = set(plan["topics"])
        passed = (set(case["required"]) <= topics and not (set(case["forbidden"]) & topics)
                  and all(plan.get("candidate_filter", {}).get(key) == value
                          for key, value in case.get("filters", {}).items()))
        rows.append({**case, **plan, "passed": passed, "ms": round((perf_counter()-start) * 1000)})
    report = {"mode": "live" if options.live else "rules", "passed": sum(row["passed"] for row in rows),
              "total": len(rows), "model_calls": sum(row["route"] != "rules" for row in rows),
              "fallbacks": sum(row["route"] == "fallback" for row in rows), "rows": rows}
    if options.out:
        with options.out.open("x", encoding="utf-8") as output:
            json.dump(report, output, ensure_ascii=False, indent=2)
    print(json.dumps({**report, "rows": [row for row in rows if not row["passed"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
