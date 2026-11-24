"""覆盖商城角色隔离、中文原因和版本条款下的办理资格。"""

from datetime import date, datetime, timezone
from decimal import Decimal
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from fastapi.testclient import TestClient

from app.access import DemoAccess, MALL_LOGIN_ROLES, build_account_book
from app.api import configured_app, create_app
from app.mall.consultation import answer, describe_outbound, describe_outcome, describe_return
from app.mall.offers import prepare_offer, refundable
from app.mall.photos import PhotoInput, decode_photos
from app.mall.rules import (
    MallOffer, MallOperation, ReturnDetails, assess_eligibility, available_actions, freight_quote, identify_reason, next_status,
    platform_intervention,
)
from app.tickets.service_requests import ServiceError


class MallRulesTests(unittest.TestCase):
    """验证条款差异和会改变售后状态的关键分支。"""

    def test_orders_apply_their_saved_policy_version(self):
        """同一次签收按不同版本核算，运输中和未发货使用独立路径。"""
        item = {"stage": "delivered", "delivered_at": datetime(2026, 11, 11, tzinfo=timezone.utc),
                "return_days": 5, "fault_days": 15, "warranty_days": 90,
                "change_of_mind": True, "repairable": True, "quantity": 2, "remaining_quantity": 2}
        today = date(2026, 11, 17)
        expired = assess_eligibility(item, "wrong_purchase", today)
        self.assertFalse(expired.kinds)
        self.assertIn("2026-11-16 截止", expired.message)
        self.assertEqual(assess_eligibility({**item, "return_days": 7}, "wrong_purchase", today).kinds,
                         ("return_refund", "exchange"))
        self.assertEqual(assess_eligibility(item, "missing", today).kinds, ("reship", "refund_only"))
        self.assertFalse(assess_eligibility({**item, "remaining_quantity": 0}, "quality", today).kinds)
        self.assertEqual(assess_eligibility({**item, "stage": "preparing"}, "cancel", today).kinds, ("refund_only",))
        shipping = assess_eligibility({**item, "stage": "in_transit", "expected_on": today}, "not_received", today)
        self.assertFalse(shipping.kinds)
        self.assertIn("尚未超期", shipping.message)

    def test_expired_return_explains_cutoff_and_quality_check_still_supports_repair(self):
        """重现旧条款键盘超期，保留实际质量问题的独立办理路径。"""
        today = date(2026, 11, 17)
        item = {"stage": "delivered", "delivered_at": datetime(2026, 10, 30, tzinfo=timezone.utc),
                "quantity": 1, "change_of_mind": True, "repairable": True, "return_days": 5, "fault_days": 15,
                "warranty_days": 90, "return_postage": Decimal("12"), "seller_advances": True,
                "terms_title": "松林数码售后服务条款", "terms_version": 1}
        refused, _ = answer(item, "买错了", None, {}, None, today)
        self.assertEqual(refused["kinds"], {})
        self.assertIn("2026-10-30", refused["answer"])
        self.assertIn("2026-11-04", refused["answer"])
        self.assertIn("质量问题", refused["eligibility"]["next_step"])
        repair, _ = answer(item, "质量问题", None, {}, None, today)
        self.assertEqual(repair["kinds"], {"repair": "维修"})
        self.assertIn("质量问题退换已于", repair["eligibility"]["message"])

    def test_order_with_active_case_routes_back_to_original(self):
        """同一商品有在办售后时，咨询直接提供原单编号。"""
        item = {"stage": "preparing", "quantity": 1, "terms_title": "条款", "terms_version": 1}
        case = {"case_no": "42", "status_label": "待店铺响应"}
        result, _ = answer(item, "买错了", None, {}, case, date(2026, 11, 17), active_case=case)
        self.assertFalse(result["kinds"])
        self.assertEqual(result["case_no"], "42")
        self.assertIn("待店铺响应", result["answer"])

    def test_transit_return_without_waiting_for_delay(self):
        """未签收想退和只问时效使用不同流程，客服不虚报运费。"""
        today = date(2026, 11, 18)
        item = {"stage": "in_transit", "quantity": 2, "expected_on": today, "terms_title": "条款", "terms_version": 1}
        result, _ = answer(item, "买了几天还没收到，不想要了，退货运费谁出？", None, {}, None, today)
        self.assertTrue(result["eligibility"]["transit"])
        self.assertEqual(result["kinds"], {"return_refund": "在途退回退款"})
        self.assertIsNone(result["freight"])
        self.assertIn("承运方核实", result["answer"])
        inquiry, _ = answer(item, "没收到包裹", None, {}, None, today)
        self.assertFalse(inquiry["kinds"])
        self.assertIn("尚未超期", inquiry["answer"])

    def test_unintercepted_parcel_keeps_return_paths_and_overdue_escalation(self):
        """拦截失败仅记录一次，继续派送期间保留退回、签收及超时入口。"""
        case = {"status": "awaiting_outcome", "kind": "return_refund", "transit_request": "whole",
                "package_stage": "in_transit", "interception_failure": {"note": "派送途中未拦住"},
                "interception_failed": True}
        self.assertEqual(available_actions(case, "merchant", overdue=False), ["carrier_return", "evidence"])
        self.assertIn("buyer_received", available_actions(case, "buyer", overdue=False))
        self.assertNotIn("dispute", available_actions(case, "buyer", overdue=False))
        self.assertIn("dispute", available_actions(case, "buyer", overdue=True))
        self.assertIn("仍在配送", describe_return(case))
        case.update(status="awaiting_return", return_details_pending=True, package_stage="delivered")
        self.assertNotIn("ship", available_actions(case, "buyer", overdue=True))
        self.assertIn("dispute", available_actions(case, "buyer", overdue=True))

    def test_freight_and_chinese_reason_distinctions(self):
        """避免把否定句和漏发当成原先的误购或发错，并使用剩余运费权益。"""
        self.assertIsNone(identify_reason("不是买错了"))
        self.assertEqual(identify_reason("不是买错了，是少发一件"), "missing")
        self.assertEqual(identify_reason("没收到包裹"), "not_received")
        item = {"stage": "delivered", "return_postage": Decimal("12"), "return_credit": Decimal("8")}
        self.assertEqual(freight_quote(item, "wrong_purchase")["due"], Decimal("4"))

    def test_disputed_case_records_evidence_and_platform_decision(self):
        """升级前后只有符合职责的角色能推进这张单。"""
        case = {"status": "submitted", "dispute_opened_at": None, "kind": "reship"}
        self.assertNotIn("dispute", available_actions(case, "buyer", overdue=False))
        self.assertIn("dispute", available_actions(case, "buyer", overdue=True))
        case["status"] = "disputed"
        self.assertEqual(available_actions(case, "merchant", overdue=True), ["evidence"])
        self.assertEqual(available_actions(case, "staff", overdue=True), ["support_buyer", "support_seller"])

    def test_open_offer_only_buyer_can_accept_and_rejection_allows_dispute(self):
        """方案进行中切换给买家，拒绝后可在原单介入。"""
        case = {"status": "submitted", "dispute_opened_at": None, "kind": "return_refund", "prompt_kind": "offer"}
        self.assertIn("accept_offer", available_actions(case, "buyer", overdue=False))
        self.assertNotIn("approve", available_actions(case, "merchant", overdue=False))
        operation = MallOperation(operation_id=uuid4(), action="accept_offer", note="买家同意")
        self.assertEqual(next_status(case, operation), "awaiting_return")
        case.update(prompt_kind=None, declined_offer=True)
        self.assertIn("dispute", available_actions(case, "buyer", overdue=False))
        case["prompt_kind"] = "offer"
        self.assertIn("dispute", available_actions(case, "buyer", overdue=False))
        self.assertNotIn("dispute", available_actions(case, "merchant", overdue=False))

    def test_platform_steps_and_submittable_actions_follow_same_rules(self):
        """只有允许的阶段展示介入入口，已介入或已完成可回到原单。"""
        for status, prompt, existing, overdue, outcome in (
            ("submitted", None, None, False, "waiting"),
            ("submitted", "offer", None, True, "waiting"),
            ("submitted", None, None, True, "available"),
            ("rejected", None, None, False, "available"),
            ("awaiting_return", None, None, False, "waiting"),
            ("disputed", None, True, True, "reviewing"),
            ("awaiting_outcome", None, True, True, "decided"),
            ("refunded", None, None, False, "closed"),
        ):
            with self.subTest(status=status, prompt=prompt, existing=existing, overdue=overdue):
                case = {"status": status, "kind": "return_refund", "prompt_kind": prompt, "dispute_opened_at": existing}
                self.assertEqual(platform_intervention(case, overdue=overdue)["state"], outcome)
                self.assertEqual("dispute" in available_actions(case, "buyer", overdue=overdue), outcome == "available")

    def test_order_chat_routes_human_request_to_the_case_or_intake(self):
        """覆盖截图的请求，以及未发起售后和平台处理中两个状态。"""
        item = {"terms_title": "商城条款", "terms_version": 2}
        today = date(2026, 11, 18)
        none, _ = answer(item, "转人工", None, {}, None, today)
        self.assertEqual(none["intervention"]["state"], "no_case")
        self.assertIsNone(none["case_no"])
        self.assertIn("请先向店铺申请", none["answer"])
        raw = {"status": "submitted", "declined_offer": True}
        case = {"case_no": "38", "intervention": platform_intervention(raw, overdue=False)}
        ready, _ = answer(item, "申请人工介入", None, {}, case, today)
        self.assertEqual(ready["case_no"], "38")
        self.assertEqual(ready["intervention"]["state"], "available")
        self.assertIn("你已拒绝店铺方案", ready["answer"])
        raw["status"] = "disputed"
        case["intervention"] = platform_intervention(raw, overdue=False)
        processing, _ = answer(item, "平台介入了吗", None, {}, case, today)
        self.assertEqual(processing["intervention"]["state"], "reviewing")
        self.assertIn("补充凭证", processing["answer"])

    def test_packaging_damage_is_confirmed_before_suggesting_reason(self):
        """外包装破损先追问，商品完好时不预填质量问题。"""
        today = date(2026, 11, 17)
        item = {"stage": "delivered", "delivered_at": datetime(2026, 11, 16, tzinfo=timezone.utc),
                "fault_days": 15, "warranty_days": 90, "repairable": False, "quantity": 1,
                "return_postage": Decimal("12"), "seller_advances": True, "terms_title": "条款", "terms_version": 2}
        first, context = answer(item, "外包装破了", None, {}, None, today)
        self.assertIsNone(first["reason"])
        confirmed, _ = answer(item, "里面也坏了", None, context, None, today)
        self.assertEqual(confirmed["reason"], "quality")
        self.assertIn("破损", confirmed["suggested_description"])
        intact, _ = answer(item, "商品没坏", None, context, None, today)
        self.assertIsNone(intact["suggested_description"])
        self.assertIsNone(intact["reason"])

    def test_evidence_rejects_plain_text_disguised_as_image(self):
        """保留浏览器可安全展示的受限图片类型。"""
        with self.assertRaises(ServiceError):
            decode_photos([PhotoInput(mime="image/png", data="dGV4dA==")])

    def test_platform_return_waits_for_the_shops_address(self):
        """缺少寄回地址时保持店铺待办，买家不能提前报运单。"""
        case = {"status": "awaiting_return", "decision": "support_buyer", "dispute_opened_at": True,
                "return_details_pending": True, "kind": "return_refund"}
        self.assertIn("provide_return_details", available_actions(case, "merchant", overdue=False))
        self.assertNotIn("ship", available_actions(case, "buyer", overdue=False))
        self.assertNotIn("review", available_actions(case, "buyer", overdue=False))
        self.assertIn("review", available_actions(case, "buyer", overdue=True))
        self.assertIn("当前由店铺补充", describe_return(case))
        case.update(return_details_pending=False,
                    return_details={"address": "本地验收园区", "recipient": "收件人", "phone": "01012340000"})
        self.assertIn("ship", available_actions(case, "buyer", overdue=False))
        self.assertIn("本地验收园区", describe_return(case))

    def test_receipt_review_requires_merchant_timeout_then_preserves_the_return(self):
        """复核只对尚未确认收件的超时开放，平台确认后进入收件后的执行。"""
        case = {"status": "return_sent", "decision": "support_buyer", "dispute_opened_at": True,
                "kind": "return_refund"}
        self.assertNotIn("review", available_actions(case, "buyer", overdue=False))
        self.assertIn("review", available_actions(case, "buyer", overdue=True))
        case.update(status="disputed", active_review_no=1, active_review_phase="receipt")
        self.assertEqual(available_actions(case, "buyer", overdue=False), ["evidence"])
        self.assertEqual(next_status(case, MallOperation(operation_id=uuid4(), action="support_buyer", note="已核对")),
                         "received")

    def test_review_for_missing_address_restores_buyer_shipment_stage(self):
        """平台可在复核里填已确认的收件信息，地址则由操作表单校验。"""
        case = {"status": "disputed", "active_review_phase": "return_details", "kind": "return_refund"}
        details = ReturnDetails(recipient="收件人", address="验收园区收件点", phone="01012340000")
        operation = MallOperation(operation_id=uuid4(), action="support_buyer", note="地址已核实", return_details=details)
        self.assertEqual(next_status(case, operation), "awaiting_return")

    def test_exchange_shipment_keeps_the_case_open_until_buyer_confirmation(self):
        """收回商品后等买家确认地址，发货与办结分离。"""
        case = {"status": "received", "kind": "exchange"}
        self.assertNotIn("replace", available_actions(case, "merchant", overdue=False))
        self.assertIn("confirm_delivery_address", available_actions(case, "buyer", overdue=False))
        case["outbound"] = {"stage": "ready", "problem": None}
        self.assertIn("replace", available_actions(case, "merchant", overdue=False))
        self.assertIn("report_delivery_problem", available_actions(case, "buyer", overdue=True))
        dispatch = MallOperation(operation_id=uuid4(), action="replace", note="发货", carrier="演练", tracking="TRACK1")
        self.assertEqual(next_status(case, dispatch), "received")
        case["outbound"]["stage"] = "shipped"
        self.assertIn("confirm_delivery", available_actions(case, "buyer", overdue=False))
        self.assertNotIn("confirm_delivery", available_actions(case, "merchant", overdue=False))
        receipt = MallOperation(operation_id=uuid4(), action="confirm_delivery", note="已收")
        self.assertEqual(next_status(case, receipt), "replacement_sent")

    def test_outbound_problem_waits_for_shop_and_agent_reads_the_new_waybill(self):
        """已报异常的包裹仍在原单，店铺可以处理，Agent 读取新运单。"""
        parcel = {"parcel_no": "5", "stage": "shipped", "carrier": "演练",
                  "tracking": "NEW456", "problem": "wrong_item", "received_at": None}
        case = {"status": "awaiting_outcome", "kind": "reship", "outbound": parcel}
        self.assertNotIn("confirm_delivery", available_actions(case, "buyer", overdue=False))
        self.assertIn("reissue_delivery", available_actions(case, "merchant", overdue=False))
        self.assertIn("NEW456", describe_outbound(case))
        self.assertIn("发错", describe_outbound(case))

    def test_delivery_review_can_follow_an_earlier_return_ruling(self):
        """新包裹超时可以单独介入，沿用原单和既有退货裁定。"""
        case = {"status": "received", "kind": "exchange", "dispute_opened_at": True,
                "decision": "support_buyer", "outbound": {"stage": "shipped", "problem": "damaged"}}
        self.assertNotIn("request_delivery_review", available_actions(case, "buyer", overdue=False))
        self.assertIn("request_delivery_review", available_actions(case, "buyer", overdue=True))
        self.assertEqual(platform_intervention(case, overdue=True)["state"], "delivery_review_available")
        case["delivery_review"] = {"action": "request_delivery_review"}
        self.assertEqual(platform_intervention(case, overdue=False)["state"], "reviewing")
        self.assertEqual(available_actions(case, "merchant", overdue=False), ["evidence"])
        self.assertEqual(available_actions(case, "staff", overdue=False), ["platform_reissue", "platform_continue_delivery"])

    def test_unshipped_review_can_require_dispatch_without_closing_case(self):
        """平台可催店铺发货，店铺登记前仍然没有售后运单。"""
        case = {"status": "awaiting_outcome", "kind": "reship", "outbound": {"stage": "ready", "problem": None},
                "delivery_review": {"action": "request_delivery_review"}}
        self.assertEqual(available_actions(case, "staff", overdue=True), ["platform_dispatch"])
        operation = MallOperation(operation_id=uuid4(), action="platform_dispatch", note="请店铺继续寄出")
        self.assertEqual(next_status(case, operation), "awaiting_outcome")


class MallAccessTests(unittest.TestCase):
    """验证进入新商城后旧平台商品办理和无身份读取被禁止。"""

    def test_configured_directory_retires_old_logins_and_preserves_staff(self):
        """新目录启用时，旧买家和店铺无法登录，平台口令继续有效。"""
        legacy = {"staff": "fixture-staff", "buyer-a": "fixture-buyer"}
        directory = {"buyer-001": "fixture-new-buyer", "shop-001": "fixture-new-shop"}
        with (
            patch("app.api.local_setting", side_effect=lambda key: {"ORDER_DATABASE_URL": "fixture"}.get(key)),
            patch("app.api.load_credentials", side_effect=(legacy, directory)) as load,
            patch("app.api.DIRECTORY_ACCOUNT_FILE") as account_file,
            patch("app.api.MERCHANT_ACCOUNT_FILE") as merchant_file,
            patch("app.api.PostgresMall"),
            patch("app.api.MallDirectory.account_names", return_value={"buyer-001": "张三"}),
            patch("app.api.create_app", return_value=Mock()) as create,
        ):
            account_file.is_file.return_value = True
            configured_app()
        self.assertEqual(load.call_count, 2)
        merchant_file.is_file.assert_not_called()
        access = create.call_args.kwargs["access"]
        self.assertIsNone(access.login("buyer-a", "fixture-buyer"))
        self.assertIsNone(access.login("shop-qh", "fixture-shop"))
        self.assertEqual(access.login("张三", "fixture-new-buyer")[1].account_id, "buyer-001")
        self.assertEqual(access.login("shop-001", "fixture-new-shop")[1].role, "merchant")
        self.assertEqual(access.login("staff", "fixture-staff")[1].role, "staff")

    def test_account_book_links_each_name_to_the_right_file_and_key(self):
        """账号索引只展示新买家、新店铺和平台客服。"""
        names = {account: f"名称{index}" for index, account in enumerate(MALL_LOGIN_ROLES)}
        names.update({"buyer-001": "张三", "shop-001": "栖禾数码"})
        book = build_account_book(names)
        self.assertEqual([len(group) for group in book.values()], [50, 100, 1])
        self.assertEqual(book["买家"]["buyer-001"], {
            "名称": "张三", "密码文件": "./mall-directory-accounts.json", "密码键": "buyer-001",
        })
        self.assertEqual(book["店铺"]["shop-001"]["名称"], "栖禾数码")
        self.assertNotIn("shop-qm", book["店铺"])
        self.assertEqual(book["平台客服"]["staff"]["密码文件"], "./demo-accounts.json")

    def test_chinese_login_name_resolves_to_existing_account(self):
        """中文名和编号使用同一套口令、账号和角色。"""
        credentials = {"buyer-001": "buyer-secret", "shop-001": "merchant-secret"}
        access = DemoAccess(credentials, names={"buyer-001": "张三", "shop-001": "栖禾数码"})
        self.assertIsNone(access.login("张三", credentials["shop-001"]))
        for name, account_id, role in (("张三", "buyer-001", "buyer"), ("栖禾数码", "shop-001", "merchant")):
            _, account = access.login(name, credentials[account_id])
            _, original = access.login(account_id, credentials[account_id])
            self.assertEqual((account.account_id, account.role, account.display_name), (account_id, role, name))
            self.assertEqual(account, original)

    def test_duplicate_login_names_require_the_account_number(self):
        """两个账号同名时不把姓名归给任一账号。"""
        credentials = {"buyer-001": "first-buyer-secret", "buyer-002": "second-buyer-secret"}
        access = DemoAccess(credentials, names=dict.fromkeys(credentials, "周宁"))
        for account_id, secret in credentials.items():
            self.assertIsNone(access.login("周宁", secret))
            self.assertEqual(access.login(account_id, secret)[1].display_name, "周宁")

    def test_history_cannot_bypass_new_platform_scope(self):
        """测试账号保持现有角色，商城上线后旧平台查单路由关闭。"""
        creds = {name: "test-only-mall-login-" + name for name in ("staff", "shop-qh")}
        store = Mock()
        store.page.return_value = {"cases": [], "next_before": None}
        with TestClient(create_app(access=DemoAccess(creds), mall=store), follow_redirects=False) as client:
            self.assertEqual(client.get("/api/staff/mall/cases").status_code, 401)
            login = client.post("/api/session/login", json={"account_id": "staff", "secret": creds["staff"]})
            self.assertEqual(login.json()["home"], "/staff/disputes")
            self.assertEqual(client.get("/staff/service-requests").headers["location"], "/staff/disputes")
            self.assertEqual(client.get("/api/staff/service-requests").status_code, 410)
            self.assertEqual(client.get("/api/staff/orders/JD-00020155F1A4").status_code, 410)
            self.assertEqual(client.get("/api/staff/mall/cases").status_code, 200)
            client.post("/api/session/login", json={"account_id": "shop-qh", "secret": creds["shop-qh"]})
            self.assertEqual(client.get("/api/staff/mall/cases").status_code, 403)

    def test_logged_out_case_link_keeps_requested_action(self):
        """从收藏或会话失效后打开介入链接，登录页能恢复原地址。"""
        with TestClient(create_app(mall=Mock()), follow_redirects=False) as client:
            path = "/mall/after-sales?case=38&action=dispute"
            response = client.get(path)
            self.assertEqual(response.status_code, 303)
            location = urlsplit(response.headers["location"])
            self.assertEqual(location.path, "/login")
            self.assertEqual(parse_qs(location.query), {"next": [path]})


class MallOfferTests(unittest.TestCase):
    """验证方案金额、类型与已确认后的操作限制。"""

    def setUp(self):
        """使用带一部分既有退款的签收订单商品。"""
        self.case = {"kind": "return_refund", "reason": "quality", "package_stage": "delivered",
                     "created_at": datetime(2026, 11, 17, tzinfo=timezone.utc),
                     "delivered_at": datetime(2026, 11, 16, tzinfo=timezone.utc), "fault_days": 15,
                     "repairable": False, "quantity": 2, "remaining_quantity": 2,
                     "paid_unit": Decimal("139"), "refund_balance": Decimal("258")}

    def test_offer_caps_amount_and_survives_prior_partial_credit(self):
        """留货退款受本次件数和累计余额限制，寄回退款按实算。"""
        self.assertEqual(refundable(self.case, 2), Decimal("258"))
        offer = MallOffer(kind="partial_refund", quantity=1, amount="20")
        self.assertEqual(prepare_offer(self.case, offer)["amount"], "20.00")
        with self.assertRaises(ServiceError):
            prepare_offer(self.case, MallOffer(kind="partial_refund", quantity=1, amount="139"))
        return_offer = MallOffer(kind="return_refund", quantity=2, return_instructions="演练地址")
        self.assertEqual(prepare_offer(self.case, return_offer)["amount"], "258.00")

    def test_accepted_partial_refund_uses_agreed_route(self):
        """原申请为退货时，已接受的留货方案直接转至店铺执行。"""
        selected = {"kind": "partial_refund"}
        state = {**self.case, "pending_offer": selected, "status": "submitted"}
        op = MallOperation(operation_id=uuid4(), action="accept_offer", note="确认")
        self.assertEqual(next_status(state, op), "awaiting_outcome")
        state.update(pending_offer=None, agreement=selected, status="awaiting_outcome")
        self.assertIn("refund", available_actions(state, "merchant", overdue=False))

    def test_consult_distinguishes_agreement_and_recorded_refund(self):
        """Agent 按真实登记回答，接受方案不等于已退款。"""
        case = {"agreement": {"kind": "partial_refund", "quantity": 1, "amount": "20.00",
                              "return_instructions": None}}
        phrases = describe_outcome(case, check_offer=True, check_refund=True)
        self.assertTrue(any("尚未登记退款" in phrase for phrase in phrases))
        case["recorded_refund"] = Decimal("20.00")
        self.assertTrue(any("已在商城登记商品退款 ¥20.00" in phrase for phrase in
                            describe_outcome(case, check_offer=False, check_refund=True)))

    def test_consult_answers_original_shipping_separately(self):
        """单问配送费时，引用售后明细中的原配送费记录及原售后编号。"""
        case = {"case_no": "42", "status_label": "退款成功", "owner": None, "overdue": False,
                "refund_summary": {"original_shipping": {"message": "原配送费实付 ¥6.00。"},
                                   "return_shipping": {"message": "寄回运费待核实。"}}}
        result, _ = answer({"terms_version": 1, "terms_title": "条款"}, "原配送费会退吗？",
                           None, {}, case, date(2026, 11, 19))
        self.assertEqual(result["case_no"], "42")
        self.assertIn("原配送费实付 ¥6.00", result["answer"])
        self.assertNotIn("寄回运费", result["answer"])

