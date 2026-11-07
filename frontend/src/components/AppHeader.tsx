import { NavLink } from "react-router-dom";
import type { HealthSnapshot } from "../hooks/useHealth";
import { Badge } from "./ui";

const NAV_ITEMS = [
  { to: "/orders", label: "订单列表" },
  { to: "/orders/new", label: "新建订单" },
  { to: "/planner", label: "自主规划" },
];

export function AppHeader({ health }: { health: HealthSnapshot }) {
  const stateLabel =
    health.state === "online"
      ? "API 已连接"
      : health.state === "checking"
        ? "API 检查中"
        : "API 离线";
  const tone =
    health.state === "online"
      ? "success"
      : health.state === "checking"
        ? "neutral"
        : "failure";
  return (
    <header className="app-header">
      <a className="skip-link" href="#main-content">
        跳到主要内容
      </a>
      <div className="brand">
        <span className="brand-mark" aria-hidden="true">
          FF
        </span>
        <span className="brand-copy">
          <strong>柔性工厂</strong>
          <span>订单工作台</span>
        </span>
      </div>
      <nav className="main-nav" aria-label="主要导航">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) => (isActive ? "active" : "")}
            end={item.to === "/orders"}
          >
            {item.label}
          </NavLink>
        ))}
      </nav>
      <div className="api-state" title={health.error || stateLabel}>
        <Badge tone={tone}>{stateLabel}</Badge>
        {health.state === "offline" ? (
          <button
            type="button"
            className="button button-quiet"
            onClick={health.refresh}
          >
            重试
          </button>
        ) : null}
      </div>
    </header>
  );
}
