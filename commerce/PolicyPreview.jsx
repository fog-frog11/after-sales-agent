/** 以当前时间预览本店商品的买家条款，并可对照本机混合召回。 */
import {useState} from "react";
import {request} from "./core.js";

function PreviewEvidence({title, result}) {
  return <section className="inline-feedback">
    <h4>{title}</h4><p>{result.answer}</p>
    {result.evidence.map(clause => <blockquote key={clause.id}>
      <strong>{clause.source}</strong><p>{clause.text}</p>
    </blockquote>)}
    <small>查询 {result.ms} 毫秒{result.prepare_ms > 0 && " · 模型及索引准备 " + result.prepare_ms + " 毫秒"}</small>
  </section>;
}

export function PolicyPreview({shop}) {
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  if (!shop.products?.length) return null;

  async function preview(event) {
    event.preventDefault();
    if (loading) return;
    const values = new FormData(event.currentTarget);
    setLoading(true); setError(""); setResult(null);
    try {
      setResult(await request("/api/merchant/mall/supplements/" + encodeURIComponent(shop.shop_id) + "/preview", {
        sku: values.get("sku"), question: values.get("question"), hybrid: values.has("hybrid"),
      }));
    } catch (failure) {
      setError(failure.message);
    } finally {
      setLoading(false);
    }
  }

  return <details className="policy-trial">
    <summary>预览新订单的条款引用</summary>
    <form className="action-fields" onSubmit={preview}>
      <label>本店商品<select name="sku" required defaultValue="">
        <option value="">选择商品</option>
        {shop.products.map(product => <option key={product.sku} value={product.sku}>{product.title} · {product.sku}</option>)}
      </select></label>
      <label>买家会怎么问<input name="question" required minLength={2} maxLength={500} placeholder="例如：少了两个垫片，能单独补寄吗？"/></label>
      <label><input type="checkbox" name="hybrid"/> 同时试跑本机混合召回</label>
      <button className="button button-soft" disabled={loading}>{loading ? "正在核对条款…" : "预览"}</button>
    </form>
    {error && <p className="error-message" role="alert">{error}</p>}
    {result && <div className="action-fields" aria-live="polite">
      <p>当前下单版本 {result.edition} · 可查条款 {result.candidate_count} 条</p>
      <PreviewEvidence title="买家端检索" result={result.lexical}/>
      {result.hybrid && <PreviewEvidence title="混合召回试查" result={result.hybrid}/>}
      {result.hybrid_status === "unavailable" && <p>本机语义模型暂不可用，已显示买家端检索结果。</p>}
      {result.hybrid_status === "deferred" && <p>本次未运行混合试查，已显示买家端检索结果。</p>}
    </div>}
  </details>;
}
