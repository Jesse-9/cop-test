"""阶段 2 步骤 6: 协调器经执行适配器启动/轮询 (不需要 CoppeliaSim).

用记录型假适配器验证委托顺序与记账边界:
每个任务只 `start` 一次、每 tick 只 `poll` 一次、失败只在协调器落账,
适配器不直接改任务状态。
"""

from __future__ import annotations

from types import SimpleNamespace

from flexible_factory.application.coordinator import (ControllerRecord,
                                                     Stage4Coordinator)
from flexible_factory.domain.default_process import default_catalog
from flexible_factory.domain.tasks import make_task_from_operation
from flexible_factory.simulation.execution import (ExecutionReport,
                                                   ExecutionState)
from flexible_factory.simulation.layout import default_layout
from flexible_factory.simulation.unit_controller_base import UnitTaskState

CATALOG = default_catalog()
OPERATION = CATALOG.operation("housing_direct_A")


class _Scene:
    stage4_layout = default_layout()

    def unit(self, unit_id: str):
        return SimpleNamespace(spec=SimpleNamespace(
            agv_tcp_pose=(0.0, 0.0, 0.3, 0.0, 0.0, 0.0, 1.0),
            agv_material_pose=(0.0, 0.0, 0.25, 0.0, 0.0, 0.0, 1.0)))


class _Flow:
    """空谱系: 只让 by_uid / pair_candidates 可调用."""

    def by_uid(self, uid):
        return None

    def pair_candidates(self):
        return None, None


class _Materials:
    def by_uid(self, uid):
        return None


class _StubArmExecutor:
    """记录 start/poll/pre/post/release 的假适配器."""

    kind = "arm"
    device_id = "arm_A"

    def __init__(self, report: ExecutionReport = None) -> None:
        self.report = report
        self.starts: list = []
        self.polls = 0
        self.pre_ticks = 0
        self.post_ticks = 0
        self.releases: list[str] = []
        self._controller = SimpleNamespace(
            state=UnitTaskState.WAITING_FOR_ITEM, finished=False,
            source="conveyor", sink="station", dock_id="")

    @property
    def controller(self):
        return self._controller

    def start(self, request) -> str:
        self.starts.append(request)
        return "arm_A#1"

    def pre_tick(self, sim_time: float) -> None:
        self.pre_ticks += 1

    def post_tick(self, sim_time: float) -> None:
        self.post_ticks += 1

    def poll(self, execution_id: str) -> ExecutionReport:
        self.polls += 1
        assert execution_id == "arm_A#1"
        return self.report

    def release(self, reason: str = "") -> None:
        self.releases.append(reason)


class _StubAgvExecutor:
    kind = "agv"
    device_id = "agv_1"

    def __init__(self) -> None:
        self.active = True
        self.pre_ticks = 0
        self.state = SimpleNamespace(value="DRIVING")

    def pre_tick(self, sim_time: float) -> None:
        self.pre_ticks += 1


def _coordinator() -> Stage4Coordinator:
    comp = {key: None for key in (
        "materials", "flow", "agv", "runtime", "ik", "suction",
        "conveyor")}
    comp["flow"] = _Flow()
    comp["materials"] = _Materials()
    return Stage4Coordinator(None, _Scene(), comp)


def _running_task(coordinator: Stage4Coordinator, uid: int = 1):
    task = make_task_from_operation(OPERATION, uid)
    coordinator.queue.submit(task, 0.0)
    coordinator.queue.start(task.task_id, 0.0)
    return task


def _report(state: ExecutionState, **detail) -> ExecutionReport:
    return ExecutionReport(
        execution_id="arm_A#1", device_id="arm_A",
        operation_id=OPERATION.id, state=state,
        finished=state.terminal,
        failure_reason=detail.pop("failure_reason", ""),
        detail=detail, evidence={"controller": {"unit_id": "A"}})


# ═══════════════════════════════════════════════════════════════
# 适配器装配
# ═══════════════════════════════════════════════════════════════

def test_coordinator_builds_one_executor_per_arm_device():
    coordinator = _coordinator()
    assert sorted(coordinator.arm_executors) == [
        "arm_A", "arm_B", "arm_C", "arm_D", "arm_E"]
    assert coordinator.agv_executor.device_id == "agv_1"


def test_start_unit_task_delegates_build_to_executor():
    coordinator = _coordinator()
    stub = _StubArmExecutor()
    coordinator.arm_executors["arm_A"] = stub
    task = make_task_from_operation(OPERATION, 7)
    coordinator.queue.submit(task, 1.0)

    coordinator._start_unit_task(task, "A", 1.25)

    assert len(stub.starts) == 1
    request = stub.starts[0]
    assert request.device_id == "arm_A"
    assert request.operation is OPERATION
    assert request.uid == 7
    assert request.task_id == task.task_id
    assert request.sim_time == 1.25
    record = coordinator.controllers["A"]
    assert record.execution_id == "arm_A#1"
    assert task.phase == "WAITING_FOR_ITEM"
    assert coordinator.queue.get(task.task_id).status.value == "RUNNING"
    assert [row["kind"] for row in coordinator.events].count(
        "TASK_START") == 1


def test_step_calls_pre_and_post_tick_once_per_record():
    coordinator = _coordinator()
    stub = _StubArmExecutor(_report(ExecutionState.RUNNING, phase="PLACING"))
    coordinator.arm_executors["arm_A"] = stub
    task = _running_task(coordinator)
    coordinator.controllers["A"] = ControllerRecord(
        unit_id="A", task_id=task.task_id, executor=stub,
        execution_id="arm_A#1")

    # 抑制来源生成, 只验证适配器调用次数 (sim 用替身, 不推进物理).
    coordinator._batch = {}
    coordinator.sim = SimpleNamespace(step=lambda: None,
                                      getSimulationTime=lambda: 0.0)
    coordinator.step()

    assert stub.pre_ticks == 1
    assert stub.post_ticks == 1
    assert stub.polls == 1
    assert task.phase == "PLACING"


# ═══════════════════════════════════════════════════════════════
# 失败落账
# ═══════════════════════════════════════════════════════════════

def test_controller_failure_is_committed_by_coordinator():
    coordinator = _coordinator()
    stub = _StubArmExecutor(_report(
        ExecutionState.FAILED, phase="FAILED",
        controller_failure="PLACE_ERROR: 0.02",
        failure_reason="PLACE_ERROR: 0.02"))
    task = _running_task(coordinator)
    coordinator.controllers["A"] = ControllerRecord(
        unit_id="A", task_id=task.task_id, executor=stub,
        execution_id="arm_A#1")

    coordinator._after_step(2.0)

    stored = coordinator.queue.get(task.task_id)
    assert stored.status.value == "FAILED"
    assert "PLACE_ERROR" in stored.failure_reason
    assert coordinator.frozen and coordinator.aborted
    assert "任务失败" in coordinator.stop_reason
    assert stub.polls == 1


def test_safety_latch_freezes_with_latch_reason():
    coordinator = _coordinator()
    stub = _StubArmExecutor(_report(
        ExecutionState.FAILED, phase="TRANSFERRING", safety_engaged=True,
        safety_latch_reason="tcp_collision",
        failure_reason="SAFETY_LATCH: tcp_collision"))
    task = _running_task(coordinator)
    coordinator.controllers["A"] = ControllerRecord(
        unit_id="A", task_id=task.task_id, executor=stub,
        execution_id="arm_A#1")

    coordinator._after_step(3.0)

    stored = coordinator.queue.get(task.task_id)
    assert stored.status.value == "FAILED"
    assert stored.failure_reason == "SAFETY_LATCH: tcp_collision"
    assert "安全锁存" in coordinator.stop_reason
    assert "tcp_collision" in coordinator.stop_reason
    failed_events = [row for row in coordinator.events
                     if row["kind"] == "TASK_FAILED"]
    assert failed_events[-1]["detail"]["reason"] == "SAFETY_LATCH"


def test_running_report_keeps_task_running():
    coordinator = _coordinator()
    stub = _StubArmExecutor(_report(ExecutionState.RUNNING, phase="LIFTING"))
    task = _running_task(coordinator)
    coordinator.controllers["A"] = ControllerRecord(
        unit_id="A", task_id=task.task_id, executor=stub,
        execution_id="arm_A#1")

    coordinator._after_step(1.0)
    coordinator._after_step(1.05)

    assert stub.polls == 2
    assert coordinator.queue.get(task.task_id).status.value == "RUNNING"
    assert not coordinator.frozen


# ═══════════════════════════════════════════════════════════════
# AGV 委托
# ═══════════════════════════════════════════════════════════════

def test_advance_route_delegates_to_agv_executor():
    coordinator = _coordinator()
    coordinator.agv_executor = _StubAgvExecutor()
    task = make_task_from_operation(CATALOG.operation("transport_BA"), 2)
    coordinator.route_task = task

    coordinator._advance_route(4.0)

    assert coordinator.agv_executor.pre_ticks == 1
    assert task.phase == "DRIVING"


def test_poll_agv_stays_idle_while_route_is_active():
    coordinator = _coordinator()
    coordinator.agv_executor = _StubAgvExecutor()
    task = make_task_from_operation(OPERATION, 3)
    coordinator.queue.submit(task, 0.0)

    coordinator._poll_agv(0.0)

    assert coordinator.agv_at_dock == "A"
    assert coordinator.queue.get(task.task_id).status.value == "PENDING"
