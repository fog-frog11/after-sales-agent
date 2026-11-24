"""检验条款层级、模型待确认建议和连续短问的业务边界。"""

import unittest
from unittest.mock import patch
from datetime import date
from decimal import Decimal

from app.policies.mall_preview import compare_policy_preview
from app.storage.mall_policies import ShopPublication, validate_commitments
from app.mall.consultation import answer, is_direct_question
from app.mall.dialogue import last_subjects
from app.mall.rules import policy_lines
from app.tickets.service_requests import ServiceError
from test_mall_agent import run_turn, shipped_product


class ShopPolicyTests(unittest.TestCase):
    """店铺优惠可优于底线，各项承诺也必须相互兼容。"""

    def test_floor_limits(self):
        floor = dict(return_days=7, fault_days=30, warranty_days=30, reply_hours=48, max_postage=Decimal("12"))
        fields = dict(base_version=1, effective_on=date(2026, 11, 19), return_days=15, fault_days=45,
                      warranty_days=90, reply_hours=16, return_postage=9, item_credit=4,
                      seller_advances=False, guidance_key="category")
        validate_commitments(ShopPublication(**fields), floor)
        for patch in [dict(return_days=6), dict(fault_days=29), dict(warranty_days=20), dict(reply_hours=49),
                      dict(return_postage=13), dict(item_credit=10), dict(return_days=50), dict(warranty_days=35)]:
            with self.subTest(patch=patch), self.assertRaises(ServiceError):
                validate_commitments(ShopPublication(**(fields | patch)), floor)

    def test_order_bound_text(self):
        item = shipped_product()
        self.assertEqual(len(policy_lines(item)), 4)
        new = dict(item, platform_version=1, item_credit=4, guidance="寄回前可以给配件拍照。")
        self.assertEqual(len(policy_lines(new)), 6)
        self.assertIn(new["guidance"], policy_lines(new))

    def test_preview_returns_lexical_evidence_when_local_model_is_unavailable(self):
        """可选依赖故障时仍返回与买家同一路径的本单依据。"""
        item = shipped_product(shop_id="market-001", sku="SAMPLE-001")
        with patch("app.policies.mall_preview.preview_index", side_effect=OSError("missing")) as model:
            with self.assertLogs("app.policies.mall_preview", level="WARNING"):
                result = compare_policy_preview(item, "退货运费谁承担？", True)
            model.assert_called_once()
        self.assertEqual(result["hybrid_status"], "unavailable")
        self.assertEqual([article["id"] for article in result["lexical"]["evidence"]], ["freight"])
        self.assertNotIn("hybrid", result)


class ShortConversationTests(unittest.TestCase):
    def test_buyer_incident_self_checks(self):
        """从买家视角检查事实、澄清、假设和表单是否一致。"""
        for question, reason, expected, draft in (
            ("打开是空盒，并且快递包装也压坏了", "missing", "少件漏发", "快递包装也压坏了"),
            ("收到个空包裹", "missing", "少件漏发", "空包裹"),
            ("箱子里啥也没有", "missing", "少件漏发", "啥也没有"),
            ("商家发错颜色了", "wrong_item", "发错商品", "发错颜色"),
            ("鼠标坏了，外盒压坏了", "quality", "质量问题", "鼠标坏了"),
            ("外包装压坏了", None, "商品本身", None),
            ("只是外盒破了，商品没坏", None, "商品目前完好", None),
            ("如果收到空盒，可以申请售后吗？", None, "少收到东西", None),
            ("我没有收到空包裹", None, "这件商品的退换条件", None),
        ):
            with self.subTest(question=question):
                result, _ = run_turn(question)
                self.assertEqual(result.get("reason"), reason)
                self.assertIn(expected, result["answer"])
                if draft:
                    self.assertIn(draft, result["suggested_description"])
                    self.assertTrue(result["evidence_checklist"])
                else:
                    self.assertIsNone(result.get("suggested_description"))

    def test_empty_goods_report_keeps_draft_on_eligibility_followup(self):
        first, context = run_turn("打开是空盒，并且快递包装也压坏了")
        evidence, preserved = run_turn("我没拍开箱视频", context)
        self.assertIn("手头现有的材料", evidence["answer"])
        self.assertIn("这件商品的少件漏发", evidence["answer"])
        self.assertEqual(evidence["response_kind"], "evidence_guidance")
        self.assertEqual(preserved, context)
        second, _ = run_turn("这样还能提交吗？", preserved)
        self.assertIn("补发或仅退款", second["answer"])
        self.assertEqual(first["suggested_description"], second["suggested_description"])
        self.assertNotIn("运单号 PKG", first["answer"])
    """按口语咨询顺序核对金额、版本、引用范围及表单副作用。"""

    def test_citation_lookup_requires_support_for_each_asked_promise(self):
        """运费原文和没有依据的会员承诺各自说明，追问时保持相同范围。"""
        result, ctx = run_turn("查本单条款：退货运费谁出？有会员包邮吗？")
        self.assertTrue(result["evidence_requested"])
        self.assertEqual([row["id"] for row in result["evidence"]], ["freight"])
        self.assertIn("第 2 版", result["evidence"][0]["source"])
        self.assertIn("找不到能核实「会员运费权益」", result["answer"])
        repeated, _ = run_turn("那依据呢？", ctx)
        self.assertEqual(repeated["evidence"], result["evidence"])
        unsupported, _ = run_turn("终身免费换新吗？")
        self.assertFalse(unsupported["evidence"])
        self.assertIn("终身换新", unsupported["answer"])

    def test_policy_lookup_will_not_use_generic_reship_for_a_receiver(self):
        """已审缺件说明可以回答通用补发，接收器收纳位需要专门原文。"""
        item = shipped_product(missing_supplement={"id":4,"note":"可申请补发零件，请填写名称和数量。"})
        loose, _ = run_turn("查本单条款：零件少了能补寄吗？", item=item)
        self.assertEqual(loose["evidence"][0]["source"], "店铺已审核说明 #4")
        specific, _ = run_turn("查本单条款：接收器会放在电池仓吗？", item=item)
        self.assertFalse(specific["evidence"])
        self.assertIn("接收器收纳位", specific["answer"])

    def test_extra_promise_does_not_replace_order_status(self):
        """同一轮保留正常查单，同时对缺依据的请求给出说明。"""
        reply, _ = run_turn("原包裹到哪了？能上门维修吗？")
        self.assertIn("PKG00012", reply["answer"])
        self.assertIn("找不到能核实「上门维修」", reply["answer"])
        self.assertFalse(reply.get("evidence"))

    def test_missing_part_and_damaged_box_are_resolved_independently(self):
        """复合问题回答补发和照片，且只把已确认的缺件事实填入申请。"""
        question = "充电线少了一根，外盒也压扁了。线能补寄吗？照片要拍哪些？"
        self.assertTrue(is_direct_question(question))
        self.assertFalse(is_direct_question(question + "另外这个商品什么材质？"))
        item = shipped_product(missing_supplement={"id":4,"note":"请列出缺少的配件名称。"})
        reply, context = run_turn(question, item=item)
        self.assertEqual(reply["reason"], "missing")
        self.assertIn("reship", reply["kinds"])
        self.assertIn("压坏的位置", reply["answer"])
        self.assertIn("补发或仅退款", reply["answer"])
        self.assertIn("请列出缺少的配件名称", reply["answer"])
        self.assertEqual(reply["suggested_description"], "充电线少了一根")
        self.assertFalse(reply["quick_replies"])

        corrected, context = run_turn("线找到了，只是外盒破了，照片要拍哪些？", context, item=item)
        self.assertTrue(corrected["reset_suggestion"])
        self.assertFalse(corrected["kinds"])
        self.assertIsNone(context["reason"])
        self.assertIn("外箱和破损处照片", corrected["answer"])
        self.assertFalse(corrected["quick_replies"])
        later, _ = run_turn("那我这个可以申请售后吧？", context, item=item)
        self.assertFalse(later["kinds"])

    def test_found_cable_and_mouse_fault_replace_missing_draft(self):
        """配件找到后按商品故障核对资格，保留首轮的包装提示。"""
        first, context = run_turn("鼠标线没给我，盒子也瘪了")
        self.assertEqual(first["suggested_description"], "鼠标线没给我")
        self.assertIn("压坏的位置", first["answer"])
        corrected, context = run_turn("刚翻到了，鼠标倒是开不了机", context)
        self.assertEqual(context["reason"], "quality")
        self.assertTrue(corrected["reset_suggestion"])
        self.assertNotIn("reship", corrected["kinds"])
        self.assertIsNone(corrected["case_no"])

    def test_composite_returns_the_approved_product_supplement(self):
        """正式补充条款按当前缺件事实召回，不会被同轮拍照问点抢占。"""
        note = "无线鼠标少了充电线，可以单独补寄。请注明接口型号。"
        item = shipped_product(supplements=[{"id":12,"topic":"missing","title":"无线鼠标充电线缺件补发","note":note}])
        reply, context = run_turn("充电线少了一根，外盒也压扁了。线能补寄吗？照片要拍哪些？", item=item)
        self.assertIn(note, reply["answer"])
        explanation, _ = run_turn("依据呢？", context, item=item)
        self.assertEqual(explanation["evidence"][0]["text"], note)

    def test_answer_photo_question_while_clarifying_missing_reason(self):
        """有一项缺信息时，先回答已明确的拍照请求，再追问补发原因。"""
        reply, _ = run_turn("照片要拍哪些？能补寄吗？")
        self.assertIn("拍外包装全貌", reply["answer"])
        self.assertIn("你是少收到配件", reply["answer"])
        self.assertFalse(reply["kinds"])
        self.assertIn("少件漏发", reply["quick_replies"])
        case = {"case_no": "38", "status_label": "待店铺响应"}
        active, _ = answer(shipped_product(), "照片要拍哪些？能补寄吗？", None, {}, case,
                           date(2026, 11, 19), active_case=case)
        self.assertIn("拍外包装全貌", active["answer"])
        self.assertEqual(active["case_no"], "38")
        self.assertFalse(active["quick_replies"])

    def test_reviewed_guidance_changes_by_order_snapshot(self):
        """同一句复合问题使用本店据该购买时间已生效的说明。"""
        question = "少了一根充电线，外盒破了，能补寄吗？照片要拍哪些？"
        guidance = ("留言写明型号。", "说明缺件的位置。")
        for index, note in enumerate(guidance):
            item = shipped_product(missing_supplement={"id":index+1,"note":note})
            reply, _ = run_turn(question, item=item)
            self.assertIn(note, reply["answer"])
            self.assertNotIn(guidance[1-index], reply["answer"])
        expired, _ = run_turn(question, item=shipped_product(fault_days=1,
                              missing_supplement={"id":7,"note":"留言写明型号。"}))
        self.assertFalse(expired["kinds"])
        self.assertIn("截止", expired["answer"])
        self.assertNotIn("留言写明型号", expired["answer"])

    def test_missing_report_then_eligibility_confirmation(self):
        """缺件陈述和资格确认应给不同答复，并保留已有申请入口。"""
        report = "无线鼠标少了充电线"
        followup = "那我这个可以申请售后吧？"
        self.assertTrue(is_direct_question(report))
        self.assertTrue(is_direct_question(followup))
        self.assertFalse(is_direct_question("无线鼠标少了充电线，还能退款吗"))
        first, context = run_turn(report)
        second, _ = run_turn(followup, context)
        self.assertEqual(first["suggested_description"], report)
        self.assertEqual(second["suggested_description"], report)
        self.assertNotEqual(first["answer"], second["answer"])
        self.assertIn("2026-12-02", second["answer"])
        self.assertIn("reship", second["kinds"])

    def test_missing_part_followup_and_source(self):
        """买家追问整套寄回时使用同商品的已审说明。"""
        item = shipped_product(missing_supplement={"id":12,"note":"请说明缺少哪根线。"})
        _, ctx = run_turn("盒子里少了根线", item=item)
        reply,ctx = run_turn("那要把整套寄回吗？",ctx,item=item)
        self.assertIn("不用为申请补发把整套寄回",reply["answer"])
        self.assertIn("reship",reply["kinds"])
        source,_ = run_turn("依据呢",ctx,item=item)
        self.assertEqual(source["evidence"][0]["text"],"请说明缺少哪根线。")
        both,_ = run_turn("那要把整套寄回吗？这件一共买了几件？",ctx,item=item)
        self.assertIn("不用为申请补发把整套寄回",both["answer"])
        self.assertIn("买了 2 件",both["answer"])

    def test_missing_report_then_retract(self):
        """找到配件后，后续资格查询应重新核实原因。"""
        _, context = run_turn("盒子里少了根线")
        confirmed, context = run_turn("那我这个可以申请售后吧？", context)
        self.assertIn("reship", confirmed["kinds"])
        cleared, context = run_turn("找到了，不缺了", context)
        self.assertTrue(cleared["reset_suggestion"])
        later, _ = run_turn("那我这个可以申请售后吧？", context)
        self.assertFalse(later["kinds"])

    def test_parcel_followup_prefers_reship_tracking(self):
        """补发后的简短追问使用售后运单，单独询问则请买家确认包裹。"""
        case = {"case_no":"22","status":"reshipped","status_label":"已补发",
                "intervention":{"state":"waiting"},"outbound":{"parcel_no":"3","tracking":"RESHIP12345",
                "carrier":"店铺配送","received_at":None,"stage":"on_route","problem":None}}
        _, context = run_turn("补发到哪了？", case=case)
        result, _ = run_turn("那单号呢？", context, case=case)
        self.assertEqual(result["case_no"], "22")
        self.assertIn("RESHIP12345", result["answer"])
        self.assertNotIn("PKG00012", result["answer"])
        ambiguous, _ = run_turn("那单号呢？", case=case)
        self.assertIn("补发到哪了？", ambiguous["quick_replies"])

    def test_followup_amount_and_source(self):
        _, context = run_turn("寄回运费谁出？")
        quote, context = run_turn("买错了", context)
        self.assertLess(len(quote["answer"]), 55)
        self.assertNotIn("条款", quote["answer"])
        again, context = run_turn("那多少钱？", context)
        self.assertIn("预计自付 ¥7.00", again["answer"])
        why, context = run_turn("那是根据哪条规定？", context)
        self.assertIn("第 2 版", why["answer"])
        self.assertEqual([e["id"] for e in why["evidence"]], ["freight"])
        self.assertIn(why["evidence"][0]["text"], policy_lines(shipped_product())[2])
        self.assertNotIn("质量", why["evidence"][0]["text"])
        repeated, _ = run_turn("为什么", context)
        self.assertEqual(repeated["evidence"], why["evidence"])

    def test_multiple_costs_ask_which_amount(self):
        _, ctx = run_turn("原配送费和退货运费分别多少？", item=shipped_product(shipping=Decimal("6")))
        ambiguous, _ = run_turn("多少？", ctx)
        self.assertIn("原配送费多少？", ambiguous["quick_replies"])
        self.assertIn("寄回运费多少？", ambiguous["quick_replies"])

    def test_quantity_only_and_source(self):
        qty, ctx = run_turn("一共几件？")
        self.assertEqual(qty["answer"], "买了 2 件。")
        source, _ = run_turn("你怎么知道的？", ctx)
        self.assertIn("商品明细", source["answer"])
        self.assertFalse(source["evidence"])

    def test_unconfirmed_suggestion_and_hypothetical_do_not_fill_in_reason(self):
        prompt, ctx = run_turn("手滑点成另外那个款了，退回得掏钱吗", topics=["freight", "confirm_wrong_purchase"])
        self.assertIsNone(prompt["reason"])
        self.assertEqual(prompt["quick_replies"], ["对", "不是这个原因"])
        no, _ = run_turn("不是这个原因", ctx)
        self.assertTrue(no["reset_suggestion"])
        result, ctx = run_turn("买错了", ctx)
        self.assertEqual(result["freight"]["due"], Decimal("7"))
        maybe, ctx = run_turn("如果是商家发错，寄回运费谁承担？", ctx)
        self.assertFalse(maybe["kinds"])
        self.assertIsNone(maybe["reason"])
        self.assertEqual(ctx["reason"], "wrong_purchase")
        why, _ = run_turn("按哪条？", ctx)
        self.assertIn("发错", why["evidence"][0]["text"])

    def test_store_and_platform_are_distinct_from_purchased_terms(self):
        current = dict(return_days=30, fault_days=45, platform_version=1, reply_hours=12, warranty_days=120, return_postage=Decimal("9"), item_credit=Decimal("4"),
                       version=3, guidance="可以附型号。", floor=dict(version=1, return_days=7, fault_days=30,
                       reply_hours=48, max_postage=Decimal("12"), warranty_days=30))
        item = shipped_product(current_policy=current)
        now, ctx = run_turn("这家店现在寄回运费多少钱？", item=item)
        self.assertIn("¥9.00", now["answer"])
        self.assertIn("下单时用的是第 2 版", now["answer"])
        why, _ = run_turn("依据呢？", ctx, item=item)
        self.assertIn("店铺第 3 版", why["answer"])
        floor, _ = run_turn("平台最低保障是什么？", item=item)
        self.assertIn("至少 7 天", floor["answer"])
        order, _ = run_turn("我买错了，这单寄回多少钱？", ctx, item=item)
        self.assertEqual(order["freight"]["fee"], Decimal("12"))

    def test_missing_promises_and_older_context(self):
        result, _ = run_turn("是终身保修吗？")
        self.assertIn("找不到能核实「终身保修」", result["answer"])
        self.assertIsNone(result["reason"])
        self.assertEqual(last_subjects({}, {"topics": [], "freight": {"due": "6"}}), ["freight"])
