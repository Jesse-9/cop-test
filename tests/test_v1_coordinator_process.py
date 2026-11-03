"""阶段四协调器读取默认工艺目录的纯映射测试 (不需要 CoppeliaSim)."""

from __future__ import annotations

from types import SimpleNamespace

from flexible_factory.application.coordinator import Stage4Coordinator
from flexible_factory.domain.default_process import default_catalog
from flexible_factory.simulation.layout import default_layout
from flexible_factory.domain.tasks import make_task, make_task_from_operation


class _Scene:
    stage4_layout = default_layout()

    def unit(self, unit_id: str):
        return SimpleNamespace(spec=SimpleNamespace(
            agv_tcp_pose=(0.0, 0.0, 0.3, 0.0, 0.0, 0.0, 1.0),
            agv_material_pose=(0.0, 0.0, 0.25, 0.0, 0.0, 0.0, 1.0)))


def _coordinator() -> Stage4Coordinator:
    comp = {key: None for key in (
        "materials", "flow", "agv", "runtime", "ik", "suction",
        "conveyor")}
    return Stage4Coordinator(None, _Scene(), comp)


def test_coordinator_reads_operation_fields_from_catalog():
    coordinator = _coordinator()
    catalog = default_catalog()
    direct = make_task_from_operation(
        catalog.operation("housing_direct_A"), 1)
    assert coordinator._source_zone(direct) == "line_s1a"
    assert coordinator._destination_zone(direct) == "handoff_ac"
    assert coordinator._owner(direct) == f"unit_A/task_{direct.task_id}"

    transport = make_task_from_operation(
        catalog.operation("transport_BA"), 2)
    assert coordinator._owner(transport) == \
        f"agv/task_{transport.task_id}"

    reposition = make_task("reposition", 2, source="A", destination="B",
                           dock="B", device="agv")
    assert coordinator._owner(reposition) == \
        f"agv/task_{reposition.task_id}"


def test_sink_spec_is_derived_from_operation_output_zone():
    coordinator = _coordinator()
    catalog = default_catalog()
    handoff = coordinator._sink_spec(
        catalog.operation("housing_direct_A"), "A")
    assert handoff.kind == "station" and handoff.zone == "handoff_ac"

    conveyor = coordinator._sink_spec(
        catalog.operation("housing_finish"), "C")
    assert conveyor.kind == "conveyor" and conveyor.zone == "line_ce"

    tray = coordinator._sink_spec(
        catalog.operation("housing_alt_B"), "B")
    assert tray.kind == "agv" and tray.zone == "agv"

    buffer_d = coordinator._sink_spec(
        catalog.operation("bracket_to_E"), "E")
    assert buffer_d.kind == "station" and buffer_d.zone == "buffer_e_d"


def test_logic_join_successor_does_not_create_stage4_task():
    """E join 由 _check_join 触发; 不能把 kit_join 当成可派工任务."""
    coordinator = _coordinator()
    assert not coordinator.queue.all()
    coordinator._submit_followup("kit_join", 1, 0.0)
    assert not coordinator.queue.all()
