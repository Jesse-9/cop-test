import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type {
  CompilePreview,
  OrderHeader,
  OrderVersionDetail,
  RunMeta,
} from "../api/types";
import { DiagnosticsPanel } from "../components/Inspector";
import { StartRunDialog } from "../components/StartRunDialog";
import {
  Badge,
  EmptyState,
  InlineError,
  LoadingState,
  SectionTitle,
} from "../components/ui";
import {
  ORDER_STATUS_LABELS,
  SOURCE_LABELS,
  formatTime,
  orderStatusTone,
  shortDigest,
} from "../domain/status";

export function OrderDetailPage() {
  const { orderId = "" } = useParams();
  const [order, setOrder] = useState<OrderHeader | null>(null);
  const [selectedVersion, setSelectedVersion] = useState(0);
  const [version, setVersion] = useState<OrderVersionDetail | null>(null);
  const [preview, setPreview] = useState<CompilePreview | null>(null);
  const [runs, setRuns] = useState<RunMeta[]>([]);
  const [loading, setLoading] = useState(true);
  const [versionLoading, setVersionLoading] = useState(false);
  const [error, setError] = useState("");
  const [versionError, setVersionError] = useState("");

  useEffect(() => {
    if (!orderId) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    Promise.all([api.getOrder(orderId), api.listOrderRuns(orderId, 20)])
      .then(([orderPayload, runsPayload]) => {
        if (cancelled) return;
        setOrder(orderPayload.order);
        setRuns(runsPayload.runs);
        setSelectedVersion(orderPayload.order.current_version);
      })
      .catch((exc) => {
        if (!cancelled) {
          setError(exc instanceof ApiError ? exc.message : String(exc));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [orderId]);

  useEffect(() => {
    if (!orderId || !selectedVersion) return;
    let cancelled = false;
    setVersionLoading(true);
    setVersionError("");
    api
      .getVersion(orderId, selectedVersion)
      .then((payload) => {
        if (!cancelled) setVersion(payload.version);
      })
      .catch((exc) => {
        if (!cancelled) {
          setVersionError(exc instanceof ApiError ? exc.message : String(exc));
        }
      });
    api
      .previewVersion(orderId, selectedVersion)
      .then((payload) => {
        if (!cancelled) setPreview(payload.preview);
      })
      .catch((exc) => {
        if (!cancelled) {
          setVersionError(exc instanceof ApiError ? exc.message : String(exc));
        }
      })
      .finally(() => {
        if (!cancelled) setVersionLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [orderId, selectedVersion]);

  if (!orderId) {
    return <InlineError message="缺少订单 ID" />;
  }
  if (loading && !order) {
    return <LoadingState label="加载订单…" />;
  }
  if (error) {
    return (
      <div className="page">
        <InlineError message={error} />
      </div>
    );
  }
  if (!order) {
    return (
      <EmptyState title="订单不存在" description={`找不到订单 ${orderId}`} />
    );
  }

  return (
    <div className="page">
      <SectionTitle
        title={order.order_id}
        description={`当前版本 v${order.current_version} · 来源 ${SOURCE_LABELS[order.source]} · 更新 ${formatTime(order.updated_at)}`}
        actions={
          <Link
            className="button button-secondary"
            to={`/orders/${encodeURIComponent(order.order_id)}/edit`}
          >
            基于最新版本编辑
          </Link>
        }
      />
      <div className="detail-toolbar">
        <label className="field field-compact">
          <span className="field-label">查看版本</span>
          <select
            value={selectedVersion || order.current_version}
            onChange={(event) => setSelectedVersion(Number(event.target.value))}
          >
            {order.versions.map((row) => (
              <option key={row.version} value={row.version}>
                v{row.version} · {SOURCE_LABELS[row.source]} ·{" "}
                {shortDigest(row.dag_digest, 10)}
              </option>
            ))}
          </select>
        </label>
        {versionLoading ? <span className="muted">加载版本…</span> : null}
      </div>
      {versionError ? <InlineError message={versionError} /> : null}
      {version ? (
        <div className="detail-grid-layout">
          <section className="panel">
            <SectionTitle
              title={`版本 v${version.version}`}
              description={`创建 ${formatTime(version.created_at)} · ${version.note || "无备注"}`}
            />
            <dl className="detail-grid">
              <dt>来源</dt>
              <dd>
                <Badge tone={version.source === "planner" ? "accent" : "neutral"}>
                  {SOURCE_LABELS[version.source]}
                </Badge>
              </dd>
              <dt>digest</dt>
              <dd className="mono">{version.dag_digest}</dd>
              <dt>目录版本</dt>
              <dd className="mono">{version.catalog_version || "未记录"}</dd>
              <dt>场景</dt>
              <dd className="mono">{version.scene_id}</dd>
              <dt>目标</dt>
              <dd>
                {version.spec.target.item_type} × {version.spec.target.quantity}
              </dd>
              <dt>节点 / 连线</dt>
              <dd>
                {version.spec.nodes.length} / {version.spec.edges.length}
              </dd>
              <dt>投料</dt>
              <dd>
                {version.spec.inputs
                  .map(
                    (input) =>
                      `${input.item_type} × ${input.quantity} @ ${input.zone || "自动"}`,
                  )
                  .join("；") || "无"}
              </dd>
              {version.planner ? (
                <>
                  <dt>planner 策略</dt>
                  <dd className="mono">{version.planner.policy_version}</dd>
                  <dt>候选数</dt>
                  <dd>{version.planner.candidate_count ?? "未记录"}</dd>
                </>
              ) : null}
            </dl>
            {version.planner?.explain ? (
              <details className="explain-block">
                <summary>查看 planner explain</summary>
                <pre className="json-block">
                  {JSON.stringify(version.planner.explain, null, 2)}
                </pre>
              </details>
            ) : null}
          </section>
          <section className="panel">
            <SectionTitle
              title="服务端编译预览"
              description={
                preview?.ok
                  ? `通过 · digest ${shortDigest(preview.digest)}`
                  : "用于确认保存版本可被同一 compiler 编译。"
              }
            />
            {preview ? (
              <>
                <div className="preview-facts">
                  <Badge tone={preview.ok ? "success" : "failure"}>
                    {preview.ok ? "预览通过" : "预览失败"}
                  </Badge>
                  <span className="mono">digest {shortDigest(preview.digest, 16)}</span>
                  <span className="muted">
                    {preview.diagnostics.length} 个诊断
                  </span>
                </div>
                <DiagnosticsPanel
                  diagnostics={preview.diagnostics}
                  onSelect={() => undefined}
                  emptyLabel="没有诊断。"
                />
                {preview.ok ? (
                  <StartRunDialog
                    orderId={order.order_id}
                    version={version.version}
                    enabled
                  />
                ) : null}
              </>
            ) : (
              <p className="muted">正在加载预览…</p>
            )}
          </section>
        </div>
      ) : null}
      <section className="panel">
        <SectionTitle title="运行历史" description="只读运行快照与终态。" />
        {runs.length === 0 ? (
          <p className="muted">该订单还没有运行记录。</p>
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>运行</th>
                  <th>版本</th>
                  <th>状态</th>
                  <th>开始</th>
                  <th>失败范围</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.run_id}>
                    <td>
                      <Link
                        className="mono strong-link"
                        to={`/runs/${encodeURIComponent(run.run_id)}`}
                      >
                        {run.run_id}
                      </Link>
                    </td>
                    <td className="mono">v{run.order_version}</td>
                    <td>
                      <Badge tone={orderStatusTone(run.status)}>
                        {ORDER_STATUS_LABELS[run.status]}
                      </Badge>
                    </td>
                    <td>{formatTime(run.started_at)}</td>
                    <td>{run.failure_scope || "未记录"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
