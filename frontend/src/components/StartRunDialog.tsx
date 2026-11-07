import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { Field, InlineError } from "./ui";

export function StartRunDialog({
  orderId,
  version,
  enabled,
  onClose,
}: {
  orderId: string;
  version: number;
  enabled: boolean;
  onClose?: () => void;
}) {
  const navigate = useNavigate();
  const [artifact, setArtifact] = useState("current");
  const [maxSimTime, setMaxSimTime] = useState(1200);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const start = async () => {
    if (!artifact.trim()) {
      setError("必须指定场景工件引用");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const payload = await api.start(orderId, version, {
        artifact_id: artifact.trim(),
        max_sim_time: maxSimTime,
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
          hint="可填具体 artifact ID；current 由服务端解析。"
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
      </div>
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
