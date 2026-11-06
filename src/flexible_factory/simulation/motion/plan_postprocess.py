"""plan_postprocess.py — 成功计划的平滑与压缩.

从 :mod:`path_planning` 拆出; 旧模块保留 re-export. 平滑/压缩开关、
容差和原始路径恢复语义保持不变.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

from .. import config_motion as cfg
from .contact import quat_angle_between
from .plan_checks import ConfigurationChecker
from .plan_types import _OK_STATUSES, MotionPlan, MotionSegment
from .trajectory import slerp


def smooth_plan_waypoints(plan: MotionPlan,
                          checker: ConfigurationChecker) -> dict:
    """对已选链做段内三点平滑 (06-10 §8.3.5), 默认关闭.

    只在段内部点应用 ``q_i ← (1-2w)q_i + w(q_{i-1}+q_{i+1})``, 段端点与
    语义 waypoint 不变. 平滑会改变 TCP/载荷路径, 因此调用方必须对结果重新
    执行 ``_verify_plan``; 本函数只记录相对原路径的最大 TCP/姿态偏差, 不
    自行宣称安全. 失败时调用方用返回字典中的原始路径恢复.
    """
    if not cfg.TRAJECTORY_SMOOTHING_ENABLED or plan.status not in _OK_STATUSES:
        plan.smoothing = {"enabled": bool(cfg.TRAJECTORY_SMOOTHING_ENABLED),
                          "applied": False}
        return plan.smoothing
    original_waypoints = [list(q) for q in plan.waypoints]
    original_counts = [segment.waypoint_count for segment in plan.segments]
    weight = max(0.0, min(0.5, float(cfg.TRAJECTORY_SMOOTHING_WEIGHT)))
    passes = max(1, int(cfg.TRAJECTORY_SMOOTHING_PASSES))
    changed = 0
    max_pos = 0.0
    max_ang = 0.0
    per_segment: list[dict] = []
    for start, end, segment in plan.segment_spans():
        if end - start < 2:
            continue
        segment_changed = 0
        for _pass in range(passes):
            updates: dict[int, list[float]] = {}
            for index in range(start + 1, end):
                previous = plan.waypoints[index - 1]
                current = plan.waypoints[index]
                following = plan.waypoints[index + 1]
                updates[index] = [
                    (1.0 - 2.0 * weight) * float(current[j])
                    + weight * (float(previous[j]) + float(following[j]))
                    for j in range(len(current))
                ]
            for index, q_new in updates.items():
                before = checker.tcp_at(plan.waypoints[index])
                after = checker.tcp_at(q_new)
                if before is not None and after is not None:
                    max_pos = max(max_pos, math.dist(before[:3], after[:3]))
                    max_ang = max(max_ang, quat_angle_between(
                        before[3:7], after[3:7]))
                plan.waypoints[index] = q_new
                changed += 1
                segment_changed += 1
        per_segment.append({"segment": segment.name,
                            "changed_points": segment_changed})
    plan.smoothing = {
        "enabled": True,
        "applied": changed > 0,
        "passes": passes,
        "weight": weight,
        "changed_points": changed,
        "segments": per_segment,
        "max_tcp_pos_deviation": round(max_pos, 6),
        "max_tcp_ang_deviation": round(max_ang, 6),
        "reverted": False,
        "_original_waypoints": original_waypoints,
        "_original_counts": original_counts,
    }
    return plan.smoothing


def compact_plan_waypoints(plan: MotionPlan,
                           checker: ConfigurationChecker) -> dict:
    """删除段内数值冗余点, 不跨语义段, 不改变段端点.

    只在相邻两段关节插值的 FK TCP 轨迹仍贴近原折线 (位置/姿态容差内)
    时删除中间点; 压缩后必须重新执行 ``_verify_plan``. 失败时调用方恢复
    原始路径. 返回的统计会写入 ``plan.compact``.
    """
    if not cfg.TRAJECTORY_COMPACTION_ENABLED or plan.status not in _OK_STATUSES:
        plan.compact = {"enabled": bool(cfg.TRAJECTORY_COMPACTION_ENABLED),
                        "removed": 0}
        return plan.compact
    original_waypoints = [list(q) for q in plan.waypoints]
    original_counts = [segment.waypoint_count for segment in plan.segments]
    removed = 0
    max_pos = 0.0
    max_ang = 0.0
    for segment in plan.segments:
        count = int(segment.waypoint_count)
        if count < 2:
            continue
        start = _segment_start(plan, segment)
        while count >= 2:
            index = start + count // 2
            if index <= start or index >= start + count:
                break
            deviation = _interpolation_deviation(
                plan.waypoints[index - 1], plan.waypoints[index],
                plan.waypoints[index + 1], checker)
            if deviation is None:
                break
            if deviation["pos"] > cfg.COMPACTION_TCP_POS_TOL or \
                    deviation["ang"] > cfg.COMPACTION_TCP_ANG_TOL:
                break
            del plan.waypoints[index]
            count -= 1
            segment.waypoint_count = count
            removed += 1
            max_pos = max(max_pos, deviation["pos"])
            max_ang = max(max_ang, deviation["ang"])
    plan.compact = {
        "enabled": True,
        "removed": removed,
        "points_before": len(original_waypoints),
        "points_after": len(plan.waypoints),
        "max_tcp_pos_deviation": round(max_pos, 6),
        "max_tcp_ang_deviation": round(max_ang, 6),
        "pos_tol": cfg.COMPACTION_TCP_POS_TOL,
        "ang_tol": cfg.COMPACTION_TCP_ANG_TOL,
        "_original_waypoints": original_waypoints,
        "_original_counts": original_counts,
    }
    return plan.compact


def _segment_start(plan: MotionPlan, target: MotionSegment) -> int:
    start = 0
    for segment in plan.segments:
        if segment is target:
            return start
        start += max(0, int(segment.waypoint_count))
    return 0


def _interpolation_deviation(q0: Sequence[float], q1: Sequence[float],
                             q2: Sequence[float],
                             checker: ConfigurationChecker) -> Optional[dict]:
    """检查去掉 q1 后关节插值 q0→q2 的 TCP 轨迹是否仍贴近原折线."""
    p0 = checker.tcp_at(q0)
    p1 = checker.tcp_at(q1)
    p2 = checker.tcp_at(q2)
    if p0 is None or p1 is None or p2 is None:
        return None
    points = [p0[:3], p1[:3], p2[:3]]
    max_pos = 0.0
    max_ang = 0.0
    samples = max(2, int(cfg.COMPACTION_SAMPLES))
    for step in range(1, samples):
        t = step / samples
        q = [float(q0[i]) + (float(q2[i]) - float(q0[i])) * t
             for i in range(len(q0))]
        pose = checker.tcp_at(q)
        if pose is None:
            return None
        nearest = _nearest_on_polyline(pose[:3], points)
        max_pos = max(max_pos, nearest["distance"])
        expected = slerp(p0[3:7], p2[3:7], t)
        max_ang = max(max_ang, quat_angle_between(pose[3:7], expected))
    return {"pos": max_pos, "ang": max_ang}


def _nearest_on_polyline(point: Sequence[float],
                         points: Sequence[Sequence[float]]) -> dict:
    best = float("inf")
    for a, b in zip(points, points[1:]):
        direction = [float(b[i]) - float(a[i]) for i in range(3)]
        length_sq = sum(v * v for v in direction)
        if length_sq <= 1e-12:
            candidate = list(a)
        else:
            t = sum((float(point[i]) - float(a[i])) * direction[i]
                    for i in range(3)) / length_sq
            t = max(0.0, min(1.0, t))
            candidate = [float(a[i]) + direction[i] * t for i in range(3)]
        best = min(best, math.dist(point, candidate))
    return {"distance": best}
