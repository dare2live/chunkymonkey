import { NavLink, Outlet, useLocation } from "react-router-dom";

// 产品洞察面在前, ops 面 (工作台) 收尾 — 每日打开先看趋势/关系/大局, 而非运维。
const NAV_ITEMS = [
  { to: "/market", label: "市场", enabled: true, prefix: "/market" },
  { to: "/stock/600519", label: "股票档案", enabled: true, prefix: "/stock" },
  { to: "/institutions", label: "机构档案", enabled: true, prefix: "/institutions" },
  { to: "/paper", label: "观察账本", enabled: true, prefix: "/paper" },
  { to: "/workbench", label: "工作台", enabled: true, prefix: "/workbench" },
];

export function Layout() {
  const loc = useLocation();
  return (
    <div className="layout">
      <header className="topbar">
        <span className="brand">ChunkyMonkey</span>
        <span className="brand-sub">每日市场洞察</span>
      </header>
      <div className="layout-main">
        <nav className="sidenav">
          {NAV_ITEMS.map((item) =>
            item.enabled ? (
              <NavLink
                key={item.to}
                to={item.to}
                className={() =>
                  `nav-item${loc.pathname.startsWith(item.prefix) ? " active" : ""}`
                }
              >
                {item.label}
              </NavLink>
            ) : (
              <span key={item.to} className="nav-item disabled" title="占位 — 未实现">
                {item.label}
                <em>占位</em>
              </span>
            ),
          )}
        </nav>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
