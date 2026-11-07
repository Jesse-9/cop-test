import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type {
  Diagnostic,
  OrderVersionDetail,
  RunDetail,
  RunEvent,
  RunNode,
  TemplateCatalog,
} from "../api/types";
import { OrderCanvas, type Selection } from "../components/OrderCanvas";
import {
  Badge,
  EmptyState,
  InlineError,
  InlineNotice,
  LoadingState,
  SectionTitle,
} from "../components/ui";
import { layoutDraft } from "../domain/layout";
import { fromSpec } from "../domain/orderDraft";
import {
  NODE_STATE_LABELS,
  ORDER_STATUS_LABELS,
  failureScopeLabel,
  formatTime,
  isTerminalRunStatus,
  nodeStateTone,
  orderStatusTone,
  shortDigest,
} from "../domain/status";

const POLL_MS = 1500;
const NO_DIAGNOSTICS: Diagnostic[] = [];

export function RunPage() {
  const { runId = "" } = useParams();
  const [run, setRun] = useState<RunDetail | null>(null);
  const [nodes, setNodes] = useState<RunNode[]>([]);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [version, setVersion] = useState<OrderVersionDetail | null>(null);
  const [templates, setTemplates] = useState<TemplateCatalog | null>(null);
  const [selection, setSelection] = useState<Selection>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [pollError, setPollError] = useState("");
  const [stopping, setStopping] = useState(false);
  const [stopNotice, setStopNotice] = useState("");
  const [lastUpdated, setLastUpdated] = useState("");
  const mounted = useRef(true);

  const refresh = useCallback(async () => {
    if (!runId) return;
    try {
      const runPayload = await api.getRun(runId);
      if (!mounted.current) return;
      setRun(runPayload.run);
      setLastUpdated(new Date().toLocaleTimeString("zh-CN", { hour12: false }));
      const [nodesPayload, eventsPayload] = await Promise.all([
        api.getRunNodes(runId),
        api.getRunEvents(runId, 500).catch(() => ({ events: [] })),
      ]);
      if (!mounted.current) return;
      setNodes(nodesPayload.nodes);
      setEvents(eventsPayload.events);
      setPollError("");
    } catch (exc) {
      if (!mounted.current) return;
      setPollError(exc instanceof ApiError ? exc.message : String(exc));
    }
  }, [runId]);

  useEffect(() => {
    mounted.current = true;
    if (!runId) return;
    setLoading(true);
    setError("");
    api
      .getRun(runId)
      .then(async (runPayload) => {
        if (!mounted.current) return;
        setRun(runPayload.run);
        const [nodesPayload, eventsPayload, catalog] = await Promise.all([
          api.getRunNodes(runId),
          api.getRunEvents(runId, 500).catch(() => ({ events: [] })),
          api.templates().catch(() => null),
        ]);
        if (!mounted.current) return;
        setNodes(nodesPayload.nodes);
        setEvents(eventsPayload.events);
        setTemplates(catalog);
        try {
          const versionPayload = await api.getVersion(
            runPayload.run.order_id,
            runPayload.run.order_version,
          );
          if (mounted.current) setVersion(versionPayload.version);
        } catch {
          if (mounted.current) setVersion(null);
        }
      })
      .catch((exc) => {
        if (mounted.current) {
          setError(exc instanceof ApiError ? exc.message : String(exc));
        }
      })
      .finally(() => {
        if (mounted.current) setLoading(false);
      });
    return () => {
      mounted.current = false;
    };
  }, [runId]);

  useEffect(() => {
    if (!run || isTerminalRunStatus(run.status)) {
      return;
    }
    const interval = window.setInterval(() => {
      if (mounted.current) void refresh();
    }, POLL_MS);
    return () => window.clearInterval(interval);
  }, [run?.status, refresh]);

  const selectedNode = useMemo(() => {
    if (selection?.kind !== "node") return null;
    return nodes.find((node) => node.node_id === selection.nodeId) ?? null;
  }, [selection, nodes]);

  const runDraft = useMemo(() => {
    if (!version) return null;
    const base = fromSpec(version.spec);
    return { ...base, positions: layoutDraft(base) };
  }, [version]);

  const stop = async () => {
    setStopping(true);
    setStopNotice("");
    try {
      await api.stopRun(runId);
      setStopNotice("停止请求已提交；最终状态以服务端轮询结果为准。");
      await refresh();
    } catch (exc) {
      setStopNotice(exc instanceof ApiError ? exc.message : String(exc));
    } finally {
      setStopping(false);
    }
  };

  if (!runId) return <InlineError message="缺少运行 ID" />;
  if (loading && !run) return <LoadingState label="加载运行快照…" />;
  if (error) {
    return (
      <div className="page">
        <InlineError message={error} />
      </div>
    );
  }
  if (!run) {
    return <EmptyState title="运行不存在" description={`找不到运行 ${runId}`} />;
  }

  return (
    <div className="page run-page">
      <div className="run-header">
        <div>
          <div className="breadcrumbs">
            <Link to="/orders">订单</Link>
            <span aria-hidden="true">/</span>
            <Link to={`/orders/${encodeURIComponent(run.order_id)}`}>
              {run.order_id}
            </Link>
            <span aria-hidden="true">/</span>
            <span>{run.run_id}</span>
          </div>
          <h1 className="run-title">
            运行 {run.run_id}{" "}
            <Badge tone={orderStatusTone(run.status)}>
              {ORDER_STATUS_LABELS[run.status]}
            </Badge>
          </h1>
          <p className="muted run-meta">
            订单 v{run.order_version} · digest {shortDigest(run.dag_digest, 16)} · 工件{" "}
            {run.artifact_id || "未记录"} · 失败范围{" "}
            {failureScopeLabel(run.failure_scope)}
          </p>
          <p className="muted run-meta">
            开始 {formatTime(run.started_at)} · 结束{" "}
            {formatTime(run.finished_at)} · 最后刷新 {lastUpdated || "未刷新"}
          </p>
        </div>
        <div className="run-actions">
          <button
            type="button"
            className="button button-secondary"
            onClick={() => void refresh()}
          >
            刷新状态
          </button>
          <button
            type="button"
            className="button button-danger"
            disabled={isTerminalRunStatus(run.status) || stopping}
            onClick={() => void stop()}
          >
            {stopping ? "请求中…" : "请求停止"}
          </button>
        </div>
      </div>
      {stopNotice ? <InlineNotice tone="warning">{stopNotice}</InlineNotice> : null}
      {pollError ? (
        <InlineError message={`状态可能过期：${pollError}`} onRetry={() => void refresh()} />
      ) : null}
      {run.stop_reason ? (
        <InlineNotice tone={run.status === "ABORTED" ? "failure" : "warning"}>
          停止原因：{run.stop_reason}
        </InlineNotice>
      ) : null}
      {run.status === "PARTIAL_FAILED" ? (
        <InlineNotice tone="warning">
          订单部分失败：服务端报告存在局部失败；已完成的独立分支仍以节点状态显示。
        </InlineNotice>
      ) : null}
      {run.status === "ABORTED" ? (
        <InlineNotice tone="failure">
          全局中止：所有活动执行应已停止；未完成节点不会被界面显示为完成。
        </InlineNotice>
      ) : null}

      <div className="run-layout">
        <section className="panel run-graph">
          <SectionTitle
            title="节点状态图"
            description="只读运行快照；选中节点查看服务端字段。"
          />
          {runDraft && templates ? (
            <OrderCanvas
              draft={runDraft}
              templates={templates.templates}
              diagnostics={NO_DIAGNOSTICS}
              selected={selection}
              runNodes={nodes}
              readOnly
              onSelect={setSelection}
              onConnect={() => undefined}
              onMoveNode={() => undefined}
            />
          ) : (
            <p className="muted">
              无法加载订单版本快照；下方节点表仍展示服务端运行记录。
            </p>
          )}
        </section>
        <aside className="panel run-inspector" aria-label="运行节点详情">
          {selectedNode ? (
            <>
              <SectionTitle
                title="节点详情"
                description={`node_id ${selectedNode.node_id}`}
              />
              <dl className="detail-grid">
                <dt>状态</dt>
                <dd>
                  <Badge tone={nodeStateTone(selectedNode.state)}>
                    {NODE_STATE_LABELS[selectedNode.state]}
                  </Badge>
                </dd>
                <dt>模板</dt>
                <dd className="mono">{selectedNode.template_id}</dd>
                <dt>物料 UID</dt>
                <dd className="mono">
                  {selectedNode.uid}（输入{" "}
                  {selectedNode.input_uids.join(", ") || "无"} / 输出{" "}
                  {selectedNode.output_uids.join(", ") || "无"}）
                </dd>
                <dt>失败范围</dt>
                <dd>{failureScopeLabel(selectedNode.failure_scope)}</dd>
                <dt>failure code</dt>
                <dd className="mono">{selectedNode.failure_code || "未记录"}</dd>
                <dt>失败原因</dt>
                <dd>{selectedNode.failure_reason || "未记录"}</dd>
                <dt>阻断来源</dt>
                <dd className="mono">
                  {selectedNode.blocked_by.join(", ") || "未记录"}
                </dd>
              </dl>
              {Object.keys(selectedNode.evidence).length > 0 ? (
                <details className="explain-block">
                  <summary>节点证据</summary>
                  <pre className="json-block">
                    {JSON.stringify(selectedNode.evidence, null, 2)}
                  </pre>
                </details>
              ) : null}
            </>
          ) : (
            <EmptyState
              title="未选择节点"
              description="点击画布节点或下方节点表查看状态、失败范围和物料 UID。"
            />
          )}
        </aside>
      </div>

      <section className="panel">
        <SectionTitle
          title="节点表"
          description={`${nodes.length} 个节点 · ${run.failed_count} 失败 · ${run.cancelled_count} 取消`}
        />
        {nodes.length === 0 ? (
          <p className="muted">服务端尚未返回节点快照。</p>
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>节点</th>
                  <th>状态</th>
                  <th>UID</th>
                  <th>失败范围 / code</th>
                  <th>阻断来源</th>
                </tr>
              </thead>
              <tbody>
                {nodes.map((node) => (
                  <tr
                    key={node.instance_id}
                    className={
                      selection?.kind === "node" &&
                      selection.nodeId === node.node_id
                        ? "selected-row"
                        : ""
                    }
                  >
                    <td>
                      <button
                        type="button"
                        className="link-button mono"
                        onClick={() =>
                          setSelection({ kind: "node", nodeId: node.node_id })
                        }
                      >
                        {node.node_id}
                      </button>
                      <span className="muted mono cell-sub">{node.template_id}</span>
                    </td>
                    <td>
                      <Badge tone={nodeStateTone(node.state)}>
                        {NODE_STATE_LABELS[node.state]}
                      </Badge>
                    </td>
                    <td className="mono">{node.uid}</td>
                    <td>
                      {node.failure_scope || node.failure_code ? (
                        <span className="mono">
                          {failureScopeLabel(node.failure_scope)}{" "}
                          {node.failure_code}
                        </span>
                      ) : (
                        <span className="muted">未记录</span>
                      )}
                    </td>
                    <td className="mono">
                      {node.blocked_by.join(", ") || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel">
        <SectionTitle
          title="事件时间线"
          description={`最近 ${events.length} 条服务端事件；完整记录以服务端返回为准。`}
        />
        {events.length === 0 ? (
          <p className="muted">服务端未返回事件；运行记录可能未开启。</p>
        ) : (
          <ol className="event-timeline">
            {events.slice(-200).reverse().map((event, index) => (
              <li key={`${event.event_id ?? index}-${event.kind}`}>
                <div className="event-head">
                  <span className="mono event-time">
                    {formatTime(event.sim_time)}
                  </span>
                  <span className="event-kind">{event.kind}</span>
                  {event.task_id ? (
                    <span className="mono muted">{event.task_id}</span>
                  ) : null}
                  {event.uid !== null && event.uid !== undefined ? (
                    <span className="mono muted">uid {event.uid}</span>
                  ) : null}
                </div>
                {event.detail && Object.keys(event.detail).length > 0 ? (
                  <details>
                    <summary>事件字段</summary>
                    <pre className="json-block">
                      {JSON.stringify(event.detail, null, 2)}
                    </pre>
                  </details>
                ) : null}
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}
