"""transport_fleet.py — 双车编制骨架（P2 第一步，默认不启用）.

本模块提供"第二台运输车"的**运行时对象与执行器契约**，供协调器后续接管：

  - :class:`FleetConfig`：`single` / `dual` 两种编制；dual 下按工序类型固定
    分工（壳体回程 → `agv_1`，支架运输 → `cart_2`）；
  - :class:`VehicleRuntime`：一台车的运行时槽（执行器 / 所在 dock /
    当前航线任务 / 执行 ID），是协调器把 `self.agv_*` 单例搬进 fleet 的容器；
  - :class:`KinematicTransportExecutor`：给小车二用的运动学执行器，实现与
    :class:`~flexible_factory.simulation.execution.agv.AgvExecutor` 相同的
    `start / pre_tick / post_tick / poll / active` 契约，但**不写轮速**，
    而是按仿真时间推进位姿（不调用 `sim.step()`）。

**当前状态：未被任何生产代码导入，`single` 模式下行为完全不变。**
要真正启用 dual，还差三件事（缺一不可）：

  1. `application/coordinator.py`：`self.agv / route_task / route_execution_id /
     agv_at_dock / agv_executor` 收进 :class:`VehicleRuntime`，并把
     `_poll_agv / _start_transport / _launch_route / _advance_route /
     _after_route` 改为按车遍历；
  2. `simulation/unit_controller*`：A/E 的"AGV 取放"目前按固定别名
     （`AGV_LoadTray` 等）解析托盘——小车二要有自己的托盘子对象与 dock 传感器，
     否则机械臂会把物料放到 AGV 的托盘上；
  3. `domain/default_process.py`：`transport_DE` 的候选设备加入 `cart_2`，
     `PROCESS_MODEL_VERSION` 递增 → **必须新建工件 v005**（契约钉住了版本号）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Optional

from .execution.base import (ExecutionReport, ExecutionRequest, ExecutionState,
                             ExecutorError, ensure_device, make_execution_id)
from .transport_schedule import CART_2, NODES, plan_route

FLEET_SINGLE = "single"
FLEET_DUAL = "dual"
FLEET_MODES = (FLEET_SINGLE, FLEET_DUAL)

#: 单车道模式下小车二的停车位: 不占 dock、不占直道 (东侧空地)。
CART2_PARK_POSE = (3.50, 2.30, 0.0)

#: 固定分工：工序类型 → 车辆。未列出的类型走 `default_vehicle`。
_DUAL_ASSIGNMENT = {
    "b_conveyor": "agv_1",          # B 装载（AGV 空载到位）
    "a_agv": "agv_1",               # A 从 AGV 卸货
    "transport_ba": "agv_1",        # 壳体回程 B→A
    "d_conveyor": "cart_2",         # D 装载
    "e_agv": "cart_2",              # E 从车卸货
    "transport_de": "cart_2",       # 支架运输 D→E
}


@dataclass(frozen=True)
class FleetConfig:
    """车队编制。`single` 保持 v1 现状（所有运输走 agv_1）。"""

    mode: str = FLEET_SINGLE
    default_vehicle: str = "agv_1"

    def __post_init__(self) -> None:
        if self.mode not in FLEET_MODES:
            raise ValueError(f"未知车队模式: {self.mode!r}")

    @property
    def dual(self) -> bool:
        return self.mode == FLEET_DUAL

    def vehicle_for(self, kind: str) -> str:
        if not self.dual:
            return self.default_vehicle
        return _DUAL_ASSIGNMENT.get(kind, self.default_vehicle)

    def as_dict(self) -> dict:
        return {"mode": self.mode,
                "assignment": ({k: self.vehicle_for(k) for k in _DUAL_ASSIGNMENT}
                               if self.dual else {})}


@dataclass
class VehicleRuntime:
    """一台车的运行时槽；协调器把原来的单例字段搬到这里。"""

    vehicle_id: str
    executor: object = None
    at_dock: str = ""
    route_task: object = None
    route_execution_id: str = ""

    @property
    def active(self) -> bool:
        return bool(getattr(self.executor, "active", False))

    def as_dict(self) -> dict:
        return {"vehicle_id": self.vehicle_id, "at_dock": self.at_dock,
                "active": self.active,
                "task_id": getattr(self.route_task, "task_id", "")}


# ═══════════════════════════════════════════════════════════════
# 运动学执行器（小车二）
# ═══════════════════════════════════════════════════════════════

@dataclass
class _KinematicMetrics:
    """运动学车的"完美"指标; 到站误差与打滑按 0 计。"""

    stop_error: float = 0.0
    heading_error: float = 0.0
    material_slip: float = 0.0


class _KinematicControllerView:
    """协调器 `_verify_route` 需要的只读控制器视图。"""

    def __init__(self, spec) -> None:
        self.spec = spec
        self.metrics = _KinematicMetrics()


@dataclass
class _KinematicExecution:
    execution_id: str
    operation_id: str
    uid: Optional[int]
    route: object
    started_at: float
    duration: float
    state: ExecutionState = ExecutionState.RUNNING
    elapsed: float = 0.0
    failure: str = ""


Mover = Callable[[float, float, float], None]


def pose_along_route(route, ratio: float) -> tuple[float, float, float]:
    """按行程比例返回 (x, y, yaw)；用于运动学推进。"""
    ratio = max(0.0, min(1.0, float(ratio)))
    nodes = route.nodes
    if len(nodes) < 2:
        x, y = NODES[nodes[0]]
        return x, y, math.pi / 2.0
    total = route.length or 1e-9
    remaining = ratio * total
    for index in range(len(nodes) - 1):
        start = NODES[nodes[index]]
        end = NODES[nodes[index + 1]]
        segment = math.dist(start, end)
        if remaining <= segment or index == len(nodes) - 2:
            local = 0.0 if segment <= 0 else min(1.0, remaining / segment)
            x = start[0] + (end[0] - start[0]) * local
            y = start[1] + (end[1] - start[1]) * local
            yaw = math.atan2(end[1] - start[1], end[0] - start[0])
            return x, y, yaw
        remaining -= segment
    start, end = NODES[nodes[-2]], NODES[nodes[-1]]
    return end[0], end[1], math.atan2(end[1] - start[1], end[0] - start[0])


class KinematicTransportExecutor:
    """小车二的执行器：与 AgvExecutor 同契约，按仿真时间推进位姿。"""

    kind = "agv"

    def __init__(self, sim=None, *, device_id: str = "cart_2",
                 speed: float = CART_2.speed,
                 turn_time: float = CART_2.turn_time,
                 mover: Optional[Mover] = None,
                 logger=None) -> None:
        self.sim = sim
        self.device_id = device_id
        self.speed = float(speed)
        self.turn_time = float(turn_time)
        self.logger = logger
        self._mover = mover or self._default_mover
        self._handle = -1
        self._spec = None
        self._serial = 0
        self._active: Optional[_KinematicExecution] = None
        self._last_report: dict = {}

    # ── 契约 ────────────────────────────────────────────────

    @property
    def active(self) -> bool:
        return self._active is not None

    @property
    def state(self) -> ExecutionState:
        """与 AgvExecutor.state 同形：供协调器写任务阶段文本。"""
        if self._active is None:
            return ExecutionState.SUCCEEDED
        return self._active.state

    def start(self, request: ExecutionRequest) -> str:
        ensure_device(request, self.device_id)
        spec = request.route_spec
        if spec is None:
            raise ExecutorError("运动学执行器需要 route_spec")
        route = plan_route(spec.start_dock, spec.end_dock)
        self._spec = spec
        duration = (route.length / self.speed
                    + route.turns * self.turn_time)
        self._serial += 1
        execution = _KinematicExecution(
            execution_id=make_execution_id(self.device_id, self._serial),
            operation_id=(request.operation.id
                          if request.operation is not None else ""),
            uid=request.uid,
            route=route,
            started_at=float(request.sim_time),
            duration=max(duration, 1e-6),
        )
        self._active = execution
        x, y, yaw = pose_along_route(route, 0.0)
        self._mover(x, y, yaw)
        return execution.execution_id

    def pre_tick(self, sim_time: float) -> None:
        if self._active is None:
            return
        execution = self._active
        elapsed = float(sim_time) - execution.started_at
        execution.elapsed = elapsed
        ratio = elapsed / execution.duration
        x, y, yaw = pose_along_route(execution.route, ratio)
        self._mover(x, y, yaw)
        if execution.state is ExecutionState.RUNNING and elapsed >= execution.duration:
            execution.state = ExecutionState.SUCCEEDED

    def post_tick(self, sim_time: float) -> None:
        return None

    def poll(self, execution_id: str) -> ExecutionReport:
        if self._active is None or self._active.execution_id != execution_id:
            raise ExecutorError(f"{self.device_id}: 没有执行 {execution_id!r}")
        execution = self._active
        finished = execution.state.terminal
        report = ExecutionReport(
            execution_id=execution.execution_id,
            device_id=self.device_id,
            operation_id=execution.operation_id,
            state=execution.state,
            finished=finished,
            failure_reason=execution.failure,
            failure_code=execution.failure,
            detail={
                "kind": "kinematic_transport",
                # 协调器靠 route_state 判定到站/失败; 与 RouteState 取值一致。
                "route_state": ("DONE" if execution.state
                                is ExecutionState.SUCCEEDED else
                                "FAILED" if execution.state
                                is ExecutionState.FAILED else "RUNNING"),
                "route": list(execution.route.nodes),
                "length": round(execution.route.length, 4),
                "elapsed": round(execution.elapsed, 4),
                "duration": round(execution.duration, 4),
                "progress": round(min(1.0, execution.elapsed / execution.duration), 4),
            },
            evidence={"uid": execution.uid},
        )
        if finished:
            self._active = None
            self._last_report = report.as_dict()
        return report

    # ── 协调器需要的只读视图与生命周期 ─────────────────────

    @property
    def spec(self):
        return self._spec

    @property
    def controller(self):
        return _KinematicControllerView(self._spec)

    def release(self, reason: str = "") -> None:
        self._active = None

    def resume(self, *args, **kwargs) -> bool:
        # 运动学车没有"受阻续行"语义: 视为可继续。
        return True

    # ── 位姿 ────────────────────────────────────────────────

    def _default_mover(self, x: float, y: float, yaw: float) -> None:
        if self.sim is None:
            raise ExecutorError("没有 sim 也没有注入 mover")
        if self._handle < 0:
            from .scene_base import resolve_alias

            self._handle = int(resolve_alias(self.sim, "Cart2", required=True))
        self.sim.setObjectPosition(self._handle, [float(x), float(y), 0.0],
                                   self.sim.handle_world)
        self.sim.setObjectOrientation(self._handle, [0.0, 0.0, float(yaw)],
                                      self.sim.handle_world)


# ═══════════════════════════════════════════════════════════════
# 装配
# ═══════════════════════════════════════════════════════════════

def park_cart2(sim) -> bool:
    """把小车二挪到不挡路的停车位 (只改运行时位姿, 不写工件)。

    单车道模式下 AGV 需要进 dock D, 若小车二还停在 D 会把它顶死。
    """
    from .scene_base import resolve_alias

    handle = int(resolve_alias(sim, "Cart2", required=False))
    if handle < 0:
        return False
    x, y, yaw = CART2_PARK_POSE
    sim.setObjectPosition(handle, [float(x), float(y), 0.0],
                          sim.handle_world)
    sim.setObjectOrientation(handle, [0.0, 0.0, float(yaw)],
                             sim.handle_world)
    return True


def build_cart2_runtime(sim, *, device_id: str = "cart_2",
                        home: str = "D") -> VehicleRuntime:
    """在已加载场景里为小车二建运行时槽；车体不存在时给出明确错误。"""
    if sim is None:
        raise ValueError("build_cart2_runtime 需要 sim")
    from .scene_base import resolve_alias

    handle = int(resolve_alias(sim, "Cart2", required=False))
    if handle < 0:
        raise ValueError(
            "场景里没有小车二 (Cart2); 先运行 tools/cart2.py build, "
            "或加载已包含它的工件")
    executor = KinematicTransportExecutor(sim, device_id=device_id)
    return VehicleRuntime(device_id, executor, at_dock=home)


class Cart2VehicleAdapter:
    """把 Cart2 车体包装成 ``Stage4UnitController`` 需要的车辆接口子集。

    控制器只用 ``pose / position / stop / set_cmd / is_stopped / is_upright /
    state``; 小车二是运动学的, 这些操作要么读根位姿, 要么是空操作。
    """

    def __init__(self, sim, *, alias: str = "Cart2") -> None:
        from .pioneer import AGVState

        self.sim = sim
        self.alias = alias
        self.state = AGVState.IDLE
        self.cmd = (0.0, 0.0)
        self._cached_handle = -1

    def _handle(self) -> int:
        # 控制器每步都会问位姿; 句柄必须缓存, 否则每步都会枚举全场
        # (500+ 对象 × 6 类型 = 数千次 RPC), 会把仿真器拖死。
        if self._cached_handle < 0:
            from .scene_base import resolve_alias

            self._cached_handle = int(
                resolve_alias(self.sim, self.alias, required=True))
        return self._cached_handle

    def pose(self) -> list[float]:
        return [float(v) for v in self.sim.getObjectPose(
            self._handle(), self.sim.handle_world)]

    def position(self) -> list[float]:
        return self.pose()[:3]

    def stop(self) -> None:
        self.cmd = (0.0, 0.0)

    def set_cmd(self, left: float = 0.0, right: float = 0.0) -> None:
        self.cmd = (float(left), float(right))

    def is_stopped(self) -> bool:
        return True

    def is_upright(self) -> bool:
        return True


__all__ = [
    "FLEET_DUAL",
    "FLEET_MODES",
    "FLEET_SINGLE",
    "FleetConfig",
    "KinematicTransportExecutor",
    "VehicleRuntime",
    "pose_along_route",
]
