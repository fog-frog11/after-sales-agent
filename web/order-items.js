/** 在订单页和对话卡片中展示同一份商品明细与服务端金额。 */

function renderOrderItems(container, detail) {
  const title = document.createElement("h3");
  title.textContent = "商品明细";
  container.replaceChildren(title);
  if (detail?.status !== "found") {
    const note = document.createElement("p");
    note.className = "ticket-feedback";
    note.textContent = "这笔订单暂无商品明细。";
    container.append(note);
    return;
  }
  const wrap = document.createElement("div");
  wrap.className = "item-table-wrap";
  const table = document.createElement("table");
  const symbol = detail.currency === "CNY" ? "¥" : "R$ ";
  table.className = "item-table";
  table.setAttribute("aria-label", "商品明细");
  const header = table.createTHead().insertRow();
  for (const label of ["序号", "商品", "数量", "单价", "运费"]) {
    const cell = document.createElement("th");
    cell.scope = "col";
    cell.textContent = label;
    header.append(cell);
  }
  const body = table.createTBody();
  for (const item of detail.items) {
    const row = body.insertRow();
    for (const value of [item.item_no, item.category_label ?? item.category ?? "未记录", item.quantity ?? 1,
      symbol + item.price, item.freight == null ? "未记录" : symbol + item.freight]) {
      row.insertCell().textContent = value;
    }
  }
  const totals = document.createElement("p");
  totals.className = "catalog-totals";
  const freight = detail.freight_total == null ? "运费未记录" : "运费 " + symbol + detail.freight_total;
  const total = detail.recorded_total == null ? "" : " · 合计 " + symbol + detail.recorded_total;
  totals.textContent = detail.quantity + " 件 · 商品 " + symbol + detail.items_total + " · " + freight + total;
  wrap.append(table);
  container.append(wrap, totals);
}
