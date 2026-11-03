"""阶段 2 步骤 4: AGV 航线适配器的状态与证据映射 (不需要 CoppeliaSim).

用假航线控制器替换 `AGVRouteController4`, 验证 route intent 构造、逐 tick
推进转发、`DONE`/`FAILED` 映射与阻塞子状态。真实轮速路线由 focused 仿真
测试覆盖。
"""

from __future__ import annotations

from flexible_factory.domain.default_process import default_catalog
from flexible_factory.domain.tasks import make_task_from_operation
from flexible_factory.simulation.agv import make_route_spec
from flexible_factory.simulation.agv_route import RouteState
from flexible_factory.simulation.execution import (AgvExecutor,
                                                   ExecutionRequest,
                                                   ExecutionState,
                                                   ExecutorError)
from flexible_factory.simulation.layout import default_layout

CATALOG = default_catalog()
LAYOUT = default_layout()
TRANSPORT = CATALOG.operation("transport_BA")
REPOSITION_SPEC = make_route_spec(LAYOUT, "B", "A", loaded=False)
TRANSPORT_SPEC = make_route_spec(LAYOUT, "B", "A", loaded=True)


class _FakeRoute:
    """只实现 adapter 需要的航线表面."""

    def __init__(self, spec) -> None:
        self.spec = spec
        self.state = RouteState.DRIVING
        self.failure_reason = ""
        self.steps: list[float] = []
        self.resumes: list[dict] = []

    @property
    def blocked(self) -> bool:
        return self.state is RouteState.BLOCKED

    def step(self, sim_time: float) -> None:
        self.steps.append(float(sim_time))

    def resume(self, sim_time: float, *, clearance_ok: bool,
               reason: str = "") -> bool:
        self.resumes.append({"sim_time": sim_time,
                             "clearance_ok": clearance_ok,
                             "reason": reason})
        if clearance_ok:
            self.state = RouteState.DRIVING
        return bool(clearance_ok)

    def summary(self) -> dict:
        return {"route_id": self.spec.route_id,
                "state": self.state.value,
                "loaded": bool(self.spec.loaded),
                "lanes": list(self.spec.lanes)}


class _StubAgvExecutor(AgvExecutor):
    def __init__(self) -> None:
        super().__init__(None, None, LAYOUT)
        self.stub = None

    def _build_controller(self, spec, request):
        self.stub = _FakeRoute(spec)
        return self.stub


def _request(spec=TRANSPORT_SPEC, operation=TRANSPORT, uid: int = 2):
    task = make_task_from_operation(operation, uid)
    return ExecutionRequest(
        operation=operation, task=task, device_id="agv_1", uid=uid,
        task_id=task.task_id, sim_time=0.0, route_spec=spec)


# ═══════════════════════════════════════════════════════════════
# 启动与推进
# ═══════════════════════════════════════════════════════════════

def test_start_uses_route_intent_and_stays_running():
    executor = _StubAgvExecutor()
    execution_id = executor.start(_request())
    assert execution_id == "agv_1#1"
    assert executor.stub.spec is TRANSPORT_SPEC
    report = executor.poll(execution_id)
    assert report.state is ExecutionState.RUNNING
    assert report.finished is False
    assert report.detail["route_id"] == TRANSPORT_SPEC.route_id
    assert report.detail["loaded"] is True
    assert report.detail["route_state"] == "DRIVING"


def test_pre_tick_forwards_to_route_step():
    executor = _StubAgvExecutor()
    executor.start(_request())
    executor.pre_tick(0.05)
    executor.pre_tick(0.10)
    assert executor.stub.steps == [0.05, 0.10]
    assert executor.poll(executor.execution_id).state is ExecutionState.RUNNING


def test_done_route_reports_success_with_metrics():
    executor = _StubAgvExecutor()
    execution_id = executor.start(_request())
    executor.stub.state = RouteState.DONE
    report = executor.poll(execution_id)
    assert report.state is ExecutionState.SUCCEEDED
    assert report.finished is True
    assert set(report.evidence) == {"route"}
    assert report.detail["route_state"] == "DONE"
    assert report.detail["lanes"]


def test_failed_route_reports_reason():
    executor = _StubAgvExecutor()
    execution_id = executor.start(_request())
    executor.stub.state = RouteState.FAILED
    executor.stub.failure_reason = "STOP_ERROR: 0.08"
    report = executor.poll(execution_id)
    assert report.state is ExecutionState.FAILED
    assert report.failure_reason == "STOP_ERROR: 0.08"


def test_blocked_route_stays_running_with_substate():
    executor = _StubAgvExecutor()
    execution_id = executor.start(_request())
    executor.stub.state = RouteState.BLOCKED
    report = executor.poll(execution_id)
    assert report.state is ExecutionState.RUNNING
    assert report.finished is False
    assert report.detail["route_state"] == "BLOCKED"
    assert executor.blocked is True


def test_resume_is_forwarded_to_route():
    executor = _StubAgvExecutor()
    executor.start(_request())
    executor.stub.state = RouteState.BLOCKED
    assert executor.resume(1.5, clearance_ok=True,
                           reason="obstacle_removed") is True
    assert executor.stub.resumes == [{"sim_time": 1.5, "clearance_ok": True,
                                      "reason": "obstacle_removed"}]
    assert executor.blocked is False


def test_empty_reposition_uses_same_entry():
    executor = _StubAgvExecutor()
    execution_id = executor.start(_request(spec=REPOSITION_SPEC, uid=2))
    report = executor.poll(execution_id)
    assert report.detail["loaded"] is False
    assert executor.stub.spec.loaded is False
    assert report.state is ExecutionState.RUNNING


# ═══════════════════════════════════════════════════════════════
# 生命周期约束
# ═══════════════════════════════════════════════════════════════

def test_start_requires_route_spec():
    executor = _StubAgvExecutor()
    task = make_task_from_operation(TRANSPORT, 2)
    request = ExecutionRequest(operation=TRANSPORT, task=task,
                               device_id="agv_1", uid=2,
                               task_id=task.task_id, sim_time=0.0)
    try:
        executor.start(request)
    except ExecutorError as exc:
        assert "route_spec" in str(exc)
    else:
        raise AssertionError("缺少路线意图必须拒绝")


def test_second_route_is_rejected_until_release():
    executor = _StubAgvExecutor()
    executor.start(_request())
    try:
        executor.start(_request())
    except ExecutorError as exc:
        assert "已有活动航线" in str(exc)
    else:
        raise AssertionError("同一 AGV 不得并发两条航线")
    executor.release("route complete")
    assert executor.active is False
    assert executor.start(_request()) == "agv_1#2"


def test_poll_after_release_raises():
    executor = _StubAgvExecutor()
    execution_id = executor.start(_request())
    executor.release("route complete")
    try:
        executor.poll(execution_id)
    except ExecutorError as exc:
        assert execution_id in str(exc)
    else:
        raise AssertionError("过期航线必须抛 ExecutorError")


def test_request_for_other_device_is_rejected():
    executor = _StubAgvExecutor()
    task = make_task_from_operation(TRANSPORT, 2)
    request = ExecutionRequest(operation=TRANSPORT, task=task,
                               device_id="arm_A", uid=2,
                               task_id=task.task_id, sim_time=0.0,
                               route_spec=TRANSPORT_SPEC)
    try:
        executor.start(request)
    except ExecutorError as exc:
        assert "不一致" in str(exc)
    else:
        raise AssertionError("设备不匹配必须拒绝")


def test_route_spec_carries_stable_docks_and_lanes():
    assert TRANSPORT_SPEC.start_dock == "B"
    assert TRANSPORT_SPEC.end_dock == "A"
    assert TRANSPORT_SPEC.loaded is True
    assert REPOSITION_SPEC.loaded is False
    assert set(TRANSPORT_SPEC.lanes) <= {
        lane for lane in ("agv_lane_west", "agv_lane_south",
                          "agv_lane_east")}
