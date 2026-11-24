/** 聊天确认卡：核对原商品与方案后，使用服务器保存的同一个操作编号。 */
import {useRef,useState} from 'react';
import {caseUrl,encodePhotos,localTime,yuan,offerNames} from './core.js';

export function ChatActionCard({draft,receipt,active,onConfirm}) {
  const [note,setNote]=useState(draft.note??'');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [stale,setStale]=useState(false);
  const photos=useRef();
  const inFlight=useRef(false);
  const offer=draft.offer;
  async function confirm() {
    if (inFlight.current || !active || stale) return;
    inFlight.current=true;
    setBusy(true); setError('');
    try {
      const images=draft.photos_allowed?await encodePhotos(photos.current):[];
      await onConfirm(draft,{turn_no:String(draft.turn_no),note:note.trim(),photos:images});
    } catch (failure) {
      setError(failure.message);
      if (failure.status===409) setStale(true);
    } finally {inFlight.current=false;setBusy(false);}
  }
  const card=<section className={'chat-action-card'+(receipt?' is-complete':'')} aria-label="售后办理确认卡" data-draft-id={draft.id}>
    <div className="chat-action-heading"><span aria-hidden="true">{receipt?'✓':'↗'}</span><strong>{receipt?'办理结果已保存':draft.label}</strong></div>
    <p className="chat-action-product">{draft.title}<small>{draft.variant}</small></p>
    <dl className="chat-action-details">
      <div><dt>订单</dt><dd>{draft.order_id}</dd></div>
      {draft.case_no && <div><dt>原售后单</dt><dd>#{draft.case_no}</dd></div>}
      {draft.kind_label && <div><dt>办理方式</dt><dd>{draft.kind_label}</dd></div>}
      {draft.reason_label && <div><dt>申请原因</dt><dd>{draft.reason_label}</dd></div>}
      <div><dt>涉及件数</dt><dd>{draft.quantity} 件</dd></div>
      {draft.max_product_refund!=null && <div><dt>商品退款上限</dt><dd>{yuan(draft.max_product_refund)}</dd></div>}
    </dl>
    {draft.action==='ship'&&<p>寄回物流：{draft.payload?.carrier} · {draft.payload?.tracking}</p>}
    {draft.action==='confirm_delivery_address'&&<div className="chat-action-offer"><strong>本次收货信息</strong><p>{draft.payload?.delivery_address?.recipient} · {draft.payload?.delivery_address?.phone}</p><p>{draft.payload?.delivery_address?.address}</p></div>}
    {offer && <div className="chat-action-offer"><strong>店铺本次方案</strong><p>{offerNames[offer.kind]} · {offer.quantity} 件{offer.amount!=null&&<> · 商品退款 {yuan(offer.amount)}</>}</p>{offer.return_instructions&&<p>{offer.return_instructions}</p>}</div>}
    {receipt ? <div className="chat-action-receipt" role="status">
      <strong>{receipt.status_label} · 售后单 #{receipt.case_no}</strong>
      <small>{localTime(receipt.confirmed_at)}</small>
      <a className="button button-soft" href={caseUrl('buyer',receipt.case_no)}>查看这张售后单</a>
    </div> : active && !stale ? <>
      <label>给店铺或平台的说明<textarea value={note} maxLength={500} onChange={event=>setNote(event.target.value)} disabled={busy}/></label>
      {draft.photos_allowed&&<label>附上照片（可选）<input ref={photos} type="file" accept="image/jpeg,image/png" multiple disabled={busy}/></label>}
      <p className="quiet">核对后再确认。{draft.action==='submit'?'提交后由店铺审核处理。':draft.action==='withdraw'?'撤回后将结束当前售后办理。':'操作会记录在原售后单中。'}</p>
      {error&&<p className="error-message" role="alert">{error}</p>}
      <button type="button" className="button button-primary chat-action-confirm" disabled={busy||!note.trim()} onClick={confirm}>{busy?'正在办理…':'确认'+draft.label}</button>
    </> : <p className="quiet" role="status">{error||'办理内容已更新，请核对最新确认卡。'}</p>}
  </section>;
  return active||receipt?card:<details className="chat-action-history"><summary>此前准备：{draft.label} · 内容已更新</summary>{card}</details>;
}
