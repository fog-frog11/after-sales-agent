/** 读取本店售后队列，向买家保存处理答复。 */

const merchantRequests = document.getElementById("merchant-requests");
const merchantFilter = document.getElementById("merchant-filter");
const queueFeedback = document.getElementById("merchant-queue-feedback");
const merchantFirst = document.getElementById("merchant-first");
const merchantNext = document.getElementById("merchant-next");
const queueRefresh = document.getElementById("merchant-queue-refresh");
let cursor = "";
let nextCursor = "";
let loading = false;

/** 统一解析接口答复，失败时显示服务端给出的原因。 */
async function merchantJson(path, options) {
  const response = await sessionFetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.message ?? "售后服务暂不可用。");
  return data;
}

/** 保存当前订单的店铺答复，完成后刷新待处理列表。 */
async function answerMerchantRequest(event, orderId, input, button, message) {
  event.preventDefault();
  const resolution = input.value.trim();
  if (!resolution || button.disabled) return;
  button.disabled = true;
  message.textContent = "正在保存…";
  try {
    await merchantJson("/api/merchant/requests/" + encodeURIComponent(orderId) + "/resolve", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ resolution }),
    });
    await loadMerchantQueue(cursor, "答复已保存，买家现在可以查看。");
  } catch (error) {
    message.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

/** 店铺人员展开申请时读取关联订单，权限由服务端核对。 */
function merchantOrderDetails(orderId) {
  const details = document.createElement("details");
  details.className = "dialogue-sources";
  const title = document.createElement("summary");
  title.textContent = "查看关联订单";
  const info = document.createElement("p");
  info.className = "ticket-feedback";
  details.append(title, info);
  let loaded = false;
  let loading = false;
  details.addEventListener("toggle", async () => {
    if (!details.open || loaded || loading) return;
    loading = true;
    info.textContent = "正在读取订单资料…";
    try {
      const order = await merchantJson("/api/merchant/requests/" + encodeURIComponent(orderId) + "/order");
      info.textContent = [order.order_id, order.signed_on ? "签收于 " + order.signed_on : "未签收",
        order.policy?.policy_name].filter(Boolean).join(" · ");
      loaded = true;
    } catch (error) {
      info.textContent = error.message ?? "暂时无法读取订单资料。";
    } finally {
      loading = false;
    }
  });
  return details;
}

/** 将申请原因和店铺答复作为文本呈现。 */
function renderRequest(request) {
  const section = document.createElement("section");
  section.className = "ticket-panel";
  const title = document.createElement("h3");
  title.textContent = "订单 " + request.order_id;
  const submitted = document.createElement("p");
  submitted.className = "ticket-feedback";
  submitted.textContent = "提交于 " + new Date(request.submitted_at).toLocaleString("zh-CN");
  const reason = document.createElement("p");
  reason.className = "ticket-preview";
  reason.textContent = request.reason;
  section.append(title, submitted, reason, merchantOrderDetails(request.order_id));
  if (request.resolved_at) {
    const response = document.createElement("p");
    response.className = "ticket-feedback";
    response.textContent = "店铺答复\n" + request.resolution;
    section.append(response);
    return section;
  }
  const form = document.createElement("form");
  form.className = "resolution-form";
  const label = document.createElement("label");
  const input = document.createElement("textarea");
  input.id = "resolve-" + request.order_id;
  input.maxLength = 300;
  input.rows = 2;
  input.required = true;
  label.htmlFor = input.id;
  label.textContent = "给买家的答复";
  const button = document.createElement("button");
  button.className = "ticket-button";
  button.textContent = "保存答复";
  const feedback = document.createElement("p");
  feedback.setAttribute("role", "status");
  feedback.className = "ticket-feedback";
  form.append(label, input, button, feedback);
  form.addEventListener("submit", event => answerMerchantRequest(event, request.order_id, input, button, feedback));
  section.append(form);
  return section;
}

/** 按状态和订单号游标查询本店申请。 */
async function loadMerchantQueue(after = "", notice = "") {
  if (loading) return;
  loading = merchantFilter.disabled = queueRefresh.disabled = merchantNext.disabled = merchantFirst.disabled = true;
  queueFeedback.textContent = "正在读取售后申请…";
  try {
    const query = new URLSearchParams({ resolved: merchantFilter.value });
    if (after) query.set("after", after);
    const data = await merchantJson("/api/merchant/requests?" + query);
    if (data.shop_name) document.getElementById("merchant-shop").textContent = data.shop_name + " · 售后";
    merchantRequests.replaceChildren(...data.requests.map(renderRequest));
    cursor = after;
    nextCursor = data.next_after;
    merchantFirst.hidden = !after;
    merchantNext.hidden = !nextCursor;
    queueFeedback.textContent = notice || (data.requests.length ? "" : "当前没有符合条件的售后申请。");
  } catch (error) {
    queueFeedback.textContent = error.message;
  } finally {
    loading = merchantFilter.disabled = queueRefresh.disabled = merchantNext.disabled = merchantFirst.disabled = false;
  }
}

merchantFilter.addEventListener("change", () => loadMerchantQueue());
merchantFirst.addEventListener("click", () => loadMerchantQueue());
merchantNext.addEventListener("click", () => loadMerchantQueue(nextCursor));
queueRefresh.addEventListener("click", () => loadMerchantQueue(cursor));
loadMerchantQueue();
