/** 商城的路由、接口与展示格式，为 React 视图提供共用函数。 */
export const stages = {preparing: "待发货", in_transit: "运输中", delivered: "已签收", refunded: "已全额登记退款"};
export const reasons = {wrong_purchase: "买错或不想要", quality: "质量问题", wrong_item: "发错商品", missing: "少件漏发",
  not_received: "没有收到", cancel: "取消未发货商品"};
export const actions = {carrier_return:"登记快递已受理退回", carrier_interception_failed:"登记拦截失败",
  buyer_received:"我已签收，改为自行寄回",
  submit: "向店铺提交", approve: "同意申请", reject: "拒绝申请", negotiate: "提出协商方案",
  request_evidence: "要求补充凭证", provide_evidence: "提交补充凭证", accept_offer: "接受店铺方案",
  reject_offer: "拒绝店铺方案", close_prompt: "撤回当前要求或方案", reimburse: "登记垫付返还",
  revise: "补充说明后重新提交", withdraw: "撤回申请", evidence: "补充凭证说明", dispute: "申请平台介入",
  review: "申请平台复核", provide_return_details: "补充寄回信息",
  confirm_delivery_address: "确认收货信息", confirm_delivery: "确认收货",
  report_delivery_problem: "反馈包裹问题", resume_delivery: "回复并继续等待收货", reissue_delivery: "安排重新寄出",
  request_delivery_review: "申请平台处理包裹问题", platform_dispatch: "要求店铺发出包裹",
  platform_reissue: "要求店铺重寄", platform_continue_delivery: "核实后继续跟踪包裹",
  ship: "登记寄回", receive: "确认收到退回商品", refund: "发起退款", record_refund_result: "记录退款结果", replace: "寄出换货商品",
  repair_complete: "寄回维修商品", reship: "登记补发", support_buyer: "支持买家并恢复办理",
  support_seller: "维持店铺结论并关闭争议"};
export const eventRoles = {buyer: "买家", merchant: "店铺客服", staff: "平台客服"};
export const mailingActions = ["ship", "replace", "repair_complete", "reship"];
export const dispatchActions = ["replace", "repair_complete", "reship"];
export const deliveryReviewDecisions = ["platform_dispatch", "platform_reissue", "platform_continue_delivery"];
export const refundStages = {pending: "退款处理中", failed: "退款失败 · 待店铺重试", succeeded: "退款成功"};
export const outboundActions = ["confirm_delivery_address", "confirm_delivery", "report_delivery_problem",
  "resume_delivery", "reissue_delivery", "request_delivery_review", ...dispatchActions, ...deliveryReviewDecisions];
export const deliveryProblems = {not_dispatched: "店铺逾期未发", not_received: "未收到售后包裹",
  wrong_item: "收到的商品发错", damaged: "收到的商品破损"};
export const offerNames = {partial_refund: "留货部分退款", refund_only: "仅退款", return_refund: "退货退款",
  exchange: "换货", repair: "维修", reship: "补发"};
export const returnOffers = ["return_refund", "exchange", "repair"];
export const photoActions = ["evidence", "provide_evidence", "dispute", "review", "revise", "reject", "report_delivery_problem", "request_delivery_review"];
export const replyActions = ["provide_evidence", "accept_offer", "reject_offer", "close_prompt"];
export const orderPattern = /^CN-[0-9]{4,10}$/;
export const casePattern = /^[1-9][0-9]{0,18}$/;

export const routes = {
  buyer: [["我的订单", "/mall/orders", "箱"], ["我的售后", "/mall/after-sales", "↶"]],
  merchant: [["商品售后", "/merchant/mall", "↶"], ["本店订单", "/merchant/mall/orders", "箱"], ["店铺条款", "/merchant/mall/policies", "册"]],
  staff: [["平台介入", "/staff/disputes", "↶"], ["商城档案", "/staff/mall/catalog", "册"]],
};

/** 所有查询、写入和会话续跳仍由现有 FastAPI 接口处理。 */
export async function request(path, payload) {
  const options = payload === undefined ? undefined : {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload),
  };
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401) {
      location.replace("/login?" + new URLSearchParams({next: location.pathname + location.search}));
    }
    const error = new Error(data.message ?? (typeof data.detail === "string" ? data.detail : "请检查输入后重试。"));
    error.status = response.status;
    error.fields = data.field_errors;
    throw error;
  }
  return data;
}

/** 站内选择保留 Ctrl/Command 点击和中键打开新标签页。 */
export function openInline(event, open) {
  if (event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  open();
}

export function caseUrl(role, number, action) {
  const path = {buyer: "/mall/after-sales", merchant: "/merchant/mall", staff: "/staff/disputes"}[role];
  const search = new URLSearchParams({case: number});
  if (action) search.set("action", action);
  return path + "?" + search;
}

export function orderUrl(order, item) {
  const search = new URLSearchParams({order});
  if (item) search.set("item", item);
  return "/mall/orders?" + search;
}

export function interventionAction(state) {
  return {available: "dispute", review_available: "review", delivery_review_available: "request_delivery_review"}[state];
}

export function actionLabel(action, editingAddress, retryRefund = false) {
  if (action === "confirm_delivery_address" && editingAddress) return "修改收货信息";
  if (action === "refund" && retryRefund) return "重试退款";
  return actions[action];
}

const money = new Intl.NumberFormat("zh-CN", {style: "currency", currency: "CNY"});
const clock = new Intl.DateTimeFormat("zh-CN", {dateStyle: "short", timeStyle: "short", timeZone: "Asia/Shanghai"});
export const yuan = amount => amount == null ? "待查" : money.format(amount);
export const localTime = value => value ? clock.format(new Date(value)) : "";
export const formatAddress = value => [value.recipient, value.phone, value.address].join(" · ");

/** 网络重试复用同一提交编号，内容改变后再生成新编号。 */
export function submissionKey(draft, payload) {
  const signature = JSON.stringify(payload);
  if (draft.current?.signature !== signature) draft.current = {signature, id: crypto.randomUUID()};
  return draft.current.id;
}

/** 将相机照片缩放并重新编码，上传时不保留原始 EXIF。 */
export async function encodePhotos(input) {
  const files = Array.from(input.files);
  if (files.length > 3) throw new Error("每次最多上传 3 张照片。");
  const photos = [];
  for (const file of files) {
    if (!["image/jpeg", "image/png"].includes(file.type) || file.size > 16_000_000) {
      throw new Error("请选择 JPEG 或 PNG 格式照片，原图不得超过 16 MB。");
    }
    const image = await createImageBitmap(file).catch(() => { throw new Error("无法读取选中的照片。"); });
    const canvas = document.createElement("canvas");
    const scale = Math.min(1, 1600 / Math.max(image.width, image.height));
    canvas.width = Math.max(1, Math.round(image.width * scale));
    canvas.height = Math.max(1, Math.round(image.height * scale));
    const context = canvas.getContext("2d", {alpha: false});
    try {
      context.fillStyle = "#fff";
      context.fillRect(0, 0, canvas.width, canvas.height);
      context.drawImage(image, 0, 0, canvas.width, canvas.height);
    } finally {
      image.close();
    }
    const data = canvas.toDataURL("image/jpeg", 0.78).split(",")[1];
    if (!data || data.length > 1_333_336) throw new Error("照片过大，请缩小后重试。");
    photos.push({mime: "image/jpeg", data});
  }
  return photos;
}

