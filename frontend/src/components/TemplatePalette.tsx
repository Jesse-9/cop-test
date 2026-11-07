import { useMemo, useState } from "react";
import type { TemplateSummary } from "../api/types";

const EXECUTOR_LABELS: Record<string, string> = {
  arm: "机械臂工序",
  agv: "AGV 运输",
  logic: "逻辑合流",
};

function portSummary(template: TemplateSummary): string {
  const inputs = template.inputs
    .map((port) => `${port.zone}/${port.item_type}`)
    .join(" + ");
  const output = template.output
    ? `${template.output.zone}/${template.output.item_type}`
    : "无输出";
  return `${inputs || "无输入"} → ${output}`;
}

export function TemplatePalette({
  templates,
  onAdd,
}: {
  templates: TemplateSummary[];
  onAdd: (templateId: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [executor, setExecutor] = useState("all");
  const groups = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const filtered = templates.filter((template) => {
      if (executor !== "all" && template.executor_kind !== executor) {
        return false;
      }
      if (!needle) return true;
      return [
        template.template_id,
        template.kind,
        template.semantics,
        template.capability,
        ...template.inputs.map((port) => port.item_type),
      ]
        .join(" ")
        .toLowerCase()
        .includes(needle);
    });
    const byKind = new Map<string, TemplateSummary[]>();
    for (const template of filtered) {
      const key = template.kind;
      const rows = byKind.get(key) ?? [];
      rows.push(template);
      byKind.set(key, rows);
    }
    return [...byKind.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [templates, query, executor]);

  return (
    <section className="palette" aria-labelledby="palette-title">
      <div className="palette-head">
        <h2 id="palette-title">工序模板</h2>
        <span className="muted">{templates.length} 个可编排模板</span>
      </div>
      <div className="palette-filters">
        <label className="field field-compact">
          <span className="field-label">搜索模板</span>
          <input
            type="search"
            value={query}
            placeholder="模板 ID、语义、物料…"
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <label className="field field-compact">
          <span className="field-label">执行器</span>
          <select
            value={executor}
            onChange={(event) => setExecutor(event.target.value)}
          >
            <option value="all">全部</option>
            <option value="arm">机械臂</option>
            <option value="agv">AGV</option>
            <option value="logic">逻辑合流</option>
          </select>
        </label>
      </div>
      {groups.length === 0 ? (
        <p className="muted palette-empty">没有匹配的模板。</p>
      ) : (
        <div className="palette-groups">
          {groups.map(([kind, rows]) => (
            <div className="palette-group" key={kind}>
              <h3>
                {EXECUTOR_LABELS[rows[0]?.executor_kind] ?? kind}{" "}
                <span className="mono">{kind}</span>
              </h3>
              {rows.map((template) => (
                <article className="template-card" key={template.template_id}>
                  <div className="template-card-head">
                    <strong>{template.semantics}</strong>
                    <span className="mono template-id">
                      {template.template_id}
                    </span>
                  </div>
                  <p className="template-ports mono">{portSummary(template)}</p>
                  <p className="template-meta">
                    <span>能力 {template.capability}</span>
                    <span>设备 {template.candidate_devices.join(", ")}</span>
                  </p>
                  <button
                    type="button"
                    className="button button-secondary button-small"
                    onClick={() => onAdd(template.template_id)}
                    aria-label={`添加模板 ${template.template_id}`}
                  >
                    添加节点
                  </button>
                </article>
              ))}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
