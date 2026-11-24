"""用真实查询规划器验证多轮使用体验、资格和表单。"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.mall.agent import MallAgent, deepseek_mall_planner
from app.mall.consultation import is_direct_question
from app.settings import local_setting
from test_mall_agent import run_turn, shipped_product


# 对每轮预先标注表单原因、可办方式与必须出现的答复。每组使用独立商品上下文。
SCENARIOS = (
    ("坐垫", {}, (("这垫子靠着有点硌人", None, None, "硌人"),
                  ("那这件还能退吗？", "wrong_purchase", "return_refund", "退换期限"))),
    ("床垫", {}, (("躺上去太硬了，我能退吗？", "wrong_purchase", "return_refund", "退换期限"),)),
    ("耳机", {}, (("耳机戴上夹耳", None, None, "夹耳"),
                  ("我想找平台客服", None, None, "还没有售后单"),
                  ("所以还能退货不？", "wrong_purchase", "return_refund", "退换期限"))),
    ("皮鞋", {"return_days": 1}, (("穿着磨脚，想退掉", "wrong_purchase", None, "截止"),)),
    ("坐垫", {"change_of_mind": False}, (("这件坐上去不舒服能退吗", "wrong_purchase", None, "未承诺无理由"),)),
    ("背包", {}, (("背着很不舒服", None, None, "不舒服"),
                  ("看错了，肩带已经裂了", "quality", "return_refund", "质量问题"))),
    ("耳机", {}, (("假如戴起来不舒服，能退货吗", None, None, ""),)),
    ("鼠标", {}, (("手搁上去老觉得别扭", None, None, "不合适"),
                  ("是的", "wrong_purchase", "return_refund", "退换期限"))),
    ("眼镜", {}, (("这个夹得我鼻梁难受", None, None, "不合适"),
                  ("对", "wrong_purchase", "return_refund", "退换期限"))),
    ("坐垫", {}, (("我买的是哪个规格", None, None, "380mL"),)),
    ("坐垫", {}, (("坐着不舒服", None, None, "不舒服"),
                  ("刚才说错了，现在坐着很舒服", None, None, ""),
                  ("能不能退？", None, None, ""))),
    ("鞋子", {}, (("穿着磨脚，退货运费谁出？", "wrong_purchase", "return_refund", "自付"),)),
)


def main():
    """执行与页面一致的意图规划和回复生成，输出逐轮与整组成绩。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    key = local_setting("DEEPSEEK_API_KEY") if args.live else None
    if args.live and not key:
        parser.error("缺少 DeepSeek 配置")
    agent = MallAgent(deepseek_mall_planner(key) if key else None)
    rows = []
    for index, (title, item_fields, turns) in enumerate(SCENARIOS, 1):
        context = {}
        item = shipped_product(title=title, **item_fields)
        for question, reason, kind, text in turns:
            def respond(topics, route):
                nonlocal context
                result, context = run_turn(question, context, item=item, topics=topics)
                return {**result, "route": route}

            result = agent.run(question, not is_direct_question(question), respond)
            passed = (result["reason"] == reason and (kind in result["kinds"] if kind else not result["kinds"])
                      and text in result["answer"] and result["route"] != "fallback")
            rows.append(dict(scenario=index, question=question, passed=passed, **result))
    failures = [row for row in rows if not row["passed"]]
    print(json.dumps({"scenarios": len(SCENARIOS), "turns": len(rows), "passed": len(rows) - len(failures),
                      "failed": failures, "rows": rows}, ensure_ascii=False, default=str))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
