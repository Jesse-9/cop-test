import { useMemo, useState } from "react";
import type {
  Diagnostic,
  ItemType,
  OrderEdge,
  TemplateSummary,
} from "../api/types";
import { ITEM_TYPES } from "../api/types";
import {
  addInput,
  addEdge,
  edgeIssue,
  removeEdge,
  removeInput,
  removeNode,
  cloneNode,
  templateMap,
  updateInput,
  updateTarget,
  type DraftState,
} from "../domain/orderDraft";
import type { Selection } from "./OrderCanvas";
import { Badge, Field, SectionTitle } from "./ui";

export function DiagnosticsPanel({
  diagnostics,
  onSelect,
  emptyLabel = "尚未校验。",
}: {
  diagnostics: Diagnostic[];
  onSelect: (diagnostic: Diagnostic) => void;
  emptyLabel?: string;
}) {
  if (diagnostics.length === 0) {
    return <p className="muted diagnostics-empty">{emptyLabel}</p>;
  }
  return (
    <ul className="diagnostic-list" aria-label="校验诊断">
      {diagnostics.map((diagnostic, index) => (
        <li key={`${diagnostic.code}-${index}`}>
          <button
            type="button"
            className="diagnostic-item"
            onClick={() => onSelect(diagnostic)}
          >
            <span className="diagnostic-code mono">{diagnostic.code}</span>
            <span>{diagnostic.message}</span>
            <span className="diagnostic-locator mono">
              {diagnostic.node_id ||
                (diagnostic.edge_index >= 0
                  ? `edge#${diagnostic.edge_index}`
                  : diagnostic.input_id || diagnostic.port_id || "订单")}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}

function portsSummary(template: TemplateSummary): string {
  return `${template.inputs
    .map((port) => `${port.port_id}: ${port.zone}/${port.item_type}`)
    .join(" + ")} → ${
    template.output
      ? `${template.output.port_id}: ${template.output.zone}/${template.output.item_type}`
      : "无输出"
  }`;
}

function OrderSettings({
  draft,
  templates,
  onChange,
}: {
  draft: DraftState;
  templates: TemplateSummary[];
  onChange: (draft: DraftState) => void;
}) {
  const map = useMemo(() => templateMap(templates), [templates]);
  const [newInput, setNewInput] = useState({
    item_type: "RAW_A" as ItemType,
    quantity: 1,
    zone: "",
    node_id: "",
    port_id: "",
  });
  const nodeTemplate = (nodeId: string) =>
    draft.nodes.find((node) => node.node_id === nodeId);
  const portOptions = (nodeId: string): string[] => {
    const node = nodeTemplate(nodeId);
    if (!node) return [];
    return map[node.template_id]?.inputs.map((port) => port.port_id) ?? [];
  };

  return (
    <div className="stack">
      <SectionTitle title="订单目标与投料" />
      <div className="form-grid">
        <Field label="目标物料" htmlFor="target-item">
          <select
            id="target-item"
            value={draft.target.item_type}
            onChange={(event) =>
              onChange(
                updateTarget(draft, {
                  item_type: event.target.value as ItemType,
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
        </Field>
        <Field label="目标数量" htmlFor="target-quantity">
          <input
            id="target-quantity"
            type="number"
            min={1}
            value={draft.target.quantity}
            onChange={(event) =>
              onChange(
                updateTarget(draft, {
                  quantity: Math.max(1, Number(event.target.value) || 1),
                }),
              )
            }
          />
        </Field>
      </div>
      <h3 className="subhead">投料</h3>
      {draft.inputs.length === 0 ? (
        <p className="muted">尚未添加入口投料；编译器会拒绝缺少供给的入口端口。</p>
      ) : (
        <div className="input-list">
          {draft.inputs.map((input) => {
            const options = portOptions(input.node_id);
            return (
              <div className="input-row" key={input.input_id}>
                <div className="input-row-title">
                  <span className="mono">{input.input_id}</span>
                  <button
                    type="button"
                    className="button button-quiet button-small"
                    onClick={() => onChange(removeInput(draft, input.input_id))}
                  >
                    删除
                  </button>
                </div>
                <div className="form-grid form-grid-2">
                  <Field label="物料类型" htmlFor={`${input.input_id}-type`}>
                    <select
                      id={`${input.input_id}-type`}
                      value={input.item_type}
                      onChange={(event) =>
                        onChange(
                          updateInput(draft, input.input_id, {
                            item_type: event.target.value as ItemType,
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
                  </Field>
                  <Field label="数量" htmlFor={`${input.input_id}-qty`}>
                    <input
                      id={`${input.input_id}-qty`}
                      type="number"
                      min={1}
                      value={input.quantity}
                      onChange={(event) =>
                        onChange(
                          updateInput(draft, input.input_id, {
                            quantity: Math.max(
                              1,
                              Number(event.target.value) || 1,
                            ),
                          }),
                        )
                      }
                    />
                  </Field>
                  <Field label="区域" htmlFor={`${input.input_id}-zone`}>
                    <input
                      id={`${input.input_id}-zone`}
                      value={input.zone}
                      placeholder="line_s1a"
                      onChange={(event) =>
                        onChange(
                          updateInput(draft, input.input_id, {
                            zone: event.target.value,
                          }),
                        )
                      }
                    />
                  </Field>
                  <Field label="入口节点（可选）" htmlFor={`${input.input_id}-node`}>
                    <select
                      id={`${input.input_id}-node`}
                      value={input.node_id}
                      onChange={(event) =>
                        onChange(
                          updateInput(draft, input.input_id, {
                            node_id: event.target.value,
                            port_id: "",
                          }),
                        )
                      }
                    >
                      <option value="">按区域/类型自动匹配</option>
                      {draft.nodes.map((node) => (
                        <option key={node.node_id} value={node.node_id}>
                          {node.node_id} · {node.template_id}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="入口端口（可选）" htmlFor={`${input.input_id}-port`}>
                    <select
                      id={`${input.input_id}-port`}
                      value={input.port_id}
                      disabled={!input.node_id}
                      onChange={(event) =>
                        onChange(
                          updateInput(draft, input.input_id, {
                            port_id: event.target.value,
                          }),
                        )
                      }
                    >
                      <option value="">自动匹配</option>
                      {options.map((portId) => (
                        <option key={portId} value={portId}>
                          {portId}
                        </option>
                      ))}
                    </select>
                  </Field>
                </div>
              </div>
            );
          })}
        </div>
      )}
      <div className="input-row input-add">
        <div className="form-grid form-grid-2">
          <Field label="新投料物料" htmlFor="new-input-type">
            <select
              id="new-input-type"
              value={newInput.item_type}
              onChange={(event) =>
                setNewInput({
                  ...newInput,
                  item_type: event.target.value as ItemType,
                })
              }
            >
              {ITEM_TYPES.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </Field>
          <Field label="数量" htmlFor="new-input-qty">
            <input
              id="new-input-qty"
              type="number"
              min={1}
              value={newInput.quantity}
              onChange={(event) =>
                setNewInput({
                  ...newInput,
                  quantity: Math.max(1, Number(event.target.value) || 1),
                })
              }
            />
          </Field>
          <Field label="区域" htmlFor="new-input-zone">
            <input
              id="new-input-zone"
              value={newInput.zone}
              placeholder="line_s1a"
              onChange={(event) =>
                setNewInput({ ...newInput, zone: event.target.value })
              }
            />
          </Field>
        </div>
        <button
          type="button"
          className="button button-secondary"
          onClick={() =>
            onChange(
              addInput(draft, {
                item_type: newInput.item_type,
                quantity: newInput.quantity,
                zone: newInput.zone,
                node_id: newInput.node_id,
                port_id: newInput.port_id,
              }),
            )
          }
        >
          添加入口投料
        </button>
      </div>
    </div>
  );
}

function NodeInspector({
  draft,
  template,
  nodeId,
  onChange,
  onSelect,
}: {
  draft: DraftState;
  template?: TemplateSummary;
  nodeId: string;
  onChange: (draft: DraftState) => void;
  onSelect: (selection: Selection) => void;
}) {
  const incoming = draft.edges
    .map((edge, index) => ({ edge, index }))
    .filter((row) => row.edge.to === nodeId);
  const outgoing = draft.edges
    .map((edge, index) => ({ edge, index }))
    .filter((row) => row.edge.from === nodeId);
  return (
    <div className="stack">
      <div className="button-row">
        <button
          type="button"
          className="button button-quiet button-small"
          onClick={() => onSelect(null)}
        >
          返回订单设置
        </button>
      </div>
      <SectionTitle title="节点详情" description={`node_id ${nodeId}`} />
      {template ? (
        <>
          <p className="template-semantics">{template.semantics}</p>
          <dl className="detail-grid">
            <dt>模板</dt>
            <dd className="mono">{template.template_id}</dd>
            <dt>执行器</dt>
            <dd>{template.executor_kind}</dd>
            <dt>能力</dt>
            <dd>{template.capability}</dd>
            <dt>端口</dt>
            <dd className="mono">{portsSummary(template)}</dd>
            <dt>候选设备</dt>
            <dd>{template.candidate_devices.join(", ")}</dd>
            <dt>资源</dt>
            <dd>{template.resources.join(", ") || "无"}</dd>
            <dt>工步</dt>
            <dd>{template.work_steps.join(" → ")}</dd>
            <dt>完成校验</dt>
            <dd className="mono">{template.completion_verifier}</dd>
            {template.join_handler_id ? (
              <>
                <dt>join handler</dt>
                <dd className="mono">{template.join_handler_id}</dd>
              </>
            ) : null}
          </dl>
        </>
      ) : (
        <p className="muted">模板目录中找不到该节点模板。</p>
      )}
      <div className="edge-list">
        <h4>输入连线</h4>
        {incoming.length === 0 ? (
          <p className="muted">无输入连线；入口投料可能直接供给该节点。</p>
        ) : (
          incoming.map(({ edge, index }) => (
            <button
              type="button"
              className="edge-row"
              key={`in-${index}`}
              onClick={() => onSelect({ kind: "edge", index })}
            >
              <span className="mono">
                {edge.from} → {edge.to_port}
              </span>
              <span className="muted">edge #{index}</span>
            </button>
          ))
        )}
        <h4>输出连线</h4>
        {outgoing.length === 0 ? (
          <p className="muted">无输出连线。</p>
        ) : (
          outgoing.map(({ edge, index }) => (
            <button
              type="button"
              className="edge-row"
              key={`out-${index}`}
              onClick={() => onSelect({ kind: "edge", index })}
            >
              <span className="mono">
                {edge.from_port} → {edge.to}[{edge.to_port}]
              </span>
              <span className="muted">edge #{index}</span>
            </button>
          ))
        )}
      </div>
      <div className="button-row">
        <button
          type="button"
          className="button button-secondary"
          onClick={() => onChange(cloneNode(draft, nodeId).draft)}
        >
          复制节点
        </button>
        <button
          type="button"
          className="button button-danger"
          onClick={() => onChange(removeNode(draft, nodeId))}
        >
          删除节点及关联连线
        </button>
      </div>
    </div>
  );
}

function EdgeInspector({
  draft,
  edge,
  index,
  onChange,
  onSelect,
}: {
  draft: DraftState;
  edge: OrderEdge;
  index: number;
  onChange: (draft: DraftState) => void;
  onSelect: (selection: Selection) => void;
}) {
  return (
    <div className="stack">
      <div className="button-row">
        <button
          type="button"
          className="button button-quiet button-small"
          onClick={() => onSelect(null)}
        >
          返回订单设置
        </button>
      </div>
      <SectionTitle title="连线详情" description={`edge #${index}`} />
      <dl className="detail-grid">
        <dt>起点</dt>
        <dd className="mono">{edge.from}</dd>
        <dt>起点端口</dt>
        <dd className="mono">{edge.from_port || "未指定"}</dd>
        <dt>终点</dt>
        <dd className="mono">{edge.to}</dd>
        <dt>终点端口</dt>
        <dd className="mono">{edge.to_port}</dd>
      </dl>
      <button
        type="button"
        className="button button-danger"
        onClick={() => onChange(removeEdge(draft, index))}
      >
        删除连线
      </button>
    </div>
  );
}

function ConnectionForm({
  draft,
  templates,
  onConnect,
}: {
  draft: DraftState;
  templates: TemplateSummary[];
  onConnect: (edge: OrderEdge) => void;
}) {
  const map = useMemo(() => templateMap(templates), [templates]);
  const [source, setSource] = useState("");
  const [target, setTarget] = useState("");
  const [port, setPort] = useState("");
  const [error, setError] = useState("");
  const targetNode = draft.nodes.find((node) => node.node_id === target);
  const targetTemplate = targetNode ? map[targetNode.template_id] : undefined;
  const sourceNode = draft.nodes.find((node) => node.node_id === source);
  const sourceTemplate = sourceNode ? map[sourceNode.template_id] : undefined;

  const submit = () => {
    if (!source || !target || !port) {
      setError("请选择起点、终点和终点端口");
      return;
    }
    const edge: OrderEdge = {
      from: source,
      to: target,
      to_port: port,
      from_port: sourceTemplate?.output?.port_id ?? "",
    };
    const problem = edgeIssue(draft, map, edge);
    if (problem) {
      setError(problem);
      return;
    }
    setError("");
    onConnect(edge);
  };

  return (
    <section className="connection-form" aria-labelledby="connection-title">
      <SectionTitle
        title="键盘连接"
        description="不使用拖拽时，通过表单连接两个节点。"
      />
      <div className="form-grid form-grid-2">
        <Field label="起点节点" htmlFor="connect-source">
          <select
            id="connect-source"
            value={source}
            onChange={(event) => setSource(event.target.value)}
          >
            <option value="">选择起点</option>
            {draft.nodes.map((node) => (
              <option key={node.node_id} value={node.node_id}>
                {node.node_id} · {node.template_id}
              </option>
            ))}
          </select>
        </Field>
        <Field label="终点节点" htmlFor="connect-target">
          <select
            id="connect-target"
            value={target}
            onChange={(event) => {
              setTarget(event.target.value);
              setPort("");
            }}
          >
            <option value="">选择终点</option>
            {draft.nodes.map((node) => (
              <option key={node.node_id} value={node.node_id}>
                {node.node_id} · {node.template_id}
              </option>
            ))}
          </select>
        </Field>
        <Field label="终点输入端口" htmlFor="connect-port">
          <select
            id="connect-port"
            value={port}
            disabled={!targetTemplate}
            onChange={(event) => setPort(event.target.value)}
          >
            <option value="">选择端口</option>
            {(targetTemplate?.inputs ?? []).map((inputPort) => (
              <option key={inputPort.port_id} value={inputPort.port_id}>
                {inputPort.port_id} · {inputPort.zone}/{inputPort.item_type}
              </option>
            ))}
          </select>
        </Field>
      </div>
      {error ? (
        <p className="inline-error" role="alert">
          {error}
        </p>
      ) : null}
      <button type="button" className="button button-secondary" onClick={submit}>
        添加连线
      </button>
    </section>
  );
}

export function Inspector({
  draft,
  templates,
  selection,
  onChange,
  onSelect,
}: {
  draft: DraftState;
  templates: TemplateSummary[];
  selection: Selection;
  onChange: (draft: DraftState) => void;
  onSelect: (selection: Selection) => void;
}) {
  const map = useMemo(() => templateMap(templates), [templates]);
  const node =
    selection?.kind === "node"
      ? draft.nodes.find((row) => row.node_id === selection.nodeId)
      : undefined;
  const edge =
    selection?.kind === "edge" ? draft.edges[selection.index] : undefined;
  return (
    <aside className="inspector" aria-label="选择器与检查器">
      {node ? (
        <NodeInspector
          draft={draft}
          template={map[node.template_id]}
          nodeId={node.node_id}
          onChange={onChange}
          onSelect={onSelect}
        />
      ) : edge && selection?.kind === "edge" ? (
        <EdgeInspector
          draft={draft}
          edge={edge}
          index={selection.index}
          onChange={onChange}
          onSelect={onSelect}
        />
      ) : (
        <OrderSettings draft={draft} templates={templates} onChange={onChange} />
      )}
      <ConnectionForm
        draft={draft}
        templates={templates}
        onConnect={(newEdge) => {
          const result = addEdge(draft, newEdge, map);
          if (!result.error) onChange(result.draft);
        }}
      />
    </aside>
  );
}

export function NodeEdgeLists({
  draft,
  diagnostics,
  onSelect,
}: {
  draft: DraftState;
  diagnostics: Diagnostic[];
  onSelect: (selection: Selection) => void;
}) {
  const issuesByNode = new Set(
    diagnostics.map((diagnostic) => diagnostic.node_id).filter(Boolean),
  );
  return (
    <div className="node-edge-lists">
      <section aria-labelledby="node-list-title">
        <h3 id="node-list-title">节点列表</h3>
        {draft.nodes.length === 0 ? (
          <p className="muted">暂无节点。</p>
        ) : (
          <ul>
            {draft.nodes.map((node) => (
              <li key={node.node_id}>
                <button
                  type="button"
                  className="list-row"
                  onClick={() => onSelect({ kind: "node", nodeId: node.node_id })}
                >
                  <span className="mono">{node.node_id}</span>
                  <span>{node.template_id}</span>
                  {issuesByNode.has(node.node_id) ? (
                    <Badge tone="failure">有诊断</Badge>
                  ) : null}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section aria-labelledby="edge-list-title">
        <h3 id="edge-list-title">连线列表</h3>
        {draft.edges.length === 0 ? (
          <p className="muted">暂无连线。</p>
        ) : (
          <ul>
            {draft.edges.map((edge, index) => (
              <li key={`${edge.from}-${edge.to}-${edge.to_port}-${index}`}>
                <button
                  type="button"
                  className="list-row"
                  onClick={() => onSelect({ kind: "edge", index })}
                >
                  <span className="mono">
                    {edge.from} → {edge.to}[{edge.to_port}]
                  </span>
                  <span className="muted">edge #{index}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
