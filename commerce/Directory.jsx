/** 平台商城档案及店铺的订单查询，沿用原有分页与权限接口。 */
import {useEffect, useRef, useState} from "react";
import {caseUrl, localTime, openInline, orderPattern, request, stages, yuan} from "./core.js";
import {Status} from "./Widgets.jsx";

const kinds = {shops: "店铺", buyers: "买家", orders: "订单"};
const headings = {
  shops: ["店铺", "主营类目", "城市", "入驻日期", "订单数", "业务"],
  buyers: ["买家", "城市", "注册日期", "订单数", "业务"],
  orders: ["订单号", "买家", "店铺", "商品数量", "金额", "包裹", "下单时间"],
};
const placeholders = {shops: "搜索店铺、账号、城市或类目", buyers: "搜索买家、账号或城市", orders: "订单号、买家、店铺、商品或规格"};

function directoryUrl(view, search, order) {
  const params = new URLSearchParams({view});
  if (search) params.set("search", search);
  if (order) params.set("order", order);
  return location.pathname + "?" + params;
}

/** 目录项可直接打开订单详情，也可按编号和关键词翻页。 */
export function Directory({role}) {
  const params = new URLSearchParams(location.search);
  const requested = params.get("view");
  const view = role === "staff" && Object.hasOwn(kinds, requested) ? requested : "orders";
  const api = "/api/" + role + "/mall/catalog";
  const [search, setSearch] = useState(params.get("search") ?? "");
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(true);
  const [status, setStatus] = useState("正在查询…");
  const [order, setOrder] = useState(null);
  const [cards, setCards] = useState(true);
  const listVersion = useRef(0);
  const orderVersion = useRef(0);
  const drawer = useRef();

  async function page(number) {
    const version = ++listVersion.current;
    setBusy(true);
    setStatus("正在查询…");
    try {
      const data = await request(api + "/" + view + "?" + new URLSearchParams({page: number, search: search.trim()}));
      if (version !== listVersion.current) return;
      setData(data);
      setStatus(data.total ? "共 " + data.total + " 条记录" : "没有符合条件的记录。");
    } catch (error) {
      if (version === listVersion.current) {
        setStatus(error.message);
        setData(null);
      }
    } finally {
      if (version === listVersion.current) setBusy(false);
    }
  }

  async function open(code) {
    const version = ++orderVersion.current;
    history.replaceState(null, "", directoryUrl(view, search, code));
    setOrder({code, status: "正在读取订单…"});
    if (!drawer.current?.open) drawer.current?.showModal();
    try {
      const data = await request(api + "/orders/" + encodeURIComponent(code));
      if (version === orderVersion.current) setOrder({code, data});
    } catch (error) {
      if (version === orderVersion.current) setOrder({code, status: error.message});
    }
  }

  function close() {
    ++orderVersion.current;
    setOrder(null);
    history.replaceState(null, "", directoryUrl(view, search));
  }

  useEffect(() => {
    page(1);
    const code = params.get("order") ?? "";
    if (view === "orders" && orderPattern.test(code)) open(code);
  }, []);

  const current = data?.page ?? 1;
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  return <div className="directory-workspace" data-view={view}>
    {role === "staff" && data?.counts && <nav id="directory-tabs" className="directory-tabs" aria-label="商城目录">
      {Object.entries(kinds).map(([key, label]) =>
        <a key={key} href={directoryUrl(key)} aria-current={view === key ? "page" : undefined}>
          <span>{label}</span><strong>{data.counts[key]}</strong><i aria-hidden="true">↗</i>
        </a>)}</nav>}
    <section className="surface directory-panel">
      <header className="section-head directory-toolbar"><h2>{kinds[view]}{view === "orders" ? "列表" : "名单"}</h2>
        {view !== "orders" && <div className="layout-toggle" aria-label="档案排列方式">
          <button aria-pressed={cards} onClick={() => setCards(true)}>名片</button>
          <button aria-pressed={!cards} onClick={() => setCards(false)}>表格</button>
        </div>}
        <form className="queue-filter" onSubmit={event => {
          event.preventDefault();
          if (drawer.current.open) drawer.current.close();
          history.replaceState(null, "", directoryUrl(view, search));
          page(1);
        }}>
          <label htmlFor="directory-query" className="sr-only">搜索</label>
          <input id="directory-query" type="search" placeholder={placeholders[view]} value={search} maxLength={60}
            onChange={event => setSearch(event.target.value)} /><button className="button button-primary">查询</button>
        </form>
      </header>
      <p id="directory-message" className="feedback" role="status">{status}</p>
      {view !== "orders" && cards ? <div id="directory-rows" className="directory-profiles">{data?.rows.map(row =>
        <article key={row.code}><header><i aria-hidden="true">{row.name.slice(0,1)}</i><div><h3>{row.name}</h3>
          <small>{row.accounts ?? row.code}</small></div></header>
          <p>{[row.category,row.city,row.joined_on].filter(Boolean).join(" · ")}</p>
          {row.description && <p>{row.description}</p>}
          <a href={directoryUrl("orders",row.code)}>查看订单 <b>{row.order_count} ↗</b></a>
        </article>)}</div> : <div className="table-scroll" tabIndex={0} aria-label={kinds[view] + "列表"}>
        <table className="directory-table"><thead><tr>{headings[view].map(label => <th key={label} scope="col">{label}</th>)}</tr></thead>
          <tbody id="directory-rows">{data?.rows.map(row => view === "orders"
            ? <tr key={row.code}>
              <th scope="row"><a href={directoryUrl(view, search, row.code)} onClick={event => openInline(event, () => open(row.code))}>{row.code} ↗</a></th>
              <td>{row.buyer_name}</td><td>{row.shop_name}</td><td>{row.units} 件 · {row.line_count} 种</td>
              <td>{yuan(row.paid_total_with_shipping ?? row.goods_paid)}</td>
              <td><div className="table-badges">{row.stages.map(stage => <Status key={stage} tone={stage}>{stages[stage]}</Status>)}</div></td>
              <td>{localTime(row.ordered_at)}</td></tr>
            : <tr key={row.code}><th scope="row">{row.name}<small>{row.accounts ?? row.code}</small></th>
              {view === "shops" && <td>{row.category || "—"}{row.description && <small>{row.description}</small>}</td>}
              <td>{row.city || "—"}</td><td>{row.joined_on || "—"}</td><td>{row.order_count}</td>
              <td><a href={directoryUrl("orders", row.code)}>查看订单 ↗</a></td></tr>)}</tbody>
        </table>
      </div>}
      <nav className="pager" aria-label="列表分页">
        <button id="directory-prev" className="button button-muted" type="button" disabled={busy || current <= 1}
          onClick={() => page(current - 1)}>上一页</button>
        <span id="directory-page">{current} / {pages}</span>
        <button id="directory-next" className="button button-muted" type="button" disabled={busy || current >= pages}
          onClick={() => page(current + 1)}>下一页</button>
      </nav>
    </section>
    <dialog className="order-drawer" id="directory-detail" ref={drawer} onClose={close} aria-labelledby="directory-order-title">
      <header className="section-head"><h2 id="directory-order-title">订单 {order?.code}</h2>
        <button className="button button-muted" type="button" onClick={() => drawer.current.close()}>关闭</button></header>
      {order?.status && <p className="feedback" role="status">{order.status}</p>}
      {order?.data && <OrderFacts data={order.data} role={role} />}
    </dialog>
  </div>;
}

function OrderFacts({data, role}) {
  return <div className="directory-detail">
    <p className="quiet">{[data.buyer_name, data.shop_name, localTime(data.ordered_at), data.payment_method].filter(Boolean).join(" · ")}</p>
    <dl className="facts-grid" id="directory-totals">
      {[["商品原价", data.list_total], ["优惠", data.discount_total], ["配送费", data.shipping],
        [data.shipping == null ? "商品实付" : "订单实付", data.paid_total], ["已退款", data.refunded_total]].map(([label, amount]) =>
        <div key={label}><dt>{label}</dt><dd>{yuan(amount)}</dd></div>)}
    </dl>
    <div className="directory-items" id="directory-items">{data.items.map(item =>
      <article key={item.item_no} className="product-card">
        <div className="section-head"><h3>{item.title}</h3><Status tone={item.stage}>{stages[item.stage]}</Status></div>
        <small className="quiet">{item.variant} · {item.sku}</small>
        <p>{item.quantity} 件 · 每件 {yuan(item.paid_unit)}</p>
        <p className="quiet">包裹 {item.package_no}{item.tracking && " · " + item.carrier + " " + item.tracking}
          {item.delivered_at ? " · 签收 " + localTime(item.delivered_at)
            : item.stage !== "refunded" && item.expected_on && " · 预计 " + item.expected_on}</p>
      </article>)}</div>
    {!!data.cases.length && <div className="record-links">{data.cases.map(entry =>
      <a key={entry.case_no} href={caseUrl(role, entry.case_no)}>售后单 #{entry.case_no} <Status>{entry.status_label}</Status></a>)}</div>}
  </div>;
}
