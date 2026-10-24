"""trajectory.py — Cartesian 法向段、关节流式执行器与轨迹一致性.

阶段二最后接近/撤离必须沿物料法向分段生成位姿, 以上一解为 seed 连续求解;
普通关节插值不保证 TCP 走直线, 因此规划阶段用 FK 检查偏差, 执行阶段逐步
复核 (06-09 §5.4–§5.7).

``JointStreamExecutor`` 每步只提交**已复核**的下一构型:
  1. 从真实 q 出发, 以 ``EXEC_MAX_JOINT_STEP`` 逼近下一个路径点;
  2. 对 ``实际 q → 下一构型`` 调用同一 ``check_segment`` 策略;
  3. 返回给控制器下发; 取消后不再产生任何命令.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional, Protocol, Sequence

from . import config as cfg
from . import metrics as motion_metrics
from .contact import quat_normalize

IDENTITY_QUAT = (0.0, 0.0, 0.0, 1.0)


# ═══════════════════════════════════════════════════════════════
# 位姿插值
# ═══════════════════════════════════════════════════════════════

def slerp(a: Sequence[float], b: Sequence[float], t: float) -> tuple:
    qa = quat_normalize(a)
    qb = quat_normalize(b)
    dot = sum(qa[i] * qb[i] for i in range(4))
    if dot < 0.0:
        qb = tuple(-v for v in qb)
        dot = -dot
    t = max(0.0, min(1.0, float(t)))
    if dot > 0.9995:
        return quat_normalize(tuple(
            qa[i] + t * (qb[i] - qa[i]) for i in range(4)))
    theta = math.acos(max(-1.0, min(1.0, dot)))
    sin_theta = math.sin(theta)
    if abs(sin_theta) < 1e-12:
        return qa
    wa = math.sin((1.0 - t) * theta) / sin_theta
    wb = math.sin(t * theta) / sin_theta
    return quat_normalize(tuple(qa[i] * wa + qb[i] * wb
                                for i in range(4)))


def cartesian_poses(start_pose: Sequence[float], goal_pose: Sequence[float],
                    max_step: float = cfg.CARTESIAN_SAMPLE_MAX_DELTA,
                    max_points: int = 400) -> list[list[float]]:
    """位置线性、姿态球面插值的位姿序列 (含起止点)."""
    start = [float(v) for v in start_pose]
    goal = [float(v) for v in goal_pose]
    if len(start) < 7 or len(goal) < 7:
        raise ValueError("Cartesian 位姿必须是 7 元素")
    step = max(float(max_step), 1e-9)
    distance = math.dist(start[:3], goal[:3])
    segments = max(1, int(math.ceil(distance / step)))
    segments = min(segments, max(1, int(max_points) - 1))
    poses: list[list[float]] = []
    for i in range(segments + 1):
        t = i / segments
        pos = [start[k] + (goal[k] - start[k]) * t for k in range(3)]
        quat = slerp(start[3:7], goal[3:7], t)
        poses.append([*pos, *quat])
    return poses


def joint_distance(a: Sequence[float], b: Sequence[float]) -> float:
    return math.dist([float(v) for v in a], [float(v) for v in b])


def joint_waypoints(q0: Sequence[float], q1: Sequence[float],
                    max_step: float) -> list[list[float]]:
    """关节空间线性插值 (含起止点)."""
    start = [float(v) for v in q0]
    goal = [float(v) for v in q1]
    distance = joint_distance(start, goal)
    steps = max(1, int(math.ceil(distance / max(float(max_step), 1e-9))))
    return [[start[i] + (goal[i] - start[i]) * (k / steps)
             for i in range(len(start))] for k in range(steps + 1)]


def approach_quality(poses: Sequence[Sequence[float]],
                     surface_center: Sequence[float],
                     surface_normal: Sequence[float]) -> dict:
    """检查法向接近序列的横向偏差与步长 (纯几何)."""
    if not poses:
        return {"ok": False, "reason": "empty"}
    lateral_max = 0.0
    step_max = 0.0
    n = [float(v) for v in surface_normal[:3]]
    for i, pose in enumerate(poses):
        rel = [float(pose[k]) - float(surface_center[k]) for k in range(3)]
        along = sum(rel[k] * n[k] for k in range(3))
        lateral = math.sqrt(max(0.0, sum(v * v for v in rel) - along * along))
        lateral_max = max(lateral_max, lateral)
        if i:
            step_max = max(step_max,
                           math.dist(pose[:3], poses[i - 1][:3]))
    return {"ok": lateral_max <= cfg.CONTACT_MAX_LATERAL,
            "lateral_max": lateral_max,
            "step_max": step_max,
            "points": len(poses)}


# ═══════════════════════════════════════════════════════════════
# 流式执行器
# ═══════════════════════════════════════════════════════════════

class ExecutorStatus(str, Enum):
    RUNNING = "RUNNING"
    DONE = "DONE"
    CANCELLED = "CANCELLED"
    COLLISION = "COLLISION"
    UNKNOWN = "UNKNOWN"
    DEVIATION = "DEVIATION"
    TIMEOUT = "TIMEOUT"
    WRIST_BRANCH = "WRIST_BRANCH"


@dataclass
class ExecutorStep:
    status: ExecutorStatus
    q: list[float]
    done: bool = False
    message: str = ""
    check: dict = field(default_factory=dict)


class SegmentChecker(Protocol):
    def check_segment(self, q0, q1, context, **kwargs): ...


class JointStreamExecutor:
    """把 MotionPlan 的路径点变成每步可复核的关节命令."""

    def __init__(self, plan, checker: SegmentChecker,
                 contexts: Sequence[object],
                 max_joint_step: float = cfg.EXEC_MAX_JOINT_STEP,
                 joint_tol: float = cfg.EXEC_JOINT_TOL,
                 command_settle_tol: float = cfg.EXEC_COMMAND_SETTLE_TOL,
                 max_deviation: float = cfg.EXEC_MAX_DEVIATION,
                 reach_timeout: float = cfg.EXEC_REACH_TIMEOUT,
                 pose_at: Optional[Callable] = None) -> None:
        self.plan = plan
        self.checker = checker
        self.contexts = list(contexts)
        self.max_joint_step = float(max_joint_step)
        self.joint_tol = float(joint_tol)
        self.command_settle_tol = float(command_settle_tol)
        self.max_deviation = float(max_deviation)
        self.reach_timeout = float(reach_timeout)
        self.pose_at = pose_at
        self.waypoints = [list(map(float, q)) for q in plan.waypoints]
        self.index = 0
        self.cancelled = False
        self.last_command: Optional[list[float]] = None
        self.started_at: Optional[float] = None
        self.waypoint_started_at: Optional[float] = None
        self.step_count = 0
        # 阶段 2.01 执行指标: 每个控制步记录实测 q 与已下发命令.
        self.samples: list[dict] = []
        self.commands: list[dict] = []
        self.spans = self._spans()

    def _spans(self) -> list[tuple[int, int, object]]:
        if hasattr(self.plan, "segment_spans"):
            spans = list(self.plan.segment_spans())
            if spans and spans[-1][1] == len(self.waypoints) - 1:
                return spans
        return [(0, max(0, len(self.waypoints) - 1), None)]

    # ── 状态 ────────────────────────────────────────────────

    @property
    def final_waypoint(self) -> list[float]:
        return self.waypoints[-1]

    def cancel(self) -> None:
        self.cancelled = True

    def _context(self) -> object:
        idx = min(self.index + 1, max(0, len(self.contexts) - 1))
        return self.contexts[idx] if self.contexts else None

    def _record(self, sim_time: float, q: Sequence[float]) -> None:
        self.samples.append({"t": float(sim_time), "index": int(self.index),
                             "q": [float(v) for v in q]})

    # ── 推进 ────────────────────────────────────────────────

    def next_command(self, actual_q: Sequence[float],
                     sim_time: float) -> ExecutorStep:
        if self.cancelled:
            return ExecutorStep(ExecutorStatus.CANCELLED, list(actual_q),
                                message="executor cancelled")
        actual = [float(v) for v in actual_q]
        self._record(sim_time, actual)
        if self.started_at is None:
            self.started_at = float(sim_time)
            self.waypoint_started_at = float(sim_time)
        if self.index >= len(self.waypoints):
            return ExecutorStep(ExecutorStatus.DONE, actual, done=True)
        if float(sim_time) - float(self.waypoint_started_at or sim_time) \
                > self.reach_timeout:
            return ExecutorStep(ExecutorStatus.TIMEOUT, actual,
                                message="轨迹执行超时")

        if self.last_command is not None:
            deviation = joint_distance(actual, self.last_command)
            if deviation > self.max_deviation:
                return ExecutorStep(
                    ExecutorStatus.DEVIATION, actual,
                    message=f"实测 q 偏离上一条命令 {deviation:.4f} rad")
            # 上一条命令尚未被真实关节达到时保持该目标, 不把目标越推越远
            if deviation > self.command_settle_tol:
                return ExecutorStep(ExecutorStatus.RUNNING,
                                    list(self.last_command),
                                    check={"holding": True})

        # 跳过已到达的路径点
        while (self.index + 1 < len(self.waypoints)
               and joint_distance(actual, self.waypoints[self.index + 1])
               <= self.joint_tol):
            self.index += 1
            self.waypoint_started_at = float(sim_time)
        target = self.waypoints[min(self.index + 1, len(self.waypoints) - 1)]
        distance = joint_distance(actual, target)
        if distance <= self.joint_tol and self.index + 1 >= len(self.waypoints):
            self.last_command = actual
            return ExecutorStep(ExecutorStatus.DONE, actual, done=True)

        delta = min(self.max_joint_step, distance)
        q_next = [actual[i] + (target[i] - actual[i]) * (delta / distance)
                  for i in range(len(actual))] if distance > 0 else list(actual)
        context = self._context()
        allow_start_load = bool(
            self.step_count == 0
            and getattr(context, "allow_start_load_contact", False))
        allow_end_load = bool(
            getattr(context, "allow_final_load_contact", False)
            and self.index + 1 >= len(self.waypoints) - 1)
        check = self.checker.check_segment(
            actual, q_next, context, fast=True, samples=3,
            allow_start_load_contact=allow_start_load,
            allow_end_load_contact=allow_end_load)
        self.step_count += 1
        if not check.ok:
            if check.status.value == "QUERY_UNKNOWN":
                status = ExecutorStatus.UNKNOWN
            elif check.status.value == "WRIST_BRANCH_MISMATCH":
                status = ExecutorStatus.WRIST_BRANCH
            else:
                status = ExecutorStatus.COLLISION
            return ExecutorStep(status, actual, message=check.message,
                                check=check.as_dict())
        self.last_command = list(q_next)
        self.commands.append({"t": float(sim_time), "index": int(self.index),
                              "q": list(q_next)})
        if distance <= self.joint_tol:
            self.index += 1
        return ExecutorStep(ExecutorStatus.RUNNING, q_next,
                            done=False, check=check.as_dict())

    # ── 执行指标 ────────────────────────────────────────────

    def execution_metrics(self) -> dict:
        """按计划段汇总**实测** q 的 TV/最大单步/时长/TCP 误差.

        采样来自每个 ``next_command`` 的实测 joint 读数, 不是 IK 端点;
        保持命令的步骤也会记录, 其增量为 0, 不虚增 TV.
        """
        if not self.samples:
            return {"segments": [], "totals": {"segments": 0,
                                                "tv_total": 0.0,
                                                "max_step": 0.0,
                                                "points": 0,
                                                "sim_duration": 0.0}}
        segments = []
        last_span = len(self.spans) - 1
        for position, (start, end, segment) in enumerate(self.spans):
            if position == last_span:
                samples = [s for s in self.samples
                           if start <= s["index"] <= end]
                commands = [c for c in self.commands
                            if start <= c["index"] <= end]
            else:
                samples = [s for s in self.samples
                           if start <= s["index"] < end]
                commands = [c for c in self.commands
                            if start <= c["index"] < end]
            if position > 0:
                # 上一段最后一次实测即本段起点实测; 不并入会把段首一步
                # 的行程漏掉.
                boundary = [s for s in self.samples
                            if s["index"] == start - 1]
                if boundary:
                    samples = [boundary[-1]] + samples
            if not samples:
                continue
            name = getattr(segment, "name", f"segment_{position}")
            cartesian = bool(getattr(segment, "cartesian", False))
            target_pose = getattr(segment, "target_pose", None)
            tcp_pose = None
            if self.pose_at is not None and target_pose is not None:
                try:
                    tcp_pose = self.pose_at(samples[-1]["q"])
                except Exception:
                    tcp_pose = None
            stats = motion_metrics.segment_metrics(
                [s["q"] for s in samples], name=name,
                cartesian=cartesian, target_pose=target_pose,
                tcp_pose=tcp_pose,
                lock_joint=getattr(segment, "lock_joint", None),
                lock_value=getattr(segment, "lock_value", None),
                lock_tol=getattr(segment, "lock_tol", None))
            stats.update({
                "steps": len(samples),
                "commands": len(commands),
                "sim_duration": round(samples[-1]["t"] - samples[0]["t"], 4),
            })
            if cfg.EXECUTION_PROFILE_ENABLED:
                stats["profile"] = motion_metrics.motion_profile(samples)
            segments.append(stats)
        total = motion_metrics.totals(segments)
        total["sim_duration"] = round(
            sum(segment.get("sim_duration") or 0.0
                for segment in segments), 4)
        total["steps"] = sum(segment.get("steps") or 0
                             for segment in segments)
        total["commands"] = sum(segment.get("commands") or 0
                                for segment in segments)
        if cfg.EXECUTION_PROFILE_ENABLED:
            total["profile"] = motion_metrics.motion_profile(self.samples)
        return {"segments": segments, "totals": total,
                **self._trace()}

    def _trace(self) -> dict:
        """实际下发/实测采样 (06-10 §8.3.1), 超限时等间隔降采样."""
        if not cfg.EXECUTION_TRACE_ENABLED:
            return {"trace": {"enabled": False}}
        limit = max(1, int(cfg.EXECUTION_TRACE_MAX_SAMPLES))

        def _sample(rows, fields):
            truncated = len(rows) > limit
            if truncated:
                stride = (len(rows) if limit <= 1 else
                          max(1, int(round((len(rows) - 1) / (limit - 1)))))
                rows = rows[::stride][:limit]
            return {
                "points": len(rows),
                "truncated": truncated,
                "values": [{key: row[key] for key in fields}
                           for row in rows],
            }

        return {"trace": {
            "enabled": True,
            "samples": _sample(self.samples, ("t", "index", "q")),
            "commands": _sample(self.commands, ("t", "index", "q")),
        }}
