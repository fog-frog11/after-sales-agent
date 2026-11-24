/** 三种商城页面共用的状态标签和空状态。 */
export function Status({children, tone}) {
  return <span className="status-pill" data-tone={tone}>{children}</span>;
}

export function Empty({heading, children}) {
  return <div className="empty-state">
    <span aria-hidden="true" className="empty-icon">≡</span>
    <strong>{heading}</strong>
    {children && <p>{children}</p>}
  </div>;
}
