"""阶段 5 WP0 业务用例基线 — `cart_2` 不可用时 `transport_DE` 无替代.

本文件只做纯调度快照, 不启动 CoppeliaSim:
- dual 固定分工把 `transport_DE` 收窄到 `cart_2`;
- `cart_2` 不可用 (无有效绑定) 时当前策略没有可派工候选, 支架分支不能
  启动, 订单目标无法达成;
- `agv_1` 是 recipe 首个候选且已在 single 基线上验收; 只有把候选恢复为
  recipe 全集时调度器才会选择它. 这记录的是阶段 5 的基线缺口, 不是
  已实现的故障改派.

`bound=False` 是当前目录模型里最接近“设备不可用”的静态代理; 本阶段没有
设备健康事实源, 因此不把该代理表述为运行时健康状态.
"""

from __future__ import annotations

import json
from pathlib import Path

from flexible_factory.domain.fleet_policy import FleetConfig
from flexible_factory.domain.metrics import recompute_order_metrics
from flexible_factory.domain.scheduling import (DeviceView, MaterialView,
                                                OperationView,
                                                SchedulingSnapshot,
                                                plan_ready)

FIXTURE = (Path(__file__).parent / "fixtures" /
           "stage5_baseline_events.json")


def _transport_de(*, candidates=("cart_2",),
                  vehicle_id="cart_2") -> OperationView:
    return OperationView(
        instance_id="baseline:transport_DE:2",
        task_id="transport_de_uid2", template_id="transport_DE",
        kind="transport_de", uid=2, executor_kind="agv",
        capability="agv_transport",
        candidate_device_ids=tuple(candidates),
        input_zones=("agv",), input_item_types=("PART_DE",),
        destination_zone="agv", source_kind="agv", sink_kind="agv",
        route_origin_dock="D", route_destination_dock="E",
        vehicle_id=str(vehicle_id))


def _cart2(*, bound: bool = True) -> DeviceView:
    return DeviceView(
        device_id="cart_2", kind="agv",
        capabilities=("agv_transport",),
        resources=("agv_lane_east", "dock_D", "dock_E"),
        zones=("agv",), bound=bool(bound))


def _agv1() -> DeviceView:
    return DeviceView(
        device_id="agv_1", kind="agv",
        capabilities=("agv_transport",),
        resources=("agv_lane_west", "dock_D", "dock_E"),
        zones=("agv",), bound=True)


def _snapshot(operations, devices, *, verified=()) -> SchedulingSnapshot:
    return SchedulingSnapshot(
        sim_time=10.0, operations=tuple(operations),
        devices=tuple(devices),
        materials=(MaterialView(2, "PART_DE", "agv"),),
        free_zones={"agv": False},
        verified_pairs=frozenset(verified),
        vehicle_at_dock={"agv_1": "D", "cart_2": "D"},
        vehicle_dock_settled={"agv_1": True, "cart_2": True})


def test_dual_fixed_dispatch_keeps_transport_de_on_cart2():
    config = FleetConfig(mode="dual")
    assert config.vehicle_for("transport_de") == "cart_2"
    assert ("cart_2", "transport_DE") in config.verified_pairs()

    result = plan_ready(_snapshot(
        [_transport_de()], [_cart2()],
        verified=config.verified_pairs()))
    decision = result.decisions[0]
    assert decision.selected_device_id == "cart_2"
    assert decision.candidates[0].accepted
    assert decision.candidates[0].reasons == ()


def test_cart2_unavailable_baseline_has_no_dispatch_candidate():
    """基线缺口: 固定分工只保留 cart_2, 它不可用则无候选."""
    config = FleetConfig(mode="dual")
    result = plan_ready(_snapshot(
        [_transport_de()], [_cart2(bound=False)],
        verified=config.verified_pairs()))
    decision = result.decisions[0]
    assert decision.selected_device_id == ""
    assert decision.defer_reason == "NO_FEASIBLE_CANDIDATE"
    assert decision.candidates[0].device_id == "cart_2"
    assert decision.candidates[0].accepted is False
    assert decision.candidates[0].reasons == ("DEVICE_NOT_BOUND",)


def test_recipe_full_candidate_set_can_fall_back_to_verified_agv1():
    """把候选恢复为 recipe 全集时, 基线设备 agv_1 可被选择.

    这是阶段 5 目标行为的纯策略可行性, 不代表运行时改派已实现.
    """
    result = plan_ready(_snapshot(
        [_transport_de(candidates=("agv_1", "cart_2"),
                       vehicle_id="agv_1")],
        [_agv1(), _cart2(bound=False)],
        verified=FleetConfig(mode="dual").verified_pairs()))
    decision = result.decisions[0]
    assert decision.selected_device_id == "agv_1"
    assert decision.candidates[0].accepted
    assert decision.candidates[1].device_id == "cart_2"
    assert decision.candidates[1].reasons == ("DEVICE_NOT_BOUND",)


def test_single_fleet_baseline_uses_agv1_for_transport_de():
    config = FleetConfig(mode="single")
    assert config.vehicle_for("transport_de") == "agv_1"
    assert config.verified_pairs() == frozenset()


def test_archived_baseline_fixture_reproduces_evidence():
    """2026-11-20 dual 运行的裁剪事件可重算出冻结基线."""
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    metrics = recompute_order_metrics(
        fixture["events"], order_id=fixture["order_id"],
        due_time=fixture["due_time_s"],
        target_quantity=fixture["target_quantity"],
        window_end=fixture["window_end_s"])
    assert metrics["completion"] == {
        "completed": True, "finals": 1,
        "completion_sim_time_s": 293.7, "completion_s": 293.7,
        "overdue_s": 0.0, "due_met": True,
    }
    assert metrics["tasks"]["total"] == 9
    assert metrics["tasks"]["done"] == 9
    assert metrics["tasks"]["wait_s"] == 177.75
    assert metrics["tasks"]["block_s"] == 0.0
    assert metrics["tasks"]["processing_s"] == 293.75
    assert metrics["queues"]["peak_active"] == 1
    assert metrics["dispatch"]["count"] == 8
    assert metrics["dispatch"]["selected_devices"] == {
        "agv_1": 1, "arm_A": 1, "arm_B": 1, "arm_C": 1,
        "arm_D": 1, "arm_E": 2, "cart_2": 1}
    assert metrics["policy_version"] == "v1_scheduler_20261104"
    assert metrics["window"]["end_s"] == 293.75
    assert metrics["recomputed_from_events"] is True