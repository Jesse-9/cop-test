import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type {
  ItemType,
  PlannerInputPayload,
  PlannerResult,
  SceneProfile,
  TemplateCatalog,
  TransportFleet,
} from "../api/types";
import {
  Badge,
  EmptyState,
  Field,
  InlineError,
  InlineNotice,
  LoadingState,
  SectionTitle,
} from "../components/ui";
import { shortDigest } from "../domain/status";

interface SupplyRow {
  item_type: ItemType;
  quantity: number;
  zone: string;
}

/**
 * 目标对应的推荐投料只是编辑器便利, 不是能力假设: 每一项都会先按
 * 目录 `item_types.inputs` 和可生成来料区域过滤。
 */
const TARGET_SUPPLIES: Record<string, string[]> = {
  FINAL: ["RAW_A", "RAW_D"],
  COMPRESSOR_ASSY: ["COMPRESSOR", "BASEPLATE"],
  FLANGE_ASSY: ["FRONT_FLANGE", "REAR_FLANGE"],
};

function zoneForItem(itemType: string, catalog: TemplateCatalog): string {
  const zone = catalog.zones.find(
    (row) => row.spawnable && row.item_types.includes(itemType),
  );
  return zone?.id ?? "";
}

function suggestedSupplies(
  target: string,
  catalog: TemplateCatalog,
): SupplyRow[] {
  const allowed = new Set<string>(catalog.item_types.inputs);
  const rows = (TARGET_SUPPLIES[target] ?? [])
    .filter((item) => allowed.has(item))
    .map((item) => ({
      item_type: item as ItemType,
      quantity: 1,
      zone: zoneForItem(item, catalog),
    }));
  if (rows.length > 0) return rows;
  const fallback = catalog.item_types.inputs[0];
  return fallback
    ? [{ item_type: fallback, quantity: 1, zone: zoneForItem(fallback, catalog) }]
    : [];
}

export function PlannerPage() {
  const navigate = useNavigate();
  const [catalog, setCatalog] = useState<TemplateCatalog | null>(null);
  const [catalogError, setCatalogError] = useState("");
  const [loading, setLoading] = useState(true);
  const [artifactInput, setArtifactInput] = useState("");
  const [artifact, setArtifact] = useState("");
  const [catalogBusy, setCatalogBusy] = useState(false);
  const [plannerProfile, setPlannerProfile] =
    useState<SceneProfile>("full_flow");

  const [targetItem, setTargetItem] = useState<ItemType>("FINAL");
  const [targetQuantity, setTargetQuantity] = useState(1);
  const [supplies, setSupplies] = useState<SupplyRow[]>([
    { item_type: "RAW_A", quantity: 1, zone: "" },
    { item_type: "RAW_D", quantity: 1, zone: "" },
  ]);
  const [allowedTemplates, setAllowedTemplates] = useState<string[]>([]);
  const [allowedDevices, setAllowedDevices] = useState<string[]>([]);
  const [preferredRoutes, setPreferredRoutes] = useState<string[]>([]);
  const [maxCandidates, setMaxCandidates] = useState(64);
  const [maxDepth, setMaxDepth] = useState(12);
  const [maxNodes, setMaxNodes] = useState(64);
  const [allowHighRisk, setAllowHighRisk] = useState(false);
  const [transportFleet, setTransportFleet] = useState<TransportFleet>("single");
  const [strictFleet, setStrictFleet] = useState(false);

  const [result, setResult] = useState<PlannerResult | null>(null);
  const [requestError, setRequestError] = useState("");
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .templates()
      .then((payload) => {
        if (!cancelled) setCatalog(payload);
      })
      .catch((exc) => {
        if (!cancelled) {
          setCatalogError(exc instanceof ApiError ? exc.message : String(exc));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!catalog) return;
    const targets = catalog.item_types.targets;
    const nextTarget = targets.includes(targetItem)
      ? targetItem
      : targets[0];
    if (nextTarget && nextTarget !== targetItem) {
      setTargetItem(nextTarget);
      setSupplies(suggestedSupplies(nextTarget, catalog));
      setResult(null);
      return;
    }
    const allowed = new Set<string>(catalog.item_types.inputs);
    setSupplies((rows) => {
      const kept = rows.filter((row) => allowed.has(row.item_type));
      return kept.length > 0
        ? kept
        : suggestedSupplies(nextTarget ?? targetItem, catalog);
    });
  }, [catalog]);

  const deviceOptions = useMemo(() => {
    const values = new Set<string>();
    for (const template of catalog?.templates ?? []) {
      for (const device of template.candidate_devices) values.add(device);
    }
    return [...values].sort();
  }, [catalog]);

  const effectiveProfile: SceneProfile =
    (catalog?.scene_profile as SceneProfile | "") || plannerProfile;

  const payload = (): PlannerInputPayload => ({
    target_item_type: targetItem,
    target_quantity: targetQuantity,
    inputs: supplies.map((supply) => ({
      item_type: supply.item_type,
      quantity: supply.quantity,
      zone: supply.zone || undefined,
    })),
    scene_id: catalog?.scene_id ?? "L5_UFLOW",
    scene_profile: effectiveProfile,
    allowed_template_ids: allowedTemplates,
    allowed_device_ids: allowedDevices,
    preferred_route_ids: preferredRoutes,
    allow_high_risk: allowHighRisk,
    max_candidates: maxCandidates,
    max_depth: maxDepth,
    max_nodes: maxNodes,
    transport_fleet: transportFleet,
    strict_fleet: strictFleet,
  });

  const generate = async () => {
    setBusy(true);
    setRequestError("");
    setSaveError("");
    try {
      const response = await api.plan(payload());
      setResult(response.plan);
    } catch (exc) {
      setResult(null);
      setRequestError(exc instanceof ApiError ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const saveDraft = async () => {
    if (!result?.ok) return;
    setSaving(true);
    setSaveError("");
    try {
      const response = await api.createPlannerDraft(payload());
      const saved = response.draft.saved_order;
      navigate(`/orders/${encodeURIComponent(saved.order_id)}/edit`);
    } catch (exc) {
      setSaveError(exc instanceof ApiError ? exc.message : String(exc));
    } finally {
      setSaving(false);
    }
  };

  const updateSupply = (index: number, changes: Partial<SupplyRow>) => {
    setSupplies((rows) =>
      rows.map((row, rowIndex) => {
        if (rowIndex !== index) return row;
        const next = { ...row, ...changes };
        if (!catalog) return next;
        const zone = catalog.zones.find((item) => item.id === next.zone);
        if (zone && zone.item_types.length > 0) {
          if (!zone.item_types.includes(next.item_type)) {
            const allowed = catalog.item_types.inputs.filter((item) =>
              zone.item_types.includes(item),
            );
            if (allowed.length > 0) next.item_type = allowed[0];
          }
        }
        return next;
      }),
    );
  };

  const changeTarget = (itemType: ItemType) => {
    setTargetItem(itemType);
    setResult(null);
    if (catalog) {
      setSupplies(suggestedSupplies(itemType, catalog));
    }
  };

  const reloadCatalog = async () => {
    setCatalogBusy(true);
    setCatalogError("");
    try {
      const payload = await api.templates(artifactInput.trim() || undefined);
      setCatalog(payload);
      setArtifact(artifactInput.trim());
      setResult(null);
    } catch (exc) {
      setCatalogError(exc instanceof ApiError ? exc.message : String(exc));
    } finally {
      setCatalogBusy(false);
    }
  };

  if (loading) return <LoadingState label="加载模板目录…" />;
  if (catalogError) {
    return (
      <div className="page">
        <InlineError message={catalogError} />
      </div>
    );
  }

  return (
    <div className="page planner-page">
      <SectionTitle
        title="自主规划订单"
        description="planner 只读静态目录和投料约束，生成普通订单草稿；物理可行性仍由运行验收决定。"
      />
      <div className="editor-meta">
        <Field label="场景工件" htmlFor="planner-artifact">
          <div className="inline-controls">
            <input
              id="planner-artifact"
              value={artifactInput}
              onChange={(event) => setArtifactInput(event.target.value)}
              placeholder="current 或具体 artifact_id"
            />
            <button
              type="button"
              className="button button-quiet button-small"
              onClick={() => void reloadCatalog()}
              disabled={catalogBusy}
            >
              {catalogBusy ? "加载中…" : "加载能力"}
            </button>
          </div>
        </Field>
        <Field label="场景 profile" htmlFor="planner-profile">
          {catalog?.profile_source === "unbound" ? (
            <select
              id="planner-profile"
              value={plannerProfile}
              onChange={(event) =>
                setPlannerProfile(event.target.value as SceneProfile)
              }
            >
              {(catalog?.profiles ?? ["full_flow", "assembly_mvp"]).map(
                (profile) => (
                  <option key={profile} value={profile}>
                    {profile}
                  </option>
                ),
              )}
            </select>
          ) : (
            <input
              id="planner-profile"
              readOnly
              value={catalog?.scene_profile || "unbound"}
            />
          )}
        </Field>
        <span className="muted editor-meta-summary">
          工件 {catalog?.artifact_id || "未绑定"} · profile 来源{" "}
          {catalog?.profile_source ?? "加载中"}
        </span>
      </div>
      {catalog?.profile_source === "unbound" ? (
        <InlineNotice tone="warning">
          当前服务未绑定场景工件；请显式选择规划 profile，规划结果仍需在
          匹配 profile 的工件上启动。
        </InlineNotice>
      ) : null}
      {artifactInput.trim() !== artifact.trim() ? (
        <InlineNotice tone="warning">
          场景工件输入已修改；点击“加载能力”后才会按新工件过滤目标和投料。
        </InlineNotice>
      ) : null}
      <div className="planner-layout">
        <section className="panel planner-form">
          <SectionTitle title="目标与约束" />
          <div className="form-grid form-grid-2">
            <Field label="目标物料" htmlFor="planner-target">
              <select
                id="planner-target"
                value={targetItem}
                onChange={(event) =>
                  changeTarget(event.target.value as ItemType)
                }
              >
                {catalog?.item_types.targets.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="目标数量" htmlFor="planner-quantity">
              <input
                id="planner-quantity"
                type="number"
                min={1}
                value={targetQuantity}
                onChange={(event) =>
                  setTargetQuantity(
                    Math.max(1, Number(event.target.value) || 1),
                  )
                }
              />
            </Field>
            <Field
              label="规划车队假设"
              htmlFor="planner-fleet"
              hint="仅影响候选可行性与解释，不写入订单 DAG；启动时仍单独选择运行模式。"
            >
              <select
                id="planner-fleet"
                value={transportFleet}
                onChange={(event) =>
                  setTransportFleet(event.target.value as TransportFleet)
                }
              >
                <option value="single">单车（agv_1）</option>
                <option value="dual">双车（agv_1 + cart_2）</option>
              </select>
            </Field>
          </div>
          <h3 className="subhead">可用投料</h3>
          <div className="supply-list">
            {supplies.map((supply, index) => (
              <div className="supply-row" key={`supply-${index}`}>
                <Field label="物料" htmlFor={`supply-type-${index}`}>
                  <select
                    id={`supply-type-${index}`}
                    value={supply.item_type}
                    onChange={(event) =>
                      updateSupply(index, {
                        item_type: event.target.value as ItemType,
                      })
                    }
                  >
                    {catalog?.item_types.inputs.map((item) => (
                      <option key={item} value={item}>
                        {item}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="数量" htmlFor={`supply-qty-${index}`}>
                  <input
                    id={`supply-qty-${index}`}
                    type="number"
                    min={1}
                    value={supply.quantity}
                    onChange={(event) =>
                      updateSupply(index, {
                        quantity: Math.max(
                          1,
                          Number(event.target.value) || 1,
                        ),
                      })
                    }
                  />
                </Field>
                <Field label="区域（可选）" htmlFor={`supply-zone-${index}`}>
                  <select
                    id={`supply-zone-${index}`}
                    value={supply.zone}
                    onChange={(event) =>
                      updateSupply(index, { zone: event.target.value })
                    }
                  >
                    <option value="">任意入口</option>
                    {(catalog?.zones ?? [])
                      .filter((zone) => zone.spawnable)
                      .map((zone) => (
                        <option key={zone.id} value={zone.id}>
                          {zone.id} · {zone.item_types.join("/") || "任意"}
                        </option>
                      ))}
                  </select>
                </Field>
                <button
                  type="button"
                  className="button button-quiet button-small"
                  onClick={() =>
                    setSupplies((rows) =>
                      rows.filter((_, rowIndex) => rowIndex !== index),
                    )
                  }
                >
                  删除
                </button>
              </div>
            ))}
          </div>
          <button
            type="button"
            className="button button-secondary"
            onClick={() => {
              const item = catalog?.item_types.inputs[0] ?? "RAW_A";
              setSupplies((rows) => [
                ...rows,
                {
                  item_type: item,
                  quantity: 1,
                  zone: catalog ? zoneForItem(item, catalog) : "",
                },
              ]);
            }}
          >
            添加投料
          </button>
          <details className="advanced-constraints">
            <summary>高级约束与搜索上限</summary>
            <div className="form-grid form-grid-2">
              <Field label="允许模板（多选）" htmlFor="allowed-templates">
                <select
                  id="allowed-templates"
                  multiple
                  size={6}
                  value={allowedTemplates}
                  onChange={(event) =>
                    setAllowedTemplates(
                      [...event.target.selectedOptions].map(
                        (option) => option.value,
                      ),
                    )
                  }
                >
                  {(catalog?.templates ?? []).map((template) => (
                    <option key={template.template_id} value={template.template_id}>
                      {template.template_id}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="允许设备（多选）" htmlFor="allowed-devices">
                <select
                  id="allowed-devices"
                  multiple
                  size={6}
                  value={allowedDevices}
                  onChange={(event) =>
                    setAllowedDevices(
                      [...event.target.selectedOptions].map(
                        (option) => option.value,
                      ),
                    )
                  }
                >
                  {deviceOptions.map((device) => (
                    <option key={device} value={device}>
                      {device}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="路线偏好（多选）" htmlFor="preferred-routes">
                <select
                  id="preferred-routes"
                  multiple
                  size={5}
                  value={preferredRoutes}
                  onChange={(event) =>
                    setPreferredRoutes(
                      [...event.target.selectedOptions].map(
                        (option) => option.value,
                      ),
                    )
                  }
                >
                  {(catalog?.routes ?? []).map((route) => (
                    <option key={route.route_id} value={route.route_id}>
                      {route.route_id} · {route.semantics}
                    </option>
                  ))}
                </select>
              </Field>
              <div className="stack">
                <Field label="最大候选数" htmlFor="max-candidates">
                  <input
                    id="max-candidates"
                    type="number"
                    min={1}
                    value={maxCandidates}
                    onChange={(event) =>
                      setMaxCandidates(
                        Math.max(1, Number(event.target.value) || 1),
                      )
                    }
                  />
                </Field>
                <Field label="最大深度" htmlFor="max-depth">
                  <input
                    id="max-depth"
                    type="number"
                    min={0}
                    value={maxDepth}
                    onChange={(event) =>
                      setMaxDepth(Math.max(0, Number(event.target.value) || 0))
                    }
                  />
                </Field>
                <Field label="最大节点数" htmlFor="max-nodes">
                  <input
                    id="max-nodes"
                    type="number"
                    min={1}
                    value={maxNodes}
                    onChange={(event) =>
                      setMaxNodes(
                        Math.max(1, Number(event.target.value) || 1),
                      )
                    }
                  />
                </Field>
                <label className="checkbox-row">
                  <input
                    type="checkbox"
                    checked={allowHighRisk}
                    onChange={(event) => setAllowHighRisk(event.target.checked)}
                  />
                  允许高风险候选（首期目录无风险元数据）
                </label>
                <label className="checkbox-row">
                  <input
                    type="checkbox"
                    checked={strictFleet}
                    onChange={(event) => setStrictFleet(event.target.checked)}
                  />
                  车队假设不满足的路线直接拒绝（默认仅降权）
                </label>
              </div>
            </div>
          </details>
          {requestError ? <InlineError message={requestError} /> : null}
          {saveError ? <InlineError message={saveError} /> : null}
          <div className="button-row">
            <button type="button" className="button" onClick={() => void generate()} disabled={busy}>
              {busy ? "生成中…" : "生成候选"}
            </button>
            {result?.ok ? (
              <button
                type="button"
                className="button button-secondary"
                onClick={() => void saveDraft()}
                disabled={saving}
              >
                {saving ? "保存中…" : "保存草稿并编辑"}
              </button>
            ) : null}
          </div>
        </section>

        <section className="panel planner-result" aria-live="polite">
          {!result ? (
            <EmptyState
              title="尚未生成候选"
              description="填写目标和投料后生成；无解时会保留输入并展示后端诊断。"
            />
          ) : (
            <>
              <SectionTitle
                title={result.ok ? "找到可行候选" : "没有可行解"}
                description={`策略 ${result.policy_version} · profile ${
                  result.scene_profile || "unbound"
                } · 最先阻断类别 ${result.first_blocking_category || "无"}`}
                actions={
                  <Badge tone={result.ok ? "success" : "failure"}>
                    {result.ok ? "可保存" : "需修改约束"}
                  </Badge>
                }
              />
              {result.diagnostics.length > 0 ? (
                <ul className="diagnostic-list" aria-label="planner 诊断">
                  {result.diagnostics.map((diagnostic, index) => (
                    <li key={`${diagnostic.code}-${index}`}>
                      <div className={`diagnostic-item static-${diagnostic.severity}`}>
                        <span className="mono diagnostic-code">{diagnostic.code}</span>
                        <span>{diagnostic.message}</span>
                        <span className="mono diagnostic-locator">
                          {diagnostic.category}
                        </span>
                      </div>
                    </li>
                  ))}
                </ul>
              ) : null}
              {result.selected ? (
                <div className="planner-selected">
                  <h3>选中候选</h3>
                  <p className="mono">
                    {result.selected.branch.route_ids.length > 0
                      ? result.selected.branch.route_ids.join(" → ")
                      : result.selected.branch.template_ids.join(" → ")}
                  </p>
                  <div className="preview-facts">
                    <Badge tone="accent">
                      {result.selected.branch.operation_count} 道工序
                    </Badge>
                    <span className="mono">digest {shortDigest(result.digest)}</span>
                    <span>
                      投料{" "}
                      {result.selected.input_usage
                        .map(
                          (row) =>
                            `${row.item_type} ${row.required}/${row.available}`,
                        )
                        .join("，")}
                    </span>
                  </div>
                  {result.explanation.fleet_context ? (
                    <div className="preview-facts">
                      <Badge tone="accent">
                        规划车队 {result.explanation.fleet_context.mode}
                      </Badge>
                      <span className="mono">
                        策略 {result.explanation.fleet_context.policy_version}
                      </span>
                      <span>
                        {Object.entries(
                          result.explanation.fleet_context.assignment,
                        ).length > 0
                          ? Object.entries(
                              result.explanation.fleet_context.assignment,
                            )
                              .map(([kind, vehicle]) => `${kind}→${vehicle}`)
                              .join("，")
                          : "全运输走 agv_1"}
                      </span>
                      {result.explanation.fleet_context.penalized ? (
                        <Badge tone="warning">车队假设降权</Badge>
                      ) : null}
                    </div>
                  ) : null}
                  <h4>join 绑定</h4>
                  {result.explanation.join_bindings.length === 0 ? (
                    <p className="muted">该候选不包含逻辑合流。</p>
                  ) : (
                    <ul className="binding-list">
                      {result.explanation.join_bindings.map((binding) => (
                        <li key={binding.join_template_id}>
                          <span className="mono">{binding.join_template_id}</span>
                          <span>
                            {binding.inputs
                              .map(
                                (input) =>
                                  input.source === "input"
                                    ? `${input.port_id}: ${input.item_type} ← 直接投料@${input.zone}`
                                    : `${input.port_id}: ${input.item_type} ← ${input.route_ids.join(
                                        "+",
                                      )}`,
                              )
                              .join("；")}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              ) : null}
              <h3>候选与拒绝原因 ({result.candidates.length + result.rejected.length})</h3>
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>候选</th>
                      <th>路线</th>
                      <th>工序</th>
                      <th>状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...result.candidates, ...result.rejected].map((candidate) => (
                      <tr key={candidate.candidate_id}>
                        <td className="mono">{candidate.candidate_id}</td>
                        <td className="mono">{candidate.route_ids.join(" → ")}</td>
                        <td>{candidate.operation_count}</td>
                        <td>
                          {candidate.feasible ? (
                            <Badge tone="success">可行</Badge>
                          ) : (
                            <span>
                              <Badge tone="failure">
                                {candidate.rejection_code || "拒绝"}
                              </Badge>{" "}
                              <span className="muted">
                                {candidate.rejection_reason}
                              </span>
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <details className="explain-block">
                <summary>搜索统计与未建模维度</summary>
                <pre className="json-block">
                  {JSON.stringify(
                    {
                      search: result.search,
                      unmodeled_dimensions:
                        result.explanation.unmodeled_dimensions,
                    },
                    null,
                    2,
                  )}
                </pre>
              </details>
              {result.ok ? (
                <InlineNotice tone="success">
                  planner 只给出静态可行草稿；保存后仍需服务端 validator/compiler
                  预览和真实运行验收。
                </InlineNotice>
              ) : (
                <InlineNotice tone="warning">
                  无解原因来自服务端 planner；调整目标、投料或允许范围后重试。
                </InlineNotice>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
}
