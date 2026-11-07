import { Navigate, Route, Routes, Link } from "react-router-dom";
import { AppHeader } from "./components/AppHeader";
import { useHealth } from "./hooks/useHealth";
import { OrderDetailPage } from "./pages/OrderDetailPage";
import { OrderEditorPage } from "./pages/OrderEditorPage";
import { OrdersPage } from "./pages/OrdersPage";
import { PlannerPage } from "./pages/PlannerPage";
import { RunPage } from "./pages/RunPage";

function NotFoundPage() {
  return (
    <div className="page">
      <div className="state-block">
        <strong>页面不存在</strong>
        <p>该路径没有对应的订单工作台页面。</p>
        <Link className="button" to="/orders">
          返回订单列表
        </Link>
      </div>
    </div>
  );
}

export function App() {
  const health = useHealth();
  return (
    <div className="app-shell">
      <AppHeader health={health} />
      <main id="main-content" className="app-main">
        <Routes>
          <Route path="/" element={<Navigate to="/orders" replace />} />
          <Route path="/orders" element={<OrdersPage />} />
          <Route path="/orders/new" element={<OrderEditorPage />} />
          <Route path="/orders/:orderId" element={<OrderDetailPage />} />
          <Route path="/orders/:orderId/edit" element={<OrderEditorPage />} />
          <Route path="/planner" element={<PlannerPage />} />
          <Route path="/runs/:runId" element={<RunPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </main>
      <footer className="app-footer">
        <span>本地订单工作台 · 浏览器不直接控制 CoppeliaSim</span>
        <span className="mono">
          {health.health?.contract_version ?? "API 契约未连接"}
        </span>
      </footer>
    </div>
  );
}
