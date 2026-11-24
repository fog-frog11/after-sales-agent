/** 买家提交店铺申请，读取当前订单的店铺答复。 */

const merchantPanel = document.getElementById("merchant-panel");
const merchantForm = document.getElementById("merchant-form");
const merchantReason = document.getElementById("merchant-reason");
const merchantSubmit = document.getElementById("merchant-submit");
const merchantRefresh = document.getElementById("merchant-refresh");
const merchantFeedback = document.getElementById("merchant-feedback");
let merchantEnabled = false;
let merchantOrder = null;
let merchantRequest = null;
let merchantBusy = false;
let merchantRevision = 0;

/** 平台申请入口只继承当前订单已提交的店铺申请。 */
function hasMerchantRequest(orderId) {
  return Boolean(orderId && merchantRequest?.order_id === orderId);
}

/** 切换订单时清空店铺处理信息，读取新订单自己的申请。 */
function showMerchantRequest(orderId) {
  if (merchantOrder === orderId && !merchantPanel.hidden) return;
  merchantOrder = orderId;
  merchantRequest = null;
  merchantRevision += 1;
  merchantBusy = false;
  merchantReason.value = "";
  merchantFeedback.textContent = "";
  merchantForm.hidden = true;
  merchantPanel.hidden = !merchantEnabled || !merchantOrder;
  if (!merchantPanel.hidden) loadMerchantRequest();
}

/** 获取店铺状态或提交已填写的申请，忽略切单后返回的旧响应。 */
async function loadMerchantRequest(reason) {
  if (!merchantOrder || merchantBusy) return;
  const revision = merchantRevision;
  const path = "/api/buyer/merchant-requests/" + encodeURIComponent(merchantOrder);
  merchantBusy = merchantRefresh.disabled = merchantSubmit.disabled = true;
  merchantFeedback.textContent = reason ? "正在提交申请…" : "正在读取店铺进度…";
  try {
    const options = reason ? { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason }) } : undefined;
    const response = await sessionFetch(path, options);
    const data = await response.json();
    if (revision !== merchantRevision) return;
    if (!response.ok) {
      merchantFeedback.textContent = data.message ?? "无法读取店铺售后，请重试。";
      return;
    }
    merchantRequest = data;
    merchantForm.hidden = Boolean(data);
    if (data?.resolution) merchantFeedback.textContent = "店铺答复\n" + data.resolution;
    else if (data) merchantFeedback.textContent = "已提交给店铺，等待答复。";
    else merchantFeedback.textContent = "";
    syncTicketForm();
  } catch {
    if (revision === merchantRevision) merchantFeedback.textContent = "暂时无法操作，请重试。";
  } finally {
    if (revision === merchantRevision) merchantBusy = merchantRefresh.disabled = merchantSubmit.disabled = false;
  }
}

merchantRefresh.addEventListener("click", () => loadMerchantRequest());
merchantForm.addEventListener("submit", event => {
  event.preventDefault();
  const reason = merchantReason.value.trim();
  if (reason) loadMerchantRequest(reason);
});
