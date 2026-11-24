"""验证工单预览、确认、重复提交和暂时故障。"""

import unittest
from datetime import datetime, timezone
from datetime import date
from decimal import Decimal
from unittest.mock import Mock
from uuid import uuid4

from fastapi.testclient import TestClient

from app.access import DemoAccess, SESSION_COOKIE
from app.api import create_app
from app.orders.logistics import LogisticsOrder
from app.orders.olist_items import OrderItem
from app.tickets.workflow import TicketStoreError


class MemoryTickets:
    """测试用工单仓储，记录写入次数并可模拟一次数据库故障。"""

    def __init__(self):
        """创建空仓储，避免不同用例共享记录。"""
        self.records = {}
        self.aliases = {}
        self.writes = 0
        self.fail_next_write = False

    def get(self, confirmation_id):
        """按确认 ID 查找已提交的工单。"""
        return self.records.get(confirmation_id) or self.aliases.get(confirmation_id)

    def create(self, confirmation_id, order_id, reason, *, handoff=None):
        """模拟写入，或者在写入前抛出一次连接错误。"""
        existing = self.find_order_ticket(order_id, pending_only=not order_id.startswith("OL-"))
        if existing is not None:
            self.aliases[confirmation_id] = existing
            return existing
        self.writes += 1
        if self.fail_next_write:
            self.fail_next_write = False
            raise TicketStoreError("工单数据库暂时不可用。")
        record = {
            "ticket_no": len(self.records) + 1,
            "order_id": order_id,
            "reason": reason,
            "status": "pending",
            "created_at": datetime.now(timezone.utc),
        }
        self.records[confirmation_id] = record
        if handoff is not None:
            record["handoff"] = handoff
        return record

    def find_olist_ticket(self, order_id):
        """返回此历史物流样本的工单。"""
        return self.find_order_ticket(order_id)

    def find_order_ticket(self, order_id, *, pending_only=False):
        """优先给出待处理工单，其次给出最新已处理工单。"""
        candidates = (row for row in self.records.values()
                      if row["order_id"] == order_id and (not pending_only or row["status"] == "pending"))
        return max(candidates, key=lambda row: (row["status"] == "pending", row["ticket_no"]), default=None)

    def get_by_number(self, ticket_no):
        """按工单号读取一个模拟工单。"""
        return next((item for item in self.records.values() if item["ticket_no"] == ticket_no), None)

    def list_pending(self):
        """返回模拟仓储里的待处理工单。"""
        return [row for row in self.records.values() if row["status"] == "pending"]

    def resolve(self, ticket_no, resolution, *, actor=None):
        """记录第一次处理并模仿数据库的状态条件。"""
        ticket = self.get_by_number(ticket_no)
        if ticket is None or ticket["status"] != "pending":
            return None
        ticket.update(status="resolved", resolution=resolution, resolved_at=datetime.now(timezone.utc))
        if actor is not None:
            ticket["resolved_by"] = actor
        return ticket


class WorkbenchAccessTests(unittest.TestCase):
    """从登录接口验证买家数据隔离和客服工单权限。"""

    def setUp(self):
        """为三个角色建立各自的 Cookie 客户端。"""
        self.credentials = {name: f"simulation-secret-for-{name}" for name in ("buyer-a", "buyer-b", "staff")}
        self.app = create_app(tickets=MemoryTickets(), access=DemoAccess(self.credentials))
        self.buyer_a = self.client_for("buyer-a")
        self.buyer_b = self.client_for("buyer-b")
        self.staff = self.client_for("staff")

    def client_for(self, name):
        """通过真实 HTTP 登录生成一套独立会话。"""
        client = self.enterContext(TestClient(self.app, follow_redirects=False))
        response = client.post("/api/session/login", json={"account_id": name, "secret": self.credentials[name]})
        self.assertEqual(response.status_code, 200)
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertIn("SameSite=strict", response.headers["set-cookie"])
        return client

    def test_signed_out_and_cross_origin_are_rejected(self):
        """未登录、外站提交和已撤销的会话无法查单。"""
        with TestClient(self.app, follow_redirects=False) as anonymous:
            self.assertEqual(anonymous.get("/").headers["location"], "/login")
            self.assertEqual(anonymous.get("/api/staff/tickets").status_code, 401)
            self.assertEqual(anonymous.post("/api/session/login", json={
                "account_id": "staff", "secret": self.credentials["buyer-a"],
            }).status_code, 401)
            bad = self.buyer_a.post("/api/conversation", json={"question": "A102"},
                                    headers={"Origin": "http://other.example"})
            self.assertEqual(bad.status_code, 403)
            original = self.buyer_a.cookies.get(SESSION_COOKIE)
            self.assertEqual(self.buyer_a.post("/api/session/logout").status_code, 200)
            anonymous.cookies.set(SESSION_COOKIE, original)
            self.assertEqual(anonymous.post("/api/conversation", json={"question": "A102"}).status_code, 401)

    def test_orders_and_dialogue_are_isolated(self):
        """买家查自己订单，猜到别人的订单号和会话 ID 也读不到内容。"""
        own = self.buyer_a.post("/api/conversation", json={"question": "A102"}).json()
        self.assertEqual(own["result"]["order_id"], "A102")
        other = self.buyer_b.post("/api/conversation", json={"question": "A102"}).json()
        self.assertEqual(other["result"]["status"], "order_not_found")
        self.assertNotIn("order_id", other["result"])
        self.assertEqual(self.buyer_b.get(f"/api/conversation/{own['conversation_id']}").status_code, 404)
        self.assertEqual(self.buyer_a.get(f"/api/conversation/{own['conversation_id']}").status_code, 200)
        self.assertEqual(self.staff.post("/api/conversation", json={"question": "A102"}).status_code, 403)

    def test_staff_sees_confirmed_handoff_snapshot(self):
        """交接预览按账号选取对话，确认只提交买家已看过的版本。"""
        order = LogisticsOrder("002f19a65a" + "0" * 22, "shipped", date(2018, 9, 1), date(2018, 10, 9), None)
        item = OrderItem(1, "a" * 32, "casa", Decimal("49.90"), Decimal("13.37"))
        store = MemoryTickets()
        self.app = create_app(tickets=store, access=DemoAccess(self.credentials),
                              logistics_lookup=lambda _: order, items_lookup=lambda _: [item])
        buyer, other, staff = [self.client_for(name) for name in ("buyer-a", "buyer-b", "staff")]
        path = "/api/conversation"
        pending = buyer.post(path, json={"question": "怎么还没收到？买了几件？"}).json()
        cid = pending["conversation_id"]
        buyer.post(path, json={"question": order.order_id, "conversation_id": cid})
        foreign = other.post(path, json={"question": "我的订单在哪？"}).json()["conversation_id"]
        payload = {"order_id": order.order_id, "reason": "请核对有没有签收"}
        preview_path = "/api/tickets/preview"
        self.assertEqual(buyer.post(preview_path, json={**payload, "conversation_id": foreign}).status_code, 409)
        response = buyer.post(preview_path, json={**payload, "conversation_id": cid})
        self.assertEqual(response.status_code, 200)
        preview = response.json()
        snapshot = preview["handoff"]
        self.assertEqual(snapshot["buyer_request"], payload["reason"])
        self.assertIn("2018-10-09", "".join(snapshot["verified_facts"]))
        self.assertEqual(snapshot["turns"][0]["question"], "怎么还没收到？买了几件？")
        self.assertIn("1 件", "".join(snapshot["verified_facts"]))
        buyer.post(path, json={"question": "运费多少？", "conversation_id": cid})
        confirmation = {"confirmation_id": preview["confirmation_id"]}
        ticket = buyer.post("/api/tickets/confirm", json=confirmation).json()
        self.assertEqual(buyer.post("/api/tickets/confirm", json=confirmation).json(), ticket)
        number = ticket["ticket_no"]
        self.assertEqual(staff.get(f"/api/staff/tickets/{number}").json()["handoff"], snapshot)
        self.assertNotIn("handoff", buyer.get(f"/api/tickets/{number}/progress").json())
        self.assertEqual(other.get(f"/api/tickets/{number}/progress").status_code, 404)
        self.assertEqual(store.writes, 1)

    def test_staff_history_permissions_filters_and_cursor(self):
        """只有客服可以搜索历史，编号和分页游标可保留 BIGINT 精度。"""
        store = MemoryTickets()
        created_at = datetime(2026, 11, 16, tzinfo=timezone.utc)
        rows = [dict(ticket_no=number, order_id="OL-002F19A65A", reason="复核物流", status="resolved", created_at=created_at)
                for number in (2**63 - 1, 10)]
        store.search = Mock(return_value=rows)
        self.app = create_app(tickets=store, access=DemoAccess(self.credentials))
        staff = self.client_for("staff")
        buyer = self.client_for("buyer-a")
        path = "/api/staff/tickets/search"
        params = {"status": "resolved", "order_id": "ol-002f19a65a", "limit": 1}
        self.assertEqual(buyer.get(path, params=params).status_code, 403)
        self.assertEqual(staff.get(path, params={**params, "limit": 101}).status_code, 400)
        self.assertEqual(staff.get(path, params={**params, "order_id": "A102 OR 1=1"}).status_code, 400)
        store.search.assert_not_called()
        response = staff.get(path, params=params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["tickets"][0]["ticket_no"], str(2**63 - 1))
        cursor = response.json()["next_before"]
        self.assertEqual(cursor, str(2**63 - 1))
        store.search.assert_called_once_with(status="resolved", order_id="OL-002F19A65A", before=None, limit=2)
        store.search.return_value = rows[1:]
        second = staff.get(path, params={**params, "before": cursor}).json()
        self.assertEqual(second["tickets"][0]["ticket_no"], "10")
        self.assertIsNone(second["next_before"])

    def test_ticket_confirm_progress_and_staff_resolution(self):
        """只有工单所属买家可确认和查进度，客服身份由会话决定。"""
        payload = {"order_id": "A102", "reason": "权限测试"}
        self.assertEqual(self.buyer_b.post("/api/tickets/preview", json=payload).status_code, 404)
        preview = self.buyer_a.post("/api/tickets/preview", json=payload).json()
        confirm = {"confirmation_id": preview["confirmation_id"]}
        self.assertEqual(self.buyer_b.post("/api/tickets/confirm", json=confirm).status_code, 409)
        ticket = self.buyer_a.post("/api/tickets/confirm", json=confirm).json()
        staff_path = f"/api/staff/tickets/{ticket['ticket_no']}"
        buyer_path = f"/api/tickets/{ticket['ticket_no']}/progress"
        self.assertEqual(self.buyer_b.post("/api/tickets/confirm", json=confirm).status_code, 409)
        self.assertEqual(self.buyer_b.get(buyer_path).status_code, 404)
        self.assertEqual(self.buyer_a.get(staff_path).status_code, 403)
        self.assertEqual(self.buyer_a.post(staff_path + "/resolve", json={"resolution": "复核"}).status_code, 403)
        self.assertEqual(self.staff.post(staff_path + "/resolve", json={
            "resolution": "已处理", "actor": "buyer-b",
        }).status_code, 200)
        self.assertEqual(self.staff.get(staff_path).json()["resolved_by"], "staff")
        self.assertEqual(self.buyer_a.get(buyer_path).json()["resolution"], "已处理")
        self.assertEqual(self.staff.post(staff_path + "/resolve", json={"resolution": "第二次"}).status_code, 409)

    def test_olist_overdue_ticket_reuses_confirmation_and_staff_flow(self):
        """所属买家可提交物流工单，客服可查看历史依据并处理。"""
        source_id = "002f19a65a" + "0" * 22
        overdue = LogisticsOrder(source_id, "shipped", date(2018, 9, 1), date(2018, 10, 9), None)
        delivered = LogisticsOrder("00010242fe" + "0" * 22, "delivered", date(2018, 9, 1),
                                   date(2018, 10, 9), date(2018, 10, 9))
        orders = {order.order_id: order for order in (overdue, delivered)}
        item = OrderItem(1, "a" * 32, "informatica_acessorios", Decimal("49.90"), Decimal("13.37"))
        self.app = create_app(tickets=MemoryTickets(), access=DemoAccess(self.credentials),
                              logistics_lookup=orders.get, items_lookup=lambda _: [item])
        buyer, other, staff = [self.client_for(name) for name in ("buyer-a", "buyer-b", "staff")]
        preview_path = "/api/tickets/preview"
        confirm_path = "/api/tickets/confirm"
        payload = {"order_id": overdue.order_id, "reason": "请复核超期未签收"}
        self.assertEqual(other.post(preview_path, json=payload).status_code, 404)
        signed_preview = buyer.post(preview_path, json={**payload, "order_id": delivered.order_id})
        self.assertEqual(signed_preview.status_code, 200)
        self.assertIn("实际签收日 2018-10-09。", signed_preview.json()["handoff"]["verified_facts"])
        response = buyer.post(preview_path, json={**payload, "order_id": overdue.order_id.lower()})
        self.assertEqual(response.status_code, 200)
        preview = response.json()
        self.assertEqual(preview["order_id"], overdue.order_id)
        self.assertEqual(preview["logistics"]["estimated_on"], "2018-10-09")
        confirmation = {"confirmation_id": preview["confirmation_id"]}
        self.assertEqual(other.post(confirm_path, json=confirmation).status_code, 409)
        ticket = buyer.post(confirm_path, json=confirmation).json()
        self.assertEqual(buyer.post(confirm_path, json=confirmation).json(), ticket)
        self.assertEqual(other.post(confirm_path, json=confirmation).status_code, 409)
        progress_path = f"/api/tickets/{ticket['ticket_no']}/progress"
        staff_path = f"/api/staff/tickets/{ticket['ticket_no']}"
        self.assertEqual(other.get(progress_path).status_code, 404)
        self.assertEqual(buyer.get(staff_path).status_code, 403)
        detail = staff.get(staff_path).json()
        self.assertEqual(detail["evidence"]["status"], "logistics_overdue")
        self.assertEqual(detail["evidence"]["logistics"]["estimated_on"], "2018-10-09")
        self.assertEqual(detail["olist_items"]["items"][0]["price"], "49.90")
        self.assertEqual(staff.post(staff_path + "/resolve", json={"resolution": "已核对物流历史"}).status_code, 200)
        self.assertEqual(staff.get(staff_path).json()["resolved_by"], "staff")
        self.assertEqual(buyer.get(progress_path).json()["resolution"], "已核对物流历史")

    def test_signed_order_chat_handoff_appears_in_after_sales_progress(self):
        """已签收订单从聊天转人工，客服处理结果和工单沿用同一订单。"""
        order = LogisticsOrder("00048cc3ae" + "0" * 22, "delivered", date(2017, 5, 15),
                               date(2017, 6, 6), date(2017, 5, 22))
        router = Mock(return_value={"name": "select_order_tools", "arguments": '{"operations":["logistics"]}'})
        store = MemoryTickets()
        self.app = create_app(tickets=store, access=DemoAccess(self.credentials),
                              logistics_lookup={order.order_id: order}.get, olist_router=router)
        buyer, other, staff = [self.client_for(name) for name in ("buyer-a", "buyer-b", "staff")]
        chat = "/api/conversation"
        shared = buyer.post(chat + "/share-order", json={"order_id": order.order_id}).json()
        context = {"conversation_id": shared["conversation_id"], "active_order_id": order.order_id}
        reply = buyer.post(chat, json={**context, "question": "帮我转人工"}).json()
        self.assertEqual(reply["result"]["human_review"]["status"], "preview")
        self.assertEqual(store.writes, 0)
        progress_path = f"/api/buyer/orders/{order.order_id}/after-sales"
        self.assertIsNone(buyer.get(progress_path).json()["platform"])
        preview = buyer.post("/api/tickets/preview", json={"order_id": order.order_id, "reason": "收到后发现商品损坏",
                                                           "conversation_id": shared["conversation_id"]}).json()
        self.assertIn("实际签收日 2017-05-22。", preview["handoff"]["verified_facts"])
        self.assertEqual(preview["handoff"]["turns"][0]["question"], "帮我转人工")
        submitted = buyer.post("/api/tickets/confirm", json={"confirmation_id": preview["confirmation_id"]}).json()
        ticket_no = submitted["ticket_no"]
        pending = buyer.get(progress_path).json()["platform"]
        self.assertEqual((pending["ticket_no"], pending["status"]), (str(ticket_no), "pending"))
        self.assertEqual(other.get(progress_path).status_code, 404)
        response = staff.post(f"/api/staff/tickets/{ticket_no}/resolve", json={"resolution": "已核实收到商品损坏"})
        self.assertEqual(response.status_code, 200)
        finished = buyer.get(progress_path).json()["platform"]
        self.assertEqual((finished["status"], finished["resolution"]), ("resolved", "已核实收到商品损坏"))

    def test_two_olist_previews_reuse_the_same_ticket_and_resume_after_restart(self):
        """两个标签页各预览一次，两个确认 ID 也只落一张工单。"""
        order = LogisticsOrder("002f19a65a" + "0" * 22, "shipped", date(2018, 9, 1), date(2018, 10, 9), None)
        store = MemoryTickets()
        access = DemoAccess(self.credentials)
        self.app = create_app(tickets=store, access=access, logistics_lookup=lambda _: order)
        buyer, other = [self.client_for(name) for name in ("buyer-a", "buyer-b")]
        draft_url = "/api/tickets/preview"
        confirm_url = "/api/tickets/confirm"
        first, second = [buyer.post(draft_url, json={"order_id": order.order_id, "reason": reason}).json()
                         for reason in ("还没收到", "请核对物流")]
        self.assertNotEqual(first["confirmation_id"], second["confirmation_id"])
        first_ticket = buyer.post(confirm_url, json={"confirmation_id": first["confirmation_id"]}).json()
        second_payload = {"confirmation_id": second["confirmation_id"]}
        self.assertEqual(buyer.post(confirm_url, json=second_payload).json(), first_ticket)
        self.assertEqual(store.writes, 1)
        store.resolve(first_ticket["ticket_no"], "已复核")
        preview = buyer.post(draft_url, json={"order_id": order.order_id, "reason": "再问一次"}).json()
        self.assertEqual(preview["existing_ticket_no"], str(first_ticket["ticket_no"]))
        self.assertEqual(preview["existing_ticket_status"], "resolved")
        self.assertNotIn("confirmation_id", preview)
        self.assertEqual(other.post(confirm_url, json=second_payload).status_code, 409)
        self.app = create_app(tickets=store, access=access, logistics_lookup=lambda _: order)
        restarted = self.client_for("buyer-a")
        self.assertEqual(restarted.post(confirm_url, json=second_payload).json()["ticket_no"], first_ticket["ticket_no"])


class SupportTicketTests(unittest.TestCase):
    """测试人机确认流程的 HTTP 行为。"""

    def setUp(self):
        """创建带模拟工单仓储的 FastAPI 测试客户端。"""
        self.store = MemoryTickets()
        self.client = self.enterContext(TestClient(create_app(tickets=self.store)))

    def preview(self, order_id="A102", reason="想请客服复核退货期限"):
        """请求工单预览并返回 HTTP 响应。"""
        return self.client.post("/api/tickets/preview", json={"order_id": order_id, "reason": reason})

    def confirm(self, confirmation_id):
        """提交指定预览的确认 ID。"""
        return self.client.post("/api/tickets/confirm", json={"confirmation_id": confirmation_id})

    def test_preview_then_confirm_is_idempotent(self):
        """预览不落库，确认和重试最终生成同一张工单。"""
        response = self.preview(order_id=" a102 ", reason="  想请客服复核  ")
        self.assertEqual(response.status_code, 200)
        draft = response.json()
        self.assertEqual((draft["order_id"], draft["reason"]), ("A102", "想请客服复核"))
        self.assertEqual(self.store.records, {})
        ticket = self.confirm(draft["confirmation_id"])
        repeated = self.confirm(draft["confirmation_id"])
        self.assertEqual(ticket.status_code, 200)
        self.assertEqual(repeated.json(), ticket.json())
        self.assertEqual(ticket.json()["ticket_no"], 1)
        self.assertEqual(ticket.json()["status"], "pending")
        self.assertEqual(self.store.writes, 1)
        # 工单记录独立于 LangGraph 的内存检查点。
        with TestClient(create_app(tickets=self.store)) as restarted:
            result = restarted.post("/api/tickets/confirm", json={"confirmation_id": draft["confirmation_id"]})
        self.assertEqual(result.json(), ticket.json())

    def test_unknown_order_and_expired_preview(self):
        """查不到订单或预览时返回可读错误。"""
        self.assertEqual(self.preview(order_id="A999").status_code, 404)
        self.assertEqual(self.preview(reason="   ").status_code, 400)
        self.assertEqual(self.confirm(str(uuid4())).status_code, 409)
        self.assertEqual(self.store.writes, 0)

    def test_two_shop_previews_reuse_open_ticket_and_allow_new_after_resolution(self):
        """两个独立预览和确认共享一张待处理工单，结案后可提交新的问题。"""
        first = self.preview(reason="问题一").json()
        second = self.preview(reason="另一窗口重复操作").json()
        ticket = self.confirm(first["confirmation_id"]).json()
        self.assertEqual(self.confirm(second["confirmation_id"]).json(), ticket)
        self.assertEqual(self.store.writes, 1)
        self.assertEqual(self.preview().json(), {"order_id": "A102", "reason": "问题一",
                                                "existing_ticket_no": str(ticket["ticket_no"]),
                                                "existing_ticket_status": "pending"})
        self.store.resolve(ticket["ticket_no"], "已处理")
        followup = self.preview(reason="新问题").json()
        new_ticket = self.confirm(followup["confirmation_id"]).json()
        self.assertNotEqual(new_ticket["ticket_no"], ticket["ticket_no"])
        self.assertEqual(self.confirm(second["confirmation_id"]).json()["ticket_no"], ticket["ticket_no"])

    def test_can_retry_after_failed_write(self):
        """写入失败后，原确认 ID 可用于继续提交。"""
        confirmation = self.preview().json()["confirmation_id"]
        self.store.fail_next_write = True
        self.assertEqual(self.confirm(confirmation).status_code, 503)
        self.assertEqual(self.confirm(confirmation).status_code, 200)
        self.assertEqual(len(self.store.records), 1)

    def test_staff_resolves_and_buyer_reads_saved_progress(self):
        """覆盖客服查看依据、保存和买家查询，并检查重复处理冲突。"""
        draft = self.preview().json()
        self.confirm(draft["confirmation_id"])
        queued = self.client.get("/api/staff/tickets").json()
        self.assertEqual([ticket["ticket_no"] for ticket in queued], [1])
        detail = self.client.get("/api/staff/tickets/1").json()
        self.assertEqual(detail["reason"], "想请客服复核退货期限")
        self.assertEqual(detail["evidence"]["signed_on"], "2026-11-05")
        self.assertEqual(detail["evidence"]["return_window_days"], 7)
        before = self.client.get("/api/tickets/1/progress").json()
        self.assertEqual(before["status"], "pending")
        self.assertNotIn("reason", before)
        self.assertNotIn("order_id", before)
        resolved = self.client.post("/api/staff/tickets/1/resolve", json={"resolution": " 已复核，超过期限。 "})
        self.assertEqual(resolved.status_code, 200)
        self.assertEqual(resolved.json()["status"], "resolved")
        self.assertTrue(resolved.json()["resolved_at"])
        repeated = self.client.post("/api/staff/tickets/1/resolve", json={"resolution": "不应覆盖"})
        self.assertEqual(repeated.status_code, 409)
        self.assertEqual(self.client.get("/api/staff/tickets").json(), [])
        with TestClient(create_app(tickets=self.store)) as restarted:
            after = restarted.get("/api/tickets/1/progress").json()
        self.assertEqual(after, resolved.json())
        self.assertEqual(after["resolution"], "已复核，超过期限。")

    def test_invalid_ticket_progress_and_resolution(self):
        """未知或不合法的工单号，以及空处理说明，都应提示错误。"""
        self.assertEqual(self.client.get("/api/tickets/999/progress").status_code, 404)
        self.assertEqual(self.client.get("/api/tickets/0/progress").status_code, 400)
        self.assertEqual(self.client.post("/api/staff/tickets/999/resolve", json={"resolution": "复核"}).status_code, 404)
        self.assertEqual(self.client.post("/api/staff/tickets/1/resolve", json={"resolution": " "}).status_code, 400)
