/** 展示商品售后的申请、时间线和与角色匹配的下一步。 */
const onStaff = location.pathname.startsWith("/staff/");
const route = onStaff ? "/staff/service-requests" : "/service-requests";
const api = onStaff ? "/api/staff/service-requests" : "/api/buyer/service-requests";
const queryParams = new URL(location.href).searchParams;
const orderCode = onStaff ? null : queryParams.get("order_id");
const serviceNode = id => document.getElementById("service-" + id);
const statusNames = {submitted:"待审核", awaiting_return:"待寄回", return_sent:"待确认收件", received:"待办结",
  completed:"已办结", rejected:"审核未通过"};
const kindNames = {return:"退货退款", exchange:"换货", repair:"维修"};
const reasonNames = {wrong_purchase:"买错了或不想要", quality:"商品有质量问题", wrong_item:"发错或少发", other:"其他原因"};
const actions = {submitted:[["approve", "审核通过"], ["reject", "审核不通过"]],
  return_sent:[["receive", "确认已收件"]], received:[["complete", "办结售后"]]};
let orderData;
let caseData;
let listCursor;
let listRevision = 0;
let detailsRevision = 0;
let freightRevision = 0;
let draftKey;
let draftPayload;
let archivedService = false;

/** 发送已有登录会话的请求并读取后端中文错误。 */
async function serviceFetch(path, payload) {
  const options = payload === undefined ? undefined : {method:"POST", headers:{"Content-Type":"application/json"},
    body:JSON.stringify(payload)};
  const response = await sessionFetch(path, options);
  const body = await response.json();
  if (!response.ok) throw Error(body.message || body.detail || "操作暂未完成，请重试。");
  return body;
}

/** 生成当前角色可以直接打开的售后单地址。 */
function caseLink(number) {
  const params = new URLSearchParams();
  if (orderCode) params.set("order_id", orderCode);
  params.set("request", number);
  return route + "?" + params;
}

/** 把实际阶段记录按时间线展示。 */
function renderCase(record) {
  if (caseData?.request_no !== record.request_no) {
    serviceNode("shipment").reset();
    serviceNode("staff-action").reset();
  }
  caseData = record;
  serviceNode("detail").hidden = false;
  serviceNode("detail-title").textContent = "售后单 #" + record.request_no;
  serviceNode("detail-status").textContent = statusNames[record.status];
  serviceNode("detail-item").textContent = [kindNames[record.kind], reasonNames[record.reason],
    "商品 " + record.sku_id.toUpperCase(), record.quantity + " 件", onStaff ? record.buyer_id : null].filter(Boolean).join(" · ");
  const timeline = serviceNode("events");
  timeline.replaceChildren();
  for (const entry of record.events) {
    const row = document.createElement("li");
    const title = document.createElement("strong");
    const date = document.createElement("small");
    const message = document.createElement("p");
    title.textContent = statusNames[entry.status];
    date.textContent = new Date(entry.created_at).toLocaleString("zh-CN");
    message.textContent = entry.note + (entry.tracking ? `\n${entry.carrier} · ${entry.tracking}` : "");
    row.append(title, date, message);
    timeline.append(row);
  }
  const choices = actions[record.status] ?? [];
  serviceNode("staff-action").hidden = !onStaff || !choices.length;
  serviceNode("shipment").hidden = archivedService || onStaff || record.status !== "awaiting_return";
  serviceNode("next-action").replaceChildren(...choices.map(([value, label]) => new Option(label, value)));
  serviceNode("action-note").placeholder = record.status === "submitted" ? "通过时填写寄回方式和注意事项，拒绝时说明原因" : "例如收件核验或实际办理结果";
  const links = serviceNode("detail-links");
  const orderLink = document.createElement("a");
  orderLink.href = (onStaff ? "/staff/orders/" : "/orders/") + encodeURIComponent(record.order_id);
  orderLink.textContent = "查看原订单";
  links.replaceChildren(orderLink);
}

/** 从用户选择或 URL 恢复售后详情。 */
async function loadCase(number) {
  const revision = ++detailsRevision;
  try {
    const record = await serviceFetch(api + "/" + encodeURIComponent(number));
    if (revision !== detailsRevision) return;
    serviceNode("action-feedback").textContent = "";
    renderCase(record);
    history.replaceState(null, "", caseLink(number));
  } catch (error) {
    if (revision === detailsRevision) serviceNode("list-feedback").textContent = error.message;
  }
}

/** 刷新列表或继续读取下一页，既有详情仍可查看。 */
async function loadServiceList(append = false) {
  const revision = ++listRevision;
  const params = new URLSearchParams();
  if (orderCode) params.set("order_id", orderCode);
  if (onStaff && serviceNode("status").value) params.set("status", serviceNode("status").value);
  if (append && listCursor) params.set("before", listCursor);
  const feedback = serviceNode("list-feedback");
  const list = serviceNode("list");
  feedback.textContent = "正在读取售后单…";
  serviceNode("next").disabled = true;
  try {
    const page = await serviceFetch(api + "?" + params);
    if (revision !== listRevision) return;
    if (!append) list.replaceChildren();
    for (const record of page.requests) {
      const row = document.createElement("li");
      const title = document.createElement("a");
      const state = document.createElement("span");
      const item = document.createElement("small");
      title.href = caseLink(record.request_no);
      title.textContent = kindNames[record.kind] + " #" + record.request_no;
      title.addEventListener("click", event => {
        if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        event.preventDefault(); loadCase(record.request_no);
      });
      state.className = "service-badge";
      state.textContent = statusNames[record.status];
      item.textContent = record.order_id + " · 商品 " + record.sku_id.toUpperCase() + " × " + record.quantity;
      row.append(title, state, item);
      list.append(row);
    }
    listCursor = page.next_before;
    serviceNode("next").hidden = !listCursor;
    feedback.textContent = list.children.length ? "" : "暂无售后单。";
  } catch (error) {
    if (revision === listRevision) feedback.textContent = error.message;
  } finally {
    if (revision === listRevision) serviceNode("next").disabled = false;
  }
}

/** 更新所选商品可输入的最大件数。 */
function syncItemQuantity() {
  const selected = orderData?.items?.items.find(item => String(item.item_no) === serviceNode("item").value);
  serviceNode("quantity").max = selected?.quantity ?? 1;
  serviceNode("quantity").value = 1;
}

/** 按买家选定的退货原因展示已核实的费用和值的来源。 */
async function refreshReturnFreight() {
  const revision = ++freightRevision;
  const box = serviceNode("freight");
  box.hidden = serviceNode("kind").value !== "return";
  if (box.hidden) return;
  box.textContent = "正在核对退货运费…";
  const params = new URLSearchParams();
  const reason = serviceNode("reason").value;
  if (reason) params.set("reason", reason);
  try {
    const quote = await serviceFetch("/api/buyer/orders/" + encodeURIComponent(orderCode) + "/return-freight?" + params);
    if (revision !== freightRevision) return;
    box.textContent = (quote.practice ? "费用演练 · " : "") + quote.message;
  } catch (error) {
    if (revision === freightRevision) box.textContent = error.message;
  }
}

/** 提交同一份表单时复用请求编号，防止网络重试重复创建。 */
async function submitService(event) {
  event.preventDefault();
  const payload = {item_no:Number(serviceNode("item").value), quantity:Number(serviceNode("quantity").value),
    kind:serviceNode("kind").value, reason:serviceNode("reason").value, description:serviceNode("description").value.trim()};
  const fingerprint = JSON.stringify(payload);
  if (draftPayload !== fingerprint) { draftKey = crypto.randomUUID(); draftPayload = fingerprint; }
  const button = event.currentTarget.querySelector('button[type="submit"]');
  if (button.disabled) return;
  button.disabled = true;
  serviceNode("form-feedback").textContent = "正在提交申请…";
  try {
    const record = await serviceFetch("/api/buyer/orders/" + encodeURIComponent(orderCode) + "/service-requests",
      {...payload, request_id:draftKey});
    serviceNode("form-feedback").textContent = "已提交售后单 #" + record.request_no + "，请查看办理进度。";
    serviceNode("description").value = "";
    draftPayload = null;
    draftKey = null;
    ++detailsRevision;
    renderCase(record);
    history.replaceState(null, "", caseLink(record.request_no));
    await loadServiceList();
  } catch (error) {
    serviceNode("form-feedback").textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

/** 买家登记寄回或客服提交处理结果。 */
async function updateService(event, shipment) {
  event.preventDefault();
  if (!caseData) return;
  const number = caseData.request_no;
  const button = event.currentTarget.querySelector('button[type="submit"]');
  if (button.disabled) return;
  button.disabled = true;
  try {
    const payload = shipment ? {carrier:serviceNode("carrier").value, tracking:serviceNode("tracking").value} :
      {action:serviceNode("next-action").value, note:serviceNode("action-note").value};
    const record = await serviceFetch(api + "/" + encodeURIComponent(number) + (shipment ? "/shipment" : "/actions"), payload);
    if (caseData?.request_no === number) {
      renderCase(record);
      if (shipment) serviceNode("shipment").reset();
      serviceNode("action-note").value = "";
      serviceNode("action-feedback").textContent = "办理记录已更新。";
    }
    await loadServiceList();
  } catch (error) {
    if (caseData?.request_no === number) serviceNode("action-feedback").textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

/** 初始化工作台，买家从订单进入时显示可申请的原始明细。 */
async function loadServiceWorkbench() {
  try {
    archivedService = Boolean((await serviceFetch("/api/config")).mall_enabled);
  } catch (error) {
    serviceNode("list-feedback").textContent = error.message;
    return;
  }
  if (onStaff) {
    serviceNode("title").textContent = "商品售后办理";
    serviceNode("back").textContent = "平台工单";
    serviceNode("back").href = "/staff";
    serviceNode("home").href = "/staff";
    serviceNode("index").href = route;
    serviceNode("index").textContent = "商品售后";
    serviceNode("filter").hidden = false;
  }
  serviceNode("refresh").addEventListener("click", () => { loadServiceList(); if (caseData) loadCase(caseData.request_no); });
  serviceNode("next").addEventListener("click", () => loadServiceList(true));
  serviceNode("status").addEventListener("change", () => loadServiceList());
  const selected = queryParams.get("request");
  if (archivedService) {
    serviceNode("title").textContent = "历史商品售后";
    serviceNode("back").textContent = "商城售后";
    serviceNode("back").href = "/mall/after-sales";
    serviceNode("context").hidden = false;
    serviceNode("context").textContent = "查看既有历史记录。新申请请从商城订单发起。";
  }
  loadServiceList();
  if (selected) loadCase(selected);
  if (!orderCode || archivedService) return;
  try {
    orderData = await serviceFetch("/api/buyer/orders/" + encodeURIComponent(orderCode));
    if (orderData.kind !== "jd" || !orderData.items?.items.length) throw Error("这笔订单暂时没有可申请的商品明细。");
    serviceNode("title").textContent = "申请商品售后";
    serviceNode("context").hidden = false;
    serviceNode("context").textContent = orderData.order_id + " · " + (orderData.seller_label || "京东历史订单") + " · 本地办理";
    serviceNode("item").replaceChildren(...orderData.items.items.map(item =>
      new Option((item.category_label ?? item.product_id) + " · " + item.quantity + " 件", item.item_no)));
    const reason = queryParams.get("reason");
    if (reasonNames[reason]) serviceNode("reason").value = reason;
    serviceNode("application").hidden = false;
    syncItemQuantity();
    refreshReturnFreight();
  } catch (error) {
    serviceNode("list-feedback").textContent = error.message;
  }
}

serviceNode("item").addEventListener("change", syncItemQuantity);
serviceNode("kind").addEventListener("change", refreshReturnFreight);
serviceNode("reason").addEventListener("change", refreshReturnFreight);
serviceNode("form").addEventListener("submit", submitService);
serviceNode("shipment").addEventListener("submit", event => updateService(event, true));
serviceNode("staff-action").addEventListener("submit", event => updateService(event, false));
loadServiceWorkbench();
