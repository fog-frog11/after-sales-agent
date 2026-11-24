/** 展示商城订单、订单内的咨询和三方各自可执行的售后动作。 */
const node = id => document.getElementById("mall-" + id);
const ordersMode = location.pathname === "/mall/orders";
const role = location.pathname === "/staff/disputes" ? "staff" : location.pathname === "/merchant/mall" ? "merchant" : "buyer";
const api = "/api/" + role + "/mall";
const params = new URLSearchParams(location.search);
const stages = {preparing: "待发货", in_transit: "运输中", delivered: "已签收", refunded: "已全额登记退款"};
const reasons = {wrong_purchase: "买错或不想要", quality: "质量问题", wrong_item: "发错商品", missing: "少件漏发",
  not_received: "没有收到", cancel: "取消未发货商品"};
const actions = {submit: "向店铺提交", approve: "同意申请", reject: "拒绝申请", negotiate: "提出协商方案",
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
const eventRoles = {buyer: "买家", merchant: "店铺客服", staff: "平台客服"};
const mailingActions = ["ship", "replace", "repair_complete", "reship"];
const dispatchActions = ["replace", "repair_complete", "reship"];
const deliveryReviewDecisions = ["platform_dispatch", "platform_reissue", "platform_continue_delivery"];
const refundStages = {pending: "退款处理中", failed: "退款失败 · 待店铺重试", succeeded: "退款成功"};
const outboundActions = ["confirm_delivery_address", "confirm_delivery", "report_delivery_problem",
  "resume_delivery", "reissue_delivery", "request_delivery_review", ...dispatchActions, ...deliveryReviewDecisions];
const deliveryProblems = {not_dispatched: "店铺逾期未发", not_received: "未收到售后包裹",
  wrong_item: "收到的商品发错", damaged: "收到的商品破损"};
const offerNames = {partial_refund: "留货部分退款", refund_only: "仅退款", return_refund: "退货退款",
  exchange: "换货", repair: "维修", reship: "补发"};
const returnOffers = ["return_refund", "exchange", "repair"];
const photoActions = ["evidence", "provide_evidence", "dispute", "review", "revise", "reject", "report_delivery_problem", "request_delivery_review"];
const replyActions = ["provide_evidence", "accept_offer", "reject_offer", "close_prompt"];
const orderPattern = /^CN-[0-9]{4,10}$/;
const casePattern = /^[1-9][0-9]{0,18}$/;
const drafts = new WeakMap();
const conversations = new Map();
let order;
let item;
let record;
let listCursor;
let listVersion = 0;
let detailVersion = 0;
let applicationVersion = 0;
let operationPending = false;

/** 区分首次确认与已确认后修改收货信息。 */
function actionLabel(action, editingAddress, retryRefund = false) {
  if (action === "confirm_delivery_address" && editingAddress) return "修改收货信息";
  if (action === "refund" && retryRefund) return "重试退款";
  return actions[action];
}

/** 构造可读错误，登录失效时交给共用的会话脚本。 */
async function mallFetch(path, payload) {
  const options = payload === undefined ? undefined : {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload),
  };
  const response = await sessionFetch(path.startsWith("/api/") ? path : api + path, options);
  const body = await response.json();
  if (!response.ok) {
    const error = new Error(body.message || body.detail || "当前操作未完成。");
    error.status = response.status;
    throw error;
  }
  return body;
}

/** 使用文本节点呈现订单数据和用户提交的说明。 */
function element(tag, value, className) {
  const result = document.createElement(tag);
  if (value !== undefined) result.textContent = value;
  if (className) result.className = className;
  return result;
}

/** 为队列条目、进度和导航创建可收藏的页面链接。 */
function link(label, path, className = "catalog-link") {
  const result = element("a", label, className);
  result.href = path;
  return result;
}

/** 页面按 URL 对应的角色选择订单或办理入口。 */
function caseUrl(number, action) {
  const path = {buyer: "/mall/after-sales", merchant: "/merchant/mall", staff: "/staff/disputes"}[role];
  const query = new URLSearchParams({case: number});
  if (action) query.set("action", action);
  return path + "?" + query;
}

/** 确定当前买家能从页面直接打开的初次介入或执行复核。 */
function interventionAction(state) {
  return {available: "dispute", review_available: "review", delivery_review_available: "request_delivery_review"}[state];
}

/** 对本地人民币金额和时间应用一致的显示格式。 */
function yuan(amount) { return Number(amount).toLocaleString("zh-CN", {style: "currency", currency: "CNY"}); }
function localTime(value) { return new Date(value).toLocaleString("zh-CN", {timeZone: "Asia/Shanghai", hour12: false}); }

/** 选择列表项而不重新载入整个页面。 */
function openInPlace(anchor, callback) {
  anchor.addEventListener("click", event => {
    if (event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    callback();
  });
}

/** 同步队列选中态，刷新和分页后也能识别当前记录。 */
function highlightSelection() {
  const current = ordersMode ? order?.order_id : record?.case_no;
  for (const row of node("list").children) {
    const selected = row.dataset.record === String(current ?? "");
    row.classList.toggle("mall-list-selected", selected);
    const anchor = row.querySelector("a");
    if (selected) anchor.setAttribute("aria-current", "true");
    else anchor.removeAttribute("aria-current");
  }
}

/** 只给当前角色展示本工作区内的对应导航。 */
function configurePage() {
  const title = ordersMode ? "我的订单" : {buyer: "我的售后", merchant: "店铺客服", staff: "平台客服"}[role];
  node("title").textContent = title;
  document.title = title + " · 商城售后";
  if (ordersMode) {
    node("detail-message").textContent = "从左侧选择订单查看商品。已提交的申请可在「我的售后」查看进度。";
    node("after-sales-link").hidden = false;
  }
  node("list-title").textContent = ordersMode ? "订单列表" : role === "staff" ? "平台介入队列" : "商品售后单";
  node("filter-label").textContent = ordersMode ? "包裹状态" : "待办";
  const filters = ordersMode ? {"": "全部状态", ...stages} :
    {all: "全部记录", pending: "待我处理", soon: "我的待办 · 4 小时内到期", overdue: "我的待办 · 已超时"};
  node("filter-select").replaceChildren(...Object.entries(filters).map(([key, label]) => new Option(label, key)));
  node("filter-select").value = role === "buyer" ? (ordersMode ? "" : "all") : "pending";
  const routes = {
    buyer: [["我的订单", "/mall/orders"], ["我的售后", "/mall/after-sales"]],
    merchant: [["本店商品售后", "/merchant/mall"], ["本店订单", "/merchant/mall/orders"]],
    staff: [["平台介入", "/staff/disputes"], ["商城档案", "/staff/mall/catalog"]],
  }[role];
  node("home").href = routes[0][1];
  for (const [label, path] of routes) {
    const current = path === location.pathname;
    const itemLink = link(label, path, current ? "topbar-link current-link" : "topbar-link");
    if (current) itemLink.setAttribute("aria-current", "page");
    node("nav").insertBefore(itemLink, document.getElementById("access-identity"));
  }
}

/** 读取条件筛选和分页结果，并保持已选中的单据可见。 */
async function loadList(append = false) {
  const version = ++listVersion;
  const search = new URLSearchParams();
  const code = node("prefix").value.trim().toUpperCase();
  search.set(ordersMode ? "prefix" : "order_id", code);
  search.set(ordersMode ? "stage" : "queue", node("filter-select").value);
  if (append && listCursor) search.set(ordersMode ? "after" : "before", listCursor);
  node("list-message").textContent = "正在读取…";
  node("more").disabled = true;
  try {
    const result = await mallFetch((ordersMode ? "/orders?" : "/cases?") + search);
    if (version !== listVersion) return;
    const list = node("list");
    if (!append) list.replaceChildren();
    for (const row of ordersMode ? result.orders : result.cases) {
      const listItem = element("li");
      if (ordersMode) {
        listItem.dataset.record = row.order_id;
        const anchor = link(row.order_id, "/mall/orders?order=" + encodeURIComponent(row.order_id));
        openInPlace(anchor, () => loadOrder(row.order_id));
        const badge = element("span", row.stages.map(value => stages[value]).join(" · "), "service-badge");
        badge.dataset.stage = row.stages.length === 1 ? row.stages[0] : "mixed";
        listItem.append(anchor, badge,
          element("small", row.shop_name + " · " + localTime(row.ordered_at)));
      } else {
        listItem.dataset.record = row.case_no;
        const anchor = link("#" + row.case_no + " " + (row.handling_label ?? row.kind_label), caseUrl(row.case_no));
        openInPlace(anchor, () => loadCase(row.case_no));
        const deadline = {soon: "即将超时", overdue: "已超时"}[row.deadline];
        const badge = element("span", [row.status_label, deadline].filter(Boolean).join(" · "), "service-badge");
        if (deadline) badge.dataset.deadline = row.deadline;
        listItem.append(anchor, badge, element("small", [row.order_id, row.shop_name, row.product,
          row.owner && "由" + row.owner + "处理", row.due_at && "截止 " + localTime(row.due_at)].filter(Boolean).join(" · ")));
      }
      list.append(listItem);
    }
    highlightSelection();
    listCursor = ordersMode ? result.next_after : result.next_before;
    node("more").hidden = !listCursor;
    node("list-message").textContent = list.children.length ? "" :
      role === "staff" ? "当前条件下没有平台介入单。" : "当前条件下没有记录。";
  } catch (error) {
    if (version === listVersion) node("list-message").textContent = error.message;
  } finally {
    if (version === listVersion) node("more").disabled = false;
  }
}

/** 顶部保留商品、哪家店铺、物流和实付金额，下面展示申请入口。 */
function renderOrder(data) {
  order = data;
  item = null;
  node("order-view").hidden = false;
  node("item-view").hidden = true;
  node("selection-title").textContent = "订单 " + data.order_id;
  highlightSelection();
  node("order-summary").textContent = [data.shop_name, localTime(data.ordered_at), "商品实付 " + yuan(data.total),
    Number(data.refunded_total) > 0 && "已登记退款 " + yuan(data.refunded_total)].filter(Boolean).join(" · ");
  const checkout = node("order-payment");
  checkout.hidden = data.shipping === null;
  checkout.replaceChildren(...[["商品原价", yuan(data.list_total)], ["优惠", yuan(data.discount_total)],
    ["配送费", yuan(data.shipping)], ["订单实付", yuan(data.paid_total)], ["付款方式", data.payment_method]].map(([label, value]) => {
    const group = document.createElement("div");
    group.append(element("dt", label), element("dd", value));
    return group;
  }));
  const originalAddress = node("order-address");
  originalAddress.hidden = !data.delivery_address;
  originalAddress.textContent = data.delivery_address ? "原订单收货信息 · " + formatAddress(data.delivery_address) : "";
  node("term-title").textContent = data.terms_title + " · 第 " + data.terms_version + " 版";
  node("terms").replaceChildren(...data.terms.map(text => element("li", text)));
  node("products").replaceChildren(...data.items.map(product => {
    const block = element("article", undefined, "mall-product");
    block.dataset.item = product.item_no;
    const buttons = element("div", undefined, "mall-product-actions");
    const existing = data.cases.find(row => row.item_no === product.item_no);
    const action = element("button", existing?.is_active || product.remaining_quantity === 0 ? "售后咨询" : "咨询或申请售后", "ticket-button");
    action.type = "button";
    action.addEventListener("click", () => selectItem(product, true));
    buttons.append(action);
    if (existing) {
      const action = interventionAction(existing.intervention?.state);
      const text = actions[action] ?? "查看售后单 #" + existing.case_no;
      buttons.append(link(text, caseUrl(existing.case_no, action)));
    }
    if (product.remaining_quantity === 0) buttons.append(element("span", "该商品已全部登记退款", "service-badge"));
    const parcel = ["包裹 " + product.package_no, stages[product.stage],
      product.tracking && product.carrier + " " + product.tracking,
      product.delivered_at ? "签收 " + localTime(product.delivered_at) :
        product.stage !== "refunded" && product.expected_on && "预计 " + product.expected_on];
    const heading = element("div", undefined, "mall-product-heading");
    const icon = element("span", undefined, "mall-parcel-icon");
    icon.setAttribute("aria-hidden", "true");
    const name = element("div");
    name.append(element("h3", product.title), element("p", product.variant + " · " + product.sku));
    const stage = element("span", stages[product.stage], "service-badge");
    stage.dataset.stage = product.stage;
    heading.append(icon, name, stage);
    const pricing = element("div", undefined, "mall-product-pricing");
    pricing.append(element("span", product.quantity + " 件 · 每件实付 " + yuan(product.paid_unit)),
      element("strong", "小计 " + yuan(product.quantity * Number(product.paid_unit))));
    block.append(heading, pricing, element("p", parcel.filter(Boolean).join(" · "), "mall-product-logistics"));
    if (Number(product.refunded_amount) > 0) {
      block.append(element("p", "已登记退款 " + yuan(product.refunded_amount) +
        " · 退款余额 " + yuan(product.refund_balance) + " · 剩余 " + product.remaining_quantity + " 件"));
    }
    block.append(buttons);
    return block;
  }));
  node("order-cases").replaceChildren(...data.cases.map(row =>
    link("售后单 #" + row.case_no + " · " + row.product + " · " + row.status_label, caseUrl(row.case_no))));
}

/** 读取订单详情，忽略已经被下一次选择替代的返回值。 */
async function loadOrder(code, itemNumber) {
  const version = ++detailVersion;
  node("detail-message").textContent = "正在读取订单…";
  node("order-view").hidden = true;
  try {
    const result = await mallFetch("/orders/" + encodeURIComponent(code));
    if (version !== detailVersion) return;
    renderOrder(result);
    history.replaceState(null, "", "/mall/orders?order=" + encodeURIComponent(code));
    const selected = result.items.find(row => String(row.item_no) === itemNumber);
    if (selected) selectItem(selected, false);
    node("detail-message").textContent = "";
    node("after-sales-link").hidden = true;
  } catch (error) {
    if (version === detailVersion) {
      node("detail-message").textContent = error.message;
      node("after-sales-link").hidden = false;
    }
  }
}

/** 选择商品后，恢复同一买家和商品的咨询与确认过的信息。 */
function selectItem(product, focus) {
  item = product;
  ++applicationVersion;
  node("item-title").textContent = product.title + " · 售后咨询";
  node("chat-store").textContent = order.shop_name;
  node("chat-order").textContent = "订单 " + order.order_id + " · 包裹 " + product.package_no;
  node("chat-stage").textContent = stages[product.stage];
  node("chat-stage").dataset.stage = product.stage;
  node("item-view").hidden = false;
  node("application").reset();
  node("application").hidden = product.remaining_quantity === 0;
  node("chat-apply").hidden = node("application").hidden;
  for (const card of node("products").children) {
    const selected = card.dataset.item === String(product.item_no);
    card.classList.toggle("mall-product-selected", selected);
  }
  node("consult").reset();
  node("application").elements.quantity.max = product.remaining_quantity;
  node("reason").replaceChildren(new Option("选择原因", ""), ...Object.entries(reasons).map(([id, label]) => new Option(label, id)));
  renderOptions(null);
  drawConversation();
  restoreHistory(order.order_id, product.item_no);
  history.replaceState(null, "", "/mall/orders?order=" + encodeURIComponent(order.order_id) + "&item=" + product.item_no);
  if (focus) {
    node("item-view").scrollIntoView({block: "start"});
    node("consult").elements.question.focus({preventScroll: true});
  }
}

/** 恢复刷新前的咨询，并防止覆盖用户刚发出的消息。 */
async function restoreHistory(code, line) {
  const key = code + "/" + line;
  if (conversations.has(key)) {
    const last = conversations.get(key).at(-1);
    if (last?.result?.reason) restoreApplication(last.result);
    return;
  }
  try {
    const result = await mallFetch("/orders/" + code + "/items/" + line + "/consult");
    if (conversations.has(key)) return;
    const turns = result.turns.map(turn => ({question: turn.question, result: turn.result}));
    conversations.set(key, turns);
    if (code === order?.order_id && line === item?.item_no) {
      drawConversation();
      if (!node("reason").value && turns.at(-1)?.result?.reason) restoreApplication(turns.at(-1).result);
    }
  } catch (error) {
    if (code === order?.order_id && line === item?.item_no) node("application-message").textContent = error.message;
  }
}

/** 渲染服务端恢复的历史和当前商品的发送进度。 */
function drawConversation() {
  const session = conversations.get(order.order_id + "/" + item.item_no) ?? [];
  const fragment = document.createDocumentFragment();
  node("chat-empty").hidden = session.length > 0;
  node("consult").querySelector("button").disabled = session.some(turn => !turn.result && !turn.error);
  for (const turn of session) {
    const question = element("li", turn.question, "mall-user");
    const result = turn.result;
    const reply = element("li", undefined, "mall-reply");
    if (result) {
      reply.append(element("p", result.answer));
      const basis = element("details", undefined, "mall-chat-basis");
      basis.append(element("summary", "查看条款依据"),
        element("p", result.terms_title + " · 第 " + result.terms_version + " 版"));
      reply.append(basis);
    } else if (turn.error) {
      reply.append(element("p", turn.error, "mall-chat-error"));
    } else {
      const progress = element("span", "正在核对订单和条款…", "mall-chat-progress");
      const spinner = element("span", undefined, "dialogue-spinner");
      spinner.setAttribute("aria-hidden", "true");
      progress.prepend(spinner);
      reply.append(progress);
    }
    if (result?.case_no) {
      const action = interventionAction(result.intervention?.state);
      const reviewing = result.intervention?.state === "reviewing";
      const label = actions[action] ?? (reviewing ? "查看平台处理进度" : "查看售后单 #" + result.case_no);
      reply.append(link(label, caseUrl(result.case_no, action), action ? "ticket-button" : "catalog-link"));
    } else if (result?.intervention?.state === "no_case" && !node("application").hidden) {
      const entry = link("填写商品售后申请", "#mall-application", "ticket-button");
      entry.addEventListener("click", event => {
        event.preventDefault();
        node("application").scrollIntoView({block: "start"});
        node("reason").focus({preventScroll: true});
      });
      reply.append(entry);
    }
    fragment.append(question, reply);
  }
  node("dialogue").replaceChildren(fragment);
  node("chat-scroll").scrollTop = node("chat-scroll").scrollHeight;
}

/** 恢复买家确认过的内容，办理资格重新按当前状态核对。 */
function restoreApplication(result) {
  fillApplication(result);
  refreshChoices();
}

/** 使用咨询里的原因和说明，保留买家亲手输入的草稿。 */
function fillApplication(result) {
  node("reason").value = result.reason;
  const description = node("application").elements.description;
  if (result.suggested_description && !description.value.trim()) description.value = result.suggested_description;
}

/** 把当前条款核算出的方案带入表单，由买家最终选择确认。 */
function renderOptions(result, fallback = "先选择售后原因，系统会核对这件商品能办哪些售后。") {
  const before = node("kind").value;
  const entries = Object.entries(result?.kinds ?? {});
  node("kind").replaceChildren(new Option("选择办理方式", ""), ...entries.map(([id, label]) => new Option(label, id)));
  if (entries.some(([id]) => id === before)) node("kind").value = before;
  else if (entries.length === 1) node("kind").value = entries[0][0];
  const fields = node("application-fields");
  fields.hidden = !entries.length;
  fields.disabled = !entries.length;
  node("apply").disabled = !entries.length;
  const eligibility = result?.eligibility;
  node("application-message").textContent = eligibility ?
    [eligibility.message, eligibility.next_step, result.freight?.message].filter(Boolean).join("\n") :
    result?.answer || fallback;
  const existing = node("application-case");
  existing.hidden = !result?.case_no || Boolean(entries.length);
  if (!existing.hidden) existing.href = caseUrl(result.case_no);
}

/** 发送订单商品咨询，先显示买家消息，再显示处理依据。 */
async function consultItem(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const raw = form.elements.question.value.trim();
  if (!raw || form.querySelector("button").disabled) return;
  const code = order.order_id;
  const line = item.item_no;
  const version = applicationVersion;
  const key = code + "/" + line;
  const session = conversations.get(key) ?? [];
  const turn = {question: raw};
  session.push(turn);
  conversations.set(key, session);
  form.elements.question.value = "";
  drawConversation();
  try {
    const result = await mallFetch("/orders/" + code + "/items/" + line + "/consult", {question: raw});
    turn.result = result;
    if (result.reason && version === applicationVersion && code === order?.order_id && line === item?.item_no) {
      fillApplication(result);
      renderOptions(result);
    }
  } catch (error) {
    turn.error = error.message;
  } finally {
    if (code === order?.order_id && line === item?.item_no) drawConversation();
  }
}

/** 用户改变原因时重新核算当前商品的可选办理方式。 */
async function refreshChoices() {
  const version = ++applicationVersion;
  const reason = node("reason").value;
  renderOptions(null, reason ? "正在核对这件商品的售后资格…" : undefined);
  if (!reason || !item) return;
  try {
    const result = await mallFetch("/orders/" + order.order_id + "/items/" + item.item_no + "/consult",
      {question: "核对可申请的售后与退货运费", reason, record: false});
    if (version === applicationVersion) renderOptions(result);
  } catch (error) {
    if (version === applicationVersion) node("application-message").textContent = error.message;
  }
}

/** 对同一确认内容重用 UUID，使网络重试不产生两条记录。 */
function submissionKey(form, payload) {
  const signature = JSON.stringify(payload);
  if (drafts.get(form)?.signature !== signature) drafts.set(form, {signature, id: crypto.randomUUID()});
  return drafts.get(form).id;
}

/** 将相机照片缩放并重新编码，上传时不保留原始 EXIF。 */
async function encodePhotos(input) {
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

/** 买家亲自确认才创建售后单，成功后定位同一张单。 */
async function submitApplication(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const fields = new FormData(form);
  const code = order.order_id;
  const data = {item_no: item.item_no, quantity: Number(fields.get("quantity")), kind: fields.get("kind"),
    reason: fields.get("reason"), description: fields.get("description")};
  const version = applicationVersion;
  node("apply").disabled = true;
  try {
    data.photos = await encodePhotos(node("application-photos"));
    if (version !== applicationVersion) return;
    data.submission_id = submissionKey(form, {code, ...data});
    const saved = await mallFetch("/orders/" + code + "/cases", data);
    drafts.delete(form);
    if (version === applicationVersion) location.assign(caseUrl(saved.case_no));
  } catch (error) {
    if (version === applicationVersion) {
      if (error.status === 409) await refreshChoices();
      else {
        node("application-message").textContent = error.message;
        node("apply").disabled = false;
      }
    }
  }
}

/** 显示真实记录、当前负责人、截止时间和本角色操作。 */
/** 显示某一版方案的业务类型、件数、金额与履约信息。 */
function summarizeOffer(offer) {
  return [offerNames[offer.kind], offer.quantity + " 件",
    offer.amount !== null && "商品退款 " + yuan(offer.amount)].filter(Boolean).join(" · ");
}

function renderCase(data) {
  record = data;
  highlightSelection();
  node("case-view").hidden = false;
  node("selection-title").textContent = "售后单 #" + data.case_no;
  node("case-summary").textContent = [data.order_id, data.shop_name, data.product, data.variant, data.quantity + " 件",
    data.kind_label, data.reason_label].join(" · ");
  const next = [data.status_label, data.owner && "当前由" + data.owner + "处理",
    data.due_at && (data.overdue ? "已超时（截止 " : "截止 ") + localTime(data.due_at) + (data.overdue ? "）" : "")];
  node("case-next").textContent = next.filter(Boolean).join(" · ");
  const intervention = role === "buyer" && !data.outbound_pending ? data.intervention : null;
  node("intervention").hidden = !intervention;
  node("intervention-message").textContent = intervention?.message ?? "";
  const escalation = interventionAction(intervention?.state);
  node("intervention-start").hidden = !escalation;
  node("intervention-start").textContent = actions[escalation] ?? "";
  renderReturnProgress(data);
  renderOutbound(data);
  renderRefundProgress(data);
  node("case-prompt").hidden = !data.prompt;
  node("case-prompt").textContent = data.prompt ? "店铺说明：" + data.prompt.note : "";
  const offer = data.agreement ?? data.prompt?.offer;
  const panel = node("case-offer");
  panel.hidden = !offer;
  panel.replaceChildren();
  if (offer) {
    panel.append(element("strong", data.agreement ? "买家已确认方案" : "待买家确认的方案"),
      element("p", data.product + " · " + summarizeOffer(offer)));
    if (offer.return_instructions) panel.append(element("p", "寄回说明：" + offer.return_instructions));
    if (data.agreement) panel.append(element("small", "确认时间 " + localTime(data.agreement.accepted_at)));
  }
  const buyerReturn = data.events.find(event => event.action === "ship")?.details;
  node("case-facts").textContent = [stages[data.package_stage], data.outbound_tracking && "原订单运单 " + data.outbound_tracking,
    buyerReturn?.tracking && "买家寄回运单 " + buyerReturn.carrier + " " + buyerReturn.tracking,
    "商品退款余额 " + yuan(data.refund_balance)].filter(Boolean).join(" · ");
  node("case-term-title").textContent = data.terms_title + " · 第 " + data.terms_version + " 版";
  node("case-terms").replaceChildren(...data.terms.map(text => element("li", text)));
  const money = node("case-money");
  money.hidden = !data.money.length;
  money.replaceChildren(...data.money.map(entry => {
    const row = element("div");
    row.append(element("dt", entry.label), element("dd", yuan(entry.amount) + " · " + entry.state));
    return row;
  }));
  node("timeline").replaceChildren(...data.events.map(event => {
    const row = element("li");
    const startingRefund = event.action === "refund" && event.details.refund_quantity !== undefined;
    const facts = [event.note, event.details.tracking && event.details.carrier + " " + event.details.tracking,
      event.details.return_details && formatReturnAddress(event.details.return_details),
      event.details.delivery_problem && deliveryProblems[event.details.delivery_problem],
      event.details.review_problem && "包裹 #" + event.details.delivery_parcel_no + " · " + deliveryProblems[event.details.review_problem],
      event.details.offer && summarizeOffer(event.details.offer),
      event.details.offer?.return_instructions && "寄回说明：" + event.details.offer.return_instructions,
      event.details.return_postage_due && "寄回预计自付 " + yuan(event.details.return_postage_due)
        + " · 已抵扣 " + yuan(event.details.credit_used),
      event.details.refund_amount && (startingRefund ? "本次发起金额 " : "退款登记 ") + yuan(event.details.refund_amount)].filter(Boolean);
    const reviewTask = {return_details: "寄回信息", receipt: "确认收件"}[event.details.review_phase];
    const decision = reviewTask && ["support_buyer", "support_seller"].includes(event.action) ? "复核结论 · " : "";
    const refundLabel = event.action === "refund" && !startingRefund ? "登记退款" : refundStages[event.details.refund_result];
    const eventLabel = refundLabel ?? actionLabel(event.action, event.details.delivery_address_changed);
    row.append(element("strong", eventRoles[event.actor_role] + " · " + decision + eventLabel +
      (reviewTask ? "（" + reviewTask + "）" : "")),
      element("small", localTime(event.created_at)), ...facts.map(value => element("p", value)));
    if (event.photos.length) {
      const photos = element("div", undefined, "mall-photo-grid");
      for (const [index, id] of event.photos.entries()) {
        const photo = link("", api + "/cases/" + data.case_no + "/photos/" + id);
        photo.target = "_blank";
        photo.rel = "noopener";
        const image = element("img");
        image.src = photo.href;
        image.alt = "打开凭证照片 " + (index + 1);
        image.loading = "lazy";
        photo.append(image);
        photos.append(photo);
      }
      row.append(photos);
    }
    return row;
  }));
  node("operation").hidden = !data.actions.length;
  node("operation").reset();
  node("operation-message").textContent = "";
  const reviewLabel = {return_details: "复核并填写寄回信息", receipt: "复核确认已收件"}[data.active_review_phase];
  node("action").replaceChildren(new Option("选择操作", ""), ...data.actions.map(value =>
    new Option(value === "support_buyer" && reviewLabel ? reviewLabel :
      actionLabel(value, Boolean(data.outbound), data.refund_attempt?.state === "failed"), value)));
  node("offer-kind").replaceChildren(new Option("选择方案", ""),
    ...Object.entries(data.offer_options).map(([value, name]) => new Option(name, value)));
  node("offer-quantity").max = data.offer_max_quantity;
  node("offer-quantity").value = data.offer_max_quantity;
  syncShipping();
}

/** 统一展示时间线中的收件信息与当前寄回地址。 */
function formatReturnAddress(details) {
  return "寄回地址：" + formatAddress(details);
}

function formatAddress(details) {
  return details.address + " · 收件人 " + details.recipient + " · 电话 " + details.phone;
}

/** 显示当前退款处理时间、结果说明与当前角色可用的操作。 */
function renderRefundProgress(data) {
  const attempt = data.refund_attempt;
  const action = ["refund", "record_refund_result"].find(value => data.actions.includes(value));
  node("refund-progress").hidden = !attempt && !action;
  const message = attempt?.state === "failed" ? "失败说明 · " + attempt.result_note :
    attempt ? (attempt.result_at ? "结果时间 " + localTime(attempt.result_at) : "发起时间 " + localTime(attempt.started_at)) : "商品退款待发起";
  node("refund-progress-message").textContent = message;
  node("refund-progress-action").hidden = !action;
  node("refund-progress-action").textContent = actionLabel(action, false, attempt?.state === "failed") ?? "";
  node("refund-progress-action").onclick = () => openOperation(action);
}

/** 分开呈现店铺新寄出的运单及之前的重寄记录。 */
function renderOutbound(data) {
  const parcel = data.outbound;
  const history = data.outbound_history ?? [];
  const kind = data.agreement?.kind ?? data.kind;
  const awaitingReturn = ["exchange", "repair"].includes(kind) &&
    ["awaiting_return", "return_sent"].includes(data.status);
  node("outbound").hidden = !data.outbound_pending && !history.length && !awaitingReturn;
  const alerts = parcel?.problem && deliveryProblems[parcel.problem];
  let summary = "待买家确认本次收货信息。确认后店铺可以寄出新包裹。";
  if (awaitingReturn) {
    if (data.status === "return_sent") summary = "等待店铺确认已收到寄回的商品。店铺确认后，买家可在这里确认收货信息。";
    else if (data.return_details_pending) summary = "等待店铺提供寄回地址。买家寄回商品并经店铺确认收件后，可在这里确认收货信息。";
    else summary = "请先登记寄回。店铺确认收件后，买家可在这里确认收货信息。";
  }
  node("outbound-summary").textContent = parcel ? [formatAddress(parcel.destination), alerts].filter(Boolean).join(" · ") : summary;
  const guidance = data.outbound_pending && data.intervention?.message;
  node("outbound-guidance").hidden = !guidance;
  node("outbound-guidance").textContent = guidance || "";
  const parcelStages = {ready: "待店铺发货", shipped: "待买家确认收货", received: "已确认收到", superseded: "已安排重寄"};
  node("outbound-history").replaceChildren(...history.map(entry =>
    element("li", ["包裹 #" + entry.parcel_no, parcelStages[entry.stage],
      entry.tracking && entry.carrier + " " + entry.tracking,
      entry.received_at && "确认收货 " + localTime(entry.received_at),
      entry.problem && deliveryProblems[entry.problem]].filter(Boolean).join(" · "))));
  const reply = data.delivery_reply;
  node("delivery-reply").hidden = !reply;
  node("delivery-reply").textContent = reply ? `店铺核查回复（${localTime(reply.at)}）${reply.note}` : "";
  const review = data.delivery_review;
  const reviewing = review?.action === "request_delivery_review";
  node("delivery-review").hidden = !review;
  if (review) {
    node("delivery-review-title").textContent = reviewing ? "平台正在处理这次包裹" : "平台结论 · " + actions[review.action];
    node("delivery-review-meta").textContent = `包裹 #${review.parcel_no} · ${deliveryProblems[review.problem]} · ${localTime(review.at)}`;
    node("delivery-review-note").textContent = review.note;
  }
  const relevant = data.actions.filter(action => outboundActions.includes(action) || (reviewing && action === "evidence"));
  node("outbound-actions").replaceChildren(...relevant.map(action => {
    const button = element("button", actionLabel(action, Boolean(parcel)), "ticket-button");
    button.type = "button";
    button.onclick = () => openOperation(action);
    return button;
  }));
}

/** 给寄回、填地址和确认收件提供角色专属直达按钮。 */
function renderReturnProgress(data) {
  const details = data.return_details;
  const action = ["provide_return_details", "ship", "receive"].find(value => data.actions.includes(value));
  const messages = [];
  if (data.status === "awaiting_return" && data.return_details_pending) {
    messages.push("当前需要店铺填写寄回收件信息。地址提供后，买家可登记运单。");
  }
  if (details && (["awaiting_return", "return_sent"].includes(data.status) || data.active_review_phase === "receipt")) {
    messages.push(formatReturnAddress(details));
  }
  if (data.status === "return_sent") messages.push("买家已登记寄回，当前等待店铺确认收件。");
  if (data.active_review_phase) {
    messages.push(data.active_review_phase === "receipt" ? "本轮复核事项：店铺逾期未确认收件。" :
      "本轮复核事项：店铺逾期未补充寄回信息。");
  }
  if (!messages.length && action === "ship") messages.push("请按原单的寄回说明寄件，并在寄出后登记运单。");
  node("return-progress").hidden = !messages.length;
  node("return-progress-message").textContent = messages.join("\n");
  node("return-progress-action").hidden = !action;
  node("return-progress-action").textContent = actions[action] ?? "";
  node("return-progress-action").onclick = () => openOperation(action);
}

/** 根据方案类型展示金额或寄回说明，余额提示始终随件数更新。 */
function syncOfferFields() {
  const active = node("action").value === "negotiate";
  const kind = active ? node("offer-kind").value : "";
  const fields = node("offer-fields");
  fields.hidden = !active;
  fields.disabled = !active;
  const partial = kind === "partial_refund";
  const amount = node("offer-amount");
  const quantity = Number(node("offer-quantity").value);
  const limit = active ? Math.round(Math.min(Number(record.refund_balance), Number(record.offer_unit_price) * quantity) * 100) : 0;
  node("offer-amount-label").hidden = !partial;
  amount.disabled = !partial;
  amount.required = partial;
  amount.max = ((limit - 1) / 100).toFixed(2);
  const needsReturn = returnOffers.includes(kind);
  node("offer-return-label").hidden = !needsReturn;
  node("offer-return").disabled = !needsReturn;
  node("offer-return").required = needsReturn;
  node("offer-preview").textContent = partial ? "买家留用商品，退款需低于 " + yuan(limit / 100) :
    ["refund_only", "return_refund"].includes(kind) ? "系统按当前余额核算商品退款 " + yuan(limit / 100) : "";
}

/** 读取收货表单，按接口的规则去掉各字段首尾空格。 */
function enteredDeliveryAddress() {
  return {recipient: node("delivery-recipient").value.trim(), address: node("delivery-address-value").value.trim(),
    phone: node("delivery-phone").value.trim()};
}

/** 定位是否为已有收货信息的实质修改，并控制提交入口。 */
function syncOperationButton() {
  const savedAddress = node("action").value === "confirm_delivery_address" && record?.outbound?.destination;
  const unchanged = Boolean(savedAddress && Object.entries(enteredDeliveryAddress()).every(([key, value]) => savedAddress[key] === value));
  const button = node("operation").querySelector('button[type="submit"]');
  button.disabled = operationPending || unchanged;
  node("delivery-address-hint").hidden = !unchanged;
  return button.disabled;
}

/** 改变选择时显示操作所需的运单或处理说明。 */
function syncShipping() {
  const action = node("action").value;
  const shipping = mailingActions.includes(action);
  node("shipping").hidden = !shipping;
  node("shipping").disabled = !shipping;
  const fillingDetails = action === "provide_return_details" ||
    (action === "support_buyer" && record?.active_review_phase === "return_details");
  node("return-details").hidden = !fillingDetails;
  node("return-details").disabled = !fillingDetails;
  const confirmAddress = action === "confirm_delivery_address";
  node("delivery-address").hidden = !confirmAddress;
  node("delivery-address").disabled = !confirmAddress;
  if (confirmAddress) {
    const destination = record.outbound?.destination ?? record.default_delivery_address;
    node("delivery-recipient").value = destination?.recipient ?? "";
    node("delivery-address-value").value = destination?.address ?? "";
    node("delivery-phone").value = destination?.phone ?? "";
  }
  const reporting = action === "report_delivery_problem";
  node("delivery-problem-field").hidden = !reporting;
  node("delivery-problem").disabled = !reporting;
  if (reporting) {
    const allowed = record.outbound?.stage === "ready" ? ["not_dispatched"] : ["not_received", "wrong_item", "damaged"];
    node("delivery-problem").replaceChildren(...allowed.map(value => new Option(deliveryProblems[value], value)));
  }
  const settlingRefund = action === "record_refund_result";
  node("refund-result-field").hidden = !settlingRefund;
  node("refund-result").disabled = !settlingRefund;
  const photos = photoActions.includes(action);
  node("operation-photo-field").hidden = !photos;
  node("operation-photos").disabled = !photos;
  const note = node("operation").elements.note;
  const acceptance = "同意店铺提出的处理方案。";
  if (action === "accept_offer" && !note.value.trim()) note.value = acceptance;
  if (action !== "accept_offer" && note.value === acceptance) note.value = "";
  const addressNote = "已补充寄回信息。";
  if (action === "provide_return_details" && !note.value.trim()) note.value = addressNote;
  if (action !== "provide_return_details" && note.value === addressNote) note.value = "";
  const deliveryNotes = {confirm_delivery_address: record?.outbound ? "已修改本次收货信息。" : "已核对本次收货信息。",
    confirm_delivery: "已收到本次售后包裹。"};
  if (["已核对本次收货信息。", "已修改本次收货信息。", deliveryNotes.confirm_delivery].includes(note.value)) note.value = "";
  if (!note.value.trim() && deliveryNotes[action]) note.value = deliveryNotes[action];
  note.placeholder = {
    approve: "需要寄回时，请填写寄回地址和注意事项",
    reject: "请说明拒绝原因和判断依据",
    evidence: "描述材料或已核实的事实",
    request_evidence: "说明需要补拍的照片或需要核对的信息",
    provide_evidence: "说明本次补交材料，也可描述无法提供的原因",
    negotiate: "说明为什么提出这项方案",
    reject_offer: "说明不同意的原因",
    dispute: "说明与店铺争议的事项和希望如何处理",
    review: "说明店铺哪一步超时，可附上物流等凭证",
    provide_return_details: "可补充寄回注意事项",
    report_delivery_problem: "描述未收到、发错或破损的经过，可补充照片",
    request_delivery_review: "说明店铺超时的情况、希望如何处理，可补充照片",
    platform_dispatch: "写明核对结果和店铺发货要求",
    platform_reissue: "写明异常判断依据和店铺重新寄出的要求",
    platform_continue_delivery: "写明物流凭证及继续跟踪这张运单的理由",
    refund: "填写本次发起退款的说明",
    record_refund_result: "填写退款渠道结果或失败原因",
    reissue_delivery: "说明改为重寄的原因",
    resume_delivery: "说明核对结果和接下来的安排",
    support_buyer: "填写决定依据。需要寄回时请说明后续安排",
  }[action] ?? "填写各方可以看到的说明";
  node("operation").querySelector('button[type="submit"]').textContent =
    {dispute: "提交平台介入申请", review: "提交平台复核申请", request_delivery_review: "提交包裹处理申请",
      confirm_delivery_address: record?.outbound ? "保存修改" : "确认收货信息", confirm_delivery: "确认收货",
      refund: actionLabel("refund", false, record?.refund_attempt?.state === "failed"), record_refund_result: "保存退款结果"}[action] ?? "确认操作";
  syncOperationButton();
  syncOfferFields();
}

/** 从订单、聊天或售后详情打开原单上的介入申请，提交前仍由买家确认。 */
function openOperation(action) {
  if (!record?.actions.includes(action)) return;
  node("action").value = action;
  syncShipping();
  node("operation").scrollIntoView({block: "center"});
  const form = node("operation").elements;
  const field = action === "provide_return_details" ? form.recipient :
    action === "confirm_delivery_address" ? node("delivery-recipient") :
    mailingActions.includes(action) ? form.carrier : form.note;
  field.focus({preventScroll: true});
}

/** 从原单提示打开买家当前可办理的平台操作。 */
function openIntervention() {
  if (role === "buyer") openOperation(interventionAction(record?.intervention?.state));
}

/** 读取同一张单，并允许复制链接给有权限的角色独立打开。 */
async function loadCase(number, action) {
  const version = ++detailVersion;
  node("case-view").hidden = true;
  node("detail-message").textContent = "正在读取售后单…";
  try {
    const result = await mallFetch("/cases/" + encodeURIComponent(number));
    if (version !== detailVersion) return;
    renderCase(result);
    node("detail-message").textContent = "";
    history.replaceState(null, "", caseUrl(number));
    if (role === "buyer" && ["dispute", "review", "request_delivery_review"].includes(action)) openOperation(action);
  } catch (error) {
    if (version === detailVersion) node("detail-message").textContent = error.message;
  }
}

/** 记录当前角色确认的操作，并刷新该单及队列。 */
async function submitOperation(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const number = record.case_no;
  if (syncOperationButton()) return;
  const {recipient, address, phone, ...data} = Object.fromEntries(new FormData(form));
  if (!node("return-details").disabled) data.return_details = {recipient, address, phone};
  if (outboundActions.includes(data.action) && record.outbound) data.delivery_parcel_no = record.outbound.parcel_no;
  if (deliveryReviewDecisions.includes(data.action)) data.delivery_review_id = record.delivery_review.request_id;
  if (dispatchActions.includes(data.action)) data.delivery_address_version = record.outbound.address_version;
  if (data.action === "confirm_delivery_address") {
    data.delivery_address = enteredDeliveryAddress();
    if (record.outbound) data.delivery_address_version = record.outbound.address_version;
  }
  if (data.action === "report_delivery_problem") data.delivery_problem = node("delivery-problem").value;
  if (data.action === "record_refund_result") {
    data.refund_attempt_id = record.refund_attempt.id;
    data.refund_result = node("refund-result").value;
  }
  if (data.action === "negotiate") {
    const kind = node("offer-kind").value;
    data.offer = {kind, quantity: Number(node("offer-quantity").value)};
    if (kind === "partial_refund") data.offer.amount = node("offer-amount").value;
    if (returnOffers.includes(kind)) data.offer.return_instructions = node("offer-return").value;
  }
  if (replyActions.includes(data.action)) data.prompt_id = record.prompt?.id;
  operationPending = true;
  syncOperationButton();
  try {
    if (photoActions.includes(data.action)) data.photos = await encodePhotos(node("operation-photos"));
    if (record?.case_no !== number) return;
    data.operation_id = submissionKey(form, {number, ...data});
    const result = await mallFetch("/cases/" + number + "/actions", data);
    drafts.delete(form);
    if (record?.case_no === number) {
      renderCase(result);
      node("operation-message").textContent = "处理记录已保存。";
    }
    await loadList();
  } catch (error) {
    if (record?.case_no === number) {
      if (["dispute", "review", "refund", "record_refund_result", ...outboundActions].includes(data.action) && error.status === 409) {
        await loadCase(number);
        node("detail-message").textContent = error.message;
        await loadList();
      } else node("operation-message").textContent = error.message;
    }
  } finally {
    operationPending = false;
    syncOperationButton();
  }
}

configurePage();
node("filter").addEventListener("submit", event => { event.preventDefault(); loadList(); });
node("refresh").addEventListener("click", () => {
  loadList();
  if (ordersMode && order) loadOrder(order.order_id, String(item?.item_no ?? ""));
  else if (!ordersMode && record) loadCase(record.case_no);
});
node("more").addEventListener("click", () => loadList(true));
node("consult").addEventListener("submit", consultItem);
node("chat-empty").addEventListener("click", event => {
  const suggestion = event.target.closest("button[data-question]");
  if (!suggestion) return;
  const input = node("question");
  input.value = suggestion.dataset.question;
  input.focus();
});
node("chat-apply").addEventListener("click", event => {
  event.preventDefault();
  node("application").scrollIntoView({block: "start"});
  node("reason").focus({preventScroll: true});
});
node("reason").addEventListener("change", refreshChoices);
node("application").addEventListener("submit", submitApplication);
node("intervention-start").addEventListener("click", openIntervention);
node("action").addEventListener("change", syncShipping);
node("delivery-address").addEventListener("input", syncOperationButton);
node("offer-kind").addEventListener("change", syncOfferFields);
node("offer-quantity").addEventListener("input", syncOfferFields);
node("operation").addEventListener("submit", submitOperation);
loadList();
if (ordersMode && orderPattern.test(params.get("order") ?? "")) loadOrder(params.get("order"), params.get("item"));
if (!ordersMode && casePattern.test(params.get("case") ?? "")) loadCase(params.get("case"), params.get("action"));
