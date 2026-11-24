import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type {
  FleetStatus,
  PathReuseMode,
  TransportFleet,
} from "../api/types";
import { Field, InlineError, InlineNotice } from "./ui";

export function StartRunDialog({
  orderId,
  version,
  enabled,
  onClose,
  artifact: artifactProp,
  onArtifactChange,
}: {
  orderId: string;
  version: number;
  enabled: boolean;
  onClose?: () => void;
  artifact?: string;
  onArtifactChange?: (value: string) => void;
}) {
  const navigate = useNavigate();
  const [artifactInternal, setArtifactInternal] = useState("current");
  const artifact = artifactProp ?? artifactInternal;
  const setArtifact = onArtifactChange ?? setArtifactInternal;
  const [maxSimTime, setMaxSimTime] = useState(1200);
  const [fleetMode, setFleetMode] = useState<TransportFleet>("single");
  const [pathReuse, setPathReuse] =
    useState<PathReuseMode>("CACHE_THEN_PLAN");
  const [fleetInfo, setFleetInfo] = useState<FleetStatus | null>(null);
  const [fleetError, setFleetError] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      api
        .fleet(artifact.trim() || undefined)
        .then((payload) => {
          if (cancelled) return;
          setFleetInfo(payload.fleet);
          setFleetError("");
          setFleetMode((current) =>
            payload.fleet.dual.available ? current : "single",
          );
        })
        .catch((exc) => {
          if (cancelled) return;
          setFleetInfo(null);
          setFleetError(exc instanceof ApiError ? exc.message : String(exc));
        });
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [artifact]);

  const dualAvailable = fleetInfo?.dual.available === true;
  const dualReason =
    fleetError ||
    fleetInfo?.dual.reason ||
    (fleetInfo ? "" : "正在检查场景工件的双车能力…");

  const start = async () => {
    if (fleetMode === "dual" && !dualAvailable) {
      setError(dualReason || "当前工件不支持双车运行");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const payload = await api.start(orderId, version, {
        artifact_id: artifact.trim(),
        max_sim_time: maxSimTime,
        transport_fleet: fleetMode,
        path_reuse: pathReuse,
      });
      navigate(`/runs/${encodeURIComponent(payload.run.run_id)}`);
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="start-panel" aria-labelledby="start-title">
      <div className="start-panel-head">
        <div>
          <h2 id="start-title">启动订单 v{version}</h2>
          <p className="muted">
            启动请求绑定不可变版本、目录版本和具体场景工件；启动后画布只读。
          </p>
        </div>
        {onClose ? (
          <button type="button" className="button button-quiet" onClick={onClose}>
            收起
          </button>
        ) : null}
      </div>
      <div className="form-grid form-grid-2">
        <Field
          label="场景工件"
          htmlFor="start-artifact"
          hint="留空使用服务默认工件；也可填具体 artifact ID 或 current。"
        >
          <input
            id="start-artifact"
            value={artifact}
            onChange={(event) => setArtifact(event.target.value)}
          />
        </Field>
        <Field label="最大仿真时间 (s)" htmlFor="start-max-time">
          <input
            id="start-max-time"
            type="number"
            min={1}
            value={maxSimTime}
            onChange={(event) =>
              setMaxSimTime(Math.max(1, Number(event.target.value) || 1))
            }
          />
        </Field>
        <Field
          label="车队模式"
          htmlFor="start-fleet"
          hint="运行选项，不写入订单版本或 DAG；双车需工件包含 Cart2。"
        >
          <div
            id="start-fleet"
            className="fleet-switch"
            role="radiogroup"
            aria-label="车队模式"
          >
            <button
              type="button"
              className={
                fleetMode === "single"
                  ? "button button-small"
                  : "button button-small button-secondary"
              }
              role="radio"
              aria-checked={fleetMode === "single"}
              disabled={busy}
              onClick={() => setFleetMode("single")}
            >
              单车
            </button>
            <button
              type="button"
              className={
                fleetMode === "dual"
                  ? "button button-small"
                  : "button button-small button-secondary"
              }
              role="radio"
              aria-checked={fleetMode === "dual"}
              disabled={busy || !dualAvailable}
              title={dualAvailable ? "" : dualReason}
              onClick={() => setFleetMode("dual")}
            >
              双车
            </button>
          </div>
        </Field>
        <Field
          label="路径复用"
          htmlFor="start-path-reuse"
          hint="仅作用于五臂 PLACING；CACHE_THEN_PLAN 未命中自动在线规划。v005 已有记录，v002/MVP 需先 PLAN_ONLY 生成。"
        >
          <select
            id="start-path-reuse"
            value={pathReuse}
            disabled={busy}
            onChange={(event) =>
              setPathReuse(event.target.value as PathReuseMode)
            }
          >
            <option value="CACHE_THEN_PLAN">缓存优先（推荐）</option>
            <option value="off">关闭</option>
            <option value="CACHE_ONLY">仅缓存（缺记录会失败）</option>
            <option value="PLAN_ONLY">只规划并记录（生成缓存）</option>
          </select>
        </Field>
      </div>
      {!dualAvailable && dualReason ? (
        <InlineNotice tone="warning">
          双车不可用：{dualReason}
        </InlineNotice>
      ) : null}
      {fleetInfo ? (
        <p className="muted">
          车队策略 {fleetInfo.policy_version} · 默认 {fleetInfo.default}
          {fleetInfo.artifact_id ? ` · 工件 ${fleetInfo.artifact_id}` : ""}
        </p>
      ) : null}
      {error ? <InlineError message={error} /> : null}
      <div className="button-row">
        <button
          type="button"
          className="button"
          disabled={!enabled || busy}
          onClick={() => void start()}
        >
          {busy ? "启动中…" : "确认启动"}
        </button>
        {!enabled ? (
          <span className="muted">先通过服务端编译预览才能启动。</span>
        ) : null}
      </div>
    </section>
  );
}
