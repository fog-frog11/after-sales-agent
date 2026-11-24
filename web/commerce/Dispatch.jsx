/** 订单速览和包裹图形。速览跟随当前查询结果，打开同一笔订单的正式详情。 */
import {useState} from "react";
import {localTime, orderUrl, openInline, stages} from "./core.js";

/** 三个折面组成包裹，灯光和轨道由 CSS 绘制。 */
export function ParcelArt() {
  return <span className="parcel-art" aria-hidden="true"><i/><b/><em/></span>;
}

/** 快速翻看订单，保留普通链接的键盘操作和新标签打开能力。 */
export function Dispatch({rows, open}) {
  const [position, setPosition] = useState(0);
  const index = Math.min(position, Math.max(0, rows.length - 1));
  const row = rows[index];
  return <section className="dispatch">
    <div className="dispatch-head"><h2>订单速览</h2><a id="mall-after-sales-link" href="/mall/after-sales">我的售后 ↗</a></div>
    <div className="dispatch-stage"><img className="dispatch-photo" src="/parcel-studio.jpg" alt="" fetchPriority="high"/>
      <div className="dispatch-caption" key={row?.order_id} aria-live="polite">
        {row ? <><small>{row.stages.map(stage => stages[stage]).join(" · ")}</small>
          <h3>{row.shop_name}</h3><p>{row.order_id} · {localTime(row.ordered_at)}</p>
          <a href={orderUrl(row.order_id)} onClick={event => openInline(event, () => open(row.order_id))}>查看订单 <span>↗</span></a></>
          : <h3>暂无可预览的订单</h3>}
      </div>
    </div>
    {row && <div className="dispatch-controls"><span>{index + 1} / {rows.length} 笔已载入</span>
      <button aria-label="上一笔订单" disabled={!index} onClick={() => setPosition(index - 1)}>←</button>
      <button aria-label="下一笔订单" disabled={index >= rows.length - 1} onClick={() => setPosition(index + 1)}>→</button></div>}
  </section>;
}
