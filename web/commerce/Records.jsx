/** 负责订单和售后队列，以及与当前记录关联的详情区。 */
import {useEffect, useRef, useState} from "react";
import {casePattern, caseUrl, eventRoles, localTime, openInline, orderPattern, orderUrl, request, stages, yuan} from "./core.js";
import {flushSync} from "react-dom";
import {OrderDetails} from "./Orders.jsx";
import {CaseDetails} from "./Case.jsx";
import {Empty, Status} from "./Widgets.jsx";

/** 在支持的浏览器里让选中的单据和详情连续过渡。 */
function reveal(update) {
  if (document.startViewTransition && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
    const transition = document.startViewTransition(() => flushSync(update));
    // 快速连点或离开页面时浏览器会主动跳过动画，状态更新仍然执行。
    transition.ready.catch(error => {
      if (!["AbortError","InvalidStateError"].includes(error.name)) console.error(error);
    });
  } else update();
}

/** 筛选、翻页和刷新时保持当前记录，丢弃已经被新选择取代的请求。 */
export function Records({role, orders}) {
  const api = "/api/" + role + "/mall";
  const filters = orders ? {"": "全部包裹", ...stages} : {
    all: "全部记录", pending: "待我处理", soon: "4 小时内到期", overdue: "已超时",
  };
  const [filter, setFilter] = useState(orders ? "" : role === "buyer" ? "all" : "pending");
  const [search, setSearch] = useState("");
  const [rows, setRows] = useState([]);
  const [layout, setLayout] = useState(orders ? "cards" : "board");
  const lastLink = useRef(null);
  const [cursor, setCursor] = useState(null);
  const [listStatus, setListStatus] = useState("正在读取…");
  const [listBusy, setListBusy] = useState(false);
  const [selection, setSelection] = useState({data: null, code: "", action: "", item: "", status: ""});
  const listRequest = useRef(0);
  const detailRequest = useRef(0);
  const detail = useRef();
  const list = useRef();
  const appliedSearch = useRef("");

  async function loadQueue({append = false, nextFilter = filter, nextSearch = search} = {}) {
    const version = ++listRequest.current;
    const query = new URLSearchParams({
      [orders ? "prefix" : "order_id"]: append ? appliedSearch.current : nextSearch.trim(),
      [orders ? "stage" : "queue"]: nextFilter,
    });
    if (append && cursor) query.set(orders ? "after" : "before", cursor);
    setListBusy(true);
    setListStatus("正在读取…");
    try {
      const data = await request(api + (orders ? "/orders?" : "/cases?") + query);
      if (version !== listRequest.current) return;
      if (!append) appliedSearch.current = nextSearch.trim();
      const items = orders ? data.orders : data.cases;
      setRows(previous => append ? [...previous, ...items] : items);
      setCursor(orders ? data.next_after : data.next_before);
      setListStatus(!items.length && !append ? "没有符合条件的记录。" : "");
    } catch (error) {
      if (version === listRequest.current) setListStatus(error.message);
    } finally {
      if (version === listRequest.current) setListBusy(false);
    }
  }

  async function loadDetail(code, {action = "", item = "", scroll = false} = {}) {
    const version = ++detailRequest.current;
    const href = orders ? orderUrl(code, item) : caseUrl(role, code, action);
    if (scroll) {
      lastLink.current = code;
      history.pushState({fromQueue:true}, "", href);
    }
    setSelection(previous => ({...previous, status: "正在读取…"}));
    try {
      const data = await request(api + (orders ? "/orders/" : "/cases/") + encodeURIComponent(code));
      const show = () => {
        if (version !== detailRequest.current) return;
        setSelection({data, code, action, item, status: ""});
        if (scroll) requestAnimationFrame(() => detail.current?.focus({preventScroll:false}));
      };
      if (version === detailRequest.current) scroll ? reveal(show) : show();
    } catch (error) {
      if (version === detailRequest.current) setSelection({data: null, code, action, item, status: error.message});
    }
  }

  useEffect(() => {
    loadQueue();
    function restore() {
      const query = new URLSearchParams(location.search);
      const code = query.get(orders ? "order" : "case") ?? "";
      if ((orders ? orderPattern : casePattern).test(code))
        loadDetail(code, {action:query.get("action"), item:query.get("item")});
      else clearSelection();
    }
    restore();
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, []);

  function refresh() {
    loadQueue();
    if (selection.code) {
      loadDetail(selection.code, {item: new URLSearchParams(location.search).get("item")});
    }
  }

  function caseUpdated(data) {
    setSelection(previous => previous.code === String(data.case_no)
      ? {...previous, data, action: "", status: ""}
      : previous);
    loadQueue();
  }

  /** 回到当前筛选结果，取消仍在路上的旧详情请求。 */
  function clearSelection() {
    ++detailRequest.current;
    setSelection({data:null, code:"", action:"", item:"", status:""});
    const code = lastLink.current;
    if (code) requestAnimationFrame(() => list.current?.querySelector('[data-code="' + CSS.escape(code) + '"]')?.focus());
  }

  function closeRecord() {
    if (history.state?.fromQueue) history.back();
    else {
      history.replaceState(null, "", location.pathname);
      clearSelection();
    }
  }

  const heading = orders ? "订单" : "售后单";
  if (selection.code) return <div className="record-stage">
    <div className="record-tools"><button className="button button-muted" onClick={closeRecord}>← 返回{heading}列表</button>
      <button className="button button-text" id="mall-refresh" onClick={refresh}>刷新详情</button></div>
    <section ref={detail} tabIndex={-1} aria-label={heading + "详情"} className="record-sheet" style={{viewTransitionName:"record-" + selection.code}}>
      {selection.status && <p id="mall-detail-message" role="status">{selection.status}</p>}
      {selection.data && (orders
        ? <OrderDetails key={selection.code} order={selection.data} initialItem={selection.item} api={api}/>
        : <CaseDetails key={selection.code} data={selection.data} role={role} api={api} initialAction={selection.action}
            onUpdated={caseUpdated} reload={() => loadDetail(selection.code)}/>)}
    </section>
  </div>;

  function recordCard(row) {
      const code = String(orders ? row.order_id : row.case_no);
      const href = orders ? orderUrl(code) : caseUrl(role,code);
      const fullTitle = row.first_product || row.shop_name || "";
      const title = orders && fullTitle.startsWith(row.shop_name + " ")
        ? fullTitle.slice(row.shop_name.length).trim() : orders ? fullTitle : row.product;
      return <li key={code}><a href={href} data-code={code} style={{viewTransitionName:"record-" + code}}
        onClick={event => openInline(event,() => loadDetail(code,{scroll:true}))}>
        <div className="stub"><span>{orders ? row.shop_name : row.kind_label}</span>
          <small>{orders ? row.order_id : "#" + code + " · " + row.order_id}</small></div>
        <div className="ticket-body"><strong>{title}{orders && row.lines > 1 && " 等 " + row.lines + " 款"}</strong>
          <small>{orders ? localTime(row.ordered_at) : "更新于 " + localTime(row.updated_at)}</small></div>
        <div className="ticket-foot"><div>{orders ? row.stages.map(stage => <Status key={stage} tone={stage}>{stages[stage]}</Status>)
          : <Status tone={row.deadline}>{row.status_label}</Status>}</div>
          <span>{orders && row.units ? row.units + " 件 · 商品 " + yuan(row.goods_paid) : row.owner ? row.owner + "处理" : ""}</span>
          <i aria-hidden="true">↗</i></div>
      </a></li>;
  }

  const lanes = new Map();
  if (layout === "board") for (const row of rows) {
    if (!lanes.has(row.owner)) lanes.set(row.owner, []);
    lanes.get(row.owner).push(row);
  }
  const ownerOrder = [...new Set([eventRoles[role], ...Object.values(eventRoles), null])];
  const modes = orders ? [["cards","卡片"],["ledger","清单"]] : [["board","按处理人"],["ledger","清单"]];
  return <section className="record-library" data-library={orders ? "orders" : "cases"}>
    <header className="library-tools">
      <div className="range-tabs" aria-label={orders ? "包裹状态" : "处理范围"}>{Object.entries(filters).map(([key,label]) =>
        <button type="button" key={key} aria-pressed={filter === key} onClick={() => {
          setFilter(key); loadQueue({nextFilter:key});
        }}>{label}</button>)}</div>
      <div className="library-controls">
        <form className="queue-filter" onSubmit={event => {event.preventDefault();loadQueue();}}>
          <input id="mall-prefix" aria-label={orders ? "搜索订单" : "搜索售后单"} type="search" value={search} maxLength={60}
            onChange={event => setSearch(event.target.value)} placeholder={orders ? "订单号、商品、规格或店铺" : "售后单号、订单号、商品或店铺"}/><button className="button button-primary">查询</button>
        </form>
        <div className="layout-toggle" aria-label="排列方式">{modes.map(([key,label]) =>
          <button key={key} aria-pressed={layout === key} onClick={() => reveal(() => setLayout(key))}>{label}</button>)}</div>
        <button id="mall-refresh" className="button button-muted" onClick={refresh}>刷新</button>
      </div>
    </header>
    <p id="mall-list-message" role="status" className="feedback">{selection.status || listStatus}</p>
    {layout === "board" ? <div className="case-board" id="mall-list" ref={list}>
      {ownerOrder.filter(owner => lanes.has(owner)).map(owner => <section key={owner || "closed"} data-mine={owner === eventRoles[role]}>
        <h2>{owner === eventRoles[role] ? "待我处理" : owner ? "待" + owner + "处理" : "已办结"}<span>{lanes.get(owner).length}</span></h2>
        <ul className="record-collection" data-layout="cards">{lanes.get(owner).map(recordCard)}</ul></section>)}
    </div> : <ul className="record-collection" data-layout={layout} ref={list} id="mall-list">{rows.map(recordCard)}</ul>}
    {!rows.length && !listBusy && <Empty heading={listStatus === "没有符合条件的记录。" ? "这个范围没有" + heading : "未载入" + heading}>
      {filter !== (orders ? "" : "all") && <button className="button button-soft" onClick={() => {
        const all = orders ? "" : "all"; setFilter(all);loadQueue({nextFilter:all});
      }}>查看全部{heading}</button>}</Empty>}
    {cursor && <button id="mall-more" className="button button-muted queue-more" disabled={listBusy}
      onClick={() => loadQueue({append:true})}>加载更多</button>}
  </section>;
}
