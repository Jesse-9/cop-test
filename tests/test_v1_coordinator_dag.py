"""阶段 3: 协调器由配方运行图驱动派工/提交/合流 (不需要 CoppeliaSim).

用真实 :class:`MaterialRegistry` (纯 Python 谱系) + 记录型执行适配器验证:
入口固定选路建图、ready 实例逐 tick 入队、成功边界提交后才解锁后继、
E 合流只提交一次. 位置/物理验证属于实机 focused 用例, 这里替换为固定通过,
测试目标是不依赖几何的编排语义.
"""

from __future__ import annotations

from types import SimpleNamespace

from flexible_factory import spec
from flexible_factory.application.coordinator import Stage4Coordinator
from flexible_factory.domain.default_process import default_catalog
from flexible_factory.domain.materials import (ItemType, MaterialRegistry,
                                               RecordState)
from flexible_factory.simulation import config as cfg
from flexible_factory.simulation.execution import (ExecutionReport,
                                                   ExecutionState)
from flexible_factory.simulation.layout import default_layout
from flexible_factory.simulation.material_manager import MaterialState

CATALOG = default_catalog()


# ═══════════════════════════════════════════════════════════════
# 假场景/物料/适配器
# ═══════════════════════════════════════════════════════════════

class _Sim:
    colorcomponent_ambient_diffuse = 1

    def __init__(self) -> None:
        self.time = 0.0

    def getSimulationTime(self) -> float:
        return self.time

    def step(self) -> None:
        self.time += 0.5

    def setObjectColor(self, *args, **kwargs) -> None:
        pass


class _Scene:
    stage4_layout = default_layout()

    def __init__(self) -> None:
        self.next_handle = 700
        self.removed: list[int] = []

    def spawn_material(self, uid, alias, position=None) -> int:
        self.next_handle += 1
        return self.next_handle

    def remove_material(self, handle) -> None:
        self.removed.append(int(handle))

    def agv_dock_hit_at(self, *args) -> bool:
        return True

    def unit(self, unit_id: str):
        return SimpleNamespace(spec=SimpleNamespace(
            agv_tcp_pose=(0.0, 0.0, 0.3, 0.0, 0.0, 0.0, 1.0),
            agv_material_pose=(0.0, 0.0, 0.25, 0.0, 0.0, 0.0, 1.0)))


class _MaterialRec:
    """MaterialManager 记录的最小替身: 句柄 + 物理状态."""

    def __init__(self, uid: int, handle: int) -> None:
        self.uid = uid
        self.alias = f"Part_{uid:04d}"
        self.sim_handle: int | None = handle
        self.state = MaterialState.RELEASED
        self.history: list[dict] = []

    def transition_to(self, state, sim_time, reason="") -> None:
        self.history.append({"state": state, "sim_time": sim_time,
                             "reason": reason})
        self.state = state


class _Materials:
    def __init__(self) -> None:
        self.records: dict[int, _MaterialRec] = {}

    def register(self, uid, alias, sim_handle=None) -> None:
        self.records[int(uid)] = _MaterialRec(int(uid), int(sim_handle))

    def by_uid(self, uid):
        return self.records.get(int(uid)) if uid is not None else None

    def detach_sim(self, uid) -> None:
        rec = self.records.get(int(uid))
        if rec is not None:
            rec.sim_handle = None

    def remove(self, uid) -> None:
        self.records.pop(int(uid), None)


class _RuntimeStub:
    def __init__(self) -> None:
        self.forgotten: list[int] = []
        self.arms: dict = {}

    def forget_material(self, handle) -> None:
        self.forgotten.append(int(handle))

    def remove_hazards(self) -> None:
        pass


class _Suction:
    def forget(self, uid) -> None:
        pass


class _Agv:
    active = False

    @property
    def state(self):
        return SimpleNamespace(name="STOPPED")

    def is_stopped(self) -> bool:
        return True

    def is_upright(self) -> bool:
        return True

    def pose(self):
        return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0)

    def stop(self) -> None:
        pass


class _StubArm:
    """立即 SUCCEEDED 的假适配器; 只记录启动次数."""

    kind = "arm"

    def __init__(self, unit_id: str) -> None:
        self.unit_id = unit_id
        self.device_id = f"arm_{unit_id}"
        self.starts = 0
        self.releases: list[str] = []
        self._controller = SimpleNamespace(
            state=SimpleNamespace(name="PLACING"),
            source="conveyor", sink="station", dock_id="",
            summary=lambda: {"unit_id": unit_id})

    @property
    def controller(self):
        return self._controller

    def start(self, request) -> str:
        self.starts += 1
        return f"{self.device_id}#{self.starts}"

    def pre_tick(self, sim_time: float) -> None:
        pass

    def post_tick(self, sim_time: float) -> None:
        pass

    def poll(self, execution_id: str) -> ExecutionReport:
        return ExecutionReport(
            execution_id=execution_id, device_id=self.device_id,
            operation_id="", state=ExecutionState.SUCCEEDED, finished=True,
            failure_reason="", detail={"phase": "DONE"}, evidence={})

    def release(self, reason: str = "") -> None:
        self.releases.append(reason)


class _StubAgvExecutor:
    kind = "agv"
    device_id = "agv_1"
    active = False
    route = None


def _coordinator(scenario: str = "full_flow",
                 batch: dict[str, int] | None = None) -> Stage4Coordinator:
    sim = _Sim()
    scene = _Scene()
    materials = _Materials()
    comp = {
        "materials": materials,
        "flow": MaterialRegistry(),
        "agv": _Agv(),
        "runtime": _RuntimeStub(),
        "ik": None,
        "suction": {unit_id: _Suction() for unit_id in "ABCDE"},
        "conveyor": None,
    }
    coordinator = Stage4Coordinator(sim, scene, comp, scenario=scenario,
                                   batch=batch)
    coordinator.arm_executors = {
        f"arm_{unit_id}": _StubArm(unit_id) for unit_id in "ABCDE"}
    coordinator.agv_executor = _StubAgvExecutor()
    # 位置/物理/类型验证需要真实仿真; 本文件只验证编排语义.
    coordinator._verify_unit_task = \
        lambda task, controller, unit_id: (True, "dry-run verified", {})
    return coordinator


# ═══════════════════════════════════════════════════════════════
# 有限批次输入
# ═══════════════════════════════════════════════════════════════

def test_default_coordinator_uses_scenario_batch():
    coordinator = _coordinator()
    assert coordinator._batch == cfg.SCENARIO_BATCH[cfg.SCENARIO_FULL_FLOW]


def test_configured_batch_spawns_declared_entries_with_fixed_routes():
    coordinator = _coordinator(batch={"RAW_A": 2, "RAW_B": 0, "RAW_D": 1})
    coordinator.start(0.0)
    coordinator._poll_sources(0.0)

    assert [(row["source"], row["item_type"], row["uid"])
            for row in coordinator.spawned] == [
        ("s1a", "RAW_A", 1), ("s2d", "RAW_D", 2)]
    graphs = [row for row in coordinator.events
              if row["kind"] == "OPERATION_GRAPH"]
    assert [(row["uid"], row["detail"]["route"]) for row in graphs] == [
        (1, "housing_direct"), (2, "bracket_route")]
    assert coordinator._batch == {"RAW_A": 2, "RAW_B": 0, "RAW_D": 1}


def test_mixed_batch_instantiates_one_fixed_route_per_entry():
    coordinator = _coordinator(batch={"RAW_A": 2, "RAW_B": 1, "RAW_D": 2})
    coordinator.start(0.0)
    coordinator._poll_sources(0.0)

    graphs = {row["uid"]: row["detail"]["route"]
              for row in coordinator.events
              if row["kind"] == "OPERATION_GRAPH"}
    assert graphs == {
        1: "housing_direct", 2: "housing_alt", 3: "bracket_route"}
    assert [row["item_type"] for row in coordinator.spawned] == [
        "RAW_A", "RAW_B", "RAW_D"]


def test_summary_and_acceptance_report_effective_batch():
    coordinator = _coordinator(batch={"RAW_A": 2, "RAW_B": 0, "RAW_D": 2})
    coordinator.start(0.0)
    summary = coordinator.summary()

    assert summary["batch"] == {"RAW_A": 2, "RAW_B": 0, "RAW_D": 2}
    assert summary["batch_input_version"] == spec.BATCH_INPUT_VERSION
    assert summary["acceptance"]["batch"] == summary["batch"]
    assert summary["acceptance"]["batch_expectation"]["final"] == 2
    start = next(row for row in coordinator.events
                 if row["kind"] == "COORDINATOR_START")
    assert start["detail"]["batch"] == summary["batch"]


# ═══════════════════════════════════════════════════════════════
# 单 UID 直达支路: S1→A→C→E
# ═══════════════════════════════════════════════════════════════

def test_direct_route_is_driven_by_ready_set_end_to_end():
    coordinator = _coordinator(batch={"RAW_A": 1})
    coordinator.start(0.0)

    steps = 0
    while not coordinator.finished:
        coordinator.step()
        steps += 1
        assert steps < 200, "运行图未能收敛"

    assert [task.kind for task in coordinator.queue.all()] == [
        "a_conveyor", "c_handoff", "e_conveyor"]
    assert coordinator.queue.all_done()
    assert coordinator.op_runtime.all_done()
    record = coordinator.flow.by_uid(1)
    assert record is not None
    assert record.item_type is ItemType.SEMI_CE
    assert record.zone == "buffer_e_c"
    assert record.state is RecordState.READY
    # 每条工序只启动一次, 且后继在前驱提交后才入队.
    assert {unit: stub.starts for unit, stub in
            coordinator.arm_executors.items()} == {
        "arm_A": 1, "arm_B": 0, "arm_C": 1, "arm_D": 0, "arm_E": 1}
    created = [row for row in coordinator.events if row["kind"] == "TASK_DONE"]
    assert [row["task_id"] for row in created] == [
        "a_conveyor_uid1", "c_handoff_uid1", "e_conveyor_uid1"]
    assert [row["kind"] for row in coordinator.events
            if row["kind"] == "OPERATION_GRAPH"] == ["OPERATION_GRAPH"]
    # 阶段 4: 每个派工决策都选定 recipe 候选并回填 execution ID;
    # 完成后资源表无残留 owner.
    assert [row["selected_device_id"]
            for row in coordinator.schedule_decisions] == [
        "arm_A", "arm_C", "arm_E"]
    assert all(row["execution_id"] for row in coordinator.schedule_decisions)
    assert all(owner is None
               for owner in coordinator.resources.owners.values())


def test_no_task_is_queued_for_unselected_route():
    coordinator = _coordinator(batch={"RAW_A": 1})
    coordinator.start(0.0)
    for _ in range(4):
        coordinator.step()

    queued = {task.operation_template_id for task in coordinator.queue.all()}
    assert queued == {"housing_direct_A", "housing_finish",
                      "housing_to_E_buffer"}
    assert "housing_alt_B" not in queued
    assert "transport_BA" not in queued


def test_verification_failure_freezes_without_unlocking_successor():
    coordinator = _coordinator(batch={"RAW_A": 1})
    coordinator._verify_unit_task = \
        lambda task, controller, unit_id: (False, "PLACE_ERROR: 0.5", {})
    coordinator.start(0.0)

    steps = 0
    while not coordinator.finished:
        coordinator.step()
        steps += 1
        assert steps < 200

    assert coordinator.frozen and coordinator.aborted
    entry = coordinator.op_runtime.graph.get("run0001:1:housing_direct_A")
    assert entry.state.value == "FAILED"
    successor = coordinator.op_runtime.graph.get("run0001:1:housing_finish")
    assert successor.state.value == "PENDING"
    assert successor.dispatched is False
    assert "c_handoff_uid1" not in coordinator.queue.tasks


# ═══════════════════════════════════════════════════════════════
# E 合流
# ═══════════════════════════════════════════════════════════════

def _complete_route(coordinator: Stage4Coordinator, entry_id: str,
                    uid: int) -> None:
    instances = coordinator.op_runtime.instantiate_entry(
        entry_id, uid, sim_time=0.0)
    for index, instance in enumerate(instances):
        coordinator.op_runtime.mark_started(instance.instance_id,
                                            index * 1.0)
        coordinator.op_runtime.commit_success(
            instance.instance_id, output_uids=[uid],
            sim_time=index * 1.0 + 0.5)


def _register_buffer_materials(coordinator: Stage4Coordinator) -> None:
    scene = coordinator.scene
    materials = coordinator.materials
    flow = coordinator.flow
    for uid, item_type, zone in ((1, ItemType.SEMI_CE, "buffer_e_c"),
                                 (3, ItemType.PART_DE, "buffer_e_d")):
        handle = scene.spawn_material(uid, f"Part_{uid:04d}")
        materials.register(uid, f"Part_{uid:04d}", sim_handle=handle)
        flow.register(uid, item_type, f"Part_{uid:04d}", sim_handle=handle,
                      source="E", zone=zone, sim_time=0.0)


def test_join_commits_once_from_completed_instances():
    coordinator = _coordinator()
    _complete_route(coordinator, "s1a", 1)
    _complete_route(coordinator, "s2d", 3)
    _register_buffer_materials(coordinator)
    coordinator._next_uid = 4          # 上游已用 UID 1/2/3

    coordinator._check_join(10.0)

    finals = coordinator.flow.final_records()
    assert len(finals) == 1 and coordinator.final_count == 1
    assert list(finals[0].parents) == [1, 3]
    join = coordinator.op_runtime.graph.get("run0001:4:kit_join")
    assert join is not None and join.state.value == "DONE"
    assert join.output_uids == [4]
    assert join.predecessor_instance_ids == [
        "run0001:1:housing_to_E_buffer", "run0002:3:bracket_to_E"]
    assert coordinator.op_runtime.all_done()

    # 同一对父件已消费: 再次检查不得生成第二个 FINAL 或第二个节点.
    coordinator._check_join(11.0)
    assert len(coordinator.flow.final_records()) == 1
    assert coordinator.final_count == 1
    assert len(coordinator.op_runtime.graph.by_template("kit_join")) == 1
    assert [row["kind"] for row in coordinator.events
            if row["kind"] == "FINAL_CREATED"] == ["FINAL_CREATED"]


def test_join_waits_until_both_parent_operations_are_committed():
    coordinator = _coordinator()
    _complete_route(coordinator, "s1a", 1)
    _register_buffer_materials(coordinator)

    coordinator._check_join(10.0)

    # 支架工序尚未实例化/提交: 不建节点, 不生成虚假 FINAL.
    assert not coordinator.op_runtime.graph.by_template("kit_join")
    assert coordinator.flow.final_records() == []
    assert coordinator.final_count == 0
