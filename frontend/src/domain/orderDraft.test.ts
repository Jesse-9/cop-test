import { describe, expect, it } from "vitest";
import type { TemplateCatalog, TemplateSummary } from "../api/types";
import {
  addEdge,
  addInput,
  addNode,
  blankDraft,
  draftProfileIssues,
  fromSpec,
  localDiagnostics,
  pruneForCatalog,
  removeNode,
  templateMap,
  toSpec,
} from "./orderDraft";

function template(
  templateId: string,
  inputZone: string,
  inputType: TemplateSummary["inputs"][number]["item_type"],
  outputZone: string,
  outputType: TemplateSummary["inputs"][number]["item_type"],
): TemplateSummary {
  return {
    template_id: templateId,
    kind: templateId,
    semantics: `工序 ${templateId}`,
    executor_kind: "arm",
    capability: "robotic_pick_place",
    candidate_devices: ["arm_A"],
    resources: [],
    inputs: [{ port_id: "input-0", zone: inputZone, item_type: inputType }],
    output: { port_id: "output-0", zone: outputZone, item_type: outputType },
    source_kind: "conveyor",
    sink_kind: "station",
    work_steps: ["arm_pick_place"],
    completion_verifier: "unit_handoff_verified",
    join_handler_id: "",
    predecessor_policy: "all",
    scene_id: "L5_UFLOW",
  };
}

const TEMPLATES = [
  template("a", "line_s1a", "RAW_A", "handoff_ac", "SEMI_AC"),
  template("c", "handoff_ac", "SEMI_AC", "output_e", "FINAL"),
];
const MAP = templateMap(TEMPLATES);

const CATALOG: TemplateCatalog = {
  catalog_version: "v1_process_test",
  contract_version: "v1_order_test",
  scene_id: "L5_UFLOW",
  artifact_id: "v1/L5_UFLOW/v005",
  scene_profile: "full_flow",
  profile_source: "manifest",
  profile_version: "v1_scene_profile_test",
  profiles: ["full_flow", "assembly_mvp"],
  item_types: { targets: ["SEMI_AC"], inputs: ["RAW_A"] },
  zones: [
    {
      id: "line_s1a",
      capacity: 1,
      description: "S1→A 输入带",
      spawnable: true,
      item_types: ["RAW_A"],
    },
  ],
  templates: [TEMPLATES[0]],
  routes: [],
};

describe("order draft model", () => {
  it("adds stable unique node IDs for repeated templates", () => {
    const blank = blankDraft("order-1");
    const first = addNode(blank, "a", { x: 0, y: 0 });
    const second = addNode(first.draft, "a", { x: 10, y: 10 });
    expect(first.nodeId).not.toBe(second.nodeId);
    expect(second.draft.nodes.map((node) => node.node_id)).toEqual([
      first.nodeId,
      second.nodeId,
    ]);
    expect(second.draft.positions[second.nodeId]).toEqual({ x: 10, y: 10 });
  });

  it("accepts a compatible edge and rejects self/duplicate/mismatch", () => {
    const first = addNode(blankDraft(), "a", { x: 0, y: 0 });
    const second = addNode(first.draft, "c", { x: 10, y: 10 });
    const valid = addEdge(
      second.draft,
      {
        from: first.nodeId,
        to: second.nodeId,
        to_port: "input-0",
        from_port: "output-0",
      },
      MAP,
    );
    expect(valid.error).toBe("");
    expect(valid.draft.edges).toHaveLength(1);

    const self = addEdge(
      valid.draft,
      {
        from: first.nodeId,
        to: first.nodeId,
        to_port: "input-0",
        from_port: "output-0",
      },
      MAP,
    );
    expect(self.error).toContain("自环");

    const duplicate = addEdge(valid.draft, valid.draft.edges[0], MAP);
    expect(duplicate.error).toContain("重复");

    const mismatch = addEdge(
      valid.draft,
      {
        from: second.nodeId,
        to: first.nodeId,
        to_port: "input-0",
        from_port: "output-0",
      },
      MAP,
    );
    expect(mismatch.error).toContain("物料不连续");
  });

  it("removes a node together with its edges and input bindings", () => {
    const first = addNode(blankDraft(), "a", { x: 0, y: 0 });
    const second = addNode(first.draft, "c", { x: 10, y: 10 });
    const withEdge = addEdge(
      second.draft,
      {
        from: first.nodeId,
        to: second.nodeId,
        to_port: "input-0",
        from_port: "output-0",
      },
      MAP,
    ).draft;
    const withInput = addInput(withEdge, {
      item_type: "RAW_A",
      quantity: 1,
      zone: "line_s1a",
      node_id: first.nodeId,
      port_id: "input-0",
    });
    const next = removeNode(withInput, first.nodeId);
    expect(next.nodes.map((node) => node.node_id)).toEqual([second.nodeId]);
    expect(next.edges).toHaveLength(0);
    expect(next.inputs).toHaveLength(0);
  });

  it("round-trips the persisted OrderSpec without canvas positions", () => {
    const first = addNode(blankDraft("roundtrip"), "a", { x: 0, y: 0 });
    const spec = toSpec(first.draft);
    expect(spec.order_id).toBe("roundtrip");
    expect(spec.nodes).toHaveLength(1);
    expect("positions" in spec).toBe(false);
    const restored = fromSpec(spec);
    expect(toSpec(restored)).toEqual(spec);
  });

  it("reports local issues for missing templates and bad input quantities", () => {
    const first = addNode(blankDraft(), "a", { x: 0, y: 0 });
    const broken = {
      ...first.draft,
      nodes: [
        ...first.draft.nodes,
        { node_id: "missing-01", template_id: "missing" },
      ],
      inputs: [
        {
          input_id: "in-001",
          item_type: "RAW_A" as const,
          quantity: 0,
          zone: "line_s1a",
          node_id: first.nodeId,
          port_id: "input-0",
        },
      ],
    };
    const issues = localDiagnostics(broken, MAP);
    expect(issues.map((issue) => issue.code)).toEqual(
      expect.arrayContaining([
        "DRAFT_TEMPLATE_MISSING",
        "DRAFT_INPUT_QUANTITY",
      ]),
    );
  });

  it("blocks cross-profile drafts and prunes incompatible content", () => {
    const withNode = addNode(blankDraft("profile"), "c", { x: 0, y: 0 });
    const draft = addInput(withNode.draft, {
      item_type: "RAW_B",
      quantity: 1,
      zone: "line_s2d",
      node_id: withNode.nodeId,
      port_id: "input-0",
    });
    const issues = draftProfileIssues(draft, CATALOG);
    expect(issues.map((issue) => issue.code)).toEqual(
      expect.arrayContaining([
        "DRAFT_TARGET_PROFILE",
        "DRAFT_TEMPLATE_PROFILE",
        "DRAFT_INPUT_PROFILE",
        "DRAFT_INPUT_ZONE_PROFILE",
      ]),
    );

    const pruned = pruneForCatalog(draft, CATALOG);
    expect(pruned.nodes).toHaveLength(0);
    expect(pruned.inputs).toHaveLength(0);
    expect(pruned.target.item_type).toBe("SEMI_AC");
    expect(draftProfileIssues(pruned, CATALOG)).toEqual([]);
  });
});
