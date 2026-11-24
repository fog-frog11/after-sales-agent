"""用虚构买家问法实测咨询图。加 --live 使用项目配置的 DeepSeek，输出每轮回答、草稿和耗时。"""

import argparse
import json
import sys
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.mall.agent import MallAgent, deepseek_mall_planner
from app.mall.consultation import is_direct_question
from app.settings import local_setting
from test_mall_agent import run_turn, shipped_product


def main():
    """在内存中沿用每组对话的上下文，使用签收商品，检查明确改口后的草稿。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    live = parser.parse_args().live
    key = local_setting("DEEPSEEK_API_KEY") if live else None
    if live and not key:
        raise SystemExit("缺少 DeepSeek 配置")
    graph = MallAgent(deepseek_mall_planner(key) if key else None)
    item = shipped_product(title="无线鼠标", variant="岩灰 · 2.4G")
    scenarios = [
        [("鼠标线没给我，盒子也瘪了", "missing"), ("刚翻到了，鼠标倒是开不了机", "quality")],
        [("少了一根充电线", "missing"), ("线找到了，只是外盒破了，照片要拍哪些？", None)],
        [("少了一根充电线", "missing"), ("那我这个可以申请售后吧？", "missing")],
    ]
    failures = []
    routes = []
    for turns in scenarios:
        context = {}
        for question, expected in turns:
            def respond(topics, route):
                """复用真实答复逻辑，传递上一轮事实。"""
                nonlocal context
                result, context = run_turn(question, context, item=item, topics=topics)
                return {**result, "route": route}

            start = perf_counter()
            result = graph.run(question, live and not is_direct_question(question), respond)
            routes.append(result["route"])
            print(json.dumps({"question": question, "route": result["route"],
                "ms": round((perf_counter()-start)*1000), "answer": result["answer"],
                "reason": result["reason"], "draft": result.get("suggested_description"),
                "reset": result.get("reset_suggestion")}, ensure_ascii=False))
            if result["reason"] != expected:
                failures.append(question)
    if failures or (live and ("function_call" not in routes or "fallback" in routes)):
        raise SystemExit(f"未通过：原因不符 {failures}，实际路由 {routes}")


if __name__ == "__main__":
    main()
