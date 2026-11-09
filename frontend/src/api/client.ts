import type {
  CompilePreview,
  Diagnostic,
  OrderHeader,
  OrderSpec,
  OrderSummary,
  OrderVersionDetail,
  PlannerDraftResponse,
  PlannerInputPayload,
  PlannerResult,
  RunDetail,
  RunEvent,
  RunMeta,
  RunNode,
  StartRunBody,
  TemplateCatalog,
  VersionMeta,
} from "./types";

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
}

export const api = {
  health: () => request<HealthResponse>("/health"),
  templates: () => request<TemplateCatalog>("/templates"),

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
  preview: (spec: OrderSpec) =>
    request<{ preview: CompilePreview }>("/orders/preview", {
      method: "POST",
      body: spec,
    }),
  previewVersion: (orderId: string, version: number) =>
    request<{ preview: CompilePreview }>(
      `/orders/${encodeURIComponent(orderId)}/versions/${version}/preview`,
      { method: "POST" },
    ),
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
