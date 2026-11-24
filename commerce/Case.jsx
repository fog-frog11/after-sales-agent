/** 原售后单的状态、时间线和按服务端授权生成的操作。 */
import {useEffect, useRef, useState} from "react";
import {actionLabel, actions, deliveryProblems, deliveryReviewDecisions, dispatchActions,
  encodePhotos, eventRoles, formatAddress, interventionAction, localTime, mailingActions,
  offerNames, orderUrl, outboundActions, photoActions, refundStages, replyActions, request,
  returnOffers, stages, submissionKey, yuan} from "./core.js";
import {Status} from "./Widgets.jsx";

const reviewLabels = {return_details: "复核并填写寄回信息", receipt: "复核确认已收件"};
const returnActions = ["carrier_return", "carrier_interception_failed", "buyer_received", "provide_return_details", "ship", "receive"];
const departureStages = {ready: "待店铺发货", shipped: "待买家确认收货", received: "已确认收到", superseded: "已安排重寄"};
const prompts = {
  carrier_return:"填写承运方已确认的拦截或拒收结果，例如联系时间及物流反馈。",
  carrier_interception_failed:"填写快递反馈的拦截失败原因，例如包裹已交给派送员并将继续配送。",
  buyer_received:"请说明实际签收情况。提交后由店铺提供寄回地址。",
  approve: "可说明寄回后的处理安排",
  reject: "请说明拒绝原因和判断依据",
  evidence: "描述提交的材料或已核实的事实",
  request_evidence: "说明需要补拍的照片或要核对的信息",
  provide_evidence: "说明本次补交的材料",
  negotiate: "说明提出这项方案的原因",
  dispute: "说明争议事项和期望的处理结果",
  review: "说明店铺哪一步超时，可附上凭证",
  report_delivery_problem: "描述未收到、发错或破损的经过",
  request_delivery_review: "说明希望平台如何处理包裹问题",
  support_buyer: "填写判断依据和后续办理要求",
  refund: "填写发起退款的说明",
  record_refund_result: "填写渠道结果或失败原因",
  reimburse: "确认已返还运费后，填写返还渠道或相关说明。",
};

function summarizeOffer(offer) {
  return [offerNames[offer.kind], offer.quantity + " 件",
    offer.amount != null && "商品退款 " + yuan(offer.amount)].filter(Boolean).join(" · ");
}

/** 用户可在同一张原单上核对状态、购买记录和历史操作。 */
export function CaseDetails({data, role, api, initialAction, onUpdated, reload}) {
  const [action, setAction] = useState(initialAction && data.actions.includes(initialAction) ? initialAction : "");
  const [message, setMessage] = useState(null);
  const [section,setSection] = useState("overview");
  const form = useRef();
  const allowed = data.actions;
  const showOperations = data.is_active && allowed.length > 0;
  const canRecordReimbursement = !data.is_active && allowed.includes("reimburse");
  const escalation = role === "buyer" ? interventionAction(data.intervention?.state) : undefined;
  const offer = data.agreement ?? data.prompt?.offer;
  const returned = data.events.find(event => event.action === "ship")?.details;
  const operationVersion = useRef(data);
  const latest = data.events.at(-1);
  const refund = data.refund_summary?.product;
  const showFunds = refund && (refund.amount != null || data.refund_summary.return_shipping.state !== "unrecorded");
  const orderLink = role === "buyer" ? orderUrl(data.order_id) :
    (role === "merchant" ? "/merchant/mall/orders" : "/staff/mall/catalog?view=orders") +
    (role === "merchant" ? "?" : "&") + new URLSearchParams({order:data.order_id});

  useEffect(() => {
    if (operationVersion.current === data) return;
    operationVersion.current = data;
    setAction(initialAction && allowed.includes(initialAction) ? initialAction : "");
  }, [data, initialAction]);
  useEffect(() => {
    if (!message?.center) return;
    const timer = window.setTimeout(() => setMessage(null), 2400);
    return () => window.clearTimeout(timer);
  }, [message]);

  function open(next) {
    if (!allowed.includes(next)) return;
    setMessage(null);
    setAction(next);
    requestAnimationFrame(() => {
      form.current?.scrollIntoView({block: "nearest"});
      form.current?.elements.note.focus({preventScroll:true});
    });
  }

  function reveal(section, target) {
    setSection(section);
    requestAnimationFrame(() => {
      const panel = document.getElementById(target);
      panel?.focus({preventScroll:true});
      panel?.scrollIntoView({block:"start",
        behavior:matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth"});
    });
  }

  const feedback = <p id="mall-operation-message" className={"feedback" + (message?.error ? " error-message" : "")}
    role="status">{message?.center ? null : message?.text}</p>;
  const editor = <OperationForm key={data.events.length + ":" + data.status} data={data} api={api}
    action={allowed.includes(action) ? action : ""} onAction={setAction} form={form}
    onUpdated={onUpdated} reload={reload} setMessage={setMessage} fixedAction={!showOperations} />;

  return <div id="mall-case-view" className="case-detail">
    <header className="case-summary">
      <div><p>{data.shop_name} <a className="case-order-link" href={orderLink}>订单 {data.order_id} <span aria-hidden="true">↗</span></a></p>
        <h2 id="mall-selection-title">售后单 #{data.case_no}</h2>
        <p id="mall-case-summary">{[data.product, data.variant, data.quantity + " 件", data.kind_label, data.reason_label].filter(Boolean).join(" · ")}</p></div>
      <p id="mall-case-next" className="case-next"><Status tone={data.overdue ? "overdue" : "info"}>{data.status_label}</Status>
        {data.owner && <span>{!data.is_active && data.return_reimbursement_due != null
          ? "待店铺登记寄回运费返还" : "当前由" + data.owner + "处理"}</span>}
        {data.due_at && <small>{data.overdue ? "已超时（截止 " : "截止 "}{localTime(data.due_at)}{data.overdue ? "）" : ""}</small>}</p>
    </header>
    <div className="case-shortcuts" aria-label="售后单快捷入口">
      <button type="button" id="case-shortcut-progress" onClick={() => reveal("overview","case-overview")}>
        <span>办理进展</span><strong>{data.status_label}</strong><i aria-hidden="true">↘</i>
      </button>
      {showFunds && <button type="button" id="case-shortcut-funds" onClick={() => reveal("overview","mall-refund-progress")}>
        <span>退款与运费</span><strong>{data.return_reimbursement_due != null && !data.is_active
          ? "待返还 " + yuan(data.return_reimbursement_due) : refund.amount == null ? "查看运费" : yuan(refund.amount)}</strong>
        <i aria-hidden="true">↘</i>
      </button>}
      {latest && <button type="button" id="case-shortcut-history" onClick={() => reveal("history","case-history")}>
        <span>最近进展 · {localTime(latest.created_at)}</span>
        <strong>{eventRoles[latest.actor_role]} · {actionLabel(latest.action,latest.details.delivery_address_changed)}</strong>
        <i aria-hidden="true">↘</i>
      </button>}
    </div>
    <div className="case-columns" data-layout={showOperations ? "with-actions" : "read-only"}>
      <div className="case-reading">
        <nav className="range-tabs case-jumps" aria-label="售后详情视图">
          <button aria-pressed={section === "overview"} onClick={() => setSection("overview")}>本单进展</button>
          <button id="show-case-history" aria-controls="case-history" aria-pressed={section === "history"} onClick={() => setSection("history")}>处理记录 · {data.events.length}</button>
        </nav>
        <section id="case-overview" className="surface case-facts" tabIndex={-1} hidden={section !== "overview"}>
          <div className="section-head"><h2>本单进展</h2></div>
          <p id="mall-case-facts" className="quiet">{[stages[data.package_stage], data.outbound_tracking && "原订单运单 " + data.outbound_tracking,
            returned?.tracking && "买家寄回运单 " + returned.carrier + " " + returned.tracking,
            "商品退款余额 " + yuan(data.refund_balance)].filter(Boolean).join(" · ")}</p>
          {data.package_lines && !data.refund_summary?.product.lines.length && <details id="mall-package-lines" className="terms" open>
            <summary>本次退回商品</summary>
            <ul>{data.package_lines.map(line => <li key={line.item_no}>
              {line.title} · {line.variant} · {line.quantity} 件 · 商品退款 {yuan(line.amount)}
            </li>)}</ul>
          </details>}
          <section className="case-callout" hidden={role !== "buyer" || data.outbound_pending || !data.intervention?.message}>
            <p>{data.intervention?.message}</p>
            <button className="button button-primary" type="button" id="mall-intervention-start"
              hidden={!escalation || data.outbound_pending} onClick={() => open(escalation)}>{actions[escalation]}</button>
          </section>
          <ReturnProgress data={data} open={open} />
          <Outbound data={data} open={open} />
          <Refund data={data} open={open}>
            {canRecordReimbursement && <div className="case-reimbursement">
              {action === "reimburse" ? editor :
                <button type="button" id="mall-reimbursement-start" className="button button-soft"
                  onClick={() => open("reimburse")}>登记寄回运费返还 {yuan(data.return_reimbursement_due)}</button>}
              {feedback}
            </div>}
          </Refund>
          {data.prompt?.note && <p className="case-callout">店铺说明：{data.prompt.note}</p>}
          {offer && <section className="case-callout">
            <strong>{data.agreement ? "买家已确认方案" : "待买家确认的方案"}</strong><p>{summarizeOffer(offer)}</p>
            {offer.return_instructions && <p>寄回说明：{offer.return_instructions}</p>}
            {data.agreement && <small>确认时间 {localTime(data.agreement.accepted_at)}</small>}
          </section>}
          {!data.refund_summary && !!data.money.length && <dl id="mall-case-money" className="facts-grid">
            {data.money.map(entry => <div key={entry.label}><dt>{entry.label}</dt><dd>{yuan(entry.amount)} · {entry.state}</dd></div>)}</dl>}
        </section>
        <section id="case-history" className="surface case-history" tabIndex={-1} hidden={section !== "history"}>
          <h2>处理记录</h2><Timeline data={data} api={api} />
          <details className="terms"><summary>{data.terms_title} · 第 {data.terms_version} 版</summary>
            <ul>{data.terms.map(term => <li key={term}>{term}</li>)}</ul></details>
        </section>
        {!showOperations && !canRecordReimbursement && feedback}
      </div>
      {showOperations && <aside id="case-operations" className="surface case-actions">
        <h2>处理售后</h2>
        {editor}
        {feedback}
      </aside>}
    </div>
    {message?.center && <p id="mall-operation-confirmed" className="operation-confirmed" role="status">
      <span aria-hidden="true">✓</span>{message.text}
    </p>}
  </div>;
}

function ReturnProgress({data, open}) {
  const relevant = returnActions.filter(value => data.actions.includes(value));
  const messages = [];
  if (data.transit_request && data.status === "awaiting_outcome") {
    if (data.transit_request === "partial") {
      messages.push("仅处理本单申请的商品。请先签收整包，再从本单确认签收并办理部分寄回。");
    } else if (data.interception_failure) {
      messages.push("包裹继续配送。快递确认受理拒收后，店铺可继续登记原包裹退回。已经签收的，可在原单改为自行寄回。");
    } else if (data.package_stage === "delivered") {
      messages.push("物流已有签收记录。买家实际收到包裹后，可在原单确认签收并办理寄回。");
    } else {
      messages.push("店铺核实原运单的拦截或拒收结果后，在这里登记。请与快递确认拒收；仅将包裹留在站点并不代表已经退回。");
    }
  }
  if (data.status === "awaiting_return" && data.return_details_pending) {
    messages.push("当前需要店铺填写寄回收件信息。地址提供后，买家可登记运单。");
  }
  if (data.return_details && (["awaiting_return", "return_sent"].includes(data.status) || data.active_review_phase === "receipt")) {
    messages.push("寄回地址：" + formatAddress(data.return_details));
  }
  if (data.status === "return_sent") messages.push(data.carrier_returned
    ? "承运方已受理退回。原包裹到店后由店铺确认收件，买家无需另填寄回运单。" : "买家已登记寄回，当前等待店铺确认收件。");
  if (data.active_review_phase) {
    messages.push(data.active_review_phase === "receipt" ? "本轮复核事项：店铺逾期未确认收件。" :
      "本轮复核事项：店铺逾期未补充寄回信息。");
  }
  if (!messages.length && relevant.includes("ship")) messages.push("请按原单的寄回说明寄件，并在寄出后登记运单。");
  return <section className="case-callout" id="mall-return-progress" hidden={!messages.length}>
    {data.interception_failure && <p id="mall-interception-result">
      <strong>店铺登记拦截失败</strong> · {localTime(data.interception_failure.at)}<br/>
      {data.interception_failure.note}
    </p>}
    <p id="mall-return-progress-message">{messages.join("\n")}</p>
    <div className="button-row">{relevant.map((action,index) =>
      <button type="button" key={action} id={index === 0 ? "mall-return-progress-action" : undefined} className="button button-soft"
        onClick={() => open(action)}>{actions[action]}</button>)}</div>
  </section>;
}

function Refund({data, open, children}) {
  const attempt = data.refund_attempt;
  const summary = data.refund_summary;
  const product = summary?.product;
  const action = ["refund", "record_refund_result"].find(value => data.actions.includes(value));
  const started = product?.started_at ?? attempt?.started_at;
  const finished = product?.result_at ?? attempt?.result_at;
  const failure = product?.failure_reason ?? (attempt?.state === "failed" ? attempt.result_note : null);
  return <section className="case-callout refund-summary" id="mall-refund-progress" tabIndex={-1}
    hidden={!attempt && !action && !product?.lines.length && summary?.return_shipping.state === "unrecorded"}>
    <header className="section-head">
      <h3>退款明细</h3>
      <Status tone={product?.state === "failed" ? "overdue" : "info"}>
        {product?.label ?? refundStages[attempt?.state] ?? "商品退款待发起"}
      </Status>
    </header>
    <p id="mall-refund-progress-message">{product?.message}{failure && " 失败说明 · " + failure}</p>
    {(finished || started) && <p className="quiet">{finished ? "结果时间 " + localTime(finished) : "发起时间 " + localTime(started)}</p>}
    {!!product?.lines.length && <ul id={data.package_lines ? "mall-package-lines" : undefined} className="refund-items">
      {product.lines.map(line => <li key={line.item_no}>
        <div><strong>{line.title}</strong><small>{line.variant}</small></div>
        <dl>
          <div><dt>本次涉及件数</dt><dd>{line.quantity} 件</dd></div>
          <div><dt>实付单价</dt><dd>{yuan(line.paid_unit)}</dd></div>
          <div><dt>本次商品退款</dt><dd>{yuan(line.amount)}</dd></div>
        </dl>
      </li>)}
    </ul>}
    {summary && <dl className="refund-fees">
      <div><dt>原配送费</dt><dd id="mall-original-shipping">{summary.original_shipping.message}</dd></div>
      <div><dt>寄回运费</dt><dd id="mall-return-shipping">{summary.return_shipping.message}</dd></div>
    </dl>}
    {action && <button type="button" id="mall-refund-progress-action" className="button button-soft"
      onClick={() => open(action)}>{actionLabel(action, false, attempt?.state === "failed")}</button>}
    {children}
  </section>;
}

function Outbound({data, open}) {
  const parcel = data.outbound;
  const history = data.outbound_history ?? [];
  const review = data.delivery_review;
  const reviewing = review?.action === "request_delivery_review";
  const awaitingReturn = ["exchange", "repair"].includes(data.agreement?.kind ?? data.kind) &&
    ["awaiting_return", "return_sent"].includes(data.status);
  let summary = "待买家确认本次收货信息。确认后店铺可以寄出新包裹。";
  if (awaitingReturn) {
    if (data.status === "return_sent") summary = "等待店铺确认已收到寄回的商品。店铺确认后，买家可在这里确认收货信息。";
    else if (data.return_details_pending) summary = "等待店铺提供寄回地址。买家寄回商品并经店铺确认收件后，可在这里确认收货信息。";
    else summary = "请先登记寄回。店铺确认收件后，买家可在这里确认收货信息。";
  }
  const relevant = data.actions.filter(action => outboundActions.includes(action) || (reviewing && action === "evidence"));
  return <section className="case-callout case-outbound" id="mall-outbound"
    hidden={!data.outbound_pending && !history.length && !awaitingReturn}>
    <h3>店铺寄出的售后包裹</h3>
    <p id="mall-outbound-summary">{parcel ? [formatAddress(parcel.destination), deliveryProblems[parcel.problem]].filter(Boolean).join(" · ") : summary}</p>
    {data.outbound_pending && data.intervention?.message && <p id="mall-outbound-guidance">{data.intervention.message}</p>}
    {!!history.length && <ul id="mall-outbound-history">{history.map(row => <li key={row.parcel_no}>
      {["包裹 #" + row.parcel_no, departureStages[row.stage], row.tracking && row.carrier + " " + row.tracking,
        row.received_at && "确认收货 " + localTime(row.received_at), deliveryProblems[row.problem]].filter(Boolean).join(" · ")}</li>)}</ul>}
    {data.delivery_reply && <p id="mall-delivery-reply">店铺核查回复（{localTime(data.delivery_reply.at)}）{data.delivery_reply.note}</p>}
    {review && <section id="mall-delivery-review" className="review-note">
      <h4>{reviewing ? "平台正在处理这次包裹" : "平台结论 · " + actions[review.action]}</h4>
      <p id="mall-delivery-review-meta">包裹 #{review.parcel_no} · {deliveryProblems[review.problem]} · {localTime(review.at)}</p>
      <p id="mall-delivery-review-note">{review.note}</p>
    </section>}
    <div className="button-row" id="mall-outbound-actions">{relevant.map(action =>
      <button type="button" key={action} className="button button-soft" onClick={() => open(action)}>
        {actionLabel(action, Boolean(parcel))}</button>)}</div>
  </section>;
}

function Timeline({data, api}) {
  return <ol id="mall-timeline" className="timeline">{data.events.map((event, index) => {
    const facts = event.details;
    const startingRefund = event.action === "refund" && facts.refund_quantity !== undefined;
    const review = {return_details: "寄回信息", receipt: "确认收件"}[facts.review_phase];
    const decision = review && ["support_buyer", "support_seller"].includes(event.action) ? "复核结论 · " : "";
    const refund = event.action === "refund" && !startingRefund ? "登记退款" : refundStages[facts.refund_result];
    const text = [event.note, facts.tracking && facts.carrier + " " + facts.tracking,
      facts.return_details && "寄回地址：" + formatAddress(facts.return_details),
      deliveryProblems[facts.delivery_problem], facts.review_problem && "包裹 #" + facts.delivery_parcel_no + " · " + deliveryProblems[facts.review_problem],
      facts.offer && summarizeOffer(facts.offer), facts.offer?.return_instructions && "寄回说明：" + facts.offer.return_instructions,
      facts.return_postage_due && "寄回预计自付 " + yuan(facts.return_postage_due) + " · 已抵扣 " + yuan(facts.credit_used),
      facts.refund_amount && (startingRefund ? "本次发起金额 " : "退款登记 ") + yuan(facts.refund_amount)].filter(Boolean);
    return <li key={index} data-actor={event.actor_role}>
      <div><strong>{eventRoles[event.actor_role]} · {decision}{refund ?? actionLabel(event.action, facts.delivery_address_changed)}
        {review && "（" + review + "）"}</strong><time dateTime={event.created_at}>{localTime(event.created_at)}</time></div>
      {text.map((line, index) => <p key={index}>{line}</p>)}
      {!!event.photos.length && <div className="case-photos">{event.photos.map((photo, index) => {
        const url = api + "/cases/" + data.case_no + "/photos/" + photo;
        return <a key={photo} href={url} target="_blank" rel="noopener"><img src={url} alt={"打开凭证照片 " + (index + 1)} loading="lazy" /></a>;
      })}</div>}
    </li>;
  })}</ol>;
}

/** 仅收集当前操作需要的字段，重复提交沿用同一请求编号。 */
function OperationForm({data, api, action, onAction, form, onUpdated, reload, setMessage, fixedAction}) {
  const draft = useRef(null);
  const pending = useRef(false);
  const mounted = useRef(true);
  const [busy, setBusy] = useState(false);
  const [fieldErrors, setFieldErrors] = useState({});
  const [edited, setEdited] = useState(false);
  const [kind, setKind] = useState("");
  const [quantity, setQuantity] = useState(data.offer_max_quantity);
  const notes = useRef({});
  const address = data.outbound?.destination ?? data.default_delivery_address;
  const confirming = action === "confirm_delivery_address";
  const unchanged = confirming && Boolean(data.outbound) && !edited;
  const writingReturn = action === "provide_return_details" ||
    action === "support_buyer" && data.active_review_phase === "return_details";
  const shipping = mailingActions.includes(action);
  const showPhoto = photoActions.includes(action);
  const reason = data.outbound?.stage === "ready" ? ["not_dispatched"] : ["not_received", "wrong_item", "damaged"];
  const retry = data.refund_attempt?.state === "failed";
  const amountLimit = Math.round(Math.min(Number(data.refund_balance), Number(data.offer_unit_price) * quantity) * 100) / 100;
  const defaultNote = {accept_offer: "同意店铺提出的处理方案。", provide_return_details: "已补充寄回信息。",
    confirm_delivery_address: data.outbound ? "已修改本次收货信息。" : "已核对本次收货信息。",
    confirm_delivery: "已收到本次售后包裹。"}[action];
  const submitLabel = {dispute: "提交平台介入申请", review: "提交平台复核申请", request_delivery_review: "提交包裹处理申请",
    provide_return_details: "保存寄回信息",
    reimburse: "确认登记运费返还",
    confirm_delivery_address: data.outbound ? "保存修改" : "确认收货信息", confirm_delivery: "确认收货",
    refund: actionLabel("refund", false, retry), record_refund_result: "保存退款结果"}[action] ?? "确认操作";

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  useEffect(() => {
    setEdited(false);
    setFieldErrors({});
    setKind("");
    setQuantity(data.offer_max_quantity);
  }, [action, data.offer_max_quantity]);

  function enteredDestination() {
    return Object.fromEntries(["recipient", "address", "phone"].map(key =>
      [key, form.current.elements["delivery-" + key].value.trim()]));
  }

  function contactField(input) {
    if (input.closest("#mall-return-details")) return "return_details." + input.name;
    if (input.closest("#mall-delivery-address")) return "delivery_address." + input.name.slice("delivery-".length);
    return null;
  }

  function showInvalid(event) {
    const field = contactField(event.target);
    if (field) {
      event.preventDefault();
      const key = field.split(".")[1];
      const tips = {recipient: "请填写收件人。", address: "请填写至少 5 个字符的详细地址。",
        phone: "联系电话至少需要 5 个字符，请填写手机号或固定电话。"};
      setFieldErrors(errors => ({...errors, [field]: tips[key]}));
      event.currentTarget.querySelector(":invalid")?.focus();
      setMessage({text: "请修正标出的收件信息后再提交。", error: true});
    } else {
      setMessage({text: event.target.validationMessage || "请填写必填项后再提交。", error: true});
    }
  }

  function clearFieldError(event) {
    const field = contactField(event.target);
    if (!field || !fieldErrors[field]) return;
    setFieldErrors(errors => ({...errors, [field]: undefined}));
    setMessage(null);
  }

  async function submit(event) {
    event.preventDefault();
    if (!action || pending.current || unchanged) return;
    const fields = new FormData(event.currentTarget);
    const payload = {action, note: fields.get("note")};
    if (shipping) Object.assign(payload, {carrier: fields.get("carrier"), tracking: fields.get("tracking")});
    if (writingReturn) payload.return_details = Object.fromEntries(["recipient", "address", "phone"].map(key => [key, fields.get(key)]));
    if (outboundActions.includes(action) && data.outbound) payload.delivery_parcel_no = data.outbound.parcel_no;
    if (deliveryReviewDecisions.includes(action)) payload.delivery_review_id = data.delivery_review.request_id;
    if (dispatchActions.includes(action) || confirming && data.outbound) {
      payload.delivery_address_version = data.outbound.address_version;
    }
    if (confirming) payload.delivery_address = enteredDestination();
    if (action === "report_delivery_problem") payload.delivery_problem = fields.get("delivery-problem");
    if (action === "record_refund_result") {
      payload.refund_attempt_id = data.refund_attempt.id;
      payload.refund_result = fields.get("refund-result");
    }
    if (action === "negotiate") {
      payload.offer = {kind: fields.get("offer-kind"), quantity: Number(fields.get("offer-quantity"))};
      if (kind === "partial_refund") payload.offer.amount = fields.get("offer-amount");
      if (returnOffers.includes(kind)) payload.offer.return_instructions = fields.get("offer-return");
    }
    if (replyActions.includes(action)) payload.prompt_id = data.prompt?.id;
    pending.current = true;
    setBusy(true);
    setFieldErrors({});
    setMessage({text: "正在提交…"});
    try {
      if (showPhoto) payload.photos = await encodePhotos(form.current.elements.photos);
      if (!mounted.current) return;
      payload.operation_id = submissionKey(draft, payload);
      const saved = await request(api + "/cases/" + data.case_no + "/actions", payload);
      if (mounted.current) {
        const text = writingReturn ? "寄回信息已保存，买家现在可以登记寄回运单。" :
          action === "reimburse" ? "寄回运费返还已登记。" : "处理记录已保存。";
        setMessage({text, center: action === "reimburse"});
        onUpdated(saved);
      }
    } catch (error) {
      if (mounted.current) {
        setFieldErrors(error.fields ?? {});
        setMessage({text: error.message, error: true});
        const first = Object.keys(error.fields ?? {})[0];
        if (first) {
          const [section, name] = first.split(".");
          form.current.elements[section === "delivery_address" ? "delivery-" + name : name]?.focus();
        }
        if (error.status === 409 && ["dispute", "review", "refund", "record_refund_result", ...outboundActions].includes(action)) reload();
      }
    } finally {
      pending.current = false;
      if (mounted.current) setBusy(false);
    }
  }

  return <form id="mall-operation" ref={form} className="action-fields" onSubmit={submit}
    onInvalid={showInvalid} onInput={clearFieldError}>
    {!fixedAction && <label>操作<select id="mall-action" value={action} onChange={event => {
      setMessage(null);
      onAction(event.target.value);
    }} required>
      <option value="">选择操作</option>{data.actions.map(value => <option key={value} value={value}>
        {value === "support_buyer" && reviewLabels[data.active_review_phase] || actionLabel(value, Boolean(data.outbound), retry)}</option>)}
    </select></label>}
    <label>给各方的说明<textarea key={action} name="note" maxLength={500} defaultValue={notes.current[action] ?? defaultNote}
      onInput={event => { notes.current[action] = event.target.value; }}
      placeholder={prompts[action] ?? "填写各方可以看到的说明"} required /></label>
    {action === "negotiate" && <fieldset>
      <legend>店铺处理方案</legend>
      <label>办理方式<select id="mall-offer-kind" name="offer-kind" defaultValue="" required
        onChange={event => setKind(event.target.value)}>
        <option value="">选择方案</option>{Object.entries(data.offer_options).map(([value, name]) =>
          <option key={value} value={value}>{name}</option>)}</select></label>
      <label>方案件数<input id="mall-offer-quantity" name="offer-quantity" type="number" min="1" max={data.offer_max_quantity}
        defaultValue={data.offer_max_quantity} onInput={event => setQuantity(Number(event.target.value))} required /></label>
      {kind === "partial_refund" && <label>退款金额（元）<input id="mall-offer-amount" name="offer-amount" type="number"
        min="0.01" max={(amountLimit - 0.01).toFixed(2)} step="0.01" required /></label>}
      {returnOffers.includes(kind) && <label>寄回说明<textarea name="offer-return" maxLength={500} required /></label>}
      {kind === "partial_refund" && <small className="quiet">买家留用商品，退款需低于 {yuan(amountLimit)}</small>}
      {["refund_only", "return_refund"].includes(kind) && <small className="quiet">预计商品退款 {yuan(amountLimit)}</small>}
    </fieldset>}
    {shipping && <fieldset id="mall-shipping"><legend>寄件信息</legend>
      <label>快递公司<input name="carrier" maxLength={30} required /></label>
      <label>运单号<input name="tracking" minLength={5} maxLength={40} required /></label>
    </fieldset>}
    {writingReturn && <fieldset id="mall-return-details"><legend>寄回收件信息</legend>
      <ContactInputs section="return_details" fieldErrors={fieldErrors}/>
    </fieldset>}
    {confirming && <fieldset id="mall-delivery-address" onInput={() => {
      setEdited(Boolean(address) && Object.entries(enteredDestination()).some(([key, value]) => address[key] !== value));
    }}><legend>本次收货信息</legend>
      <ContactInputs section="delivery_address" fieldErrors={fieldErrors} values={address}/>
      {unchanged && <small className="quiet">收货信息已确认，修改后可以保存。</small>}
    </fieldset>}
    {action === "report_delivery_problem" && <label>包裹问题<select id="mall-delivery-problem" name="delivery-problem" required>
      {reason.map(value => <option key={value} value={value}>{deliveryProblems[value]}</option>)}</select></label>}
    {action === "record_refund_result" && <label>退款处理结果<select id="mall-refund-result" name="refund-result" defaultValue="" required>
      <option value="">选择结果</option><option value="succeeded">退款成功</option><option value="failed">退款失败</option>
    </select></label>}
    {showPhoto && <label>照片凭证<input id="mall-operation-photos" name="photos" type="file" accept="image/jpeg,image/png" multiple /></label>}
    <button type="submit" className="button button-primary" disabled={busy || unchanged}>{busy ? "正在提交…" : submitLabel}</button>
  </form>;
}

/** 保留两类收件人的输入和对应字段错误。 */
function ContactInputs({section, fieldErrors, values}) {
  const delivery = section === "delivery_address";
  const prefix = delivery ? "delivery-" : "";
  const fields = [
    {name: "recipient", label: "收件人", max: 80},
    {name: "address", label: delivery ? "收货地址" : "寄回地址", min: 5, max: 240},
    {name: "phone", label: "联系电话", min: 5, max: 30, type: "tel"},
  ];
  return fields.map(field => {
    const id = "mall-" + (delivery ? "delivery-" : "return-") + field.name + (delivery && field.name === "address" ? "-value" : "");
    const error = fieldErrors[section + "." + field.name];
    return <label key={field.name}>{field.label}
      <input id={id} name={prefix + field.name} type={field.type} defaultValue={values?.[field.name]} minLength={field.min}
        maxLength={field.max} aria-invalid={Boolean(error)} aria-describedby={error ? id + "-error" : undefined} required/>
      {error && <small className="error-message" id={id + "-error"}>{error}</small>}
    </label>;
  });
}
