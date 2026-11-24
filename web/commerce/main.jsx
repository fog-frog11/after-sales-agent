/** 商城 React 入口，为各角色提供共用导航和会话。 */
import {useEffect, useState} from "react";
import {createRoot} from "react-dom/client";
import {request, routes} from "./core.js";
import {Records} from "./Records.jsx";
import {Directory} from "./Directory.jsx";
import {ShopPolicies} from "./ShopPolicies.jsx";
import {Supplements} from "./Supplements.jsx";
import {PortfolioCases} from "./PortfolioCases.jsx";
import "./workbench.css";
import "./studio.css";

const path = location.pathname;
const role = path.startsWith("/staff/") ? "staff" : path.startsWith("/merchant/") ? "merchant" : "buyer";
const directory = ["/staff/mall/catalog", "/merchant/mall/orders"].includes(path);
const orders = path === "/mall/orders";
const title = path === "/mall/portfolio" ? "项目案例" : routes[role].find(link => link[1] === path)?.[0];
document.title = title + " · 商城售后";

/** 工作台顶部显示当前身份与角色导航，窄屏将导航停靠到底部。 */
function Shell({children}) {
  const [account, setAccount] = useState(null);
  const [leaving, setLeaving] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    request("/api/session").then(setAccount).catch(error => setError(error.message));
  }, []);
  const name = account?.display_name ?? account?.account_id;
  const roleLabel = {buyer: "买家", merchant: "店铺客服", staff: "平台客服"}[role];

  async function logout() {
    setLeaving(true);
    setError("");
    try {
      await request("/api/session/logout", {});
      location.replace("/login");
    } catch (error) {
      setError(error.message);
      setLeaving(false);
    }
  }

  return <div className="workbench-shell">
    <header className="masthead">
      <a className="workbench-brand" href={routes[role][0][1]}><span aria-hidden="true">↶</span>商城售后</a>
      <nav aria-label="工作台导航">
        {routes[role].filter(([, href]) => href !== "/merchant/mall/policies" || account?.shop_policy_workbench)
          .map(([label, href]) => <a key={href} href={href} id={href === "/mall/after-sales" ? "mall-after-sales-link" : undefined}
          aria-current={path === href ? "page" : undefined}>{label}</a>)}
      </nav>
      <div className="sidebar-account">
        <span className="avatar" aria-hidden="true">{name?.slice(0, 1) || "↶"}</span>
        <div><strong id="access-identity">{name}</strong><small>{roleLabel}</small></div>
        <button type="button" id="access-logout" hidden={!name} disabled={leaving} onClick={logout}>退出</button>
        {error && <p role="status">{error}</p>}
      </div>
    </header>
    <main className="workbench-main" aria-labelledby="workbench-title">
      <header className="page-title"><h1 id="workbench-title">{title}</h1><span>{roleLabel}工作台</span></header>
      {path === "/mall/portfolio" ? <PortfolioCases account={account}/> : children}
    </main>
  </div>;
}

createRoot(document.getElementById("commerce-app")).render(
  <Shell>{path === "/merchant/mall/policies" ? <ShopPolicies /> : directory ? <><Directory role={role}/>{role==="staff" && <Supplements/>}</> : <Records role={role} orders={orders} />}</Shell>
);
