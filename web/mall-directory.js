/** 展示平台档案与店铺订单，详情沿用商城订单的业务字段。 */
const node = id => document.getElementById("directory-" + id);
const staff = location.pathname === "/staff/mall/catalog";
const params = new URLSearchParams(location.search);
const labels = {shops: "店铺", buyers: "买家", orders: "订单"};
const selected = params.get("view");
const view = staff && Object.hasOwn(labels, selected) ? selected : "orders";
const headings = {
  shops: ["店铺", "主营类目", "城市", "入驻日期", "订单数", "业务"],
  buyers: ["买家", "城市", "注册日期", "订单数", "业务"],
  orders: ["订单号", "买家", "店铺", "商品数量", "金额", "包裹", "下单时间"],
};
const stages = {preparing: "待发货", in_transit: "运输中", delivered: "已签收", refunded: "已退款"};
const api = "/api/" + (staff ? "staff" : "merchant") + "/mall/catalog";
const casesPage = staff ? "/staff/disputes" : "/merchant/mall";
const orderPattern = /^CN-[0-9]{4,10}$/;
const currency = new Intl.NumberFormat("zh-CN", {style: "currency", currency: "CNY"});
const clock = new Intl.DateTimeFormat("zh-CN", {dateStyle: "short", timeStyle: "short", timeZone: "Asia/Shanghai"});
let page = 1;
let requestNumber = 0;
let detailNumber = 0;

/** 构建只包含文字的节点，名称和说明来自业务记录。 */
function text(tag, value) {
  const child = document.createElement(tag);
  child.textContent = value ?? "—";
  return child;
}

function link(label, href) {
  const anchor = text("a", label);
  anchor.className = "catalog-link";
  anchor.href = href;
  return anchor;
}

/** 保留当前分类和关键词，直接打开订单时可以复制页面地址。 */
function directoryUrl(kind, keyword = "", order = "") {
  const query = new URLSearchParams({view: kind});
  if (keyword) query.set("search", keyword);
  if (order) query.set("order", order);
  return location.pathname + "?" + query;
}

async function read(path) {
  const response = await sessionFetch(api + path);
  const data = await response.json();
  if (!response.ok) throw new Error(data.message ?? "当前无法读取，请重试。");
  return data;
}

/** 为每个目录生成行，商家页面由服务端约束到当前店铺。 */
function makeRow(row) {
  const line = document.createElement("tr");
  if (view === "orders") {
    const anchor = link(row.code, directoryUrl(view, node("query").value, row.code));
    anchor.addEventListener("click", event => {
      if (event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      loadOrder(row.code);
    });
    const orderCell = document.createElement("td");
    orderCell.append(anchor);
    line.append(orderCell, text("td", row.buyer_name), text("td", row.shop_name),
      text("td", `${row.units} 件 · ${row.line_count} 种`), text("td", currency.format(row.paid_total_with_shipping ?? row.goods_paid)),
      text("td", row.stages.map(stage => stages[stage]).join(" / ")), text("td", clock.format(new Date(row.ordered_at))));
    return line;
  }
  const identity = text("td", row.name);
  identity.append(text("small", view === "shops" ? row.accounts ?? row.code : row.code));
  const orderCell = document.createElement("td");
  orderCell.append(link("查看订单", directoryUrl("orders", row.code)));
  line.append(identity);
  if (view === "shops") {
    const category = text("td", row.category);
    if (row.description) category.append(text("small", row.description));
    line.append(category);
  }
  line.append(text("td", row.city), text("td", row.joined_on), text("td", row.order_count), orderCell);
  return line;
}

/** 按服务端分页返回的记录数刷新目录和上、下页。 */
async function loadPage(target = 1) {
  const version = ++requestNumber;
  const search = node("query").value.trim();
  const query = new URLSearchParams({search, page: target});
  node("message").textContent = "正在查询…";
  node("prev").disabled = node("next").disabled = true;
  try {
    const data = await read("/" + view + "?" + query);
    if (version !== requestNumber) return;
    page = target;
    if (data.counts) {
      node("tabs").replaceChildren(...Object.entries(labels).map(([kind, label]) => {
        const anchor = link(label + " " + data.counts[kind], directoryUrl(kind));
        if (kind === view) anchor.setAttribute("aria-current", "page");
        return anchor;
      }));
    }
    node("rows").replaceChildren(...data.rows.map(makeRow));
    node("message").textContent = data.total ? "共 " + data.total + " 条记录" : "没有符合条件的记录。";
    const lastPage = Math.max(1, Math.ceil(data.total / data.page_size));
    node("page").textContent = page + " / " + lastPage;
    node("prev").disabled = page <= 1;
    node("next").disabled = page >= lastPage;
  } catch (error) {
    if (version !== requestNumber) return;
    node("rows").replaceChildren();
    node("message").textContent = error.message;
  }
}

/** 打开商品、购买金额、包裹和当前角色可访问的售后单。 */
async function loadOrder(code) {
  const version = ++detailNumber;
  node("detail").hidden = false;
  node("order-title").textContent = "订单 " + code;
  node("order-summary").textContent = "正在读取订单…";
  node("detail").scrollIntoView({block: "start"});
  for (const id of ["totals", "items", "order-cases"]) node(id).replaceChildren();
  try {
    const data = await read("/orders/" + encodeURIComponent(code));
    if (version !== detailNumber) return;
    history.replaceState(null, "", directoryUrl(view, node("query").value, code));
    node("order-summary").textContent = [data.buyer_name, data.shop_name, clock.format(new Date(data.ordered_at)),
      data.payment_method].filter(Boolean).join(" · ");
    const totals = [["商品原价", data.list_total], ["优惠", data.discount_total], ["配送费", data.shipping],
      [data.shipping === null ? "商品实付" : "订单实付", data.paid_total], ["已退款", data.refunded_total]];
    node("totals").replaceChildren(...totals.map(([label, amount]) => {
      const group = document.createElement("div");
      group.append(text("dt", label), text("dd", amount === null ? "待查" : currency.format(amount)));
      return group;
    }));
    node("items").replaceChildren(...data.items.map(item => {
      const line = document.createElement("tr");
      const name = text("td", item.title);
      name.append(text("small", item.variant + " · " + item.sku));
      const shipment = text("td", "包裹 " + item.package_no + " · " + stages[item.stage]);
      if (item.tracking) shipment.append(text("small", item.carrier + " " + item.tracking));
      if (item.delivered_at) shipment.append(text("small", "签收 " + clock.format(new Date(item.delivered_at))));
      else if (item.expected_on && item.stage !== "refunded") shipment.append(text("small", "预计 " + item.expected_on));
      line.append(name, text("td", item.quantity + " 件"), text("td", currency.format(item.paid_unit)), shipment);
      return line;
    }));
    node("order-cases").replaceChildren(...data.cases.map(entry =>
      link("售后单 #" + entry.case_no + " · " + entry.status_label, casesPage + "?case=" + entry.case_no)));
  } catch (error) {
    if (version === detailNumber) node("order-summary").textContent = error.message;
  }
}

node("title").textContent = staff ? "商城档案" : "本店订单";
document.title = node("title").textContent + " · 商城售后";
node("home").href = casesPage;
node("cases").href = casesPage;
node("cases").textContent = staff ? "平台介入" : "本店商品售后";
node("current").href = location.pathname;
node("current").textContent = node("title").textContent;
node("tabs").hidden = !staff;
node("list-title").textContent = labels[view] + (view === "orders" ? "列表" : "名单");
node("query").placeholder = {
  shops: "店铺名、账号、城市或类目", buyers: "买家姓名、账号或城市", orders: "订单号、买家或店铺",
}[view];
node("query").value = params.get("search") ?? "";
node("columns").replaceChildren(...headings[view].map(heading => {
  const column = text("th", heading);
  column.scope = "col";
  return column;
}));
node("search").addEventListener("submit", event => {
  event.preventDefault();
  history.replaceState(null, "", directoryUrl(view, node("query").value));
  detailNumber++;
  node("detail").hidden = true;
  loadPage();
});
node("prev").addEventListener("click", () => loadPage(page - 1));
node("next").addEventListener("click", () => loadPage(page + 1));
loadPage();
if (view === "orders" && orderPattern.test(params.get("order") ?? "")) loadOrder(params.get("order"));
