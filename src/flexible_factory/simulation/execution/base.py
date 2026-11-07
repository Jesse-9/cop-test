"""execution/base.py — 设备执行适配层的统一契约 (06-20 接口与代码落点).

契约只描述**执行生命周期**, 不承载调度策略:
  - ``start(request)``: 装配底层控制器/航线并登记一次执行, 不阻塞、不推进
    仿真; 返回稳定的 ``execution_id``;
  - ``pre_tick`` / ``post_tick``: 由协调器在统一步进点前后调用, 顺序与
    ``simulation/unit_controller_base.py`` 的控制器一致;
  - ``poll(execution_id)``: 只读查询, 返回 ``RUNNING`` / ``SUCCEEDED`` /
    ``FAILED`` 与执行证据.

语义边界 (06-20, 由测试固定):
  - ``SUCCEEDED`` 只表示执行端完成, 不代表物料已提交; 应用层验证
    (``_verify_unit_task`` / ``_verify_route``) 仍可能失败;
  - ``poll`` 不写任务队列、物料谱系或资源表, 不产生事件;
  - 会话资源 (影子臂/碰撞集合/吸盘) 由 runtime 与 runner 管理, adapter
    不创建也不销毁; 序列化内容只含 device/zone/alias/execution_id,
    不含 CoppeliaSim 句柄.

单设备单活动执行: 现行资源策略下每台臂与 AGV 各自最多一个活动执行,
因此 ``poll`` 对过期 ``execution_id`` 抛 :class:`ExecutorError`, 不做多路复用.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional, Protocol

from ...domain.process_model import OperationTemplate
from ...domain.tasks import Stage4Task
from ...util import jsonable

if TYPE_CHECKING:                       # 仅类型标注, 避免与 agv.py 循环导入
    from .agv import RouteSpec4


class ExecutionState(str, Enum):
    """执行端状态; 应用层验证结果不进入本枚举."""

    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"

    @property
    def terminal(self) -> bool:
        return self in (ExecutionState.SUCCEEDED, ExecutionState.FAILED)


class ExecutorError(RuntimeError):
    """执行适配层的契约错误: 设备不匹配、无活动执行、重复启动."""

    code = "V1_EXECUTION"


@dataclass(frozen=True)
class ExecutionRequest:
    """协调器下发给设备 adapter 的一次运行时命令 (不是持久化配方).

    ``task`` 是运行时任务上下文, 只在当前进程内使用, 不序列化;
    空载 reposition 也需要协调器创建的任务对象, 避免 adapter 自行构造
    临时任务。
    """

    operation: Optional[OperationTemplate]
    task: Stage4Task
    device_id: str
    uid: Optional[int]
    task_id: str
    sim_time: float
    route_spec: Optional["RouteSpec4"] = None


@dataclass(frozen=True)
class ExecutionReport:
    """一次执行的只读快照; 证据字段经 ``util.jsonable`` 归一."""

    execution_id: str
    device_id: str
    operation_id: str
    state: ExecutionState
    finished: bool
    failure_reason: str = ""
    failure_code: str = ""
    detail: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "execution_id": self.execution_id,
            "device_id": self.device_id,
            "operation_id": self.operation_id,
            "state": self.state.value,
            "finished": bool(self.finished),
            "failure_reason": self.failure_reason,
            "failure_code": self.failure_code,
            "detail": jsonable(self.detail),
            "evidence": jsonable(self.evidence),
        }


class Executor(Protocol):
    """设备执行适配器: 只有这三个入口参与步进."""

    kind: str
    device_id: str

    def start(self, request: ExecutionRequest) -> str: ...

    def pre_tick(self, sim_time: float) -> None: ...

    def post_tick(self, sim_time: float) -> None: ...

    def poll(self, execution_id: str) -> ExecutionReport: ...


def make_execution_id(device_id: str, serial: int) -> str:
    """稳定执行 ID; 只含设备与序号, 不含句柄或时间戳."""
    return f"{device_id}#{int(serial)}"


def ensure_device(request: ExecutionRequest, device_id: str) -> None:
    """校验请求目标与 adapter 一致; 不一致时拒绝执行."""
    if request.device_id != device_id:
        raise ExecutorError(
            f"执行请求设备 {request.device_id!r} 与适配器 "
            f"{device_id!r} 不一致")


def evidence_ok(report: ExecutionReport) -> bool:
    """便捷判断: 执行端成功且已结束."""
    return report.state is ExecutionState.SUCCEEDED and report.finished


__all__ = [
    "ExecutionRequest",
    "ExecutionReport",
    "ExecutionState",
    "Executor",
    "ExecutorError",
    "ensure_device",
    "evidence_ok",
    "make_execution_id",
]
