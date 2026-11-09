"""阶段 4: 纯调度策略的候选筛选、排序与确定性 (不需要 CoppeliaSim).

覆盖 06-22 §能力匹配模型与 `docs/implementation/plans/production-flow/v1-stage-04-capability-scheduling-implementation.md` WP1/WP2:
- recipe allowlist、静态能力/绑定、物料/区域前提与未验收几何组合的拒绝原因;
- 业务优先级 → 等待时间 → ready 时间 → 稳定实例 ID 的排序;
- 输入顺序变化不改变决策; 决策只含可 JSON 序列化数据;
- 默认 recipe 的每个工序都能解释当前固定设备为何可行.
"""

from __future__ import annotations

import json

from flexible_factory.domain.default_process import default_catalog
from flexible_factory.domain.scheduling import (DeviceView, MaterialView,
                                                OperationView,
                                                SchedulingSnapshot,
                                                plan_ready)
from flexible_factory.simulation.device_binding import device_bindings

CATALOG = default_catalog()


# ═══════════════════════════════════════════════════════════════
# 快照构造
# ═══════════════════════════════════════════════════════════════

def _arm(device_id: str, *, capabilities=("pick",), resources=(),
         zones=(), bound: bool = True) -> DeviceView:
    return DeviceView(
        device_id=device_id, kind="arm", capabilities=tuple(capabilities),
        resources=tuple(resources), zones=tuple(zones), bound=bound)


def _op(instance_id: str, *, candidates=("arm_A",), capability="pick",
        uid: int = 1, item_type: str = "RAW_A", zone: str = "line_s1a",
        destination: str = "handoff_ac", priority: int = 1,
        ready: float = 0.0, executor_kind: str = "arm",
        resources=(), template_id: str = "op") -> OperationView:
    return OperationView(
        instance_id=instance_id, task_id=f"task_{instance_id}",
        template_id=template_id, kind=template_id, uid=uid,
        executor_kind=executor_kind, capability=capability,
        candidate_device_ids=tuple(candidates), priority=priority,
        ready_sim_time=ready, input_zones=(zone,),
        input_item_types=(item_type,), destination_zone=destination,
        required_resources=tuple(resources), source_kind="conveyor",
        sink_kind="station")


def _snapshot(operations, devices, *, materials=None, free=None,
              owners=None, busy=None, verified=(), agv_at="A",
              settled=True, sim_time=0.0) -> SchedulingSnapshot:
    return SchedulingSnapshot(
        sim_time=sim_time, operations=tuple(operations),
        devices=tuple(devices),
        materials=tuple(materials if materials is not None else [
            MaterialView(1, "RAW_A", "line_s1a")]),
        free_zones=dict(free if free is not None else
                        {"handoff_ac": True}),
        resource_owners=dict(owners or {}),
        busy_devices=dict(busy or {}),
        verified_pairs=frozenset(verified),
        agv_at_dock=agv_at, agv_dock_settled=settled)


def _decision(result, instance_id: str):
    return next(row for row in result.decisions
                if row.operation_instance_id == instance_id)


# ═══════════════════════════════════════════════════════════════
# 候选筛选
# ═══════════════════════════════════════════════════════════════

def test_capability_mismatch_rejects_candidate_and_selects_verified_alt():
    operations = [_op("run:1:op", candidates=("arm_A", "arm_B"))]
    devices = [
        _arm("arm_A", capabilities=("other",)),
        _arm("arm_B", capabilities=("pick",), resources=("arm_B",),
             zones=("line_s1a", "handoff_ac")),
    ]
    # 非基线设备未登记动态几何验收时不得顶替.
    result = plan_ready(_snapshot(operations, devices))
    decision = _decision(result, "run:1:op")
    assert decision.selected_device_id == ""
    assert decision.defer_reason == "NO_FEASIBLE_CANDIDATE"
    reasons = {row.device_id: row.reasons for row in decision.candidates}
    assert any("CAPABILITY_MISMATCH" in reason
               for reason in reasons["arm_A"])
    assert "DYNAMIC_GEOMETRY_NOT_VERIFIED" in reasons["arm_B"]

    result = plan_ready(_snapshot(
        operations, devices, verified={("arm_B", "op")}))
    decision = _decision(result, "run:1:op")
    assert decision.selected_device_id == "arm_B"
    assert not decision.defer_reason


def test_allowlist_and_unbound_device_are_rejected():
    operations = [_op("run:1:op", candidates=("arm_B",))]
    devices = [_arm("arm_B", resources=("arm_B",), bound=False,
                    zones=("line_s1a", "handoff_ac"))]
    decision = _decision(plan_ready(_snapshot(operations, devices)),
                         "run:1:op")
    assert decision.candidates[0].reasons == ("DEVICE_NOT_BOUND",)


def test_unknown_device_and_empty_candidates_defer():
    operation = _op("run:1:op", candidates=("arm_missing",))
    decision = _decision(plan_ready(_snapshot([operation], [])), "run:1:op")
    assert decision.candidates[0].reasons == ("UNKNOWN_DEVICE",)
    assert decision.selected_device_id == ""

    empty = _op("run:2:op", candidates=())
    decision = _decision(plan_ready(_snapshot([empty], [])), "run:2:op")
    assert decision.defer_reason == "NO_FEASIBLE_CANDIDATE"
    assert decision.candidates[0].reasons == ("NO_CANDIDATE_DEVICE",)


def test_material_and_destination_preconditions_reject():
    devices = [_arm("arm_A", resources=("arm_A",),
                    zones=("line_s1a", "handoff_ac"))]
    operation = _op("run:1:op", resources=("arm_A",))

    missing = _decision(plan_ready(_snapshot(
        [operation], devices, materials=[])), "run:1:op")
    assert "MATERIAL_MISSING" in missing.candidates[0].reasons

    wrong_zone = _decision(plan_ready(_snapshot(
        [operation], devices,
        materials=[MaterialView(1, "RAW_A", "line_s1b")])), "run:1:op")
    assert any("ZONE_MISMATCH" in reason
               for reason in wrong_zone.candidates[0].reasons)

    occupied = _decision(plan_ready(_snapshot(
        [operation], devices, free={"handoff_ac": False})), "run:1:op")
    assert "DEST_ZONE_OCCUPIED: handoff_ac" in \
        occupied.candidates[0].reasons


def test_agv_transport_ignores_same_zone_destination_capacity():
    operation = OperationView(
        instance_id="run:1:move", template_id="transport_BA",
        kind="transport", uid=2, executor_kind="agv", capability="agv",
        candidate_device_ids=("agv_1",), input_zones=("agv",),
        input_item_types=("SEMI_AB",), destination_zone="agv",
        source_kind="agv", sink_kind="agv", route_origin_dock="B",
        route_destination_dock="A")
    device = DeviceView(
        device_id="agv_1", kind="agv", capabilities=("agv",),
        resources=("agv_lane_west", "dock_A", "dock_B"), zones=("agv",))
    result = plan_ready(_snapshot(
        [operation], [device], agv_at="B",
        materials=[MaterialView(2, "SEMI_AB", "agv")],
        free={"agv": False}))
    decision = _decision(result, "run:1:move")
    assert decision.selected_device_id == "agv_1"
    assert decision.candidates[0].reasons == ()


# ═══════════════════════════════════════════════════════════════
# 占用偏好与排序确定性
# ═══════════════════════════════════════════════════════════════

def test_idle_alternative_is_preferred_but_busy_device_still_selected():
    operation = _op("run:1:op", candidates=("arm_A", "arm_B"))
    devices = [
        _arm("arm_A", resources=("arm_A",), zones=("line_s1a",
                                                  "handoff_ac")),
        _arm("arm_B", resources=("arm_B",), zones=("line_s1a",
                                                  "handoff_ac")),
    ]
    verified = {("arm_B", "op")}

    result = plan_ready(_snapshot(
        [operation], devices, busy={"arm_A": "execution/arm_A#1"},
        verified=verified))
    decision = _decision(result, "run:1:op")
    assert decision.selected_device_id == "arm_B"

    # 全部忙时不放弃派工: 选择排序第一的候选并记录等待条件.
    result = plan_ready(_snapshot(
        [operation], devices,
        busy={"arm_A": "execution/arm_A#1",
              "arm_B": "execution/arm_B#1"},
        verified=verified))
    decision = _decision(result, "run:1:op")
    assert decision.selected_device_id == "arm_A"
    assert any(reason.startswith("DEVICE_BUSY")
               for reason in decision.candidates[0].conditions)


def test_input_order_does_not_change_decisions():
    devices = [
        _arm("arm_A", resources=("arm_A",),
             zones=("line_s1a", "handoff_ac")),
        _arm("arm_B", resources=("arm_B",),
             zones=("line_s1b", "handoff_ac")),
    ]
    first = _op("run:1:a", candidates=("arm_A",), zone="line_s1a",
                priority=1, ready=0.0)
    second = _op("run:2:b", candidates=("arm_B",), zone="line_s1b",
                 priority=0, ready=5.0)
    third = _op("run:3:c", candidates=("arm_A",), zone="line_s1a",
                priority=1, ready=2.0)
    materials = [MaterialView(1, "RAW_A", "line_s1a"),
                 MaterialView(2, "RAW_A", "line_s1b"),
                 MaterialView(3, "RAW_A", "line_s1a")]

    forward = plan_ready(_snapshot(
        [first, second, third], devices, materials=materials,
        sim_time=10.0))
    backward = plan_ready(_snapshot(
        [third, first, second], devices, materials=materials,
        sim_time=10.0))
    assert forward.as_dict() == backward.as_dict()
    # 优先级 0 先; 同优先级下等待更久的 run:1 先于 run:3.
    assert [row.operation_instance_id for row in forward.decisions] == [
        "run:2:b", "run:1:a", "run:3:c"]


def test_decision_is_json_serializable():
    operation = _op("run:1:op", resources=("arm_A",))
    device = _arm("arm_A", resources=("arm_A",),
                  zones=("line_s1a", "handoff_ac"))
    payload = plan_ready(_snapshot([operation], [device])).as_dict()
    text = json.dumps(payload, sort_keys=True)
    assert "v1_scheduler_" in text
    assert payload["decisions"][0]["selected_device_id"] == "arm_A"
    # 未建模维度显式标注, 不静默当作通过.
    assert payload["decisions"][0]["not_modeled"] == [
        "device_health", "tool_payload", "pose_envelope"]


# ═══════════════════════════════════════════════════════════════
# 默认 recipe: 每个工序的固定设备都可解释
# ═══════════════════════════════════════════════════════════════

def _catalog_operation_view(operation, uid: int) -> OperationView:
    return OperationView(
        instance_id=f"run0001:{uid}:{operation.id}",
        task_id=f"{operation.kind}_uid{uid}", template_id=operation.id,
        kind=operation.kind, uid=uid, executor_kind=operation.executor_kind,
        capability=operation.capability,
        candidate_device_ids=tuple(operation.candidate_devices),
        priority=int(operation.priority),
        input_zones=tuple(port.zone for port in operation.inputs),
        input_item_types=tuple(port.item_type.value
                               for port in operation.inputs),
        destination_zone=(operation.output.zone
                          if operation.output else ""),
        source_kind=operation.source_kind, sink_kind=operation.sink_kind,
        dock=operation.dock,
        route_origin_dock=operation.route_origin_dock,
        route_destination_dock=operation.route_destination_dock,
        required_resources=tuple(operation.resources))


def test_every_default_operation_explains_its_fixed_device():
    devices = []
    for binding in device_bindings():
        spec = CATALOG.device(binding.device_id)
        devices.append(DeviceView(
            device_id=binding.device_id, kind=binding.kind,
            capabilities=tuple(spec.capabilities),
            resources=tuple(binding.resources), zones=tuple(binding.zones),
            default_line_id=binding.default_line_id, bound=True))

    operations = []
    for index, operation in enumerate(CATALOG.recipe(
            "mixed_line_v1").operations, start=1):
        operations.append(_catalog_operation_view(operation, index))
    materials = []
    for index, operation in enumerate(
            CATALOG.recipe("mixed_line_v1").operations, start=1):
        port = operation.inputs[0]
        materials.append(MaterialView(index, port.item_type.value,
                                      port.zone))
    snapshot = SchedulingSnapshot(
        sim_time=0.0, operations=tuple(operations),
        devices=tuple(devices), materials=tuple(materials),
        free_zones={zone: True for zone in (
            "handoff_ac", "line_ce", "buffer_e_c", "buffer_e_d", "agv",
            "output_e")},
        agv_at_dock="B", agv_dock_settled=True)

    result = plan_ready(snapshot)
    assert len(result.decisions) == len(operations)
    for decision in result.decisions:
        assert decision.selected_device_id == \
            decision.candidates[0].device_id, decision.operation_instance_id
        assert decision.candidates[0].accepted, \
            (decision.operation_instance_id, decision.candidates[0].reasons)
        assert decision.policy_version.startswith("v1_scheduler_")
