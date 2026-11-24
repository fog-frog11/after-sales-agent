/** 处理本机工作台的登录、会话过期和退出。 */

const signIn = document.getElementById("access-login");
const signOut = document.getElementById("access-logout");
// 预览不同角色使用的工作台，实际入口由服务端根据登录身份返回。
if (signIn) {
  const previews = {buyer:["我的订单","查包裹、联系店铺，跟进每一笔售后。"],
    merchant:["店铺售后","核对售后原因，回应买家并安排寄回。"],
    staff:["平台介入","查看争议记录、双方凭证和等待复核的申请。"]};
  const choices = document.querySelectorAll("[data-preview]");
  for (const choice of choices) choice.addEventListener("click", () => {
    const [title,caption] = previews[choice.dataset.preview];
    document.getElementById("portal-title").textContent = title;
    document.getElementById("portal-caption").textContent = caption;
    document.querySelector(".commerce-login-art").dataset.role = choice.dataset.preview;
    for (const button of choices) button.setAttribute("aria-pressed",String(choice === button));
  });
  const toggle = document.getElementById("secret-toggle");
  toggle?.addEventListener("click", () => {
    const field = document.getElementById("access-secret");
    const show = field.type === "password";
    field.type = show ? "text" : "password";
    toggle.textContent = show ? "隐藏" : "显示";
    toggle.setAttribute("aria-label",toggle.textContent + "口令");
    toggle.setAttribute("aria-pressed",String(show));
  });
}
const mallPageRoles = {"/mall/orders": "buyer", "/mall/after-sales": "buyer",
  "/merchant/mall": "merchant", "/merchant/mall/orders": "merchant",
  "/staff/disputes": "staff", "/staff/mall/catalog": "staff"};

/** 在受保护的业务请求遇到过期会话时返回登录页。 */
async function sessionFetch(path, options) {
  const response = await fetch(path, options);
  if (response.status === 401) {
    const next = new URLSearchParams({next: location.pathname + location.search});
    location.replace(mallPageRoles[location.pathname] ? "/login?" + next : "/login");
  }
  return response;
}

/** 登录成功后打开对应角色的工作台。 */
async function loginToWorkbench(event) {
  event.preventDefault();
  const button = document.getElementById("access-submit");
  const feedback = document.getElementById("access-feedback");
  button.disabled = true;
  feedback.textContent = "";
  try {
    const response = await fetch("/api/session/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        account_id: document.getElementById("access-account").value,
        secret: document.getElementById("access-secret").value,
      }),
    });
    const data = await response.json();
    if (!response.ok) {
      feedback.textContent = data.message;
      return;
    }
    const next = new URLSearchParams(location.search).get("next");
    if (data.home && next?.startsWith("/") && URL.canParse(next, location.origin)) {
      const target = new URL(next, location.origin);
      if (target.origin === location.origin && mallPageRoles[target.pathname] === data.role) {
        location.replace(target.pathname + target.search);
        return;
      }
    }
    location.replace(data.home ?? { staff: "/staff", merchant: "/merchant", buyer: "/orders" }[data.role] ?? "/");
  } catch {
    feedback.textContent = "暂时无法登录，请稍后重试。";
  } finally {
    button.disabled = false;
  }
}

/** 退出后返回登录页，允许换一个演示账号。 */
async function logoutOfWorkbench() {
  signOut.disabled = true;
  try {
    const response = await fetch("/api/session/logout", { method: "POST" });
    if (!response.ok) throw new Error();
    location.replace("/login");
  } catch {
    signOut.textContent = "重试退出";
    signOut.disabled = false;
  }
}

/** 在工作台页显示当前账号以及退出入口。 */
async function showWorkbenchAccount() {
  try {
    const response = await fetch("/api/session");
    if (!response.ok) return;
    const account = await response.json();
    if (!account.account_id) return;
    document.getElementById("access-identity").textContent = account.display_name ?? account.account_id;
    signOut.hidden = false;
    const buyerLink = document.getElementById("buyer-link");
    if (buyerLink) buyerLink.hidden = true;
  } catch {
    // 业务请求会在会话失效时把浏览器引导到登录页。
  }
}

if (signIn) signIn.addEventListener("submit", loginToWorkbench);
if (signOut) {
  signOut.addEventListener("click", logoutOfWorkbench);
  showWorkbenchAccount();
}
