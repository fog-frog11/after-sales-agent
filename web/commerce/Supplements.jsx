/** 商家起草和修订补充条款、平台比较审核。 */
import {useEffect,useRef,useState} from "react";
import {request} from "./core.js";
import {PolicyPreview} from "./PolicyPreview.jsx";

const topics = {missing:"缺件补发", evidence:"举证与凭证", freight:"寄回运费", return:"退换和寄回",
  refund:"退款", delivery:"售后包裹", service:"维修与其他服务"};
const statuses = {approved:"已通过", rejected:"已退回"};

export function Supplements({shop,onSubmitted}) {
  const [rows,setRows]=useState([]), [message,setMessage]=useState(""), [busy,setBusy]=useState(false);
  const [draft,setDraft]=useState({title:"",topic:"missing",sku:"",note:""});
  const [revises,setRevises]=useState(null);
  const editor=useRef(null);
  const api="/api/staff/mall/supplements";
  useEffect(()=>{if(!shop) request(api).then(setRows).catch(e=>setMessage(e.message));},[shop]);
  async function submit(event,row) {
    event.preventDefault();
    if(busy) return;
    const form=event.currentTarget;
    const values=shop ? {...draft,sku:draft.sku || null,revises_id:revises} : Object.fromEntries(new FormData(form));
    setBusy(true);
    try {
      await request(shop ? "/api/merchant/mall/supplements/"+encodeURIComponent(shop.shop_id) : api+"/"+row.id,values);
      form.reset();
      setDraft({title:"",topic:"missing",sku:"",note:""}); setRevises(null);
      setMessage(shop ? "已提交平台审核。" : "审核结论已保存。");
      if(!shop) setRows(await request(api));
      else if(onSubmitted) await onSubmitted();
    } catch(e){setMessage(e.message);} finally{setBusy(false);}
  }
  function edit(key,value) { setDraft(current=>({...current,[key]:value})); }
  function revise(row) {
    setRevises(row.id);
    setDraft({title:row.title,topic:row.topic,sku:row.sku ?? "",note:row.note});
    editor.current?.scrollIntoView({block:"nearest"});
  }
  return <section className="surface"><h2>{shop?"补充条款":"待审店铺条款"}</h2>
    {shop ? <>
      <form className="action-fields" ref={editor} onSubmit={e=>submit(e)}>
        {revises && <p className="inline-feedback">正在修订条款 #{revises}。审核通过后适用于新订单。</p>}
        {!revises && Boolean(shop.supplement_templates?.length) && <label>用品类示例起草
          <select value="" onChange={event=>{
            const template=shop.supplement_templates[Number(event.target.value)-1];
            if(event.target.value && template) setDraft({title:template.title,topic:template.topic,note:template.note,sku:""});
          }}>
            <option value="">选择示例</option>{[[false,"本品类"],[true,"通用办理"]].map(([shared,label])=>
              <optgroup key={label} label={label}>{shop.supplement_templates.map((example,index)=>
                example.shared===shared && <option key={example.title} value={String(index+1)}>{example.title}</option>)}</optgroup>)}
          </select>
        </label>}
        <label>主题<select value={draft.topic} disabled={Boolean(revises)} onChange={e=>edit("topic",e.target.value)}>
          {Object.entries(topics).map(([key,label])=><option key={key} value={key}>{label}</option>)}
        </select></label>
        <label>适用商品<select value={draft.sku} disabled={Boolean(revises)} onChange={e=>edit("sku",e.target.value)}>
          <option value="">本店所有商品</option>{shop.products?.map(product=>
            <option key={product.sku} value={product.sku}>{product.title} · {product.sku}</option>)}
        </select></label>
        <label>标题<input name="title" value={draft.title} onChange={e=>edit("title",e.target.value)} required minLength={2} maxLength={60}/></label>
        <label>给买家的办理说明<textarea name="note" required minLength={5} maxLength={250}
          value={draft.note} onChange={e=>edit("note",e.target.value)}/></label>
        <div className="action-row">
          <button className="button button-primary" disabled={busy}>提交审核</button>
          {revises && <button type="button" className="button button-muted" onClick={()=>{
            setRevises(null); setDraft({title:"",topic:"missing",sku:"",note:""});
          }}>结束修订</button>}
        </div>
      </form>
      <PolicyPreview shop={shop}/>
      {Boolean(shop.supplements?.length) && <h3>条款及审核记录</h3>}
      {shop.supplements?.map(row=><details key={row.id}>
        <summary>{row.title ?? "整店缺件说明"} · #{row.id} · {statuses[row.decision] ?? "待审核"}</summary>
        <p>{topics[row.topic]} · {row.sku ?? "本店所有商品"}</p>
        <p>{row.note}</p>{row.feedback && <p>平台回复：{row.feedback}</p>}
        {row.can_revise && <button type="button" className="button button-muted" onClick={()=>revise(row)}>起草修订版</button>}
      </details>)}
    </> :
      rows.length ? rows.map(row=><form className="action-fields" key={row.id} onSubmit={e=>submit(e,row)}>
        <h3>{row.name} · {row.title ?? "缺件补发说明"}</h3>
        <p>{topics[row.topic]} · {row.sku ?? "本店所有商品"}</p>
        {row.previous_note && <details><summary>查看修订前的版本 #{row.revises_id}</summary><p>{row.previous_note}</p></details>}
        <p>{row.note}</p><label>审核结论<select name="decision" required defaultValue=""><option value="">请选择</option><option value="approved">通过，对新订单生效</option><option value="rejected">退回</option></select></label>
        <label>审核说明<input required name="feedback" maxLength={200} placeholder="核对平台保障、店铺承诺和办理限制"/></label><button className="button button-primary" disabled={busy}>确认审核</button></form>) : <p>暂无待审条款。</p>}
    <p role="status">{message}</p></section>;
}
