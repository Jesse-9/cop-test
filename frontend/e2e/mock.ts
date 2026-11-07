import type { Page } from "@playwright/test";
import type {
  CompilePreview,
  OrderHeader,
  OrderSpec,
  OrderVersionDetail,
  PlannerResult,
  RunDetail,
  TemplateCatalog,
  TemplateSummary,
} from "../src/api/types";

const DIGEST = "d".repeat(64);
const PLANNER_DIGEST = "p".repeat(64);

function template(
  id: string,
  inputZone: string,
  inputType: TemplateSummary["inputs"][number]["item_type"],
  outputZone: string,
  outputType: TemplateSummary["inputs"][number]["item_type"],
  device: string,
): TemplateSummary {
  return {
    template_id: id,
    kind: id,
    semantics: `工序 ${id}`,
    executor_kind: "arm",
    capability: "robotic_pick_place",
    candidate_devices: [device],
    resources: [inputZone, outputZone, device],
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

export const catalog: TemplateCatalog = {
  catalog_version: "v1_process_mock",
  contract_version: "v1_order_20261106",
  scene_id: "L5_UFLOW",
  templates: [
    template("a", "line_s1a", "RAW_A", "handoff_ac", "SEMI_AC", "arm_A"),
    template("c", "handoff_ac", "SEMI_AC", "output_e", "FINAL", "arm_C"),
  ],
  routes: [
    {
      route_id: "route_a",
      group: "test_group",
      semantics: "A → C 测试路线",
      operation_ids: ["a", "c"],
      entry_zone: "line_s1a",
      exit_zone: "output_e",
    },
  ],
};

export function specFor(
  orderId: string,
  version = 1,
  source: OrderSpec["source"] = "manual",
): OrderSpec {
  return {
    contract_version: "v1_order_20261106",
    order_id: orderId,
    version,
    scene_id: "L5_UFLOW",
    source,
    target: { item_type: "FINAL", quantity: 1 },
    inputs: [
      {
        input_id: "in-001",
        item_type: "RAW_A",
        quantity: 1,
        zone: "line_s1a",
        node_id: "a-01",
        port_id: "input-0",
      },
    ],
    nodes: [
      { node_id: "a-01", template_id: "a" },
      { node_id: "c-01", template_id: "c" },
    ],
    edges: [
      { from: "a-01", to: "c-01", to_port: "input-0", from_port: "output-0" },
    ],
  };
}

function versionMeta(
  orderId: string,
  version: number,
  source: OrderSpec["source"],
) {
  return {
    order_id: orderId,
    version,
    contract_version: "v1_order_20261106",
    scene_id: "L5_UFLOW",
    source,
    dag_digest: source === "planner" ? PLANNER_DIGEST : DIGEST,
    catalog_version: "v1_process_mock",
    parent_version: version > 1 ? version - 1 : null,
    planner_policy_version:
      source === "planner" ? "v1_planner_20261107" : "",
    planner_candidate_count: source === "planner" ? 1 : null,
    created_at: "2026-11-07T10:00:00+00:00",
    note: "",
  };
}

export function orderHeader(
  orderId: string,
  version = 1,
  source: OrderSpec["source"] = "manual",
): OrderHeader {
  return {
    order_id: orderId,
    current_version: version,
    source,
    created_at: "2026-11-07T10:00:00+00:00",
    updated_at: "2026-11-07T10:00:00+00:00",
    note: "",
    versions: [versionMeta(orderId, version, source)],
    latest_run: null,
  };
}

export function versionDetail(
  orderId: string,
  version = 1,
  source: OrderSpec["source"] = "manual",
): OrderVersionDetail {
  return {
    ...versionMeta(orderId, version, source),
    spec: specFor(orderId, version, source),
    planner:
      source === "planner"
        ? {
            policy_version: "v1_planner_20261107",
            input: {},
            explain: { selected_branch: { route_ids: ["route_a"] } },
            candidate_count: 1,
          }
        : null,
  };
}

export const preview: CompilePreview = {
  order_id: "order-1",
  version: 1,
  scene_id: "L5_UFLOW",
  contract_version: "v1_order_20261106",
  digest: DIGEST,
  ok: true,
  simulation_started: false,
  diagnostics: [],
  topological_order: ["a-01", "c-01"],
  entry_node_ids: ["a-01"],
  terminal_target_node_ids: ["c-01"],
  nodes: [
    {
      node_id: "a-01",
      template_id: "a",
      available: true,
      inputs: [{ port_id: "input-0", zone: "line_s1a", item_type: "RAW_A" }],
      output: { port_id: "output-0", zone: "handoff_ac", item_type: "SEMI_AC" },
    },
    {
      node_id: "c-01",
      template_id: "c",
      available: true,
      inputs: [
        { port_id: "input-0", zone: "handoff_ac", item_type: "SEMI_AC" },
      ],
      output: { port_id: "output-0", zone: "output_e", item_type: "FINAL" },
    },
  ],
  instance_counts: { "a-01": 1, "c-01": 1 },
  material_plans: [],
  target_instance_ids: ["preview:c-01:1"],
  expected_outputs: [
    {
      instance_id: "preview:c-01:1",
      node_id: "c-01",
      uid: 1,
      item_type: "FINAL",
      zone: "output_e",
      path: ["a-01", "c-01"],
    },
  ],
};

const candidate = {
  candidate_id: "cand-000",
  branch: {
    kind: "route",
    label: "route_a",
    route_ids: ["route_a"],
    template_ids: ["a", "c"],
    input_requirements: [
      { item_type: "RAW_A" as const, zone: "line_s1a", quantity: 1 },
    ],
    output_item_type: "FINAL" as const,
    output_zone: "output_e",
    operation_count: 2,
  },
  feasible: true,
  rejection_code: "",
  rejection_reason: "",
  operation_count: 2,
  route_ids: ["route_a"],
  template_ids: ["a", "c"],
  digest: PLANNER_DIGEST,
  score: [0, 2, "route_a"],
  input_usage: [
    { item_type: "RAW_A" as const, zone: "line_s1a", required: 1, available: 1 },
  ],
};

export const plannerResult: PlannerResult = {
  ok: true,
  policy_version: "v1_planner_20261107",
  input: {},
  first_blocking_category: "",
  diagnostics: [],
  selected: candidate,
  selected_order: specFor("planner-order", 1, "planner"),
  digest: PLANNER_DIGEST,
  preview: { ...preview, order_id: "planner-order" },
  candidates: [candidate],
  rejected: [],
  explanation: {
    policy_version: "v1_planner_20261107",
    input_summary: {},
    selected_candidate_id: "cand-000",
    selected_branch: candidate.branch,
    selected_order: specFor("planner-order", 1, "planner"),
    digest: PLANNER_DIGEST,
    target_paths: preview.expected_outputs,
    join_bindings: [],
    candidates: [candidate],
    rejected: [],
    search: { expansions: 3, branches: 1, truncated: false },
    unmodeled_dimensions: ["设备健康/节拍"],
    first_blocking_category: "",
  },
  search: { expansions: 3, branches: 1, truncated: false },
};

function runNode(
  nodeId: string,
  templateId: string,
  state: "FAILED" | "BLOCKED" | "DONE",
  scope: "" | "NODE" | "GLOBAL",
  code: string,
  blockedBy: string[] = [],
) {
  return {
    instance_id: `run:${nodeId}:1`,
    node_id: nodeId,
    template_id: templateId,
    uid: 1,
    state,
    failure_scope: scope,
    failure_code: code,
    failure_reason: code ? "模拟失败原因" : "",
    blocked_by: blockedBy,
    input_uids: [1],
    output_uids: [],
    evidence: {},
    updated_at: "2026-11-07T10:01:00+00:00",
  };
}

export const runs: Record<string, RunDetail> = {
  "run-1": {
    run_id: "run-1",
    order_id: "order-1",
    order_version: 1,
    dag_digest: DIGEST,
    artifact_id: "artifact-test",
    status: "PARTIAL_FAILED",
    failure_scope: "NODE",
    stop_reason: "",
    started_at: "2026-11-07T10:00:00+00:00",
    finished_at: "2026-11-07T10:05:00+00:00",
    result: null,
    node_count: 2,
    failed_count: 1,
    cancelled_count: 0,
    nodes: [],
  },
  "run-2": {
    run_id: "run-2",
    order_id: "order-1",
    order_version: 1,
    dag_digest: DIGEST,
    artifact_id: "artifact-test",
    status: "ABORTED",
    failure_scope: "GLOBAL",
    stop_reason: "安全锁存: tcp_collision",
    started_at: "2026-11-07T10:10:00+00:00",
    finished_at: "2026-11-07T10:11:00+00:00",
    result: null,
    node_count: 2,
    failed_count: 1,
    cancelled_count: 0,
    nodes: [],
  },
};

export const runNodes: Record<string, ReturnType<typeof runNode>[]> = {
  "run-1": [
    runNode("a-01", "a", "FAILED", "NODE", "NODE_PRECHECK_REJECTED"),
    runNode("c-01", "c", "BLOCKED", "NODE", "", ["a-01"]),
  ],
  "run-2": [
    runNode("a-01", "a", "FAILED", "GLOBAL", "SAFETY_LATCH"),
    runNode("c-01", "c", "BLOCKED", "GLOBAL", "", ["a-01"]),
  ],
};

export const runEvents: Record<string, unknown[]> = {
  "run-1": [
    {
      event_id: 1,
      sim_time: 0.0,
      kind: "TASK_START",
      task_id: "a_uid1",
      uid: 1,
      detail: {},
    },
    {
      event_id: 2,
      sim_time: 2.0,
      kind: "TASK_FAILED",
      task_id: "a_uid1",
      uid: 1,
      detail: { failure_scope: "NODE", failure_code: "NODE_PRECHECK_REJECTED" },
    },
  ],
  "run-2": [
    {
      event_id: 1,
      sim_time: 0.0,
      kind: "FAILURE",
      detail: { failure_scope: "GLOBAL", failure_code: "SAFETY_LATCH" },
    },
  ],
};

export async function installMocks(page: Page): Promise<void> {
  await page.route(
    /\/api\/(?:health|templates|orders|planner|runs)(?:\/|$)/,
    async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const fulfill = (payload: unknown, status = 200) =>
      route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify(payload),
      });

    if (path === "/api/health") {
      return fulfill({
        ok: true,
        service: "order",
        contract_version: "v1_order_20261106",
        planner_policy_version: "v1_planner_20261107",
        catalog_version: "v1_process_mock",
        scene_id: "L5_UFLOW",
      });
    }
    if (path === "/api/templates") {
      return fulfill({ ok: true, ...catalog });
    }
    if (path === "/api/planner/plan" && method === "POST") {
      return fulfill({ ok: true, plan: plannerResult });
    }
    if (path === "/api/planner/drafts" && method === "POST") {
      return fulfill({
        ok: true,
        draft: {
          ok: true,
          plan: plannerResult,
          order: orderHeader("planner-order", 1, "planner"),
          saved_order: {
            order_id: "planner-order",
            version: 1,
            dag_digest: PLANNER_DIGEST,
            plan_digest: PLANNER_DIGEST,
          },
        },
      });
    }
    if (path === "/api/orders" && method === "GET") {
      return fulfill({
        ok: true,
        orders: [orderHeader("order-1"), orderHeader("planner-order", 1, "planner")],
      });
    }
    if (path === "/api/orders" && method === "POST") {
      const body = JSON.parse(request.postData() ?? "{}");
      return fulfill({
        ok: true,
        order: orderHeader(body.order_id ?? "new-order"),
      });
    }
    if (path === "/api/orders/preview" && method === "POST") {
      return fulfill({ ok: true, preview });
    }
    const orderMatch = path.match(/^\/api\/orders\/([^/]+)$/);
    if (orderMatch && method === "GET") {
      const orderId = decodeURIComponent(orderMatch[1]);
      return fulfill({
        ok: true,
        order: orderHeader(
          orderId,
          orderId === "planner-order" ? 1 : 1,
          orderId === "planner-order" ? "planner" : "manual",
        ),
      });
    }
    const versionMatch = path.match(
      /^\/api\/orders\/([^/]+)\/versions\/(\d+)$/,
    );
    if (versionMatch && method === "GET") {
      const orderId = decodeURIComponent(versionMatch[1]);
      const version = Number(versionMatch[2]);
      return fulfill({
        ok: true,
        version: versionDetail(
          orderId,
          version,
          orderId === "planner-order" ? "planner" : "manual",
        ),
      });
    }
    const versionsMatch = path.match(
      /^\/api\/orders\/([^/]+)\/versions$/,
    );
    if (versionsMatch && method === "GET") {
      const orderId = decodeURIComponent(versionsMatch[1]);
      return fulfill({
        ok: true,
        versions: [versionMeta(orderId, 1, "manual")],
      });
    }
    if (versionsMatch && method === "POST") {
      const orderId = decodeURIComponent(versionsMatch[1]);
      return fulfill({
        ok: true,
        order: orderHeader(orderId, 2),
      });
    }
    const previewMatch = path.match(
      /^\/api\/orders\/([^/]+)\/versions\/(\d+)\/preview$/,
    );
    if (previewMatch && method === "POST") {
      return fulfill({ ok: true, preview });
    }
    const startMatch = path.match(
      /^\/api\/orders\/([^/]+)\/versions\/(\d+)\/start$/,
    );
    if (startMatch && method === "POST") {
      return fulfill({ ok: true, run: runs["run-1"] });
    }
    const runsMatch = path.match(/^\/api\/orders\/([^/]+)\/runs$/);
    if (runsMatch && method === "GET") {
      return fulfill({ ok: true, runs: [] });
    }
    const runMatch = path.match(/^\/api\/runs\/([^/]+)$/);
    if (runMatch && method === "GET") {
      const runId = decodeURIComponent(runMatch[1]);
      const run = runs[runId];
      if (!run) return fulfill({ ok: false, error: { code: "NOT_FOUND" } }, 404);
      return fulfill({
        ok: true,
        run: { ...run, nodes: runNodes[runId] },
      });
    }
    const runNodesMatch = path.match(/^\/api\/runs\/([^/]+)\/nodes$/);
    if (runNodesMatch && method === "GET") {
      return fulfill({
        ok: true,
        nodes: runNodes[decodeURIComponent(runNodesMatch[1])] ?? [],
      });
    }
    const runEventsMatch = path.match(/^\/api\/runs\/([^/]+)\/events$/);
    if (runEventsMatch && method === "GET") {
      return fulfill({
        ok: true,
        events: runEvents[decodeURIComponent(runEventsMatch[1])] ?? [],
      });
    }
    const stopMatch = path.match(/^\/api\/runs\/([^/]+)\/stop$/);
    if (stopMatch && method === "POST") {
      const runId = decodeURIComponent(stopMatch[1]);
      return fulfill({ ok: true, run: runs[runId] });
    }
    return fulfill(
      {
        ok: false,
        error: { code: "MOCK_NOT_FOUND", message: `未模拟 ${method} ${path}` },
      },
      404,
    );
  });
}
