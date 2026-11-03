"""execution/agv.py — AGV 航线执行适配器 (06-20 步骤 4).

``AgvExecutor`` 包装 :class:`flexible_factory.simulation.agv.
AGVRouteController4` 与 v1 既有路线生命周期:

  - ``start``: 用协调器给出的 route intent (``RouteSpec4``) 构造航线控制器,
    载货与空载 reposition 走同一入口, 由 ``spec.loaded`` 区分;
  - ``pre_tick``: 下发轮速推进 (等价于原 ``_advance_route``), 必须在
    ``sim.step()`` 之前调用;
  - ``poll``: ``DONE`` → ``SUCCEEDED``, ``FAILED`` → ``FAILED``, 其余
    ``RUNNING``; ``BLOCKED`` 作为 ``RUNNING`` 的子状态写在 ``detail`` 中,
    协调器据此保留既有清障/续行分支;
  - ``resume``: 透传航线的续行接口, 不改动清障判据.

航段规划、速度剖面、停靠/航向判据仍在 ``simulation/agv.py``;
本模块不写车体位姿, 不让 recipe 直接下发轮速.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from ..agv import AGVRouteController4, RouteSpec4
from ..agv_route import RouteState
from .base import (ExecutionReport, ExecutionRequest, ExecutionState,
                   ExecutorError, ensure_device, make_execution_id)


@dataclass
class _AgvExecution:
    execution_id: str
    operation_id: str
    uid: Optional[int]
    controller: AGVRouteController4


class AgvExecutor:
    """共享 AGV 的航线执行适配器; 同一时刻只允许一条航线."""

    kind = "agv"

    def __init__(self, sim: Any, agv: Any, layout: Any, *,
                 device_id: str = "agv_1", materials: Any = None,
                 logger: Any = None) -> None:
        self.sim = sim
        self.agv = agv
        self.layout = layout
        self.device_id = device_id
        self.materials = materials
        self.logger = logger
        self._serial = 0
        self._active: Optional[_AgvExecution] = None

    # ── 查询 ────────────────────────────────────────────────

    @property
    def active(self) -> bool:
        return self._active is not None

    @property
    def execution_id(self) -> str:
        return "" if self._active is None else self._active.execution_id

    @property
    def controller(self) -> Optional[AGVRouteController4]:
        return None if self._active is None else self._active.controller

    @property
    def route(self) -> Optional[AGVRouteController4]:
        return self.controller

    @property
    def spec(self) -> Optional[RouteSpec4]:
        controller = self.controller
        return None if controller is None else controller.spec

    @property
    def state(self) -> RouteState:
        controller = self.controller
        return RouteState.IDLE if controller is None else controller.state

    @property
    def blocked(self) -> bool:
        controller = self.controller
        return bool(controller is not None and controller.blocked)

    # ── 生命周期 ────────────────────────────────────────────

    def start(self, request: ExecutionRequest) -> str:
        """构造航线控制器并登记执行; 不推进仿真."""
        ensure_device(request, self.device_id)
        if self._active is not None:
            raise ExecutorError(
                f"{self.device_id}: 已有活动航线 "
                f"{self._active.execution_id}")
        spec = request.route_spec
        if spec is None:
            raise ExecutorError(
                f"{self.device_id}: 航线执行需要 route_spec")
        controller = self._build_controller(spec, request)
        self._serial += 1
        execution_id = make_execution_id(self.device_id, self._serial)
        operation_id = (request.operation.id
                        if request.operation is not None else "")
        self._active = _AgvExecution(
            execution_id=execution_id, operation_id=operation_id,
            uid=request.uid, controller=controller)
        return execution_id

    def pre_tick(self, sim_time: float) -> None:
        controller = self.controller
        if controller is None:
            return
        controller.step(sim_time)

    def post_tick(self, sim_time: float) -> None:
        return None

    def poll(self, execution_id: str) -> ExecutionReport:
        active = self._require(execution_id)
        controller = active.controller
        spec = controller.spec
        route_state = controller.state
        detail: dict = {
            "route_state": route_state.value,
            "route_id": spec.route_id,
            "loaded": bool(spec.loaded),
            "start_dock": spec.start_dock,
            "end_dock": spec.end_dock,
            "distance": round(float(spec.distance), 4),
            "lanes": list(spec.lanes),
        }
        evidence: dict = {"route": controller.summary()}
        if route_state is RouteState.FAILED:
            failure = str(controller.failure_reason or "AGV_ROUTE_FAILED")
            return ExecutionReport(
                execution_id=active.execution_id, device_id=self.device_id,
                operation_id=active.operation_id,
                state=ExecutionState.FAILED, finished=True,
                failure_reason=failure, detail=detail, evidence=evidence)
        if route_state is RouteState.DONE:
            return ExecutionReport(
                execution_id=active.execution_id, device_id=self.device_id,
                operation_id=active.operation_id,
                state=ExecutionState.SUCCEEDED, finished=True,
                detail=detail, evidence=evidence)
        return ExecutionReport(
            execution_id=active.execution_id, device_id=self.device_id,
            operation_id=active.operation_id,
            state=ExecutionState.RUNNING,
            finished=False, detail=detail, evidence=evidence)

    def resume(self, sim_time: float, *, clearance_ok: bool,
               reason: str = "") -> bool:
        """透传航线的续行判定; 清障与资源所有权仍由协调器确认."""
        controller = self.controller
        if controller is None:
            return False
        return bool(controller.resume(sim_time, clearance_ok=clearance_ok,
                                      reason=reason))

    def release(self, reason: str = "") -> None:
        self._active = None

    def summary(self) -> dict:
        controller = self.controller
        return {} if controller is None else controller.summary()

    # ── 内部 ────────────────────────────────────────────────

    def _build_controller(self, spec: RouteSpec4,
                          request: ExecutionRequest) -> AGVRouteController4:
        """构造航线控制器 (原协调器 ``_launch_route`` 构造段)."""
        try:
            return AGVRouteController4(
                self.sim, self.agv, spec,
                material_manager=self.materials,
                uid=request.uid if spec.loaded else None,
                logger=self.logger)
        except Exception as exc:
            raise ExecutorError(
                f"{self.device_id}: 航线构造失败: "
                f"{type(exc).__name__}: {exc}") from exc

    def _require(self, execution_id: str) -> _AgvExecution:
        active = self._active
        if active is None or active.execution_id != str(execution_id):
            raise ExecutorError(
                f"{self.device_id}: 航线 {execution_id!r} 未处于活动状态")
        return active


__all__ = ["AgvExecutor"]
