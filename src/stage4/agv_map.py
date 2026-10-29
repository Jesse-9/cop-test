"""agv_map.py — 阶段四 AGV 直线路线图与航线执行 (06-16 §F4-4).

阶段四所有 dock 共线于 x=0.40, 车头固定朝 -Y, 因此任意两 dock 之间的
路线都是**单段直线**:

    dock_D (-2.45) ── dock_B (-0.70) ── dock_A (0.70) ── dock_E (2.45)

沿 -Y 行驶为前进 (direction=+1), 沿 +Y 为倒车 (direction=-1); 车体
全程不旋转. 底层仍复用阶段三 :class:`stage3.agv_route.AGVRouteController`
的轮速驱动、速度剖面、停靠判据与 BLOCKED 检测, 不写车体位姿.

航线分两类:
  - **loaded** (`B→A` / `D→E`): 带载运输, 记录载荷滑移;
  - **empty** (空载调机): 车辆从当前 dock 前往下一装货 dock.

优先级与等待升级由协调器决定; 本模块只执行已选定的单段路线.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from stage3.agv_route import (AGVRouteController as _BaseRouteController,
                              RouteState, pose_yaw)

from . import config as cfg


def route_direction(start: tuple[float, float],
                    end: tuple[float, float], heading: float) -> int:
    """按车头朝向计算前进/倒车方向 (+1 前进, -1 倒车)."""
    fx, fy = math.cos(heading), math.sin(heading)
    delta = (end[0] - start[0], end[1] - start[1])
    along = delta[0] * fx + delta[1] * fy
    if abs(along) <= 1e-9:
        return 1
    return 1 if along > 0.0 else -1


@dataclass(frozen=True)
class RouteSpec4:
    """阶段四单段路线: 起点/终点 dock、朝向、负载状态."""

    route_id: str
    start_dock: str
    end_dock: str
    start: tuple[float, float]
    end: tuple[float, float]
    heading: float = cfg.AGV_INITIAL_YAW
    loaded: bool = False
    speed: float = cfg.AGV_ROUTE_SPEED
    arrive_tol: float = cfg.AGV_ROUTE_ARRIVE_TOL
    heading_tol: float = cfg.AGV_ROUTE_HEADING_TOL

    @property
    def direction(self) -> int:
        return route_direction(self.start, self.end, self.heading)

    def to_route_spec(self):
        from stage3.agv_route import RouteSpec
        return RouteSpec(
            route_id=self.route_id, start=tuple(self.start),
            end=tuple(self.end), heading=float(self.heading),
            direction=self.direction, speed=float(self.speed),
            loaded=bool(self.loaded), arrive_tol=float(self.arrive_tol),
            heading_tol=float(self.heading_tol))

    def as_dict(self) -> dict:
        return {
            "route_id": self.route_id,
            "start_dock": self.start_dock,
            "end_dock": self.end_dock,
            "start": list(self.start),
            "end": list(self.end),
            "heading": round(float(self.heading), 6),
            "direction": int(self.direction),
            "speed": float(self.speed),
            "loaded": bool(self.loaded),
        }


def make_route_spec(layout, start_dock: str, end_dock: str,
                    loaded: bool) -> RouteSpec4:
    docks = layout.dock_positions
    if start_dock not in docks or end_dock not in docks:
        raise ValueError(f"未知 dock: {start_dock!r} → {end_dock!r}")
    return RouteSpec4(
        route_id=f"{start_dock}_to_{end_dock}"
                 f"{'_loaded' if loaded else '_empty'}",
        start_dock=start_dock, end_dock=end_dock,
        start=tuple(docks[start_dock]), end=tuple(docks[end_dock]),
        heading=layout.agv_initial_yaw, loaded=loaded)


class AGVRouteController4:
    """阶段四航线执行器: 包装阶段三单段路线并补充 dock 证据."""

    def __init__(self, sim, agv, spec: RouteSpec4, *,
                 material_manager=None, uid: Optional[int] = None,
                 logger=None) -> None:
        self.spec = spec
        self.uid = uid
        self._impl = _BaseRouteController(
            sim, agv, spec.to_route_spec(),
            material_manager=material_manager,
            uid=uid if spec.loaded else None,
            logger=logger,
            timeout_s=cfg.AGV_ROUTE_TIMEOUT)

    # ── 状态透传 ────────────────────────────────────────────

    @property
    def state(self) -> RouteState:
        return self._impl.state

    @property
    def metrics(self):
        return self._impl.metrics

    @property
    def blocked(self) -> bool:
        return self._impl.blocked

    @property
    def done(self) -> bool:
        return self._impl.done

    @property
    def finished(self) -> bool:
        return self._impl.finished

    @property
    def failure_reason(self) -> str:
        return self._impl.failure_reason

    def step(self, sim_time: float) -> RouteState:
        return self._impl.step(sim_time)

    def resume(self, sim_time: float, *, clearance_ok: bool,
               reason: str = "") -> bool:
        return self._impl.resume(sim_time, clearance_ok=clearance_ok,
                                 reason=reason)

    def summary(self) -> dict:
        data = self._impl.summary()
        data.update({
            "version": cfg.STAGE4_AGV_ROUTE_VERSION,
            "start_dock": self.spec.start_dock,
            "end_dock": self.spec.end_dock,
        })
        return data


def route_map(layout) -> dict:
    """路线图描述 (契约/报告用, 不执行)."""
    docks = layout.dock_positions
    legs = []
    for start in ("A", "B", "D", "E"):
        for end in ("A", "B", "D", "E"):
            if start == end:
                continue
            spec = make_route_spec(layout, start, end, loaded=True)
            legs.append({
                "route_id": spec.route_id,
                "start": start, "end": end,
                "distance": round(math.dist(docks[start], docks[end]), 4),
                "direction": spec.direction,
            })
    return {
        "version": cfg.STAGE4_AGV_ROUTE_VERSION,
        "axis": {"x": cfg.AGV_ROUTE_X,
                 "y": sorted(round(pos[1], 4) for pos in docks.values())},
        "heading": cfg.AGV_INITIAL_YAW,
        "docks": {key: list(value) for key, value in docks.items()},
        "initial_dock": cfg.AGV_INITIAL_DOCK,
        "turn_policy": "straight_shuttle_no_dock_rotation",
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


__all__ = ["RouteSpec4", "AGVRouteController4", "make_route_spec",
           "route_direction", "route_map", "pose_yaw", "RouteState"]
