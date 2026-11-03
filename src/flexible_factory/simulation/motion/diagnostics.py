"""diagnostics.py — v1 运动层逐 waypoint 规划证据.

本模块只读影子臂与碰撞查询, **不移动真实机械臂、不改场景**:
  - 对计划中的每个路径点记录 q、FK TCP、载荷影子位姿、腕部分支/裕量、
    锁定关节偏差、工作区/载荷精确净空和所属语义段;
  - 输出上限 :data:`flexible_factory.simulation.config_motion.PLAN_TRACE_MAX_POINTS`,
    超出时等间隔降采样并标记 ``truncated``;
  - 查询异常记录在单点 ``errors`` 中, 不把诊断失败误报为规划失败.

规划器在最终复核通过后调用 :func:`build_plan_trace`; 失败/锁存计划保留
``attempts`` 与执行侧部分实测, 不生成“成功路径”trace.
"""

from __future__ import annotations

from typing import Optional, Sequence

from .. import config_motion as cfg
from .branch import wrist_branch
from .contact import quat_angle_between


def _segment_index(spans, index: int) -> int:
    for position, (start, end, _segment) in enumerate(spans):
        if start <= index < end:
            return position
    return max(0, len(spans) - 1)


def _nearest_goal_points(spans, index: int) -> tuple[int, int]:
    position = _segment_index(spans, index)
    start, end, _segment = spans[position]
    return start, end


def build_plan_trace(plan, checker, max_points: Optional[int] = None) -> dict:
    """生成计划级逐点诊断; 任何查询异常都降级为字段, 不抛出."""
    limit = int(cfg.PLAN_TRACE_MAX_POINTS if max_points is None
                else max_points)
    if not cfg.PLAN_TRACE_ENABLED or not getattr(plan, "waypoints", None):
        return {"enabled": bool(cfg.PLAN_TRACE_ENABLED), "points": 0,
                "waypoints": [], "truncated": False}
    probe = getattr(checker, "probe", None)
    collisions = getattr(checker, "collisions", None)
    spans = plan.segment_spans()
    total = len(plan.waypoints)
    indices = list(range(total))
    truncated = total > limit
    if truncated:
        if limit <= 1:
            indices = [total - 1]
        else:
            stride = max(1, int(round((total - 1) / (limit - 1))))
            indices = sorted(set(list(range(0, total, stride)) + [total - 1]))
            indices = indices[:limit]

    rows: list[dict] = []
    for index in indices:
        q = [float(v) for v in plan.waypoints[index]]
        context = plan.contexts[min(index + 1, len(plan.contexts) - 1)] \
            if plan.contexts else None
        carrying = bool(getattr(context, "carrying", False))
        segment = None
        if spans:
            segment = spans[_segment_index(spans, index)][2]
        row = {
            "index": int(index),
            "segment": getattr(segment, "name", ""),
            "cartesian": bool(getattr(segment, "cartesian", False)),
            "q": [round(v, 6) for v in q],
            "wrist_branch": None,
            "wrist_margin": None,
            "tcp": None,
            "load_pose": None,
            "min_workcell_distance": None,
            "min_load_distance": None,
            "lock_joint": getattr(segment, "lock_joint", None),
            "lock_deviation": None,
            "errors": [],
        }
        label, margin = wrist_branch(q)
        row["wrist_branch"] = label
        row["wrist_margin"] = round(float(margin), 6)
        lock_joint = row["lock_joint"]
        lock_value = getattr(segment, "lock_value", None)
        if lock_joint is not None and lock_value is not None:
            row["lock_deviation"] = round(
                abs(q[int(lock_joint)] - float(lock_value)), 6)
        if probe is None:
            row["errors"].append("probe_unavailable")
            rows.append(row)
            continue
        try:
            error = (checker.set_configuration(q)
                     if hasattr(checker, "set_configuration") else None)
            if error is not None:
                row["errors"].append(f"configuration: {error}")
            else:
                row["tcp"] = [round(float(v), 6)
                              for v in probe.tcp_pose()]
                if carrying:
                    load = probe.load_pose()
                    row["load_pose"] = (None if load is None else
                                        [round(float(v), 6) for v in load])
        except Exception as exc:
            row["errors"].append(f"fk: {type(exc).__name__}: {exc}")
        if collisions is not None:
            try:
                result = collisions.distance_margin(0.0)
                if result.distance is not None:
                    row["min_workcell_distance"] = round(
                        float(result.distance), 6)
            except Exception as exc:
                row["errors"].append(
                    f"distance: {type(exc).__name__}: {exc}")
            if carrying:
                try:
                    result = collisions.load_distance_margin(0.0)
                    if result.distance is not None:
                        row["min_load_distance"] = round(
                            float(result.distance), 6)
                except Exception as exc:
                    row["errors"].append(
                        f"load_distance: {type(exc).__name__}: {exc}")
        if segment is not None and getattr(segment, "target_pose", None) \
                is not None and index == _nearest_goal_points(
                    spans, index)[1] and row["tcp"] is not None:
            target = segment.target_pose
            row["tcp_error_pos"] = round(
                sum((row["tcp"][k] - float(target[k])) ** 2
                    for k in range(3)) ** 0.5, 6)
            row["tcp_error_ang"] = round(
                quat_angle_between(row["tcp"][3:7], target[3:7]), 6)
        rows.append(row)

    distances = [row["min_workcell_distance"] for row in rows
                 if row.get("min_workcell_distance") is not None]
    load_distances = [row["min_load_distance"] for row in rows
                      if row.get("min_load_distance") is not None]
    return {
        "enabled": True,
        "points": len(rows),
        "total_waypoints": total,
        "truncated": truncated,
        "min_workcell_distance": (min(distances) if distances else None),
        "min_load_distance": (min(load_distances) if load_distances else None),
        "waypoints": rows,
    }
