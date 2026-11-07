"""阶段 2: 结构化故障分类、局部隔离与全局停止 (纯 Python/记录型 executor)."""

from __future__ import annotations

from types import SimpleNamespace

from flexible_factory.application.coordinator import Stage4Coordinator
from flexible_factory.domain.default_process import default_catalog
from flexible_factory.domain.failures import (FailureScope, classify_failure,
                                              failure_class)
from flexible_factory.domain.materials import MaterialRegistry
from flexible_factory.domain.operation_runtime import (OperationInstance,
                                                       OperationRuntime,
                                                       TaskStatus)
from flexible_factory.domain.order_compiler import OrderDagCompiler
from flexible_factory.domain.orders import OrderRunContext
from flexible_factory.simulation.execution import (ExecutionReport,
                                                   ExecutionState)
from flexible_factory.simulation.layout import default_layout
from flexible_factory.simulation.material_manager import MaterialState
from order_fixtures import full_flow_order

CATALOG = default_catalog()
RECIPE = CATALOG.recipe("mixed_line_v1")
COMPILER = OrderDagCompiler(CATALOG)
ORDER = full_flow_order()
COMPILED = COMPILER.compile(ORDER, run_id="runfail")


# ═══════════════════════════════════════════════════════════════
# 分类表
# ═══════════════════════════════════════════════════════════════

def test_failure_classification_is_conservative():
    assert classify_failure(
        "NODE_PRECHECK_REJECTED", pre_physical=True,
        safe_state_confirmed=True) is FailureScope.NODE
    assert classify_failure(
        "NODE_PRECHECK_REJECTED", pre_physical=False,
        safe_state_confirmed=True) is FailureScope.GLOBAL
    assert classify_failure(
        "NODE_PRECHECK_REJECTED", pre_physical=True,
        safe_state_confirmed=False) is FailureScope.GLOBAL
    assert classify_failure(
        "SAFETY_LATCH", pre_physical=True,
        safe_state_confirmed=True) is FailureScope.GLOBAL
    assert classify_failure(
        "UNKNOWN_CODE", pre_physical=True,
        safe_state_confirmed=True) is FailureScope.GLOBAL
    assert failure_class("AGV_ROUTE_FAILED").default_scope is \
        FailureScope.GLOBAL


# ═══════════════════════════════════════════════════════════════
# 图级传播与收敛
# ═══════════════════════════════════════════════════════════════

def _runtime() -> OperationRuntime:
    compiled = COMPILER.compile(ORDER, run_id="runfail")
    runtime = OperationRuntime(CATALOG, RECIPE, order=compiled.context)
    runtime.load_order_instances(compiled.instances)
    return runtime


def _finish(runtime: OperationRuntime, instance: OperationInstance,
            sim_time: float) -> None:
    runtime.mark_started(instance.instance_id, sim_time)
    runtime.commit_success(instance.instance_id,
                           output_uids=[instance.uid],
                           sim_time=sim_time + 0.1)


def test_node_failure_blocks_only_dependent_descendants():
    runtime = _runtime()
    housing_start = runtime.instance("runfail:housing-start:1")
    bracket_start = runtime.instance("runfail:bracket-start:2")
    assert housing_start is not None and bracket_start is not None

    blocked = runtime.propagate_failure(
        housing_start.instance_id, "NODE_PRECHECK_REJECTED", 1.0,
        failure_code="NODE_PRECHECK_REJECTED")
    assert housing_start.state is TaskStatus.FAILED
    assert {row.node_id for row in blocked} == {
        "housing-finish", "housing-buffer", "kit-join"}
    assert all(row.state is TaskStatus.BLOCKED for row in blocked)
    # 独立支架分支不受影响, 仍可执行并提交.
    assert bracket_start.state is TaskStatus.PENDING
    _finish(runtime, bracket_start, 2.0)
    bracket_transport = runtime.instance("runfail:bracket-transport:2")
    _finish(runtime, bracket_transport, 3.0)
    bracket_buffer = runtime.instance("runfail:bracket-buffer:2")
    _finish(runtime, bracket_buffer, 4.0)
    assert bracket_buffer.state is TaskStatus.DONE
    assert runtime.progress_state() == "PARTIAL_FAILED"
    summary = runtime.failure_summary()
    assert summary["failed_nodes"] == ["housing-start"]
    assert "bracket-start" in summary["continued_nodes"]

    cancelled = runtime.cancel_unreachable(5.0)
    assert {row.node_id for row in cancelled} == {
        "housing-finish", "housing-buffer", "kit-join"}
    assert all(row.state is TaskStatus.CANCELLED for row in cancelled)
    assert runtime.progress_state() == "FAILED"


def test_join_is_blocked_when_one_and_input_fails():
    runtime = _runtime()
    runtime.mark_started("runfail:bracket-start:2", 0.5)
    runtime.commit_success("runfail:bracket-start:2", output_uids=[2],
                           sim_time=0.6)
    runtime.mark_started("runfail:bracket-transport:2", 0.7)
    runtime.commit_success("runfail:bracket-transport:2", output_uids=[2],
                           sim_time=0.8)
    runtime.mark_started("runfail:bracket-buffer:2", 0.9)
    runtime.commit_success("runfail:bracket-buffer:2", output_uids=[2],
                           sim_time=1.0)
    blocked = runtime.propagate_failure(
        "runfail:housing-start:1", "NODE_PRECHECK_REJECTED", 1.1,
        failure_code="NODE_PRECHECK_REJECTED")
    join = runtime.instance("runfail:kit-join:1+2")
    assert join in blocked
    assert join.state is TaskStatus.BLOCKED
    assert join.blocked_by == ["runfail:housing-start:1"]
    # 已 DONE 的独立分支不被改写.
    assert runtime.instance("runfail:bracket-buffer:2").state is \
        TaskStatus.DONE


def test_partial_failed_when_other_branch_satisfies_target():
    """目标已完成且有无关失败节点时, 订单为 PARTIAL_FAILED."""
    target = "run:ok:target"
    failed = "run:bad:source"
    graph_runtime = OperationRuntime(CATALOG, RECIPE, order=OrderRunContext(
        order_id="partial", order_version=1, dag_digest="d" * 64,
        scene_id="L5_UFLOW", node_ids=("bad", "ok"),
        target_item_type="FINAL", target_quantity=1,
        target_instance_ids=(target,)))
    ok = graph_runtime.graph.add(OperationInstance(
        instance_id=target, task_id="target_ok", template_id="kit_join",
        kind="logical_join", uid=1, source="buffer_e_c",
        destination="output_e", run_id="run", node_id="ok",
        order_id="partial", order_version=1,
        state=TaskStatus.DONE, committed_sim_time=1.0,
        selected_route=""))
    graph_runtime.graph.add(OperationInstance(
        instance_id=failed, task_id="bad_source",
        template_id="housing_direct_A", kind="a_conveyor", uid=2,
        source="line_s1a", destination="handoff_ac", run_id="run",
        node_id="bad", order_id="partial", order_version=1,
        selected_route=""))
    graph_runtime.propagate_failure(
        failed, "NODE_PRECHECK_REJECTED", 2.0,
        failure_code="NODE_PRECHECK_REJECTED")
    assert ok.state is TaskStatus.DONE
    assert graph_runtime.progress_state() == "PARTIAL_FAILED"


# ═══════════════════════════════════════════════════════════════
# 协调器: NODE 局部失败继续独立分支
# ═══════════════════════════════════════════════════════════════

class _Sim:
    colorcomponent_ambient_diffuse = 1

    def __init__(self, fail_step: bool = False) -> None:
        self.time = 0.0
        self.fail_step = fail_step

    def getSimulationTime(self) -> float:
        return self.time

    def step(self) -> None:
        if self.fail_step:
            raise RuntimeError("session lost")
        self.time += 0.5

    def setObjectColor(self, *args, **kwargs) -> None:
        pass


class _Scene:
    stage4_layout = default_layout()

    def __init__(self) -> None:
        self.next_handle = 900
        self.removed: list[int] = []

    def spawn_material(self, uid, alias, position=None) -> int:
        self.next_handle += 1
        return self.next_handle

    def remove_material(self, handle) -> None:
        self.removed.append(int(handle))

    def agv_dock_hit_at(self, *args) -> bool:
        return True


class _MaterialRec:
    def __init__(self, uid: int, handle: int) -> None:
        self.uid = uid
        self.alias = f"Part_{uid:04d}"
        self.sim_handle = handle
        self.state = MaterialState.RELEASED
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
    def __init__(self) -> None:
        self.arms: dict = {}

    def forget_material(self, handle) -> None:
        pass

    def remove_hazards(self) -> None:
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
    """记录型臂适配器; 可按 uid 返回结构化失败 report."""

    kind = "arm"

    def __init__(self, unit_id: str, *, fail_uid: int | None = None,
                 report: ExecutionReport | None = None,
                 poll_error: bool = False) -> None:
        self.unit_id = unit_id
        self.device_id = f"arm_{unit_id}"
        self.fail_uid = fail_uid
        self.failure_report = report
        self.poll_error = poll_error
        self.starts: list[int] = []
        self.releases: list[str] = []
        self.uid: int | None = None
        self._controller = SimpleNamespace(
            state=SimpleNamespace(name="PLACING"), source="conveyor",
            sink="station", dock_id="", summary=lambda: {})

    @property
    def controller(self):
        return self._controller

    def start(self, request) -> str:
        self.uid = request.uid
        self.starts.append(int(request.uid))
        return f"{self.device_id}#{len(self.starts)}"

    def pre_tick(self, sim_time: float) -> None:
        pass

    def post_tick(self, sim_time: float) -> None:
        pass

    def poll(self, execution_id: str) -> ExecutionReport:
        if self.poll_error:
            raise RuntimeError("poll exploded")
        fail = (self.failure_report is not None
                and (self.fail_uid is None or self.uid == self.fail_uid))
        if fail:
            return self.failure_report
        return ExecutionReport(
            execution_id=execution_id, device_id=self.device_id,
            operation_id="", state=ExecutionState.SUCCEEDED, finished=True,
            failure_reason="", detail={"phase": "DONE"}, evidence={})

    def release(self, reason: str = "") -> None:
        self.releases.append(reason)


class _StubAgvExecutor:
    """记录型 AGV 适配器: 航线一步完成, 供独立分支收敛使用."""

    kind = "agv"
    device_id = "agv_1"

    def __init__(self) -> None:
        self._active = False
        self._spec = None
        self._serial = 0

    @property
    def active(self) -> bool:
        return self._active

    @property
    def execution_id(self) -> str:
        return f"agv_1#{self._serial}" if self._active else ""

    @property
    def spec(self):
        return self._spec

    @property
    def controller(self):
        return SimpleNamespace(metrics=SimpleNamespace(stop_error=0.0,
                                                       heading_error=0.0),
                               spec=self._spec,
                               summary=lambda: {"state": "DONE"})

    @property
    def route(self):
        return self.controller if self._active else None

    @property
    def state(self):
        return SimpleNamespace(value="DRIVING")

    def start(self, request) -> str:
        self._serial += 1
        self._spec = request.route_spec
        self._active = True
        return f"agv_1#{self._serial}"

    def pre_tick(self, sim_time: float) -> None:
        pass

    def poll(self, execution_id: str) -> ExecutionReport:
        from flexible_factory.simulation.agv_route import RouteState
        return ExecutionReport(
            execution_id=execution_id, device_id=self.device_id,
            operation_id="", state=ExecutionState.SUCCEEDED, finished=True,
            detail={"route_state": RouteState.DONE.value,
                    "route_id": self._spec.route_id if self._spec else "",
                    "loaded": bool(self._spec.loaded) if self._spec else False},
            evidence={"route": {"state": RouteState.DONE.value,
                                "spec": {"loaded": bool(
                                    self._spec.loaded) if self._spec else False},
                                "metrics": {"stop_error": 0.0,
                                            "heading_error": 0.0,
                                            "material_slip": 0.0}}})

    def release(self, reason: str = "") -> None:
        self._active = False

    def resume(self, sim_time: float, *, clearance_ok: bool,
               reason: str = "") -> bool:
        return True


def _coordinator(*, batch=None, fail_uid=None, failure_report=None,
                 poll_error_unit=None, fail_step=False, order_plan=None
                 ) -> Stage4Coordinator:
    sim = _Sim(fail_step=fail_step)
    scene = _Scene()
    materials = _Materials()
    comp = {
        "materials": materials, "flow": MaterialRegistry(),
        "agv": _Agv(), "runtime": _RuntimeStub(), "ik": None,
        "suction": {unit_id: SimpleNamespace(forget=lambda uid: None)
                    for unit_id in "ABCDE"},
        "conveyor": None,
    }
    coordinator = Stage4Coordinator(
        sim, scene, comp, scenario="full_flow",
        batch=batch or {"RAW_A": 1}, order_plan=order_plan)
    arms = {}
    for unit_id in "ABCDE":
        arms[f"arm_{unit_id}"] = _StubArm(
            unit_id,
            fail_uid=fail_uid if unit_id == "A" else None,
            report=failure_report if unit_id == "A" else None,
            poll_error=(poll_error_unit == unit_id))
    coordinator.arm_executors = arms
    coordinator.agv_executor = _StubAgvExecutor()
    coordinator._agv_at_dock = lambda dock: True
    coordinator._verify_route = lambda task, route: (True, "stub route")
    coordinator._verify_unit_task = \
        lambda task, controller, unit_id: (True, "dry-run verified", {})
    return coordinator


def _run(coordinator: Stage4Coordinator, limit: int = 240) -> int:
    steps = 0
    while not coordinator.finished:
        coordinator.step()
        steps += 1
        assert steps < limit, "运行未能收敛"
    return steps


def test_coordinator_isolates_node_failure_and_continues_independent_uid():
    report = ExecutionReport(
        execution_id="arm_A#1", device_id="arm_A", operation_id="",
        state=ExecutionState.FAILED, finished=True,
        failure_reason="NODE_PRECHECK_REJECTED: material not ready",
        failure_code="NODE_PRECHECK_REJECTED",
        detail={"phase": "FAILED", "physical_action_started": False},
        evidence={})
    coordinator = _coordinator(batch={"RAW_A": 1, "RAW_D": 1},
                               fail_uid=1, failure_report=report)
    coordinator.start(0.0)
    _run(coordinator)

    assert not coordinator.frozen and not coordinator.aborted
    assert coordinator.failures
    node_failure = coordinator.failures[0]
    assert node_failure["scope"] == "NODE"
    assert node_failure["node_id"] == "housing_direct_A"
    # 壳体后继被阻断; 独立支架分支 D→AGV→E 继续完成.
    runtime = coordinator.op_runtime
    assert runtime.instance("run0001:1:housing_direct_A").state is \
        TaskStatus.FAILED
    assert runtime.instance("run0001:1:housing_finish").state is \
        TaskStatus.CANCELLED
    assert runtime.instance("run0002:2:bracket_process").state is \
        TaskStatus.DONE
    assert runtime.instance("run0002:2:transport_DE").state is \
        TaskStatus.DONE
    assert runtime.instance("run0002:2:bracket_to_E").state is \
        TaskStatus.DONE
    assert coordinator.order_status() == "FAILED"
    assert all(owner is None
               for owner in coordinator.resources.owners.values())


def test_coordinator_safety_latch_is_global_and_releases_resources():
    report = ExecutionReport(
        execution_id="arm_A#1", device_id="arm_A", operation_id="",
        state=ExecutionState.FAILED, finished=True,
        failure_reason="SAFETY_LATCH: tcp_collision",
        failure_code="SAFETY_LATCH",
        detail={"phase": "TRANSFERRING", "safety_engaged": True,
                "safety_latch_reason": "tcp_collision"},
        evidence={})
    coordinator = _coordinator(failure_report=report)
    coordinator.start(0.0)
    _run(coordinator)

    assert coordinator.frozen and coordinator.aborted
    assert any(row["scope"] == "GLOBAL"
               and row["failure_code"] == "SAFETY_LATCH"
               for row in coordinator.failures)
    assert all(owner is None
               for owner in coordinator.resources.owners.values())
    assert not coordinator.controllers


def test_coordinator_sim_step_failure_stops_all_and_marks_global():
    coordinator = _coordinator(fail_step=True)
    coordinator.start(0.0)
    _run(coordinator)
    assert coordinator.frozen and coordinator.aborted
    assert coordinator._sim_failure
    assert any(row["failure_code"] == "SIM_STEP_FAILED"
               for row in coordinator.failures)
    assert all(owner is None
               for owner in coordinator.resources.owners.values())


def test_coordinator_poll_exception_defaults_to_global():
    coordinator = _coordinator(poll_error_unit="A")
    coordinator.start(0.0)
    _run(coordinator)
    assert coordinator.frozen and coordinator.aborted
    assert any(row["failure_code"] == "UNKNOWN_EXECUTOR_FAILURE"
               for row in coordinator.failures)


def test_coordinator_unknown_verification_exception_defaults_to_global():
    coordinator = _coordinator()
    coordinator._verify_unit_task = \
        lambda task, controller, unit_id: (_ for _ in ()).throw(
            RuntimeError("verification exploded"))
    coordinator.start(0.0)
    _run(coordinator)
    assert coordinator.frozen and coordinator.aborted
    assert any(row["failure_code"] == "UNKNOWN_FAILURE"
               for row in coordinator.failures)
