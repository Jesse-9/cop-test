"""阶段四协调器读取默认工艺目录的纯映射测试 (不需要 CoppeliaSim)."""

from __future__ import annotations

from types import SimpleNamespace

from flexible_factory.application.coordinator import Stage4Coordinator
from flexible_factory.domain.default_process import default_catalog
from flexible_factory.domain.materials import ItemType, MaterialRegistry
from flexible_factory.domain.operation_runtime import OperationInstance
from flexible_factory.simulation.layout import default_layout
from flexible_factory.domain.tasks import make_task, make_task_from_operation


class _Scene:
    stage4_layout = default_layout()

    def unit(self, unit_id: str):
        return SimpleNamespace(spec=SimpleNamespace(
            agv_tcp_pose=(0.0, 0.0, 0.3, 0.0, 0.0, 0.0, 1.0),
            agv_material_pose=(0.0, 0.0, 0.25, 0.0, 0.0, 0.0, 1.0)))


def _register_source(coordinator: Stage4Coordinator, uid: int) -> None:
    coordinator.flow.register(
        uid, ItemType.RAW_A, f"Part_{uid:04d}", source="line_s1a",
        zone="line_s1a", sim_time=0.0)


def _coordinator() -> Stage4Coordinator:
    comp = {key: None for key in (
        "materials", "flow", "agv", "runtime", "ik", "suction",
        "conveyor")}
    comp["flow"] = MaterialRegistry()
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


def test_ready_dispatch_submits_only_the_entry_operation():
    """阶段 3/4: ready 集合只派发已解锁实例; 逻辑合流不进入队列."""
    coordinator = _coordinator()
    _register_source(coordinator, 1)
    instances = coordinator.op_runtime.instantiate_entry("s1a", 1, sim_time=0.0)

    coordinator._dispatch_ready(0.0)

    assert [task.operation_template_id for task in coordinator.queue.all()] == [
        "housing_direct_A"]
    assert coordinator.queue.all()[0].task_id == instances[0].task_id
    assert coordinator.queue.all()[0].operation is instances[0]
    assert instances[0].selected_device_id == "arm_A"
    assert coordinator.schedule_decisions[0]["selected_device_id"] == "arm_A"


def test_dispatch_ready_is_idempotent_while_instance_is_active():
    """同一实例只提交一次; 依赖未满足的后继不提前入队."""
    coordinator = _coordinator()
    _register_source(coordinator, 1)
    coordinator.op_runtime.instantiate_entry("s1a", 1, sim_time=0.0)
    coordinator._dispatch_ready(0.0)
    coordinator._dispatch_ready(0.05)
    coordinator._dispatch_ready(0.10)
    assert len(coordinator.queue.all()) == 1
    assert len(coordinator.schedule_decisions) == 1
    assert not coordinator.queue.rejections


def test_dispatch_ready_skips_logic_join_instances():
    """合流节点即使 ready 也不生成 Stage4Task (由 _check_join 提交账本)."""
    coordinator = _coordinator()
    join_operation = coordinator.catalog.operation("kit_join")
    coordinator.op_runtime.graph.add(OperationInstance(
        instance_id="run0001:9:kit_join", task_id="logical_join_uid9",
        template_id=join_operation.id, kind=join_operation.kind, uid=9,
        source="", destination="output_e", run_id="run0001",
        priority=0))

    coordinator._dispatch_ready(0.0)

    assert not coordinator.queue.all()
