import type {
  Diagnostic,
  MaterialPort,
  OrderEdge,
  OrderInput,
  OrderNode,
  OrderSpec,
  OrderTarget,
  TemplateSummary,
} from "../api/types";

export const ORDER_CONTRACT_VERSION = "v1_order_20261106";

export interface NodePosition {
  x: number;
  y: number;
}

export interface DraftState {
  contractVersion: string;
  orderId: string;
  version: number;
  sceneId: string;
  source: OrderSpec["source"];
  target: OrderTarget;
  inputs: OrderInput[];
  nodes: OrderNode[];
  edges: OrderEdge[];
  positions: Record<string, NodePosition>;
}

export interface TemplateMap {
  [templateId: string]: TemplateSummary;
}

export function templateMap(templates: TemplateSummary[]): TemplateMap {
  return Object.fromEntries(
    templates.map((template) => [template.template_id, template]),
  );
}

export function blankDraft(orderId = "order-draft"): DraftState {
  return {
    contractVersion: ORDER_CONTRACT_VERSION,
    orderId,
    version: 1,
    sceneId: "L5_UFLOW",
    source: "manual",
    target: { item_type: "FINAL", quantity: 1 },
    inputs: [],
    nodes: [],
    edges: [],
    positions: {},
  };
}

export function fromSpec(spec: OrderSpec): DraftState {
  return {
    contractVersion: spec.contract_version,
    orderId: spec.order_id,
    version: spec.version,
    sceneId: spec.scene_id,
    source: spec.source,
    target: { ...spec.target },
    inputs: spec.inputs.map((input) => ({ ...input })),
    nodes: spec.nodes.map((node) => ({ ...node })),
    edges: spec.edges.map((edge) => ({ ...edge })),
    positions: {},
  };
}

export function toSpec(draft: DraftState): OrderSpec {
  return {
    contract_version: draft.contractVersion,
    order_id: draft.orderId,
    version: draft.version,
    scene_id: draft.sceneId,
    source: draft.source,
    target: { ...draft.target },
    inputs: draft.inputs.map((input) => ({ ...input })),
    nodes: draft.nodes.map((node) => ({ ...node })),
    edges: draft.edges.map((edge) => ({ ...edge })),
  };
}

export function cloneDraft(draft: DraftState): DraftState {
  return {
    ...draft,
    target: { ...draft.target },
    inputs: draft.inputs.map((input) => ({ ...input })),
    nodes: draft.nodes.map((node) => ({ ...node })),
    edges: draft.edges.map((edge) => ({ ...edge })),
    positions: Object.fromEntries(
      Object.entries(draft.positions).map(([key, position]) => [
        key,
        { ...position },
      ]),
    ),
  };
}

export function sanitizeIdentifier(value: string): string {
  const normalized = value.replace(/[^A-Za-z0-9._-]/g, "-");
  return /^[A-Za-z0-9]/.test(normalized) ? normalized : `n-${normalized}`;
}

export function newNodeId(draft: DraftState, templateId: string): string {
  const prefix = sanitizeIdentifier(templateId).slice(0, 48);
  for (let serial = 1; serial < 10_000; serial += 1) {
    const suffix = `-${String(serial).padStart(2, "0")}`;
    const candidate = `${prefix}${suffix}`;
    if (!draft.nodes.some((node) => node.node_id === candidate)) {
      return candidate.slice(0, 64);
    }
  }
  throw new Error("无法生成唯一节点 ID");
}

export function newInputId(draft: DraftState): string {
  for (let serial = 1; serial < 10_000; serial += 1) {
    const candidate = `in-${String(serial).padStart(3, "0")}`;
    if (!draft.inputs.some((input) => input.input_id === candidate)) {
      return candidate;
    }
  }
  throw new Error("无法生成唯一投料 ID");
}

export function addNode(
  draft: DraftState,
  templateId: string,
  position: NodePosition,
): { draft: DraftState; nodeId: string } {
  const nodeId = newNodeId(draft, templateId);
  const next = cloneDraft(draft);
  next.nodes.push({ node_id: nodeId, template_id: templateId });
  next.positions[nodeId] = { ...position };
  return { draft: next, nodeId };
}

export function removeNode(draft: DraftState, nodeId: string): DraftState {
  const next = cloneDraft(draft);
  next.nodes = next.nodes.filter((node) => node.node_id !== nodeId);
  next.edges = next.edges.filter(
    (edge) => edge.from !== nodeId && edge.to !== nodeId,
  );
  next.inputs = next.inputs.filter((input) => input.node_id !== nodeId);
  delete next.positions[nodeId];
  return next;
}

export function cloneNode(
  draft: DraftState,
  nodeId: string,
): { draft: DraftState; nodeId: string } {
  const source = draft.nodes.find((node) => node.node_id === nodeId);
  if (!source) {
    return { draft, nodeId: "" };
  }
  const position = draft.positions[nodeId] ?? { x: 80, y: 80 };
  return addNode(draft, source.template_id, {
    x: position.x + 32,
    y: position.y + 48,
  });
}

export function edgeKey(edge: OrderEdge): string {
  return `${edge.from}|${edge.to}|${edge.to_port}|${edge.from_port}`;
}

function portById(
  template: TemplateSummary,
  portId: string,
): MaterialPort | undefined {
  return template.inputs.find((port) => port.port_id === portId);
}

export function edgeIssue(
  draft: DraftState,
  templates: TemplateMap,
  edge: OrderEdge,
): string | null {
  if (edge.from === edge.to) {
    return "不能创建自环";
  }
  const from = draft.nodes.find((node) => node.node_id === edge.from);
  const to = draft.nodes.find((node) => node.node_id === edge.to);
  if (!from || !to) {
    return "连线端点不存在";
  }
  if (draft.edges.some((existing) => edgeKey(existing) === edgeKey(edge))) {
    return "重复连线";
  }
  const fromTemplate = templates[from.template_id];
  const toTemplate = templates[to.template_id];
  if (!fromTemplate || !toTemplate) {
    return null;
  }
  const targetPort = portById(toTemplate, edge.to_port);
  if (!targetPort) {
    return `目标模板没有输入端口 ${edge.to_port}`;
  }
  if (fromTemplate.output === null) {
    return `源模板 ${fromTemplate.template_id} 没有输出端口`;
  }
  if (
    fromTemplate.output.zone !== targetPort.zone ||
    fromTemplate.output.item_type !== targetPort.item_type
  ) {
    return `物料不连续：${fromTemplate.output.zone}/${fromTemplate.output.item_type} → ${targetPort.zone}/${targetPort.item_type}`;
  }
  return null;
}

export function addEdge(
  draft: DraftState,
  edge: OrderEdge,
  templates: TemplateMap,
): { draft: DraftState; error: string } {
  const error = edgeIssue(draft, templates, edge);
  if (error) {
    return { draft, error };
  }
  const next = cloneDraft(draft);
  next.edges.push({ ...edge });
  return { draft: next, error: "" };
}

export function removeEdge(draft: DraftState, index: number): DraftState {
  const next = cloneDraft(draft);
  next.edges = next.edges.filter((_, edgeIndex) => edgeIndex !== index);
  return next;
}

export function updateTarget(
  draft: DraftState,
  target: Partial<OrderTarget>,
): DraftState {
  return {
    ...cloneDraft(draft),
    target: { ...draft.target, ...target },
  };
}

export function addInput(draft: DraftState, input: Omit<OrderInput, "input_id">): DraftState {
  const next = cloneDraft(draft);
  next.inputs.push({ ...input, input_id: newInputId(draft) });
  return next;
}

export function updateInput(
  draft: DraftState,
  inputId: string,
  changes: Partial<OrderInput>,
): DraftState {
  const next = cloneDraft(draft);
  next.inputs = next.inputs.map((input) =>
    input.input_id === inputId ? { ...input, ...changes } : input,
  );
  return next;
}

export function removeInput(draft: DraftState, inputId: string): DraftState {
  const next = cloneDraft(draft);
  next.inputs = next.inputs.filter((input) => input.input_id !== inputId);
  return next;
}

export function localDiagnostics(
  draft: DraftState,
  templates: TemplateMap,
): Diagnostic[] {
  const issues: Diagnostic[] = [];
  const add = (
    code: string,
    message: string,
    locator: Partial<Diagnostic> = {},
  ) => {
    issues.push({
      code,
      message,
      node_id: locator.node_id ?? "",
      input_id: locator.input_id ?? "",
      edge_index: locator.edge_index ?? -1,
      port_id: locator.port_id ?? "",
    });
  };
  if (draft.orderId.trim() === "") {
    add("DRAFT_ORDER_ID", "订单 ID 不能为空");
  }
  if (draft.target.quantity <= 0) {
    add("DRAFT_TARGET_QUANTITY", "目标数量必须大于 0");
  }
  const seen = new Set<string>();
  for (const node of draft.nodes) {
    if (seen.has(node.node_id)) {
      add("DRAFT_NODE_DUPLICATE", `重复节点 ID ${node.node_id}`, {
        node_id: node.node_id,
      });
    }
    seen.add(node.node_id);
    if (!templates[node.template_id]) {
      add(
        "DRAFT_TEMPLATE_MISSING",
        `节点 ${node.node_id} 引用未知模板 ${node.template_id}`,
        { node_id: node.node_id },
      );
    }
  }
  draft.edges.forEach((edge, index) => {
    const error = edgeIssue(draft, templates, edge);
    if (error) {
      add("DRAFT_EDGE", error, {
        node_id: edge.to,
        edge_index: index,
        port_id: edge.to_port,
      });
    }
  });
  const inputIds = new Set<string>();
  for (const input of draft.inputs) {
    if (inputIds.has(input.input_id)) {
      add("DRAFT_INPUT_DUPLICATE", `重复投料 ID ${input.input_id}`, {
        input_id: input.input_id,
      });
    }
    inputIds.add(input.input_id);
    if (input.quantity <= 0) {
      add("DRAFT_INPUT_QUANTITY", `投料 ${input.input_id} 数量必须大于 0`, {
        input_id: input.input_id,
      });
    }
    if (input.node_id) {
      const node = draft.nodes.find((row) => row.node_id === input.node_id);
      if (!node) {
        add(
          "DRAFT_INPUT_NODE",
          `投料 ${input.input_id} 绑定未知节点 ${input.node_id}`,
          { input_id: input.input_id, node_id: input.node_id },
        );
      } else {
        const template = templates[node.template_id];
        if (template && input.port_id && !portById(template, input.port_id)) {
          add(
            "DRAFT_INPUT_PORT",
            `投料 ${input.input_id} 绑定未知端口 ${input.port_id}`,
            {
              input_id: input.input_id,
              node_id: input.node_id,
              port_id: input.port_id,
            },
          );
        }
      }
    }
  }
  return issues;
}

export function applyLayout(
  draft: DraftState,
  positions: Record<string, NodePosition>,
): DraftState {
  return {
    ...cloneDraft(draft),
    positions: { ...positions },
  };
}
