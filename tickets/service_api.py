"""提供买家申请、寄回登记和客服分阶段处理接口。"""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request

from app.access import Account
from app.orders.catalog import OrderCatalog
from app.orders.logistics import LogisticsOrder
from app.storage.postgres_service_requests import PostgresServiceRequests
from app.tickets.service_requests import (
    ReturnShipment, ServiceApplication, ServiceError, ServiceReason, ServiceStatus, StaffServiceUpdate, assess_return_freight,
)


JDNumber = Annotated[str, Path(pattern=r"^[jJ][dD]-[0-9a-fA-F]{12}$")]
ServiceNumber = Annotated[int, Path(gt=0, le=2**63 - 1)]
Cursor = Annotated[int | None, Query(gt=0, le=2**63 - 1)]


def service_router(store: PostgresServiceRequests | None, catalog: OrderCatalog,
                   require_account: Callable[[Request, str], Account]) -> APIRouter:
    """绑定现有账号和订单授权，建立独立的商品售后入口。"""
    router = APIRouter()

    def repository() -> PostgresServiceRequests:
        """在未接入订单数据库时返回明确信息。"""
        if store is None:
            raise HTTPException(503, "当前环境未接入商品售后服务。")
        return store

    def buyer_order(request: Request, order_id: str) -> tuple[Account, LogisticsOrder]:
        """从当前账号可见的目录核对这笔京东订单。"""
        buyer = require_account(request, "buyer")
        order = catalog.find(order_id.upper(), buyer.account_id)
        if not isinstance(order, LogisticsOrder) or order.origin != "jd":
            raise HTTPException(404, "查不到这笔订单。")
        return buyer, order

    @router.get("/api/buyer/service-requests")
    def list_buyer(request: Request, order_id: str | None = None, before: Cursor = None) -> dict:
        """仅分页返回自己的售后申请。"""
        buyer = require_account(request, "buyer")
        if order_id is not None:
            _, order = buyer_order(request, order_id)
            order_id = order.order_id
        return repository().page(buyer_id=buyer.account_id, order_id=order_id, before=before)

    @router.get("/api/buyer/orders/{order_id}/return-freight")
    def quote(request: Request, order_id: JDNumber, reason: ServiceReason | None = None) -> dict:
        """按当前订单提供适用的退货费用答复。"""
        _, order = buyer_order(request, order_id)
        practice = repository().freight_practice(order.order_id, reason) if reason else None
        return assess_return_freight(order, reason, practice)

    @router.post("/api/buyer/orders/{order_id}/service-requests")
    def submit(request: Request, order_id: JDNumber, form: ServiceApplication) -> dict:
        """提交经过买家确认的商品售后单。"""
        buyer, order = buyer_order(request, order_id)
        return repository().create(buyer.account_id, order.order_id, form)

    @router.get("/api/buyer/service-requests/{number}")
    def buyer_details(request: Request, number: ServiceNumber) -> dict:
        """读取自身售后单和实际状态时间线。"""
        buyer = require_account(request, "buyer")
        return repository().get(number, buyer.account_id)

    @router.post("/api/buyer/service-requests/{number}/shipment")
    def ship(request: Request, number: ServiceNumber, form: ReturnShipment) -> dict:
        """只允许售后单所属买家在批准后登记寄回。"""
        buyer = require_account(request, "buyer")
        return repository().ship(number, buyer.account_id, form)

    @router.get("/api/staff/service-requests")
    def list_staff(request: Request, status: ServiceStatus | None = None, before: Cursor = None) -> dict:
        """按办理状态分页读取平台的售后处理队列。"""
        require_account(request, "staff")
        return repository().page(status=status, before=before)

    @router.get("/api/staff/service-requests/{number}")
    def staff_details(request: Request, number: ServiceNumber) -> dict:
        """让平台客服核对申请商品、原因和已发生的办理记录。"""
        require_account(request, "staff")
        return repository().get(number)

    @router.post("/api/staff/service-requests/{number}/actions")
    def staff_update(request: Request, number: ServiceNumber, form: StaffServiceUpdate) -> dict:
        """记录客服审核、收件或办结，由仓储检查合法流转。"""
        staff = require_account(request, "staff")
        return repository().staff_update(number, staff.account_id, form)

    return router
