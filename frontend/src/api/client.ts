import type {
  CompilePreview,
  Diagnostic,
  FleetStatus,
  ItemType,
  MaterialPort,
  OrderHeader,
  OrderSpec,
  OrderSummary,
  OrderTarget,
  OrderVariantBranch,
  OrderVariantCatalog,
  OrderVariantCreateBody,
  OrderVariantCreateResponse,
  OrderVariantDirectInput,
  OrderVariantScope,
  OrderVariantSummary,
  OrderVariantVerification,
  OrderVersionDetail,
  PlannerDraftResponse,
  PlannerInputPayload,
  PlannerResult,
  RouteSummary,
  RunDetail,
  RunEvent,
  RunMeta,
  RunNode,
  SceneProfile,
  SceneZoneSummary,
  StartRunBody,
  TemplateCatalog,
  TemplateSummary,
  TransportFleet,
  VersionMeta,
} from "./types";
import { KNOWN_ITEM_TYPES, SCENE_PROFILES } from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE_URL || "/api").replace(
  /\/$/,
  "",
);
const DEFAULT_TIMEOUT_MS = 15_000;

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly path: string;
  readonly diagnostics: Diagnostic[];

  constructor(
    status: number,
    code: string,
    message: string,
    options: { path?: string; diagnostics?: Diagnostic[] } = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.path = options.path ?? "";
    this.diagnostics = options.diagnostics ?? [];
  }
}

interface ErrorEnvelope {
  error?: {
    code?: string;
    message?: string;
    path?: string;
    diagnostics?: Diagnostic[];
  };
}

interface RequestOptions {
  method?: "GET" | "POST";
  body?: unknown;
  timeoutMs?: number;
  signal?: AbortSignal;
}

function isErrorEnvelope(payload: unknown): payload is ErrorEnvelope {
  return (
    typeof payload === "object" &&
    payload !== null &&
    "error" in payload &&
    typeof (payload as ErrorEnvelope).error === "object"
  );
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(
    () => controller.abort(),
    options.timeoutMs ?? DEFAULT_TIMEOUT_MS,
  );
  const abortFromCaller = () => controller.abort();
  options.signal?.addEventListener("abort", abortFromCaller);
  try {
    let response: Response;
    try {
      response = await fetch(`${API_BASE}${path}`, {
        method: options.method ?? "GET",
        headers:
          options.body === undefined
            ? { Accept: "application/json" }
            : {
                Accept: "application/json",
                "Content-Type": "application/json",
              },
        body:
          options.body === undefined
            ? undefined
            : JSON.stringify(options.body),
        signal: controller.signal,
      });
    } catch (error) {
      if (controller.signal.aborted) {
        throw new ApiError(
          0,
          options.signal?.aborted ? "ABORTED" : "TIMEOUT",
          options.signal?.aborted
            ? "请求已取消"
            : "订单服务请求超时；请确认本机 API 是否可用",
        );
      }
      throw new ApiError(
        0,
        "NETWORK",
        `无法连接订单服务：${error instanceof Error ? error.message : String(error)}`,
      );
    }

    const text = await response.text();
    let payload: unknown = null;
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch {
        throw new ApiError(
          response.status,
          "INVALID_RESPONSE",
          `订单服务返回了非 JSON 响应（HTTP ${response.status}）`,
        );
      }
    }
    if (!response.ok || isErrorEnvelope(payload)) {
      const envelope = isErrorEnvelope(payload) ? payload.error : undefined;
      throw new ApiError(
        response.status,
        envelope?.code ?? `HTTP_${response.status}`,
        envelope?.message ?? `订单服务请求失败（HTTP ${response.status}）`,
        {
          path: envelope?.path,
          diagnostics: envelope?.diagnostics,
        },
      );
    }
    return payload as T;
  } finally {
    window.clearTimeout(timeout);
    options.signal?.removeEventListener("abort", abortFromCaller);
  }
}

export interface HealthResponse {
  ok: boolean;
  service: string;
  contract_version: string;
  planner_policy_version: string;
  catalog_version: string;
  scene_id: string;
  scene_profile?: string;
  profile_source?: string;
  scene_profiles?: string[];
  scene_profile_version?: string;
  order_variant_version?: string;
  transport_fleets?: string[];
  fleet_policy_version?: string;
  path_reuse_modes?: string[];
  default_path_reuse?: string;
}

// ── 目录解析边界 ──────────────────────────────────────────────
// 服务端目录是运行时物料/模板选择的唯一来源。这里在解析边界拒绝未知
// 物料类型和不完整结构, 避免不认识的值静默进入订单草稿.

const KNOWN_ITEM_TYPE_SET = new Set<string>(KNOWN_ITEM_TYPES);
const KNOWN_SCENE_PROFILE_SET = new Set<string>(SCENE_PROFILES);

function invalidCatalog(message: string): ApiError {
  return new ApiError(200, "INVALID_RESPONSE", `模板目录无效：${message}`);
}

function catalogRecord(value: unknown, path: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw invalidCatalog(`${path} 必须是对象`);
  }
  return value as Record<string, unknown>;
}

function catalogString(
  value: unknown,
  path: string,
  { allowEmpty = false }: { allowEmpty?: boolean } = {},
): string {
  if (typeof value !== "string" || (!allowEmpty && value.trim() === "")) {
    throw invalidCatalog(`${path} 必须是非空字符串`);
  }
  return value;
}

function catalogBoolean(value: unknown, path: string): boolean {
  if (typeof value !== "boolean") {
    throw invalidCatalog(`${path} 必须是布尔值`);
  }
  return value;
}

function catalogInteger(value: unknown, path: string): number {
  if (typeof value !== "number" || !Number.isInteger(value)) {
    throw invalidCatalog(`${path} 必须是整数`);
  }
  return value;
}

function catalogArray(value: unknown, path: string): unknown[] {
  if (!Array.isArray(value)) {
    throw invalidCatalog(`${path} 必须是数组`);
  }
  return value;
}

function catalogStringArray(value: unknown, path: string): string[] {
  return catalogArray(value, path).map((item, index) =>
    catalogString(item, `${path}[${index}]`),
  );
}

function catalogItemType(value: unknown, path: string): ItemType {
  const text = catalogString(value, path);
  if (!KNOWN_ITEM_TYPE_SET.has(text)) {
    throw invalidCatalog(`${path} 含未知物料类型 ${text}`);
  }
  return text as ItemType;
}

function catalogItemTypes(value: unknown, path: string): ItemType[] {
  return catalogArray(value, path).map((item, index) =>
    catalogItemType(item, `${path}[${index}]`),
  );
}

function catalogPort(value: unknown, path: string): MaterialPort {
  const row = catalogRecord(value, path);
  return {
    port_id: catalogString(row.port_id, `${path}.port_id`),
    zone: catalogString(row.zone, `${path}.zone`),
    item_type: catalogItemType(row.item_type, `${path}.item_type`),
  };
}

function catalogTemplate(value: unknown, path: string): TemplateSummary {
  const row = catalogRecord(value, path);
  const output = row.output;
  return {
    template_id: catalogString(row.template_id, `${path}.template_id`),
    kind: catalogString(row.kind, `${path}.kind`),
    semantics: catalogString(row.semantics, `${path}.semantics`),
    executor_kind: catalogString(row.executor_kind, `${path}.executor_kind`),
    capability: catalogString(row.capability, `${path}.capability`),
    candidate_devices: catalogStringArray(
      row.candidate_devices,
      `${path}.candidate_devices`,
    ),
    resources: catalogStringArray(row.resources, `${path}.resources`),
    inputs: catalogArray(row.inputs, `${path}.inputs`).map((port, index) =>
      catalogPort(port, `${path}.inputs[${index}]`),
    ),
    output:
      output === null || output === undefined
        ? null
        : catalogPort(output, `${path}.output`),
    source_kind: catalogString(row.source_kind, `${path}.source_kind`),
    sink_kind: catalogString(row.sink_kind, `${path}.sink_kind`),
    work_steps: catalogStringArray(row.work_steps, `${path}.work_steps`),
    completion_verifier: catalogString(
      row.completion_verifier,
      `${path}.completion_verifier`,
    ),
    join_handler_id: catalogString(
      row.join_handler_id,
      `${path}.join_handler_id`,
      { allowEmpty: true },
    ),
    predecessor_policy: catalogString(
      row.predecessor_policy,
      `${path}.predecessor_policy`,
    ),
    scene_id: catalogString(row.scene_id, `${path}.scene_id`, {
      allowEmpty: true,
    }),
  };
}

function catalogRoute(value: unknown, path: string): RouteSummary {
  const row = catalogRecord(value, path);
  return {
    route_id: catalogString(row.route_id, `${path}.route_id`),
    group: catalogString(row.group, `${path}.group`),
    semantics: catalogString(row.semantics, `${path}.semantics`),
    operation_ids: catalogStringArray(
      row.operation_ids,
      `${path}.operation_ids`,
    ),
    entry_zone: catalogString(row.entry_zone, `${path}.entry_zone`),
    exit_zone: catalogString(row.exit_zone, `${path}.exit_zone`),
  };
}

function catalogZone(value: unknown, path: string): SceneZoneSummary {
  const row = catalogRecord(value, path);
  return {
    id: catalogString(row.id, `${path}.id`),
    capacity: catalogInteger(row.capacity, `${path}.capacity`),
    description: catalogString(row.description, `${path}.description`, {
      allowEmpty: true,
    }),
    spawnable: catalogBoolean(row.spawnable, `${path}.spawnable`),
    item_types: catalogStringArray(row.item_types, `${path}.item_types`).map(
      (item, index) => {
        const text = catalogString(item, `${path}.item_types[${index}]`);
        if (!KNOWN_ITEM_TYPE_SET.has(text)) {
          throw invalidCatalog(
            `${path}.item_types[${index}] 含未知物料类型 ${text}`,
          );
        }
        return text;
      },
    ),
  };
}

function catalogProfile(value: unknown, path: string): SceneProfile {
  const text = catalogString(value, path);
  if (!KNOWN_SCENE_PROFILE_SET.has(text)) {
    throw invalidCatalog(`${path} 含未知场景 profile ${text}`);
  }
  return text as SceneProfile;
}

export function parseTemplateCatalog(payload: unknown): TemplateCatalog {
  const row = catalogRecord(payload, "catalog");
  const profileSource = catalogString(
    row.profile_source,
    "catalog.profile_source",
  );
  if (!["manifest", "default", "unbound"].includes(profileSource)) {
    throw invalidCatalog(
      `catalog.profile_source 未知值 ${profileSource}`,
    );
  }
  const sceneProfileRaw = catalogString(
    row.scene_profile,
    "catalog.scene_profile",
    { allowEmpty: true },
  );
  const sceneProfile =
    sceneProfileRaw === ""
      ? ""
      : catalogProfile(sceneProfileRaw, "catalog.scene_profile");
  const itemTypes = catalogRecord(row.item_types, "catalog.item_types");
  return {
    catalog_version: catalogString(
      row.catalog_version,
      "catalog.catalog_version",
    ),
    contract_version: catalogString(
      row.contract_version,
      "catalog.contract_version",
    ),
    scene_id: catalogString(row.scene_id, "catalog.scene_id"),
    artifact_id: catalogString(row.artifact_id, "catalog.artifact_id", {
      allowEmpty: true,
    }),
    scene_profile: sceneProfile,
    profile_source: profileSource as TemplateCatalog["profile_source"],
    profile_version: catalogString(
      row.profile_version,
      "catalog.profile_version",
    ),
    profiles: catalogArray(row.profiles, "catalog.profiles").map(
      (item, index) => catalogProfile(item, `catalog.profiles[${index}]`),
    ),
    item_types: {
      targets: catalogItemTypes(
        itemTypes.targets,
        "catalog.item_types.targets",
      ),
      inputs: catalogItemTypes(itemTypes.inputs, "catalog.item_types.inputs"),
    },
    zones: catalogArray(row.zones, "catalog.zones").map((zone, index) =>
      catalogZone(zone, `catalog.zones[${index}]`),
    ),
    templates: catalogArray(row.templates, "catalog.templates").map(
      (template, index) =>
        catalogTemplate(template, `catalog.templates[${index}]`),
    ),
    routes: catalogArray(row.routes, "catalog.routes").map((route, index) =>
      catalogRoute(route, `catalog.routes[${index}]`),
    ),
  };
}

// ── 订单方案目录解析边界 ────────────────────────────────────

function variantScope(value: unknown, path: string): OrderVariantScope {
  const text = catalogString(value, path);
  if (!["production", "intermediate", "transit"].includes(text)) {
    throw invalidCatalog(`${path} 未知方案层级 ${text}`);
  }
  return text as OrderVariantScope;
}

function variantVerification(
  value: unknown,
  path: string,
): OrderVariantVerification {
  const text = catalogString(value, path);
  if (!["real_run", "planner_test", "compiler_only"].includes(text)) {
    throw invalidCatalog(`${path} 未知验证级别 ${text}`);
  }
  return text as OrderVariantVerification;
}

function variantTransportFleet(
  value: unknown,
  path: string,
): TransportFleet | "" {
  const text = catalogString(value, path, { allowEmpty: true });
  if (text !== "" && text !== "single" && text !== "dual") {
    throw invalidCatalog(`${path} 未知车队提示 ${text}`);
  }
  return text;
}

function variantTarget(value: unknown, path: string): OrderTarget {
  const row = catalogRecord(value, path);
  return {
    item_type: catalogItemType(row.item_type, `${path}.item_type`),
    quantity: catalogInteger(row.quantity, `${path}.quantity`),
  };
}

function variantBranch(value: unknown, path: string): OrderVariantBranch {
  const row = catalogRecord(value, path);
  const outputType = row.output_item_type;
  return {
    branch_id: catalogString(row.branch_id, `${path}.branch_id`),
    entry_item_type: catalogItemType(
      row.entry_item_type,
      `${path}.entry_item_type`,
    ),
    entry_zone: catalogString(row.entry_zone, `${path}.entry_zone`),
    steps: catalogArray(row.steps, `${path}.steps`).map((step, index) => {
      const item = catalogRecord(step, `${path}.steps[${index}]`);
      return {
        template_id: catalogString(
          item.template_id,
          `${path}.steps[${index}].template_id`,
        ),
        to_port: catalogString(
          item.to_port,
          `${path}.steps[${index}].to_port`,
        ),
      };
    }),
    template_ids: catalogStringArray(
      row.template_ids,
      `${path}.template_ids`,
    ),
    output_zone: catalogString(row.output_zone, `${path}.output_zone`),
    output_item_type:
      outputType === null || outputType === undefined
        ? null
        : catalogItemType(outputType, `${path}.output_item_type`),
  };
}

function variantDirectInput(
  value: unknown,
  path: string,
): OrderVariantDirectInput {
  const row = catalogRecord(value, path);
  return {
    port_id: catalogString(row.port_id, `${path}.port_id`),
    item_type: catalogItemType(row.item_type, `${path}.item_type`),
    zone: catalogString(row.zone, `${path}.zone`),
  };
}

function parseOrderVariantSummary(
  value: unknown,
  path: string,
): OrderVariantSummary {
  const row = catalogRecord(value, path);
  return {
    variant_id: catalogString(row.variant_id, `${path}.variant_id`),
    display_name: catalogString(row.display_name, `${path}.display_name`),
    description: catalogString(row.description, `${path}.description`, {
      allowEmpty: true,
    }),
    scene_profile: catalogProfile(row.scene_profile, `${path}.scene_profile`),
    scope: variantScope(row.scope, `${path}.scope`),
    verification: variantVerification(
      row.verification,
      `${path}.verification`,
    ),
    target: variantTarget(row.target, `${path}.target`),
    inputs: catalogArray(row.inputs, `${path}.inputs`).map((input, index) => {
      const item = catalogRecord(input, `${path}.inputs[${index}]`);
      return {
        item_type: catalogItemType(
          item.item_type,
          `${path}.inputs[${index}].item_type`,
        ),
        zone: catalogString(item.zone, `${path}.inputs[${index}].zone`),
        quantity: catalogInteger(
          item.quantity,
          `${path}.inputs[${index}].quantity`,
        ),
      };
    }),
    branches: catalogArray(row.branches, `${path}.branches`).map(
      (branch, index) =>
        variantBranch(branch, `${path}.branches[${index}]`),
    ),
    direct_inputs: catalogArray(
      row.direct_inputs,
      `${path}.direct_inputs`,
    ).map((input, index) =>
      variantDirectInput(input, `${path}.direct_inputs[${index}]`),
    ),
    template_ids: catalogStringArray(
      row.template_ids,
      `${path}.template_ids`,
    ),
    route_ids: catalogStringArray(row.route_ids, `${path}.route_ids`),
    join_template_id: catalogString(
      row.join_template_id,
      `${path}.join_template_id`,
      { allowEmpty: true },
    ),
    join_ports: catalogArray(row.join_ports, `${path}.join_ports`).map(
      (port, index) => {
        const item = catalogRecord(
          port,
          `${path}.join_ports[${index}]`,
        );
        return {
          branch_id: catalogString(
            item.branch_id,
            `${path}.join_ports[${index}].branch_id`,
          ),
          port_id: catalogString(
            item.port_id,
            `${path}.join_ports[${index}].port_id`,
          ),
        };
      },
    ),
    output_zone: catalogString(row.output_zone, `${path}.output_zone`),
    used_arms: catalogStringArray(row.used_arms, `${path}.used_arms`),
    used_transports: catalogStringArray(
      row.used_transports,
      `${path}.used_transports`,
    ),
    difference_from: catalogString(
      row.difference_from,
      `${path}.difference_from`,
      { allowEmpty: true },
    ),
    difference_summary: catalogString(
      row.difference_summary,
      `${path}.difference_summary`,
      { allowEmpty: true },
    ),
    recommended_transport_fleet: variantTransportFleet(
      row.recommended_transport_fleet,
      `${path}.recommended_transport_fleet`,
    ),
    route_complete: catalogBoolean(
      row.route_complete,
      `${path}.route_complete`,
    ),
    available: catalogBoolean(row.available, `${path}.available`),
    unavailable_reason: catalogString(
      row.unavailable_reason,
      `${path}.unavailable_reason`,
      { allowEmpty: true },
    ),
  };
}

export function parseOrderVariantCatalog(
  payload: unknown,
): OrderVariantCatalog {
  const row = catalogRecord(payload, "variants");
  const profileSource = catalogString(
    row.profile_source,
    "variants.profile_source",
  );
  if (!["manifest", "default", "unbound"].includes(profileSource)) {
    throw invalidCatalog(
      `variants.profile_source 未知值 ${profileSource}`,
    );
  }
  const sceneProfileRaw = catalogString(
    row.scene_profile,
    "variants.scene_profile",
    { allowEmpty: true },
  );
  const sceneProfile =
    sceneProfileRaw === ""
      ? ""
      : catalogProfile(sceneProfileRaw, "variants.scene_profile");
  return {
    artifact_id: catalogString(row.artifact_id, "variants.artifact_id", {
      allowEmpty: true,
    }),
    scene_profile: sceneProfile,
    profile_source: profileSource as OrderVariantCatalog["profile_source"],
    variant_version: catalogString(
      row.variant_version,
      "variants.variant_version",
    ),
    variants: catalogArray(row.variants, "variants.variants").map(
      (variant, index) =>
        parseOrderVariantSummary(
          variant,
          `variants.variants[${index}]`,
        ),
    ),
  };
}

export const api = {
  health: () => request<HealthResponse>("/health"),
  templates: async (artifact?: string) => {
    const query = artifact
      ? `?artifact_id=${encodeURIComponent(artifact)}`
      : "";
    const payload = await request<unknown>(`/templates${query}`);
    return parseTemplateCatalog(payload);
  },
  orderVariants: async (artifact?: string) => {
    const query = artifact
      ? `?artifact_id=${encodeURIComponent(artifact)}`
      : "";
    const payload = await request<unknown>(`/order-variants${query}`);
    return parseOrderVariantCatalog(payload);
  },
  createOrderFromVariant: (
    variantId: string,
    body: OrderVariantCreateBody,
    artifact?: string,
  ) => {
    const query = artifact
      ? `?artifact_id=${encodeURIComponent(artifact)}`
      : "";
    return request<OrderVariantCreateResponse>(
      `/order-variants/${encodeURIComponent(variantId)}/orders${query}`,
      { method: "POST", body },
    );
  },
  fleet: (artifact?: string) => {
    const query = artifact ? `?artifact_id=${encodeURIComponent(artifact)}` : "";
    return request<{ fleet: FleetStatus }>(`/fleet${query}`);
  },

  listOrders: (limit = 50) =>
    request<{ orders: OrderSummary[] }>(`/orders?limit=${limit}`),
  getOrder: (orderId: string) =>
    request<{ order: OrderHeader }>(`/orders/${encodeURIComponent(orderId)}`),
  listVersions: (orderId: string) =>
    request<{ versions: VersionMeta[] }>(
      `/orders/${encodeURIComponent(orderId)}/versions`,
    ),
  getVersion: (orderId: string, version: number) =>
    request<{ version: OrderVersionDetail }>(
      `/orders/${encodeURIComponent(orderId)}/versions/${version}`,
    ),
  createOrder: (spec: OrderSpec) =>
    request<{ order: OrderHeader }>("/orders", {
      method: "POST",
      body: spec,
    }),
  createVersion: (orderId: string, spec: OrderSpec) =>
    request<{ order: OrderHeader }>(
      `/orders/${encodeURIComponent(orderId)}/versions`,
      { method: "POST", body: spec },
    ),
  preview: (spec: OrderSpec, artifact?: string) => {
    const query = artifact
      ? `?artifact_id=${encodeURIComponent(artifact)}`
      : "";
    return request<{ preview: CompilePreview }>(`/orders/preview${query}`, {
      method: "POST",
      body: spec,
    });
  },
  previewVersion: (orderId: string, version: number, artifact?: string) => {
    const query = artifact
      ? `?artifact_id=${encodeURIComponent(artifact)}`
      : "";
    return request<{ preview: CompilePreview }>(
      `/orders/${encodeURIComponent(orderId)}/versions/${version}/preview${query}`,
      { method: "POST" },
    );
  },
  start: (orderId: string, version: number, body: StartRunBody) =>
    request<{ run: RunDetail }>(
      `/orders/${encodeURIComponent(orderId)}/versions/${version}/start`,
      { method: "POST", body },
    ),
  listOrderRuns: (orderId: string, limit = 50) =>
    request<{ runs: RunMeta[] }>(
      `/orders/${encodeURIComponent(orderId)}/runs?limit=${limit}`,
    ),
  getRun: (runId: string) =>
    request<{ run: RunDetail }>(`/runs/${encodeURIComponent(runId)}`),
  getRunNodes: (runId: string) =>
    request<{ nodes: RunNode[] }>(`/runs/${encodeURIComponent(runId)}/nodes`),
  getRunEvents: (runId: string, limit = 2000) =>
    request<{ events: RunEvent[] }>(
      `/runs/${encodeURIComponent(runId)}/events?limit=${limit}`,
    ),
  stopRun: (runId: string) =>
    request<{ run: RunDetail }>(`/runs/${encodeURIComponent(runId)}/stop`, {
      method: "POST",
    }),
  plan: (payload: PlannerInputPayload) =>
    request<{ plan: PlannerResult }>("/planner/plan", {
      method: "POST",
      body: payload,
    }),
  createPlannerDraft: (payload: PlannerInputPayload) =>
    request<{ draft: PlannerDraftResponse }>("/planner/drafts", {
      method: "POST",
      body: payload,
    }),
};

export type ApiClient = typeof api;
