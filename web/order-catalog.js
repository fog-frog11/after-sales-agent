/** 用服务端角色接口显示订单列表、详情以及对应处理入口。 */

const catalogStaff = document.body.dataset.orderRole === "staff";
const catalogPath = catalogStaff ? "/staff/orders" : "/orders";
const catalogApi = catalogStaff ? "/api/staff/orders" : "/api/buyer/orders";
const catalogLocation = new URL(location.href);
const catalogParams = catalogLocation.searchParams;
const catalogOrderPattern = /^(?:A\d{3}|OL-[0-9A-F]{10}|JD-[0-9A-F]{12})$/i;
const catalogKinds = {jd: "京东历史订单", olist: "Olist 历史订单", shop: "店铺订单"};
const catalogStatuses = {
  signed: "已签收", not_signed: "未签收", logistics_delivered: "按期签收",
  logistics_delivered_late: "延迟签收", logistics_overdue: "超期未签收",
  logistics_shipping: "运输中", logistics_cancelled: "已取消", logistics_unknown: "暂无物流状态",
};
const catalogStatusTones = {
  signed: "good", logistics_delivered: "good", logistics_delivered_late: "warn",
  logistics_overdue: "warn", logistics_cancelled: "cancelled",
};
const catalogNode = id => document.getElementById(id);
const catalogSelected = catalogLocation.pathname.slice(catalogPath.length + 1);
let catalogArchives = false;

/** 只携带页面支持的筛选条件，分页链接可刷新、可后退。 */
function catalogFilters() {
  const result = new URLSearchParams();
  for (const key of catalogStaff ? ["kind", "buyer_id", "order_id", "after"] : ["receipt", "order_id", "after"]) {
    const value = catalogParams.get(key)?.trim();
    if (value) result.set(key, value);
  }
  return result;
}

/** 生成指向列表或详情的本站链接，保留本页筛选。 */
function catalogHref(path, parameters, fragment = "") {
  const query = parameters.toString();
  return path + (query ? "?" + query : "") + fragment;
}

/** 使用现有会话处理登录失效，并展示服务端可读错误。 */
async function catalogGet(url) {
  const response = await sessionFetch(url);
  const body = await response.json();
  if (!response.ok) throw new Error(body.message || "查询失败，请重试。");
  return body;
}

/** 追加一个表格单元格并使用纯文本呈现数据。 */
function catalogCell(row, value, tag = "td") {
  const cell = document.createElement(tag);
  if (tag === "th") cell.scope = "row";
  cell.textContent = value;
  row.append(cell);
  return cell;
}

/** 在同一列表中区分来源、物流状态和实际记录的日期类型。 */
function catalogRenderList(data, filters) {
  const rows = catalogNode("catalog-rows");
  rows.replaceChildren();
  for (const order of data.orders) {
    const row = document.createElement("tr");
    const id = catalogCell(row, order.order_id, "th");
    id.className = "catalog-id";
    const source = document.createElement("small");
    source.textContent = order.seller_label || catalogKinds[order.kind];
    id.append(source);
    if (catalogStaff) catalogCell(row, order.buyer_id);
    const statusCell = catalogCell(row, "");
    const status = document.createElement("span");
    status.className = "catalog-status";
    status.dataset.tone = catalogStatusTones[order.status] || "neutral";
    status.textContent = catalogStatuses[order.status] || "暂无状态";
    statusCell.append(status);
    const date = order.purchased_on ? "下单 " + order.purchased_on :
      (order.signed_on ? "签收 " + order.signed_on : "—");
    catalogCell(row, date);
    const action = document.createElement("a");
    action.className = "catalog-link";
    action.textContent = "详情";
    action.setAttribute("aria-label", "查看订单 " + order.order_id + " 的详情");
    action.href = catalogHref(catalogPath + "/" + encodeURIComponent(order.order_id), filters, "#catalog-detail");
    if (order.order_id === catalogSelected.toUpperCase()) {
      row.classList.add("catalog-selected");
      action.setAttribute("aria-current", "page");
    }
    catalogCell(row, "").append(action);
    rows.append(row);
  }
  catalogNode("catalog-table").hidden = data.orders.length === 0;
  catalogNode("catalog-list-feedback").textContent = data.orders.length ? "" : "没有找到符合条件的订单。";
  const first = catalogNode("catalog-first");
  const next = catalogNode("catalog-next");
  first.hidden = !filters.has("after");
  next.hidden = !data.next_after;
  const firstParams = new URLSearchParams(filters);
  firstParams.delete("after");
  first.href = catalogHref(catalogPath, firstParams);
  if (data.next_after) {
    const nextParams = new URLSearchParams(filters);
    nextParams.set("after", data.next_after);
    next.href = catalogHref(catalogPath, nextParams);
  }
  catalogNode("catalog-pages").hidden = first.hidden && next.hidden;
}

/** 为订单详情添加一个字段和值。 */
function catalogFact(label, value) {
  const row = document.createElement("div");
  const term = document.createElement("dt");
  const description = document.createElement("dd");
  term.textContent = label;
  description.textContent = value ?? "暂无记录";
  row.append(term, description);
  catalogNode("catalog-facts").append(row);
}

/** 把店铺和平台的实际操作按发生时间排进同一个进度列表。 */
function catalogRenderProgress({ merchant, platform, services = [] }) {
  const steps = [];
  if (merchant) {
    steps.push({ title: "已向店铺提交申请", at: merchant.submitted_at, message: merchant.reason });
    if (merchant.resolved_at) {
      steps.push({ title: "店铺已答复", at: merchant.resolved_at, message: merchant.resolution });
    }
  }
  if (platform) {
    const label = "平台工单 #" + platform.ticket_no;
    steps.push({ title: "已申请平台介入", at: platform.created_at, message: platform.reason, label });
    if (platform.resolved_at) {
      steps.push({ title: "平台已处理", at: platform.resolved_at, message: platform.resolution, label });
    }
  }
  const list = catalogNode("catalog-progress-steps");
  list.replaceChildren();
  for (const step of steps.sort((left, right) => new Date(left.at) - new Date(right.at))) {
    const row = document.createElement("li");
    const title = document.createElement("strong");
    title.textContent = step.title;
    const meta = document.createElement("small");
    const time = document.createElement("time");
    time.dateTime = step.at;
    time.textContent = new Date(step.at).toLocaleString("zh-CN");
    if (step.label) meta.append(step.label + " · ");
    meta.append(time);
    const message = document.createElement("p");
    message.textContent = step.message ?? "";
    row.append(title, meta, message);
    list.append(row);
  }
  list.hidden = steps.length === 0;
  const serviceList = catalogNode("catalog-service-progress");
  const names = {submitted:"待审核",awaiting_return:"待寄回",return_sent:"待确认收件",received:"待办结",
    completed:"已办结",rejected:"审核未通过"};
  const serviceNames = {return:"退货退款",exchange:"换货",repair:"维修"};
  serviceList.replaceChildren();
  for (const entry of services ?? []) {
    const row = document.createElement("li");
    const link = document.createElement("a");
    link.href = "/service-requests?" + new URLSearchParams({order_id:entry.order_id, request:entry.request_no});
    link.textContent = `${'${serviceNames[entry.kind]} #${entry.request_no} · ${names[entry.status]}'}`;
    row.append(link);
    serviceList.append(row);
  }
  serviceList.hidden = !serviceList.children.length;
  let status = steps.length || !serviceList.hidden ? "" : "这笔订单还没有售后申请。";
  if (platform?.status === "pending") status = "平台工单 #" + platform.ticket_no + " 等待处理。";
  else if (!platform && merchant && !merchant.resolved_at) status = "等待店铺答复。";
  catalogNode("catalog-progress-feedback").textContent = status;
}

/** 进度可以独立刷新，暂时读不到时保留订单本身和上一份记录。 */
async function catalogLoadProgress() {
  const refresh = catalogNode("catalog-refresh-progress");
  const feedback = catalogNode("catalog-progress-feedback");
  if (refresh.disabled) return;
  refresh.disabled = true;
  feedback.textContent = "正在读取售后进度…";
  try {
    catalogRenderProgress(await catalogGet(catalogApi + "/" + encodeURIComponent(catalogSelected) + "/after-sales"));
  } catch (error) {
    feedback.textContent = error.message || "暂时无法读取售后进度，请刷新重试。";
  } finally {
    refresh.disabled = false;
  }
}

/** 展开订单资料，并给出当前角色对应的下一步操作。 */
function catalogRenderDetail(order) {
  catalogNode("catalog-record-order").textContent = order.order_id;
  catalogNode("catalog-facts").replaceChildren();
  if (catalogStaff) catalogFact("买家账号", order.buyer_id);
  catalogFact("订单来源", catalogKinds[order.kind]);
  catalogFact("订单状态", catalogStatuses[order.status] || "暂无状态");
  if (order.logistics) {
    if (order.seller_label) catalogFact(order.kind === "jd" ? "经营渠道" : "交易卖家", order.seller_label);
    catalogFact("下单日期", order.logistics.purchased_on);
    catalogFact("预计送达", order.logistics.estimated_on);
    catalogFact("实际签收", order.logistics.delivered_on);
    catalogFact("历史查询日", order.logistics.snapshot_on);
  } else {
    catalogFact("签收日期", order.signed_on);
    catalogFact("店铺", order.policy?.shop_name);
    catalogFact("适用退货条款", order.policy?.policy_name);
  }
  renderOrderItems(catalogNode("catalog-items"), order.items);
  const action = catalogNode("catalog-action");
  action.href = catalogStaff ? "/staff?order_id=" + encodeURIComponent(order.order_id) :
    catalogHref("/", new URLSearchParams({ order: order.order_id, scope: order.conversation_scope }));
  if (!catalogStaff) action.textContent = "咨询这笔订单";
  action.hidden = false;
  const apply = catalogNode("catalog-service-action");
  if (apply) {
    apply.hidden = catalogArchives || order.kind !== "jd" || !order.items?.items.length;
    apply.href = "/service-requests?order_id=" + encodeURIComponent(order.order_id);
  }
  catalogNode("catalog-detail-feedback").textContent = "";
  catalogNode("catalog-record").hidden = false;
}

/** 初始化当前角色页面，列表与详情可以各自显示查询结果。 */
async function loadCatalogPage() {
  if (!catalogStaff) {
    try {
      catalogArchives = Boolean((await catalogGet("/api/config")).mall_enabled);
      if (catalogArchives) {
        catalogNode("catalog-mall").hidden = false;
        document.querySelector(".catalog-intro h1").textContent = "历史订单参考";
        catalogNode("catalog-service-link").textContent = "历史售后";
      }
    } catch (error) {
      catalogNode("catalog-list-feedback").textContent = error.message;
      return;
    }
  }
  const filters = catalogFilters();
  catalogNode("catalog-order").value = filters.get("order_id") || "";
  if (catalogStaff) {
    const kind = filters.get("kind") || "jd";
    if (["all", "shop", "olist", "jd"].includes(kind)) catalogNode("catalog-kind").value = kind;
    catalogNode("catalog-buyer").value = filters.get("buyer_id") || "";
  } else {
    const receipt = filters.get("receipt");
    if (["all", "signed", "not_signed", "cancelled", "unknown"].includes(receipt)) catalogNode("catalog-receipt").value = receipt;
    catalogNode("catalog-clear").hidden = !filters.has("order_id") && (!receipt || receipt === "all");
  }
  const apiFilters = new URLSearchParams(filters);
  if (!catalogStaff || !filters.has("kind")) {
    const search = filters.get("order_id") || filters.get("after") || "";
    apiFilters.set("kind", /^OL/i.test(search) ? "olist" : (/^A/i.test(search) ? "shop" : "jd"));
  }
  const list = catalogGet(catalogHref(catalogApi, apiFilters))
    .then(data => catalogRenderList(data, filters))
    .catch(error => { catalogNode("catalog-list-feedback").textContent = error.message; });
  if (!catalogSelected) return list;
  const back = catalogNode("catalog-back");
  back.href = catalogHref(catalogPath, filters);
  back.hidden = false;
  if (!catalogOrderPattern.test(catalogSelected)) {
    catalogNode("catalog-detail-feedback").textContent = "请核对订单编号。";
    return list;
  }
  catalogNode("catalog-detail-feedback").textContent = "正在读取订单详情…";
  const detail = catalogGet(catalogApi + "/" + encodeURIComponent(catalogSelected))
    .then(order => {
      catalogRenderDetail(order);
      if (!catalogStaff) {
        catalogNode("catalog-refresh-progress").addEventListener("click", catalogLoadProgress);
        return catalogLoadProgress();
      }
    })
    .catch(error => { catalogNode("catalog-detail-feedback").textContent = error.message; });
  await Promise.all([list, detail]);
  if (["#catalog-detail", "#catalog-after-sales"].includes(location.hash)) {
    catalogNode(location.hash.slice(1))?.scrollIntoView({ block: "start" });
  }
}

loadCatalogPage();
