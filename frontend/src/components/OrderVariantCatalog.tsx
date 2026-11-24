import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type {
  OrderVariantCatalog,
  OrderVariantScope,
  OrderVariantSummary,
  OrderVariantVerification,
} from "../api/types";
import {
  Badge,
  Field,
  InlineError,
  InlineNotice,
  LoadingState,
  SectionTitle,
} from "./ui";

const SCOPE_LABELS: Record<OrderVariantScope, string> = {
  production: "生产",
  intermediate: "中间件",
  transit: "运输演示",
};

const SCOPE_TONES: Record<
  OrderVariantScope,
  "success" | "accent" | "warning"
> = {
  production: "success",
  intermediate: "accent",
  transit: "warning",
};

const VERIFICATION_LABELS: Record<OrderVariantVerification, string> = {
  real_run: "实机验证",
  planner_test: "planner 测试",
  compiler_only: "仅编译验证",
};

const VERIFICATION_TONES: Record<
  OrderVariantVerification,
  "success" | "accent" | "neutral"
> = {
  real_run: "success",
  planner_test: "accent",
  compiler_only: "neutral",
};

function transportLabel(templateId: string): string {
  if (templateId === "transport_BA") return "B→A";
  if (templateId === "transport_DE") return "D→E";
  return templateId;
}

function defaultOrderId(variant: OrderVariantSummary): string {
  const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, "");
  return `variant-${variant.variant_id}-${stamp}`.slice(0, 64);
}

function ArmChips({ arms }: { arms: string[] }) {
  if (arms.length === 0) {
    return <span className="muted">无机械臂（逻辑装配）</span>;
  }
  return (
    <span className="variant-chips">
      {arms.map((arm) => (
        <Badge key={arm} tone="neutral">
          {arm} 臂
        </Badge>
      ))}
    </span>
  );
}

function TransportChips({ transports }: { transports: string[] }) {
  if (transports.length === 0) {
    return <span className="muted">无 AGV 运输</span>;
  }
  return (
    <span className="variant-chips">
      {transports.map((transport) => (
        <Badge key={transport} tone="accent">
          {transportLabel(transport)}
        </Badge>
      ))}
    </span>
  );
}

function FleetHint({ variant }: { variant: OrderVariantSummary }) {
  const fleet = variant.recommended_transport_fleet;
  if (!fleet) return null;
  const text =
    fleet === "dual"
      ? "建议双车（agv_1 + cart_2；启动时可改）"
      : "建议单车（agv_1；启动时可改）";
  return <span className="muted">{text}</span>;
}

function VariantCard({
  variant,
  onUse,
}: {
  variant: OrderVariantSummary;
  onUse: (variant: OrderVariantSummary) => void;
}) {
  return (
    <article className="template-card variant-card">
      <div className="template-card-head">
        <strong>{variant.display_name}</strong>
        <span className="muted">{variant.description}</span>
      </div>
      <div className="variant-chips">
        <Badge tone={SCOPE_TONES[variant.scope]}>
          {SCOPE_LABELS[variant.scope]}
        </Badge>
        <Badge tone={VERIFICATION_TONES[variant.verification]}>
          {VERIFICATION_LABELS[variant.verification]}
        </Badge>
        <Badge tone="neutral">{variant.scene_profile}</Badge>
        {!variant.available ? (
          <Badge tone="failure">当前工件不可用</Badge>
        ) : null}
      </div>
      <p className="mono">
        目标 {variant.target.item_type} × {variant.target.quantity}
      </p>
      <p className="muted">
        投料{" "}
        {variant.inputs
          .map((input) => `${input.item_type}@${input.zone}`)
          .join(" + ") || "无"}
      </p>
      <p className="variant-line">
        机械臂 <ArmChips arms={variant.used_arms} />
      </p>
      <p className="variant-line">
        运输 <TransportChips transports={variant.used_transports} />
      </p>
      {variant.join_template_id ? (
        <p className="muted mono">join {variant.join_template_id}</p>
      ) : null}
      {variant.difference_summary ? (
        <p className="variant-difference">
          差异：{variant.difference_summary}
        </p>
      ) : null}
      <p className="muted">
        <FleetHint variant={variant} />
      </p>
      {!variant.available && variant.unavailable_reason ? (
        <p className="inline-error">{variant.unavailable_reason}</p>
      ) : null}
      <button
        type="button"
        className="button button-secondary button-small"
        disabled={!variant.available}
        onClick={() => onUse(variant)}
      >
        使用此方案创建订单
      </button>
    </article>
  );
}

export function OrderVariantCatalog() {
  const navigate = useNavigate();
  const [catalog, setCatalog] = useState<OrderVariantCatalog | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState("all");
  const [target, setTarget] = useState("all");
  const [arm, setArm] = useState("all");
  const [transport, setTransport] = useState("all");
  const [verification, setVerification] = useState("all");

  const [selected, setSelected] = useState<OrderVariantSummary | null>(null);
  const [orderId, setOrderId] = useState("");
  const [quantity, setQuantity] = useState(1);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [createError, setCreateError] = useState("");

  const load = () => {
    setLoading(true);
    setError("");
    api
      .orderVariants()
      .then((payload) => setCatalog(payload))
      .catch((exc) => {
        setError(exc instanceof ApiError ? exc.message : String(exc));
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
  }, []);

  const targets = useMemo(
    () =>
      [...new Set((catalog?.variants ?? []).map((row) => row.target.item_type))].sort(),
    [catalog],
  );
  const arms = useMemo(
    () => [...new Set((catalog?.variants ?? []).flatMap((row) => row.used_arms))].sort(),
    [catalog],
  );
  const transports = useMemo(
    () => [
      ...new Set(
        (catalog?.variants ?? []).flatMap((row) => row.used_transports),
      ),
    ].sort(),
    [catalog],
  );

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (catalog?.variants ?? []).filter((variant) => {
      if (scope !== "all" && variant.scope !== scope) return false;
      if (target !== "all" && variant.target.item_type !== target) return false;
      if (arm !== "all" && !variant.used_arms.includes(arm)) return false;
      if (
        transport !== "all" &&
        !variant.used_transports.includes(transport)
      ) {
        return false;
      }
      if (
        verification !== "all" &&
        variant.verification !== verification
      ) {
        return false;
      }
      if (!needle) return true;
      return [
        variant.variant_id,
        variant.display_name,
        variant.description,
        variant.scope,
        variant.verification,
        ...variant.used_arms,
        ...variant.used_transports,
      ]
        .join(" ")
        .toLowerCase()
        .includes(needle);
    });
  }, [catalog, query, scope, target, arm, transport, verification]);

  const complete = filtered.filter((variant) => variant.route_complete);
  const partial = filtered.filter((variant) => !variant.route_complete);

  const useVariant = (variant: OrderVariantSummary) => {
    setSelected(variant);
    setOrderId(defaultOrderId(variant));
    setQuantity(variant.target.quantity);
    setNote("");
    setCreateError("");
  };

  const create = async () => {
    if (!selected) return;
    if (!orderId.trim()) {
      setCreateError("订单 ID 不能为空");
      return;
    }
    setBusy(true);
    setCreateError("");
    try {
      const response = await api.createOrderFromVariant(selected.variant_id, {
        order_id: orderId.trim(),
        quantity,
        note: note.trim() || undefined,
      });
      navigate(
        `/orders/${encodeURIComponent(response.saved_order.order_id)}`,
      );
    } catch (exc) {
      setCreateError(exc instanceof ApiError ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel variant-catalog" aria-labelledby="variant-title">
      <SectionTitle
        title="订单方案目录"
        description="命名包装的工艺组合；创建普通订单后仍需预览/启动，不自动运行仿真。"
        actions={
          catalog ? (
            <span className="muted mono">
              {catalog.variant_version} · {catalog.scene_profile || "unbound"}
            </span>
          ) : null
        }
      />

      {error ? <InlineError message={error} onRetry={() => load()} /> : null}
      {catalog?.profile_source === "unbound" ? (
        <InlineNotice tone="warning">
          当前服务未绑定工件 profile，显示全部方案；创建后启动仍按实际工件
          profile 校验。
        </InlineNotice>
      ) : null}
      {loading ? <LoadingState label="加载订单方案…" /> : null}

      {selected ? (
        <section className="start-panel" aria-labelledby="variant-create-title">
          <div className="start-panel-head">
            <div>
              <h2 id="variant-create-title">
                创建方案订单 · {selected.display_name}
              </h2>
              <p className="muted">
                目标 {selected.target.item_type}；机械臂{" "}
                {selected.used_arms.join("/") || "无"}；运输{" "}
                {selected.used_transports.map(transportLabel).join("/") || "无"}。
                创建后跳转订单详情，由启动对话框选择工件和 single/dual。
              </p>
            </div>
            <button
              type="button"
              className="button button-quiet"
              onClick={() => setSelected(null)}
            >
              收起
            </button>
          </div>
          <div className="form-grid form-grid-2">
            <Field label="订单 ID" htmlFor="variant-order-id">
              <input
                id="variant-order-id"
                value={orderId}
                onChange={(event) => setOrderId(event.target.value)}
              />
            </Field>
            <Field label="目标数量" htmlFor="variant-quantity">
              <input
                id="variant-quantity"
                type="number"
                min={1}
                value={quantity}
                onChange={(event) =>
                  setQuantity(Math.max(1, Number(event.target.value) || 1))
                }
              />
            </Field>
            <Field label="备注（可选）" htmlFor="variant-note">
              <input
                id="variant-note"
                value={note}
                onChange={(event) => setNote(event.target.value)}
              />
            </Field>
          </div>
          {selected.difference_summary ? (
            <InlineNotice tone="neutral">
              差异：{selected.difference_summary}
            </InlineNotice>
          ) : null}
          {createError ? <InlineError message={createError} /> : null}
          <div className="button-row">
            <button
              type="button"
              className="button"
              disabled={busy}
              onClick={() => void create()}
            >
              {busy ? "创建中…" : "创建并打开订单"}
            </button>
            <span className="muted">不会自动启动仿真。</span>
          </div>
        </section>
      ) : null}

      <div className="variant-filters">
        <Field label="搜索方案" htmlFor="variant-query">
          <input
            id="variant-query"
            type="search"
            value={query}
            placeholder="名称、ID、机械臂、运输…"
            onChange={(event) => setQuery(event.target.value)}
          />
        </Field>
        <Field label="层级" htmlFor="variant-scope">
          <select
            id="variant-scope"
            value={scope}
            onChange={(event) => setScope(event.target.value)}
          >
            <option value="all">全部</option>
            <option value="production">生产</option>
            <option value="intermediate">中间件</option>
            <option value="transit">运输演示</option>
          </select>
        </Field>
        <Field label="目标" htmlFor="variant-target">
          <select
            id="variant-target"
            value={target}
            onChange={(event) => setTarget(event.target.value)}
          >
            <option value="all">全部</option>
            {targets.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </Field>
        <Field label="机械臂" htmlFor="variant-arm">
          <select
            id="variant-arm"
            value={arm}
            onChange={(event) => setArm(event.target.value)}
          >
            <option value="all">全部</option>
            {arms.map((item) => (
              <option key={item} value={item}>
                {item} 臂
              </option>
            ))}
          </select>
        </Field>
        <Field label="运输" htmlFor="variant-transport">
          <select
            id="variant-transport"
            value={transport}
            onChange={(event) => setTransport(event.target.value)}
          >
            <option value="all">全部</option>
            {transports.map((item) => (
              <option key={item} value={item}>
                {transportLabel(item)}
              </option>
            ))}
          </select>
        </Field>
        <Field label="验证" htmlFor="variant-verification">
          <select
            id="variant-verification"
            value={verification}
            onChange={(event) => setVerification(event.target.value)}
          >
            <option value="all">全部</option>
            <option value="real_run">实机验证</option>
            <option value="planner_test">planner 测试</option>
            <option value="compiler_only">仅编译验证</option>
          </select>
        </Field>
      </div>

      {catalog ? (
        <p className="muted">
          {filtered.length} / {catalog.variants.length} 个方案
          {catalog.artifact_id ? ` · 工件 ${catalog.artifact_id}` : ""}
        </p>
      ) : null}

      {complete.length > 0 ? (
        <div className="variant-section">
          <h3>完整路线方案</h3>
          <div className="variant-grid">
            {complete.map((variant) => (
              <VariantCard
                key={variant.variant_id}
                variant={variant}
                onUse={useVariant}
              />
            ))}
          </div>
        </div>
      ) : null}

      {partial.length > 0 ? (
        <div className="variant-section">
          <h3>工序与运输变体</h3>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>方案</th>
                  <th>目标</th>
                  <th>机械臂</th>
                  <th>运输</th>
                  <th>差异</th>
                  <th>层级 / 验证</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {partial.map((variant) => (
                  <tr key={variant.variant_id}>
                    <td>
                      <strong>{variant.display_name}</strong>
                      <span className="muted mono cell-sub">
                        {variant.variant_id}
                      </span>
                    </td>
                    <td>
                      {variant.target.item_type} × {variant.target.quantity}
                    </td>
                    <td>
                      <ArmChips arms={variant.used_arms} />
                    </td>
                    <td>
                      <TransportChips transports={variant.used_transports} />
                    </td>
                    <td>{variant.difference_summary || "—"}</td>
                    <td>
                      <div className="stack-tight">
                        <Badge tone={SCOPE_TONES[variant.scope]}>
                          {SCOPE_LABELS[variant.scope]}
                        </Badge>
                        <Badge tone={VERIFICATION_TONES[variant.verification]}>
                          {VERIFICATION_LABELS[variant.verification]}
                        </Badge>
                      </div>
                    </td>
                    <td>
                      <button
                        type="button"
                        className="button button-secondary button-small"
                        disabled={!variant.available}
                        onClick={() => useVariant(variant)}
                      >
                        使用
                      </button>
                      {!variant.available ? (
                        <span className="muted cell-sub">
                          {variant.unavailable_reason}
                        </span>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      {!loading && catalog && filtered.length === 0 ? (
        <p className="muted">没有匹配的方案；调整筛选条件。</p>
      ) : null}
    </section>
  );
}