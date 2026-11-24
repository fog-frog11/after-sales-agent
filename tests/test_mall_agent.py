"""覆盖商品咨询的工具选择、两轮追问、条款版本和模型故障回退。"""

import json
import unittest
from contextlib import nullcontext
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.access import DemoAccess
from app.api import create_app
from app.storage.postgres_mall import PostgresMall

from app.integrations.deepseek import ModelError
from app.mall.agent import MallAgent, PLANNING_TOOL
from app.mall.consultation import answer, consultation_evidence, narrow_order_candidates, resolve_query_scope, split_order_questions
from app.mall.rules import MallQuestion, policy_lines


def shipped_product(**changes):
    """创建带购买时条款的已签收商品，便于精确核对金额与期限。"""
    return {"title": "旅行杯", "variant": "岩灰 · 380mL", "quantity": 2, "package_no": 1,
            "paid_unit": Decimal("39"), "stage": "delivered", "tracking": "PKG00012",
            "delivered_at": datetime(2026, 11, 17, tzinfo=timezone.utc), "return_days": 7,
            "fault_days": 15, "warranty_days": 90, "reply_hours": 24, "return_postage": Decimal("12"),
            "return_credit": Decimal("5"), "seller_advances": True, "change_of_mind": True, "repairable": False,
            "terms_title": "店铺售后条款", "terms_version": 2, **changes}


def run_turn(question, context=None, *, item=None, topics=None, case=None, follow_up=None):
    """运行确定性消费节点，使用固定日期对照每个回复。"""
    return answer(item or shipped_product(), question, None, context or {}, case,
                  date(2026, 11, 19), topics=topics, follow_up=follow_up)


class MallAgentTests(unittest.TestCase):
    """检验模型只能改变读取的事项，业务结论来自已授权的事实。"""

    def test_off_topic_plan_does_not_override_known_after_sales_questions(self):
        """范围判断只在整句没有已识别的售后诉求时生效。"""
        planner = Mock(return_value={"name": PLANNING_TOOL, "arguments": json.dumps({"topics": ["out_of_scope"]})})
        agent = MallAgent(planner)
        for question, expected in (
            ("杭州今天会下雨吗？", ["out_of_scope"]),
            ("杭州今天会下雨吗？顺便查原包裹到哪了。", ["logistics"]),
            ("我没拍开箱视频", []),
            ("这个毛刷用着效果不太好", []),
        ):
            with self.subTest(question=question):
                result = agent.select_queries({"question": question, "use_model": True})
                self.assertEqual(result["topics"], expected)

    def test_off_topic_writer_can_make_redirect_natural_without_order_data(self):
        """只把服务范围交给撰写模型，拒绝不含引导的回复。"""
        writer = Mock(return_value="我平时会看天气。")
        mall = PostgresMall("unused", writer=writer)
        result = {"route": "function_call", "response_kind": "out_of_scope", "answer": "默认范围提示"}
        with patch.object(mall, "connect", return_value=nullcontext(Mock())) as connect:
            mall._write_consultation(1, "明天天气如何", result)
            connect.assert_not_called()
            self.assertEqual(result["answer"], "默认范围提示")
            writer.return_value = "我在这儿帮你处理订单售后。你想查物流还是退换货？"
            mall._write_consultation(1, "明天天气如何", result)
            connect.assert_called_once()
        writer.assert_called_with({"任务": "引导回售后", "可协助": ["物流", "退换货", "退款进度"]})
        self.assertEqual(result["answer_source"], "model")

    def test_writer_cannot_reopen_known_scope_or_rewrite_evidence(self):
        """有绑定商品时，模型的二次润色不得增加商品或订单追问。"""
        writer = Mock(return_value="请问您具体是哪个商品或凭证有问题呢？")
        mall = PostgresMall("unused", writer=writer)
        advice = {"route": "rules", "topics": [], "response_kind": "evidence_guidance", "answer": "请拍外箱。"}
        mall._write_consultation(1, "没拍开箱视频", advice)
        writer.assert_not_called()
        product = {"route": "function_call", "topics": ["product"], "reference": {"title": "帆布包"},
                   "answer": "斜挎帆布包。"}
        mall._write_consultation(2, "买的哪个款", product)
        writer.assert_called_once()
        self.assertEqual(product["answer"], "斜挎帆布包。")

    def test_split_order_questions_keeps_each_request_with_its_scope(self):
        """支持倒序与同句连接词，并保留同一订单的连续追问。"""
        self.assertEqual(split_order_questions("这单退货要我出邮费吗？刚才那单还能退吗？"),
                         ("这单退货要我出邮费吗？", "这笔订单还能退吗？"))
        self.assertEqual(split_order_questions("刚才那笔订单还能退吗另外这件商品的规格是什么？"),
                         ("另外这件商品的规格是什么？", "这笔订单还能退吗？"))
        self.assertIsNone(split_order_questions("这单补发到哪了？那单号呢？"))

    def test_mixed_question_queries_current_item_and_returns_pending_selection(self):
        """当前订单回复与另一单待确认的问题分别进入响应。"""
        mall = PostgresMall("unused")
        conn = Mock()
        candidate = {"order_id": "CN-300002", "item_no": 1}
        conn.execute.return_value.fetchone.side_effect = [None, {"result": {"order_candidates": [candidate]}}, {"turn_no": 42}]
        item = shipped_product(title="无线鼠标", shop_id="market-001")
        question = MallQuestion(question="这件商品的规格是什么？刚才那笔订单还能退吗？")
        with patch.object(mall, "connect", return_value=nullcontext(conn)), patch.object(
            mall, "_items", return_value=[item]
        ), patch.object(mall, "_page", return_value={"cases": []}):
            result = mall.consult("buyer-001", "CN-300001", 1, question)
        self.assertIn("无线鼠标", result["answer"])
        self.assertEqual(result["reference"]["order_id"], "CN-300001")
        self.assertEqual(result["topics"], ["product"])
        self.assertEqual(result["order_candidates"], [candidate])
        self.assertEqual(result["pending_question"], "这笔订单还能退吗？")
        self.assertEqual(result["pending_turn"], "42")

    def test_order_reference_filters_displayed_candidates(self):
        """顺序和月份只从实际候选筛选，跨年时照样按购买日期匹配。"""
        rows = [{"purchased": "2027-01-03"}, {"purchased": "2026-12-15"}, {"purchased": "2026-12-20"}]
        today = date(2027, 1, 4)
        self.assertEqual(narrow_order_candidates("我指的是第二笔", rows, today), rows[1:2])
        self.assertEqual(narrow_order_candidates("上个月买的那单", rows, today), rows[1:])
        self.assertEqual(narrow_order_candidates("12月20号买的", rows[1:], today), rows[2:])
        self.assertEqual(narrow_order_candidates("第四笔", rows, today), [])

    def test_model_filter_is_validated_and_forwarded(self):
        """模型只提供日期与价格方向，不能用筛选器替换订单。"""
        def select(filters):
            planner = Mock(return_value={"name": PLANNING_TOOL, "arguments": json.dumps(
                {"topics": ["clarify_order"], "candidate_filter": filters}
            )})
            respond = Mock(return_value={})
            filtered = Mock(return_value={})
            MallAgent(planner).run("六月份买的便宜那件", True, respond, respond_filtered=filtered)
            return respond, filtered

        for bad in ({"month": True}, {"month": 13}, {"order_id": "CN-410001"}, {"price": "invalid"},
                    {"year": 2025, "month": 2, "day": 29}, {"day": 32}):
            with self.subTest(bad=bad):
                respond, filtered = select(bad)
                filtered.assert_not_called()
                respond.assert_called_once_with([], "fallback")
        filters = {"month": 6, "day": 3, "price": "lowest"}
        respond, filtered = select(filters)
        respond.assert_not_called()
        filtered.assert_called_once_with(["clarify_order"], "function_call", filters)

    def test_candidate_price_filter_applies_after_month_and_keeps_ties(self):
        """更低价的其他月份订单不能入选，同月份同价不单独挑一个。"""
        conn = Mock()
        candidates = [{"order_id": f"CN-41000{number}", "item_no": 1} for number in range(1, 5)]
        conn.execute.return_value.fetchall.return_value = [
            {**row, "purchased": date(2026, month, 10), "paid_unit": Decimal(price)}
            for row, month, price in zip(candidates, (5, 6, 6, 6), ("5", "19", "19", "29"))
        ]
        rows = PostgresMall._filter_buyer_candidates(conn, "buyer-001", candidates, {"month": 6, "price": "lowest"})
        self.assertEqual([row["order_id"] for row in rows], ["CN-410002", "CN-410003"])
        self.assertEqual([row["paid_unit"] for row in rows], ["19", "19"])
        candidates_by_date = conn.execute.return_value.fetchall.return_value
        candidates_by_date[2]["purchased"] = date(2026, 6, 11)
        rows = PostgresMall._filter_buyer_candidates(conn, "buyer-001", candidates,
                                                     {"month": 6, "day": 10, "price": "lowest"})
        self.assertEqual([row["order_id"] for row in rows], ["CN-410002"])

    def test_consult_api_reports_timing_and_skips_planner_for_direct_questions(self):
        """用内存中的订单答复覆盖 HTTP 计时、意图分流和身份预检。"""
        planner = Mock(return_value={"name": PLANNING_TOOL, "arguments": '{"topics":["product"]}'})
        mall = PostgresMall("unused", planner)
        conn = Mock()
        conn.execute.return_value.fetchone.return_value = {"owned": 1}
        creds = {"buyer-001": "test-only-mall-consultation"}
        with patch.object(mall, "connect", return_value=nullcontext(conn)), patch.object(
            mall, "_answer_consultation", side_effect=lambda buyer, order, item, form, topics, route, other: {"route": route}
        ), TestClient(create_app(mall=mall, access=DemoAccess(creds))) as client:
            self.assertEqual(client.post("/api/session/login", json={"account_id":"buyer-001","secret":creds["buyer-001"]}).status_code, 200)
            path = "/api/buyer/mall/orders/CN-300001/items/1/consult"
            for question, expected in (("补发到哪了？", "rules"), ("那款长啥样", "function_call")):
                response = client.post(path, json={"question":question})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["route"], expected)
                phases = {step.split(";")[0] for step in response.headers["server-timing"].split(", ")}
                self.assertEqual(phases, {"auth", "plan", "order", "consult"})
        planner.assert_called_once_with("那款长啥样")
        conn.execute.assert_called_once()

    def test_multi_topic_model_routes_through_graph(self):
        """即使口语没有命中本地规则，模型仍可同时选择规格与退款。"""
        choose = Mock(return_value={"name": PLANNING_TOOL, "arguments": '{"topics":["product","refund"]}'})
        callback = lambda topics, route: {**run_turn("那款啥样，款子回了没", topics=topics)[0], "route": route}
        result = MallAgent(choose).run("那款啥样，款子回了没", True, callback)
        self.assertEqual(result["route"], "function_call")
        self.assertIn("岩灰 · 380mL", result["answer"])
        self.assertIn("暂时没有售后申请", result["answer"])
        choose.assert_called_once_with("那款啥样，款子回了没")

    def test_replacement_scope_discards_generic_original_logistics(self):
        """即使规划同时提出两条物流路径，明确问替换件时也只查询售后包裹。"""
        choose = Mock(return_value={"name": PLANNING_TOOL, "arguments": '{"topics":["logistics","outbound"]}'})
        agent = MallAgent(choose)
        result = agent.select_queries({"question": "替换给我的那件是不是已经交给快递公司了", "use_model": True})
        self.assertEqual(result["topics"], ["outbound"])
        combined = agent.select_queries({"question": "原包裹到哪了？替换给我的也交给快递了吗？", "use_model": True})
        self.assertEqual(set(combined["topics"]), {"outbound", "logistics"})

    def test_invalid_plan_and_timeout_keep_user_question(self):
        """格式错误、未知事项、夹带订单号和超时均回到本地多意图判断。"""
        state = {"question": "买了几件？退款到哪了？", "use_model": True}
        for args in ({"topics": ["issue_refund"]}, {"topics": ["refund"], "order_id": "CN-300002"},
                     {"topics": "refund"}, {"topics": [{}]}, None):
            with self.subTest(args=args):
                planner = Mock(return_value={"name": PLANNING_TOOL, "arguments": json.dumps(args)})
                plan = MallAgent(planner).select_queries(state)
                self.assertEqual(plan["route"], "fallback")
                self.assertTrue({"product", "refund"} <= set(plan["topics"]))
        for error in (TimeoutError(), ModelError()):
            self.assertEqual(MallAgent(Mock(side_effect=error)).select_queries(state)["route"], "fallback")

    def test_structured_preflight_bypasses_model(self):
        """表单指定原因或快捷回复时不向模型发请求。"""
        choose = Mock(side_effect=AssertionError("unexpected model request"))
        plan = MallAgent(choose).select_queries({"question": "核对退货运费", "use_model": False})
        self.assertEqual(plan, {"route": "rules", "topics": ["freight"]})
        choose.assert_not_called()

    def test_postage_followup_evidence_is_exact_order_version(self):
        """首次追问原因，答复、权益抵扣及引用段落与该订单相符。"""
        first, state = run_turn("退货邮费咋算")
        self.assertIn("买错了", first["quick_replies"])
        for postage, version in (("12", 1), ("19", 2)):
            with self.subTest(version=version):
                item = shipped_product(return_postage=Decimal(postage), terms_version=version)
                result, _ = run_turn("买错了", state, item=item)
                self.assertEqual(result["freight"]["due"], Decimal(postage) - 5)
                self.assertEqual(result["terms_version"], version)
                evidence = consultation_evidence(item, [], result)
                self.assertEqual([row["text"] for row in evidence], [policy_lines(item)[0], policy_lines(item)[2]])

    def test_confirmed_reason_survives_logistics_until_corrected(self):
        """中途查询原包裹后仍可继续核价，纠正原因不会沿用旧报价。"""
        _, state = run_turn("我拍多了")
        delivery, state = run_turn("物流到哪了", state)
        self.assertIn("PKG00012", delivery["answer"])
        quote, state = run_turn("那我自付多少", state)
        self.assertEqual(quote["freight"]["due"], Decimal("7"))
        revised, state = run_turn("不是买错了，那运费呢", state)
        self.assertIsNone(revised["reason"])
        self.assertIsNone(state["reason"])
        self.assertIn("商家发错了", revised["quick_replies"])

    def test_damage_and_postage_resolve_after_one_followup(self):
        """先问清本体是否损坏，随后同时回答拍照、垫付和可办方式。"""
        first, state = run_turn("外包装破了，照片怎么拍，运费谁出？")
        self.assertIsNone(first["reason"])
        self.assertEqual(first["quick_replies"], ["里面也坏了", "商品没坏"])
        result, _ = run_turn("里面也坏了", state)
        self.assertIn("先垫付 ¥12.00", result["answer"])
        self.assertIn("遮住手机号", result["answer"])
        self.assertIn("return_refund", result["kinds"])
        intact, _ = run_turn("商品没坏", state)
        self.assertIsNone(intact["reason"])
        self.assertIsNone(intact["suggested_description"])

    def test_case_progress_reads_owner_deadline_and_recorded_amount(self):
        """即使是模型识别的口语，退款数额、负责人和截止时间也由原单给出。"""
        case = {"case_no": "18", "status": "received", "owner": "店铺客服", "overdue": False,
                "status_label": "待店铺处理结果", "due_at": datetime(2026, 11, 20, 2, tzinfo=timezone.utc),
                "refund_summary": {"product": {"message": "商品退款 ¥17.00 本次处理失败，等待店铺按原金额重试。",
                                              "failure_reason": "渠道超时"}}}
        result, _ = run_turn("款子回来没", topics=["refund"], case=case)
        self.assertEqual(result["case_no"], "18")
        for expected in ("¥17.00", "渠道超时"):
            self.assertIn(expected, result["answer"])
        self.assertNotIn("本步截止", result["answer"])
        progress, _ = run_turn("售后谁处理，什么时候截止", topics=["progress"], case=case)
        for expected in ("店铺客服", "2026-11-20 10:00"):
            self.assertIn(expected, progress["answer"])
        self.assertEqual(consultation_evidence(shipped_product(), ["refund"], result), [])

    def test_retract_damage_clears_prior_quality_suggestion(self):
        """买家更正商品实际完好时，旧的质量原因和拟填说明不再适用。"""
        _, context = run_turn("外包装破了")
        _, context = run_turn("里面也坏了", context)
        answer_now, context = run_turn("刚看清楚，商品没坏", context)
        self.assertTrue(answer_now["reset_suggestion"])
        self.assertIn("商品本身完好", answer_now["answer"])
        self.assertIsNone(context["reason"])

    def test_original_shipping_is_separate_from_return_quote(self):
        """没有售后单时，原配送费读取结算记录，不挪用寄回报价。"""
        item = shipped_product(shipping=Decimal("6"))
        result, _ = run_turn("原配送费多少？", item=item)
        self.assertIn("原订单配送费实付 ¥6.00", result["answer"])
        self.assertIsNone(result["freight"])
        self.assertFalse(result["quick_replies"])

    def test_query_scope_keeps_only_requested_fee_or_photos(self):
        """快递相关词出现在费用或拍照问题中时，保留独立的物流诉求。"""
        for question, planned, expected in (
            ("快递盒破损的部位拍哪几个角度", ["logistics", "photos"], ["photos"]),
            ("我退货发快递得掏几块钱", ["logistics", "freight"], ["freight"]),
            ("当时给我送货的邮费算了几块", ["freight", "original_shipping"], ["original_shipping"]),
            ("外箱怎么拍，顺便查原包裹到哪了", ["photos", "logistics"], ["photos", "logistics"]),
        ):
            with self.subTest(question=question):
                self.assertEqual(resolve_query_scope(question, planned), expected)

