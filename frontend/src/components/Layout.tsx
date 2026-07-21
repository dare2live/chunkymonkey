import { NavLink, Outlet } from "react-router-dom";

const NAV_ITEMS = [
  { to: "/institutions", label: "机构档案", enabled: true },
  { to: "/paper", label: "观察账本", enabled: true },
  { to: "/workbench", label: "工作台", enabled: true },
  { to: "/market", label: "市场感知", enabled: true },
];

export function Layout() {
  return (
    <div className="layout">
      <header className="topbar">
        <span className="brand">ChunkyMonkey</span>
        <span className="brand-sub">edge 前端 v1</span>
      </header>
      <div className="layout-main">
        <nav className="sidenav">
          {NAV_ITEMS.map((item) =>
            item.enabled ? (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}
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
