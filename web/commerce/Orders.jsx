/** 买家的订单详情、商品咨询及售后申请。 */
import {useEffect, useRef, useState} from "react";
import {actions, caseUrl, encodePhotos, formatAddress, interventionAction, localTime,
  orderUrl, reasons, request, stages, submissionKey, yuan} from "./core.js";
import {Status} from "./Widgets.jsx";
import portfolio from "../../app/portfolio_cases.json";
import {ChatActionCard} from './ChatActionCard.jsx';

const portfolioOrders = new Set(portfolio.cases.flatMap(entry => entry.orders.map(order => order.id)));
const consultationRoutes = {rules: "本地规则", function_call: "DeepSeek 规划查询", fallback: "模型不可用，按本地规则"};
const retrievalRoutes = {scoped_bm25:"范围内词法召回",milvus_bm25:"Milvus BM25",milvus_dense:"Milvus 稠密召回",e5_dense:"E5 语义召回",
  rrf:"RRF 融合",evidence_window_rerank:"证据窗口排序",rrf_prior:"保留融合名次"};

const suggestions = [
  ["找其他订单", "找订单："],
  ["退货运费", "如果退货，运费谁承担？"],
  ["包装破损", "外包装破了，要拍哪些照片？"],
  ["查本单条款", "查本单条款"],
  ["查物流", "这件商品的物流到哪了？"],
  ["查售后进度", "这件商品的售后办到哪了？"],
];
const emptyTurns = [];

/** 按商品身份保存会话，切换商品时使用各自的咨询记录。 */
export function OrderDetails({order, initialItem, api}) {
  const [line, setLine] = useState(initialItem);
  const [parcel,setParcel] = useState("");
  const [returning, setReturning] = useState(null);
  const [sessions, setSessions] = useState({});
  const orderView = useRef();
  const product = order.items.find(item => String(item.item_no) === String(line));
  const exists = order.cases.find(entry => (entry.item_nos ?? [entry.item_no]).includes(product?.item_no));
  const parcels = [...new Map(order.items.map(item => [String(item.package_no),item])).values()];
  const visibleItems = parcel ? order.items.filter(item => String(item.package_no) === parcel) : order.items;
  const activeItems = new Set(order.cases.filter(entry => entry.is_active).flatMap(entry => entry.item_nos ?? [entry.item_no]));
  const totals = [["商品原价", yuan(order.list_total)], ["优惠", yuan(order.discount_total)],
    ["配送费", yuan(order.shipping)], [order.shipping == null ? "商品实付" : "订单实付", yuan(order.paid_total ?? order.total)]];

  function select(item) {
    setLine(item);
    setReturning(null);
    history.replaceState(history.state, "", orderUrl(order.order_id, item));
    orderView.current?.scrollIntoView({block: "start"});
  }

  function updateTurns(item, transform) {
    setSessions(current => ({...current, [item]: transform(current[item] ?? emptyTurns)}));
  }

  /** 定位到本单的实际内容，同时为键盘操作展开并聚焦付款明细。 */
  function jumpTo(id) {
    const section = orderView.current.querySelector("#" + id);
    if (!section) return;
    if (section instanceof HTMLDetailsElement) section.open = true;
    const heading = section.querySelector("summary, h2");
    heading?.focus({preventScroll: true});
    section.scrollIntoView({block: "start", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth"});
  }

  return <div id="mall-order-view" ref={orderView} className="order-detail">
    <section className="order-overview" aria-labelledby="mall-selection-title" hidden={Boolean(product)}>
      <div className="order-identity">
        <p>{order.shop_name}</p>
        <h2 id="mall-selection-title">订单 {order.order_id}</h2>
        <time dateTime={order.ordered_at}>下单于 {localTime(order.ordered_at)}</time>
        <nav className="order-sections" aria-label="订单详情快捷定位">
          <button type="button" aria-controls="order-goods" onClick={() => jumpTo("order-goods")}>商品与物流 · {parcels.length} 个包裹</button>
          <button type="button" aria-controls="order-payment" onClick={() => jumpTo("order-payment")}>付款与收货</button>
          {!!order.cases.length && <button type="button" aria-controls="order-cases" onClick={() => jumpTo("order-cases")}>关联售后</button>}
        </nav>
      </div>
      <div className="parcel-art" aria-hidden="true"><i/><i/><i/></div>
      <div className="order-total"><span>{totals[3][0]}</span><strong>{totals[3][1]}</strong>
        {Number(order.refunded_total) > 0 && <small>已登记退款 {yuan(order.refunded_total)}</small>}</div>
    </section>
    {product && <Chat key={product.item_no} order={order} product={product} existing={exists} api={api}
      turns={sessions[product.item_no] ?? emptyTurns} seen={Object.hasOwn(sessions, product.item_no)}
      update={transform => updateTurns(product.item_no, transform)} onBack={() => select("")} />}
    <section id="order-goods" className="surface order-products" aria-labelledby="order-products-title" hidden={Boolean(product)}>
      <div className="section-head"><h2 id="order-products-title" tabIndex={-1}>商品明细</h2><span className="quiet">{order.items.length} 种商品</span></div>
      <div className="parcel-selector" aria-label="按包裹查看商品">
        <button aria-pressed={!parcel} onClick={() => {setParcel(""); setReturning(null);}}>全部商品</button>
        {parcels.map(item => <button key={item.package_no} aria-pressed={parcel === String(item.package_no)}
          onClick={() => {setParcel(String(item.package_no)); setReturning(null);}}>包裹 {item.package_no} · {stages[item.stage]}</button>)}
      </div>
      {order.package_returns && parcels.filter(item => item.stage === "in_transit" && (!parcel || parcel === String(item.package_no)))
        .map(item => {
          const items = order.items.filter(product => product.package_no === item.package_no);
          if (items.length < 2 || items.some(product => activeItems.has(product.item_no)
              || product.remaining_quantity !== product.quantity)) return null;
          return <div key={item.package_no} className="case-callout">
            <button className="button button-soft" type="button" aria-expanded={returning === item.package_no}
              onClick={() => setReturning(returning === item.package_no ? null : item.package_no)}>
              包裹 {item.package_no} · 整包在途退回
            </button>
            {returning === item.package_no && <PackageReturn order={order} items={items} api={api}/>}
          </div>;
        })}
      <div id="mall-products" className="product-grid">
        {visibleItems.map(item => <article key={item.item_no} className="product-card" data-stage={item.stage}>
          <div className="product-intro"><span className="parcel-icon" aria-hidden="true"></span>
            <div><h3>{item.title}</h3><p>{item.variant}</p></div></div>
          <div className="product-money"><strong>{yuan(item.paid_unit)}</strong><span>× {item.quantity} 件</span></div>
          <div className="product-shipment"><Status tone={item.stage}>{stages[item.stage]}</Status>
            <small>包裹 {item.package_no}{item.tracking && " · " + item.carrier + " " + item.tracking}</small></div>
          {item.stage !== "refunded" && <ol className="parcel-rail" aria-label="原包裹行程">
            {["preparing","in_transit","delivered"].map((stage,index) => <li key={stage}
              data-reached={index <= ["preparing","in_transit","delivered"].indexOf(item.stage)}
              aria-current={stage === item.stage ? "step" : undefined}>{stages[stage]}</li>)}
          </ol>}
          {(item.delivered_at || item.stage !== "refunded" && item.expected_on) && <small className="quiet">
            {item.delivered_at ? "签收 " + localTime(item.delivered_at) : "预计送达 " + item.expected_on}</small>}
          {Number(item.refunded_amount) > 0 && <p className="quiet">已登记退款 {yuan(item.refunded_amount)} · 剩余 {item.remaining_quantity} 件</p>}
          <button type="button" className="button button-soft" onClick={() => select(item.item_no)}>
            {activeItems.has(item.item_no) || !item.remaining_quantity
              ? "售后咨询" : item.stage === "in_transit" ? "咨询物流或申请在途退回" : "咨询或申请售后"} <span aria-hidden="true">↗</span></button>
        </article>)}
      </div>
    </section>
    <details id="order-payment" className="surface order-facts" open={!product}>
      <summary>付款与收货信息</summary>
      <dl id="mall-order-payment" className="facts-grid">
        {[...totals, ["付款方式", order.payment_method ?? "待查"]].map(([label, value]) =>
          <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
      </dl>
      <p id="mall-order-address" hidden={!order.delivery_address}>原订单收货信息 · {order.delivery_address && formatAddress(order.delivery_address)}</p>
      <details className="terms"><summary>本单购买时 · {order.terms_title} · 第 {order.terms_version} 版</summary>
        <ul>{order.terms.map(term => <li key={term}>{term}</li>)}</ul></details>
      {order.current_policy && <details className="terms"><summary>店铺现行条款与平台保障</summary>
        <h4>店铺第 {order.current_policy.version} 版 · {order.current_policy.effective_on} 起生效</h4>
        <ul>{order.current_policy.terms.map(line => <li key={line}>{line}</li>)}</ul>
        <h4>平台最低保障 · 第 {order.current_policy.platform_version} 版</h4>
        <ul>{order.current_policy.platform.map(line => <li key={line}>{line}</li>)}</ul></details>}
    </details>
    {!!order.cases.length && <section id="order-cases" className="surface record-links"><h2 tabIndex={-1}>关联售后</h2>
      {order.cases.map(row => <a key={row.case_no} href={caseUrl("buyer", row.case_no)}>
        #{row.case_no} · {row.product} <Status>{row.status_label}</Status></a>)}
    </section>}
  </div>;
}

/** 确认整个包裹的商品，交给现有售后单办理。 */
function PackageReturn({order, items, api}) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const draft = useRef(null);
  const pending = useRef(false);

  async function apply(event) {
    event.preventDefault();
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setMessage("");
    const fields = event.currentTarget.elements;
    try {
      const body = {reason:"wrong_purchase", description:fields.description.value.trim(),
        photos:await encodePhotos(fields.photos)};
      const result = await request(api + "/orders/" + order.order_id + "/packages/" + items[0].package_no + "/cases",
        {...body, submission_id:submissionKey(draft, body)});
      location.assign(caseUrl("buyer", result.case_no));
    } catch (error) {
      setMessage(error.message);
      setBusy(false);
      pending.current = false;
    }
  }

  return <form className="action-fields" aria-label="整包在途退回申请" onSubmit={apply}>
    <p>本次申请退回这个包裹里的全部商品。店铺会核实拦截或拒收结果。</p>
    <ul>{items.map(item => <li key={item.item_no}>{item.title} · {item.variant} · {item.quantity} 件</li>)}</ul>
    <label>申请说明<textarea name="description" maxLength={500} required /></label>
    <label>照片凭证<input name="photos" type="file" accept="image/jpeg,image/png" multiple /></label>
    {message && <p role="alert" className="error-message">{message}</p>}
    <button className="button button-primary" disabled={busy}>{busy ? "正在提交…" : "提交整包退回"}</button>
  </form>;
}

/** 显示已发消息、服务器回复、条款依据和原单操作入口。 */
function Chat({order, product, existing, api, turns, seen, update, onBack}) {
  const [continuationError, setContinuationError] = useState("");
  const continuing = useRef(false);
  const [option, setOption] = useState(null);
  const [focused, setFocused] = useState(false);
  const [notice, setNotice] = useState("先选择原因，系统会核对可办理的方式。");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [requestedUnits,setRequestedUnits] = useState(1);
  const [chatCase,setChatCase]=useState(existing??null);
  const quantityEdited=useRef(false);
  const packageUnits = order.items.filter(item => item.package_no === product.package_no)
    .reduce((total,item) => total + item.quantity, 0);
  const form = useRef();
  const questionInput = useRef();
  const scroll = useRef();
  const applicationRequest = useRef(0);
  const draft = useRef(null);
  const alive = useRef(true);
  const sending = useRef(false);
  const historyLoad = useRef(null);
  const lastSuggestion = useRef(null);
  const consult = api + "/orders/" + order.order_id + "/items/" + product.item_no + "/consult";
  const pending = turns.some(turn => !turn.result && !turn.error);
  const latestSignature=turns.findLast(turn=>turn.result?.task_signature)?.result.task_signature;
  const latestRevision=turns.findLast(turn=>turn.result?.task_revision!=null)?.result.task_revision??0;
  const activeDraft=turns.findLast(turn=>turn.result?.action_draft?.state==='ready' && !turn.result?.action_execution
    && turn.result.action_draft.memory_signature===latestSignature
    && (turn.result.action_draft.memory_revision??0)===latestRevision)?.result.action_draft.id;
  const preparedApplication=Boolean(activeDraft && turns.some(turn=>turn.result?.action_draft?.id===activeDraft && turn.result.action_draft.action==='submit'));

  useEffect(()=>{
    if (!preparedApplication && !chatCase?.is_active && form.current) useResult(turns.at(-1)?.result,false);
  },[preparedApplication,chatCase?.is_active]);

  async function confirmDraft(card,payload) {
    const saved=await request(consult+'/actions/'+card.id+'/confirm',payload);
    update(previous=>previous.map(turn=>turn.result?.action_draft?{...turn,result:{...turn.result,
      action_draft:{...turn.result.action_draft,state:turn.result.action_draft.id===card.id?'ready':'outdated'},
      ...(turn.result.action_draft.id===card.id?{action_execution:saved,case_no:saved.case_no,current_case:saved,kinds:{},eligibility:null}:{} )}}:turn));
    const is_active=saved.is_active;
    setChatCase({...saved,is_active});
    setOption(is_active?{case_no:saved.case_no,kinds:{}}:null);
    return saved;
  }

  function useResult(result, autoFill) {
    if (result && 'current_case' in result) setChatCase(result.current_case);
    if (!form.current || !result) return;
    const fields = form.current.elements;
    const prior = lastSuggestion.current;
    if (result.reset_suggestion && !fields.reason.value) {
      setOption(null);
      setNotice('先选择原因，系统会核对可办理的方式。');
    }
    if (prior && (result.reset_suggestion || result.reason && prior.reason !== result.reason)) {
      if (fields.description.value === prior.description) fields.description.value = "";
      if (fields.reason.value === prior.reason) {
        fields.reason.value = "";
        setOption(null);
        setNotice("先选择原因，系统会核对可办理的方式。");
      }
      lastSuggestion.current = null;
    }
    if (!result.reason || (fields.reason.value && fields.reason.value !== prior?.reason && fields.reason.value !== result.reason)) return;
    fields.reason.value = result.reason;
    if (autoFill && result.suggested_description &&
        (!fields.description.value.trim() || fields.description.value === prior?.description)) {
      fields.description.value = result.suggested_description;
    }
    if (autoFill && result.suggested_quantity!=null && !quantityEdited.current) setRequestedUnits(result.suggested_quantity);
    lastSuggestion.current = {reason:result.reason, description:result.suggested_description ?? prior?.description};
    setOption(result);
  }

  useEffect(() => {
    alive.current = true;
    function restore(history) {
      const result = history.map(turn => turn.result).findLast(turn => turn?.reset_suggestion || turn?.reason);
      useResult(result, true);
      if (result?.reason && product.remaining_quantity) checkReason(result.reason, result.recommended_kind);
    }
    if (seen) restore(turns);
    else historyLoad.current = request(consult).then(data => {
      if ('current_case' in data) setChatCase(data.current_case);
      const history = data.turns.map((turn, index) => ({...turn, key: "history-" + index}));
      update(previous => [...history, ...previous.filter(turn => !turn.key.startsWith("history-"))]);
      if (alive.current && !sending.current && form.current && !form.current.elements.reason.value) {
        restore(history);
      }
    }).catch(error => { if (alive.current) setNotice(error.message); });
    return () => { alive.current = false; };
  }, []);

  useEffect(() => {
    scroll.current?.scrollTo({top: scroll.current.scrollHeight});
  }, [turns]);

  function goToApplication(response) {
    setFocused(false);
    requestAnimationFrame(() => {
      if (form.current && response?.reason) {
        useResult(response, true);
        checkReason(response.reason, response.recommended_kind);
      }
      form.current?.scrollIntoView({block: "nearest"});
      form.current?.elements.reason.focus({preventScroll: true});
    });
  }

  /** 文本和追问选项都走同一个请求，一次只发送一轮。 */
  async function sendQuestion(value) {
    const question = value.trim();
    if (!question || pending || sending.current || continuing.current) return;
    sending.current = true;
    const key = crypto.randomUUID();
    const version = applicationRequest.current;
    update(previous => [...previous, {key, question}]);
    if (questionInput.current.value.trim() === question) questionInput.current.value = "";
    try {
      // 先恢复服务端旧记录，防止刚发出的消息被历史接口再次带回来。
      await historyLoad.current;
      const result = await request(consult, {question});
      update(previous => previous.map(turn => turn.key === key ? {...turn, result} : turn));
      if (alive.current && version === applicationRequest.current) useResult(result, true);
    } catch (error) {
      update(previous => previous.map(turn => turn.key === key ? {...turn, error: error.message} : turn));
    } finally { sending.current = false; }
  }

  /** 提交当前待办选择。目标对话中的回复已保存后才导航。 */
  async function continueOn(item, pendingTurn) {
    if (sending.current || continuing.current) return;
    continuing.current = true;
    setContinuationError("");
    try {
      const target = await request(consult + "/continue", {
        pending_turn: pendingTurn, order_id: item.order_id, item_no: item.item_no,
      });
      location.assign(orderUrl(target.order_id, target.item_no));
    } catch (error) {
      setContinuationError(error.message);
    } finally { continuing.current = false; }
  }

  async function checkReason(reason, recommendedKind) {
    const version = ++applicationRequest.current;
    setOption(null);
    setNotice(reason ? "正在核对售后资格…" : "先选择原因，系统会核对可办理的方式。");
    if (!reason) return;
    try {
      const result = await request(consult, {question: "核对可申请的售后与退货运费", reason, record: false});
      if (alive.current && version === applicationRequest.current) {
        setOption({...result, recommended_kind:result.kinds?.[recommendedKind] ? recommendedKind : undefined});
      }
    } catch (error) {
      if (alive.current && version === applicationRequest.current) setNotice(error.message);
    }
  }

  async function apply(event) {
    event.preventDefault();
    if (submitting) return;
    const fields = new FormData(event.currentTarget);
    const files = form.current.elements.photos;
    const data = {item_no: product.item_no, reason: fields.get("reason"), kind: fields.get("kind"),
      quantity: Number(fields.get("quantity")), description: fields.get("description")};
    setSubmitting(true);
    setSubmitError("");
    try {
      data.photos = await encodePhotos(files);
      if (!alive.current) return;
      data.submission_id = submissionKey(draft, data);
      const saved = await request(api + "/orders/" + order.order_id + "/cases", data);
      if (alive.current) location.assign(caseUrl("buyer", saved.case_no));
    } catch (error) {
      if (!alive.current) return;
      if (error.status === 409) await checkReason(data.reason);
      else setSubmitError(error.message);
    } finally { if (alive.current) setSubmitting(false); }
  }

  const options = Object.entries(option?.kinds ?? {});
  const recommended = option?.recommended_kind;
  const noticeText = option?.eligibility
    ? [option.eligibility.message, option.freight?.message, option.eligibility.next_step].filter(Boolean).join("\n")
    : option?.answer ?? notice;

  return <section id="mall-item-view" className="product-support" data-focused={focused} aria-labelledby="mall-item-title">
    <div className="return-line"><button type="button" className="button button-text" onClick={onBack}>← 返回商品明细</button>
      <button type="button" className="focus-chat button button-muted" aria-pressed={focused} onClick={() => setFocused(!focused)}>{focused ? "恢复标准视图" : "展开咨询区"}</button></div>
    <div className="support-grid">
      <section className="chat-surface" aria-label="商品咨询">
        <header className="chat-header"><span className="chat-avatar" aria-hidden="true">↶</span>
          <div><h3 id="mall-item-title">{product.title}</h3><p id="mall-chat-store">{order.shop_name} · 售后助手</p></div>
          <Status tone={product.stage}>{stages[product.stage]}</Status>
        </header>
        <details id="mall-chat-order" className="chat-order"><summary>正在咨询的订单 · {order.order_id}</summary>
          <dl className="facts-grid">{[["商品",product.title],["规格",product.variant],["购买数量",product.quantity + " 件"],
            ["实付单价",yuan(product.paid_unit)],["包裹",product.package_no]].map(([label,value]) =>
            <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl></details>
        <div className="chat-scroll" ref={scroll}>
          <div className="chat-empty" hidden={Boolean(turns.length)}>
            <span className="voice-mark" aria-hidden="true"><i/><i/><i/><i/><i/></span>
            <strong>这件商品需要什么帮助？</strong>
            <div className="chat-suggestions">{suggestions.map(([label, question]) =>
              <button key={label} type="button" className="button button-muted" data-question={question} onClick={() => {
                questionInput.current.value = question;
                questionInput.current.focus();
              }}>{label}</button>)}</div>
          </div>
          <ol id="mall-dialogue" aria-label="商品咨询记录" aria-live="polite">
            {turns.map((turn, index) => {
              const response = turn.result;
              const escalation = interventionAction(response?.intervention?.state);
              const caseLabel = actions[escalation] ??
                (response?.intervention?.state === "reviewing" ? "查看平台处理进度" : "查看售后单 #" + response?.case_no);
              return <Turn key={turn.key} turn={turn} action={escalation} caseLabel={caseLabel}
                apply={product.remaining_quantity ? goToApplication : null}
                showTrace={portfolioOrders.has(order.order_id)}
                confirmDraft={confirmDraft} activeDraft={activeDraft}
                reply={index === turns.length - 1 && !pending ? sendQuestion : null} continueOn={continueOn} />;
            })}
          </ol>
        </div>
        {continuationError && <p className="error-message" role="alert">{continuationError}</p>}
        <form id="mall-consult" className="chat-composer" onSubmit={event => {
          event.preventDefault(); sendQuestion(questionInput.current.value);
        }}>
          <label className="sr-only" htmlFor="mall-question">售后问题</label>
          <input id="mall-question" ref={questionInput} name="question" placeholder="输入售后问题…" maxLength={500} required />
          <button type="submit" className="button button-primary" disabled={pending}>发送 <span aria-hidden="true">↑</span></button>
        </form>
      </section>
      <aside className="surface support-application">
        <h3>办理售后</h3>
        {chatCase && <a className="button button-soft" href={caseUrl("buyer", chatCase.case_no,
          interventionAction(chatCase.intervention?.state))}>查看原售后单 #{chatCase.case_no}</a>}
        {chatCase?.is_active ? <p className="scope-note">当前已有售后单，请在对话中继续查询或办理原单。</p>
          : preparedApplication ? <div className="scope-note"><strong>申请已在对话中准备好</strong>
            <p>请核对确认卡后提交。想调整件数或办理方式，直接在对话里告诉我就可以。</p>
            <button type="button" className="button button-soft" onClick={()=>scroll.current?.querySelector('[data-draft-id="'+activeDraft+'"]')?.scrollIntoView({block:'center',behavior:'smooth'})}>核对这份申请</button>
          </div> : !product.remaining_quantity ? <p className="quiet">商品已全部登记退款。</p> :
          <form id="mall-application" ref={form} className="action-fields" onSubmit={apply}>
            <label>售后原因<select name="reason" id="mall-reason" required defaultValue=""
              onChange={event => {
                lastSuggestion.current = null;
                checkReason(event.target.value);
              }}>
              <option value="">选择原因</option>{Object.entries(reasons).map(([value, label]) =>
                <option key={value} value={value}>{value === "cancel" && product.stage === "in_transit"
                  ? "取消在途商品" : value === "wrong_purchase" ? "尺寸不合适、买错或不想要" : label}</option>)}</select></label>
            <p className="inline-feedback" id="mall-application-message" role="status">{noticeText}</p>
            {submitError && <p className="error-message" role="alert">{submitError}</p>}
            {option?.case_no && !options.length && !existing &&
              <a href={caseUrl("buyer", option.case_no)}>查看原售后单</a>}
            <fieldset id="mall-application-fields" hidden={!options.length} disabled={!options.length || submitting}>
              <legend className="sr-only">办理方式与申请信息</legend>
              <label>办理方式<select key={options.map(([value]) => value).join(",") + (recommended ?? "")} name="kind" id="mall-kind" required
                defaultValue={recommended ?? (options.length === 1 ? options[0][0] : "")}>
                <option value="">选择办理方式</option>{options.map(([value, label]) =>
                  <option key={value} value={value}>{label}</option>)}</select></label>
              <label>申请件数<input name="quantity" type="number" min="1" max={product.remaining_quantity} value={requestedUnits}
                onChange={event => {quantityEdited.current=true;setRequestedUnits(event.target.value);}} required /></label>
              {option?.eligibility?.transit && <p className="scope-note" role="status">
                原包裹共 {packageUnits} 件。{Number(requestedUnits) === packageUnits
                  ? "本次申请覆盖整包，店铺可核实原运单退回。" : "本次只退部分商品，请勿整包拒收。签收后在本单办理寄回。"}
              </p>}
              <label>申请说明<textarea name="description" maxLength={500} required /></label>
              <label>照片凭证<input id="mall-application-photos" name="photos" type="file" accept="image/jpeg,image/png" multiple /></label>
              {option?.evidence_checklist?.length > 0 && <details className="consult-trace">
                <summary>建议准备的凭证</summary>
                <ul>{option.evidence_checklist.map(evidence => <li key={evidence}>{evidence}</li>)}</ul>
              </details>}
              <small className="quiet">最多 3 张 JPEG 或 PNG。请遮住手机号和地址。</small>
              <button id="mall-apply" className="button button-primary">提交给店铺</button>
            </fieldset>
          </form>}
      </aside>
    </div>
  </section>;
}

/** 只在最近一轮展示追问按钮，旧记录依然可以查阅当时的原单依据。 */
function Turn({turn, action, caseLabel, apply, reply, showTrace, continueOn,confirmDraft,activeDraft}) {
  const response = turn.result;
  const canContinue = reply && response?.pending_turn && response.pending_question;
  return <>
    <li className="chat-user">{turn.question}</li>
    <li className="chat-reply">
      {response ? <>
        <p>{response.answer}</p>
        {response.action_draft?.id && <ChatActionCard draft={response.action_draft} receipt={response.action_execution}
          active={response.action_draft.id===activeDraft} onConfirm={confirmDraft}/>}
        {canContinue && <p className="quiet">{response.order_candidates.length
          ? "另一笔订单的问题暂待确认。请选择对应商品，再继续查询。"
          : "另一笔订单待确认。先发「找订单：商品名」缩小范围。"}</p>}
        {response.continued_to && <a className="button button-soft"
          href={orderUrl(response.continued_to.order_id, response.continued_to.item_no)}>查看另一笔订单的答复</a>}
        {response.order_candidates?.length > 0 && <ul className="order-search-results">
          {response.order_candidates.map(item => <li key={item.order_id + "/" + item.item_no}>
            <strong>{item.title} · {item.variant}</strong>
            <small>{item.shop_name} · {item.purchased} · {item.order_id}
              {item.paid_unit != null && <> · 实付单价 {yuan(item.paid_unit)}</>}</small>
            {canContinue ? <a className="button button-soft" href={orderUrl(item.order_id, item.item_no)}
              data-pending-turn={response.pending_turn} onClick={event => {
                if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                event.preventDefault();
                void continueOn(item, response.pending_turn);
              }}>选这件并继续问</a> : <a className="button button-soft"
                href={orderUrl(item.order_id, item.item_no)}>咨询这件商品</a>}
          </li>)}
        </ul>}
        {response.purchase_history && response.order_candidates.length > 0 &&
          <a className="button button-soft" href="/mall/orders">查看全部订单</a>}
        {response.evidence_requested && response.evidence?.length > 0 &&
          <blockquote className="consult-citation" aria-label="本单条款原文">
            {response.evidence.map(clause => <p key={clause.id}>{clause.source && <strong>{clause.source}　</strong>}{clause.text}</p>)}
          </blockquote>}
        {showTrace && response.reference && response.response_kind !== "out_of_scope" && <details className="consult-trace">
          <summary>查看本轮查询</summary>
          <dl>
            <div><dt>订单商品</dt><dd>{response.reference.order_id} · {response.reference.title}</dd></div>
            <div><dt>查询路径</dt><dd>{consultationRoutes[response.route] ?? "本单查询"}</dd></div>
            {response.answer_source === "model" && <div><dt>回复撰写</dt><dd>{response.response_kind === "clarification"
              ? "DeepSeek 根据对话生成追问" : "DeepSeek 根据查询结果撰写"}</dd></div>}
            {response.generation_status === "fallback" && <div><dt>回复撰写</dt><dd>采用已核对的业务答复</dd></div>}
            {response.retrieval?.routes?.length > 0 && <div><dt>条款检索</dt><dd>{response.retrieval.routes.map(
              route => retrievalRoutes[route] ?? route).join(" → ")}</dd></div>}
            {response.retrieval?.coverage && <div><dt>依据覆盖</dt><dd>{{supported:'本轮问点均找到适用依据',partial:'部分问点尚无对应依据',unsupported:'尚无可核实的对应依据'}[response.retrieval.coverage]}</dd></div>}
            {response.retrieval?.aspects?.filter(row=>!row.supported).map((row,index)=><div key={'uncovered-'+index}><dt>待核实</dt><dd>{row.question}</dd></div>)}
            {response.retrieval?.timings_ms && <div><dt>条款查询耗时</dt><dd>{response.retrieval.timings_ms.total} ms</dd></div>}
            {response.retrieval?.read_loop && <div><dt>补查过程</dt><dd>{response.retrieval.read_loop.steps.map(step=>`${step.step}. ${step.query}`).join(' → ')}（{response.retrieval.read_loop.stop_reason}）</dd></div>}
            {response.business_read_loop && <div><dt>原单补查</dt><dd>{response.business_read_loop.status==='completed'?(response.business_read_loop.added_topics.join('、')||'当前事项已足够'):'补查暂不可用，保留原查询结果'}</dd></div>}
            {response.terms_title && <div><dt>购买条款</dt><dd>{response.terms_title} · 第 {response.terms_version} 版</dd></div>}
            {response.order_candidates ? <div><dt>查询工具</dt><dd>{response.topics?.includes("clarify_order")
              ? "等待买家选择订单" : "查找本人订单商品"}</dd></div> : <div><dt>原文来源</dt><dd>{response.evidence_requested && response.evidence?.length
              ? response.evidence.map(clause => clause.source ?? clause.id).join("、") : "可追问「依据呢」核对原文"}</dd></div>}
          </dl>
        </details>}
        {response.select_order && <a className="button button-soft" href="/mall/orders">从我的订单选择商品</a>}
        {!response.action_execution && (response.case_no ? <a className="button button-soft" href={caseUrl("buyer", response.case_no, action)}>{caseLabel}</a>
          : apply && response.action_draft?.state!=='ready' && (response.intervention?.state === "no_case" || Object.keys(response.kinds ?? {}).length > 0)
            && <button type="button" className="button button-soft" onClick={() => apply(response)}>
              {response.recommended_kind === "exchange" ? "填写换货申请" : "填写商品售后申请"}</button>)}
        {reply && response.quick_replies?.length > 0 && <div className="consult-choices" aria-label="回复售后助手">
          {response.quick_replies.map(label => <button type="button" key={label} onClick={() => reply(label)}>{label}</button>)}</div>}
      </> : turn.error ? <p className="error-message">{turn.error}</p>
        : <span className="chat-pending"><span className="spinner" aria-hidden="true" />正在核对…</span>}
    </li>
  </>;
}
