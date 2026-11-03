"""v1 双站层 AGV 路线控制测试 (纯逻辑/假车体, 不需要 CoppeliaSim)."""

import math

from flexible_factory.simulation import config_dual as cfg
from flexible_factory.simulation.agv_route import (AGVRouteController, RouteSpec, RouteState,
                              pose_yaw, route_specs, wrap_angle)
from flexible_factory.simulation.layout_dual import default_layout, quat_from_yaw


class FakeAGV:
    def __init__(self, x=0.40, y=0.70, yaw=-math.pi / 2, move=True):
        self.x, self.y, self.yaw = float(x), float(y), float(yaw)
        self.move = move
        self._cmd_v = 0.0
        self._cmd_w = 0.0
        self.stopped = True
        self.last_speeds = type("S", (), {"left": 0.0, "right": 0.0})()

    def pose(self):
        return [self.x, self.y, 0.0, *quat_from_yaw(self.yaw)]

    def set_cmd(self, v, w, dt=cfg.base.SIM_STEP):
        self._cmd_v, self._cmd_w = float(v), float(w)
        self.stopped = abs(v) < 1e-6 and abs(w) < 1e-6
        if self.move:
            # 简化运动学: 直接按指令推进
            self.x += math.cos(self.yaw) * v * dt
            self.y += math.sin(self.yaw) * v * dt
            self.yaw = wrap_angle(self.yaw + w * dt)

    def stop(self):
        self._cmd_v = self._cmd_w = 0.0
        self.stopped = True

    def is_stopped(self):
        return self.stopped

    def is_upright(self):
        return True

    def _velocity(self):
        return ([self._cmd_v, 0.0, 0.0], [0.0, 0.0, self._cmd_w])


class FakeSim:
    handle_world = -1
    simulation_stopped = 0

    def getSimulationTime(self):
        return 0.0


def test_route_specs_direction_and_loading():
    specs = route_specs(default_layout())
    assert specs["A_to_B"].direction == 1 and specs["A_to_B"].loaded
    assert specs["B_to_A"].direction == -1 and not specs["B_to_A"].loaded
    assert specs["A_to_B"].start == cfg.AGV_DOCK_A
    assert specs["A_to_B"].end == cfg.AGV_DOCK_B


def test_route_drives_to_dock_b_and_stops_within_tolerance():
    agv = FakeAGV()
    spec = RouteSpec("A_to_B", start=cfg.AGV_DOCK_A, end=cfg.AGV_DOCK_B,
                     heading=-math.pi / 2, direction=1, loaded=False,
                     speed=0.20)
    route = AGVRouteController(FakeSim(), agv, spec)
    t = 0.0
    for _ in range(2000):
        if route.finished:
            break
        t += cfg.base.SIM_STEP
        route.step(t)
    assert route.state is RouteState.DONE, route.summary()
    metrics = route.metrics
    assert metrics.stop_error is not None
    assert metrics.stop_error <= cfg.AGV_ROUTE_ARRIVE_TOL
    assert abs(metrics.heading_error or 0.0) <= cfg.AGV_ROUTE_HEADING_TOL
    assert metrics.samples > 0


def test_route_blocks_when_wheels_commanded_but_body_stuck():
    agv = FakeAGV(move=False)
    spec = RouteSpec("A_to_B", start=cfg.AGV_DOCK_A, end=cfg.AGV_DOCK_B,
                     heading=-math.pi / 2, direction=1, loaded=False)
    route = AGVRouteController(FakeSim(), agv, spec)
    t = 0.0
    for _ in range(200):
        t += cfg.base.SIM_STEP
        route.step(t)
        if route.blocked:
            break
    assert route.state is RouteState.BLOCKED
    assert route.metrics.blocked["moved_m"] <= cfg.AGV_ROUTE_BLOCK_DISPLACEMENT
    assert not route.resume(t, clearance_ok=False)
    assert route.state is RouteState.BLOCKED
    assert route.resume(t + 1.0, clearance_ok=True, reason="cleared")
    assert route.state is RouteState.DRIVING
    assert route.metrics.resumed


def test_route_reverse_direction_moves_toward_positive_y():
    agv = FakeAGV(x=cfg.AGV_DOCK_B[0], y=cfg.AGV_DOCK_B[1],
                  yaw=-math.pi / 2)
    spec = RouteSpec("B_to_A", start=cfg.AGV_DOCK_B, end=cfg.AGV_DOCK_A,
                     heading=-math.pi / 2, direction=-1, loaded=False,
                     speed=0.20)
    route = AGVRouteController(FakeSim(), agv, spec)
    t = 0.0
    for _ in range(2000):
        if route.finished:
            break
        t += cfg.base.SIM_STEP
        route.step(t)
    assert route.state is RouteState.DONE
    assert agv.y > cfg.AGV_DOCK_B[1] + 0.5
    assert route.metrics.stop_error <= cfg.AGV_ROUTE_ARRIVE_TOL


def test_pose_yaw_and_wrap():
    assert abs(pose_yaw([0, 0, 0, *quat_from_yaw(0.4)]) - 0.4) < 1e-9
    # wrap_angle 的值域是 [-pi, pi); 3*pi 等价于 -pi
    assert abs(abs(wrap_angle(3 * math.pi)) - math.pi) < 1e-9
    assert abs(wrap_angle(-3 * math.pi) + math.pi) < 1e-9


def test_route_spec_serialization():
    spec = RouteSpec("x", start=(0, 0), end=(0, 1), heading=0.0)
    data = spec.as_dict()
    assert data["route_id"] == "x"
    assert data["direction"] == 1
    assert data["loaded"] is False


def test_route_timeout_can_be_overridden():
    spec = route_specs(default_layout())["A_to_B"]
    default = AGVRouteController(FakeSim(), FakeAGV(), spec)
    assert default.timeout_s == cfg.AGV_ROUTE_TIMEOUT
    long_route = AGVRouteController(
        FakeSim(), FakeAGV(), spec, timeout_s=150.0)
    assert long_route.timeout_s == 150.0
