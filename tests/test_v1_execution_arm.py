"""阶段 2 步骤 3: 机械臂执行适配器的状态与证据映射 (不需要 CoppeliaSim).

用假控制器替换 `Stage4UnitController`, 只验证 adapter 的契约:
`start` 立即返回、`pre/post_tick` 转发、`poll` 三态映射、证据键与释放。
真实控制器的 IK/碰撞/吸盘路径由 focused 仿真测试覆盖。
"""

from __future__ import annotations

from flexible_factory.domain.default_process import default_catalog
from flexible_factory.domain.tasks import make_task_from_operation
from flexible_factory.simulation.execution import (ArmExecutor,
                                                   ExecutionRequest,
                                                   ExecutionState,
                                                   ExecutorError)
from flexible_factory.simulation.unit_controller_base import UnitTaskState

CATALOG = default_catalog()
ARM_OPERATION = CATALOG.operation("housing_direct_A")


class _FakeLatch:
    def __init__(self, reason: str = "tcp_collision") -> None:
        self.reason = reason

    def as_dict(self) -> dict:
        return {"reason": self.reason}


class _FakeSafety:
    def __init__(self) -> None:
        self.engaged = False
        self.latch = _FakeLatch()


class _FakeController:
    """只实现 adapter 需要的控制器表面."""

    def __init__(self) -> None:
        self.state = UnitTaskState.WAITING_FOR_ITEM
        self.failure_reason = ""
        self.finished = False
        self.safety = _FakeSafety()
        self.pre_ticks = 0
        self.post_ticks = 0

    def pre_tick(self) -> None:
        self.pre_ticks += 1

    def post_tick(self) -> None:
        self.post_ticks += 1

    def summary(self) -> dict:
        return {"unit_id": "A", "state": self.state.name,
                "finished": self.finished}


class _StubArmExecutor(ArmExecutor):
    """注入假控制器, 跳过真实装配."""

    def __init__(self, device_id: str = "arm_A") -> None:
        device = CATALOG.device(device_id)
        super().__init__(None, _EmptyScene(), _EmptyComp(), device=device,
                         catalog=CATALOG)
        self.stub = _FakeController()

    def _build_controller(self, operation, request):
        return self.stub


class _EmptyScene:
    stage4_layout = None


class _EmptyComp:
    def __getitem__(self, key):
        raise AssertionError(f"纯测试不应访问组件 {key!r}")


def _request(operation=ARM_OPERATION, device_id: str = "arm_A",
             uid: int = 1) -> ExecutionRequest:
    task = make_task_from_operation(operation, uid)
    return ExecutionRequest(
        operation=operation, task=task, device_id=device_id, uid=uid,
        task_id=task.task_id, sim_time=0.0)


# ═══════════════════════════════════════════════════════════════
# 启动与轮询
# ═══════════════════════════════════════════════════════════════

def test_start_returns_execution_id_without_completing():
    executor = _StubArmExecutor()
    execution_id = executor.start(_request())
    assert execution_id == "arm_A#1"
    assert executor.active and executor.execution_id == execution_id
    report = executor.poll(execution_id)
    assert report.state is ExecutionState.RUNNING
    assert report.finished is False
    assert report.operation_id == ARM_OPERATION.id
    assert report.device_id == "arm_A"


def test_pre_and_post_tick_delegate_to_controller():
    executor = _StubArmExecutor()
    execution_id = executor.start(_request())
    executor.pre_tick(0.0)
    executor.post_tick(0.05)
    assert executor.stub.pre_ticks == 1
    assert executor.stub.post_ticks == 1
    assert executor.poll(execution_id).state is ExecutionState.RUNNING


def test_pre_tick_is_skipped_once_controller_finished():
    executor = _StubArmExecutor()
    execution_id = executor.start(_request())
    executor.stub.finished = True
    executor.stub.state = UnitTaskState.DONE
    executor.pre_tick(0.1)
    assert executor.stub.pre_ticks == 0
    assert executor.poll(execution_id).state is ExecutionState.SUCCEEDED


def test_poll_reports_success_with_controller_evidence():
    executor = _StubArmExecutor()
    execution_id = executor.start(_request())
    executor.stub.finished = True
    executor.stub.state = UnitTaskState.DONE
    report = executor.poll(execution_id)
    assert report.state is ExecutionState.SUCCEEDED
    assert report.finished is True
    assert report.failure_reason == ""
    assert set(report.evidence) == {"controller"}
    assert report.detail["phase"] == "DONE"
    payload = report.as_dict()
    assert payload["state"] == "SUCCEEDED"
    assert isinstance(payload["evidence"], dict)


def test_poll_maps_controller_failure():
    executor = _StubArmExecutor()
    execution_id = executor.start(_request())
    executor.stub.state = UnitTaskState.FAILED
    executor.stub.failure_reason = "PLACE_ERROR: 0.02"
    report = executor.poll(execution_id)
    assert report.state is ExecutionState.FAILED
    assert report.finished is True
    assert report.failure_reason == "PLACE_ERROR: 0.02"
    assert report.detail["controller_failure"] == "PLACE_ERROR: 0.02"


def test_poll_maps_safety_latch_before_controller_state():
    executor = _StubArmExecutor()
    execution_id = executor.start(_request())
    executor.stub.state = UnitTaskState.TRANSFERRING
    executor.stub.safety.engaged = True
    report = executor.poll(execution_id)
    assert report.state is ExecutionState.FAILED
    assert report.failure_reason == "SAFETY_LATCH: tcp_collision"
    assert report.detail["safety_engaged"] is True
    assert report.detail["safety_latch_reason"] == "tcp_collision"
    assert "safety" in report.evidence


# ═══════════════════════════════════════════════════════════════
# 生命周期约束
# ═══════════════════════════════════════════════════════════════

def test_second_start_is_rejected_until_release():
    executor = _StubArmExecutor()
    executor.start(_request())
    try:
        executor.start(_request(uid=2))
    except ExecutorError as exc:
        assert "已有活动执行" in str(exc)
    else:
        raise AssertionError("同一设备不得并发两个执行")
    executor.release("done")
    assert executor.active is False
    assert executor.start(_request(uid=2)) == "arm_A#2"


def test_poll_after_release_raises():
    executor = _StubArmExecutor()
    execution_id = executor.start(_request())
    executor.release("unit task complete")
    try:
        executor.poll(execution_id)
    except ExecutorError as exc:
        assert executor.device_id in str(exc)
    else:
        raise AssertionError("过期执行必须抛 ExecutorError")


def test_request_for_other_device_is_rejected():
    executor = _StubArmExecutor("arm_A")
    try:
        executor.start(_request(device_id="arm_B"))
    except ExecutorError as exc:
        assert "不一致" in str(exc)
    else:
        raise AssertionError("设备不匹配必须拒绝")


def test_operation_is_required_for_arm_execution():
    executor = _StubArmExecutor()
    task = make_task_from_operation(ARM_OPERATION, 1)
    request = ExecutionRequest(operation=None, task=task,
                               device_id="arm_A", uid=1,
                               task_id=task.task_id, sim_time=0.0)
    try:
        executor.start(request)
    except ExecutorError as exc:
        assert "工序上下文" in str(exc)
    else:
        raise AssertionError("缺少工序时必须拒绝")


def test_non_arm_device_is_rejected_at_construction():
    agv_device = CATALOG.device("agv_1")
    try:
        ArmExecutor(None, _EmptyScene(), _EmptyComp(), device=agv_device,
                    catalog=CATALOG)
    except ExecutorError as exc:
        assert "arm" in str(exc)
    else:
        raise AssertionError("ArmExecutor 不得接受非臂设备")


def test_sink_spec_rejects_unknown_station_zone():
    from dataclasses import replace

    from flexible_factory.domain.materials import ItemType
    from flexible_factory.domain.process_model import MaterialPort
    from flexible_factory.simulation.execution.arm import sink_spec_for

    operation = replace(
        ARM_OPERATION, output=MaterialPort("zone_unknown", ItemType.FINAL))
    try:
        sink_spec_for(operation, "A", _EmptyScene(), {})
    except ExecutorError as exc:
        assert "zone_unknown" in str(exc)
        assert operation.id in str(exc)
    else:
        raise AssertionError("未知放置区域必须拒绝")
