"""compare_stage4_process_baseline.py — 阶段四 full_flow 基线归一化与对照.

用法:
    # 从原始运行报告生成基线签名 (只保留可比较字段, 不含时间/句柄)
    .venv/Scripts/python docs/measurement/compare_stage4_process_baseline.py \
        extract docs/implementation/runs/phase4_run_full_flow_<stamp>.json \
        --out docs/implementation/evidence/phase0_1_baseline_full_flow.json

    # 用基线签名对照新运行, 输出差异并返回 0/1
    .venv/Scripts/python docs/measurement/compare_stage4_process_baseline.py \
        compare docs/implementation/runs/phase4_run_full_flow_<new_stamp>.json \
        --baseline docs/implementation/evidence/phase0_1_baseline_full_flow.json \
        --out docs/implementation/evidence/phase0_1_comparison_<stamp>.json

只读 JSON, 不连接 CoppeliaSim. 阶段 0-1 的等价判据是:
  - 输入批次、任务 (kind/uid/终态) 集合、FINAL 数与父 UID;
  - 物料终态 (类型/状态/区域) 与位置 (run-to-run 0.06 m 容差);
  - 实际执行的两条载货路线及其终态;
  - 每个 UID 的关键事件序列 (工序转换、区域提交、配对)。
不比较仿真时间、墙钟、handle、路线停靠误差等允许波动的运行指标.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

ACCEPTANCE_KEYS = (
    "final_count", "final_parents", "unpaired_semi_ce", "tasks_done",
    "any_task_failed", "any_task_blocked", "any_safety_latch",
    "resources_released", "loaded_routes", "route_ok", "ok",
)
# 物料终态位置容差: 单次放置验收容差 0.03 m, 两次运行各自可在
# 目标点两侧偏差, 因此 run-to-run 允许 2 倍容差; 不比较姿态和物理抖动.
MATERIAL_POSE_TOL_M = 0.06
EVENT_KINDS = {
    "MATERIAL_CREATED", "MATERIAL_SPAWN", "MATERIAL_PROCESS",
    "MATERIAL_MOVE", "MATERIAL_PLACE", "MATERIAL_CONSUMED",
    "TASK_CREATED", "TASK_START", "TASK_DONE",
    "ROUTE_START", "ROUTE_DONE", "JOIN_COMMIT", "FINAL_CREATED",
}
DETAIL_KEYS = {
    "TASK_CREATED": ("kind", "priority"),
    "TASK_START": ("kind", "source", "sink", "line"),
    "TASK_DONE": ("kind",),
    "MATERIAL_PROCESS": ("from", "to"),
    "MATERIAL_MOVE": ("from",),
    "MATERIAL_PLACE": ("task_kind",),
    "MATERIAL_CONSUMED": ("pair_key", "final_uid"),
    "JOIN_COMMIT": ("pair_key", "semi_uid", "part_uid", "final_uid"),
    "FINAL_CREATED": ("parents", "pair_key"),
    "ROUTE_START": ("route_id", "loaded", "lanes"),
    "ROUTE_DONE": ("route_id", "loaded"),
}


def _result(data: dict) -> dict:
    results = data.get("results") or []
    return results[0] if results else {}


def _event_signature(event: dict) -> dict:
    detail = event.get("detail") or {}
    kind = event.get("kind", "")
    normalized_detail = {
        key: detail[key] for key in DETAIL_KEYS.get(kind, ())
        if key in detail}
    return {
        "kind": kind,
        "task_id": event.get("task_id", ""),
        "item_type": event.get("item_type", ""),
        "zone": event.get("zone", ""),
        "source": event.get("source", ""),
        "destination": event.get("destination", ""),
        "detail": normalized_detail,
    }


def extract(path: Path) -> dict:
    """提取可比较的运行签名; 不包含时间/句柄/停靠误差."""
    data = json.loads(path.read_text(encoding="utf-8"))
    result = _result(data)
    coordinator = result.get("coordinator") or {}
    acceptance = result.get("acceptance") or coordinator.get(
        "acceptance") or {}
    tasks = (coordinator.get("tasks") or {}).get("tasks") or []
    materials = result.get("materials") or {}
    routes = coordinator.get("routes") or []
    events = coordinator.get("events") or []

    task_rows = sorted(
        [{"kind": task.get("kind", ""),
          "uid": task.get("uid", 0),
          "status": task.get("status", ""),
          "failure_reason": task.get("failure_reason", "")}
         for task in tasks],
        key=lambda row: (row["kind"], row["uid"], row["status"]))

    material_rows = {}
    material_poses = {}
    for uid, row in materials.items():
        flow = row.get("flow") or {}
        material_rows[str(uid)] = {
            "item_type": flow.get("item_type"),
            "state": row.get("state"),
            "zone": flow.get("zone"),
            "parents": flow.get("parents") or [],
        }
        pose = row.get("pose")
        material_poses[str(uid)] = (
            [round(float(value), 6) for value in pose] if pose else None)

    route_rows = sorted(
        [{"route_id": (row.get("spec") or {}).get("route_id", ""),
          "loaded": bool((row.get("spec") or {}).get("loaded")),
          "state": row.get("state", ""),
          "lanes": list((row.get("spec") or {}).get("lanes") or [])}
         for row in routes],
        key=lambda row: (row["route_id"], row["loaded"], row["state"]))

    events_by_uid: dict[str, list[dict]] = {}
    for event in events:
        if event.get("kind") not in EVENT_KINDS:
            continue
        uid = event.get("uid")
        if uid is None:
            continue
        events_by_uid.setdefault(str(uid), []).append(
            _event_signature(event))

    return {
        "source": str(path),
        "artifact_id": data.get("artifact_id"),
        "scenario": data.get("scenario"),
        "fault": data.get("fault"),
        "all_ok": bool(data.get("all_ok")),
        "batch": acceptance.get("batch") or {},
        "acceptance": {key: acceptance.get(key) for key in ACCEPTANCE_KEYS},
        "tasks": task_rows,
        "materials": material_rows,
        "material_poses": material_poses,
        "routes": route_rows,
        "events_by_uid": events_by_uid,
    }


def compare(baseline: dict, current: dict) -> dict:
    """比较两个签名; 返回 ok 与差异列表."""
    differences: list[dict] = []

    def diff(field: str, expected, actual) -> None:
        if expected != actual:
            differences.append({"field": field, "baseline": expected,
                                "current": actual})

    for key in ("artifact_id", "scenario", "fault", "batch", "acceptance",
                "tasks", "materials", "routes"):
        diff(key, baseline.get(key), current.get(key))
    diff("all_ok", baseline.get("all_ok"), current.get("all_ok"))

    pose_deltas: list[dict] = []
    baseline_poses = baseline.get("material_poses") or {}
    current_poses = current.get("material_poses") or {}
    for uid in sorted(set(baseline_poses) | set(current_poses)):
        expected = baseline_poses.get(uid)
        actual = current_poses.get(uid)
        if expected is None or actual is None:
            if expected != actual:
                differences.append({
                    "field": f"material_poses[{uid}]",
                    "baseline": expected, "current": actual})
            continue
        if len(expected) < 3 or len(actual) < 3:
            differences.append({
                "field": f"material_poses[{uid}]",
                "baseline": expected, "current": actual})
            continue
        distance = math.dist(expected[:3], actual[:3])
        pose_deltas.append({"uid": uid,
                            "distance_m": round(distance, 6)})
        if distance > MATERIAL_POSE_TOL_M:
            differences.append({
                "field": f"material_poses[{uid}]",
                "baseline": expected[:3], "current": actual[:3],
                "distance_m": round(distance, 6),
                "tolerance_m": MATERIAL_POSE_TOL_M})

    baseline_events = baseline.get("events_by_uid") or {}
    current_events = current.get("events_by_uid") or {}
    for uid in sorted(set(baseline_events) | set(current_events)):
        diff(f"events_by_uid[{uid}]", baseline_events.get(uid),
             current_events.get(uid))

    return {
        "ok": not differences,
        "baseline_source": baseline.get("source"),
        "current_source": current.get("source"),
        "material_pose_tolerance_m": MATERIAL_POSE_TOL_M,
        "material_pose_deltas": pose_deltas,
        "differences": differences,
        "summary": {
            "acceptance": current.get("acceptance"),
            "task_count": len(current.get("tasks") or []),
            "material_count": len(current.get("materials") or {}),
            "loaded_routes": [
                row["route_id"] for row in current.get("routes") or []
                if row.get("loaded")],
        },
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="阶段四 full_flow 基线对照")
    sub = ap.add_subparsers(dest="command", required=True)

    extract_ap = sub.add_parser("extract", help="生成基线签名")
    extract_ap.add_argument("path")
    extract_ap.add_argument("--out", required=True)

    compare_ap = sub.add_parser("compare", help="对照新运行与基线")
    compare_ap.add_argument("path")
    compare_ap.add_argument("--baseline", required=True)
    compare_ap.add_argument("--out")

    ns = ap.parse_args(argv)
    if ns.command == "extract":
        signature = extract(Path(ns.path))
        Path(ns.out).write_text(
            json.dumps(signature, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(json.dumps({"ok": True, "out": ns.out,
                          "tasks": len(signature["tasks"]),
                          "materials": len(signature["materials"])},
                         ensure_ascii=False))
        return 0

    baseline = json.loads(Path(ns.baseline).read_text(encoding="utf-8"))
    current = extract(Path(ns.path))
    report = compare(baseline, current)
    if ns.out:
        Path(ns.out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
