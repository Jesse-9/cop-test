import type {
  NodeState,
  OrderRunStatus,
  OrderSource,
  PlannerDiagnostic,
} from "../api/types";

export const ORDER_STATUS_LABELS: Record<OrderRunStatus, string> = {
  STARTING: "启动中",
  RUNNING: "运行中",
  SUCCEEDED: "成功",
  PARTIAL_FAILED: "部分失败",
  FAILED: "失败",
  ABORTED: "全局中止",
};

export const NODE_STATE_LABELS: Record<NodeState, string> = {
  PENDING: "待执行",
  WAITING: "等待",
  RUNNING: "执行中",
  BLOCKED: "已阻断",
  DONE: "已完成",
  FAILED: "失败",
  REJECTED: "已拒绝",
  CANCELLED: "已取消",
};

export const SOURCE_LABELS: Record<OrderSource, string> = {
  manual: "手工",
  planner: "planner",
  fixture: "fixture",
};

export type Tone = "neutral" | "accent" | "success" | "warning" | "failure" | "blocked";

export function orderStatusTone(status: OrderRunStatus): Tone {
  switch (status) {
    case "SUCCEEDED":
      return "success";
    case "PARTIAL_FAILED":
      return "warning";
    case "FAILED":
    case "ABORTED":
      return "failure";
    case "RUNNING":
      return "accent";
    default:
      return "neutral";
  }
}

export function nodeStateTone(state: NodeState): Tone {
  switch (state) {
    case "DONE":
      return "success";
    case "FAILED":
    case "REJECTED":
      return "failure";
    case "BLOCKED":
      return "blocked";
    case "RUNNING":
      return "accent";
    case "WAITING":
    case "PENDING":
      return "warning";
    default:
      return "neutral";
  }
}

export function isTerminalRunStatus(status: OrderRunStatus): boolean {
  return ["SUCCEEDED", "PARTIAL_FAILED", "FAILED", "ABORTED"].includes(
    status,
  );
}

export function isActiveRunStatus(status: OrderRunStatus): boolean {
  return status === "STARTING" || status === "RUNNING";
}

export function failureScopeLabel(scope: "" | "NODE" | "GLOBAL"): string {
  if (scope === "NODE") return "节点局部";
  if (scope === "GLOBAL") return "全局";
  return "未记录";
}

export function diagnosticSeverity(
  diagnostic: PlannerDiagnostic,
): "error" | "warning" {
  return diagnostic.severity;
}

export function formatTime(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") {
    return "未记录";
  }
  if (typeof value === "number") {
    return `${value.toFixed(2)} s`;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }
  return date.toLocaleString("zh-CN", { hour12: false });
}

export function shortDigest(digest: string, length = 12): string {
  if (!digest) return "未记录";
  return digest.length > length ? `${digest.slice(0, length)}…` : digest;
}
