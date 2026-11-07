import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type {
  CompilePreview,
  Diagnostic,
  TemplateSummary,
} from "../api/types";
import { ITEM_TYPES } from "../api/types";
import { DiagnosticsPanel, Inspector, NodeEdgeLists } from "../components/Inspector";
import { OrderCanvas, type Selection } from "../components/OrderCanvas";
import { TemplatePalette } from "../components/TemplatePalette";
import {
  Badge,
  Field,
  InlineError,
  InlineNotice,
  LoadingState,
  SectionTitle,
} from "../components/ui";
import { layoutDraft, defaultPosition } from "../domain/layout";
import {
  addEdge,
  addNode,
  blankDraft,
  cloneDraft,
  fromSpec,
  localDiagnostics,
  templateMap,
  toSpec,
  updateTarget,
  type DraftState,
  type NodePosition,
} from "../domain/orderDraft";

function signature(draft: DraftState): string {
  return JSON.stringify(toSpec(draft));
}

export function OrderEditorPage() {
  const { orderId } = useParams();
  const navigate = useNavigate();
  const isNew = !orderId;

  const [templates, setTemplates] = useState<TemplateSummary[]>([]);
  const [draft, setDraft] = useState<DraftState | null>(null);
  const [past, setPast] = useState<DraftState[]>([]);
  const [future, setFuture] = useState<DraftState[]>([]);
  const [selection, setSelection] = useState<Selection>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [localError, setLocalError] = useState("");
  const [dirty, setDirty] = useState(false);
  const [preview, setPreview] = useState<CompilePreview | null>(null);
  const [previewSignature, setPreviewSignature] = useState("");
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [baseVersion, setBaseVersion] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    const load = async () => {
      try {
        const catalog = await api.templates();
        if (cancelled) return;
        setTemplates(catalog.templates);
        if (orderId) {
          const order = await api.getOrder(orderId);
          if (cancelled) return;
          const version = await api.getVersion(
            order.order.order_id,
            order.order.current_version,
          );
          if (cancelled) return;
          const loaded = fromSpec(version.version.spec);
          setDraft(loaded);
          setBaseVersion(version.version.version);
          setDirty(false);
        } else {
          const blank = blankDraft();
          blank.contractVersion = catalog.contract_version;
          blank.sceneId = catalog.scene_id;
          setDraft(blank);
          setBaseVersion(0);
          setDirty(true);
        }
      } catch (exc) {
        if (!cancelled) {
          setError(exc instanceof ApiError ? exc.message : String(exc));
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [orderId]);

  const map = useMemo(() => templateMap(templates), [templates]);
  const localIssues = useMemo(
    () => (draft ? localDiagnostics(draft, map) : []),
    [draft, map],
  );
  const allDiagnostics = useMemo(
    () => [...localIssues, ...(preview?.diagnostics ?? [])],
    [localIssues, preview],
  );
  const previewMatches = useMemo(
    () => draft !== null && previewSignature === signature(draft),
    [draft, previewSignature],
  );
  const canSave =
    draft !== null &&
    preview !== null &&
    preview.ok &&
    previewMatches &&
    !saving;

  const applySemantic = (next: DraftState) => {
    if (!draft) return;
    setPast((rows) => [...rows.slice(-49), draft]);
    setFuture([]);
    setDraft(next);
    setDirty(true);
    setPreview(null);
    setPreviewSignature("");
    setLocalError("");
    setSaveError("");
  };

  const setPositions = (nodeId: string, position: NodePosition) => {
    if (!draft) return;
    setDraft({
      ...draft,
      positions: { ...draft.positions, [nodeId]: position },
    });
  };

  const undo = () => {
    if (!draft || past.length === 0) return;
    const previous = past[past.length - 1];
    setPast(past.slice(0, -1));
    setFuture([draft, ...future]);
    setDraft(previous);
    setDirty(true);
    setPreview(null);
    setPreviewSignature("");
  };

  const redo = () => {
    if (!draft || future.length === 0) return;
    const next = future[0];
    setFuture(future.slice(1));
    setPast([...past, draft]);
    setDraft(next);
    setDirty(true);
    setPreview(null);
    setPreviewSignature("");
  };

  const addTemplateNode = (templateId: string) => {
    if (!draft) return;
    const position = defaultPosition(draft.nodes.length);
    const result = addNode(draft, templateId, position);
    applySemantic(result.draft);
    setSelection({ kind: "node", nodeId: result.nodeId });
  };

  const connect = (edge: Parameters<typeof addEdge>[1]) => {
    if (!draft) return;
    const result = addEdge(draft, edge, map);
    if (result.error) {
      setLocalError(result.error);
      return;
    }
    applySemantic(result.draft);
  };

  const autoLayout = () => {
    if (!draft) return;
    setDraft({
      ...cloneDraft(draft),
      positions: layoutDraft(draft),
    });
  };

  const runPreview = async () => {
    if (!draft) return;
    setPreviewLoading(true);
    setPreviewError("");
    try {
      const payload = await api.preview({
        ...toSpec(draft),
        source: "manual",
      });
      setPreview(payload.preview);
      setPreviewSignature(signature(draft));
    } catch (exc) {
      setPreview(null);
      setPreviewSignature("");
      setPreviewError(exc instanceof ApiError ? exc.message : String(exc));
    } finally {
      setPreviewLoading(false);
    }
  };

  const save = async () => {
    if (!draft || !canSave) return;
    setSaving(true);
    setSaveError("");
    try {
      const payload = {
        ...toSpec(draft),
        source: "manual" as const,
        order_id: draft.orderId,
        version: isNew ? 1 : baseVersion + 1,
      };
      const result = isNew
        ? await api.createOrder(payload)
        : await api.createVersion(orderId as string, payload);
      navigate(`/orders/${encodeURIComponent(result.order.order_id)}`);
    } catch (exc) {
      setSaveError(exc instanceof ApiError ? exc.message : String(exc));
    } finally {
      setSaving(false);
    }
  };

  const selectDiagnostic = (diagnostic: Diagnostic) => {
    if (diagnostic.edge_index >= 0) {
      setSelection({ kind: "edge", index: diagnostic.edge_index });
    } else if (diagnostic.node_id) {
      setSelection({ kind: "node", nodeId: diagnostic.node_id });
    }
  };

  if (loading) return <LoadingState label="加载订单目录…" />;
  if (error) return <div className="page"><InlineError message={error} /></div>;
  if (!draft) return <InlineError message="没有可编辑的订单草稿" />;

  const title = isNew ? "新建订单" : `编辑 ${draft.orderId}`;
  return (
    <div className="page editor-page">
      <div className="editor-topbar">
        <div>
          <div className="breadcrumbs">
            <Link to="/orders">订单</Link>
            <span aria-hidden="true">/</span>
            <span>{isNew ? "新建" : draft.orderId}</span>
          </div>
          <h1>{title}</h1>
          <div className="editor-status">
            <Badge tone={dirty ? "warning" : "success"}>
              {dirty ? "未保存" : "已保存"}
            </Badge>
            <span className="muted">
              {isNew
                ? "新订单 v1"
                : `基于 v${baseVersion} 创建新版本`}
            </span>
            {preview ? (
              <Badge tone={preview.ok ? "success" : "failure"}>
                {preview.ok ? "预览通过" : "预览失败"}
              </Badge>
            ) : (
              <Badge tone="neutral">未预览</Badge>
            )}
          </div>
        </div>
        <div className="editor-actions">
          <button
            type="button"
            className="button button-quiet"
            onClick={undo}
            disabled={past.length === 0}
          >
            撤销
          </button>
          <button
            type="button"
            className="button button-quiet"
            onClick={redo}
            disabled={future.length === 0}
          >
            重做
          </button>
          <button type="button" className="button button-secondary" onClick={autoLayout}>
            自动布局
          </button>
          <button
            type="button"
            className="button button-secondary"
            onClick={() => void runPreview()}
            disabled={previewLoading}
          >
            {previewLoading ? "预览中…" : "服务端预览"}
          </button>
          <button
            type="button"
            className="button"
            onClick={() => void save()}
            disabled={!canSave}
            title={
              canSave
                ? "保存为新的不可变版本"
                : "先通过当前草稿的服务端预览才能保存"
            }
          >
            {saving ? "保存中…" : isNew ? "保存订单" : "保存新版本"}
          </button>
        </div>
      </div>

      <div className="editor-meta">
        <Field label="订单 ID" htmlFor="order-id">
          <input
            id="order-id"
            value={draft.orderId}
            readOnly={!isNew}
            onChange={(event) =>
              applySemantic({ ...draft, orderId: event.target.value })
            }
          />
        </Field>
        <Field label="目标" htmlFor="editor-target">
          <div className="inline-controls">
            <select
              id="editor-target"
              value={draft.target.item_type}
              onChange={(event) =>
                applySemantic(
                  updateTarget(draft, {
                    item_type: event.target.value as DraftState["target"]["item_type"],
                  }),
                )
              }
            >
              {ITEM_TYPES.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
            <input
              type="number"
              min={1}
              aria-label="目标数量"
              value={draft.target.quantity}
              onChange={(event) =>
                applySemantic(
                  updateTarget(draft, {
                    quantity: Math.max(1, Number(event.target.value) || 1),
                  }),
                )
              }
            />
          </div>
        </Field>
        <Field label="场景" htmlFor="editor-scene">
          <input id="editor-scene" value={draft.sceneId} readOnly />
        </Field>
        <span className="muted editor-meta-summary">
          节点 {draft.nodes.length} · 连线 {draft.edges.length} · 投料{" "}
          {draft.inputs.length}
        </span>
      </div>

      {localError ? <InlineError message={localError} /> : null}
      {saveError ? <InlineError message={saveError} /> : null}
      {previewError ? <InlineError message={previewError} /> : null}
      {preview && !previewMatches ? (
        <InlineNotice tone="warning">
          草稿在预览后已修改；请重新预览后再保存。
        </InlineNotice>
      ) : null}

      <div className="editor-layout">
        <TemplatePalette templates={templates} onAdd={addTemplateNode} />
        <OrderCanvas
          draft={draft}
          templates={templates}
          diagnostics={allDiagnostics}
          selected={selection}
          onSelect={setSelection}
          onConnect={connect}
          onMoveNode={setPositions}
        />
        <Inspector
          draft={draft}
          templates={templates}
          selection={selection}
          onChange={applySemantic}
          onSelect={setSelection}
        />
      </div>

      <section className="panel editor-lists">
        <SectionTitle
          title="键盘与列表视图"
          description="画布之外的等价操作；所有错误也可在此定位。"
        />
        <NodeEdgeLists
          draft={draft}
          diagnostics={allDiagnostics}
          onSelect={setSelection}
        />
      </section>

      <section className="panel">
        <SectionTitle
          title="校验摘要"
          description="本地检查只改善编辑体验；服务端 preview 是权威结论。"
        />
        <div className="diagnostics-columns">
          <div>
            <h3>本地即时检查 ({localIssues.length})</h3>
            <DiagnosticsPanel
              diagnostics={localIssues}
              onSelect={selectDiagnostic}
              emptyLabel="本地未发现问题。"
            />
          </div>
          <div>
            <h3>服务端预览 ({preview?.diagnostics.length ?? 0})</h3>
            <DiagnosticsPanel
              diagnostics={preview?.diagnostics ?? []}
              onSelect={selectDiagnostic}
              emptyLabel={
                preview
                  ? "服务端没有诊断。"
                  : "点击“服务端预览”获取权威校验结果。"
              }
            />
            {preview ? (
              <p className="muted mono">digest {preview.digest}</p>
            ) : null}
          </div>
        </div>
      </section>
    </div>
  );
}
