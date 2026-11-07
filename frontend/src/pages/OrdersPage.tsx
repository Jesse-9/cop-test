import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { OrderHeader, OrderTarget } from "../api/types";
import {
  ORDER_STATUS_LABELS,
  SOURCE_LABELS,
  formatTime,
  orderStatusTone,
  shortDigest,
} from "../domain/status";
import { Badge, EmptyState, InlineError, LoadingState, SectionTitle } from "../components/ui";

interface OrderRow {
  order: OrderHeader;
  target: OrderTarget | null;
}

export function OrdersPage() {
  const navigate = useNavigate();
  const [rows, setRows] = useState<OrderRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [source, setSource] = useState("all");
  const [status, setStatus] = useState("all");

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const payload = await api.listOrders(100);
      const enriched = await Promise.all(
        payload.orders.map(async (order) => {
          try {
            const version = await api.getVersion(
              order.order_id,
              order.current_version,
            );
            return { order, target: version.version.spec.target };
          } catch {
            return { order, target: null };
          }
        }),
      );
      setRows(enriched);
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return rows.filter(({ order }) => {
      if (source !== "all" && order.source !== source) return false;
      if (
        status !== "all" &&
        (order.latest_run?.status ?? "NONE") !== status
      ) {
        return false;
      }
      if (!needle) return true;
      return [
        order.order_id,
        order.source,
        String(order.current_version),
        order.latest_run?.status ?? "",
      ]
        .join(" ")
        .toLowerCase()
        .includes(needle);
    });
  }, [rows, query, source, status]);

  return (
    <div className="page">
      <SectionTitle
        title="订单"
        description="订单版本不可变；编辑已保存订单会创建新版本。"
        actions={
          <>
            <Link className="button button-secondary" to="/planner">
              自主规划
            </Link>
            <Link className="button" to="/orders/new">
              新建订单
            </Link>
          </>
        }
      />
      {error ? (
        <InlineError message={error} onRetry={() => void load()} />
      ) : null}
      <div className="toolbar">
        <label className="field field-compact">
          <span className="field-label">搜索订单</span>
          <input
            type="search"
            value={query}
            placeholder="订单 ID、来源、状态…"
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <label className="field field-compact">
          <span className="field-label">来源</span>
          <select value={source} onChange={(event) => setSource(event.target.value)}>
            <option value="all">全部</option>
            <option value="manual">手工</option>
            <option value="planner">planner</option>
            <option value="fixture">fixture</option>
          </select>
        </label>
        <label className="field field-compact">
          <span className="field-label">最近运行</span>
          <select value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="all">全部</option>
            <option value="NONE">无运行</option>
            <option value="RUNNING">运行中</option>
            <option value="SUCCEEDED">成功</option>
            <option value="PARTIAL_FAILED">部分失败</option>
            <option value="FAILED">失败</option>
            <option value="ABORTED">中止</option>
          </select>
        </label>
        <span className="muted toolbar-count">
          {filtered.length} / {rows.length} 个订单
        </span>
      </div>
      {loading ? <LoadingState label="加载订单…" /> : null}
      {!loading && !error && rows.length === 0 ? (
        <EmptyState
          title="还没有订单"
          description="创建空白订单并手工连接工序，或让后端 planner 根据目标生成候选 DAG。"
          actions={
            <>
              <Link className="button" to="/orders/new">
                创建空白订单
              </Link>
              <Link className="button button-secondary" to="/planner">
                自主规划
              </Link>
            </>
          }
        />
      ) : null}
      {!loading && filtered.length > 0 ? (
        <div className="table-wrap">
          <table className="data-table order-table">
            <thead>
              <tr>
                <th>订单</th>
                <th>目标 / 数量</th>
                <th>来源</th>
                <th>版本</th>
                <th>最近运行</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(({ order, target }) => {
                const run = order.latest_run;
                return (
                  <tr key={order.order_id}>
                    <td>
                      <Link className="strong-link" to={`/orders/${encodeURIComponent(order.order_id)}`}>
                        {order.order_id}
                      </Link>
                      <span className="muted mono cell-sub">
                        {shortDigest(
                          order.versions.at(-1)?.dag_digest ?? "",
                          16,
                        )}
                      </span>
                    </td>
                    <td>
                      {target
                        ? `${target.item_type} × ${target.quantity}`
                        : "未记录"}
                    </td>
                    <td>
                      <Badge tone={order.source === "planner" ? "accent" : "neutral"}>
                        {SOURCE_LABELS[order.source]}
                      </Badge>
                    </td>
                    <td className="mono">v{order.current_version}</td>
                    <td>
                      {run ? (
                        <div className="stack-tight">
                          <Badge tone={orderStatusTone(run.status)}>
                            {ORDER_STATUS_LABELS[run.status]}
                          </Badge>
                          <Link
                            className="mono small-link"
                            to={`/runs/${encodeURIComponent(run.run_id)}`}
                          >
                            {run.run_id}
                          </Link>
                        </div>
                      ) : (
                        <span className="muted">无运行</span>
                      )}
                    </td>
                    <td>
                      <div className="button-row">
                        <button
                          type="button"
                          className="button button-secondary button-small"
                          onClick={() =>
                            navigate(`/orders/${encodeURIComponent(order.order_id)}`)
                          }
                        >
                          打开
                        </button>
                        <button
                          type="button"
                          className="button button-quiet button-small"
                          onClick={() =>
                            navigate(`/orders/${encodeURIComponent(order.order_id)}/edit`)
                          }
                        >
                          编辑新版本
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="muted table-footnote">
            最近更新时间 {formatTime(rows[0]?.order.updated_at)}
          </p>
        </div>
      ) : null}
      {!loading && !error && rows.length > 0 && filtered.length === 0 ? (
        <EmptyState
          title="没有匹配的订单"
          description="调整搜索或筛选条件；筛选只作用于已加载订单。"
          actions={
            <button
              type="button"
              className="button button-secondary"
              onClick={() => {
                setQuery("");
                setSource("all");
                setStatus("all");
              }}
            >
              清除筛选
            </button>
          }
        />
      ) : null}
    </div>
  );
}
