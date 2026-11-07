"""v1 UFLOW 指标重算与契约摘要测试."""

from __future__ import annotations

import json

from flexible_factory.simulation.conveyor import BeltGeometry, transport_step
from flexible_factory.simulation.motion.conveyor_safety import bounded_transport_step
from flexible_factory.simulation import config as cfg
from flexible_factory.domain.metrics import recompute_metrics
from flexible_factory.simulation.contract import (
    build_contract_extra as build_stage4_extra,
    config_digest as stage4_config_digest,
    layout_digest as stage4_layout_digest)


def _event(kind, sim_time, **kwargs):
    row = {"kind": kind, "sim_time": sim_time, "task_id": "",
           "uid": None, "item_type": "", "zone": "", "source": "",
           "destination": "", "device": "", "evidence_id": "",
           "detail": {}}
    row.update(kwargs)
    return row


def test_metrics_recompute_from_events():
    events = [
        _event("TASK_CREATED", 0.0, task_id="a_conveyor_uid1", uid=1,
               device="arm_A", detail={"task_kind": "a_conveyor"}),
        _event("TASK_WAIT", 0.5, task_id="a_conveyor_uid1", uid=1,
               detail={"reason": "handoff busy"}),
        _event("TASK_START", 1.0, task_id="a_conveyor_uid1", uid=1,
               device="arm_A", detail={"task_kind": "a_conveyor"}),
        _event("TASK_DONE", 6.0, task_id="a_conveyor_uid1", uid=1,
               device="arm_A", detail={"task_kind": "a_conveyor"}),
        _event("MATERIAL_SPAWN", 0.0, uid=1, item_type="RAW_A"),
        _event("ROUTE_START", 1.0, evidence_id="B_to_A",
               detail={"route_id": "B_to_A", "loaded": True}),
        _event("ROUTE_DONE", 5.0, evidence_id="B_to_A",
               detail={"route_id": "B_to_A", "loaded": True}),
        _event("JOIN_COMMIT", 9.0, uid=9, item_type="FINAL"),
        _event("JOIN_COMMIT", 19.0, uid=10, item_type="FINAL"),
    ]
    metrics = recompute_metrics(events, wip={"total": 1})
    assert metrics["recomputed_from_events"] is True
    assert metrics["throughput"]["finals"] == 2
    assert metrics["throughput"]["output_intervals_s"] == [10.0]
    task = metrics["tasks"]["by_kind"]["a_conveyor"]
    assert task["count"] == 1 and task["done"] == 1
    assert task["wait_s"] == 0.5
    assert task["processing_s"] == [5.0]
    assert task["cycle_s"] == [6.0]
    assert metrics["agv"]["loaded_routes"] == 1
    assert metrics["agv"]["loaded_time_s"] == 4.0
    assert metrics["devices"]["arm_A"]["busy_s"] == 5.0
    assert metrics["queues"]["peak_active"] == 1
    # 重算结果可 JSON 序列化 (报告直接写入)
    json.dumps(metrics, ensure_ascii=False)


def test_metrics_dedupe_duplicate_join_commit():
    events = [
        _event("MATERIAL_SPAWN", 0.0, uid=1),
        _event("JOIN_COMMIT", 5.0, uid=9, evidence_id="1+3"),
        _event("JOIN_COMMIT", 5.0, uid=9, evidence_id="1+3"),
    ]
    metrics = recompute_metrics(events)
    assert metrics["throughput"]["finals"] == 1


def test_metrics_ignores_join_event_task_id():
    """JOIN_COMMIT 携带 task_id 供追溯, 但不得生成任务窗口."""
    events = [
        _event("TASK_CREATED", 0.0, task_id="a_conveyor_uid1", uid=1,
               detail={"task_kind": "a_conveyor"}),
        _event("TASK_START", 1.0, task_id="a_conveyor_uid1", uid=1),
        _event("TASK_DONE", 5.0, task_id="a_conveyor_uid1", uid=1),
        _event("JOIN_COMMIT", 5.0, task_id="join_e", uid=4,
               item_type="FINAL", evidence_id="1+3",
               detail={"pair_key": "1+3"}),
    ]
    metrics = recompute_metrics(events)
    assert metrics["tasks"]["total"] == 1
    assert "unknown" not in metrics["tasks"]["by_kind"]
    assert metrics["tasks"]["by_kind"]["a_conveyor"]["done"] == 1


def test_metrics_block_time_and_failed_task():
    events = [
        _event("TASK_CREATED", 0.0, task_id="t", uid=1,
               detail={"task_kind": "transport_de"}),
        _event("TASK_START", 1.0, task_id="t", uid=1, device="agv"),
        _event("TASK_BLOCK", 2.0, task_id="t", uid=1),
        _event("TASK_UNBLOCK", 5.0, task_id="t", uid=1),
        _event("TASK_FAILED", 8.0, task_id="t", uid=1, device="agv"),
    ]
    metrics = recompute_metrics(events)
    task = metrics["tasks"]["by_kind"]["transport_de"]
    assert task["failed"] == 1
    assert task["block_s"] == 3.0
    assert task["processing_s"] == [7.0]


def test_contract_extra_is_stable_and_complete():
    first = build_stage4_extra()
    second = build_stage4_extra()
    assert json.dumps(first, sort_keys=True) == \
        json.dumps(second, sort_keys=True)
    assert first["contract_version"] == cfg.STAGE4_CONTRACT_VERSION
    assert first["controller"]["id"] == cfg.STAGE4_CONTROLLER_ID
    assert first["config"]["digest"] == stage4_config_digest()
    assert first["layout"]["digest"] == stage4_layout_digest()
    edge_ids = {edge["id"] for edge in first["topology"]["edges"]}
    assert edge_ids == {"S1_to_A", "S1_to_B", "B_to_A", "A_to_C",
                        "C_to_E", "S2_to_D", "D_to_E"}
    assert first["material_model"]["types"] == list(cfg.ITEM_TYPES)
    assert first["material_model"]["join"]["inputs"] == list(
        cfg.JOIN_INPUTS)
    assert first["resources"]["order"] == list(cfg.RESOURCE_ORDER)
    assert first["task_flow"]["kinds"] == list(cfg.TASK_KINDS)
    assert first["task_flow"]["process_model_version"] == \
        "v1_process_20261106a"
    assert first["task_flow"]["recipe_id"] == "mixed_line_v1"
    assert any("housing_direct" in line
               for line in first["task_flow"]["process_routes"])
    assert set(first["acceptance"]["scenarios"]) == set(cfg.STAGE4_SCENARIOS)
    assert first["agv_route"]["loaded_routes"] == ["B_to_A", "D_to_E"]


def test_digests_change_with_layout_payload():
    from flexible_factory.simulation.layout import default_layout
    baseline = stage4_layout_digest(default_layout())
    payload = default_layout().as_dict()
    payload["agv_initial_yaw"] = -1.5
    from flexible_factory.simulation.layout import layout_from_dict
    changed = stage4_layout_digest(layout_from_dict(payload))
    assert changed != baseline


def test_y_oriented_belt_transport_advances_y():
    geometry = BeltGeometry(entry=(0.0, 0.0, 0.45),
                            stop=(0.0, 1.0, 0.45))
    step = transport_step((0.0, 0.2, 0.50), geometry, 0.2, 0.05)
    assert step.delta > 0.0
    assert abs(step.new_position[1] - (0.2 + step.delta)) < 1e-9
    assert abs(step.new_position[0]) < 1e-9
    bounded = bounded_transport_step(
        (0.0, 0.2, 0.50), (0.0, 0.2, 0.50), geometry, 0.2, 0.05)
    assert bounded.delta > 0.0
    assert bounded.new_position[1] > 0.2


def test_x_oriented_belt_transport_unchanged():
    geometry = BeltGeometry(entry=(-1.0, 0.0, 0.45),
                            stop=(0.0, 0.0, 0.45))
    step = transport_step((-0.5, 0.02, 0.50), geometry, 0.2, 0.05)
    assert abs(step.new_position[0] - (-0.5 + step.delta)) < 1e-9
    assert abs(step.new_position[1] - 0.02) < 1e-9
