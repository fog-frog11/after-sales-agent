"""从买家的连续操作验收咨询图；每个场景单独开始，然后复用真实返回的会话状态。"""

import unittest
from datetime import date

from app.mall.agent import MallAgent
from app.mall.consultation import answer, question_topics
from test_mall_agent import run_turn, shipped_product


class ConversationAcceptance(unittest.TestCase):
    """核对连续追问、凭证缺失、撤回申请建议与商品隔离。"""

    def test_size_preference_suggests_exchange_with_order_rules(self):
        cases = ("这个围巾偏小", "这件衣服穿着太紧了", "可以换大一码吗？")
        for question in cases:
            with self.subTest(question=question):
                result, _ = run_turn(question, item=shipped_product(title="围巾"))
                self.assertEqual(result["reason"], "wrong_purchase")
                self.assertEqual(result["recommended_kind"], "exchange")
                self.assertIn("这单可以申请换货", result["answer"])
                self.assertEqual(result["suggested_description"], question)
        expired, _ = run_turn("这个围巾偏小", item=shipped_product(return_days=1))
        self.assertIn("截止", expired["answer"])
        self.assertFalse(expired["kinds"])
        self.assertIsNone(expired.get("recommended_kind"))

    def test_size_discrepancy_and_explicit_return_are_distinct(self):
        different, _ = run_turn("实际长度比页面标注的短了", topics=["fit_exchange"])
        self.assertIsNone(different["reason"])
        self.assertIsNone(different.get("recommended_kind"))
        returned, _ = run_turn("这件偏小，我想退货退款", topics=["fit_exchange", "eligibility"])
        self.assertEqual(returned["reason"], "wrong_purchase")
        self.assertIn("return_refund", returned["kinds"])
        self.assertIsNone(returned.get("recommended_kind"))

    def test_exchange_follow_up_keeps_requested_size_without_changing_facts(self):
        item = shipped_product(title="围巾")
        _, context = run_turn("这个围巾偏小", item=item)
        result, _ = run_turn("那就大一码", context, item=item, topics=["out_of_scope"])
        self.assertEqual(result["recommended_kind"], "exchange")
        self.assertIn("这个围巾偏小", result["suggested_description"])
        self.assertIn("那就大一码", result["suggested_description"])
        result, _ = run_turn("退款金额偏小", topics=["fit_exchange", "refund"])
        self.assertNotIn("recommended_kind", result)

    def test_clarification_carries_buyer_report_and_does_not_loop(self):
        first_report = "这个玻璃保鲜盒打开后碎了"
        question = {"fact": "damage_timing", "question": "你是刚拆包就发现玻璃碎了，还是使用后才碎的？"}
        first, context = run_turn(first_report, topics=["eligibility", "confirm_quality"], follow_up=question)
        self.assertEqual(first["answer_source"], "model")
        self.assertEqual(first["answer"], question["question"])
        revised, context = run_turn("不是这个原因", context, follow_up=question)
        self.assertNotEqual(revised["answer"], first["answer"])
        self.assertEqual(context["issue_report"], first_report)
        second_report = "买来的时候里面盒子打开已经碎了"
        second, context = run_turn(second_report, context, topics=["eligibility", "confirm_quality"], follow_up=question)
        self.assertIn("拆包时就发现破损", second["answer"])
        self.assertNotIn("质量问题", second["answer"])
        self.assertIsNone(second["reason"])
        result, _ = run_turn("这件还可以退吗？", context)
        self.assertEqual(result["reason"], "quality")
        self.assertIn("return_refund", result["kinds"])
        self.assertEqual(result["suggested_description"], second_report)

    def test_rejected_reasons_and_unsafe_clarifications_are_not_replayed(self):
        _, ctx = run_turn("到手跟我想的根本不一样", topics=["eligibility", "confirm_wrong_purchase"])
        _, ctx = run_turn("不是这个原因", ctx)
        self.assertEqual(ctx["rejected_reasons"], ["wrong_purchase"])
        invalid = {"fact": "expected_vs_actual", "question": "我保证已退款，给我订单号好吗？"}
        result, ctx = run_turn("拿到的款跟页面完全不一样", ctx,
                               topics=["eligibility", "confirm_wrong_purchase"], follow_up=invalid)
        self.assertNotIn("answer_source", result)
        self.assertNotIn("已退款", result["answer"])
        self.assertNotIn("proposed_reason", ctx)
        other = {"fact": "affected_part", "question": "你收到的是颜色不同，还是商品型号也不一样？"}
        result, _ = run_turn("完全不是那个样子", ctx, topics=["eligibility"], follow_up=other)
        self.assertEqual(result["answer"], other["question"])

    def test_user_supplied_facts_take_priority_over_model_clarification(self):
        follow_up = {"fact": "affected_part", "question": "你具体遇到的是哪部分不合适？"}
        for message, expected in (
            ("仓库少发了挂钩", "missing"),
            ("商家发错型号了", "wrong_item"),
            ("插上电之后完全不通电", "quality"),
        ):
            with self.subTest(message=message):
                result, _ = run_turn(message, topics=["eligibility"], follow_up=follow_up)
                self.assertEqual(result["reason"], expected)
                self.assertTrue(result["kinds"])

    def test_new_product_experience_replaces_missing_item_suggestion(self):
        item = shipped_product(title="手持梳毛刷")
        _, context = run_turn("打开是空盒，并且快递包装也压坏了", item=item)
        _, context = run_turn("这件商品的物流到哪了？", context, item=item)
        result, context = run_turn("这个毛刷用着效果不太好", context, item=item, topics=["eligibility"])
        self.assertIn("效果没达到你的预期", result["answer"])
        self.assertNotIn("补发", result["answer"])
        self.assertIsNone(result["reason"])
        self.assertTrue(result["reset_suggestion"])
        result, _ = run_turn("这件还可以退吗？", context, item=item)
        self.assertEqual(result["reason"], "wrong_purchase")
        self.assertEqual(result["suggested_description"], "这个毛刷用着效果不太好")

    def test_off_topic_does_not_consume_the_service_draft(self):
        _, context = run_turn("打开是空盒")
        result, saved = run_turn("给我讲个笑话", context, topics=["out_of_scope"])
        self.assertIn("订单售后", result["answer"])
        self.assertIsNone(result["reason"])
        self.assertEqual(result["kinds"], {})
        self.assertEqual(saved, context)
        result, _ = run_turn("那我这个可以申请售后吧？", saved)
        self.assertEqual(result["reason"], "missing")

    def test_evidence_then_decision(self):
        """连续三轮检查事实、可选操作和草稿，不以回答中出现一个关键词代替核验。"""
        agent = MallAgent()
        scenarios = (
            ("打开是空盒，外箱还有个洞", "开箱录像我没有", "missing"),
            ("收到的是空箱", "拆箱的时候忘了录", "missing"),
            ("少了一根充电线", "视频没留，我没拍照片", "missing"),
            ("箱子里什么都没有", "拆箱视频找不到", "missing"),
            ("商家发错颜色了", "拆箱时没有录视频", "wrong_item"),
            ("发错商品了", "开箱视频丢了", "wrong_item"),
            ("发错颜色了", "我没留照片", "wrong_item"),
            ("鼠标坏了", "没拍视频", "quality"),
            ("开不了机", "开箱的视频没有", "quality"),
            ("商品坏了，外箱压坏了", "开箱的时候忘了拍", "quality"),
        )
        for report, evidence, reason in scenarios:
            with self.subTest(report=report):
                item = shipped_product()
                ctx = {}
                replies = []
                for question in (report, evidence, "那这样还能申请吗？"):
                    def respond(topics, route):
                        nonlocal ctx
                        result, ctx = run_turn(question, ctx, item=item, topics=topics)
                        return result
                    replies.append(agent.run(question, False, respond))
                first, second, third = replies
                self.assertEqual(first["reason"], reason)
                self.assertEqual(second.get("response_kind"), "evidence_guidance")
                self.assertNotIn("哪个商品", second["answer"])
                self.assertEqual(third["reason"], reason)
                self.assertEqual(third["suggested_description"], first["suggested_description"])
                self.assertTrue(third["kinds"])

    def test_interleaved_conversations_and_correction(self):
        """两件商品交替咨询，一件撤回缺件事实后，另一件继续按原问题处理。"""
        _, a = run_turn("少了一根充电线")
        _, b = run_turn("发错颜色了")
        cleared, a = run_turn("找到了，没有少", a)
        evidence, b = run_turn("开箱视频我没录", b)
        self.assertTrue(cleared["reset_suggestion"])
        self.assertFalse(a.get("reason"))
        self.assertEqual(b["reason"], "wrong_item")
        self.assertEqual(evidence["response_kind"], "evidence_guidance")
        _, a = run_turn("现在开不了机", a)
        self.assertEqual(a["reason"], "quality")
    def test_cushion_complaint_platform_detour_and_return(self):
        """先反馈体验，再问人工，最后核对退货资格。"""
        complaint = "我这个订单的坐垫坐着不舒服哎"
        item = shipped_product(title="折叠坐垫")
        first, context = run_turn(complaint, item=item, topics=["product"])
        self.assertIn("坐着不舒服", first["answer"])
        self.assertIsNone(first["reason"])
        platform, context = run_turn("申请人工介入", context, item=item)
        self.assertTrue(platform["intervention"])
        result, _ = run_turn("我觉得不舒服 能退货吗？", context, item=item,
                             topics=["return_window", "eligibility"])
        self.assertEqual(result["reason"], "wrong_purchase")
        self.assertIn("return_refund", result["kinds"])
        self.assertEqual(result["suggested_description"], complaint)

    def test_preference_uses_order_terms_and_defect_overrides_it(self):
        """核对购买时的条款，买家改口陈述故障时更换申请原因。"""
        item = shipped_product(title="鞋子")
        options, context = run_turn("这鞋穿着磨脚，能退吗？", item=item)
        self.assertTrue(options["kinds"])
        defect, _ = run_turn("说错了，鞋底其实已经裂了", context, item=item)
        self.assertEqual(defect["reason"], "quality")
        for blocked in (shipped_product(change_of_mind=False),
                        shipped_product(return_days=1)):
            result, _ = run_turn("这鞋穿着磨脚，能退吗？", item=blocked)
            self.assertEqual(result["reason"], "wrong_purchase")
            self.assertFalse(result["kinds"])
            self.assertIsNone(result["suggested_description"])

    def test_does_not_record_hypothetical_or_rejected_cause(self):
        """假设、否定和未确认的模型建议都不能变成售后事实。"""
        first, context = run_turn("假如这鞋穿着磨脚，能退吗？")
        self.assertIsNone(first["reason"])
        _, context = run_turn("这东西使起来有点别扭", context,
                              topics=["eligibility", "confirm_wrong_purchase"])
        _, context = run_turn("不是这个原因", context)
        result, _ = run_turn("是的", context)
        self.assertIsNone(result["reason"])

    def test_existing_case_remains_the_action_for_preference(self):
        """查询退货时如已有申请，应引导到原单。"""
        item = shipped_product(title="折叠坐垫")
        case = {"case_no": 42, "status_label": "待店铺响应"}
        question = "这个坐垫坐着不舒服能退吗"
        result, _ = answer(item, question, None, {}, case, date(2026, 11, 19),
                           active_case=case, topics=question_topics(question))
        self.assertEqual(result["case_no"], 42)
        self.assertEqual(result["kinds"], {})

    def test_unconfirmed_suggestion_expires_after_a_different_request(self):
        """确认只对应上一个问题，人工介入查询不能替用户确认原因。"""
        _, pending = run_turn("使起来总觉得别扭", topics=["eligibility", "confirm_wrong_purchase"])
        confirmed, _ = run_turn("对", pending)
        self.assertIn("return_refund", confirmed["kinds"])
        _, after_platform = run_turn("申请人工介入", pending)
        unrelated, _ = run_turn("对", after_platform)
        self.assertIsNone(unrelated["reason"])

    def test_preference_is_product_scoped_and_retractable(self):
        """撤回体验问题后不能沿用原理由，另一个商品从空状态开始。"""
        _, first_item = run_turn("这东西用着不舒服")
        _, first_item = run_turn("能退吗？", first_item)
        self.assertEqual(first_item["reason"], "wrong_purchase")
        other, _ = run_turn("这件还可以退吗？", {})
        self.assertIsNone(other["reason"])
        corrected, first_item = run_turn("现在坐着很舒服", first_item)
        self.assertTrue(corrected["reset_suggestion"])
        repeated, _ = run_turn("这件还可以退吗？", first_item)
        self.assertIsNone(repeated["reason"])

