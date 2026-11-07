export const ITEM_TYPES = [
  "RAW_A",
  "RAW_B",
  "SEMI_AB",
  "SEMI_AC",
  "SEMI_CE",
  "RAW_D",
  "PART_DE",
  "FINAL",
] as const;

export type ItemType = (typeof ITEM_TYPES)[number];
export type OrderSource = "manual" | "planner" | "fixture";
export type OrderRunStatus =
  | "STARTING"
  | "RUNNING"
  | "SUCCEEDED"
  | "PARTIAL_FAILED"
  | "FAILED"
  | "ABORTED";
export type NodeState =
  | "PENDING"
  | "WAITING"
  | "RUNNING"
  | "BLOCKED"
  | "DONE"
  | "FAILED"
  | "REJECTED"
  | "CANCELLED";

export interface MaterialPort {
  port_id: string;
  zone: string;
  item_type: ItemType;
}

export interface TemplateSummary {
  template_id: string;
  kind: string;
  semantics: string;
  executor_kind: string;
  capability: string;
  candidate_devices: string[];
  resources: string[];
  inputs: MaterialPort[];
  output: MaterialPort | null;
  source_kind: string;
  sink_kind: string;
  work_steps: string[];
  completion_verifier: string;
  join_handler_id: string;
  predecessor_policy: string;
  scene_id: string;
}

export interface RouteSummary {
  route_id: string;
  group: string;
  semantics: string;
  operation_ids: string[];
  entry_zone: string;
  exit_zone: string;
}

export interface TemplateCatalog {
  catalog_version: string;
  contract_version: string;
  scene_id: string;
  templates: TemplateSummary[];
  routes: RouteSummary[];
}

export interface OrderTarget {
  item_type: ItemType;
  quantity: number;
}

export interface OrderInput {
  input_id: string;
  item_type: ItemType;
  quantity: number;
  zone: string;
  node_id: string;
  port_id: string;
}

export interface OrderNode {
  node_id: string;
  template_id: string;
}

export interface OrderEdge {
  from: string;
  to: string;
  to_port: string;
  from_port: string;
}

export interface OrderSpec {
  contract_version: string;
  order_id: string;
  version: number;
  scene_id: string;
  source: OrderSource;
  target: OrderTarget;
  inputs: OrderInput[];
  nodes: OrderNode[];
  edges: OrderEdge[];
}

export interface VersionMeta {
  order_id: string;
  version: number;
  contract_version: string;
  scene_id: string;
  source: OrderSource;
  dag_digest: string;
  catalog_version: string;
  parent_version: number | null;
  planner_policy_version: string;
  planner_candidate_count: number | null;
  created_at: string;
  note: string;
}

export interface RunMeta {
  run_id: string;
  order_id: string;
  order_version: number;
  dag_digest: string;
  artifact_id: string;
  status: OrderRunStatus;
  failure_scope: "" | "NODE" | "GLOBAL";
  stop_reason: string;
  started_at: string;
  finished_at: string;
  result: Record<string, unknown> | null;
}

export interface OrderHeader {
  order_id: string;
  current_version: number;
  source: OrderSource;
  created_at: string;
  updated_at: string;
  note: string;
  versions: VersionMeta[];
  latest_run: RunMeta | null;
}

export interface StoredPlanner {
  policy_version: string;
  input: Record<string, unknown> | null;
  explain: Record<string, unknown> | null;
  candidate_count: number | null;
}

export interface OrderVersionDetail extends VersionMeta {
  spec: OrderSpec;
  planner: StoredPlanner | null;
}

export interface Diagnostic {
  code: string;
  message: string;
  node_id: string;
  input_id: string;
  edge_index: number;
  port_id: string;
}

export interface PreviewNode {
  node_id: string;
  template_id: string;
  available: boolean;
  semantics?: string;
  executor_kind?: string;
  capability?: string;
  candidate_devices?: string[];
  resources?: string[];
  inputs?: MaterialPort[];
  output?: MaterialPort | null;
  source_kind?: string;
  sink_kind?: string;
  completion_verifier?: string;
  work_steps?: string[];
  join_handler_id?: string;
  predecessor_nodes?: string[];
}

export interface MaterialPlan {
  uid: number;
  item_type: ItemType;
  zone: string;
  kind: string;
  input_id: string;
  node_id: string;
  port_id: string;
  producer_instance_id: string;
}

export interface ExpectedOutput {
  instance_id: string;
  node_id: string;
  uid: number;
  item_type: ItemType;
  zone: string;
  path: string[];
}

export interface CompilePreview {
  order_id: string;
  version: number;
  scene_id: string;
  contract_version: string;
  digest: string;
  ok: boolean;
  simulation_started: boolean;
  diagnostics: Diagnostic[];
  topological_order: string[];
  entry_node_ids: string[];
  terminal_target_node_ids: string[];
  nodes: PreviewNode[];
  instance_counts: Record<string, number>;
  material_plans: MaterialPlan[];
  target_instance_ids: string[];
  expected_outputs: ExpectedOutput[];
}

export interface PlannerSupplyPayload {
  item_type: ItemType;
  quantity: number;
  zone?: string;
}

export interface PlannerInputPayload {
  policy_version?: string;
  target_item_type: ItemType;
  target_quantity: number;
  inputs: PlannerSupplyPayload[];
  scene_id?: string;
  allowed_template_ids?: string[];
  allowed_device_ids?: string[];
  preferred_route_ids?: string[];
  allow_high_risk?: boolean;
  max_candidates?: number;
  max_depth?: number;
  max_nodes?: number;
  order_id?: string;
  order_version?: number;
}

export interface PlannerBranch {
  kind: string;
  label: string;
  route_ids: string[];
  template_ids: string[];
  input_requirements: Array<{
    item_type: ItemType;
    zone: string;
    quantity: number;
  }>;
  output_item_type: ItemType;
  output_zone: string;
  operation_count: number;
}

export interface PlannerCandidate {
  candidate_id: string;
  branch: PlannerBranch;
  feasible: boolean;
  rejection_code: string;
  rejection_reason: string;
  operation_count: number;
  route_ids: string[];
  template_ids: string[];
  digest: string;
  score: Array<string | number>;
  input_usage: Array<{
    item_type: ItemType;
    zone: string;
    required: number;
    available: number;
  }>;
}

export interface PlannerDiagnostic {
  code: string;
  message: string;
  category: string;
  severity: "error" | "warning";
  item_type: string;
  route_id: string;
  template_id: string;
  port_id: string;
}

export interface PlannerResult {
  ok: boolean;
  policy_version: string;
  input: Record<string, unknown>;
  first_blocking_category: string;
  diagnostics: PlannerDiagnostic[];
  selected: PlannerCandidate | null;
  selected_order: OrderSpec | null;
  digest: string;
  preview: CompilePreview | null;
  candidates: PlannerCandidate[];
  rejected: PlannerCandidate[];
  explanation: {
    policy_version: string;
    input_summary: Record<string, unknown>;
    selected_candidate_id: string | null;
    selected_branch: PlannerBranch | null;
    selected_order: OrderSpec | null;
    digest: string;
    target_paths: ExpectedOutput[];
    join_bindings: Array<{
      join_template_id: string;
      inputs: Array<{
        port_id: string;
        item_type: ItemType;
        route_ids: string[];
        template_ids: string[];
      }>;
    }>;
    candidates: PlannerCandidate[];
    rejected: PlannerCandidate[];
    search: Record<string, unknown>;
    unmodeled_dimensions: string[];
    first_blocking_category: string;
  };
  search: Record<string, unknown>;
}

export interface RunNode {
  instance_id: string;
  node_id: string;
  template_id: string;
  uid: number;
  state: NodeState;
  failure_scope: "" | "NODE" | "GLOBAL";
  failure_code: string;
  failure_reason: string;
  blocked_by: string[];
  input_uids: number[];
  output_uids: number[];
  evidence: Record<string, unknown>;
  updated_at: string;
}

export interface RunDetail extends RunMeta {
  node_count: number;
  failed_count: number;
  cancelled_count: number;
  nodes: RunNode[];
  live?: Record<string, unknown> | null;
}

export interface RunEvent {
  event_id?: number;
  sim_time: number;
  kind: string;
  task_id?: string;
  uid?: number | null;
  item_type?: string;
  zone?: string;
  source?: string;
  destination?: string;
  device?: string;
  evidence_id?: string;
  instance_id?: string;
  detail?: Record<string, unknown>;
}

export interface StartRunBody {
  artifact_id: string;
  max_sim_time?: number;
}

export interface PlannerDraftResponse {
  ok: boolean;
  plan: PlannerResult;
  order: OrderHeader;
  saved_order: {
    order_id: string;
    version: number;
    dag_digest: string;
    plan_digest: string;
  };
}
