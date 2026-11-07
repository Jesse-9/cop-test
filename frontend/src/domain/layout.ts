import dagre from "@dagrejs/dagre";
import type { DraftState, NodePosition } from "./orderDraft";

const NODE_WIDTH = 240;
const NODE_HEIGHT = 132;

export function layoutDraft(draft: DraftState): Record<string, NodePosition> {
  if (draft.nodes.length === 0) {
    return {};
  }
  const graph = new dagre.graphlib.Graph();
  graph.setDefaultEdgeLabel(() => ({}));
  graph.setGraph({
    rankdir: "LR",
    nodesep: 48,
    ranksep: 96,
    marginx: 24,
    marginy: 24,
  });
  for (const node of draft.nodes) {
    graph.setNode(node.node_id, {
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
    });
  }
  for (const edge of draft.edges) {
    if (graph.hasNode(edge.from) && graph.hasNode(edge.to)) {
      graph.setEdge(edge.from, edge.to);
    }
  }
  dagre.layout(graph);
  const positions: Record<string, NodePosition> = {};
  for (const node of draft.nodes) {
    const position = graph.node(node.node_id) as
      | { x: number; y: number }
      | undefined;
    positions[node.node_id] = position
      ? {
          x: position.x - NODE_WIDTH / 2,
          y: position.y - NODE_HEIGHT / 2,
        }
      : { x: 48, y: 48 };
  }
  return positions;
}

export function defaultPosition(index: number): NodePosition {
  return {
    x: 48 + (index % 3) * 280,
    y: 48 + Math.floor(index / 3) * 180,
  };
}
