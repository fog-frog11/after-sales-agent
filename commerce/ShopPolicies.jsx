/** 店铺可以核对平台底线，设置生效日并发布供新订单使用的承诺。 */
import {useEffect, useState} from "react";
import {request, yuan} from "./core.js";
import {Supplements} from "./Supplements.jsx";

const fields = [["return_days", "无理由退换（天）"], ["fault_days", "质量售后（天）"],
  ["warranty_days", "支持维修的商品（天）"], ["reply_hours", "店铺响应（小时）"],
  ["return_postage", "寄回报价（元）"], ["item_credit", "每款商品运费权益（元）"]];

/** 依次展示店铺各版承诺；编辑始终以最近发布版为基础。 */
export function ShopPolicies() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  async function load() {
    setData(await request("/api/merchant/mall/policies"));
  }
  useEffect(() => { load().catch(err => setError(err.message)); }, []);

  async function publish(event, shop) {
    event.preventDefault();
    if (busy) return;
    const values = Object.fromEntries(new FormData(event.currentTarget));
    setBusy(true); setError(""); setMessage("");
    try {
      const saved = await request("/api/merchant/mall/policies/" + encodeURIComponent(shop.shop_id),
        {...values, base_version:shop.editions[0].version, seller_advances:values.seller_advances === "yes"});
      setMessage("第 " + saved.version + " 版已发布，" + saved.effective_on + " 起对新订单生效。");
      await load();
    } catch (err) {
      setError(err.message);
      if (err.status === 409) await load().catch(e => setError(e.message));
    } finally { setBusy(false); }
  }
  return <div className="policy-book">
    {error && <p className="error-message" role="alert">{error}</p>}
    {message && <p className="inline-feedback" role="status">{message}</p>}
    {!data && !error && <p role="status">正在读取条款…</p>}
    {data && <section className="surface policy-floor"><h2>平台最低保障</h2>
      <ul>{data.platform.map(line => <li key={line}>{line}</li>)}</ul></section>}
    {data?.shops.map(shop => {
      const latest = shop.editions[0];
      const current = shop.editions.find(edition => edition.effective_on <= data.today);
      return <section className="policy-columns" key={shop.shop_id}>
        <div className="surface"><h2>{shop.name} · 已发布条款</h2>
          {shop.editions.map(ed => <details key={ed.version} open={ed.version === current?.version}>
            <summary>第 {ed.version} 版 · {ed.effective_on} · {ed.version === current?.version ? "生效中" : ed.effective_on > data.today ? "待生效" : "历史"}</summary>
            <p>无理由退换 {ed.return_days} 天，质量售后 {ed.fault_days} 天，支持维修的商品保障 {ed.warranty_days} 天。</p>
            <p>{ed.reply_hours} 小时内响应，寄回报价 {yuan(ed.return_postage)}。{ed.item_credit != null && "每款商品运费权益至少 " + yuan(ed.item_credit) + "。"}</p>
            {ed.guidance && <p>{ed.guidance}</p>}
          </details>)}</div>
        <form key={latest.version} className="surface action-fields" onSubmit={event => publish(event, shop)}>
          <h2>发布下一版</h2><p className="quiet">新版本适用于生效后购买的订单。</p>
          <div className="policy-fields">{fields.map(([id,label]) => <label key={id}>{label}
            <input name={id} type="number" step={["item_credit","return_postage"].includes(id) ? "0.01" : "1"} min="0" required defaultValue={latest[id] ?? 0}/></label>)}</div>
          <label>责任类寄回运费<select name="seller_advances" defaultValue={latest.seller_advances ? "yes" : "no"}>
            <option value="yes">买家先垫付，店铺核实后返还</option><option value="no">店铺提供寄回方式，买家免垫付</option></select></label>
          <label>本店办理指引<select name="guidance_key" defaultValue={latest.guidance_key ?? "category"}>
            {Object.entries(shop.guidance_options).map(([key,line]) => <option key={key} value={key}>{line}</option>)}</select></label>
          <label>生效日期<input type="date" name="effective_on" min={shop.first_date} defaultValue={shop.first_date} required /></label>
          <button className="button button-primary" disabled={busy}>{busy ? "正在发布…" : "发布新版本"}</button>
        </form>
        <Supplements shop={shop} onSubmitted={load}/>
      </section>;
    })}
  </div>;
}
