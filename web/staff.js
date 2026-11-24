// 管理客服工单查询、详情、店铺条款检索和处理结果提交。
const queue = document.getElementById("ticket-queue");
const refresh = document.getElementById("refresh-queue");
const searchForm = document.getElementById("staff-ticket-search");
const statusFilter = document.getElementById("staff-status-filter");
const numberInput = document.getElementById("staff-search-number");
const previousPage = document.getElementById("staff-prev-page");
const nextPage = document.getElementById("staff-next-page");
const form = document.getElementById("resolution-form");
const note = document.getElementById("resolution");
const resolveButton = document.getElementById("resolve-button");
const policyForm = document.getElementById("policy-search-form");
const policyQuestion = document.getElementById("policy-question");
const policySubmit = document.getElementById("policy-submit");
const policySources = document.getElementById("policy-sources");
let selected = null;
let detailVersion = 0;
let queueVersion = 0;
let saving = false;
let policyAvailable = false;
let olistPolicyAvailable = false;
let jdPolicyAvailable = false;
let selectedOrderType = "shop";
let appliedOrder = "";
let cursorHistory = [null];
let nextBefore = null;
const orderNumberPattern = /^(?:A[0-9]{3}|OL-[0-9A-F]{10}|JD-[0-9A-F]{12})$/;
const ticketNumberPattern = /^[1-9][0-9]{0,18}$/;
const statusLabel = { pending: "待处理", resolved: "已处理" };

/** 将服务端返回的字段作为纯文本显示。 */
function setText(id, value) {
  document.getElementById(id).textContent = value;
}

/** 发出工单请求，返回 JSON 或抛出可显示的错误。 */
async function requestJson(path, options) {
  const response = await sessionFetch(path, options);
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(data.message ?? "操作失败，请重试。");
    error.status = response.status;
    throw error;
  }
  return data;
}

/** 保存当前筛选和选中的详情，方便刷新后回到原位置。 */
function rememberTicketFilters() {
  const params = new URLSearchParams();
  if (statusFilter.value !== "all") params.set("status", statusFilter.value);
  if (appliedOrder) params.set("order_id", appliedOrder);
  if (selected) params.set("ticket", selected);
  const query = params.toString();
  history.replaceState(null, "", query ? "/staff?" + query : "/staff");
}

/** 读取上一页的游标，按当前条件刷新客服列表。 */
async function loadQueue(cursors = cursorHistory) {
  const version = ++queueVersion;
  refresh.disabled = true;
  previousPage.disabled = nextPage.disabled = true;
  setText("queue-feedback", "正在读取工单…");
  try {
    const params = new URLSearchParams({ status: statusFilter.value });
    if (appliedOrder) params.set("order_id", appliedOrder);
    const before = cursors.at(-1);
    if (before) params.set("before", before);
    const page = await requestJson("/api/staff/tickets/search?" + params);
    if (version !== queueVersion) return;
    if (!page.tickets.length && cursors.length > 1) {
      await loadQueue(cursors.slice(0, -1));
      return;
    }
    cursorHistory = cursors;
    nextBefore = page.next_before;
    queue.replaceChildren();
    setText("queue-feedback", page.tickets.length ? "" : "没有找到符合条件的工单。");
    for (const ticket of page.tickets) {
      const item = document.createElement("li");
      const button = document.createElement("button");
      const metadata = document.createElement("span");
      const reason = document.createElement("span");
      button.type = "button";
      button.className = "staff-ticket";
      button.dataset.ticketNo = ticket.ticket_no;
      button.textContent = "#" + ticket.ticket_no + " · " + ticket.order_id;
      metadata.className = "staff-ticket-meta";
      metadata.textContent = (statusLabel[ticket.status] ?? ticket.status) + " · 提交于 "
        + new Date(ticket.created_at).toLocaleString("zh-CN");
      reason.className = "staff-ticket-reason";
      reason.textContent = ticket.reason;
      button.append(metadata, reason);
      button.setAttribute("aria-pressed", String(ticket.ticket_no === selected));
      button.addEventListener("click", () => {
        if (!saving) loadDetail(ticket.ticket_no);
      });
      item.append(button);
      queue.append(item);
    }
  } catch (error) {
    if (version === queueVersion) setText("queue-feedback", error.message);
  } finally {
    if (version === queueVersion) {
      refresh.disabled = false;
      previousPage.disabled = cursorHistory.length <= 1;
      nextPage.disabled = !nextBefore;
      document.getElementById("staff-pages").hidden = previousPage.disabled && nextPage.disabled;
      setText("staff-page-label", "第 " + cursorHistory.length + " 页");
    }
  }
}

/** 新筛选从第一页查询，旧列表不参与新的翻页。 */
function applyTicketFilters() {
  rememberTicketFilters();
  queue.replaceChildren();
  cursorHistory = [null];
  nextBefore = null;
  return loadQueue();
}

/** 工单号直达详情，订单号用于筛选历史列表。 */
function searchTickets(event) {
  event.preventDefault();
  if (saving) return;
  const value = numberInput.value.trim().toUpperCase();
  const ticketNo = value.replace(/^#?0*/, "");
  if (ticketNumberPattern.test(ticketNo)) return loadDetail(ticketNo);
  if (value && !orderNumberPattern.test(value)) {
    setText("queue-feedback", "请输入工单号（如 #10）或订单号。");
    return;
  }
  appliedOrder = value;
  if (selected !== null) {
    selected = null;
    detailVersion += 1;
    document.getElementById("ticket-detail").hidden = true;
    setText("detail-feedback", "请选择一张工单。");
  }
  return applyTicketFilters();
}

/** 从地址恢复筛选与详情，列表按最新提交时间重读。 */
function restoreTicketView() {
  const params = new URLSearchParams(location.search);
  const status = params.get("status");
  const order = params.get("order_id")?.toUpperCase() ?? "";
  const ticketNo = params.get("ticket") ?? "";
  statusFilter.value = status === "pending" || status === "resolved" ? status : "all";
  appliedOrder = orderNumberPattern.test(order) ? order : "";
  numberInput.value = appliedOrder || (ticketNumberPattern.test(ticketNo) ? "#" + ticketNo : "");
  const list = loadQueue();
  return ticketNumberPattern.test(ticketNo) ? Promise.all([list, loadDetail(ticketNo)]) : list;
}

/** 显示工单、当前政策和适合当前状态的处理控件。 */
function showDetail(ticket) {
  const evidence = ticket.evidence;
  const done = ticket.status === "resolved";
  selectedOrderType = ticket.order_id.startsWith("JD-") ? "jd" : (ticket.order_id.startsWith("OL-") ? "olist" : "shop");
  setText("staff-ticket-no", "#" + selected);
  setText("staff-status", done ? "已处理" : "待处理");
  setText("staff-order", ticket.order_id);
  document.getElementById("staff-order").href = "/staff/orders/" + encodeURIComponent(ticket.order_id) + "#catalog-detail";
  setText("staff-signed", evidence.signed_on ?? "—");
  setText("staff-policy", evidence.policy_name ?? "—");
  setText("staff-elapsed", evidence.days_since_delivery == null ? "—" : evidence.days_since_delivery + " 天");
  document.querySelectorAll(".staff-policy-fact").forEach(fact => { fact.hidden = selectedOrderType !== "shop"; });
  document.querySelectorAll(".staff-logistics-fact").forEach(fact => { fact.hidden = !evidence.logistics; });
  setText("staff-logistics-snapshot", evidence.logistics?.snapshot_on ?? "—");
  setText("staff-logistics-estimated", evidence.logistics?.estimated_on ?? "暂无记录");
  setText("staff-logistics-delivered", evidence.logistics?.delivered_on ?? "暂无记录");
  showOlistItemsEvidence(ticket.olist_items);
  syncPolicyAvailability();
  setText("staff-evidence", evidence.message);
  setText("staff-reason", ticket.reason);
  document.getElementById("staff-merchant-request").hidden = !ticket.merchant_request;
  setText("staff-merchant-reason", ticket.merchant_request?.reason ?? "");
  setText("staff-merchant-resolution", ticket.merchant_request?.resolution ?? "店铺尚未答复。");
  document.getElementById("staff-request-summary").hidden = Boolean(ticket.handoff);
  renderTicketHandoff(document.getElementById("staff-handoff"), ticket.handoff, "买家确认的对话交接");
  setText("saved-note", ticket.resolution ?? "");
  const processedAt = done && ticket.resolved_at ? new Date(ticket.resolved_at).toLocaleString("zh-CN") : "";
  setText("saved-time", [ticket.resolved_by, processedAt].filter(Boolean).join(" · "));
  form.hidden = done;
  document.getElementById("saved-resolution").hidden = !done;
  document.getElementById("ticket-detail").hidden = false;
}

/** 展示关联商品及各行金额，区分明细缺失与服务未配置。 */
function showOlistItemsEvidence(details) {
  document.getElementById("staff-olist-items").hidden = selectedOrderType === "shop";
  const rows = document.getElementById("staff-olist-items-rows");
  rows.replaceChildren();
  if (details?.status !== "found") {
    setText("staff-olist-items-summary", details?.answer ?? "暂无可用的商品明细。");
    return;
  }
  const symbol = details.currency === "CNY" ? "¥" : "R$ ";
  const total = details.recorded_total == null ? "商品 " + symbol + details.items_total + " · 运费未记录" :
    "含运费合计 " + symbol + details.recorded_total;
  setText("staff-olist-items-summary", details.quantity + " 件 · " + total);
  for (const item of details.items) {
    const row = document.createElement("li");
    row.textContent = (item.category_label ?? item.category ?? "品类未记录") + " · " + (item.quantity ?? 1) +
      " 件 · 单价 " + symbol + item.price + (item.freight == null ? "" : " · 运费 " + symbol + item.freight);
    rows.append(row);
  }
}

/** 按当前工单的数据来源启用条款查询。 */
function syncPolicyAvailability() {
  const available = {jd: jdPolicyAvailable, olist: olistPolicyAvailable, shop: policyAvailable};
  document.getElementById("policy-search-panel").hidden = !available[selectedOrderType];
  setText("policy-search-title", selectedOrderType === "shop" ? "查店铺条款" : "查公开售后规则");
}

/** 加载当前选择，忽略用户快速切换时迟到的旧响应。 */
async function loadDetail(ticketNo) {
  selected = ticketNo;
  rememberTicketFilters();
  const version = ++detailVersion;
  for (const button of queue.querySelectorAll("button")) {
    button.setAttribute("aria-pressed", String(button.dataset.ticketNo === String(ticketNo)));
  }
  document.getElementById("ticket-detail").hidden = true;
  setText("detail-feedback", "正在读取工单详情…");
  setText("resolution-feedback", "");
  note.value = "";
  policyQuestion.value = "";
  policySubmit.disabled = false;
  setText("policy-answer", "");
  policySources.replaceChildren();
  try {
    const ticket = await requestJson("/api/staff/tickets/" + ticketNo);
    if (version !== detailVersion) return;
    showDetail(ticket);
    setText("detail-feedback", "");
  } catch (error) {
    if (version === detailVersion) setText("detail-feedback", error.message);
  }
}

/** 提交一次人工处理。遇到已被处理的工单时读取最新状态。 */
async function saveResolution(event) {
  event.preventDefault();
  const resolution = note.value.trim();
  if (saving || selected === null || !resolution) return;
  const ticketNo = selected;
  saving = true;
  note.disabled = resolveButton.disabled = true;
  setText("resolution-feedback", "正在保存处理结果…");
  try {
    await requestJson("/api/staff/tickets/" + ticketNo + "/resolve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ resolution }),
    });
    await Promise.all([loadDetail(ticketNo), loadQueue()]);
    setText("resolution-feedback", "处理结果已保存。");
  } catch (error) {
    if (error.status === 409) await Promise.all([loadDetail(ticketNo), loadQueue()]);
    setText("resolution-feedback", error.message);
  } finally {
    saving = false;
    note.disabled = resolveButton.disabled = false;
  }
}

/** 展示生成说明及 Milvus 实际返回的条款原文。 */
function showPolicyEvidence(result) {
  setText("policy-answer", result.answer);
  for (const source of result.sources) {
    const item = document.createElement("li");
    const heading = document.createElement("strong");
    const location = document.createElement("p");
    const text = document.createElement("p");
    heading.textContent = "[" + source.article_id + "] " + source.title;
    location.className = "policy-source-location";
    location.textContent = source.source;
    text.textContent = source.text;
    item.append(heading, location, text);
    policySources.append(item);
  }
}

/** 查询当前工单的店铺政策，切换工单后忽略旧请求。 */
async function searchPolicies(event) {
  event.preventDefault();
  const question = policyQuestion.value.trim();
  if (!question || selected === null || policySubmit.disabled) return;
  const version = detailVersion;
  policySubmit.disabled = true;
  policySources.replaceChildren();
  setText("policy-answer", "正在查询售后规则…");
  try {
    const result = await requestJson("/api/staff/tickets/" + selected + "/policy-search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    if (version === detailVersion) showPolicyEvidence(result);
  } catch (error) {
    if (version === detailVersion) setText("policy-answer", error.message);
  } finally {
    if (version === detailVersion) policySubmit.disabled = false;
  }
}

/** 从本机配置判断是否在客服详情里展示政策检索。 */
async function loadPolicyAvailability() {
  try {
    const config = await requestJson("/api/config");
    policyAvailable = Boolean(config.policy_search_enabled);
    olistPolicyAvailable = Boolean(config.olist_policy_search_enabled);
    jdPolicyAvailable = Boolean(config.jd_policy_search_enabled);
  } catch {
    policyAvailable = false;
    olistPolicyAvailable = false;
    jdPolicyAvailable = false;
  }
  syncPolicyAvailability();
}

refresh.addEventListener("click", () => loadQueue());
searchForm.addEventListener("submit", searchTickets);
statusFilter.addEventListener("change", applyTicketFilters);
previousPage.addEventListener("click", () => loadQueue(cursorHistory.slice(0, -1)));
nextPage.addEventListener("click", () => loadQueue([...cursorHistory, nextBefore]));
form.addEventListener("submit", saveResolution);
policyForm.addEventListener("submit", searchPolicies);
loadPolicyAvailability();
restoreTicketView();
