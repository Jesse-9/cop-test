"""analyze_stage4_run.py — 提取阶段四运行报告的关键验收字段.

用法:
    .venv/Scripts/python docs/measurement/analyze_stage4_run.py \
        docs/implementation/runs/phase4_run_full_flow_<stamp>.json

只读 JSON, 不连接 CoppeliaSim. 输出验收、任务终态、AGV 航线指标、物料
谱系与指标摘要, 供 PHASE4_REPORT 和证据核对使用.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def summarize(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    result = (data.get("results") or [{}])[0]
    coordinator = result.get("coordinator") or {}
    acceptance = result.get("acceptance") or coordinator.get("acceptance") or {}
    tasks = (coordinator.get("tasks") or {}).get("tasks") or []
    routes = coordinator.get("routes") or []
    metrics = coordinator.get("metrics") or {}
    events = coordinator.get("events") or []
    # 用当前实现从事件重算, 避免旧运行报告的指标缓存与修复后的统计
    # 规则不一致 (例如 JOIN_COMMIT 的 task_id 不应生成任务窗口).
    try:
        from stage4.metrics import recompute_metrics
    except ImportError:
        recompute_metrics = None
    if recompute_metrics is not None and events:
        wip = (metrics.get("queues") or {}).get("wip")
        metrics = recompute_metrics(events, wip=wip)
    materials = result.get("materials") or {}
    return {
        "file": str(path),
        "scenario": data.get("scenario"),
        "fault": data.get("fault"),
        "all_ok": data.get("all_ok"),
        "sim_time": result.get("sim_time"),
        "acceptance": acceptance,
        "tasks": [
            {"task_id": task.get("task_id"),
             "kind": task.get("kind"),
             "uid": task.get("uid"),
             "status": task.get("status"),
             "failure_reason": task.get("failure_reason")}
            for task in tasks],
        "routes": [
            {"route_id": row.get("spec", {}).get("route_id"),
             "loaded": row.get("spec", {}).get("loaded"),
             "state": row.get("state"),
             "stop_error": row.get("metrics", {}).get("stop_error"),
             "heading_error": row.get("metrics", {}).get("heading_error"),
             "slip": row.get("metrics", {}).get("material_slip"),
             "duration_s": row.get("metrics", {}).get("duration_s")}
            for row in routes],
        "material_types": {
            uid: {"type": row.get("flow", {}).get("item_type")
                  if row.get("flow") else None,
                  "state": row.get("state"),
                  "zone": (row.get("flow") or {}).get("zone")}
            for uid, row in materials.items()},
        "metrics": {
            "throughput": metrics.get("throughput"),
            "tasks": metrics.get("tasks"),
            "devices": metrics.get("devices"),
            "agv": metrics.get("agv"),
            "window": metrics.get("window"),
            "queues": metrics.get("queues"),
        },
        "runtime_destroy": data.get("runtime_destroy"),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="阶段四运行报告关键字段提取")
    ap.add_argument("path", help="phase4_run_*.json 路径")
    ap.add_argument("--full", action="store_true",
                    help="输出完整 JSON (默认只打印摘要)")
    ns = ap.parse_args(argv)
    summary = summarize(Path(ns.path))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
