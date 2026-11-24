/** 用和初始化脚本共用的案例清单跳转到实际订单。 */
import {useState} from "react";
import guide from "../../app/portfolio_cases.json";
import {orderUrl} from "./core.js";

export function PortfolioCases({account}) {
  const [copied, setCopied] = useState("");
  const canOpen = account?.account_id === guide.buyer;
  async function copyQuestion(entry) {
    try {
      await navigator.clipboard.writeText(entry.question);
      setCopied(entry.key);
    } catch {
      setCopied("");
    }
  }

  return <div className="portfolio-cases">
    <p className="quiet">买家使用 <strong>{guide.buyer}</strong>，店铺使用 <strong>{guide.merchant}</strong>，平台客服使用 <strong>{guide.staff}</strong>。</p>
    {account && !canOpen && <p className="inline-feedback">退出当前账号，再用 {guide.buyer} 登录可打开案例订单。</p>}
    {guide.cases.map(entry => <section className="surface portfolio-case" key={entry.key}>
      <h2>{entry.title}</h2>
      <ol>{entry.steps.map(step => <li key={step}>{step}</li>)}</ol>
      {entry.question && <div className="portfolio-question">
        <p>{entry.question}</p>
        <button type="button" className="button button-muted" onClick={() => copyQuestion(entry)}>
          {copied === entry.key ? "已复制" : "复制问法"}
        </button>
      </div>}
      <div className="action-row">{entry.orders.map(order => canOpen
        ? <a className="button button-soft" key={order.id} href={orderUrl(order.id, "1")}>{order.label} · {order.id}</a>
        : <span key={order.id}>{order.id}</span>)}</div>
    </section>)}
  </div>;
}
