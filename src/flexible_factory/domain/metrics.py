"""metrics.py — v1 UFLOW 事件级节拍/吞吐/WIP/利用率.

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

from . import rules as cfg


#: 订单级调度基线 schema 版本; 只随字段/重算口径变化递增. 它不进入
#: ``simulation/config.py`` 的冻结摘要, 因而不改变场景工件 config_digest.
ORDER_METRICS_VERSION = "v1_order_metrics_20261124"

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
        "version": cfg.METRICS_VERSION,
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


# ═══════════════════════════════════════════════════════════════
# 订单级调度基线 (阶段 5 WP1)
# ═══════════════════════════════════════════════════════════════

def event_order_id(event: dict) -> str:
    """事件归属的订单 ID; 协调器在 ``detail.order_id`` 写入稳定值.

    物料事件 (``MATERIAL_SPAWN``/``JOIN_COMMIT``) 目前不带订单字段.
    v1 一次运行只允许一个订单, 因此这些未标记事件仍属于当前订单.
    """
    detail = event.get("detail") or {}
    return str(event.get("order_id") or detail.get("order_id") or "")


def _belongs_to_order(event: dict, order_id: str) -> bool:
    if not order_id:
        return True
    scoped = event_order_id(event)
    return not scoped or scoped == str(order_id)


def _round_or_none(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(float(value), 4)


def recompute_order_metrics(
        events: Iterable[dict], *,
        order_id: str = "",
        due_time: Optional[float] = None,
        target_quantity: int = 1,
        policy_version: str = "",
        window_start: Optional[float] = None,
        window_end: Optional[float] = None,
        wip: Optional[dict] = None) -> dict:
    """从事件重算单订单的完成/逾期/等待/阻塞/WIP 与派工统计.

    输入是协调器事件与物料事件的普通字典列表. 订单过滤只保留
    ``detail.order_id`` 匹配或未标记的事件; ``order_id`` 为空时统计全部
    事件 (兼容既有无订单字段的历史证据). 本函数不改写事件、不依赖
    CoppeliaSim; 相同事件重复调用必须返回相同结果.
    """
    scoped = [event for event in events
              if _belongs_to_order(event, str(order_id))]
    base = recompute_metrics(scoped, window_start=window_start,
                             window_end=window_end, wip=wip)
    windows = collect_task_windows(scoped)
    task_rows = base["tasks"]["by_kind"]

    wait_s = round(sum(float(row["wait_s"]) for row in task_rows.values()), 4)
    block_s = round(sum(float(row["block_s"]) for row in task_rows.values()),
                    4)
    processing_s = round(sum(
        float(value) for row in task_rows.values()
        for value in row["processing_s"] if value is not None), 4)
    cycle_s = round(sum(
        float(value) for row in task_rows.values()
        for value in row["cycle_s"] if value is not None), 4)

    finals = int(base["throughput"]["finals"])
    final_times = [float(value)
                   for value in base["throughput"]["final_times_s"]]
    target = max(1, int(target_quantity))
    completed = finals >= target
    completion_sim_time = (final_times[target - 1] if completed else None)
    span_start = float(base["window"]["start_s"])
    span_end = float(base["window"]["end_s"])
    completion_s = (None if completion_sim_time is None
                    else round(max(0.0, completion_sim_time - span_start), 4))

    overdue_s: Optional[float] = None
    due_met: Optional[bool] = None
    if due_time is not None:
        reference = (completion_sim_time if completed else span_end)
        overdue_s = round(max(0.0, float(reference) - float(due_time)), 4)
        due_met = bool(completed and overdue_s == 0.0)

    decisions = [event for event in scoped
                 if event.get("kind") == "SCHEDULE_DECISION"]
    waits = [event for event in scoped
             if event.get("kind") == "SCHEDULE_WAIT"]
    reassignments = [event for event in scoped
                     if event.get("kind") == "SCHEDULE_REASSIGN"]

    def _detail(event: dict) -> dict:
        return event.get("detail") or {}

    selected_devices: dict[str, int] = {}
    for event in decisions:
        device = str(event.get("device")
                     or _detail(event).get("selected_device_id") or "")
        if device:
            selected_devices[device] = selected_devices.get(device, 0) + 1
    defer_reasons: dict[str, int] = {}
    for event in waits:
        reason = str(_detail(event).get("reason") or "")
        if reason:
            defer_reasons[reason] = defer_reasons.get(reason, 0) + 1
    reassignment_reasons: dict[str, int] = {}
    for event in reassignments:
        reason = str(_detail(event).get("reason") or "")
        if reason:
            reassignment_reasons[reason] = \
                reassignment_reasons.get(reason, 0) + 1
    policy_versions = sorted({
        str(_detail(event).get("policy_version") or "")
        for event in [*decisions, *waits]
        if _detail(event).get("policy_version")
    })
    if not policy_versions and policy_version:
        policy_versions = [str(policy_version)]

    failed_tasks = [window.task_id for window in windows.values()
                    if window.status == "FAILED"]
    blocked_tasks = [window.task_id for window in windows.values()
                     if window.status == "BLOCKED"]

    return {
        "version": ORDER_METRICS_VERSION,
        "order_id": str(order_id),
        "target_quantity": target,
        "due_time_s": _round_or_none(due_time),
        "policy_version": (policy_versions[-1] if policy_versions
                           else str(policy_version)),
        "policy_versions": policy_versions,
        "completion": {
            "completed": bool(completed),
            "finals": finals,
            "completion_sim_time_s": _round_or_none(completion_sim_time),
            "completion_s": completion_s,
            "overdue_s": overdue_s,
            "due_met": due_met,
        },
        "tasks": {
            "total": int(base["tasks"]["total"]),
            "done": sum(int(row["done"]) for row in task_rows.values()),
            "failed": sum(int(row["failed"]) for row in task_rows.values()),
            "blocked": sum(int(row["blocked"]) for row in task_rows.values()),
            "failed_task_ids": failed_tasks,
            "blocked_task_ids": blocked_tasks,
            "wait_s": wait_s,
            "block_s": block_s,
            "processing_s": processing_s,
            "cycle_s": cycle_s,
        },
        "dispatch": {
            "count": len(decisions),
            "wait_count": len(waits),
            "defer_reasons": dict(sorted(defer_reasons.items())),
            "selected_devices": dict(sorted(selected_devices.items())),
            "reassignment_count": len(reassignments),
            "reassignment_reasons": dict(sorted(reassignment_reasons.items())),
        },
        "queues": dict(base["queues"]),
        "window": dict(base["window"]),
        "events": len(scoped),
        "recomputed_from_events": True,
    }
