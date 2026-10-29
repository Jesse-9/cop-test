"""metrics.py — 阶段四事件级节拍/吞吐/WIP/利用率 (06-16 §F4-5).

报告指标全部可由协调器的事件日志重算:
  - 任务: 创建/开始/等待/阻塞/完成时刻与处理/周期时间;
  - 吞吐: FINAL 数量、出件间隔、统计窗口;
  - 设备: 各岛/AGV/物流线的忙碌时长与利用率;
  - 队列: 活动任务数与 WIP 峰值;
  - 背压: 等待原因与累计等待时长.

本模块不导入 sim; 输入是 `FlowEvent.as_dict()` 兼容的普通字典列表.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from . import config as cfg


TERMINAL = {"TASK_DONE", "TASK_FAILED"}
BUSY_START = "TASK_START"
WAIT_START = "TASK_WAIT"
BLOCK_START = "TASK_BLOCK"
UNBLOCK = "TASK_UNBLOCK"


@dataclass
class TaskWindow:
    task_id: str
    kind: str = ""
    uid: Optional[int] = None
    device: str = ""
    created: Optional[float] = None
    started: Optional[float] = None
    finished: Optional[float] = None
    status: str = ""
    wait_s: float = 0.0
    block_s: float = 0.0
    wait_reasons: list[str] = field(default_factory=list)

    def processing_s(self) -> Optional[float]:
        if self.started is None or self.finished is None:
            return None
        return max(0.0, self.finished - self.started)

    def cycle_s(self) -> Optional[float]:
        if self.created is None or self.finished is None:
            return None
        return max(0.0, self.finished - self.created)

    def as_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "kind": self.kind,
            "uid": self.uid,
            "device": self.device,
            "status": self.status,
            "created_sim_time": _round(self.created),
            "started_sim_time": _round(self.started),
            "finished_sim_time": _round(self.finished),
            "processing_s": _round(self.processing_s()),
            "cycle_s": _round(self.cycle_s()),
            "wait_s": round(float(self.wait_s), 4),
            "block_s": round(float(self.block_s), 4),
            "wait_reasons": list(self.wait_reasons),
        }


def _round(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(float(value), 4)


def _event_value(event: dict, *names, default=None):
    for name in names:
        if name in event and event[name] is not None:
            return event[name]
    return default


def collect_task_windows(events: Iterable[dict]) -> dict[str, TaskWindow]:
    windows: dict[str, TaskWindow] = {}
    open_wait: dict[str, float] = {}
    open_block: dict[str, float] = {}

    def get(task_id: str) -> TaskWindow:
        if task_id not in windows:
            windows[task_id] = TaskWindow(task_id=task_id)
        return windows[task_id]

    for event in events:
        kind = str(event.get("kind") or "")
        # 只有 TASK_* 生命周期事件构成任务窗口; JOIN_COMMIT 等业务事件
        # 虽携带 task_id (如 join_e) 供追溯, 但不是任务状态, 不能计入
        # 任务数/等待时间.
        if not kind.startswith("TASK_"):
            continue
        task_id = str(event.get("task_id") or "")
        if not task_id:
            continue
        t = float(event.get("sim_time") or 0.0)
        window = get(task_id)
        window.kind = str(event.get("detail", {}).get("kind")
                          or event.get("detail", {}).get("task_kind")
                          or event.get("task_kind") or window.kind)
        uid = _event_value(event, "uid")
        if uid is not None:
            window.uid = int(uid)
        device = str(event.get("device") or "")
        if device:
            window.device = device

        if kind == "TASK_CREATED":
            window.created = t
        elif kind == "TASK_START":
            window.started = t
            window.status = "RUNNING"
            if task_id in open_wait:
                window.wait_s += max(0.0, t - open_wait.pop(task_id))
            if task_id in open_block:
                window.block_s += max(0.0, t - open_block.pop(task_id))
        elif kind == "TASK_WAIT":
            if task_id not in open_wait:
                open_wait[task_id] = t
            reason = str(event.get("detail", {}).get("reason") or "")
            if reason and reason not in window.wait_reasons:
                window.wait_reasons.append(reason)
            window.status = "WAITING"
        elif kind == "TASK_BLOCK":
            if task_id not in open_block:
                open_block[task_id] = t
            window.status = "BLOCKED"
        elif kind == "TASK_UNBLOCK":
            if task_id in open_block:
                window.block_s += max(0.0, t - open_block.pop(task_id))
            window.status = "RUNNING"
        elif kind in TERMINAL:
            window.finished = t
            window.status = kind.replace("TASK_", "")
            if task_id in open_wait:
                window.wait_s += max(0.0, t - open_wait.pop(task_id))
            if task_id in open_block:
                window.block_s += max(0.0, t - open_block.pop(task_id))
    return windows


def recompute_metrics(events: list[dict], *,
                      window_start: Optional[float] = None,
                      window_end: Optional[float] = None,
                      wip: Optional[dict] = None) -> dict:
    """从事件日志重算阶段四指标 (报告与测试共用同一实现)."""
    events = list(events)
    windows = collect_task_windows(events)
    join_events = []
    seen_joins: set = set()
    for event in events:
        if event.get("kind") != "JOIN_COMMIT":
            continue
        key = (event.get("evidence_id"), event.get("uid"),
               event.get("sim_time"))
        if key in seen_joins:
            continue
        seen_joins.add(key)
        join_events.append(event)
    spawn_events = [event for event in events
                    if event.get("kind") == "MATERIAL_SPAWN"]
    times = [float(event.get("sim_time") or 0.0) for event in events]
    if window_start is None:
        window_start = (min(times) if times else 0.0)
    if window_end is None:
        window_end = (max(times) if times else 0.0)
    span = max(0.0, float(window_end) - float(window_start))

    finals = len(join_events)
    join_times = sorted(float(event.get("sim_time") or 0.0)
                        for event in join_events)
    intervals = [round(join_times[i + 1] - join_times[i], 4)
                 for i in range(len(join_times) - 1)]

    by_kind: dict[str, list[TaskWindow]] = {}
    by_device: dict[str, float] = {}
    for window in windows.values():
        by_kind.setdefault(window.kind or "unknown", []).append(window)
        if window.device and window.started is not None:
            end = (window.finished if window.finished is not None
                   else window_end)
            by_device[window.device] = by_device.get(window.device, 0.0) + \
                max(0.0, float(end) - float(window.started))

    task_rows = {}
    for kind, rows in sorted(by_kind.items()):
        done = [row for row in rows if row.status == "DONE"]
        task_rows[kind] = {
            "count": len(rows),
            "done": len(done),
            "failed": sum(1 for row in rows if row.status == "FAILED"),
            "blocked": sum(1 for row in rows if row.status == "BLOCKED"),
            "processing_s": [row.processing_s() for row in rows
                             if row.processing_s() is not None],
            "cycle_s": [row.cycle_s() for row in rows
                        if row.cycle_s() is not None],
            "wait_s": round(sum(row.wait_s for row in rows), 4),
            "block_s": round(sum(row.block_s for row in rows), 4),
        }

    # 活动任务数/队列峰值
    active = 0
    peak = 0
    for event in sorted(events, key=lambda item: float(
            item.get("sim_time") or 0.0)):
        kind = str(event.get("kind") or "")
        if kind == "TASK_START":
            active += 1
            peak = max(peak, active)
        elif kind in TERMINAL:
            active = max(0, active - 1)

    # AGV 航线
    routes = []
    open_routes: dict[str, dict] = {}
    for event in events:
        kind = str(event.get("kind") or "")
        if kind == "ROUTE_START":
            route_id = str(event.get("detail", {}).get("route_id")
                           or event.get("evidence_id") or "")
            open_routes[route_id] = event
        elif kind == "ROUTE_DONE":
            route_id = str(event.get("detail", {}).get("route_id")
                           or event.get("evidence_id") or "")
            start = open_routes.pop(route_id, None)
            if start is not None:
                routes.append({
                    "route_id": route_id,
                    "loaded": bool(start.get("detail", {}).get("loaded")),
                    "duration_s": round(
                        float(event.get("sim_time") or 0.0)
                        - float(start.get("sim_time") or 0.0), 4),
                })

    loaded = [row for row in routes if row["loaded"]]
    empty = [row for row in routes if not row["loaded"]]
    devices = {}
    for device, busy in sorted(by_device.items()):
        devices[device] = {
            "busy_s": round(busy, 4),
            "utilization": round(busy / span, 4) if span > 0 else None,
        }

    tasks_total = len(windows)
    return {
        "version": cfg.STAGE4_METRICS_VERSION,
        "window": {
            "start_s": round(float(window_start), 4),
            "end_s": round(float(window_end), 4),
            "span_s": round(span, 4),
            "warmup_s": cfg.METRICS_WARMUP_S,
            "policy": cfg.METRICS_WINDOW,
        },
        "throughput": {
            "finals": finals,
            "final_times_s": [round(t, 4) for t in join_times],
            "output_intervals_s": intervals,
            "spawned": len(spawn_events),
            "finals_per_s": (round(finals / span, 6)
                             if span > 0 else None),
            "finals_per_hour": (round(finals / span * 3600.0, 2)
                                if span > 0 else None),
        },
        "tasks": {
            "total": tasks_total,
            "by_kind": task_rows,
        },
        "devices": devices,
        "agv": {
            "routes": routes,
            "loaded_routes": len(loaded),
            "empty_routes": len(empty),
            "loaded_time_s": round(sum(r["duration_s"] for r in loaded), 4),
            "empty_time_s": round(sum(r["duration_s"] for r in empty), 4),
        },
        "queues": {
            "active_at_end": active,
            "peak_active": peak,
            "wip": wip or {},
        },
        "events": len(events),
        "recomputed_from_events": True,
    }
