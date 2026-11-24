// 串联买家查单、政策问答、工单确认和进度查询。
const form = document.querySelector("#query-form");
const question = document.querySelector("#question");
const submit = document.querySelector("#submit");
const newQuery = document.querySelector("#new-query");
const ticketForm = document.querySelector("#ticket-form");
const ticketReason = document.querySelector("#ticket-reason");
const ticketSubmit = document.querySelector("#ticket-submit");
const ticketEdit = document.querySelector("#ticket-edit");
const ticketRefresh = document.querySelector("#ticket-refresh");
const ticketOrderProgress = document.querySelector("#ticket-order-progress");
const humanReviewButton = document.querySelector("#human-review-button");
const humanReviewPanel = document.querySelector("#human-review-panel");
const humanReviewClose = document.querySelector("#human-review-close");
const buyerSidebar = document.querySelector("#buyer-sidebar");
const narrowChat = window.matchMedia("(max-width: 700px)");
const afterSalesLink = document.querySelector("#after-sales-link");
const dialogueHistory = document.querySelector("#conversation-history");
const dialogueScroll = document.querySelector("#dialogue-scroll");
const sharePrompt = document.querySelector("#order-share-prompt");
const shareButton = document.querySelector("#order-share-submit");
const conversationStorageKey = "after-sales.pending-conversation";
let conversation = null;
let orderDraft = null;
let outgoingTurn = null;
let visibleOrderId = null;
let ticketEnabled = false;
let ticketOrder = null;
let ticketDraft = null;
let ticketBusy = false;
let ticketComplete = false;
let humanReviewOpen = false;
let ticketLookupEnabled = false;
let ticketLookupLoading = false;
let ticketLookupRevision = 0;
const policyStageLabels = {
  order_check: "校验订单",
  model_tool_request: "DeepSeek 请求函数调用",
  tool_validation: "校验函数参数",
  mcp_lookup: "MCP 查询订单与条款",
  reply_generation: "生成与复核客服答复",
};
const policyStageStatuses = { ok: "成功", failed: "失败", skipped: "跳过", fallback: "仅返回原文" };
const policyTimingLabels = { milvus_retrieval: "Milvus 检索", deepseek_draft: "DeepSeek 起草", answer_verification: "引用复核" };
const statusLabels = {
  signed: "已签收",
  eligible: "在退货期限内",
  outside_window: "已超出退货期限",
  not_signed: "订单尚未签收",
  order_not_found: "未找到订单",
  missing_order_id: "需要订单号",
  invalid_delivery_date: "签收日期异常",
  policy_unavailable: "暂无适用条款",
  logistics_delivered: "已签收",
  logistics_delivered_late: "延迟签收",
  logistics_overdue: "超期未签收",
  logistics_shipping: "已发货",
  logistics_cancelled: "订单已取消",
  logistics_unknown: "送达信息待核对",
  logistics_unavailable: "物流样本未接入",
};

/** 用纯文本更新页面，保留订单字段和用户输入的原样显示。 */
function setText(id, value) {
  document.getElementById(id).textContent = value;
}

let historicalChat = false;

/** 在输入区上方展示最近核对的订单和当前结果。 */
function showActiveOrder(result) {
  visibleOrderId = result?.order_id ?? null;
  document.getElementById("active-order").hidden = !visibleOrderId;
  setText("active-order-id", visibleOrderId ?? "");
  setText("active-order-label", orderDraft ? "待发送订单" : "当前订单");
  setText("active-order-status", statusLabels[result?.status] ?? "");
  afterSalesLink.href = visibleOrderId ? orderProgressHref(visibleOrderId) : "/orders";
  const action = document.getElementById("chat-service-action");
  action.hidden = historicalChat || Boolean(orderDraft) || !visibleOrderId?.startsWith("JD-");
  if (!action.hidden) action.href = "/service-requests?order_id=" + encodeURIComponent(visibleOrderId);
}

/** 用订单号直接定位售后时间线。 */
function orderProgressHref(orderId) {
  return "/orders/" + encodeURIComponent(orderId) + "#catalog-after-sales";
}

/** 展示当前卡片关联的签收信息和商品明细。 */
function renderInlineOrderDetail(container, order) {
  const facts = document.createElement("dl");
  facts.className = "facts";
  const fields = [["订单号", order.order_id], ["状态", statusLabels[order.status]]];
  if (order.logistics) {
    fields.push(["下单日期", order.logistics.purchased_on], ["预计送达", order.logistics.estimated_on],
      ["实际签收", order.logistics.delivered_on]);
  } else {
    fields.push(["店铺", order.policy?.shop_name], ["签收日期", order.signed_on]);
  }
  for (const [label, value] of fields) {
    const row = document.createElement("div");
    const term = document.createElement("dt");
    const description = document.createElement("dd");
    term.textContent = label;
    description.textContent = value ?? "—";
    row.append(term, description);
    facts.append(row);
  }
  const items = document.createElement("div");
  items.className = "catalog-items";
  renderOrderItems(items, order.items);
  container.replaceChildren(facts, items);
}

/** 点击时在消息内部读订单，使用订单接口已有的买家权限校验。 */
function inlineOrderDetails(orderId, previous) {
  if (previous?.dataset.orderId === orderId) return previous;
  const details = document.createElement("details");
  details.className = "dialogue-order-details";
  details.dataset.orderId = orderId;
  const summary = document.createElement("summary");
  summary.textContent = "查看订单详情";
  const content = document.createElement("div");
  content.className = "dialogue-order-content";
  const feedback = document.createElement("p");
  feedback.setAttribute("role", "status");
  details.append(summary, feedback, content);
  let loaded = false;
  let busy = false;
  details.addEventListener("toggle", async () => {
    summary.textContent = details.open ? "收起订单详情" : "查看订单详情";
    if (!details.open || loaded || busy) return;
    busy = true;
    feedback.textContent = "正在读取订单详情…";
    try {
      const response = await sessionFetch("/api/buyer/orders/" + encodeURIComponent(orderId));
      const order = await response.json();
      if (!response.ok) {
        feedback.textContent = order.message ?? "暂时无法读取订单，收起后再试。";
        return;
      }
      renderInlineOrderDetail(content, order);
      feedback.textContent = "";
      loaded = true;
    } catch {
      feedback.textContent = "暂时无法读取订单，收起后再试。";
    } finally {
      busy = false;
    }
  });
  return details;
}

/** 将待发送或已发送的订单渲染为同一种卡片。 */
function renderOrderCard(card, pending = false, previousDetail) {
  const node = document.createElement("article");
  node.className = "dialogue-order-card";
  const title = document.createElement("strong");
  title.textContent = pending ? "咨询此订单" : "已发送订单";
  if (pending) title.id = "order-share-title";
  const order = document.createElement("p");
  order.textContent = [card.order_id, statusLabels[card.status]].filter(Boolean).join(" · ");
  const items = document.createElement("small");
  const subtotalLabel = card.order_id.startsWith("JD-") ? "商品 ¥" : "含运费 R$ ";
  items.textContent = [card.items_summary, card.recorded_total ? subtotalLabel + card.recorded_total : null]
    .filter(Boolean).join(" · ");
  node.append(title, order);
  if (items.textContent) node.append(items);
  node.append(inlineOrderDetails(card.order_id, previousDetail));
  return node;
}

/** 创建一轮用户消息和客服气泡，实时发送和历史回放共用。 */
function createDialogueTurn(text) {
  const entry = document.createElement("li");
  const asked = document.createElement("p");
  const reply = document.createElement("article");
  entry.className = "dialogue-turn";
  asked.className = "dialogue-user";
  asked.textContent = text;
  reply.className = "dialogue-answer";
  entry.append(asked, reply);
  return { entry, asked, reply };
}

/** 在客服气泡里显示查询进度或发送失败。 */
function showOutgoingStatus(turn, message, waiting, facts) {
  const status = document.createElement("p");
  status.className = waiting ? "dialogue-status" : "dialogue-status dialogue-status-error";
  status.setAttribute("role", "status");
  if (waiting) {
    const spinner = document.createElement("span");
    spinner.className = "dialogue-spinner";
    spinner.setAttribute("aria-hidden", "true");
    status.append(spinner);
  }
  status.append(message);
  const previousDetail = turn.reply.querySelector(".dialogue-order-details");
  turn.reply.replaceChildren();
  if (facts) appendTurnAnswer(turn.reply, facts, previousDetail);
  turn.reply.append(status);
}

/** 使用后端确认的历史，并用正式答复更新本次发送的气泡。 */
function renderConversationHistory(turns) {
  const previousDetails = Array.from(dialogueHistory.children, row => row.querySelector(".dialogue-order-details"));
  const lastTurn = turns.at(-1);
  const confirmed = outgoingTurn && !lastTurn?.order_card && lastTurn?.question === outgoingTurn.asked.textContent;
  const rows = document.createDocumentFragment();
  document.getElementById("dialogue-empty").hidden = turns.length > 0 || Boolean(orderDraft);
  for (const [index, turn] of turns.entries()) {
    if (turn.order_card) {
      const entry = document.createElement("li");
      entry.className = "dialogue-turn";
      entry.append(renderOrderCard(turn.order_card, false, previousDetails[index]));
      rows.append(entry);
      continue;
    }
    const row = turn === lastTurn && confirmed ? outgoingTurn : createDialogueTurn(turn.question);
    row.reply.replaceChildren();
    appendTurnAnswer(row.reply, turn.result, previousDetails[index]);
    rows.append(row.entry);
  }
  outgoingTurn = null;
  dialogueHistory.replaceChildren(rows);
  dialogueScroll.scrollTop = dialogueScroll.scrollHeight;
}

/** 标注当轮已返回的业务结果，并保留相应工单的链接。 */
function appendTurnAnswer(reply, result, previousDetail) {
  const operations = result.tool_plan?.operations;
  const showLogistics = result.logistics && (!operations || operations.includes("logistics"));
  const items = result.olist_items;
  const review = result.human_review;
  const policy = result.policy_assistance;
  const freight = result.return_freight;
  const progress = result.service_progress;
  const labels = [];
  const messages = [];
  if (showLogistics) {
    labels.push("物流");
    messages.push(result.message);
  }
  if (items) {
    labels.push("商品明细");
    // 兼容旧检查点中已保存的商品答复。
    messages.push(items.answer.replace("Olist 公开记录仅提供匿名商品编号和品类，具体商品名未公开。", ""));
  }
  if (result.items_message) messages.push(result.items_message);
  if (freight) {
    labels.push("退货运费");
    messages.push((freight.practice ? "费用演练 · " : "") + freight.message);
  }
  if (progress) {
    labels.push("售后进度");
    if (progress.unavailable) messages.push("暂时无法读取售后进度，请稍后重试。");
    else if (!progress.requests.length) messages.push("这笔订单还没有商品售后单。");
  }
  if (review) {
    labels.push("人工复核");
    messages.push(review.message);
  }
  if (policy) {
    if (result.show_order_summary) {
      labels.push("退货判断");
      messages.push(result.message);
    }
    labels.push(result.logistics ? "公开售后规则" : "售后条款");
    messages.push(policy.answer);
  }
  if (result.order_id) labels.unshift(result.order_id);
  if (labels.length) {
    const actions = document.createElement("p");
    actions.className = "dialogue-actions";
    actions.textContent = labels.join(" · ");
    reply.append(actions);
  }
  for (const message of messages.length || progress?.requests.length ? messages : [result.message]) {
    const paragraph = document.createElement("p");
    paragraph.textContent = message;
    reply.append(paragraph);
  }
  if (result.order_id && (items || result.order_detail_requested)) {
    reply.append(inlineOrderDetails(result.order_id, previousDetail));
  }
  if (policy?.sources.length) {
    const detail = document.createElement("details");
    const summary = document.createElement("summary");
    const sources = document.createElement("ul");
    detail.className = "dialogue-sources";
    summary.textContent = result.logistics ? "查看公开依据" : "查看条款原文";
    sources.className = "policy-sources";
    appendPolicySources(sources, policy.sources);
    detail.append(summary, sources);
    reply.append(detail);
  }
  if (policy?.trace) appendPolicyTrace(reply, policy);
  if (progress) {
    const stage = {submitted:"待审核",awaiting_return:"待寄回",return_sent:"待确认收件",received:"待办结",
      completed:"已办结",rejected:"审核未通过"};
    const kind = {return:"退货退款",exchange:"换货",repair:"维修"};
    for (const entry of progress.requests) {
      const link = document.createElement("a");
      link.href = "/service-requests?" + new URLSearchParams({order_id:entry.order_id, request:entry.request_no});
      link.textContent = kind[entry.kind] + " #" + entry.request_no + " · " + stage[entry.status];
      reply.append(link);
    }
  }
  if (freight && result.order_id) {
    if (!historicalChat) {
      const apply = document.createElement("a");
      const params = new URLSearchParams({order_id:result.order_id});
      if (freight.reason) params.set("reason", freight.reason);
      apply.href = "/service-requests?" + params;
      apply.textContent = "填写售后申请";
      reply.append(apply);
    }
    if (freight.source_url) {
      const authority = document.createElement("a");
      authority.href = freight.source_url;
      authority.textContent = "查看运费规则";
      reply.append(authority);
    }
  }
  if (review?.status === "existing" && review.ticket_no) {
    const link = document.createElement("a");
    link.href = orderProgressHref(result.order_id);
    link.textContent = "查看工单进度";
    reply.append(link);
  } else if (!historicalChat && result.order_id && result.order_id === visibleOrderId && (
    ["preview", "merchant_first"].includes(review?.status) || policy?.status === "no_matches" ||
    policy?.items?.some((item) => item.status === "needs_review")
  )) {
    const action = document.createElement("button");
    action.className = "text-button";
    action.type = "button";
    action.textContent = review?.status === "merchant_first" ? "向店铺申请售后" : "打开人工申请";
    action.addEventListener("click", () => setHumanReviewOpen(true, true));
    reply.append(action);
  }
}

/** 根据是否等待订单号切换查询表单和提示。 */
function syncConversationForm() {
  const waiting = Boolean(conversation?.waiting);
  const continuing = Boolean(conversation?.canContinue);
  setText("question-label", waiting ? "补充订单号" : (continuing ? "继续问这笔订单" : "订单号或售后问题"));
  setText("submit-label", waiting ? "确认订单" : "发送");
  question.placeholder = waiting ? "从我的订单复制订单号" : (continuing
    ? "例如：退货运费谁承担？"
    : "例如：这单怎么还没收到？我买了几件？");
  newQuery.hidden = !conversation && !visibleOrderId;
  form.hidden = Boolean(orderDraft);
  sharePrompt.hidden = !orderDraft;
}

/** 地址只恢复本次会话，店铺缓存用于从订单入口找到旧对话。 */
function rememberConversation() {
  const page = new URL(location.href);
  page.searchParams.delete("order");
  page.searchParams.delete("scope");
  page.searchParams.set("conversation", conversation.id);
  if (page.href !== location.href) history.replaceState(null, "", page);
  try {
    if (conversation.scope) sessionStorage.setItem(conversationStorageKey + ":" + conversation.scope, conversation.id);
  } catch {
    // 浏览器禁止会话存储时，当前页面仍可使用内存状态。
  }
}

/** 把追问或完成结果同步到页面和当前标签页。 */
function applyConversationResponse(data) {
  orderDraft = data.draft_order ? { card: data.draft_order, conversationId: data.conversation_id } : null;
  if (data.awaiting_order_id) {
    conversation = { id: data.conversation_id, waiting: true, scope: data.scope };
    question.value = data.retry_reply ?? "";
  } else {
    conversation = { id: data.conversation_id, waiting: false, canContinue: data.can_continue, scope: data.scope };
    question.value = "";
  }
  if (!orderDraft) rememberConversation();
  const { result } = data;
  const historicalSeller = data.scope?.startsWith("olist-seller:") ? data.scope.slice(-8).toUpperCase() : null;
  setText("query-title", result.shop_name ? result.shop_name + " · 店铺售后" :
    (historicalSeller ? "历史卖家 " + historicalSeller + " · 售后咨询" :
      (result.logistics ? "历史订单售后" : "售后助手")));
  document.getElementById("dialogue-assistant-note").hidden = !result.order_id;
  setText("dialogue-assistant-note", result.logistics ? "售后助手根据历史订单记录自动回复。" :
    "售后助手自动回复，店铺答复在售后申请中查看。");
  setText("dialogue-empty-title", data.can_continue ? "开始咨询这笔订单" : "需要处理哪笔订单？");
  setText("dialogue-empty-hint", data.can_continue ? "可以直接说遇到的问题。" : "先说遇到的问题，或者输入订单号。");
  showActiveOrder(result);
  renderConversationHistory(data.turns);
  if (orderDraft) document.getElementById("order-share-card").replaceChildren(renderOrderCard(orderDraft.card, true));
  setText("dialogue-feedback", data.retry_reply ? result.message : "");
  const shopOrder = orderDraft || result.logistics ? null : (result.order_id ?? null);
  showMerchantRequest(shopOrder);
  resetTicket(orderDraft ? null : result.order_id ?? null);
  if (result.human_review?.status === "existing") {
    showPlatformTicket(result.human_review.ticket_no, result.human_review.ticket_status);
  }
  if (["existing", "preview", "merchant_first"].includes(result.human_review?.status)) {
    setHumanReviewOpen(true, true);
  }
  syncConversationForm();
}

/** 页面刷新后用会话 ID 读取检查点，恢复原问题或已完成的结果。 */
async function restoreBuyerConversation(conversationId) {
  if (!conversationId) return;
  const controls = [submit, question];
  controls.forEach(control => { control.disabled = true; });
  setText("dialogue-feedback", "正在恢复对话…");
  try {
    const response = await sessionFetch("/api/conversation/" + encodeURIComponent(conversationId));
    const data = await response.json();
    if (!response.ok) {
      setText("dialogue-feedback", data.message ?? "请刷新页面重试。");
      return;
    }
    applyConversationResponse(data);
  } catch {
    setText("dialogue-feedback", "暂时无法恢复对话，请刷新页面重试。");
  } finally {
    controls.forEach(control => { control.disabled = false; });
    syncConversationForm();
    if (conversation) question.focus({ preventScroll: true });
  }
}

/** 按行读取同一请求的订单事实和正式答复。 */
async function readConversationEvents(response, onFacts) {
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let pending = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) throw new Error("Incomplete conversation response");
      pending += value;
      let newline;
      while ((newline = pending.indexOf("\n")) !== -1) {
        const line = pending.slice(0, newline);
        pending = pending.slice(newline + 1);
        if (!line.trim()) continue;
        const event = JSON.parse(line);
        if (event.type === "partial") onFacts(event.data);
        else if (event.type === "final" || event.type === "error") return event;
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
  }
}

/** 发送问题，在同一个气泡中逐步显示已核对的结果。 */
async function queryOrder() {
  const text = question.value.trim();
  if (!text || submit.disabled || ticketBusy || orderDraft) return;
  const payload = { question: text };
  if (conversation) payload.conversation_id = conversation.id;
  if (conversation?.canContinue && visibleOrderId) payload.active_order_id = visibleOrderId;
  const controls = [submit, newQuery,
    ...form.querySelectorAll("input, textarea")];
  controls.forEach(control => { control.disabled = true; });
  setText("dialogue-feedback", "");
  const turn = (outgoingTurn ??= createDialogueTurn(text));
  turn.asked.textContent = text;
  showOutgoingStatus(turn, "正在查询，请稍等…", true);
  dialogueHistory.append(turn.entry);
  document.getElementById("dialogue-empty").hidden = true;
  dialogueScroll.scrollTop = dialogueScroll.scrollHeight;
  question.value = "";
  let facts = null;

  try {
    const response = await sessionFetch("/api/conversation/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const data = await response.json();
      showOutgoingStatus(turn, data.message ?? "查询失败，请重新发送。", false);
      question.value = text;
      return;
    }
    const event = await readConversationEvents(response, result => {
      const atBottom = dialogueScroll.scrollHeight - dialogueScroll.scrollTop - dialogueScroll.clientHeight < 64;
      facts = result;
      showOutgoingStatus(turn, "正在核对售后条款…", true, facts);
      if (atBottom) dialogueScroll.scrollTop = dialogueScroll.scrollHeight;
    });
    if (event.type === "error") {
      showOutgoingStatus(turn, event.message ?? "查询失败，请重新发送。", false, facts);
      question.value = text;
      return;
    }
    applyConversationResponse(event.data);
    return true;
  } catch {
    if (outgoingTurn === turn) {
      showOutgoingStatus(turn, "暂时没收到完整答复，请重新发送。", false, facts);
      question.value = text;
    }
  } finally {
    controls.forEach(control => { control.disabled = false; });
    syncConversationForm();
    question.focus({ preventScroll: true });
  }
}

/** 为当前或历史答复渲染对应的只读原文。 */
function appendPolicySources(container, sources) {
  for (const source of sources) {
    const item = document.createElement("li");
    const title = document.createElement("strong");
    const location = document.createElement("p");
    const text = document.createElement("p");
    title.textContent = "[" + source.article_id + "] " + source.title;
    location.className = "policy-source-location";
    location.textContent = source.source;
    text.textContent = source.text;
    item.append(title, location, text);
    container.append(item);
  }
}

/** 将模型调用和检索记录放在对应的条款答复下方。 */
function appendPolicyTrace(reply, policy) {
  const trace = document.createElement("details");
  trace.className = "dialogue-sources";
  const summary = document.createElement("summary");
  summary.textContent = "查看工具调用";
  const meta = document.createElement("p");
  meta.textContent = "请求 " + policy.trace.request_id + " · " + policy.trace.duration_ms + " ms";
  const stages = document.createElement("ol");
  stages.className = "policy-stages";
  for (const stage of policy.trace.stages) {
    const row = document.createElement("li");
    row.textContent = (policyStageLabels[stage.name] ?? stage.name) + " · " +
      (policyStageStatuses[stage.status] ?? stage.status) + " · " + stage.duration_ms + " ms";
    stages.append(row);
  }
  for (const [stage, duration] of Object.entries(policy.trace.details_ms ?? {})) {
    const row = document.createElement("li");
    row.textContent = (policyTimingLabels[stage] ?? stage) + " · " + duration + " ms";
    stages.append(row);
  }
  trace.append(summary, meta, stages);
  if (policy.function_call) {
    const calls = document.createElement("p");
    calls.textContent = "函数 " + policy.function_call + " · MCP " + (policy.mcp_tools?.join("、") || "未执行");
    trace.append(calls);
  }
  reply.append(trace);
}

/** 更新工单区在编辑、预览和已提交三个状态下的控件。 */
function syncTicketForm() {
  const historical = /^(?:JD|OL)-/.test(ticketOrder || "");
  const shopAvailable = merchantEnabled && !historical;
  const showPlatform = ticketEnabled && ticketOrder &&
    (!shopAvailable || hasMerchantRequest(ticketOrder) || ticketComplete);
  const available = Boolean(ticketOrder && (ticketEnabled || shopAvailable));
  humanReviewButton.hidden = !available;
  humanReviewPanel.hidden = !available || !humanReviewOpen;
  humanReviewButton.setAttribute("aria-expanded", String(!humanReviewPanel.hidden));
  humanReviewButton.textContent = !humanReviewPanel.hidden ? "收起人工服务" : (ticketComplete ? "查看人工进度" : "转人工");
  setText("human-review-order", ticketOrder ?? "");
  document.getElementById("ticket-panel").hidden = !showPlatform;
  setText("ticket-title", ticketComplete ? "平台处理进度" :
    (historical ? "申请平台人工处理" : "申请平台客服介入"));
  ticketForm.hidden = ticketComplete || ticketLookupLoading;
  ticketOrderProgress.hidden = !ticketComplete || !ticketLookupEnabled;
  ticketRefresh.hidden = ticketOrderProgress.hidden;
  ticketRefresh.disabled = ticketLookupLoading;
  document.getElementById("ticket-editor").hidden = Boolean(ticketDraft);
  const preview = document.getElementById("ticket-preview");
  preview.hidden = !ticketDraft;
  const reason = ticketDraft && !ticketDraft.handoff ? "\n" + ticketDraft.reason : "";
  preview.textContent = ticketDraft ? "订单 " + ticketDraft.order_id + reason : "";
  renderTicketHandoff(document.getElementById("ticket-handoff"), ticketDraft?.handoff, "随工单提交的交接内容");
  ticketReason.required = !ticketDraft;
  ticketReason.disabled = ticketBusy || Boolean(ticketDraft);
  ticketSubmit.disabled = ticketBusy;
  ticketSubmit.textContent = ticketDraft ? "确认提交" : "预览工单";
  ticketEdit.hidden = !ticketDraft;
  ticketEdit.disabled = ticketBusy;
}

/** 在断点处移动原表单，保留已填写的原因和预览。 */
function placeHumanReviewPanel() {
  const target = narrowChat.matches ? dialogueScroll : buyerSidebar;
  if (humanReviewPanel.parentElement === target) return;
  const focused = humanReviewPanel.contains(document.activeElement) ? document.activeElement : null;
  if (narrowChat.matches) target.append(humanReviewPanel);
  else target.prepend(humanReviewPanel);
  focused?.focus({ preventScroll: true });
}

/** 在当前会话打开人工服务面板，并同步这笔订单已有的工单。 */
function setHumanReviewOpen(open, reveal = false) {
  if (!ticketOrder) return;
  humanReviewOpen = open;
  syncTicketForm();
  if (open) {
    lookupExistingPlatformTicket(true);
    if (reveal && !humanReviewPanel.hidden) {
      if (narrowChat.matches) {
        dialogueScroll.scrollTop += humanReviewPanel.getBoundingClientRect().top - dialogueScroll.getBoundingClientRect().top - 12;
      } else {
        humanReviewPanel.scrollIntoView({ block: "nearest" });
      }
    }
  }
}

/** 就地显示已有工单，并链接到订单详情中的处理记录。 */
function showPlatformTicket(ticketNo, status, prefix = "已有工单 #", resolution) {
  ticketComplete = true;
  ticketDraft = null;
  ticketOrderProgress.href = orderProgressHref(ticketOrder);
  const state = status === "resolved" ? " 已处理。" : " 等待平台处理。";
  setText("ticket-feedback", prefix + ticketNo + state + (resolution ? "\n" + resolution : ""));
}

/** 切到订单时先查现有工单，返回的旧订单结果不改变当前表单。 */
async function lookupExistingPlatformTicket(refresh = false) {
  if (!ticketLookupEnabled || !ticketOrder || (ticketComplete && !refresh) || ticketLookupLoading || ticketBusy) return;
  const revision = ++ticketLookupRevision;
  ticketLookupLoading = true;
  ticketRefresh.textContent = "刷新人工进度";
  if (!ticketComplete) setText("ticket-feedback", "正在读取平台进度…");
  syncTicketForm();
  try {
    const path = "/api/buyer/orders/" + encodeURIComponent(ticketOrder) + "/after-sales";
    const response = await sessionFetch(path);
    const data = response.ok ? await response.json() : null;
    if (revision !== ticketLookupRevision) return;
    const platform = data?.platform;
    if (platform && (platform.status === "pending" || ticketOrder.startsWith("OL-") || ticketComplete)) {
      showPlatformTicket(platform.ticket_no, platform.status, "工单 #", platform.resolution);
    } else if (!ticketComplete) {
      setText("ticket-feedback", data ? "" : "暂时无法读取已有工单，可以重试预览。");
    } else if (!data) {
      ticketRefresh.textContent = "重试刷新";
    }
  } catch {
    if (revision === ticketLookupRevision) {
      if (ticketComplete) ticketRefresh.textContent = "重试刷新";
      else setText("ticket-feedback", "暂时无法读取已有工单，可以重试预览。");
    }
  } finally {
    if (revision === ticketLookupRevision) {
      ticketLookupLoading = false;
      syncTicketForm();
    }
  }
}

/** 开始新订单查询时清空页面上的工单预览和反馈。 */
function resetTicket(orderId = null) {
  if (ticketOrder === orderId) return;
  ticketOrder = orderId;
  humanReviewOpen = false;
  ticketDraft = null;
  ticketComplete = false;
  ticketLookupLoading = false;
  ticketLookupRevision += 1;
  ticketReason.value = "";
  setText("ticket-feedback", "");
  syncTicketForm();
  lookupExistingPlatformTicket();
}

/** 第一次提交仅预览，第二次提交同一确认 ID 并展示工单号。 */
async function submitTicket(event) {
  event.preventDefault();
  if (!ticketEnabled || !ticketOrder || ticketBusy || ticketComplete || ticketLookupLoading || submit.disabled) return;
  const confirming = Boolean(ticketDraft);
  const action = confirming ? "confirm" : "preview";
  const payload = confirming
    ? { confirmation_id: ticketDraft.confirmation_id }
    : { order_id: ticketOrder, reason: ticketReason.value.trim() };
  if (!confirming && conversation?.canContinue && visibleOrderId === ticketOrder) {
    payload.conversation_id = conversation.id;
  }
  ticketBusy = true;
  syncTicketForm();
  setText("ticket-feedback", confirming ? "正在提交工单…" : "正在准备预览…");
  try {
    const response = await sessionFetch("/api/tickets/" + action, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      if (response.status === 409) ticketDraft = null;
      setText("ticket-feedback", data.message ?? "操作失败，请重试。");
      return;
    }
    if (confirming || data.existing_ticket_no) {
      const ticketNo = data.existing_ticket_no ?? String(data.ticket_no);
      const status = data.existing_ticket_status ?? data.status;
      showPlatformTicket(ticketNo, status, data.existing_ticket_no ? "已有工单 #" : "工单 #");
    } else {
      ticketDraft = data;
      setText("ticket-feedback", "请核对订单和原因，再确认提交。");
    }
  } catch {
    setText("ticket-feedback", "未收到工单响应，请重试当前操作。");
  } finally {
    ticketBusy = false;
    syncTicketForm();
    if (ticketComplete) lookupExistingPlatformTicket(true);
  }
}

/** 切回编辑原因，下一次预览将生成新的确认 ID。 */
function editTicketReason() {
  ticketDraft = null;
  setText("ticket-feedback", "");
  syncTicketForm();
  ticketReason.focus();
}

ticketForm.addEventListener("submit", submitTicket);
ticketEdit.addEventListener("click", editTicketReason);
ticketRefresh.addEventListener("click", () => lookupExistingPlatformTicket(true));
humanReviewButton.addEventListener("click", () => setHumanReviewOpen(!humanReviewOpen, true));
humanReviewClose.addEventListener("click", () => {
  setHumanReviewOpen(false);
  humanReviewButton.focus({ preventScroll: true });
});
narrowChat.addEventListener("change", placeHumanReviewPanel);
placeHumanReviewPanel();
shareButton.addEventListener("click", shareSelectedOrder);

form.addEventListener("submit", event => {
  event.preventDefault();
  queryOrder();
});

newQuery.addEventListener("click", () => {
  if (ticketBusy || submit.disabled) return;
  location.assign("/orders");
});

fetch("/api/config")
  .then(response => {
    if (!response.ok) throw new Error("Config unavailable");
    return response.json();
  })
  .then(config => {
    historicalChat = Boolean(config.mall_enabled);
    ticketEnabled = Boolean(config.ticket_enabled) && !historicalChat;
    ticketLookupEnabled = Boolean(config.ticket_enabled) && config.access_enabled;
    merchantEnabled = Boolean(config.merchant_enabled) && !historicalChat;
    if (historicalChat) document.getElementById("chat-service-action").hidden = true;
    afterSalesLink.hidden = !config.access_enabled;
    document.getElementById("staff-link").hidden = !ticketEnabled || config.access_enabled;
    document.getElementById("orders-link").hidden = !config.access_enabled;
    showMerchantRequest(orderDraft || /^(?:JD|OL)-/.test(visibleOrderId || "") ? null : visibleOrderId);
    syncTicketForm();
    lookupExistingPlatformTicket();
  })
  .catch(() => setText(orderDraft ? "order-share-feedback" : "dialogue-feedback", "暂时无法连接售后服务。"));

/** 用户明确发送后，保存卡片并启用该订单的对话。 */
async function shareSelectedOrder() {
  if (!orderDraft || shareButton.disabled) return;
  shareButton.disabled = true;
  setText("order-share-feedback", "正在发送订单…");
  try {
    const response = await sessionFetch("/api/conversation/share-order", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ order_id: orderDraft.card.order_id, conversation_id: orderDraft.conversationId }),
    });
    const data = await response.json();
    if (!response.ok) {
      setText("order-share-feedback", data.message ?? "订单发送失败，请重试。");
      return;
    }
    applyConversationResponse(data);
    question.focus({ preventScroll: true });
  } catch {
    setText("order-share-feedback", "订单发送失败，请重试。");
  } finally {
    shareButton.disabled = false;
  }
}

/** 订单入口展示目标店铺及待发送卡片，普通刷新恢复已发送的对话。 */
async function openBuyerConversation() {
  const page = new URL(location.href);
  const selected = page.searchParams.get("order")?.trim().toUpperCase();
  if (!selected) return restoreBuyerConversation(page.searchParams.get("conversation"));
  if (!/^(?:A[0-9]{3}|OL-[0-9A-F]{10}|JD-[0-9A-F]{12})$/.test(selected)) {
    setText("dialogue-feedback", "请从订单详情选择有效的订单。");
    return;
  }
  const payload = { order_id: selected };
  try {
    const scope = page.searchParams.get("scope");
    const saved = scope && sessionStorage.getItem(conversationStorageKey + ":" + scope);
    if (saved) payload.conversation_id = saved;
  } catch {
    // 服务端可在没有浏览器缓存时定位店铺会话。
  }
  const controls = [submit, question];
  controls.forEach(control => { control.disabled = true; });
  setText("dialogue-feedback", "正在打开售后对话…");
  try {
    const response = await sessionFetch("/api/conversation/prepare-order", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      setText("dialogue-feedback", data.message ?? "暂时无法打开售后对话。");
      return;
    }
    applyConversationResponse(data);
  } catch {
    setText("dialogue-feedback", "暂时无法打开售后对话，请刷新后重试。");
  } finally {
    controls.forEach(control => { control.disabled = false; });
    syncConversationForm();
    if (orderDraft) shareButton.focus({ preventScroll: true });
    else if (conversation) question.focus({ preventScroll: true });
  }
}

openBuyerConversation();
