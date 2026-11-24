"""把商城订单、店铺售后与平台争议接入按账号隔离的 HTTP 接口。"""

from collections.abc import Callable
from typing import Annotated, Literal
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel, Field, StringConstraints
from fastapi.responses import FileResponse, RedirectResponse, Response

from app.access import Account
from app.policies.mall_preview import compare_policy_preview
from app.settings import ROOT
from app.storage.mall_directory import MallDirectory
from app.storage.mall_policies import MallPolicyBook, ShopPublication
from app.storage.mall_supplements import SupplementDraft, review_supplement, submit_supplement
from app.storage.postgres_mall import PostgresMall
from app.mall.rules import MallApplication, MallOperation, MallPackageApplication, MallQuestion
from app.mall.chat_actions import ChatConfirmation


OrderCode = Annotated[str, Path(pattern=r"^CN-[0-9]{4,10}$")]
SearchText = Annotated[str, Query(max_length=60)]
OrderCursor = Annotated[str, Query(pattern=r"^(?:CN-[0-9]{4,10})?$")]
CaseNumber = Annotated[int, Path(gt=0, le=2**63 - 1)]
PhotoNumber = Annotated[int, Path(gt=0, le=2**63 - 1)]
LineNumber = Annotated[int, Path(gt=0, le=2**31 - 1)]
CaseCursor = Annotated[int | None, Query(gt=0, le=2**63 - 1)]
Queue = Literal["all", "pending", "soon", "overdue"]


class ConsultationSelection(BaseModel):
    """买家选中的待办轮次及候选商品。"""

    pending_turn: Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]{0,17}$")]
    order_id: Annotated[str, StringConstraints(pattern=r"^CN-[0-9]{4,10}$")]
    item_no: int = Field(gt=0, le=2**31 - 1)


PAGE_ROLES = {"/mall/orders": "buyer", "/mall/after-sales": "buyer", "/mall/portfolio": "buyer",
              "/merchant/mall": "merchant", "/staff/disputes": "staff",
              "/staff/mall/catalog": "staff", "/merchant/mall/orders": "merchant", "/merchant/mall/policies": "merchant"}
MALL_HOME = {"buyer": "/mall/orders", "merchant": "/merchant/mall", "staff": "/staff/disputes"}


class SupplementDecision(BaseModel):
    """平台审核员的结论与说明。"""
    decision: Literal["approved", "rejected"]
    feedback: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class PolicyPreview(BaseModel):
    """商家选择本店商品和一条买家问法进行只读试查。"""
    sku: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=500)]
    hybrid: bool = False


def mall_router(store: PostgresMall, require_account: Callable[[Request, str], Account]) -> APIRouter:
    """绑定现有登录会话；所有权限边界在服务端和数据库查询中校验。"""
    router = APIRouter()
    directory = MallDirectory(store)
    policies = MallPolicyBook(store)

    @router.post("/api/merchant/mall/supplements/{shop_id}/preview")
    def preview_supplements(request: Request, shop_id: str, form: PolicyPreview):
        """在事务关闭后执行检索，语义模型加载期间不占用数据库连接。"""
        user = require_account(request, "merchant")
        item = policies.preview_item(user.account_id, shop_id, form.sku)
        return compare_policy_preview(item, form.question, form.hybrid)

    @router.post("/api/merchant/mall/supplements/{shop_id}")
    def send_supplement(request: Request, shop_id: str, form: SupplementDraft):
        """只允许商家给本店提交待审条款。"""
        user = require_account(request, "merchant")
        with store.connect() as conn:
            return submit_supplement(conn, shop_id, user.account_id, form)

    @router.get("/api/staff/mall/supplements")
    def pending_supplements(request: Request):
        """平台按提交顺序审核店铺说明。"""
        require_account(request, "staff")
        with store.connect() as conn:
            return conn.execute("""SELECT r.id,s.name,r.note,r.topic,r.title,r.sku,r.revises_id,
                previous.note AS previous_note FROM public.mall_supplement_reviews r
                LEFT JOIN public.mall_supplement_reviews previous ON previous.id=r.revises_id
                JOIN public.mall_shops s ON s.shop_id=r.shop_id
                WHERE r.decision IS NULL ORDER BY r.id LIMIT 50""").fetchall()

    @router.post("/api/staff/mall/supplements/{number}")
    def accept_supplement_review(request: Request, number: CaseNumber, form: SupplementDecision):
        """并发审核仅首个结论生效。"""
        user = require_account(request, "staff")
        with store.connect() as conn:
            return review_supplement(conn, number, user.account_id, form.decision, form.feedback)

    @router.get("/mall/orders", include_in_schema=False)
    @router.get("/mall/portfolio", include_in_schema=False)
    @router.get("/mall/after-sales", include_in_schema=False)
    @router.get("/merchant/mall", include_in_schema=False)
    @router.get("/staff/disputes", include_in_schema=False)
    @router.get("/staff/mall/catalog", include_in_schema=False)
    @router.get("/merchant/mall/orders", include_in_schema=False)
    @router.get("/merchant/mall/policies", include_in_schema=False)
    def workbench(request: Request):
        """打开当前角色的商城工作台。"""
        role = PAGE_ROLES[request.url.path]
        account = request.state.account
        if account is None:
            target = request.url.path + ("?" + request.url.query if request.url.query else "")
            return RedirectResponse("/login?" + urlencode({"next": target}), status_code=303)
        if account.role != role:
            return RedirectResponse(MALL_HOME[account.role], status_code=303)
        return FileResponse(ROOT / "web/commerce.html", media_type="text/html")

    @router.get("/parcel-studio.jpg", include_in_schema=False)
    def parcel_portrait():
        """登录和订单共用的包装摄影素材。"""
        return FileResponse(ROOT / "web/parcel-studio.jpg", media_type="image/jpeg")

    @router.get("/commerce-build/{asset}", include_in_schema=False)
    def commerce_asset(asset: Literal["workbench.js", "workbench.css"]):
        """提供 React 工作台的构建产物。"""
        media_type = "text/javascript" if asset == "workbench.js" else "text/css"
        return FileResponse(ROOT / "web/commerce-build" / asset, media_type=media_type)

    @router.get("/mall-directory.js", include_in_schema=False)
    def directory_script():
        """提供商城目录的翻页、查询和订单详情交互。"""
        return FileResponse(ROOT / "web/mall-directory.js", media_type="text/javascript")

    @router.get("/api/staff/mall/catalog/{kind}")
    def staff_directory(request: Request, kind: Literal["shops", "buyers", "orders"],
                        search: Annotated[str, Query(max_length=60)] = "",
                        page: Annotated[int, Query(ge=1, le=1000)] = 1):
        """在平台工作台查店铺、买家及订单目录。"""
        return directory.page(require_account(request, "staff"), kind, search, page)

    @router.get("/api/merchant/mall/catalog/orders")
    def merchant_orders(request: Request, search: Annotated[str, Query(max_length=60)] = "",
                        page: Annotated[int, Query(ge=1, le=1000)] = 1):
        """查当前店铺账号负责的购物订单。"""
        return directory.page(require_account(request, "merchant"), "orders", search, page)

    @router.get("/api/merchant/mall/policies")
    def merchant_policies(request: Request):
        """查看本人店铺的承诺、历史版本和平台保障。"""
        return policies.read(require_account(request, "merchant").account_id)

    @router.post("/api/merchant/mall/policies/{shop_id}")
    def publish_policy(request: Request, shop_id: str, form: ShopPublication):
        """追加本店新版本，保留所有已购订单的下单条款。"""
        return policies.release(require_account(request, "merchant").account_id, shop_id, form)

    def directory_order_route(role: str):
        """给平台与商家注册可查看相应订单的接口。"""
        @router.get(f"/api/{role}/mall/catalog/orders/{{order_id}}", name=f"{role}_mall_catalog_order")
        def read_order(request: Request, order_id: OrderCode):
            return directory.order(require_account(request, role), order_id)

    for directory_role in ("staff", "merchant"):
        directory_order_route(directory_role)

    @router.get("/mall.js", include_in_schema=False)
    def script():
        """提供商城订单和售后页面的交互。"""
        return FileResponse(ROOT / "web/mall.js", media_type="text/javascript")

    @router.get("/api/buyer/mall/orders")
    def orders(request: Request, prefix: SearchText = "", after: OrderCursor = "",
               stage: Literal["", "preparing", "in_transit", "delivered", "refunded"] = ""):
        """返回买家本人的商城订单，可按包裹状态或订单号筛选。"""
        return store.orders(require_account(request, "buyer").account_id, prefix, after, stage)

    @router.get("/api/buyer/mall/orders/{order_id}")
    def order(request: Request, order_id: OrderCode):
        """读取本单商品、包裹、购买时的条款和售后进度。"""
        return store.order(require_account(request, "buyer").account_id, order_id)

    @router.post("/api/buyer/mall/orders/{order_id}/items/{item_no}/consult")
    def consult(request: Request, response: Response, order_id: OrderCode, item_no: LineNumber, form: MallQuestion):
        """Agent 以订单中的商品、包裹和条款回答咨询。"""
        timings = {}
        result = store.consult(require_account(request, "buyer").account_id, order_id, item_no, form, timings=timings)
        response.headers["Server-Timing"] = ", ".join(f"{step};dur={ms:.2f}" for step, ms in timings.items())
        return result

    @router.get("/api/buyer/mall/orders/{order_id}/items/{item_no}/consult")
    def consultation_history(request: Request, order_id: OrderCode, item_no: LineNumber):
        """恢复买家自己的商品咨询历史。"""
        return store.consultation_history(require_account(request, "buyer").account_id, order_id, item_no)

    @router.post('/api/buyer/mall/orders/{order_id}/items/{item_no}/consult/actions/{draft_id}/confirm')
    def confirm_chat_action(request:Request,order_id:OrderCode,item_no:LineNumber,draft_id:UUID,form:ChatConfirmation):
        """仅登录买家明确确认后，执行其商品下保存的办理卡。"""
        return store.confirm_chat_action(require_account(request,'buyer').account_id,order_id,item_no,draft_id,form)

    @router.post("/api/buyer/mall/orders/{order_id}/items/{item_no}/consult/continue")
    def continue_consult(request: Request, order_id: OrderCode, item_no: LineNumber, selection: ConsultationSelection):
        """校验候选后续问，再返回已处理的目标商品。"""
        buyer = require_account(request, "buyer").account_id
        return store.continue_consultation(buyer, order_id, item_no, int(selection.pending_turn),
                                           selection.order_id, selection.item_no)

    @router.post("/api/buyer/mall/orders/{order_id}/cases")
    def submit(request: Request, order_id: OrderCode, form: MallApplication):
        """买家确认提交后创建归属到商品店铺的售后单。"""
        return store.submit(require_account(request, "buyer"), order_id, form)

    @router.post("/api/buyer/mall/orders/{order_id}/packages/{package_no}/cases")
    def submit_package(request: Request, order_id: OrderCode, package_no: LineNumber, form: MallPackageApplication):
        """向所属店铺一次提交该包裹内的全部商品。"""
        return store.submit(require_account(request, "buyer"), order_id, form, package_no=package_no)

    def role_routes(role: str):
        """为一个角色安装相同对象上的列表、详情与受控操作。"""
        prefix = f"/api/{role}/mall/cases"

        @router.get(prefix, name=f"mall_{role}_queue")
        def cases(request: Request, order_id: SearchText = "", before: CaseCursor = None, queue: Queue = "all"):
            """仅返回当前角色可处理或曾经参与的相关售后单。"""
            return store.page(require_account(request, role), order_id, before, queue)

        @router.get(prefix + "/{number}", name=f"mall_{role}_detail")
        def details(request: Request, number: CaseNumber):
            """读取当前角色有权访问的售后单和时间线。"""
            return store.detail(require_account(request, role), number)

        @router.get(prefix + "/{number}/photos/{photo_id}", name=f"mall_{role}_photo")
        def photo(request: Request, number: CaseNumber, photo_id: PhotoNumber):
            """仅向有权查看原单的账号提供图片内容。"""
            mime, content = store.photo(require_account(request, role), number, photo_id)
            return Response(content, media_type=mime, headers={
                "X-Content-Type-Options": "nosniff",
                "Cross-Origin-Resource-Policy": "same-origin",
                "Content-Security-Policy": "default-src 'none'; sandbox",
            })

        @router.post(prefix + "/{number}/actions", name=f"mall_{role}_action")
        def operate(request: Request, number: CaseNumber, form: MallOperation):
            """按当前阶段执行商家、买家或已介入平台的动作。"""
            return store.operate(require_account(request, role), number, form)

    for role in ("buyer", "merchant", "staff"):
        role_routes(role)
    return router

