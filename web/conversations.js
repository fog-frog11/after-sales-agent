/** 显示服务端按账号保存的对话，按选中的会话地址打开。 */

const conversationList = document.getElementById("conversation-list");
const conversationFeedback = document.getElementById("conversations-feedback");
const conversationDate = new Intl.DateTimeFormat("zh-CN", {
  month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Shanghai",
});

/** 使用文字绘制标题、最近订单和最后一句。 */
function renderConversation(row) {
  const item = document.createElement("li");
  const link = document.createElement("a");
  link.className = "conversation-entry";
  link.href = "/?conversation=" + encodeURIComponent(row.conversation_id);
  const title = document.createElement("strong");
  title.textContent = row.title;
  const when = document.createElement("time");
  when.dateTime = row.updated_at;
  when.textContent = conversationDate.format(new Date(row.updated_at));
  const context = document.createElement("small");
  context.textContent = [row.order_id, row.awaiting_order_id ? "待补充订单号" : null].filter(Boolean).join(" · ");
  const preview = document.createElement("p");
  preview.textContent = row.preview;
  link.append(title, when, preview);
  if (context.textContent) link.append(context);
  item.append(link);
  return item;
}

/** 保留分页位置，浏览器前进返回可以回到同一页。 */
async function loadConversations() {
  const before = new URL(location.href).searchParams.get("before");
  try {
    const response = await sessionFetch("/api/buyer/conversations" + (before ? "?before=" + encodeURIComponent(before) : ""));
    const page = await response.json();
    if (!response.ok) throw new Error(page.message ?? "暂时无法读取会话。");
    conversationList.replaceChildren(...page.conversations.map(renderConversation));
    conversationFeedback.textContent = page.conversations.length ? "" : (before ? "这一页没有可打开的会话。" : "还没有售后对话。");
    const first = document.getElementById("conversation-first");
    const next = document.getElementById("conversation-next");
    first.hidden = !before;
    next.hidden = !page.next_before;
    if (page.next_before) next.href = "/conversations?before=" + encodeURIComponent(page.next_before);
    document.getElementById("conversation-pages").hidden = first.hidden && next.hidden;
  } catch (error) {
    conversationFeedback.textContent = error.message || "暂时无法读取会话，请刷新重试。";
  }
}

loadConversations();
