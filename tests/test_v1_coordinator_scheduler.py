"""阶段 4 WP3: 协调器按调度决策启动设备、全量申请资源并保留等待语义.

用记录型执行适配器驱动, 不依赖 CoppeliaSim:
- ready 工序经纯策略选定 recipe 候选设备后才入队/启动;
- 决策记录含候选、拒绝/等待原因、策略版本与回填的 execution ID;
- 资源冲突时不启动、不持有部分资源、工序不标 FAILED;
- 物料未就绪时保持 ready, 不做一次性丢弃.
"""

from __future__ import annotations

from types import SimpleNamespace

from flexible_factory.application.coordinator import Stage4Coordinator
from flexible_factory.domain.materials import ItemType, MaterialRegistry
from flexible_factory.simulation.execution import (ExecutionReport,
                                                   ExecutionState)
from flexible_factory.simulation.layout import default_layout


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

    def spawn_material(self, uid, alias, position=None) -> int:
        self.next_handle += 1
        return self.next_handle

    def remove_material(self, handle) -> None:
        pass

    def agv_dock_hit_at(self, *args) -> bool:
        return True

    def unit(self, unit_id: str):
        return SimpleNamespace(spec=SimpleNamespace(
            agv_tcp_pose=(0.0, 0.0, 0.3, 0.0, 0.0, 0.0, 1.0),
            agv_material_pose=(0.0, 0.0, 0.25, 0.0, 0.0, 0.0, 1.0)))


class _MaterialRec:
    def __init__(self, uid: int, handle: int) -> None:
        self.uid = uid
        self.alias = f"Part_{uid:04d}"
        self.sim_handle = handle
        self.state = SimpleNamespace(value="RELEASED")
        self.history: list[dict] = []

    def transition_to(self, state, sim_time, reason="") -> None:
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
    arms: dict = {}

    def forget_material(self, handle) -> None:
        pass

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
    kind = "arm"

    def __init__(self, unit_id: str) -> None:
        self.unit_id = unit_id
        self.device_id = f"arm_{unit_id}"
        self.starts: list = []
        self.releases: list[str] = []
        self.report = ExecutionReport(
            execution_id="", device_id=self.device_id, operation_id="",
            state=ExecutionState.SUCCEEDED, finished=True,
            detail={"phase": "DONE"}, evidence={})
        self._controller = SimpleNamespace(
            state=SimpleNamespace(name="WAITING_FOR_ITEM"),
            source="conveyor", sink="station", dock_id="",
            failure_reason="", summary=lambda: {"unit_id": unit_id})

    @property
    def controller(self):
        return self._controller

    def start(self, request) -> str:
        self.starts.append(request)
        return f"{self.device_id}#{len(self.starts)}"

    def pre_tick(self, sim_time: float) -> None:
        pass

    def post_tick(self, sim_time: float) -> None:
        pass

    def poll(self, execution_id: str) -> ExecutionReport:
        return self.report

    def release(self, reason: str = "") -> None:
        self.releases.append(reason)


class _StubAgvExecutor:
    kind = "agv"
    device_id = "agv_1"
    active = False
    route = None
    execution_id = ""


def _coordinator() -> Stage4Coordinator:
    comp = {
        "materials": _Materials(),
        "flow": MaterialRegistry(),
        "agv": _Agv(),
        "runtime": _RuntimeStub(),
        "ik": None,
        "suction": {unit_id: _Suction() for unit_id in "ABCDE"},
        "conveyor": None,
    }
    coordinator = Stage4Coordinator(_Sim(), _Scene(), comp,
                                    scenario="full_flow")
    coordinator.arm_executors = {
        f"arm_{unit_id}": _StubArm(unit_id) for unit_id in "ABCDE"}
    coordinator.agv_executor = _StubAgvExecutor()
    coordinator._verify_unit_task = \
        lambda task, controller, unit_id: (True, "dry-run verified", {})
    coordinator._batch = {"RAW_A": 1}
    return coordinator


# ═══════════════════════════════════════════════════════════════
# 决策 → 启动
# ═══════════════════════════════════════════════════════════════

def test_scheduler_selects_recipe_device_and_records_execution_id():
    coordinator = _coordinator()
    coordinator.start(0.0)
    coordinator.step()

    task = coordinator.queue.get("a_conveyor_uid1")
    assert task is not None
    assert task.operation.selected_device_id == "arm_A"
    assert task.resources == [
        "line_s1a", "handoff_ac", "zone_ab", "zone_ac", "arm_A"]
    assert len(coordinator.arm_executors["arm_A"].starts) == 1
    assert sum(len(stub.starts) for name, stub
               in coordinator.arm_executors.items()
               if name != "arm_A") == 0

    decision = next(row for row in coordinator.schedule_decisions
                    if row["task_id"] == "a_conveyor_uid1")
    assert decision["selected_device_id"] == "arm_A"
    assert decision["policy_version"].startswith("v1_scheduler_")
    assert decision["execution_id"] == "arm_A#1"
    assert decision["candidates"][0]["accepted"]
    assert decision["run_id"]
    assert decision["ready_sim_time"] == 0.0
    event = next(row for row in coordinator.events
                 if row["kind"] == "SCHEDULE_DECISION")
    assert event["detail"]["selected_device_id"] == "arm_A"


def test_missing_material_defers_without_claiming_ready_instance():
    coordinator = _coordinator()
    coordinator.op_runtime.instantiate_entry("s1a", 1, sim_time=0.0)

    coordinator._dispatch_ready(0.0)
    coordinator._dispatch_ready(0.05)

    instance = coordinator.op_runtime.instance(
        "run0001:1:housing_direct_A")
    assert instance is not None
    assert instance.dispatched is False
    assert instance.selected_device_id == ""
    assert not coordinator.queue.all()
    assert not coordinator.schedule_decisions
    # 同一原因只记录一次等待, 但实例保持 PENDING 以便下一 tick 重试.
    assert len(coordinator.schedule_waits) == 1
    assert coordinator.schedule_waits[0]["defer_reason"] == \
        "NO_FEASIBLE_CANDIDATE"
    assert instance.state.value == "PENDING"


# ═══════════════════════════════════════════════════════════════
# 资源全有或全无
# ═══════════════════════════════════════════════════════════════

def test_resource_conflict_does_not_start_or_leave_partial_locks():
    coordinator = _coordinator()
    coordinator._spawn_source("s1a", 0.0)
    coordinator._dispatch_ready(0.0)
    task = coordinator.queue.get("a_conveyor_uid1")
    assert task is not None

    # 模拟同一 tick 内资源在决策后被其他 owner 占用.
    coordinator.resources.acquire("handoff_ac", "external/task", "external",
                                  0.0, "test hold")
    coordinator._poll_unit("A", 0.0)

    stub = coordinator.arm_executors["arm_A"]
    assert not stub.starts
    assert task.status.value == "WAITING"
    assert "资源等待" in task.phase
    assert task.status.value != "FAILED"
    assert coordinator.resources.owner("handoff_ac") == "external/task"
    assert coordinator.resources.owner("line_s1a") is None
    assert coordinator.resources.owner("arm_A") is None
    decision = next(row for row in coordinator.schedule_decisions
                    if row["task_id"] == task.task_id)
    assert decision["resource_result"] == "BUSY"
    assert decision["resource_busy"] == "handoff_ac"
    assert decision["resource_owner"] == "external/task"

    coordinator.resources.release_all("external/task", 0.05, "release")
    coordinator._poll_unit("A", 0.05)
    assert len(stub.starts) == 1
    assert task.status.value == "RUNNING"
    assert coordinator.resources.owner("handoff_ac") == \
        "unit_A/task_a_conveyor_uid1"
    assert decision["resource_result"] == "ACQUIRED"
    assert "arm_A" in decision["acquired_resources"]


# ═══════════════════════════════════════════════════════════════
# 全批次 dry-run: 调度不改变 AGV 调机与 E 合流顺序
# ═══════════════════════════════════════════════════════════════

class _StubRouteController:
    metrics = SimpleNamespace(stop_error=0.001, heading_error=0.0,
                              material_slip=0.0)


class _StubRouteExecutor:
    """一次 poll 即完成航线; 只替换物理运动, 保留协调器路由/资源语义."""

    kind = "agv"
    device_id = "agv_1"

    def __init__(self) -> None:
        self._active = False
        self.spec = None
        self.execution_id = ""
        self.state = SimpleNamespace(value="DRIVING")
        self._route_controller = _StubRouteController()
        self.route = None
        self._serial = 0
        self.releases: list[str] = []

    @property
    def active(self) -> bool:
        return self._active

    @property
    def controller(self):
        return self._route_controller

    def start(self, request) -> str:
        self._serial += 1
        self._active = True
        self.route = self._route_controller
        self.spec = request.route_spec
        self.execution_id = f"agv_1#{self._serial}"
        return self.execution_id

    def pre_tick(self, sim_time: float) -> None:
        pass

    def poll(self, execution_id: str) -> ExecutionReport:
        spec = self.spec
        summary = {
            "route_id": spec.route_id,
            "loaded": bool(spec.loaded),
            "state": "DONE",
            "spec": {"route_id": spec.route_id,
                     "loaded": bool(spec.loaded),
                     "start_dock": spec.start_dock,
                     "end_dock": spec.end_dock},
            "metrics": {"stop_error": 0.001, "heading_error": 0.0,
                        "material_slip": 0.0},
        }
        return ExecutionReport(
            execution_id=execution_id, device_id=self.device_id,
            operation_id="", state=ExecutionState.SUCCEEDED, finished=True,
            detail={"route_state": "DONE"}, evidence={"route": summary})

    def release(self, reason: str = "") -> None:
        self.releases.append(reason)
        self._active = False
        self.route = None
        self.spec = None


def test_full_batch_dry_run_keeps_agv_reposition_and_join():
    coordinator = _coordinator()
    coordinator._batch = {"RAW_A": 1, "RAW_B": 1, "RAW_D": 1}
    coordinator.agv_executor = _StubRouteExecutor()
    coordinator._verify_route = lambda task, route: (True, "dry-run")
    coordinator._agv_at_dock = lambda dock: True
    coordinator.start(0.0)

    steps = 0
    while not coordinator.finished:
        coordinator.step()
        steps += 1
        assert steps < 2000, "全批次 dry-run 未收敛"

    acceptance = coordinator.acceptance()
    assert acceptance["graph_complete"]
    assert acceptance["scheduler_ok"], acceptance["scheduler_detail"]
    assert acceptance["ok"], acceptance
    finals = coordinator.flow.final_records()
    assert len(finals) == 1
    assert list(finals[0].parents) == [1, 3]
    assert acceptance["unpaired_semi_ce"] == 1
    assert acceptance["loaded_routes"] == 2
    # 11 条物理工序都有决策记录与选定设备 (kit_join 是逻辑节点); 资源表最终无 owner.
    assert len(coordinator.schedule_decisions) == 11
    assert all(row["selected_device_id"] and row["execution_id"]
               for row in coordinator.schedule_decisions)
    assert all(owner is None
               for owner in coordinator.resources.owners.values())
