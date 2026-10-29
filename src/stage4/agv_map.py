"""agv_map.py — 阶段四 AGV 分段路网与航线执行 (06-17 §4.1-5).

阶段 4.1 的 AGV 路网由三段轴对齐直道组成:

    A (0.40, 0.70) ─┐
    B (0.40,-0.70) ─┴─ west → J_SW (0.40,-2.10)
                                   │
                              south lane
                                   │
    D (2.90,-1.35) ─┐         J_SE (2.90,-2.10)
    E (2.90, 1.35) ─┴─ east ──────┘

载货路线保持已验证的单段直道:
  - B→A: x=0.40, 沿 +Y 倒车;
  - D→E: x=2.90, 沿 +Y 倒车.
空载调机经南横道连接两侧, 在 J_SW / J_SE 停车并原地转向 90°.

执行约定:
  - 直道复用阶段三 :class:`stage3.agv_route.AGVRouteController` 的轮速
    驱动、速度剖面、停靠判据与 BLOCKED 检测, 不写车体位姿;
  - 转向只用左右轮差速 `set_cmd(0, w)`, 先停车对准再进入下一段;
  - 转向段记录航向误差、停稳与载荷偏移; 载货路线不含转向;
  - 任一航段 BLOCKED 时整车停车并保留任务/UID, 清障确认后从当前航段
    续行, 不自动重规划.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Optional

from stage1 import config as base
from stage1.pioneer_controller import PioneerController
from stage3.agv_route import (AGVRouteController as _BaseRouteController,
                              RouteMetrics, RouteSpec, RouteState, pose_yaw,
                              wrap_angle)
from stage3.layout import quat_from_yaw

from . import config as cfg
from .layout import lane_for, shortest_node_path


# ═══════════════════════════════════════════════════════════════
# 航段
# ═══════════════════════════════════════════════════════════════

def route_direction(start: tuple[float, float],
                    end: tuple[float, float], heading: float) -> int:
    """按车头朝向计算前进/倒车方向 (+1 前进, -1 倒车)."""
    fx, fy = math.cos(heading), math.sin(heading)
    delta = (end[0] - start[0], end[1] - start[1])
    along = delta[0] * fx + delta[1] * fy
    if abs(along) <= 1e-9:
        return 1
    return 1 if along > 0.0 else -1


def heading_for_leg(start: tuple[float, float],
                    end: tuple[float, float]) -> float:
    """轴对齐直道的车头朝向.

    所有 Y 向航段保持 -Y 车头 (与阶段三一致, 不在 dock 旋转):
      - 沿 -Y 行驶为前进, 沿 +Y 为倒车.
    X 向航段以行驶方向为车头.
    """
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    if abs(dy) >= abs(dx):
        return float(cfg.AGV_INITIAL_YAW)
    return 0.0 if dx > 0 else math.pi


@dataclass(frozen=True)
class RouteLeg:
    """一段直道或一次原地转向."""

    index: int
    kind: str                      # "straight" | "turn"
    start: tuple[float, float]
    end: tuple[float, float]
    heading: float
    direction: int = 1
    lane: str = ""
    loaded: bool = False
    start_heading: Optional[float] = None
    target_heading: Optional[float] = None

    @property
    def distance(self) -> float:
        return math.dist(self.start, self.end)

    def as_dict(self) -> dict:
        return {
            "index": int(self.index),
            "kind": self.kind,
            "start": [round(float(v), 6) for v in self.start],
            "end": [round(float(v), 6) for v in self.end],
            "heading": round(float(self.heading), 6),
            "direction": int(self.direction),
            "lane": self.lane,
            "loaded": bool(self.loaded),
            "distance": round(float(self.distance), 6),
            "start_heading": (None if self.start_heading is None
                              else round(float(self.start_heading), 6)),
            "target_heading": (None if self.target_heading is None
                               else round(float(self.target_heading), 6)),
        }


@dataclass(frozen=True)
class RouteSpec4:
    """阶段四多航段路线: 起点/终点 dock、负载状态与航段序列."""

    route_id: str
    start_dock: str
    end_dock: str
    start: tuple[float, float]
    end: tuple[float, float]
    legs: tuple[RouteLeg, ...]
    lanes: tuple[str, ...] = ()
    loaded: bool = False
    speed: float = cfg.AGV_ROUTE_SPEED
    arrive_tol: float = cfg.AGV_ROUTE_ARRIVE_TOL
    heading_tol: float = cfg.AGV_ROUTE_HEADING_TOL

    @property
    def straight_legs(self) -> tuple[RouteLeg, ...]:
        return tuple(leg for leg in self.legs if leg.kind == "straight")

    @property
    def turn_legs(self) -> tuple[RouteLeg, ...]:
        return tuple(leg for leg in self.legs if leg.kind == "turn")

    @property
    def direction(self) -> int:
        legs = self.straight_legs
        return legs[0].direction if legs else 1

    @property
    def distance(self) -> float:
        return sum(leg.distance for leg in self.straight_legs)

    def to_route_spec(self, leg: RouteLeg) -> RouteSpec:
        return RouteSpec(
            route_id=f"{self.route_id}:L{leg.index}",
            start=tuple(leg.start), end=tuple(leg.end),
            heading=float(leg.heading), direction=int(leg.direction),
            speed=float(self.speed), loaded=bool(self.loaded),
            arrive_tol=float(self.arrive_tol),
            heading_tol=float(self.heading_tol))

    def as_dict(self) -> dict:
        return {
            "route_id": self.route_id,
            "start_dock": self.start_dock,
            "end_dock": self.end_dock,
            "start": [round(float(v), 6) for v in self.start],
            "end": [round(float(v), 6) for v in self.end],
            "loaded": bool(self.loaded),
            "distance": round(float(self.distance), 6),
            "lanes": list(self.lanes),
            "straight_legs": len(self.straight_legs),
            "turn_legs": len(self.turn_legs),
            "legs": [leg.as_dict() for leg in self.legs],
        }


def _merge_collinear(layout, segments: list[tuple[str, str, str]]
                     ) -> list[tuple[str, str, str]]:
    """合并同一直道上的连续段 (A→B→J_SW 视为一段)."""
    merged: list[tuple[str, str, str]] = []
    for start, end, lane in segments:
        if merged:
            p_start, p_end, p_lane = merged[-1]
            if p_lane == lane:
                ax = layout.agv_nodes[p_start]
                bx = layout.agv_nodes[p_end]
                cx = layout.agv_nodes[end]
                v1 = (bx[0] - ax[0], bx[1] - ax[1])
                v2 = (cx[0] - bx[0], cx[1] - bx[1])
                n1 = math.hypot(*v1)
                n2 = math.hypot(*v2)
                if n1 > 1e-9 and n2 > 1e-9 and \
                        abs(v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2) \
                        >= 0.999999:
                    merged[-1] = (p_start, end, p_lane)
                    continue
        merged.append((start, end, lane))
    return merged


def make_route_spec(layout, start_dock: str, end_dock: str,
                    loaded: bool) -> RouteSpec4:
    """按路网最短路径生成多航段路线 (含转向段)."""
    docks = layout.dock_positions
    if start_dock not in docks or end_dock not in docks:
        raise ValueError(f"未知 dock: {start_dock!r} → {end_dock!r}")
    path = shortest_node_path(layout, start_dock, end_dock)
    segments = [(path[i], path[i + 1],
                 lane_for(layout, path[i], path[i + 1]))
                for i in range(len(path) - 1)]
    segments = _merge_collinear(layout, segments)

    legs: list[RouteLeg] = []
    lanes: list[str] = []
    current_heading = float(cfg.AGV_INITIAL_YAW)
    for start, end, lane in segments:
        a = tuple(layout.agv_nodes[start])
        b = tuple(layout.agv_nodes[end])
        heading = heading_for_leg(a, b)
        if lane not in lanes:
            lanes.append(lane)
        if abs(wrap_angle(heading - current_heading)) > 1e-6:
            legs.append(RouteLeg(
                index=len(legs), kind="turn", start=a, end=a,
                heading=float(heading), direction=1, lane=lane,
                loaded=bool(loaded), start_heading=current_heading,
                target_heading=float(heading)))
        legs.append(RouteLeg(
            index=len(legs), kind="straight", start=a, end=b,
            heading=float(heading),
            direction=route_direction(a, b, heading), lane=lane,
            loaded=bool(loaded)))
        current_heading = heading

    return RouteSpec4(
        route_id=f"{start_dock}_to_{end_dock}"
                 f"{'_loaded' if loaded else '_empty'}",
        start_dock=start_dock, end_dock=end_dock,
        start=tuple(docks[start_dock]), end=tuple(docks[end_dock]),
        legs=tuple(legs), lanes=tuple(lanes), loaded=bool(loaded))


# ═══════════════════════════════════════════════════════════════
# 直道横向纠偏
# ═══════════════════════════════════════════════════════════════

class _StraightLegController(_BaseRouteController):
    """阶段三直道控制器 + 横向纠偏.

    原地转向后车体中心可能偏离下一段直道 2–4 cm; 阶段三控制器只按
    目标点距离和航向行驶, 不会消除横向偏差. 本类在行驶中按前视距离
    给航向叠加一个小偏置, 把车体拉回 Lane 中心; 距目标 0.10 m 内恢复
    原航向, 停靠与航向验收口径不变.
    """

    def __init__(self, sim, agv, spec: RouteSpec, **kwargs) -> None:
        super().__init__(sim, agv, spec, **kwargs)
        self._lane_heading = float(spec.heading)
        self._lane_start = tuple(spec.start)
        self._lane_end = tuple(spec.end)
        ux = self._lane_end[0] - self._lane_start[0]
        uy = self._lane_end[1] - self._lane_start[1]
        norm = math.hypot(ux, uy)
        self._lane_u = (ux / norm, uy / norm) if norm > 1e-9 else (0.0, 0.0)

    def step(self, sim_time: float) -> RouteState:
        if self.state not in (RouteState.DONE, RouteState.FAILED,
                              RouteState.BLOCKED) and self._lane_u != (0.0,
                                                                       0.0):
            pose = self._pose()
            if pose is not None:
                dx = float(pose[0]) - self._lane_start[0]
                dy = float(pose[1]) - self._lane_start[1]
                nx, ny = -self._lane_u[1], self._lane_u[0]
                cross = dx * nx + dy * ny
                remaining = math.hypot(
                    self._lane_end[0] - float(pose[0]),
                    self._lane_end[1] - float(pose[1]))
                if remaining > 0.10:
                    offset = -math.atan2(
                        cross, cfg.AGV_LANE_LOOKAHEAD)
                    offset = max(-cfg.AGV_LANE_MAX_HEADING_OFFSET,
                                 min(cfg.AGV_LANE_MAX_HEADING_OFFSET,
                                     offset))
                    heading = self._lane_heading + offset
                else:
                    heading = self._lane_heading
                self.spec = replace(self.spec, heading=heading)
        return super().step(sim_time)


# ═══════════════════════════════════════════════════════════════
# 原地转向
# ═══════════════════════════════════════════════════════════════

class _TurnLegController:
    """停车原地转向; 只用差速轮速, 不写车体位姿."""

    def __init__(self, sim, agv: PioneerController, leg: RouteLeg,
                 material_manager=None, uid: Optional[int] = None,
                 logger=None, timeout_s: Optional[float] = None) -> None:
        self.sim = sim
        self.agv = agv
        self.leg = leg
        self.materials = material_manager
        self.uid = uid
        self.logger = logger
        self.timeout_s = float(cfg.AGV_TURN_TIMEOUT if timeout_s is None
                               else timeout_s)
        self.state = RouteState.IDLE
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None
        self.failure_reason = ""
        self._settle_count = 0
        self._resume_speed = False
        self._block_window_start: Optional[float] = None
        self._block_window_yaw: Optional[float] = None
        self._last_sim_time = 0.0
        self._start_yaw: Optional[float] = None
        self._start_material_local: Optional[list[float]] = None
        self.metrics = RouteMetrics(route_id=leg_as_id(leg),
                                    loaded=bool(leg.loaded))

    @property
    def finished(self) -> bool:
        return self.state in (RouteState.DONE, RouteState.FAILED)

    @property
    def done(self) -> bool:
        return self.state is RouteState.DONE

    @property
    def blocked(self) -> bool:
        return self.state is RouteState.BLOCKED

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
        yaw = pose_yaw(pose)
        error = wrap_angle(float(self.leg.target_heading) - yaw)

        if self._blocked_now(sim_time, yaw, error):
            return self.state

        if self.state is RouteState.SETTLING:
            self.agv.stop()
            self._settle_count = (self._settle_count + 1
                                  if self.agv.is_stopped() else 0)
            if self._settle_count >= cfg.AGV_TURN_SETTLE_STEPS:
                if abs(error) > cfg.AGV_ROUTE_HEADING_TOL:
                    return self._fail(
                        f"TURN_HEADING_ERROR: {error:.4f} rad > "
                        f"{cfg.AGV_ROUTE_HEADING_TOL:.4f} rad")
                return self._finish(sim_time, pose, error)
            if sim_time - self.started_at > self.timeout_s:
                return self._fail("TURN_SETTLE_TIMEOUT")
            return self.state

        if abs(error) <= cfg.AGV_TURN_STOP_TOL:
            self.state = RouteState.SETTLING
            self._settle_count = 0
            self.agv.stop()
            return self.state

        if sim_time - self.started_at > self.timeout_s:
            return self._fail(f"TURN_TIMEOUT: {self.timeout_s:.1f}s")

        rate = cfg.AGV_TURN_GAIN * error
        rate = max(-cfg.AGV_TURN_MAX_RATE,
                   min(cfg.AGV_TURN_MAX_RATE, rate))
        if 0.0 < abs(rate) < cfg.AGV_TURN_MIN_RATE:
            rate = math.copysign(cfg.AGV_TURN_MIN_RATE, error)
        self.agv.set_cmd(0.0, rate)
        return self.state

    def resume(self, sim_time: float, *, clearance_ok: bool,
               reason: str = "") -> bool:
        if self.state is not RouteState.BLOCKED:
            return False
        if not clearance_ok:
            return False
        self.state = RouteState.DRIVING
        self._block_window_start = None
        self._block_window_yaw = None
        self.metrics.resumed.append({
            "sim_time": round(float(sim_time), 4),
            "reason": reason or "clearance_confirmed"})
        return True

    # ── 内部 ────────────────────────────────────────────────

    def _begin(self, sim_time: float, pose) -> None:
        self.state = RouteState.DRIVING
        self.started_at = float(sim_time)
        self._start_yaw = pose_yaw(pose)
        self.metrics.start_pose = list(pose)
        self.metrics.target_pose = [
            float(pose[0]), float(pose[1]), float(pose[2]),
            *quat_from_yaw(float(self.leg.target_heading))]
        material = self._material_pose()
        if material is not None:
            self._start_material_local = _local_offset(pose, material)
            self.metrics.material_relative_start = [
                round(v, 6) for v in self._start_material_local]
        if self.logger:
            self.logger.info(
                "[AGV_TURN] %s %.3f → %.3f rad",
                self.metrics.route_id, float(self.leg.start_heading),
                float(self.leg.target_heading))

    def _finish(self, sim_time: float, pose, error: float) -> RouteState:
        self.state = RouteState.DONE
        self.finished_at = float(sim_time)
        self.metrics.end_pose = list(pose)
        self.metrics.stop_error = 0.0
        self.metrics.heading_error = float(error)
        self.metrics.duration_s = float(sim_time) - float(self.started_at)
        material = self._material_pose()
        if material is not None:
            end_local = _local_offset(pose, material)
            self.metrics.material_relative_end = [round(v, 6)
                                                  for v in end_local]
            if self._start_material_local is not None:
                self.metrics.material_slip = math.hypot(
                    end_local[0] - self._start_material_local[0],
                    end_local[1] - self._start_material_local[1])
        return self.state

    def _blocked_now(self, sim_time: float, yaw: float,
                     error: float) -> bool:
        if self.state is RouteState.SETTLING:
            return False
        commanded = abs(getattr(self.agv, "_cmd_w", 0.0))
        if commanded <= 0.02:
            self._block_window_start = None
            self._block_window_yaw = None
            return False
        if self._block_window_start is None:
            self._block_window_start = float(sim_time)
            self._block_window_yaw = float(yaw)
            return False
        if float(sim_time) - self._block_window_start < \
                cfg.AGV_TURN_BLOCK_TIMEOUT:
            return False
        moved = abs(wrap_angle(yaw - float(self._block_window_yaw)))
        if moved >= cfg.AGV_TURN_BLOCK_YAW:
            self._block_window_start = float(sim_time)
            self._block_window_yaw = float(yaw)
            return False
        self.agv.stop()
        self.state = RouteState.BLOCKED
        self.metrics.blocked = {
            "sim_time": round(float(sim_time), 4),
            "commanded_rate": round(float(commanded), 4),
            "moved_rad": round(moved, 6),
            "window_s": cfg.AGV_TURN_BLOCK_TIMEOUT,
            "yaw": round(float(yaw), 6),
            "error": round(float(error), 6),
        }
        if self.logger:
            self.logger.warning(
                "[AGV_TURN] %s BLOCKED: 角速度已下发但 %.2fs 内仅转 %.4f rad",
                self.metrics.route_id, cfg.AGV_TURN_BLOCK_TIMEOUT, moved)
        return True

    def _fail(self, reason: str) -> RouteState:
        self.agv.stop()
        self.state = RouteState.FAILED
        self.failure_reason = str(reason)
        self.finished_at = self._last_sim_time
        if self.logger:
            self.logger.error("[AGV_TURN] %s FAILED: %s",
                              self.metrics.route_id, reason)
        return self.state

    def _pose(self):
        try:
            return list(self.agv.pose())
        except Exception:
            return None

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

    def summary(self) -> dict:
        return {
            "state": self.state.value,
            "failure_reason": self.failure_reason,
            "spec": self.leg.as_dict(),
            "metrics": self.metrics.as_dict(),
            "version": cfg.STAGE4_AGV_ROUTE_VERSION,
        }


def leg_as_id(leg: RouteLeg) -> str:
    return f"L{leg.index}_{leg.kind}"


def _local_offset(pose, point) -> list[float]:
    """世界点相对车体坐标系的偏移 (旋转 -yaw, 不平移 Z)."""
    yaw = pose_yaw(pose)
    dx = float(point[0]) - float(pose[0])
    dy = float(point[1]) - float(pose[1])
    dz = float(point[2]) - float(pose[2])
    c, s = math.cos(-yaw), math.sin(-yaw)
    return [c * dx - s * dy, s * dx + c * dy, dz]


# ═══════════════════════════════════════════════════════════════
# 多航段执行器
# ═══════════════════════════════════════════════════════════════

class AGVRouteController4:
    """阶段四航线执行器: 逐段直道 + 原地转向, 汇总停靠与滑移证据."""

    def __init__(self, sim, agv, spec: RouteSpec4, *,
                 material_manager=None, uid: Optional[int] = None,
                 logger=None, timeout_s: Optional[float] = None) -> None:
        self.sim = sim
        self.agv = agv
        self.spec = spec
        self.uid = uid
        self.materials = material_manager
        self.logger = logger
        self.timeout_s = float(cfg.AGV_ROUTE_TIMEOUT
                               if timeout_s is None else timeout_s)
        self.state = RouteState.IDLE
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None
        self.failure_reason = ""
        self._index = 0
        self._leg = None
        self._leg_summaries: list[dict] = []
        self._start_material_local: Optional[list[float]] = None
        self._max_leg_slip: Optional[float] = None
        self.metrics = RouteMetrics(route_id=spec.route_id,
                                    loaded=bool(spec.loaded))

    # ── 状态 ────────────────────────────────────────────────

    @property
    def finished(self) -> bool:
        return self.state in (RouteState.DONE, RouteState.FAILED)

    @property
    def done(self) -> bool:
        return self.state is RouteState.DONE

    @property
    def blocked(self) -> bool:
        return self.state is RouteState.BLOCKED

    # ── 主步进 ──────────────────────────────────────────────

    def step(self, sim_time: float) -> RouteState:
        if self.state in (RouteState.DONE, RouteState.FAILED):
            return self.state
        if self.state is RouteState.BLOCKED:
            self.agv.stop()
            return self.state
        if self.started_at is None:
            self._begin(sim_time)
        if self._leg is None and not self._start_next_leg(sim_time):
            return self.state
        result = self._leg.step(sim_time)
        if result is RouteState.DONE:
            self._leg_summaries.append(self._leg.summary())
            slip = self._leg.metrics.material_slip
            if slip is not None:
                self._max_leg_slip = max(self._max_leg_slip or 0.0, slip)
            self._leg = None
            self._index += 1
            if self._index >= len(self.spec.legs):
                return self._finish(sim_time)
            self.state = RouteState.DRIVING
            return self.state
        if result is RouteState.BLOCKED:
            self.state = RouteState.BLOCKED
            self.metrics.blocked = dict(getattr(self._leg.metrics,
                                                "blocked", {}) or {})
            return self.state
        if result is RouteState.FAILED:
            self._fail(f"LEG_FAILED: {self._leg.failure_reason}")
            return self.state
        self.state = result
        return self.state

    def resume(self, sim_time: float, *, clearance_ok: bool,
               reason: str = "") -> bool:
        if self.state is not RouteState.BLOCKED or self._leg is None:
            return False
        if not self._leg.resume(sim_time, clearance_ok=clearance_ok,
                                reason=reason):
            return False
        self.state = RouteState.DRIVING
        self.metrics.resumed.append({
            "sim_time": round(float(sim_time), 4),
            "reason": reason or "clearance_confirmed",
            "leg": self._index,
        })
        return True

    # ── 内部 ────────────────────────────────────────────────

    def _begin(self, sim_time: float) -> None:
        self.started_at = float(sim_time)
        pose = self._pose()
        if pose is not None:
            self.metrics.start_pose = list(pose)
            material = self._material_pose()
            if material is not None:
                self._start_material_local = _local_offset(pose, material)
                self.metrics.material_relative_start = [
                    round(v, 6) for v in self._start_material_local]
        if self.logger:
            self.logger.info(
                "[AGV_ROUTE4] %s 开始 loaded=%s legs=%d lanes=%s",
                self.spec.route_id, self.spec.loaded, len(self.spec.legs),
                list(self.spec.lanes))

    def _start_next_leg(self, sim_time: float) -> bool:
        if self._index >= len(self.spec.legs):
            self._finish(sim_time)
            return False
        leg = self.spec.legs[self._index]
        try:
            if leg.kind == "turn":
                self._leg = _TurnLegController(
                    self.sim, self.agv, leg,
                    material_manager=self.materials,
                    uid=self.uid if leg.loaded else None,
                    logger=self.logger)
            else:
                self._leg = _StraightLegController(
                    self.sim, self.agv, self.spec.to_route_spec(leg),
                    material_manager=self.materials,
                    uid=self.uid if leg.loaded else None,
                    logger=self.logger, timeout_s=self.timeout_s)
        except Exception as exc:
            self._fail(f"LEG_BUILD_FAILED: {type(exc).__name__}: {exc}")
            return False
        return True

    def _finish(self, sim_time: float) -> RouteState:
        pose = self._pose()
        self.state = RouteState.DONE
        self.finished_at = float(sim_time)
        self.metrics.duration_s = float(sim_time) - float(self.started_at)
        if pose is not None:
            self.metrics.end_pose = list(pose)
            last = self._leg_summaries[-1]["metrics"] if \
                self._leg_summaries else {}
            self.metrics.stop_error = last.get("stop_error")
            self.metrics.heading_error = last.get("heading_error")
            material = self._material_pose()
            if material is not None:
                end_local = _local_offset(pose, material)
                self.metrics.material_relative_end = [
                    round(v, 6) for v in end_local]
                if self._start_material_local is not None:
                    self.metrics.material_slip = math.hypot(
                        end_local[0] - self._start_material_local[0],
                        end_local[1] - self._start_material_local[1])
        if self._max_leg_slip is not None:
            self.metrics.material_slip = max(
                self.metrics.material_slip or 0.0, self._max_leg_slip)
        if self.logger:
            self.logger.info(
                "[AGV_ROUTE4] %s 完成 duration=%.2fs stop=%s heading=%s "
                "slip=%s legs=%d",
                self.spec.route_id, self.metrics.duration_s,
                self.metrics.stop_error, self.metrics.heading_error,
                self.metrics.material_slip, len(self.spec.legs))
        return self.state

    def _fail(self, reason: str) -> None:
        self.agv.stop()
        self.state = RouteState.FAILED
        self.failure_reason = str(reason)
        self.finished_at = self._last_time()
        if self.logger:
            self.logger.error("[AGV_ROUTE4] %s FAILED: %s",
                              self.spec.route_id, reason)

    def _last_time(self) -> float:
        return float(self.metrics.duration_s or 0.0)

    def _pose(self):
        try:
            return list(self.agv.pose())
        except Exception:
            return None

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

    def summary(self) -> dict:
        data = {
            "state": self.state.value,
            "failure_reason": self.failure_reason,
            "spec": self.spec.as_dict(),
            "metrics": self.metrics.as_dict(),
            "legs": list(self._leg_summaries),
            "version": cfg.STAGE4_AGV_ROUTE_VERSION,
        }
        return data


# ═══════════════════════════════════════════════════════════════
# 路线图描述
# ═══════════════════════════════════════════════════════════════

def route_map(layout) -> dict:
    """路线图描述 (契约/报告用, 不执行)."""
    docks = layout.dock_positions
    legs = []
    for start in cfg.AGV_DOCK_IDS:
        for end in cfg.AGV_DOCK_IDS:
            if start == end:
                continue
            spec = make_route_spec(layout, start, end, loaded=True)
            legs.append({
                "route_id": spec.route_id,
                "start": start, "end": end,
                "distance": round(spec.distance, 4),
                "direction": spec.direction,
                "lanes": list(spec.lanes),
                "straight_legs": len(spec.straight_legs),
                "turn_legs": len(spec.turn_legs),
                "path": [leg.start for leg in spec.straight_legs]
                        + [spec.straight_legs[-1].end]
                        if spec.straight_legs else [],
            })
    return {
        "version": cfg.STAGE4_AGV_ROUTE_VERSION,
        "nodes": {key: list(value)
                  for key, value in layout.agv_nodes.items()},
        "edges": [{"start": edge.start, "end": edge.end,
                   "lane": edge.lane,
                   "length": round(edge.length(layout.agv_nodes), 4)}
                  for edge in layout.agv_edges],
        "docks": {key: list(value) for key, value in docks.items()},
        "initial_dock": cfg.AGV_INITIAL_DOCK,
        "dock_heading": cfg.AGV_INITIAL_YAW,
        "lanes": list(cfg.AGV_LANES),
        "turn_nodes": list(layout.agv_turn_nodes),
        "turn_policy": "stop_then_in_place_turn_no_teleport",
        "turn_radius": round(
            math.hypot(*cfg.AGV_FOOTPRINT_AT_DOCK) / 2.0
            + cfg.AGV_ROUTE_CLEARANCE, 6),
        "routes": legs,
        "loaded_routes": ["B_to_A", "D_to_E"],
        "empty_routes": "scheduler_selected",
        "priority": {"B_to_A": 0, "D_to_E": 1},
        "starvation_upgrade_s": cfg.D_E_STARVATION_UPGRADE_S,
        "preemption": False,
        "arrive_tol": cfg.AGV_ROUTE_ARRIVE_TOL,
        "heading_tol": cfg.AGV_ROUTE_HEADING_TOL,
        "stop_vel_tol": cfg.AGV_ROUTE_STOP_VEL_TOL,
        "slip_tol": cfg.AGV_ROUTE_SLIP_TOL,
        "timeout_s": cfg.AGV_ROUTE_TIMEOUT,
        "auto_retry": False,
    }


__all__ = ["RouteLeg", "RouteSpec4", "AGVRouteController4",
           "make_route_spec", "route_direction", "route_map", "pose_yaw",
           "RouteState", "wrap_angle"]
