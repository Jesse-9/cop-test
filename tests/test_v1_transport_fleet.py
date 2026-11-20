"""双车编制骨架（transport_fleet.py）的离线测试.

不需要 CoppeliaSim：执行器通过注入 mover 记录位姿，验证
  1. single 模式映射不变（默认行为零变化）；
  2. dual 模式的固定分工；
  3. 运动学执行器与 AgvExecutor 的 start/pre_tick/poll 契约；
  4. 路线插值、超时失败、设备不匹配拒绝。
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from flexible_factory.simulation import transport_fleet as tf
from flexible_factory.simulation.execution.base import (
    ExecutionRequest, ExecutionState, ExecutorError)
from flexible_factory.simulation.transport_schedule import NODES, plan_route


@dataclass
class _RouteSpec:
    start_dock: str
    end_dock: str


def _request(start: str = "D", end: str = "E", sim_time: float = 0.0,
             device_id: str = "cart_2") -> ExecutionRequest:
    return ExecutionRequest(operation=None, task=None, device_id=device_id,
                            uid=4, task_id="t1", sim_time=sim_time,
                            route_spec=_RouteSpec(start, end))


# ═══════════════════════════════════════════════════════════════
# 1. 编制与分工
# ═══════════════════════════════════════════════════════════════

def test_single_mode_keeps_everything_on_agv():
    config = tf.FleetConfig()
    assert config.mode == tf.FLEET_SINGLE
    for kind in ("transport_ba", "transport_de", "a_agv", "whatever"):
        assert config.vehicle_for(kind) == "agv_1"


def test_dual_mode_assigns_by_kind():
    config = tf.FleetConfig(tf.FLEET_DUAL)
    assert config.vehicle_for("transport_ba") == "agv_1"
    assert config.vehicle_for("transport_de") == "cart_2"
    assert config.vehicle_for("d_conveyor") == "cart_2"
    assert config.vehicle_for("e_agv") == "cart_2"
    assert config.vehicle_for("unknown_kind") == "agv_1"
    assert config.as_dict()["assignment"]["transport_de"] == "cart_2"


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError):
        tf.FleetConfig("triple")


def test_normalize_fleet_mode_defaults_and_rejects_unknown():
    assert tf.normalize_fleet_mode(None) == tf.FLEET_SINGLE
    assert tf.normalize_fleet_mode("") == tf.FLEET_SINGLE
    assert tf.normalize_fleet_mode(" Dual ") == tf.FLEET_DUAL
    with pytest.raises(ValueError, match="车队模式"):
        tf.normalize_fleet_mode("triple")


def test_fleet_config_reports_policy_and_verified_pairs():
    config = tf.FleetConfig(tf.FLEET_DUAL)
    payload = config.as_dict()
    assert payload["policy_version"] == tf.FLEET_POLICY_VERSION
    assert ["cart_2", "transport_DE"] in payload["verified_pairs"]
    assert payload["assignment"]["transport_de"] == "cart_2"
    single = tf.FleetConfig().as_dict()
    assert single["verified_pairs"] == []
    assert single["assignment"] == {}


def test_effective_candidates_follow_fleet_assignment():
    from flexible_factory.application.coordinator import Stage4Coordinator
    from flexible_factory.domain.default_process import default_catalog

    coordinator = object.__new__(Stage4Coordinator)
    template = default_catalog().operation("transport_DE")
    candidates = tuple(template.candidate_devices)

    coordinator.fleet_config = tf.FleetConfig(tf.FLEET_SINGLE)
    assert coordinator._effective_candidates(template, candidates) == \
        (template.candidate_devices[0],)

    coordinator.fleet_config = tf.FleetConfig(tf.FLEET_DUAL)
    assert coordinator._effective_candidates(template, candidates) == \
        ("cart_2",)


def test_fleet_assignment_is_validated_against_catalog():
    from types import SimpleNamespace

    from flexible_factory.application.coordinator import (CoordinatorError,
                                                          Stage4Coordinator)

    template = SimpleNamespace(
        id="transport_DE", kind="transport_de", executor_kind="agv",
        candidate_devices=("agv_1",))
    coordinator = object.__new__(Stage4Coordinator)
    coordinator.catalog = SimpleNamespace(
        recipes=(SimpleNamespace(operations=(template,)),))
    coordinator._validate_fleet_assignment(tf.FleetConfig(tf.FLEET_SINGLE))
    with pytest.raises(CoordinatorError, match="FLEET_ASSIGNMENT_INVALID"):
        coordinator._validate_fleet_assignment(tf.FleetConfig(tf.FLEET_DUAL))


# ═══════════════════════════════════════════════════════════════
# 2. 路线插值
# ═══════════════════════════════════════════════════════════════

def test_pose_along_route_endpoints_and_middle():
    route = plan_route("D", "E")
    x0, y0, _ = tf.pose_along_route(route, 0.0)
    x1, y1, _ = tf.pose_along_route(route, 1.0)
    xm, ym, _ = tf.pose_along_route(route, 0.5)
    assert (x0, y0) == pytest.approx(NODES["D"])
    assert (x1, y1) == pytest.approx(NODES["E"])
    assert ym == pytest.approx((NODES["D"][1] + NODES["E"][1]) / 2)


def test_pose_along_route_clamps_out_of_range():
    route = plan_route("D", "E")
    assert tf.pose_along_route(route, -1.0)[:2] == pytest.approx(NODES["D"])
    assert tf.pose_along_route(route, 9.0)[:2] == pytest.approx(NODES["E"])


# ═══════════════════════════════════════════════════════════════
# 3. 执行器契约
# ═══════════════════════════════════════════════════════════════

def test_executor_runs_to_completion():
    trace: list[tuple[float, float]] = []
    executor = tf.KinematicTransportExecutor(
        mover=lambda x, y, yaw: trace.append((x, y)))
    execution_id = executor.start(_request())
    assert executor.active is True

    report = executor.poll(execution_id)
    assert report.state is ExecutionState.RUNNING
    assert report.finished is False

    # D→E 长 2.7 m, 0.10 m/s → 27 s; 推进到 30 s 应完成
    executor.pre_tick(30.0)
    report = executor.poll(execution_id)
    assert report.state is ExecutionState.SUCCEEDED
    assert report.finished is True
    assert executor.active is False
    assert trace[0] == pytest.approx(NODES["D"])
    assert trace[-1][1] == pytest.approx(NODES["E"][1], abs=1e-6)


def test_executor_pose_moves_monotonically():
    trace: list[float] = []
    executor = tf.KinematicTransportExecutor(
        mover=lambda x, y, yaw: trace.append(y))
    executor.start(_request())
    for sim_time in range(0, 31, 5):
        executor.pre_tick(float(sim_time))
    assert trace == sorted(trace)
    assert trace[-1] == pytest.approx(NODES["E"][1], abs=1e-6)


def test_executor_rejects_wrong_device():
    executor = tf.KinematicTransportExecutor(mover=lambda *a: None)
    with pytest.raises(ExecutorError):
        executor.start(_request(device_id="agv_1"))


def test_executor_rejects_unknown_execution_id():
    executor = tf.KinematicTransportExecutor(mover=lambda *a: None)
    executor.start(_request())
    with pytest.raises(ExecutorError):
        executor.poll("cart_2#999")


def test_executor_late_tick_still_arrives_at_end():
    """进度按仿真时间推进: 一次大步跳也会停在终点并判定成功。"""
    trace: list[tuple[float, float]] = []
    executor = tf.KinematicTransportExecutor(
        mover=lambda x, y, yaw: trace.append((x, y)))
    execution_id = executor.start(_request())
    executor.pre_tick(1000.0)
    report = executor.poll(execution_id)
    assert report.state is ExecutionState.SUCCEEDED
    assert report.detail["progress"] == pytest.approx(1.0)
    assert trace[-1][1] == pytest.approx(NODES["E"][1], abs=1e-6)


def test_executor_poll_evidence_matches_agv_contract():
    """小车二证据与 AgvExecutor 同形: 验收层不需要按车辆实现分支."""
    executor = tf.KinematicTransportExecutor(mover=lambda *a: None)
    execution_id = executor.start(_request())
    executor.pre_tick(1000.0)
    report = executor.poll(execution_id)
    route = report.evidence["route"]
    assert route["spec"]["loaded"] is True
    assert route["spec"]["start_dock"] == "D"
    assert route["spec"]["end_dock"] == "E"
    assert route["vehicle_id"] == "cart_2"
    assert route["metrics"]["stop_error"] == 0.0
    assert report.detail["vehicle_id"] == "cart_2"


# ═══════════════════════════════════════════════════════════════
# 4. 运行时槽
# ═══════════════════════════════════════════════════════════════

def test_vehicle_runtime_tracks_executor_activity():
    executor = tf.KinematicTransportExecutor(mover=lambda *a: None)
    runtime = tf.VehicleRuntime("cart_2", executor, at_dock="D")
    assert runtime.active is False
    executor.start(_request())
    assert runtime.active is True
    assert runtime.as_dict()["vehicle_id"] == "cart_2"


def test_coordinator_singleton_fields_delegate_to_fleet():
    """协调器的 agv_* 单例字段现在读写同一份车队槽位（行为不变）。"""
    from flexible_factory.application.coordinator import Stage4Coordinator

    coordinator = object.__new__(Stage4Coordinator)
    coordinator.fleet = {"agv_1": tf.VehicleRuntime("agv_1", object(),
                                                    at_dock="A")}

    assert coordinator.agv_at_dock == "A"
    coordinator.agv_at_dock = "D"
    assert coordinator.fleet["agv_1"].at_dock == "D"

    coordinator.route_execution_id = "agv_1#3"
    assert coordinator.route_execution_id == "agv_1#3"
    assert coordinator.fleet["agv_1"].route_execution_id == "agv_1#3"

    coordinator.route_task = None
    assert coordinator.route_task is None
