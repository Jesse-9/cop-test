import { useEffect, useMemo } from "react";
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type {
  Diagnostic,
  NodeState,
  TemplateSummary,
  OrderEdge,
} from "../api/types";
import type { DraftState, NodePosition } from "../domain/orderDraft";
import { NODE_STATE_LABELS, nodeStateTone } from "../domain/status";
import type { RunNode } from "../api/types";
import { Badge } from "./ui";

export type ProcessNodeData = {
  label: string;
  templateId: string;
  template?: TemplateSummary;
  issueCount: number;
  state?: NodeState;
  failureScope?: "" | "NODE" | "GLOBAL";
  [key: string]: unknown;
};

type ProcessNodeType = Node<ProcessNodeData, "process">;

function ProcessNode({ data, selected }: NodeProps<ProcessNodeType>) {
  const template = data.template;
  const state = data.state;
  return (
    <div
      className={`process-node${selected ? " selected" : ""}${
        state ? ` node-${nodeStateTone(state)}` : ""
      }`}
      data-testid={`node-${data.templateId}`}
    >
      {template?.inputs.map((port, index) => (
        <Handle
          key={port.port_id}
          id={port.port_id}
          type="target"
          position={Position.Left}
          style={{ top: `${28 + index * 24}px` }}
          className="port-handle port-in"
          title={`${port.port_id}: ${port.zone}/${port.item_type}`}
        />
      ))}
      <div className="process-node-head">
        <span className="node-kind">{template?.executor_kind ?? "?"}</span>
        {state ? (
          <Badge tone={nodeStateTone(state)}>{NODE_STATE_LABELS[state]}</Badge>
        ) : null}
        {data.issueCount > 0 ? (
          <Badge tone="failure">{data.issueCount} 个问题</Badge>
        ) : null}
      </div>
      <strong className="node-label">{data.label}</strong>
      <span className="mono node-template">{data.templateId}</span>
      <div className="node-ports">
        <span>
          {(template?.inputs ?? [])
            .map((port) => port.item_type)
            .join(" + ") || "无输入"}
        </span>
        <span aria-hidden="true">→</span>
        <span>{template?.output?.item_type ?? "无输出"}</span>
      </div>
      {template?.output ? (
        <Handle
          id={template.output.port_id}
          type="source"
          position={Position.Right}
          className="port-handle port-out"
          title={`${template.output.port_id}: ${template.output.zone}/${template.output.item_type}`}
        />
      ) : null}
    </div>
  );
}

const nodeTypes = { process: ProcessNode };
const NO_RUN_NODES: RunNode[] = [];

function buildNodes(
  draft: DraftState,
  templates: Map<string, TemplateSummary>,
  diagnostics: Diagnostic[],
  selectedNodeId: string,
  runNodes: Map<string, RunNode>,
  readOnly: boolean,
): ProcessNodeType[] {
  const issues = new Map<string, number>();
  for (const diagnostic of diagnostics) {
    if (diagnostic.node_id) {
      issues.set(diagnostic.node_id, (issues.get(diagnostic.node_id) ?? 0) + 1);
    }
  }
  return draft.nodes.map((node, index) => {
    const template = templates.get(node.template_id);
    const run = runNodes.get(node.node_id);
    const position = draft.positions[node.node_id] ?? {
      x: 48 + (index % 3) * 280,
      y: 48 + Math.floor(index / 3) * 180,
    };
    return {
      id: node.node_id,
      type: "process",
      position,
      selected: node.node_id === selectedNodeId,
      draggable: !readOnly,
      selectable: true,
      data: {
        label: template?.semantics ?? `未知模板 ${node.template_id}`,
        templateId: node.template_id,
        template,
        issueCount: issues.get(node.node_id) ?? 0,
        state: run?.state,
        failureScope: run?.failure_scope,
      },
    };
  });
}

function buildEdges(draft: DraftState, diagnostics: Diagnostic[]): Edge[] {
  const badEdges = new Set(
    diagnostics
      .filter((diagnostic) => diagnostic.edge_index >= 0)
      .map((diagnostic) => diagnostic.edge_index),
  );
  return draft.edges.map((edge: OrderEdge, index: number) => ({
    id: `edge-${index}`,
    source: edge.from,
    target: edge.to,
    sourceHandle: edge.from_port || undefined,
    targetHandle: edge.to_port || undefined,
    label: edge.to_port,
    type: "smoothstep",
    markerEnd: { type: MarkerType.ArrowClosed },
    data: { index },
    className: badEdges.has(index) ? "edge-error" : "",
    labelBgPadding: [4, 2] as [number, number],
    labelBgBorderRadius: 3,
  }));
}

export type Selection =
  | { kind: "node"; nodeId: string }
  | { kind: "edge"; index: number }
  | null;

export function OrderCanvas({
  draft,
  templates,
  diagnostics,
  selected,
  runNodes = NO_RUN_NODES,
  readOnly = false,
  onSelect,
  onConnect,
  onMoveNode,
}: {
  draft: DraftState;
  templates: TemplateSummary[];
  diagnostics: Diagnostic[];
  selected: Selection;
  runNodes?: RunNode[];
  readOnly?: boolean;
  onSelect: (selection: Selection) => void;
  onConnect: (edge: OrderEdge) => void;
  onMoveNode: (nodeId: string, position: NodePosition) => void;
}) {
  const templateMap = useMemo(
    () =>
      new Map(templates.map((template) => [template.template_id, template])),
    [templates],
  );
  const runMap = useMemo(
    () => new Map(runNodes.map((node) => [node.node_id, node])),
    [runNodes],
  );
  const [nodes, setNodes, onNodesChange] = useNodesState<ProcessNodeType>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);

  useEffect(() => {
    setNodes(
      buildNodes(
        draft,
        templateMap,
        diagnostics,
        selected?.kind === "node" ? selected.nodeId : "",
        runMap,
        readOnly,
      ),
    );
    setEdges(buildEdges(draft, diagnostics).map((edge) => ({
      ...edge,
      selected:
        selected?.kind === "edge" && edge.data?.index === selected.index,
    })));
  }, [draft, templateMap, diagnostics, selected, runMap, readOnly, setNodes, setEdges]);

  const handleConnect = (connection: Connection) => {
    if (!connection.source || !connection.target) return;
    const sourceNode = draft.nodes.find(
      (node) => node.node_id === connection.source,
    );
    const targetNode = draft.nodes.find(
      (node) => node.node_id === connection.target,
    );
    if (!sourceNode || !targetNode) return;
    const sourceTemplate = templateMap.get(sourceNode.template_id);
    const targetTemplate = templateMap.get(targetNode.template_id);
    const fromPort =
      connection.sourceHandle ?? sourceTemplate?.output?.port_id ?? "";
    const toPort =
      connection.targetHandle ?? targetTemplate?.inputs[0]?.port_id ?? "";
    onConnect({
      from: connection.source,
      to: connection.target,
      to_port: toPort,
      from_port: fromPort,
    });
  };

  return (
    <div className="canvas-wrap" aria-label="订单工序 DAG 画布">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={handleConnect}
        onNodeClick={(_, node) => onSelect({ kind: "node", nodeId: node.id })}
        onEdgeClick={(_, edge) =>
          onSelect({ kind: "edge", index: Number(edge.data?.index ?? -1) })
        }
        onPaneClick={() => onSelect(null)}
        onNodeDragStop={(_, node) => onMoveNode(node.id, node.position)}
        nodesDraggable={!readOnly}
        nodesConnectable={!readOnly}
        elementsSelectable
        fitView
        fitViewOptions={{ padding: 0.2 }}
        minZoom={0.25}
        maxZoom={1.8}
        proOptions={{ hideAttribution: false }}
      >
        <Background gap={18} size={1} />
        <Controls showInteractive={!readOnly} />
        <MiniMap pannable zoomable />
      </ReactFlow>
      {draft.nodes.length === 0 ? (
        <div className="canvas-empty">
          <strong>画布为空</strong>
          <p>从左侧工序模板添加节点，或使用下方“添加节点”表单。</p>
        </div>
      ) : null}
    </div>
  );
}
