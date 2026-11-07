import { describe, expect, it } from "vitest";
import {
  ORDER_STATUS_LABELS,
  failureScopeLabel,
  isActiveRunStatus,
  isTerminalRunStatus,
  nodeStateTone,
  orderStatusTone,
  shortDigest,
} from "./status";

describe("status presentation", () => {
  it("keeps order terminal status separate from active status", () => {
    expect(isTerminalRunStatus("SUCCEEDED")).toBe(true);
    expect(isTerminalRunStatus("PARTIAL_FAILED")).toBe(true);
    expect(isTerminalRunStatus("RUNNING")).toBe(false);
    expect(isActiveRunStatus("STARTING")).toBe(true);
    expect(isActiveRunStatus("ABORTED")).toBe(false);
  });

  it("maps failure and blocked states to distinct tones/labels", () => {
    expect(nodeStateTone("FAILED")).toBe("failure");
    expect(nodeStateTone("BLOCKED")).toBe("blocked");
    expect(nodeStateTone("DONE")).toBe("success");
    expect(orderStatusTone("PARTIAL_FAILED")).toBe("warning");
    expect(ORDER_STATUS_LABELS.ABORTED).toBe("全局中止");
    expect(failureScopeLabel("NODE")).toBe("节点局部");
    expect(failureScopeLabel("")).toBe("未记录");
  });

  it("shortens digests without hiding short values", () => {
    expect(shortDigest("abcdef", 12)).toBe("abcdef");
    expect(shortDigest("a".repeat(64), 12)).toHaveLength(13);
  });
});
