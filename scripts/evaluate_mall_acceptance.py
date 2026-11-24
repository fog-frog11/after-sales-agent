"""用单独维护的中文问法检查生产路由，统计误选、漏选、回退和延迟。"""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from math import ceil
from time import perf_counter

from app.mall.agent import MallAgent, TOPICS, deepseek_mall_planner
from app.mall.consultation import is_direct_question
from app.settings import ROOT, local_setting


def main():
    """同一批问题可分别运行离线和真实模型，严格按查询集合计分。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--holdout", action="store_true", help="读取单独保留的一组题，含复合诉求")
    parser.add_argument("--audit", action="store_true", help="读取最终验收题")
    args = parser.parse_args()
    key = local_setting("DEEPSEEK_API_KEY") if args.live else None
    if args.live and not key:
        parser.error("缺少模型配置")
    agent = MallAgent(deepseek_mall_planner(key) if key else None)
    name = "mall_release_audit" if args.audit else "mall_release_holdout" if args.holdout else "mall_acceptance_questions"
    groups = json.loads((ROOT / f"scripts/fixtures/{name}.json").read_text("utf-8"))
    cases = [(question, topic) for topic, questions in groups.items() for question in questions]
    if len(set(q for q, _ in cases)) != len(cases) or any(set(g.split("+")) - TOPICS.keys() for g in groups):
        raise ValueError("题目重复或标签无效")

    def score(case):
        """执行与买家页面相同的规划决策，保留每题原始输出。"""
        question, expected = case
        start = perf_counter()
        plan = agent.select_queries({"question": question, "use_model": not is_direct_question(question)})
        return dict(question=question, expected=expected, **plan,
                    passed=set(plan["topics"]) == set(expected.split("+")), ms=round((perf_counter()-start)*1000))

    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = list(pool.map(score, cases))
    summary = dict(total=len(rows), exact=sum(r["passed"] for r in rows),
                   fallback=sum(r["route"] == "fallback" for r in rows),
                   p95_ms=sorted(r["ms"] for r in rows)[ceil(len(rows)*.95)-1])
    print(json.dumps(dict(summary=summary, rows=rows), ensure_ascii=False))
    return 0 if summary["exact"] >= .95 * len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
