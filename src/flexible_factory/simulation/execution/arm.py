"""execution/arm.py — 机械臂执行适配器 (06-20 步骤 3).

``ArmExecutor`` 包装 :class:`flexible_factory.simulation.unit_controller.
Stage4UnitController`:

  - ``start``: 按设备绑定与工序模板装配控制器 (原协调器
    ``_start_unit_task`` 的构造段), 同一 tick 内返回, 不做任何动作推进;
  - ``pre_tick`` / ``post_tick``: 转发控制器的步前/步后逻辑;
  - ``poll``: 把控制器状态映射为 ``RUNNING`` / ``SUCCEEDED`` / ``FAILED``,
    并附带 ``controller.summary()`` 与安全锁存证据.

五台臂共用同一实现；本模块不区分工位差异, 差异全部来自
``DeviceSpec``、``UNIT_ALIASES`` 与布局。会话资源 (影子臂/碰撞集合/吸盘)
由 ``simulation/runtime.py`` 与 runner 管理, 适配器只引用不创建。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from ...domain.process_model import (DeviceSpec, OperationTemplate,
                                     ProcessCatalog)
from .. import config as cfg
from .. import config_base as base
from ..layout import handoff_park_tcp, line_specs
from ..unit_controller import SinkSpec, Stage4UnitController, TaskLayout
from ..unit_controller_base import UnitTaskState
from .base import (ExecutionReport, ExecutionRequest, ExecutionState,
                   ExecutorError, ensure_device, make_execution_id)


def sink_spec_for(operation: OperationTemplate, unit_id: str,
                  scene: Any, lines: dict) -> SinkSpec:
    """工序输出区域 → 放置汇几何 (台面/线入口/AGV 载台).

    纯几何映射, 不含协调器策略; 未知区域立即拒绝, 避免静默放错位置。
    """
    sink = operation.sink_kind
    dest_zone = operation.output.zone if operation.output else ""
    if sink == "agv":
        unit = scene.unit(unit_id)
        return SinkSpec(
            kind="agv", tcp_pose=tuple(unit.spec.agv_tcp_pose),
            material_pose=tuple(unit.spec.agv_material_pose),
            support_z=base.AGV_TRAY_TOP_Z, zone=dest_zone)
    if sink == "conveyor":
        line = lines[dest_zone]
        pose = line.place_material_pose
        tcp = base.tcp_pose_above(pose)
        return SinkSpec(
            kind="conveyor", tcp_pose=tuple(tcp),
            material_pose=tuple(pose),
            support_z=base.CONVEYOR_SURFACE_Z, zone=dest_zone, line=line)
    if dest_zone == cfg.HANDOFF_AC["id"]:
        pos = cfg.HANDOFF_AC["position"]
        material = (pos[0], pos[1],
                    base.STATION_TOP_Z + base.MATERIAL_HALF_HEIGHT)
        support = base.STATION_TOP_Z
    elif dest_zone in cfg.E_BUFFERS:
        pos = cfg.E_BUFFERS[dest_zone]["position"]
        material = (pos[0], pos[1],
                    base.ISLAND_TOP_Z + base.MATERIAL_HALF_HEIGHT)
        support = base.ISLAND_TOP_Z
    else:
        raise ExecutorError(
            f"未知 station 目标区域: {dest_zone!r} "
            f"(operation={operation.id})")
    return SinkSpec(
        kind="station", tcp_pose=tuple(base.tcp_pose_above(material)),
        material_pose=tuple(material), support_z=support, zone=dest_zone)


@dataclass
class _ArmExecution:
    """一次臂执行的内部记录; 只在本进程存活."""

    execution_id: str
    operation_id: str
    uid: Optional[int]
    controller: Stage4UnitController


class ArmExecutor:
    """一台机械臂的执行适配器; v1 每台臂同一时刻只有一个活动执行."""

    kind = "arm"

    def __init__(self, sim: Any, scene: Any, comp: dict, *,
                 device: DeviceSpec, catalog: ProcessCatalog,
                 resources: Any = None, logger: Any = None) -> None:
        if device.kind != "arm":
            raise ExecutorError(
                f"ArmExecutor 需要 arm 设备, 收到 {device.kind!r}")
        self.sim = sim
        self.scene = scene
        self.comp = comp
        self.device = device
        self.device_id = device.id
        self.unit_id = device.id.removeprefix("arm_")
        self.catalog = catalog
        self.resources = resources
        self.logger = logger
        self.lines = line_specs()
        self._serial = 0
        self._active: Optional[_ArmExecution] = None

    # ── 查询 ────────────────────────────────────────────────

    @property
    def active(self) -> bool:
        return self._active is not None

    @property
    def execution_id(self) -> str:
        return "" if self._active is None else self._active.execution_id

    @property
    def controller(self) -> Optional[Stage4UnitController]:
        return None if self._active is None else self._active.controller

    # ── 生命周期 ────────────────────────────────────────────

    def start(self, request: ExecutionRequest) -> str:
        """装配控制器并登记执行; 不推进仿真, 不阻塞到动作完成."""
        ensure_device(request, self.device_id)
        if self._active is not None:
            raise ExecutorError(
                f"{self.device_id}: 已有活动执行 "
                f"{self._active.execution_id}")
        operation = request.operation
        if operation is None:
            raise ExecutorError(
                f"{self.device_id}: 机械臂执行需要工序上下文")
        controller = self._build_controller(operation, request)
        self._serial += 1
        execution_id = make_execution_id(self.device_id, self._serial)
        self._active = _ArmExecution(
            execution_id=execution_id, operation_id=operation.id,
            uid=request.uid, controller=controller)
        return execution_id

    def pre_tick(self, sim_time: float) -> None:
        active = self._active
        if active is None:
            return
        if not active.controller.finished:
            active.controller.pre_tick()

    def post_tick(self, sim_time: float) -> None:
        active = self._active
        if active is None:
            return
        active.controller.post_tick()

    def poll(self, execution_id: str) -> ExecutionReport:
        active = self._require(execution_id)
        controller = active.controller
        summary = controller.summary()
        detail: dict = {
            "phase": controller.state.name,
            "uid": active.uid,
            "safety_engaged": False,
        }
        evidence: dict = {"controller": summary}
        safety = getattr(controller, "safety", None)
        if getattr(safety, "engaged", False):
            latch = getattr(safety, "latch", None)
            latch_reason = str(getattr(latch, "reason", "") or "unknown")
            if hasattr(latch, "as_dict"):
                evidence["safety"] = latch.as_dict()
            detail.update({"safety_engaged": True,
                           "safety_latch_reason": latch_reason})
            return ExecutionReport(
                execution_id=active.execution_id, device_id=self.device_id,
                operation_id=active.operation_id,
                state=ExecutionState.FAILED, finished=True,
                failure_reason=f"SAFETY_LATCH: {latch_reason}",
                detail=detail, evidence=evidence)
        if controller.state is UnitTaskState.FAILED:
            failure = str(controller.failure_reason or "UNIT_FAILED")
            detail["controller_failure"] = failure
            return ExecutionReport(
                execution_id=active.execution_id, device_id=self.device_id,
                operation_id=active.operation_id,
                state=ExecutionState.FAILED, finished=True,
                failure_reason=failure, detail=detail, evidence=evidence)
        state = (ExecutionState.SUCCEEDED if controller.finished
                 else ExecutionState.RUNNING)
        return ExecutionReport(
            execution_id=active.execution_id, device_id=self.device_id,
            operation_id=active.operation_id, state=state,
            finished=controller.finished, detail=detail, evidence=evidence)

    def release(self, reason: str = "") -> None:
        """结束本次执行登记; 会话资源与资源表由调用方处理."""
        self._active = None

    # ── 内部 ────────────────────────────────────────────────

    def _require(self, execution_id: str) -> _ArmExecution:
        active = self._active
        if active is None or active.execution_id != str(execution_id):
            raise ExecutorError(
                f"{self.device_id}: 执行 {execution_id!r} 未处于活动状态")
        return active

    def _build_controller(self, operation: OperationTemplate,
                          request: ExecutionRequest) -> Stage4UnitController:
        """按工序与设备绑定装配控制器 (原协调器 `_start_unit_task` 构造段)."""
        arm = self.comp["runtime"].arm(self.unit_id)
        unit = self.scene.unit(self.unit_id)
        source = operation.source_kind
        sink = operation.sink_kind
        line_id = self.device.default_line_id
        line = self.lines[line_id]
        sink_spec = sink_spec_for(operation, self.unit_id, self.scene,
                                  self.lines)
        layout_name = getattr(getattr(self.scene, "stage4_layout", None),
                              "name", "L5_UFLOW")
        task_layout = TaskLayout(
            name=f"{layout_name}/{self.unit_id}", sink=sink_spec,
            unit_view=unit.layout,
            target="agv" if sink == "agv" else "station")
        expected_types = tuple(port.item_type
                               for port in operation.inputs)
        return Stage4UnitController(
            sim=self.sim, scene=arm.view,
            materials=self.comp["materials"],
            conveyor=self.comp["conveyor"],
            belt_geometry=line.geometry,
            ik=self.comp["ik"][self.unit_id],
            suction=self.comp["suction"][self.unit_id],
            agv=self.comp["agv"],
            target=("agv" if sink == "agv" else "station"),
            uid=request.uid, fault=cfg.FAULT_NONE, layout=task_layout,
            logger=self.logger, runtime=arm, unit_id=self.unit_id,
            source=source, sink=sink, dock_id=operation.dock,
            resources=self.resources, task=request.task, line=line,
            sink_spec=sink_spec, flow=self.comp["flow"],
            expected_types=expected_types,
            park_tcp_pose=handoff_park_tcp(self.unit_id),
            pick_timeout=cfg.TASK_TIMEOUT)


__all__ = ["ArmExecutor", "sink_spec_for"]
