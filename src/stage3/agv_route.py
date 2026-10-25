"""agv_route.py — 阶段三 AGV 两站直线路线、停靠与阻塞续行 (06-12 §3.3).

只通过 `PioneerController.set_cmd()` 下发轮速, 不写车体位姿. 路线是
x=0.40 的直线穿梭:
    dock A (y=+0.70)  →  dock B (y=-0.70)   带载
    dock B            →  dock A              空载基线

车体初始朝向 -Y, 因此两个方向都不需要在 dock 处原地旋转, 不会扫过任一
Island/Station. 反向行程以负线速度前进, 航向仍保持 -Y.

停靠判据:
  - 位置误差 ≤ `AGV_ROUTE_ARRIVE_TOL`;
  - 实际线速度 ≤ `AGV_ROUTE_STOP_VEL_TOL`, 并连续稳定 `SETTLE_STEPS`;
  - 航向误差 ≤ `AGV_ROUTE_HEADING_TOL`;
  - 带载时载荷相对载台的滑移 ≤ `AGV_ROUTE_SLIP_TOL`.

阻塞: 轮速已下发但车体在 `BLOCK_TIMEOUT` 窗口内位移小于阈值 → BLOCKED,
立即 `stop()`. 只有调用方确认无安全锁存、障碍已清除、位置/航向仍可确认
后才允许 `resume()`; 不自动重试.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from stage1 import config as base
from stage1.pioneer_controller import PioneerController

from . import config as cfg
from .layout import quat_from_yaw, yaw_from_quat


class RouteState(str, Enum):
    IDLE = "IDLE"
    DRIVING = "DRIVING"
    SETTLING = "SETTLING"
    DONE = "DONE"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"

    @property
    def terminal(self) -> bool:
        return self in (RouteState.DONE, RouteState.FAILED)


def wrap_angle(angle: float) -> float:
    return (float(angle) + math.pi) % (2.0 * math.pi) - math.pi


def pose_yaw(pose) -> float:
    return yaw_from_quat(pose[3:7])


def _local_offset(pose, point) -> list[float]:
    """世界点相对车体坐标系的偏移 (旋转 -yaw, 不平移 Z)."""
    yaw = pose_yaw(pose)
    dx = float(point[0]) - float(pose[0])
    dy = float(point[1]) - float(pose[1])
    dz = float(point[2]) - float(pose[2])
    c, s = math.cos(-yaw), math.sin(-yaw)
    return [c * dx - s * dy, s * dx + c * dy, dz]


@dataclass(frozen=True)
class RouteSpec:
    route_id: str
    start: tuple[float, float]
    end: tuple[float, float]
    heading: float
    direction: int = 1                 # +1 正向, -1 倒车
    speed: float = cfg.AGV_ROUTE_SPEED
    loaded: bool = False
    arrive_tol: float = cfg.AGV_ROUTE_ARRIVE_TOL
    heading_tol: float = cfg.AGV_ROUTE_HEADING_TOL

    def as_dict(self) -> dict:
        return {
            "route_id": self.route_id,
            "start": list(self.start),
            "end": list(self.end),
            "heading": round(float(self.heading), 6),
            "direction": int(self.direction),
            "speed": float(self.speed),
            "loaded": bool(self.loaded),
            "arrive_tol": float(self.arrive_tol),
            "heading_tol": float(self.heading_tol),
        }


@dataclass
class RouteMetrics:
    route_id: str
    loaded: bool
    start_pose: Optional[list[float]] = None
    end_pose: Optional[list[float]] = None
    target_pose: Optional[list[float]] = None
    stop_error: Optional[float] = None
    heading_error: Optional[float] = None
    duration_s: float = 0.0
    max_speed: float = 0.0
    mean_speed: float = 0.0
    samples: int = 0
    stable_samples: int = 0
    stable_fraction: float = 0.0
    material_slip: Optional[float] = None
    material_relative_start: Optional[list[float]] = None
    material_relative_end: Optional[list[float]] = None
    blocked: dict = field(default_factory=dict)
    resumed: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "route_id": self.route_id,
            "loaded": bool(self.loaded),
            "start_pose": (None if self.start_pose is None else
                           [round(float(v), 6) for v in self.start_pose]),
            "end_pose": (None if self.end_pose is None else
                         [round(float(v), 6) for v in self.end_pose]),
            "target_pose": (None if self.target_pose is None else
                            [round(float(v), 6) for v in self.target_pose]),
            "stop_error": (None if self.stop_error is None
                           else round(float(self.stop_error), 6)),
            "heading_error": (None if self.heading_error is None
                              else round(float(self.heading_error), 6)),
            "duration_s": round(float(self.duration_s), 4),
            "max_speed": round(float(self.max_speed), 6),
            "mean_speed": round(float(self.mean_speed), 6),
            "samples": int(self.samples),
            "stable_samples": int(self.stable_samples),
            "stable_fraction": round(float(self.stable_fraction), 4),
            "material_slip": (None if self.material_slip is None
                              else round(float(self.material_slip), 6)),
            "material_relative_start": self.material_relative_start,
            "material_relative_end": self.material_relative_end,
            "blocked": self.blocked,
            "resumed": list(self.resumed),
        }


class AGVRouteController:
    """单方向路线执行器; 由协调器每步调用 `step()`."""

    def __init__(self, sim, agv: PioneerController, spec: RouteSpec,
                 material_manager=None, uid: Optional[int] = None,
                 logger=None) -> None:
        self.sim = sim
        self.agv = agv
        self.spec = spec
        self.materials = material_manager
        self.uid = uid
        self.logger = logger

        self.state = RouteState.IDLE
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None
        self.failure_reason = ""
        self._resume_speed = False
        self._settle_count = 0
        self._block_window_start: Optional[float] = None
        self._block_window_pos: Optional[list[float]] = None
        self._last_sim_time = 0.0
        self._speed_sum = 0.0
        self._last_material_local: Optional[list[float]] = None
        self.metrics = RouteMetrics(route_id=spec.route_id,
                                    loaded=spec.loaded)

    # ── 状态 ────────────────────────────────────────────────

    @property
    def finished(self) -> bool:
        return self.state.terminal

    @property
    def blocked(self) -> bool:
        return self.state is RouteState.BLOCKED

    @property
    def done(self) -> bool:
        return self.state is RouteState.DONE

    # ── 主步进 ──────────────────────────────────────────────

    def step(self, sim_time: float) -> RouteState:
        self._last_sim_time = float(sim_time)
        if self.state in (RouteState.DONE, RouteState.FAILED):
            return self.state
        if self.state is RouteState.BLOCKED:
            self.agv.stop()
            return self.state

        pose = self._pose()
        if pose is None:
            return self._fail("AGV_POSE_UNREADABLE")
        if self.state is RouteState.IDLE:
            self._begin(sim_time, pose)

        target = (self.spec.end[0], self.spec.end[1])
        distance = math.hypot(target[0] - pose[0], target[1] - pose[1])
        heading_error = wrap_angle(self.spec.heading - pose_yaw(pose))
        speed = self._linear_speed()

        self._record_speed(speed, sim_time)
        self._record_material(pose)

        if self._blocked_now(sim_time, pose):
            return self.state

        if distance <= self.spec.arrive_tol:
            self.agv.stop()
            if self.state is not RouteState.SETTLING:
                self.state = RouteState.SETTLING
                self._settle_count = 0
                if self.logger:
                    self.logger.info(
                        "[AGV_ROUTE] %s 到达停靠窗口 distance=%.4f heading=%.4f",
                        self.spec.route_id, distance, heading_error)
            settle_ok = (speed <= cfg.AGV_ROUTE_STOP_VEL_TOL
                         and self.agv.is_stopped())
            self._settle_count = (self._settle_count + 1) if settle_ok else 0
            if self._settle_count >= cfg.AGV_ROUTE_SETTLE_STEPS:
                if abs(heading_error) > self.spec.heading_tol:
                    return self._fail(
                        f"HEADING_ERROR: {heading_error:.4f} rad > "
                        f"{self.spec.heading_tol:.4f} rad")
                return self._finish(sim_time, pose, distance, heading_error)
            if sim_time - self.started_at > cfg.AGV_ROUTE_TIMEOUT:
                return self._fail("SETTLE_TIMEOUT: 到站后未停稳")
            return self.state

        if sim_time - self.started_at > cfg.AGV_ROUTE_TIMEOUT:
            return self._fail(
                f"ROUTE_TIMEOUT: {cfg.AGV_ROUTE_TIMEOUT:.1f}s 未到站")

        # 速度剖面: 距目标越近, 目标速度越低.
        remaining = max(0.0, distance - self.spec.arrive_tol)
        v_max = (cfg.AGV_ROUTE_RESUME_SPEED if self._resume_speed
                 else self.spec.speed)
        v_target = min(v_max, math.sqrt(
            max(0.0, 2.0 * cfg.AGV_ROUTE_DECEL * remaining)))
        if remaining < 0.02:
            v_target = min(v_target, 0.03)

        # 航向修正: 偏差大且已接近目标时原地对准, 避免斜向撞入 dock.
        w = max(-cfg.AGV_ROUTE_MAX_YAW_RATE,
                min(cfg.AGV_ROUTE_MAX_YAW_RATE,
                    cfg.AGV_ROUTE_HEADING_KP * heading_error))
        if abs(heading_error) > 0.35 and distance < 0.25:
            self.agv.set_cmd(0.0, w)
        else:
            self.agv.set_cmd(self.spec.direction * v_target, w)
        return self.state

    # ── 阻塞/续行 ───────────────────────────────────────────

    def _blocked_now(self, sim_time: float, pose) -> bool:
        commanded = abs(self.agv._cmd_v) if hasattr(self.agv, "_cmd_v") else 0.0
        if commanded <= 0.02:
            self._block_window_start = None
            self._block_window_pos = None
            return False
        if self.state is RouteState.SETTLING:
            return False
        position = [float(pose[0]), float(pose[1])]
        if self._block_window_start is None:
            self._block_window_start = float(sim_time)
            self._block_window_pos = position
            return False
        if float(sim_time) - self._block_window_start < \
                cfg.AGV_ROUTE_BLOCK_TIMEOUT:
            return False
        moved = math.dist(position, self._block_window_pos or position)
        if moved >= cfg.AGV_ROUTE_BLOCK_DISPLACEMENT:
            self._block_window_start = float(sim_time)
            self._block_window_pos = position
            return False
        self.agv.stop()
        self.state = RouteState.BLOCKED
        self.metrics.blocked = {
            "sim_time": round(float(sim_time), 4),
            "commanded_speed": round(float(commanded), 4),
            "moved_m": round(moved, 6),
            "window_s": cfg.AGV_ROUTE_BLOCK_TIMEOUT,
            "position": [round(v, 5) for v in position],
        }
        if self.logger:
            self.logger.warning(
                "[AGV_ROUTE] %s BLOCKED: 轮速已下发但 %.2fs 内仅移动 %.4f m",
                self.spec.route_id, cfg.AGV_ROUTE_BLOCK_TIMEOUT, moved)
        return True

    def resume(self, sim_time: float, *, clearance_ok: bool,
               reason: str = "") -> bool:
        """清障后受控续行; 调用方必须已确认无安全锁存."""
        if self.state is not RouteState.BLOCKED:
            return False
        if not clearance_ok:
            if self.logger:
                self.logger.warning(
                    "[AGV_ROUTE] %s 拒绝续行: 路线/所有权未重新确认",
                    self.spec.route_id)
            return False
        pose = self._pose()
        if pose is None:
            return False
        self.state = RouteState.DRIVING
        self._resume_speed = True
        self._block_window_start = None
        self._block_window_pos = None
        self.metrics.resumed.append({
            "sim_time": round(float(sim_time), 4),
            "reason": reason or "clearance_confirmed",
            "position": [round(float(pose[0]), 5),
                         round(float(pose[1]), 5)],
        })
        if self.logger:
            self.logger.info(
                "[AGV_ROUTE] %s 清障续行 speed=%.3f m/s reason=%s",
                self.spec.route_id, cfg.AGV_ROUTE_RESUME_SPEED,
                reason or "clearance_confirmed")
        return True

    # ── 内部 ────────────────────────────────────────────────

    def _begin(self, sim_time: float, pose) -> None:
        self.state = RouteState.DRIVING
        self.started_at = float(sim_time)
        self.metrics.start_pose = list(pose)
        self.metrics.target_pose = [
            float(self.spec.end[0]), float(self.spec.end[1]),
            float(pose[2]), *quat_from_yaw(self.spec.heading)]
        material = self._material_pose()
        if material is not None:
            self._last_material_local = _local_offset(pose, material)
            self.metrics.material_relative_start = [
                round(v, 6) for v in self._last_material_local]
        if self.logger:
            self.logger.info(
                "[AGV_ROUTE] %s 开始 loaded=%s start=(%.3f,%.3f) "
                "target=(%.3f,%.3f) direction=%+d",
                self.spec.route_id, self.spec.loaded, pose[0], pose[1],
                self.spec.end[0], self.spec.end[1], self.spec.direction)

    def _finish(self, sim_time: float, pose, distance: float,
                heading_error: float) -> RouteState:
        self.state = RouteState.DONE
        self.finished_at = float(sim_time)
        material = self._material_pose()
        self.metrics.end_pose = list(pose)
        self.metrics.stop_error = float(distance)
        self.metrics.heading_error = float(heading_error)
        self.metrics.duration_s = float(sim_time) - float(self.started_at)
        if self.metrics.samples:
            self.metrics.mean_speed = (self._speed_sum
                                       / self.metrics.samples)
            self.metrics.stable_fraction = (
                self.metrics.stable_samples / self.metrics.samples)
        if material is not None:
            end_local = _local_offset(pose, material)
            self.metrics.material_relative_end = [round(v, 6)
                                                  for v in end_local]
            if self._last_material_local is not None:
                self.metrics.material_slip = math.hypot(
                    end_local[0] - self._last_material_local[0],
                    end_local[1] - self._last_material_local[1])
        if self.logger:
            self.logger.info(
                "[AGV_ROUTE] %s 完成 stop=%.4f m heading=%.4f rad "
                "duration=%.2fs slip=%s",
                self.spec.route_id, distance, heading_error,
                self.metrics.duration_s,
                None if self.metrics.material_slip is None
                else round(self.metrics.material_slip, 4))
        return self.state

    def _fail(self, reason: str) -> RouteState:
        self.agv.stop()
        self.state = RouteState.FAILED
        self.failure_reason = str(reason)
        self.finished_at = self._last_sim_time
        if self.logger:
            self.logger.error("[AGV_ROUTE] %s FAILED: %s",
                              self.spec.route_id, reason)
        return self.state

    def _pose(self):
        try:
            return list(self.agv.pose())
        except Exception:
            return None

    def _linear_speed(self) -> float:
        try:
            lin, _ang = self.agv._velocity()
            return math.sqrt(sum(float(v) * float(v) for v in lin[:3]))
        except Exception:
            return 0.0

    def _material_pose(self):
        if self.materials is None or self.uid is None:
            return None
        rec = self.materials.by_uid(self.uid)
        if rec is None or rec.sim_handle is None:
            return None
        try:
            return list(self.sim.getObjectPose(rec.sim_handle,
                                               self.sim.handle_world))
        except Exception:
            return None

    def _record_speed(self, speed: float, sim_time: float) -> None:
        self.metrics.samples += 1
        self.metrics.max_speed = max(self.metrics.max_speed, float(speed))
        self._speed_sum += float(speed)
        target = self.spec.speed
        if abs(float(speed) - target) <= cfg.AGV_ROUTE_SPEED_WINDOW_TOL:
            self.metrics.stable_samples += 1

    def _record_material(self, pose) -> None:
        material = self._material_pose()
        if material is not None:
            self._last_material_local = _local_offset(pose, material)

    # ── 报告 ────────────────────────────────────────────────

    def summary(self) -> dict:
        return {
            "state": self.state.value,
            "failure_reason": self.failure_reason,
            "spec": self.spec.as_dict(),
            "metrics": self.metrics.as_dict(),
            "version": cfg.STAGE3_AGV_ROUTE_VERSION,
        }


def route_specs(layout) -> dict[str, RouteSpec]:
    """按布局生成 A→B 带载 / B→A 空载两条路线."""
    a, b = layout.unit("A"), layout.unit("B")
    heading = float(layout.agv_initial_yaw)
    return {
        "A_to_B": RouteSpec(
            route_id="A_to_B", start=tuple(a.dock), end=tuple(b.dock),
            heading=heading, direction=1, loaded=True),
        "B_to_A": RouteSpec(
            route_id="B_to_A", start=tuple(b.dock), end=tuple(a.dock),
            heading=heading, direction=-1, loaded=False),
    }
