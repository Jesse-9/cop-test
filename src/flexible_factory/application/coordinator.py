"""coordinator.py — v1 UFLOW 多岛任务编排、AGV 调度、配对与背压 (06-16 §F4-4).

固定单步顺序 (CLAUDE.md §8, Python 始终是 clock master):
  1. 来源生成 (有限批次, 区域未满才生成);
  2. 任务轮询: 单元任务一次性申请资源; AGV 调度按优先级选下一段路线;
  3. 故障注入 (AGV 阻塞 / 传送带停带);
  4. 所有活动执行适配器 `pre_tick()` (转发单元控制器);
  5. AGV 航线 `pre_tick()` (轮速);
  6. `sim.step()` — 每步只推进一次, 协调器是唯一步进点;
  7. 各适配器 `post_tick()` (步后联锁);
  8. `poll()` 读取执行状态, 再做任务完成校验、物料区域/类型提交、
     E 岛配对、资源释放、指标事件.

阶段 2 起, 控制器与航线的装配/步进/状态映射由
`simulation.execution` 的 `ArmExecutor` / `AgvExecutor` 承担; 本模块保留
资源申请、队列状态、应用层验证、提交与冻结. 适配器不调用 `sim.step()`.

任务链 (06-16 §1):
    S1→A ─► A ─固定交接─► C ─C→E─► E ┐
    S1→B ─► B ─AGV B→A─► A ────────┤
    S2→D ─► D ─AGV D→E──────────────┴─► E 配对 → FINAL

v1 不自动重试/续作; 安全锁存、航线失败或传送带停带故障后冻结现场,
任务不得改写成 DONE. 统一复位仍属最终故障治理阶段.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping, Optional

from .. import spec

from ..simulation import config_base as base
from ..simulation import physics as phys
from ..simulation.material_manager import MaterialState
from ..simulation.scene_base import alias_of

from ..simulation.agv_route import RouteState

from ..simulation import config as cfg
from ..simulation.agv import AGVRouteController4, make_route_spec, pose_yaw
from ..simulation.execution import (AgvExecutor, ArmExecutor,
                                    ExecutionRequest, ExecutionState,
                                    ExecutorError, sink_spec_for)
from ..domain.default_process import DEFAULT_RECIPE_ID, default_catalog
from ..domain.failures import (FAILURE_POLICY_VERSION, FailureScope,
                               classify_failure, make_failure_record)
from ..simulation.layout import line_specs
from ..domain.materials import (ItemType, MaterialFlowError, MaterialRegistry,
                                RecordState)
from ..domain.operation_runtime import (OperationRuntime,
                                        OperationRuntimeError)
from ..domain.order_compiler import CompiledOrder
from ..domain.process_model import OperationTemplate, ProcessCatalog
from ..domain.resources import ResourceTable
from ..domain.scheduling import (SCHEDULER_POLICY_VERSION, DeviceView,
                                 MaterialView, OperationView,
                                 SchedulingSnapshot, plan_ready)
from ..simulation.scene import Stage4Scene
from ..domain.tasks import (Stage4Task, TaskQueue, TaskStatus,
                            make_task_from_operation)
from ..simulation.device_binding import DeviceBindingError, binding_for
from ..simulation.unit_controller import (SinkSpec, Stage4UnitController,
                                      agv_material_position)
from .batch import expected_flow_counts, validate_batch


_SOURCE_ITEMS_BY_ZONE = {
    item["line"]: item for item in cfg.SOURCE_ITEMS.values()}


class CoordinatorError(RuntimeError):
    code = "STAGE4_COORDINATOR"


@dataclass
class ControllerRecord:
    """一个在跑的臂执行: 适配器 + 执行 ID, 控制器通过适配器访问."""

    unit_id: str
    task_id: str
    executor: ArmExecutor
    execution_id: str

    @property
    def controller(self) -> Stage4UnitController:
        controller = self.executor.controller
        if controller is None:
            raise CoordinatorError(
                f"{self.unit_id}: 执行 {self.execution_id} 已释放")
        return controller


class Stage4Coordinator:
    """阶段四唯一调度器; 不直接写车体/关节位姿."""

    def __init__(self, sim, scene: Stage4Scene, comp, *,
                 scenario: str = cfg.SCENARIO_FULL_FLOW,
                 fault: str = cfg.FAULT_NONE,
                 logger=None, on_controller=None,
                 catalog: Optional[ProcessCatalog] = None,
                 batch: Optional[Mapping[str, int]] = None,
                 order_plan: Optional[CompiledOrder] = None) -> None:
        if scenario not in cfg.STAGE4_SCENARIOS:
            raise CoordinatorError(f"未知 scenario: {scenario!r}")
        # 批次只影响本次运行的来源数量; 显式输入在创建任何任务前校验,
        # 缺省时严格沿用 scenario 默认批次. 订单模式不使用固定批次.
        self._batch = (dict(cfg.SCENARIO_BATCH[scenario])
                       if batch is None else validate_batch(batch))
        self.catalog = catalog if catalog is not None else default_catalog()
        # 启动前完整校验: 未知设备/zone/资源、重复 ID、缺失端口、无效后继、
        # 循环依赖均在此拒绝, 错误信息带 recipe/operation 定位.
        self.process_report = self.catalog.validate()
        self.recipe = self.catalog.recipe(DEFAULT_RECIPE_ID)
        self.order_plan = order_plan
        self.order = None if order_plan is None else order_plan.context
        if order_plan is not None:
            self._batch = {}
        self.sim = sim
        self.scene = scene
        self.comp = comp
        self.materials = comp["materials"]
        self.flow: MaterialRegistry = comp["flow"]
        self.agv = comp["agv"]
        self.runtime = comp["runtime"]
        self.ik = comp["ik"]
        self.suction = comp["suction"]
        self.conveyor = comp["conveyor"]
        self.layout = scene.stage4_layout
        self.lines = line_specs()
        self.logger = logger
        self.on_controller = on_controller
        self.scenario = scenario
        self.fault = fault

        self.queue = TaskQueue(catalog=self.catalog)
        self.resources = ResourceTable()
        # 阶段 3: 配方运行图 hold 工序实例与依赖; 任务队列只承接已 ready 的
        # 实例, 不再由模板 successor 静态推进. 订单模式装载编译后的实例图.
        self.op_runtime = OperationRuntime(
            self.catalog, self.recipe, order=self.order)
        if order_plan is not None:
            self.op_runtime.load_order_instances(order_plan.instances)
            self._order_inputs = tuple(
                plan for plan in order_plan.material_plans
                if plan.kind == "input")
            self._order_spawned: set[int] = set()
            if order_plan.material_plans:
                self._next_uid = max(
                    int(plan.uid) for plan in order_plan.material_plans) + 1
        else:
            self._order_inputs = ()
            self._order_spawned = set()
        # 设备执行适配器: 每台臂一个, AGV 一个; 只包装, 不改调度策略.
        self.arm_executors = {
            device.id: ArmExecutor(self.sim, scene, comp, device=device,
                                   catalog=self.catalog,
                                   resources=self.resources, logger=logger)
            for device in self.catalog.devices if device.kind == "arm"}
        self.agv_executor = AgvExecutor(self.sim, self.agv, self.layout,
                                        materials=self.materials,
                                        logger=logger)
        self.controllers: dict[str, ControllerRecord] = {}
        self.controller_history: list[dict] = []
        self.route_task: Optional[Stage4Task] = None
        self.route_execution_id = ""
        self.agv_at_dock = cfg.AGV_INITIAL_DOCK
        self.agv_history: list[dict] = []
        self.dispatch_decisions: list[dict] = []
        # 阶段 4: 调度决策与拒绝/等待原因; 决策记录在设备选定时生成,
        # 执行 ID 在 executor.start 后回填.
        self.schedule_decisions: list[dict] = []
        self.schedule_waits: list[dict] = []
        self._schedule_wait_reason: dict[str, str] = {}
        self._device_view_cache: Optional[list[DeviceView]] = None
        self.events: list[dict] = []
        self.injected: list[dict] = []
        self.spawned: list[dict] = []
        self.failures: list[dict] = []
        self.final_count = 0
        self.aborted = False
        self.frozen = False
        self.stop_reason = ""
        self._sim_failure = ""
        self.started_at: Optional[float] = None
        self._abort_settle_steps = 0
        self._frozen_since: Optional[float] = None
        self._fault_handles: list[int] = []
        self._block_since: Optional[float] = None
        self._line_faulted = False
        self._unstable_steps = 0
        self._next_uid = 1
        self._source_entries = {entry.id: entry
                                for entry in self.recipe.entries}
        self._source_keys = tuple(self._source_entries)
        self._unit_templates = tuple(
            template for template in self.recipe.operations
            if self.catalog.device(
                template.candidate_devices[0]).kind == "arm")
        self._source_count = {key: 0 for key in self._source_keys}
        self._source_last_spawn = {key: None for key in self._source_keys}
        self._wait_reason: dict[str, str] = {}
        self._evidence_seq = 0

    # ═══════════════════════════════════════════════════════
    # 初始化
    # ═══════════════════════════════════════════════════════

    @property
    def route(self) -> Optional[AGVRouteController4]:
        """当前活动航线控制器的只读视图; 生命周期由 AgvExecutor 管理."""
        return self.agv_executor.route

    def start(self, sim_time: float = 0.0) -> None:
        self.started_at = float(sim_time)
        self._event("COORDINATOR_START", detail={
            "scenario": self.scenario, "fault": self.fault,
            "batch": dict(self._batch),
            "batch_input_version": spec.BATCH_INPUT_VERSION})

    # ═══════════════════════════════════════════════════════
    # 单步
    # ═══════════════════════════════════════════════════════

    def step(self) -> None:
        sim_time = self._sim_time()
        if self.started_at is None:
            self.started_at = sim_time
        if self.aborted or self.frozen:
            self._settle_step()
            return
        try:
            self._poll_sources(sim_time)
            self._poll_tasks(sim_time)
            self._maybe_inject_fault(sim_time)

            for record in list(self.controllers.values()):
                record.executor.pre_tick(sim_time)

            self._advance_route(sim_time)

            try:
                self.sim.step()
            except Exception as exc:
                self._sim_failure = f"{type(exc).__name__}: {exc}"
                self._global_failure(
                    "SIM_STEP_FAILED",
                    f"SIM_STEP_FAILED: {self._sim_failure}", sim_time,
                    evidence={"sim_time": sim_time})
                return

            for record in list(self.controllers.values()):
                record.executor.post_tick(sim_time)

            self._after_step(sim_time)
            self._timeout_check(sim_time)
        except Exception as exc:
            self._global_failure(
                "UNKNOWN_FAILURE",
                f"UNKNOWN_FAILURE: {type(exc).__name__}: {exc}", sim_time,
                evidence={"phase": "coordinator_step"})

    def _settle_step(self) -> None:
        sim_time = self._sim_time()
        if not self._sim_failure:
            for record in list(self.controllers.values()):
                record.executor.post_tick(sim_time)
            try:
                self.sim.step()
            except Exception as exc:
                self._sim_failure = f"{type(exc).__name__}: {exc}"
        self._abort_settle_steps += 1

    # ═══════════════════════════════════════════════════════
    # 来源生成 (有限批次)
    # ═══════════════════════════════════════════════════════

    def _poll_sources(self, sim_time: float) -> None:
        if self.order is not None:
            self._poll_order_sources(sim_time)
            return
        for key in self._source_keys:
            entry = self._source_entries[key]
            planned = int(self._batch.get(entry.item_type.value, 0))
            if self._source_count[key] >= planned:
                continue
            last = self._source_last_spawn[key]
            if last is not None and \
                    sim_time - last < cfg.SOURCE_SPAWN_INTERVAL_S:
                continue
            zone = entry.line
            if not self.flow.zone_free(zone):
                self._event("SOURCE_BACKPRESSURE", zone=zone,
                            detail={"source": key,
                                    "reason": cfg.BACKPRESSURE_REASONS.get(
                                        zone, "区域占用")})
                continue
            self._spawn_source(key, sim_time)

    def _poll_order_sources(self, sim_time: float) -> None:
        """按编译器的物料计划生成订单入口来料; UID 已在编译期确定."""
        for plan in self._order_inputs:
            if int(plan.uid) in self._order_spawned:
                continue
            zone = plan.zone
            if not self.flow.zone_free(zone):
                self._event("SOURCE_BACKPRESSURE", zone=zone,
                            detail={"input_id": plan.input_id,
                                    "reason": cfg.BACKPRESSURE_REASONS.get(
                                        zone, "区域占用")})
                continue
            source = _SOURCE_ITEMS_BY_ZONE.get(zone)
            if source is None:
                self._global_failure(
                    "SCENE_INTEGRITY_LOST",
                    f"订单输入区域 {zone!r} 没有已登记生成位置",
                    sim_time)
                return
            item_type = ItemType(plan.item_type)
            alias = f"Part_{int(plan.uid):04d}"
            try:
                handle = self.scene.spawn_material(
                    plan.uid, alias, position=source["position"])
            except Exception as exc:
                self._global_failure(
                    "SCENE_INTEGRITY_LOST",
                    f"ORDER_SOURCE_SPAWN_FAILED: {type(exc).__name__}: "
                    f"{exc}", sim_time)
                return
            self._set_material_color(handle, item_type)
            self.materials.register(plan.uid, alias, sim_handle=handle)
            self.flow.register(
                plan.uid, item_type, alias, sim_handle=handle,
                source=zone, destination="", zone=zone,
                sim_time=sim_time)
            self._order_spawned.add(int(plan.uid))
            self.spawned.append({
                "sim_time": round(sim_time, 4), "source": plan.input_id,
                "uid": int(plan.uid), "item_type": item_type.value,
                "position": list(source["position"]),
                "handle": int(handle), "node_id": plan.node_id})
            self._event(
                "MATERIAL_SPAWN", uid=int(plan.uid),
                item_type=item_type.value, zone=zone, source=zone,
                node_id=plan.node_id,
                detail={"alias": alias, "handle": int(handle),
                        "input_id": plan.input_id,
                        "order_id": self.order.order_id
                        if self.order is not None else "",
                        "spawn": source["position"]})

    def _spawn_source(self, key: str, sim_time: float) -> None:
        entry = self._source_entries[key]
        spec = cfg.SOURCE_ITEMS[key]
        item_type = ItemType(entry.item_type.value)
        uid = self._next_uid
        self._next_uid += 1
        alias = f"Part_{uid:04d}"
        try:
            handle = self.scene.spawn_material(
                uid, alias, position=spec["position"])
        except Exception as exc:
            self._event("SOURCE_SPAWN_FAILED", detail={"source": key,
                                                       "error": str(exc)})
            self._freeze(f"SOURCE_SPAWN_FAILED: {exc}", sim_time)
            return
        self._set_material_color(handle, item_type)
        self.materials.register(uid, alias, sim_handle=handle)
        self.flow.register(
            uid, item_type, alias, sim_handle=handle, source=entry.line,
            destination="", zone=entry.line, sim_time=sim_time)
        self._source_count[key] += 1
        self._source_last_spawn[key] = sim_time
        self.spawned.append({
            "sim_time": round(sim_time, 4), "source": key, "uid": uid,
            "item_type": item_type.value, "position": list(spec["position"]),
            "handle": int(handle)})
        self._event("MATERIAL_SPAWN", uid=uid, item_type=item_type.value,
                    zone=entry.line, source=set_name(key),
                    detail={"alias": alias, "handle": int(handle),
                            "spawn": spec["position"]})
        # 按入口固定的 route variant 实例化该 UID 的工序链; 首个 ready
        # 实例由下一次 _dispatch_ready() 进入任务队列.
        try:
            instances = self.op_runtime.instantiate_entry(
                entry.id, uid, item_type=item_type.value, sim_time=sim_time)
        except OperationRuntimeError as exc:
            self._event("OPERATION_GRAPH_FAILED", uid=uid,
                        detail={"source": key, "error": str(exc)})
            self._freeze(f"OPERATION_GRAPH_FAILED: {exc}", sim_time)
            return
        self._event("OPERATION_GRAPH", uid=uid, item_type=item_type.value,
                    zone=entry.line,
                    evidence_id=instances[0].run_id,
                    detail={"run_id": instances[0].run_id,
                            "recipe": self.recipe.id,
                            "route": instances[0].selected_route,
                            "operations": [row.template_id
                                           for row in instances]})

    def _set_material_color(self, handle: int, item_type: ItemType) -> None:
        color = cfg.ITEM_COLORS[item_type.value]
        try:
            self.sim.setObjectColor(
                handle, 0, self.sim.colorcomponent_ambient_diffuse,
                list(color))
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════
    # 任务轮询
    # ═══════════════════════════════════════════════════════

    def _poll_tasks(self, sim_time: float) -> None:
        if self.aborted or self.frozen:
            return
        self._dispatch_ready(sim_time)
        for unit_id in cfg.UNIT_IDS:
            self._poll_unit(unit_id, sim_time)
        self._poll_agv(sim_time)

    def _dispatch_ready(self, sim_time: float) -> None:
        """用阶段 4 纯策略为 ready 实例选设备并投影为任务.

        依赖未满足的实例留在图中, 不提前进入队列标成 WAITING; 逻辑合流
        由 :meth:`_check_join` 提交账本, 不参与臂/AGV 派工. 策略只做静态/
        物料筛选与选择记录; 资源申请仍由 :meth:`_poll_unit` /
        :meth:`_start_transport` 全量完成.
        """
        ready = [instance for instance in self.op_runtime.ready()
                 if self._template(
                     instance.template_id).executor_kind != "logic"]
        if not ready:
            return
        snapshot = self._scheduling_snapshot(sim_time, ready)
        result = plan_ready(snapshot)
        for decision in result.decisions:
            instance = self.op_runtime.instance(
                decision.operation_instance_id)
            if instance is None:
                continue
            template = self._template(instance.template_id)
            record = self._decision_record(decision, instance)
            if not decision.selected_device_id:
                previous = self._schedule_wait_reason.get(
                    instance.instance_id)
                self._schedule_wait_reason[instance.instance_id] = \
                    decision.defer_reason
                if previous != decision.defer_reason:
                    self.schedule_waits.append(record)
                    self._event("SCHEDULE_WAIT", task_id=instance.task_id,
                                uid=instance.uid,
                                detail={"reason": decision.defer_reason,
                                        "decision": record})
                continue
            self._schedule_wait_reason.pop(instance.instance_id, None)
            try:
                self.op_runtime.claim_instance(instance.instance_id,
                                               sim_time)
                self.op_runtime.select_device(
                    instance.instance_id, decision.selected_device_id)
            except OperationRuntimeError as exc:
                self._event("SCHEDULE_REJECTED", task_id=instance.task_id,
                            uid=instance.uid,
                            detail={"reason": str(exc),
                                    "decision": record})
                self._freeze(f"SCHEDULE_REJECTED: {exc}", sim_time)
                return
            task = make_task_from_operation(
                template, instance.uid, instance=instance, sim_time=sim_time)
            if not self._submit(task, sim_time):
                continue
            self.schedule_decisions.append(record)
            self._event("SCHEDULE_DECISION", task_id=task.task_id,
                        uid=task.uid, device=decision.selected_device_id,
                        detail=record)

    # ── 调度快照 ────────────────────────────────────────────

    def _scheduling_snapshot(self, sim_time: float,
                             ready: list) -> SchedulingSnapshot:
        operations = tuple(self._operation_view(instance)
                           for instance in ready)
        devices = tuple(self._device_views())
        materials = []
        free_zones: dict[str, bool] = {}
        seen_uids: set[int] = set()
        for operation in operations:
            if operation.uid not in seen_uids:
                seen_uids.add(operation.uid)
                record = self.flow.by_uid(operation.uid)
                if record is not None:
                    materials.append(MaterialView(
                        uid=int(operation.uid),
                        item_type=record.item_type.value,
                        zone=record.zone))
            if operation.destination_zone:
                free_zones[operation.destination_zone] = bool(
                    self.flow.zone_free(operation.destination_zone))
        return SchedulingSnapshot(
            sim_time=float(sim_time), operations=operations,
            devices=devices, materials=tuple(materials),
            free_zones=free_zones,
            resource_owners={name: owner for name, owner
                             in self.resources.owners.items() if owner},
            busy_devices=self._busy_devices(),
            agv_at_dock=self.agv_at_dock,
            agv_dock_settled=self._agv_dock_settled())

    def _operation_view(self, instance) -> OperationView:
        template = self._template(instance.template_id)
        return OperationView(
            instance_id=instance.instance_id,
            task_id=instance.task_id,
            template_id=template.id,
            kind=template.kind,
            uid=int(instance.uid),
            executor_kind=template.executor_kind,
            capability=template.capability,
            candidate_device_ids=tuple(
                instance.candidate_device_ids
                or template.candidate_devices),
            run_id=instance.run_id,
            entry_id=instance.entry_id,
            priority=int(instance.priority),
            ready_sim_time=self._ready_sim_time(instance),
            input_zones=tuple(port.zone for port in template.inputs),
            input_item_types=tuple(port.item_type.value
                                   for port in template.inputs),
            destination_zone=(template.output.zone
                              if template.output else ""),
            source_kind=template.source_kind,
            sink_kind=template.sink_kind,
            dock=template.dock,
            route_origin_dock=template.route_origin_dock,
            route_destination_dock=template.route_destination_dock,
            required_resources=tuple(template.resources),
            selected_route=instance.selected_route)

    def _ready_sim_time(self, instance) -> float:
        """实例变为 ready 的时间: 前驱全部提交时刻, 入口用创建时间."""
        times = []
        for predecessor_id in self.op_runtime.graph.predecessors(
                instance.instance_id):
            predecessor = self.op_runtime.graph.get(predecessor_id)
            if predecessor is None:
                continue
            if predecessor.finished_sim_time is not None:
                times.append(float(predecessor.finished_sim_time))
            elif predecessor.committed_sim_time is not None:
                times.append(float(predecessor.committed_sim_time))
        if times:
            return max(times)
        return float(instance.created_sim_time)

    def _device_views(self) -> list[DeviceView]:
        if self._device_view_cache is not None:
            return list(self._device_view_cache)
        views: list[DeviceView] = []
        for device in self.catalog.devices:
            try:
                binding = binding_for(device.id)
            except DeviceBindingError:
                views.append(DeviceView(
                    device_id=device.id, kind=device.kind,
                    capabilities=tuple(device.capabilities),
                    default_line_id=device.default_line_id, bound=False))
                continue
            views.append(DeviceView(
                device_id=device.id, kind=device.kind,
                capabilities=tuple(device.capabilities),
                resources=tuple(binding.resources),
                zones=tuple(binding.zones),
                default_line_id=device.default_line_id, bound=True))
        self._device_view_cache = list(views)
        return views

    def _busy_devices(self) -> dict[str, str]:
        busy: dict[str, str] = {}
        for record in self.controllers.values():
            busy[record.executor.device_id] = \
                f"execution/{record.execution_id}"
        try:
            if self.agv_executor.active:
                busy[self.agv_executor.device_id] = (
                    f"execution/{self.agv_executor.execution_id or 'route'}")
        except Exception:
            pass
        return busy

    def _agv_dock_settled(self) -> bool:
        try:
            return bool(self._agv_at_dock(self.agv_at_dock))
        except Exception:
            return False

    def _decision_record(self, decision, instance) -> dict:
        record = decision.as_dict()
        record["execution_id"] = ""
        record["instance_id"] = instance.instance_id
        return record

    def _attach_execution(self, instance_id: str,
                          execution_id: str) -> None:
        for row in reversed(self.schedule_decisions):
            if row.get("operation_instance_id") == instance_id:
                row["execution_id"] = str(execution_id)
                return

    def _note_resource_result(self, task: Stage4Task, *,
                              acquired: bool, resources=(),
                              busy: str = "", owner: str = "") -> None:
        """把 ``acquire_all()`` 的结果回填到派工决策 (同一 dict 也是事件 detail)."""
        if not task.runtime_backed:
            return
        for row in reversed(self.schedule_decisions):
            if row.get("operation_instance_id") != task.instance_id:
                continue
            row["resource_result"] = ("ACQUIRED" if acquired else "BUSY")
            if resources:
                row["acquired_resources"] = [str(name) for name in resources]
            if busy:
                row["resource_busy"] = str(busy)
            if owner:
                row["resource_owner"] = str(owner)
            return

    # ═══════════════════════════════════════════════════════
    # 运行图状态迁移 (实例是唯一状态所有者)
    # ═══════════════════════════════════════════════════════

    def _mark_started(self, task: Stage4Task, sim_time: float,
                      execution_id: str = "") -> None:
        """配方工序经 runtime 启动; 空载调机仍走队列."""
        if task.runtime_backed:
            self.op_runtime.mark_started(
                task.instance_id, sim_time, execution_id=execution_id)
        else:
            self.queue.start(task.task_id, sim_time)

    def _commit_operation(self, task: Stage4Task, sim_time: float,
                          evidence: Optional[dict] = None) -> None:
        """成功唯一边界: 执行完成 + 应用层验证 + 物料账本都成功后才 DONE."""
        if task.runtime_backed:
            self.op_runtime.commit_success(
                task.instance_id, output_uids=[task.uid], sim_time=sim_time,
                evidence=evidence)
        else:
            self.queue.done(task.task_id, sim_time, evidence)

    def _fail_operation(self, task: Stage4Task, sim_time: float, reason: str,
                        evidence: Optional[dict] = None, *,
                        failure_code: str = "") -> None:
        if task.runtime_backed:
            self.op_runtime.mark_failed(
                task.instance_id, reason, sim_time,
                failure_code=failure_code, evidence=evidence)
        else:
            self.queue.fail(task.task_id, sim_time, reason, evidence)

    def _operation(self, task: Stage4Task) -> OperationTemplate:
        """任务 → 默认工艺模板; 调机等动态任务没有模板."""
        if task.operation.template_id:
            return self._template(task.operation.template_id)
        return self.catalog.operation_for_kind(task.kind)

    def _template(self, template_id: str) -> OperationTemplate:
        """模板查找; 订单模式按全局唯一 ID, 配方模式限制在当前 recipe."""
        if self.order is not None:
            return self.catalog.operation(template_id)
        return self.catalog.operation(template_id, self.recipe.id)

    def _operation_or_none(
            self, task: Stage4Task) -> Optional[OperationTemplate]:
        try:
            return self._operation(task)
        except Exception:
            return None

    def _task_consumes_line(self, task: Stage4Task, line_id: str) -> bool:
        template = self._operation_or_none(task)
        return template is not None and any(
            port.zone == line_id for port in template.inputs)

    def _poll_unit(self, unit_id: str, sim_time: float) -> None:
        if unit_id in self.controllers:
            return
        device_id = f"arm_{unit_id}"
        kinds: list[str] = []
        priorities: dict[str, int] = {}
        for template in self.catalog.operations_for_device(device_id):
            if template.kind not in kinds:
                kinds.append(template.kind)
            priorities[template.kind] = int(template.priority)
        candidates: list[Stage4Task] = []
        for kind in kinds:
            candidates.extend([
                task for task in self.queue.by_kind(kind)
                if task.status in (TaskStatus.PENDING, TaskStatus.WAITING)])
        candidates.sort(key=lambda task: (
            priorities.get(task.kind, 9),
            task.created_sim_time, task.task_id))
        for task in candidates:
            # 阶段 4: 只由调度器选定本单元的任务在此启动; 未选定的实例
            # 留在运行图 ready 集合, 不提前占资源.
            if task.runtime_backed and \
                    task.selected_device_id != device_id:
                continue
            template = self._operation(task)
            if (self._line_faulted
                    and self._task_consumes_line(
                        task, cfg.CONVEYOR_FAULT_LINE)):
                self._note_wait(task, sim_time, "line_ce 故障停带")
                continue
            ok, reason = self._preconditions(task)
            if not ok:
                self._note_wait(task, sim_time, reason)
                continue
            owner = self._owner(task)
            acquired, busy = self.resources.acquire_all(
                template.resources, owner, task.task_id, sim_time,
                f"{task.kind} start")
            if not acquired:
                self._note_resource_result(
                    task, acquired=False, busy=busy,
                    owner=self.resources.owner(busy) or "")
                self._note_wait(task, sim_time,
                                f"资源等待: {busy}")
                continue
            self._note_resource_result(
                task, acquired=True, resources=template.resources)
            self._record_resources(task, template.resources)
            self._start_unit_task(task, unit_id, sim_time)
            return

    def _preconditions(self, task: Stage4Task) -> tuple[bool, str]:
        template = self._operation(task)
        flow = self.flow.by_uid(task.uid)
        if flow is None:
            return False, "FLOW_RECORD_MISSING"
        input_types = tuple(port.item_type for port in template.inputs)
        if flow.item_type not in input_types:
            return False, (f"ITEM_TYPE_MISMATCH: {flow.item_type.value} "
                           f"not in "
                           f"{[item.value for item in input_types]}")
        expected_zone = template.inputs[0].zone
        if flow.zone != expected_zone:
            return False, (f"ZONE_MISMATCH: uid={task.uid} 在 {flow.zone} "
                           f"而非 {expected_zone}")
        dest_zone = template.output.zone if template.output else ""
        if dest_zone and not self.flow.zone_free(dest_zone):
            return False, cfg.BACKPRESSURE_REASONS.get(
                dest_zone, f"{dest_zone} 占用")
        if template.source_kind == "agv" or template.sink_kind == "agv":
            dock = template.dock
            if self.agv_at_dock != dock:
                return False, f"AGV 不在 dock {dock}"
            if not self._agv_at_dock(dock):
                return False, f"等待 AGV 在 dock {dock} 停稳"
        rec = self.materials.by_uid(task.uid)
        if rec is None or rec.sim_handle is None:
            return False, "UID_NOT_REGISTERED"
        return True, ""

    def _source_zone(self, task: Stage4Task) -> str:
        return self._operation(task).inputs[0].zone

    def _destination_zone(self, task: Stage4Task) -> str:
        template = self._operation(task)
        return template.output.zone if template.output else ""

    def _start_unit_task(self, task: Stage4Task, unit_id: str,
                         sim_time: float) -> None:
        """组装执行请求并交给 ArmExecutor; 协调器只记账与派发."""
        template = self._operation(task)
        source = template.source_kind
        sink = template.sink_kind
        selected = task.operation.selected_device_id
        device = self.catalog.device(
            selected or template.candidate_devices[0])
        if device.id != f"arm_{unit_id}":
            self._release_task(task, sim_time, "device mismatch")
            self._fail_operation(task, sim_time,
                                 f"DEVICE_MISMATCH: 任务选定 {device.id!r}, "
                                 f"单元 {unit_id!r}")
            self._freeze(f"{unit_id} 派工设备不一致: {device.id!r}",
                         sim_time, abort=True)
            return
        line_id = device.default_line_id
        executor = self.arm_executors.get(device.id)
        if executor is None:
            self._release_task(task, sim_time, "controller build failed")
            self._fail_operation(task, sim_time,
                                 f"CONTROLLER_BUILD_FAILED: "
                                 f"未登记设备 {device.id!r}")
            self._freeze(f"{unit_id} 控制器构建失败: 未登记设备 "
                         f"{device.id!r}", sim_time, abort=True)
            return
        try:
            execution_id = executor.start(ExecutionRequest(
                operation=template, task=task, device_id=device.id,
                uid=task.uid, task_id=task.task_id, sim_time=sim_time))
        except Exception as exc:
            self._release_task(task, sim_time, "controller build failed")
            self._fail_operation(task, sim_time,
                                 f"CONTROLLER_BUILD_FAILED: "
                                 f"{type(exc).__name__}: {exc}")
            self._freeze(f"{unit_id} 控制器构建失败: {exc}", sim_time,
                         abort=True)
            return
        self._mark_started(task, sim_time, execution_id)
        self._attach_execution(task.instance_id, execution_id)
        record = ControllerRecord(
            unit_id=unit_id, task_id=task.task_id, executor=executor,
            execution_id=execution_id)
        self.controllers[unit_id] = record
        self._set_phase(task, record.controller.state.name)
        self._event("TASK_START", task_id=task.task_id, uid=task.uid,
                    item_type=(self.flow.by_uid(task.uid).item_type.value
                               if self.flow.by_uid(task.uid) else ""),
                    zone=template.inputs[0].zone,
                    device=f"arm_{unit_id}",
                    detail={"kind": task.kind,
                            "operation": template.id,
                            "source": source, "sink": sink,
                            "line": line_id,
                            "execution_id": execution_id,
                            "resources": list(task.resources)})
        if self.on_controller is not None:
            try:
                self.on_controller(unit_id, record.controller)
            except Exception:
                pass

    def _sink_spec(self, template: OperationTemplate,
                   unit_id: str) -> SinkSpec:
        """放置汇几何委托给执行适配层, 保留方法供既有调用方/测试使用."""
        try:
            return sink_spec_for(template, unit_id, self.scene, self.lines)
        except ExecutorError as exc:
            raise CoordinatorError(str(exc)) from exc

    def _note_wait(self, task: Stage4Task, sim_time: float,
                   reason: str) -> None:
        if task.runtime_backed:
            previous = task.phase
            self.op_runtime.mark_waiting(
                task.instance_id, sim_time, reason)
            if previous != task.phase:
                self._event("TASK_WAIT", task_id=task.task_id,
                            uid=task.uid, detail={"reason": reason})
            self._wait_reason[task.task_id] = reason
            return
        if task.status is TaskStatus.PENDING:
            self.queue.wait(task.task_id, sim_time, reason)
            self._event("TASK_WAIT", task_id=task.task_id, uid=task.uid,
                        detail={"reason": reason})
            self._wait_reason[task.task_id] = reason
        elif task.status is TaskStatus.WAITING:
            if self._wait_reason.get(task.task_id) != reason:
                task.evidence.setdefault("waits", []).append({
                    "sim_time": round(float(sim_time), 4), "reason": reason})
                self._event("TASK_WAIT", task_id=task.task_id, uid=task.uid,
                            detail={"reason": reason})
                self._wait_reason[task.task_id] = reason
            task.phase = f"WAITING: {reason}"

    def _set_phase(self, task: Stage4Task, phase: str) -> None:
        if task.runtime_backed:
            self.op_runtime.record_phase(task.instance_id, phase)
        else:
            task.phase = str(phase)

    def _record_resources(self, task: Stage4Task,
                          resources) -> None:
        if task.runtime_backed:
            self.op_runtime.record_resources(task.instance_id, resources)
        else:
            task.resources = list(resources)

    def _record_upgraded(self, task: Stage4Task, upgraded: bool) -> None:
        if task.runtime_backed:
            self.op_runtime.record_upgraded(task.instance_id, upgraded)
        else:
            task.upgraded = bool(upgraded)

    def _block_task(self, task: Stage4Task, sim_time: float,
                    reason: str) -> bool:
        """阻塞任务; runtime-backed 走 OperationRuntime 窄接口."""
        if task.runtime_backed:
            if task.status is TaskStatus.BLOCKED:
                return False
            self.op_runtime.mark_blocked(task.instance_id, sim_time, reason)
            return True
        if task.status is TaskStatus.BLOCKED:
            return False
        self.queue.block(task.task_id, sim_time, reason)
        return True

    def _unblock_task(self, task: Stage4Task, sim_time: float,
                      reason: str) -> None:
        if task.runtime_backed:
            self.op_runtime.mark_resumed(task.instance_id, sim_time, reason)
        else:
            self.queue.unblock(task.task_id, sim_time, reason)

    def _fail_task(self, task: Stage4Task, sim_time: float,
                   reason: str) -> None:
        if task.runtime_backed:
            self.op_runtime.mark_failed(task.instance_id, reason, sim_time)
        else:
            self.queue.fail(task.task_id, sim_time, reason)

    # ═══════════════════════════════════════════════════════
    # AGV 调度
    # ═══════════════════════════════════════════════════════

    def _poll_agv(self, sim_time: float) -> None:
        if self.agv_executor.active:
            return
        # 任一单元正在本 dock 装卸时, 车辆必须保持停靠.
        for record in self.controllers.values():
            controller = record.controller
            if controller.sink == "agv" or controller.source == "agv":
                if controller.dock_id == self.agv_at_dock:
                    return
        requests = self._agv_move_requests(sim_time)
        if not requests:
            return
        requests.sort(key=lambda row: (row["priority"],
                                       row["created"], row["uid"]))
        top = requests[0]
        dock = top["dock"]
        if self.agv_at_dock == dock:
            if top["kind"] == "transport":
                if self._start_transport(top["transport"], dock,
                                         top["destination"], sim_time):
                    return
                self._note_wait(top["task"], sim_time,
                                "AGV 通道/dock 资源等待")
            # 最高优先级请求就在当前 dock: 停在原地等待装卸, 不服务低优先级.
            # 单元派工已经记录自己的 WAIT 原因, 这里不重复写日志.
            return
        if self._ensure_reposition(top["uid"], dock, sim_time, top["task"]):
            return
        self._note_wait(top["task"], sim_time,
                        f"等待空载调机 {self.agv_at_dock}→{dock}")

    def _agv_move_requests(self, sim_time: float) -> list[dict]:
        requests: list[dict] = []
        for task in self.queue.pending_transports(sim_time):
            template = self._operation(task)
            origin = template.route_origin_dock
            destination = template.route_destination_dock
            if not origin or not destination:
                continue
            flow = self.flow.by_uid(task.uid)
            if flow is None or flow.zone != template.inputs[0].zone:
                continue
            requests.append({
                "kind": "transport", "transport": task, "task": task,
                "dock": origin, "destination": destination,
                "uid": task.uid, "created": task.created_sim_time,
                "priority": task.effective_priority(sim_time),
            })
        # 装载型单元任务 (B/D) 需要 AGV 先空载到位.
        for template in self._unit_templates:
            if template.sink_kind != "agv":
                continue
            for task in self.queue.by_kind(template.kind):
                if task.status not in (TaskStatus.PENDING,
                                       TaskStatus.WAITING):
                    continue
                flow = self.flow.by_uid(task.uid)
                if flow is None or flow.zone != template.inputs[0].zone:
                    continue
                dock = template.dock
                requests.append({
                    "kind": "load", "task": task, "dock": dock,
                    "destination": dock, "uid": task.uid,
                    "created": task.created_sim_time,
                    "priority": int(template.priority),
                })
        # 卸载型单元任务 (A/E) 需要 AGV 留在目标 dock.
        for template in self._unit_templates:
            if template.source_kind != "agv":
                continue
            for task in self.queue.by_kind(template.kind):
                if task.status not in (TaskStatus.PENDING,
                                       TaskStatus.WAITING):
                    continue
                flow = self.flow.by_uid(task.uid)
                if flow is None or flow.zone != template.inputs[0].zone:
                    continue
                dock = template.dock
                requests.append({
                    "kind": "unload", "task": task, "dock": dock,
                    "destination": dock, "uid": task.uid,
                    "created": task.created_sim_time,
                    "priority": int(template.priority),
                })
        return requests

    def _route_resources(self, spec) -> tuple[str, ...]:
        """路线实际经过的直道 + 全部 dock (车辆可能穿过中间 dock)."""
        resources = list(spec.lanes)
        if cfg.AGV_ROUTE_REQUIRES_ALL_DOCKS:
            resources.extend(f"dock_{dock}"
                             for dock in cfg.AGV_DOCK_IDS)
        return tuple(resources)

    def _ensure_reposition(self, uid: int, dock: str, sim_time: float,
                           target_task: Stage4Task) -> bool:
        """确保 AGV 空载前往 dock; 已有同目标 internal 任务则复用."""
        for task in self.queue.by_kind(cfg.TASK_KIND_REPOSITION):
            if (task.uid == uid and task.destination == dock
                    and task.status in (TaskStatus.PENDING,
                                        TaskStatus.WAITING)):
                return self._try_start_reposition(task, dock, sim_time)
        serial = self.queue.next_serial()
        task = Stage4Task(
            task_id=f"reposition_uid{uid}_to{dock}_leg{serial}",
            kind=cfg.TASK_KIND_REPOSITION, uid=uid,
            source=self.agv_at_dock, destination=dock,
            dock=dock, device="agv",
            status=TaskStatus.PENDING,
            priority=int(cfg.TASK_PRIORITY[cfg.TASK_KIND_REPOSITION]),
            created_sim_time=sim_time)
        ok, _reason = self.queue.submit(task, sim_time)
        if not ok:
            return False
        self._event("TASK_CREATED", task_id=task.task_id, uid=task.uid,
                    detail={"kind": task.kind,
                            "for_task": target_task.task_id,
                            "from": self.agv_at_dock, "to": dock})
        return self._try_start_reposition(task, dock, sim_time)

    def _try_start_reposition(self, task: Stage4Task, dock: str,
                              sim_time: float) -> bool:
        spec = make_route_spec(self.layout, self.agv_at_dock, dock,
                               loaded=False)
        owner = self._owner(task)
        acquired, busy = self.resources.acquire_all(
            self._route_resources(spec), owner, task.task_id, sim_time,
            "empty reposition")
        if not acquired:
            self._note_resource_result(
                task, acquired=False, busy=busy,
                owner=self.resources.owner(busy) or "")
            self._note_wait(task, sim_time, f"资源等待: {busy}")
            return False
        self._note_resource_result(
            task, acquired=True, resources=self._route_resources(spec))
        self._record_resources(task, self._route_resources(spec))
        return self._launch_route(task, spec, sim_time)

    def _start_transport(self, task: Stage4Task, origin: str,
                         destination: str, sim_time: float) -> bool:
        spec = make_route_spec(self.layout, origin, destination,
                               loaded=True)
        owner = self._owner(task)
        acquired, busy = self.resources.acquire_all(
            self._route_resources(spec), owner, task.task_id, sim_time,
            "loaded transport")
        if not acquired:
            self._note_resource_result(
                task, acquired=False, busy=busy,
                owner=self.resources.owner(busy) or "")
            self._note_wait(task, sim_time, f"资源等待: {busy}")
            return False
        self._note_resource_result(
            task, acquired=True, resources=self._route_resources(spec))
        self._record_resources(task, self._route_resources(spec))
        return self._launch_route(task, spec, sim_time)

    def _launch_route(self, task: Stage4Task, spec, sim_time: float) -> bool:
        try:
            execution_id = self.agv_executor.start(ExecutionRequest(
                operation=self._operation_or_none(task), task=task,
                device_id=self.agv_executor.device_id, uid=task.uid,
                task_id=task.task_id, sim_time=sim_time, route_spec=spec))
        except Exception as exc:
            self._release_task(task, sim_time, "route build failed")
            self._fail_operation(
                task, sim_time,
                f"ROUTE_BUILD_FAILED: {type(exc).__name__}: {exc}")
            self._freeze(f"AGV 路线构建失败: {exc}", sim_time)
            return False
        self.route_execution_id = execution_id
        self.route_task = task
        self._mark_started(task, sim_time, execution_id)
        self._attach_execution(task.instance_id, execution_id)
        self._set_phase(task, f"ROUTE {spec.route_id}")
        self._event("TASK_START", task_id=task.task_id, uid=task.uid,
                    source=spec.start_dock, destination=spec.end_dock,
                    device="agv",
                    detail={"kind": task.kind,
                            "route_id": spec.route_id,
                            "execution_id": execution_id,
                            "loaded": bool(spec.loaded)})
        self._event("ROUTE_START", task_id=task.task_id, uid=task.uid,
                    source=spec.start_dock, destination=spec.end_dock,
                    device="agv", evidence_id=spec.route_id,
                    detail={"route_id": spec.route_id,
                            "loaded": bool(spec.loaded),
                            "distance": round(spec.distance, 4),
                            "lanes": list(spec.lanes),
                            "legs": len(spec.legs),
                            "direction": spec.direction})
        if spec.loaded:
            self._record_upgraded(
                task, task.effective_priority(sim_time) < task.priority)
            pending = [
                {"task_id": other.task_id,
                 "priority": other.effective_priority(sim_time)}
                for other in self.queue.pending_transports(sim_time)
                if other.task_id != task.task_id]
            self.dispatch_decisions.append({
                "sim_time": round(sim_time, 4),
                "chosen": task.task_id,
                "chosen_priority": task.effective_priority(sim_time),
                "pending": pending,
                "upgraded": task.upgraded,
            })
            if self.fault == cfg.FAULT_AGV_BLOCKED:
                self._inject_agv_obstacle(spec)
        return True

    def _advance_route(self, sim_time: float) -> None:
        if not self.agv_executor.active:
            return
        if self.route_task is not None:
            self._set_phase(self.route_task, self.agv_executor.state.value)
        self.agv_executor.pre_tick(sim_time)

    # ═══════════════════════════════════════════════════════
    # 步后处理
    # ═══════════════════════════════════════════════════════

    def _after_step(self, sim_time: float) -> None:
        if self.aborted or self.frozen:
            return
        for unit_id, record in list(self.controllers.items()):
            task = self.queue.get(record.task_id)
            if task is None:
                continue
            try:
                report = record.executor.poll(record.execution_id)
            except Exception as exc:
                self._global_failure(
                    "UNKNOWN_EXECUTOR_FAILURE",
                    f"UNKNOWN_EXECUTOR_FAILURE: {type(exc).__name__}: {exc}",
                    sim_time, task=task, device_id=f"arm_{unit_id}",
                    evidence={"execution_id": record.execution_id})
                return
            self._set_phase(task, str(report.detail.get("phase",
                                                       task.phase)))
            if report.state is ExecutionState.FAILED:
                self._handle_unit_failure(unit_id, record, task, report,
                                          sim_time)
                if self.aborted or self.frozen:
                    return
                continue
            if report.state is ExecutionState.SUCCEEDED:
                controller = record.controller
                ok, reason, evidence = self._verify_unit_task(task, controller,
                                                              unit_id)
                if ok:
                    self._event("TASK_DONE", task_id=task.task_id,
                                uid=task.uid,
                                item_type=self.flow.by_uid(
                                    task.uid).item_type.value
                                if self.flow.by_uid(task.uid) else "",
                                device=f"arm_{unit_id}",
                                detail={"reason": reason})
                    # 执行端 SUCCEEDED 只触发验证; 物料区域/类型提交成功后
                    # 才由 _commit_unit_result() 把工序标为 DONE 并解锁后继.
                    self._commit_unit_result(task, unit_id, sim_time, evidence)
                    self._release_task(task, sim_time, "unit task complete")
                    self.controller_history.append({
                        "unit_id": unit_id, "task_id": task.task_id,
                        "execution_id": record.execution_id,
                        "summary": controller.summary()})
                    self.controllers.pop(unit_id, None)
                    record.executor.release("unit task complete")
                    self._maybe_inject_conveyor_fault(task, sim_time)
                    if self.frozen or self.aborted:
                        return
                else:
                    self._fail_operation(
                        task, sim_time, reason, evidence,
                        failure_code="NODE_VERIFICATION_FAILED")
                    self._event("TASK_FAILED", task_id=task.task_id,
                                uid=task.uid, device=f"arm_{unit_id}",
                                detail={"reason": reason})
                    self._global_failure(
                        "NODE_VERIFICATION_FAILED", reason, sim_time,
                        task=task, device_id=f"arm_{unit_id}",
                        evidence=evidence, mark_failed=False)
                    return
        self._after_route(sim_time)
        if self.order is None:
            self._check_join(sim_time)
        else:
            self._check_order_joins(sim_time)

    # ── 故障分类与局部隔离 ─────────────────────────────────

    def _handle_unit_failure(self, unit_id: str, record: ControllerRecord,
                             task: Stage4Task, report, sim_time: float
                             ) -> None:
        """按结构化 failure_code 分流 NODE/GLOBAL; 不解析错误字符串."""
        code = str(report.failure_code or
                   report.detail.get("failure_code") or "")
        if report.detail.get("safety_engaged"):
            code = "SAFETY_LATCH"
        if not code:
            code = "UNKNOWN_EXECUTOR_FAILURE"
        safe_ok, safe_state = self._confirm_node_local_safety(task, report)
        pre_physical = report.detail.get("physical_action_started") is False
        scope = classify_failure(
            code, pre_physical=pre_physical,
            safe_state_confirmed=safe_ok)
        reason = str(report.failure_reason or code)
        device_id = f"arm_{unit_id}"
        if scope is FailureScope.NODE:
            self._apply_node_failure(
                task, record, code, reason, sim_time, device_id,
                safe_state, dict(report.evidence))
            return
        if code == "SAFETY_LATCH":
            display = (f"{unit_id} 安全锁存: "
                       f"{report.detail.get('safety_latch_reason', '')}")
        elif code == "UNIT_CONTROLLER_FAILURE":
            display = (f"{unit_id} 任务失败: "
                       f"{report.detail.get('controller_failure', reason)}")
        else:
            display = f"{unit_id} 任务失败: {reason}"
        self._fail_operation(task, sim_time, reason,
                             dict(report.evidence), failure_code=code)
        self._event("TASK_FAILED", task_id=task.task_id, uid=task.uid,
                    device=device_id, failure_scope="GLOBAL",
                    node_id=task.operation.node_id,
                    detail={"reason": code, "failure_code": code,
                            "message": reason})
        self._global_failure(
            code, display, sim_time, task=task, device_id=device_id,
            evidence=dict(report.evidence), safe_state=safe_state,
            mark_failed=False)
        self.controllers.pop(unit_id, None)
        try:
            record.executor.release("global failure")
        except Exception:
            pass

    def _confirm_node_local_safety(
            self, task: Stage4Task, report) -> tuple[bool, dict]:
        """确认节点失败已停止且物料/账本/资源状态可核实."""
        safe: dict = {
            "executor_stopped": bool(report.finished),
            "physical_action_started": (
                report.detail.get("physical_action_started") is not False),
            "safety_engaged": bool(report.detail.get("safety_engaged")),
        }
        if not report.finished or safe["safety_engaged"]:
            return False, safe
        if report.detail.get("physical_action_started") is not False:
            return False, safe
        template = self._operation_or_none(task)
        flow = self.flow.by_uid(task.uid)
        rec = self.materials.by_uid(task.uid)
        if template is None or flow is None or rec is None \
                or rec.sim_handle is None:
            return False, safe
        if flow.zone != template.inputs[0].zone:
            return False, safe
        if flow.item_type not in template.input_types:
            return False, safe
        if flow.state not in (RecordState.CREATED, RecordState.READY):
            return False, safe
        safe.update({
            "material_uid": int(task.uid),
            "material_zone": flow.zone,
            "material_type": flow.item_type.value,
            "material_state": flow.state.value,
            "ledger_unchanged": True,
        })
        return True, safe

    def _apply_node_failure(self, task: Stage4Task, record: ControllerRecord,
                            code: str, reason: str, sim_time: float,
                            device_id: str, safe_state: dict,
                            evidence: dict) -> None:
        """节点局部失败: 停止本执行、释放资源、阻断依赖后继, 继续其他分支."""
        if not task.runtime_backed:
            self._global_failure(
                "SHARED_RESOURCE_FAILED",
                f"NODE scope 仅支持运行时订单节点: {reason}", sim_time,
                task=task, device_id=device_id, safe_state=safe_state)
            return
        unit_id = device_id.removeprefix("arm_")
        self.controllers.pop(unit_id, None)
        try:
            record.executor.release("node failure")
        except Exception:
            pass
        self._release_task(task, sim_time, "node failure")
        owner = self._owner(task)
        held = self.resources.held_by(owner)
        safe_state["resources_released"] = not held
        safe_state["held_resources"] = list(held)
        if held:
            self._global_failure(
                "RESOURCE_RELEASE_DENIED",
                f"节点 {task.task_id} 资源未释放: {held}", sim_time,
                task=task, device_id=device_id, safe_state=safe_state,
                mark_failed=False)
            return
        blocked = self.op_runtime.propagate_failure(
            task.instance_id, reason, sim_time, failure_code=code,
            evidence={"failure_scope": "NODE", "safe_state": safe_state,
                      **evidence})
        summary = self.op_runtime.failure_summary()
        failure = make_failure_record(
            code=code, message=reason,
            node_id=task.operation.node_id or task.operation_template_id,
            instance_id=task.instance_id, task_id=task.task_id,
            device_id=device_id, sim_time=sim_time,
            scope=FailureScope.NODE, safe_state=safe_state,
            evidence=evidence)
        record_row = failure.as_dict()
        record_row["blocked_nodes"] = [
            row.node_id or row.instance_id for row in blocked]
        record_row["continued_nodes"] = summary["continued_nodes"]
        record_row["order_status"] = self.order_status()
        self.failures.append(record_row)
        self._event(
            "TASK_FAILED", task_id=task.task_id, uid=task.uid,
            device=device_id, failure_scope="NODE",
            node_id=task.operation.node_id,
            detail={"reason": reason, "failure_code": code,
                    "failure": record_row,
                    "blocked": record_row["blocked_nodes"],
                    "continued": record_row["continued_nodes"]})
        self._event(
            "FAILURE_ISOLATED", failure_scope="NODE",
            node_id=task.operation.node_id,
            detail=record_row)

    def _global_failure(self, code: str, reason: str, sim_time: float, *,
                        task: Optional[Stage4Task] = None,
                        device_id: str = "",
                        evidence: Optional[dict] = None,
                        safe_state: Optional[dict] = None,
                        mark_failed: bool = True) -> None:
        """记录结构化全局故障并冻结全场; 幂等."""
        if self.aborted or self.frozen:
            return
        failure = make_failure_record(
            code=code, message=reason,
            node_id=(task.operation.node_id
                     if task is not None else ""),
            instance_id=(task.instance_id if task is not None else ""),
            task_id=(task.task_id if task is not None else ""),
            device_id=device_id, sim_time=sim_time,
            scope=FailureScope.GLOBAL, safe_state=safe_state or {},
            evidence=evidence or {})
        self.failures.append(failure.as_dict())
        if task is not None and mark_failed:
            try:
                self._fail_operation(
                    task, sim_time, reason, evidence,
                    failure_code=code)
            except Exception:
                pass
        self._event("FAILURE", failure_scope="GLOBAL",
                    node_id=(task.operation.node_id
                             if task is not None else ""),
                    detail=failure.as_dict())
        self._freeze(reason, sim_time, abort=True)

    # ── 单任务校验 ──────────────────────────────────────────

    def _verify_unit_task(self, task: Stage4Task,
                          controller: Stage4UnitController,
                          unit_id: str) -> tuple[bool, str, dict]:
        template = self._operation(task)
        evidence = {
            "controller": controller.summary(),
            "material": self._material_state_dict(task.uid),
        }
        rec = self.materials.by_uid(task.uid)
        if rec is None or rec.sim_handle is None:
            return False, "UID_NOT_REGISTERED", evidence
        pose = self._material_pose(rec.sim_handle)
        evidence["material_pose"] = pose
        evidence["material_state"] = rec.state.value
        flow = self.flow.by_uid(task.uid)
        if flow is None:
            return False, "FLOW_RECORD_MISSING", evidence
        if flow.item_type not in template.input_types:
            return False, (f"ITEM_TYPE_MISMATCH: {flow.item_type.value}"), \
                evidence
        sink = template.sink_kind
        if sink == "agv":
            if rec.state not in (MaterialState.ON_TRAY,
                                 MaterialState.RELEASED):
                return False, f"LOAD_HANDOFF_STATE: {rec.state.value}", \
                    evidence
            check = self._tray_check(rec.sim_handle)
            evidence["tray_check"] = check
            if not check["ok"]:
                return False, f"LOAD_NOT_ON_TRAY: {check['detail']}", evidence
        else:
            expected = self._expected_material_pose(template, unit_id)
            error = math.dist(pose[:3], expected) if pose else float("inf")
            evidence["place_error"] = error
            if rec.state is not MaterialState.RELEASED:
                return False, f"PLACE_STATE: {rec.state.value} != RELEASED", \
                    evidence
            if error > cfg.MATERIAL_PLACE_TOL:
                return False, (f"PLACE_ERROR: {error:.4f} > "
                               f"{cfg.MATERIAL_PLACE_TOL}"), evidence
        if not self._material_physics_ok(rec.sim_handle):
            return False, "PLACE_PHYSICS_NOT_RESTORED", evidence
        return True, f"{task.kind} 交接确认 (UID/类型/位置/物理属性)", evidence

    def _expected_material_pose(self, template: OperationTemplate,
                                unit_id: str) -> tuple[float, float, float]:
        sink = self._sink_spec(template, unit_id)
        if sink.kind == "agv":
            # 放置点跟随实测托盘; 验收必须用同一来源, 否则 AGV 停靠
            # 偏差会被误报为 PLACE_ERROR.
            try:
                tray = self.scene.handle(base.ALIASES["agv_tray"])
                if int(tray) >= 0:
                    tray_pose = self.sim.getObjectPose(
                        tray, self.sim.handle_world)
                    return tuple(agv_material_position(tray_pose))
            except Exception:
                pass
        return tuple(sink.material_pose[:3])

    def _commit_unit_result(self, task: Stage4Task, unit_id: str,
                            sim_time: float,
                            evidence: Optional[dict] = None) -> bool:
        """把验证通过的放置结果提交为物料账本, 再确认工序 DONE.

        任何一步失败都保持原有冻结语义并返回 ``False``: 工序不得标记 DONE,
        后继也不会解锁.
        """
        template = self._operation(task)
        flow = self.flow.by_uid(task.uid)
        rec = self.materials.by_uid(task.uid)
        if flow is None:
            return False
        if template.output is not None:
            input_types = set(template.input_types)
            if template.output.item_type not in input_types:
                try:
                    self.flow.process(task.uid, unit_id, sim_time,
                                      task_id=task.task_id)
                except MaterialFlowError as exc:
                    self._global_failure(
                        "PROCESS_FAILED", f"PROCESS_FAILED: {exc}",
                        sim_time, task=task, device_id=f"arm_{unit_id}")
                    return False
                flow = self.flow.by_uid(task.uid)
        dest = template.output.zone if template.output else ""
        if template.sink_kind == "conveyor":
            # 放到线入口: 物理状态回到 ON_CONVEYOR, 由下游任务启动带.
            if rec is not None and rec.state is MaterialState.RELEASED:
                try:
                    rec.transition_to(
                        MaterialState.ON_CONVEYOR, sim_time,
                        f"placed on {dest}; handoff to conveyor")
                except Exception as exc:
                    self._global_failure(
                        "MATERIAL_STATE_UNKNOWN",
                        f"CONVEYOR_STATE: {exc}", sim_time, task=task,
                        device_id=f"arm_{unit_id}")
                    return False
        try:
            self.flow.occupy(
                task.uid, dest, sim_time, reason=f"{task.kind} done",
                task_id=task.task_id,
                state=(RecordState.READY
                       if template.sink_kind != "conveyor"
                       else RecordState.IN_TRANSIT))
        except MaterialFlowError as exc:
            self._global_failure(
                "ZONE_COMMIT_FAILED", f"ZONE_COMMIT_FAILED: {exc}",
                sim_time, task=task, device_id=f"arm_{unit_id}")
            return False
        self._event("MATERIAL_PLACE", task_id=task.task_id, uid=task.uid,
                    item_type=flow.item_type.value if flow else "",
                    zone=dest, source=template.inputs[0].zone,
                    destination=dest, device=f"arm_{unit_id}",
                    detail={"task_kind": task.kind,
                            "operation": template.id})
        # 账本提交成功后才登记输出并解锁后继; 重复提交由 runtime 拒绝.
        self._commit_operation(task, sim_time, dict(evidence or {}))
        return True

    def _submit(self, task: Stage4Task, sim_time: float) -> bool:
        ok, reason = self.queue.submit(task, sim_time)
        if not ok:
            self._event("TASK_REJECTED", task_id=task.task_id, uid=task.uid,
                        detail={"reason": reason})
        else:
            self._event("TASK_CREATED", task_id=task.task_id, uid=task.uid,
                        item_type=self.flow.by_uid(task.uid).item_type.value
                        if self.flow.by_uid(task.uid) else "",
                        detail={"kind": task.kind,
                                "priority": task.priority})
        return ok

    # ═══════════════════════════════════════════════════════
    # AGV 航线收尾
    # ═══════════════════════════════════════════════════════

    def _after_route(self, sim_time: float) -> None:
        if self.aborted or self.frozen:
            return
        executor = self.agv_executor
        task = self.route_task
        if task is None or not executor.active:
            return
        report = executor.poll(self.route_execution_id)
        spec = executor.spec
        route_state = str(report.detail.get("route_state", ""))
        route_summary = dict(report.evidence.get("route") or {})
        if route_state == RouteState.DONE.value:
            ok, reason = self._verify_route(task, executor.controller)
            if ok:
                self.agv_at_dock = spec.end_dock
                self._event("ROUTE_DONE", task_id=task.task_id,
                            uid=task.uid, source=spec.start_dock,
                            destination=spec.end_dock, device="agv",
                            evidence_id=spec.route_id,
                            detail={"reason": reason,
                                    "loaded": bool(spec.loaded)})
                if spec.loaded:
                    self._event("TASK_DONE", task_id=task.task_id,
                                uid=task.uid, device="agv",
                                detail={"reason": reason})
                else:
                    self._event("TASK_DONE", task_id=task.task_id,
                                uid=task.uid, device="agv",
                                detail={"reason": reason,
                                        "kind": cfg.TASK_KIND_REPOSITION})
                # 载货航线与单元工序走同一成功边界: 停靠/滑移验证通过后
                # 才提交工序, 由运行图解锁后继 (不再读模板 successor).
                self._commit_operation(task, sim_time,
                                       {"route": route_summary})
            else:
                self._fail_operation(task, sim_time, reason,
                                     {"route": route_summary},
                                     failure_code="NODE_VERIFICATION_FAILED")
                self._event("TASK_FAILED", task_id=task.task_id,
                            uid=task.uid, device="agv",
                            detail={"reason": reason})
                self._global_failure(
                    "NODE_VERIFICATION_FAILED", reason, sim_time,
                    task=task, device_id="agv",
                    evidence={"route": route_summary}, mark_failed=False)
            self.agv_history.append(route_summary)
            self._release_task(task, sim_time, "route complete")
            executor.release("route complete")
            self.route_task = None
            self.route_execution_id = ""
            return
        if route_state == RouteState.FAILED.value:
            self._fail_operation(
                task, sim_time,
                f"AGV_ROUTE_FAILED: {report.failure_reason}",
                {"route": route_summary}, failure_code="AGV_ROUTE_FAILED")
            self.agv_history.append(route_summary)
            self._event("TASK_FAILED", task_id=task.task_id, uid=task.uid,
                        device="agv",
                        detail={"reason": report.failure_reason})
            self._release_task(task, sim_time, "route failed")
            executor.release("route failed")
            self.route_task = None
            self.route_execution_id = ""
            self._global_failure(
                "AGV_ROUTE_FAILED",
                f"AGV_ROUTE_FAILED: {report.failure_reason}", sim_time,
                task=task, device_id="agv",
                evidence={"route": route_summary}, mark_failed=False)
            return
        if route_state == RouteState.BLOCKED.value:
            if task.status is not TaskStatus.BLOCKED:
                if self._block_task(task, sim_time,
                                    "AGV 路线受阻, 轮速已停止"):
                    self._block_since = sim_time
                    self._event("TASK_BLOCK", task_id=task.task_id,
                                uid=task.uid, device="agv",
                                detail={"route_id": spec.route_id})
            if self._block_since is None:
                self._block_since = sim_time
            if sim_time - self._block_since >= cfg.AGV_BLOCK_CLEAR_AFTER_S:
                removed = self._remove_agv_obstacle()
                clearance_ok = self._route_clearance_ok()
                self._event("AGV_BLOCK_CLEAR", task_id=task.task_id,
                            uid=task.uid,
                            detail={"removed": removed,
                                    "clearance_ok": clearance_ok,
                                    "lanes": list(spec.lanes),
                                    "owner": {lane: self.resources.owner(lane)
                                              for lane in spec.lanes}})
                if clearance_ok and executor.resume(
                        sim_time, clearance_ok=True,
                        reason="obstacle_removed_and_ownership_reconfirmed"):
                    self._unblock_task(
                        task, sim_time,
                        "障碍清除, 现场/资源所有权重新确认")
                    self._event("TASK_UNBLOCK", task_id=task.task_id,
                                uid=task.uid, device="agv")
                    self._block_since = None
                    return
            if sim_time - self._block_since > cfg.AGV_ROUTE_TIMEOUT:
                self._fail_operation(
                    task, sim_time,
                    "AGV_BLOCKED_TIMEOUT: 清障后仍未续行",
                    {"route": route_summary},
                    failure_code="AGV_ROUTE_FAILED")
                self._global_failure(
                    "AGV_ROUTE_FAILED", "AGV_BLOCKED_TIMEOUT", sim_time,
                    task=task, device_id="agv",
                    evidence={"route": route_summary},
                    mark_failed=False)

    def _verify_route(self, task: Stage4Task,
                      route: AGVRouteController4) -> tuple[bool, str]:
        metrics = route.metrics
        if metrics.stop_error is None or \
                metrics.stop_error > cfg.AGV_ROUTE_ARRIVE_TOL:
            return False, (f"STOP_ERROR: {metrics.stop_error} > "
                           f"{cfg.AGV_ROUTE_ARRIVE_TOL}")
        if metrics.heading_error is None or \
                abs(metrics.heading_error) > cfg.AGV_ROUTE_HEADING_TOL:
            return False, (f"HEADING_ERROR: {metrics.heading_error} > "
                           f"{cfg.AGV_ROUTE_HEADING_TOL}")
        if route.spec.loaded:
            if metrics.material_slip is None or \
                    metrics.material_slip > cfg.AGV_ROUTE_SLIP_TOL:
                return False, (f"LOAD_SLIP: {metrics.material_slip} > "
                               f"{cfg.AGV_ROUTE_SLIP_TOL}")
            if not self._material_on_tray(task.uid):
                return False, "LOAD_NOT_ON_TRAY_AFTER_TRANSIT"
        return True, "route metrics within limits"

    def _route_clearance_ok(self) -> bool:
        if any(arm.safety.engaged for arm in self.runtime.arms.values()):
            return False
        if self._fault_handles:
            return False
        if self.route_task is None or self.route is None:
            return False
        owner = self._owner(self.route_task)
        for lane in self.route.spec.lanes:
            if self.resources.owner(lane) != owner:
                return False
        for dock in cfg.AGV_DOCK_IDS:
            if self.resources.owner(f"dock_{dock}") not in (None, owner):
                return False
        return True

    # ═══════════════════════════════════════════════════════
    # E 岛配对
    # ═══════════════════════════════════════════════════════

    def _check_join(self, sim_time: float) -> None:
        """E 岛合流: 输入与父实例都就绪后建图, 账本成功才把节点标 DONE.

        配对选择仍由 :meth:`MaterialRegistry.pair_candidates` 负责; 本方法只
        确认这两件物料分别来自图中已完成并提交的两条输入工序, 再绑定父实例.
        """
        if self.frozen or self.aborted:
            return
        join_id = self.recipe.join_operation_id
        if not join_id:
            return
        join = self.catalog.operation(join_id, self.recipe.id)
        if self.op_runtime.join_blocked(join_id):
            return
        semi, part = self.flow.pair_candidates()
        if semi is None or part is None:
            return
        semi_port = self._join_port(join, semi.item_type)
        part_port = self._join_port(join, part.item_type)
        if semi_port is None or part_port is None:
            return
        semi_instance = self.op_runtime.completed_output(semi.uid, semi_port)
        part_instance = self.op_runtime.completed_output(part.uid, part_port)
        if semi_instance is None or part_instance is None:
            # 物料在位但对应工序尚未提交: 不建节点, 不生成虚假任务.
            return
        uid = self._next_uid
        try:
            instance = self.op_runtime.add_join(
                parents=[semi_instance, part_instance],
                input_uids=[semi.uid, part.uid], uid=uid, sim_time=sim_time)
        except OperationRuntimeError as exc:
            self._event("JOIN_REJECTED", uid=uid,
                        detail={"reason": str(exc)})
            self._freeze(f"JOIN_BIND_FAILED: {exc}", sim_time)
            return
        self._next_uid += 1
        alias = f"Part_{uid:04d}"
        out = cfg.E_OUTPUT
        try:
            handle = self.scene.spawn_material(
                uid, alias,
                position=(out["position"][0], out["position"][1],
                          out["material_z"]))
        except Exception as exc:
            self.op_runtime.mark_failed(instance.instance_id,
                                        f"FINAL_SPAWN_FAILED: {exc}",
                                        sim_time)
            self._freeze(f"FINAL_SPAWN_FAILED: {exc}", sim_time)
            return
        out_zone = join.output.zone if join.output else "output_e"
        out_type = join.output.item_type if join.output else ItemType.FINAL
        self._set_material_color(handle, out_type)
        self.materials.register(uid, alias, sim_handle=handle)
        result = self.flow.commit_join(
            final_uid=uid, sim_time=sim_time, final_alias=alias,
            final_handle=handle, final_zone=out_zone)
        if not result.ok:
            self.scene.remove_material(handle)
            self.materials.remove(uid)
            self.op_runtime.mark_failed(instance.instance_id,
                                        f"JOIN_REJECTED: {result.reason}",
                                        sim_time, evidence=result.as_dict())
            self._event("JOIN_REJECTED", uid=uid,
                        detail=result.as_dict())
            return
        for parent in (result.semi_uid, result.part_uid):
            rec = self.materials.by_uid(parent)
            if rec is not None and rec.sim_handle is not None:
                # 先移出所有臂的碰撞集合, 再删除场景对象: 否则并发的
                # ConfigurationChecker 查询会引用已删除句柄.
                self.runtime.forget_material(rec.sim_handle)
                self.scene.remove_material(rec.sim_handle)
            self.materials.detach_sim(parent)
            parent_flow = self.flow.by_uid(parent)
            if parent_flow is not None:
                parent_flow.sim_handle = None
            for unit_id in cfg.UNIT_IDS:
                try:
                    self.comp["suction"][unit_id].forget(parent)
                except Exception:
                    pass
        self.final_count += 1
        self.op_runtime.commit_success(
            instance.instance_id, output_uids=[uid], sim_time=sim_time,
            evidence={"pair_key": result.pair_key, "join": join.id,
                      "parents": [result.semi_uid, result.part_uid]})
        self._event("FINAL_CREATED", uid=uid,
                    item_type=out_type.value, zone=out_zone,
                    device="E", evidence_id=result.pair_key,
                    detail={"parents": [result.semi_uid, result.part_uid],
                            "pair_key": result.pair_key,
                            "operation": join.id,
                            "instance": instance.instance_id})

    @staticmethod
    def _join_port(join: OperationTemplate,
                   item_type: ItemType):
        for port in join.inputs:
            if port.item_type is item_type:
                return port
        return None

    # ── 订单逻辑合流 ────────────────────────────────────────

    def _zone_material_pose(self, zone: str):
        """订单逻辑工序输出区域的物料落点; 未知区域返回 None."""
        if zone == "output_e":
            return (cfg.E_OUTPUT["position"][0],
                    cfg.E_OUTPUT["position"][1],
                    cfg.E_OUTPUT["material_z"])
        if zone in cfg.E_BUFFERS:
            position = cfg.E_BUFFERS[zone]["position"]
            return (position[0], position[1],
                    base.ISLAND_TOP_Z + base.MATERIAL_HALF_HEIGHT)
        if zone == cfg.HANDOFF_AC["id"]:
            position = cfg.HANDOFF_AC["position"]
            return (position[0], position[1],
                    base.STATION_TOP_Z + base.MATERIAL_HALF_HEIGHT)
        line = self.lines.get(zone)
        if line is not None:
            return tuple(line.place_material_pose)
        return None

    def _check_order_joins(self, sim_time: float) -> None:
        """执行编译好的订单逻辑合流; 父 UID 由编译器绑定, 不重新配对."""
        if self.frozen or self.aborted or self.order is None:
            return
        for instance in self.op_runtime.graph.instances():
            template = self._template(instance.template_id)
            if template.executor_kind != "logic":
                continue
            if instance.state not in (TaskStatus.PENDING,
                                      TaskStatus.WAITING):
                continue
            predecessors = [self.op_runtime.instance(pid)
                            for pid in self.op_runtime.graph.predecessors(
                                instance.instance_id)]
            if not predecessors or not all(
                    row is not None and row.state is TaskStatus.DONE
                    and row.committed for row in predecessors):
                continue
            result = self._execute_order_join(
                instance, template, sim_time)
            if not result:
                return

    def _execute_order_join(self, instance, template: OperationTemplate,
                            sim_time: float) -> bool:
        parent_uids = [int(uid) for uid in instance.input_uids]
        if len(parent_uids) != len(template.inputs):
            self._global_failure(
                "LEDGER_INCONSISTENT",
                f"合流 {instance.node_id} 父 UID {parent_uids} 与输入端口 "
                f"{len(template.inputs)} 不一致", sim_time)
            return False
        for port in template.inputs:
            matches = [uid for uid in parent_uids
                       if self.flow.by_uid(uid) is not None
                       and self.flow.by_uid(uid).item_type == port.item_type
                       and self.flow.by_uid(uid).zone == port.zone]
            if len(matches) != 1:
                self._global_failure(
                    "LEDGER_INCONSISTENT",
                    f"合流 {instance.node_id} 端口 {port.zone}/"
                    f"{port.item_type.value} 父 UID 不唯一: {parent_uids}",
                    sim_time)
                return False
        semi_candidates = [
            uid for uid in parent_uids
            if self.flow.by_uid(uid) is not None
            and self.flow.by_uid(uid).item_type is ItemType.SEMI_CE]
        part_candidates = [
            uid for uid in parent_uids
            if self.flow.by_uid(uid) is not None
            and self.flow.by_uid(uid).item_type is ItemType.PART_DE]
        if len(semi_candidates) != 1 or len(part_candidates) != 1:
            self._global_failure(
                "LEDGER_INCONSISTENT",
                f"合流 {instance.node_id} 缺少唯一 SEMI_CE/PART_DE 父 UID: "
                f"{parent_uids}", sim_time)
            return False
        semi_uid, part_uid = semi_candidates[0], part_candidates[0]
        if template.output is None:
            self._global_failure(
                "LEDGER_INCONSISTENT",
                f"合流 {instance.node_id} 缺少输出端口", sim_time)
            return False
        out_uid = int(instance.uid)
        out_type = template.output.item_type
        out_zone = template.output.zone
        pose = self._zone_material_pose(out_zone)
        if pose is None:
            self._global_failure(
                "SCENE_INTEGRITY_LOST",
                f"合流输出区域 {out_zone!r} 没有落点", sim_time)
            return False
        alias = f"Part_{out_uid:04d}"
        try:
            handle = self.scene.spawn_material(
                out_uid, alias, position=tuple(pose))
        except Exception as exc:
            self._global_failure(
                "SCENE_INTEGRITY_LOST",
                f"JOIN_SPAWN_FAILED: {type(exc).__name__}: {exc}", sim_time)
            return False
        self._set_material_color(handle, out_type)
        self.materials.register(out_uid, alias, sim_handle=handle)
        result = self.flow.commit_join_uids(
            final_uid=out_uid, semi_uid=semi_uid, part_uid=part_uid,
            sim_time=sim_time, final_alias=alias, final_handle=handle,
            final_zone=out_zone)
        if not result.ok:
            self.scene.remove_material(handle)
            self.materials.remove(out_uid)
            self._global_failure(
                "LEDGER_INCONSISTENT",
                f"JOIN_REJECTED: {result.reason}", sim_time,
                evidence=result.as_dict())
            return False
        for parent in (result.semi_uid, result.part_uid):
            rec = self.materials.by_uid(parent)
            if rec is not None and rec.sim_handle is not None:
                self.runtime.forget_material(rec.sim_handle)
                self.scene.remove_material(rec.sim_handle)
            self.materials.detach_sim(parent)
            parent_flow = self.flow.by_uid(parent)
            if parent_flow is not None:
                parent_flow.sim_handle = None
            for unit_id in cfg.UNIT_IDS:
                try:
                    self.comp["suction"][unit_id].forget(parent)
                except Exception:
                    pass
        self.final_count += 1
        self.op_runtime.commit_success(
            instance.instance_id, output_uids=[out_uid], sim_time=sim_time,
            evidence={"pair_key": result.pair_key, "join": template.id,
                      "parents": [result.semi_uid, result.part_uid],
                      "order_id": instance.order_id,
                      "node_id": instance.node_id})
        self._event(
            "FINAL_CREATED", uid=out_uid, item_type=out_type.value,
            zone=out_zone, device="E", evidence_id=result.pair_key,
            node_id=instance.node_id,
            detail={"parents": [result.semi_uid, result.part_uid],
                    "pair_key": result.pair_key,
                    "operation": template.id,
                    "instance": instance.instance_id,
                    "order_id": instance.order_id})
        return True

    # ═══════════════════════════════════════════════════════
    # 故障注入
    # ═══════════════════════════════════════════════════════

    def _maybe_inject_fault(self, sim_time: float) -> None:
        if self.fault == cfg.FAULT_CONVEYOR_STOP and self._line_faulted:
            return

    def _maybe_inject_conveyor_fault(self, task: Stage4Task,
                                     sim_time: float) -> None:
        if self.fault != cfg.FAULT_CONVEYOR_STOP:
            return
        if self._line_faulted:
            return
        if task.kind != cfg.CONVEYOR_FAULT_AFTER_TASK:
            return
        line = self.lines[cfg.CONVEYOR_FAULT_LINE]
        try:
            self.conveyor.stop(line.conveyor)
        except Exception as exc:
            self._event("FAULT_INJECT_FAILED", detail={
                "kind": "conveyor_stop", "error": str(exc)})
            return
        self._line_faulted = True
        self.injected.append({
            "kind": "conveyor_stop",
            "sim_time": round(sim_time, 4),
            "line": line.line_id,
            "conveyor": line.conveyor,
            "after_task": task.task_id,
        })
        self._event("FAULT_INJECT", device=line.line_id,
                    detail={"kind": "conveyor_stop",
                            "after_task": task.task_id})
        # 受影响支路任务立即进入 BLOCKED 并保留现场.
        for other in self.queue.all():
            if not self._task_consumes_line(
                    other, cfg.CONVEYOR_FAULT_LINE):
                continue
            if other.status in (TaskStatus.PENDING, TaskStatus.WAITING,
                                TaskStatus.RUNNING):
                if other.status is not TaskStatus.BLOCKED:
                    if self._block_task(
                            other, sim_time,
                            f"CONVEYOR_FAULT: {line.line_id} 停带"):
                        self._event("TASK_BLOCK", task_id=other.task_id,
                                    uid=other.uid, device=line.line_id,
                                    detail={"reason": "CONVEYOR_FAULT"})
        # 冻结整线现场: 停止 AGV/所有线, 活动任务停在当前位置, 保留 UID.
        self._freeze(f"CONVEYOR_FAULT: {line.line_id} 停止", sim_time)

    def _inject_agv_obstacle(self, spec) -> None:
        if self._fault_handles:
            return
        point = self._agv_obstacle_point(spec)
        if point is None:
            self._event("FAULT_INJECT_FAILED", detail={
                "kind": "agv_blocked",
                "error": "没有足够长的载货直道用于放置障碍"})
            return
        try:
            handle = int(self.sim.createPrimitiveShape(
                self.sim.primitiveshape_cuboid,
                list(cfg.AGV_BLOCK_OBSTACLE_SIZE)))
            self.sim.setObjectAlias(handle, cfg.AGV_BLOCK_OBSTACLE_ALIAS)
            self.sim.setObjectPosition(handle, list(point),
                                       self.sim.handle_world)
            phys.apply_static_shape(self.sim, handle, respondable=True)
            self._fault_handles.append(handle)
            self.runtime.add_hazard(handle)
            self.injected.append({
                "kind": "agv_blocked",
                "sim_time": round(self._sim_time(), 4),
                "handle": handle,
                "alias": cfg.AGV_BLOCK_OBSTACLE_ALIAS,
                "position": list(point),
                "route_id": spec.route_id,
                "lane": spec.lanes[0] if spec.lanes else "",
            })
            self._event("FAULT_INJECT", device="agv",
                        detail={"kind": "agv_blocked",
                                "alias": cfg.AGV_BLOCK_OBSTACLE_ALIAS,
                                "route_id": spec.route_id,
                                "position": list(point)})
        except Exception as exc:
            self._event("FAULT_INJECT_FAILED", detail={
                "kind": "agv_blocked", "error": str(exc)})

    @staticmethod
    def _agv_obstacle_point(spec) -> Optional[tuple[float, float, float]]:
        """在载货路线的最长直道中点放置障碍 (避开 dock 收敛窗口)."""
        legs = [leg for leg in spec.straight_legs if leg.distance >= 0.30]
        if not legs:
            return None
        leg = max(legs, key=lambda item: item.distance)
        mid = ((leg.start[0] + leg.end[0]) / 2.0,
               (leg.start[1] + leg.end[1]) / 2.0)
        return (mid[0], mid[1], cfg.AGV_BLOCK_OBSTACLE_SIZE[2] / 2.0)

    def _remove_agv_obstacle(self) -> bool:
        if not self._fault_handles:
            return False
        remaining: list[int] = []
        for handle in list(self._fault_handles):
            try:
                self.sim.removeObjects([int(handle)])
            except Exception:
                remaining.append(int(handle))
        self._fault_handles = remaining
        if not remaining:
            self.runtime.remove_hazards()
        return not remaining

    # ═══════════════════════════════════════════════════════
    # 资源
    # ═══════════════════════════════════════════════════════

    def _owner(self, task: Stage4Task) -> str:
        template = self._operation_or_none(task)
        if template is not None:
            device_id = (task.operation.selected_device_id
                         or template.candidate_devices[0])
            device = self.catalog.device(device_id)
            if device.kind == "arm":
                unit_id = device.id.removeprefix("arm_")
                return f"unit_{unit_id}/task_{task.task_id}"
        return f"agv/task_{task.task_id}"

    def _release_task(self, task: Stage4Task, sim_time: float,
                      reason: str) -> None:
        self.resources.release_all(self._owner(task), sim_time, reason)

    def _release_all(self, sim_time: float, reason: str) -> None:
        for owner in sorted(set(self.resources.owners.values())):
            self.resources.release_all(owner, sim_time, reason)

    # ═══════════════════════════════════════════════════════
    # 冻结/超时
    # ═══════════════════════════════════════════════════════

    def _freeze(self, reason: str, sim_time: float,
                abort: bool = False) -> None:
        if self.aborted or self.frozen:
            return
        self.frozen = True
        self.aborted = bool(abort)
        self.stop_reason = str(reason)
        self._frozen_since = float(sim_time)
        self._abort_settle_steps = 0
        self._event("FREEZE", detail={
            "reason": reason, "abort": abort,
            "order_status": self.order_status()})
        try:
            self.agv.stop()
        except Exception:
            pass
        for line in self.lines.values():
            try:
                self.conveyor.stop(line.conveyor)
            except Exception:
                pass
        for record in list(self.controllers.values()):
            try:
                record.controller._hold_after_latch()
            except Exception:
                pass
            try:
                record.controller._fail(f"STAGE4_FROZEN: {reason}")
            except Exception:
                pass
        for task in self.queue.all():
            if task.status is TaskStatus.RUNNING:
                self._fail_task(task, sim_time,
                                f"STAGE4_FROZEN: {reason}")
            elif task.status in (TaskStatus.PENDING, TaskStatus.WAITING):
                self._block_task(task, sim_time,
                                 f"STAGE4_FROZEN: {reason}")
        self._release_all(sim_time, "freeze")
        if self.fault != cfg.FAULT_AGV_BLOCKED:
            self._remove_agv_obstacle()

    def _timeout_check(self, sim_time: float) -> None:
        if self.aborted or self.frozen:
            return
        for task in self.queue.active():
            if task.status is TaskStatus.WAITING:
                since = task.waiting_since
                if since is None:
                    since = task.created_sim_time
                if sim_time - since > cfg.BACKPRESSURE_MAX_WAIT_S:
                    self._fail_operation(
                        task, sim_time,
                        f"BACKPRESSURE_TIMEOUT: {task.phase}",
                        failure_code="BACKPRESSURE_TIMEOUT")
                    self._event("TASK_FAILED", task_id=task.task_id,
                                uid=task.uid,
                                detail={"reason": "BACKPRESSURE_TIMEOUT"})
                    self._global_failure(
                        "BACKPRESSURE_TIMEOUT", "BACKPRESSURE_TIMEOUT",
                        sim_time, task=task, mark_failed=False)
                    return
            elif task.status is TaskStatus.RUNNING:
                if sim_time - (task.started_sim_time or sim_time) > \
                        cfg.TASK_TIMEOUT:
                    self._fail_operation(task, sim_time, "TASK_TIMEOUT",
                                         failure_code="TASK_TIMEOUT")
                    self._event("TASK_FAILED", task_id=task.task_id,
                                uid=task.uid,
                                detail={"reason": "TASK_TIMEOUT"})
                    self._global_failure(
                        "TASK_TIMEOUT", "TASK_TIMEOUT", sim_time,
                        task=task, mark_failed=False)
                    return

    @property
    def finished(self) -> bool:
        if self.aborted or self.frozen:
            if self._abort_settle_steps >= cfg.SAFETY_STOP_SETTLE_STEPS:
                return True
            if self.frozen and self._frozen_since is not None:
                return (self._sim_time() - self._frozen_since
                        >= cfg.CONVEYOR_FAULT_SETTLE_STEPS * base.SIM_STEP
                        or self._abort_settle_steps >=
                        cfg.SAFETY_STOP_SETTLE_STEPS)
            return False
        if self.route is not None or self.controllers:
            return False
        if not self.queue.finished():
            return False
        self.op_runtime.cancel_unreachable(self._sim_time())
        if not self.op_runtime.settled():
            return False
        state = self.op_runtime.progress_state(aborted=self.aborted)
        if state == "RUNNING":
            return False
        if self.order is not None:
            return len(self._order_spawned) >= len(self._order_inputs)
        return all(
            self._source_count[key] >=
            int(self._batch.get(self._source_entries[key].item_type.value, 0))
            for key in self._source_keys)

    def order_status(self) -> str:
        """当前订单/运行图收敛状态; 不改变仿真生命周期."""
        return self.op_runtime.progress_state(aborted=self.aborted)

    # ═══════════════════════════════════════════════════════
    # 校验辅助
    # ═══════════════════════════════════════════════════════

    def _agv_at_dock(self, dock: str) -> bool:
        unit_id = dock
        if not self.scene.agv_dock_hit_at(unit_id, self.scene.agv_root):
            return False
        if not self.agv.is_stopped() or not self.agv.is_upright():
            return False
        pose = self.agv.pose()
        target = self.layout.dock_positions[unit_id]
        return (math.dist(pose[:2], target)
                <= cfg.AGV_ROUTE_ARRIVE_TOL * 2.0)

    def _material_on_tray(self, uid: int) -> bool:
        rec = self.materials.by_uid(uid)
        if rec is None or rec.sim_handle is None:
            return False
        return bool(self._tray_check(rec.sim_handle)["ok"])

    def _tray_check(self, handle: int) -> dict:
        try:
            pose = [float(v) for v in self.sim.getObjectPose(
                handle, self.sim.handle_world)]
            agv = [float(v) for v in self.agv.pose()]
        except Exception as exc:
            return {"ok": False, "detail": f"pose_unreadable: {exc}"}
        yaw = pose_yaw(agv)
        dx, dy = pose[0] - agv[0], pose[1] - agv[1]
        c, s = math.cos(-yaw), math.sin(-yaw)
        local_x = c * dx - s * dy
        local_y = s * dx + c * dy
        z_error = pose[2] - (base.AGV_TRAY_TOP_Z + base.MATERIAL_HALF_HEIGHT)
        ok = (abs(local_x) <= cfg.AGV_TRAY_XY_TOL
              and abs(local_y) <= cfg.AGV_TRAY_XY_TOL
              and abs(z_error) <= cfg.AGV_TRAY_Z_TOL)
        return {
            "ok": bool(ok),
            "detail": f"local=({local_x:.4f},{local_y:.4f}) "
                      f"z_error={z_error:.4f}",
            "relative_xy": [round(local_x, 6), round(local_y, 6)],
            "z_error": round(z_error, 6),
            "pose": [round(v, 6) for v in pose],
        }

    def _material_physics_ok(self, handle: int) -> bool:
        try:
            snap = phys.snapshot_physics(self.sim, handle)
        except Exception:
            return False
        return snap.get("static") == 0 and snap.get("respondable") == 1

    def _material_pose(self, handle: int) -> Optional[list[float]]:
        try:
            return [round(float(v), 6) for v in self.sim.getObjectPose(
                handle, self.sim.handle_world)]
        except Exception:
            return None

    def _material_state_dict(self, uid: int) -> dict:
        rec = self.materials.by_uid(uid)
        if rec is None:
            return {"uid": uid, "registered": False}
        return {
            "uid": uid,
            "alias": rec.alias,
            "state": rec.state.value,
            "handle": rec.sim_handle,
            "pose": (None if rec.sim_handle is None
                     else self._material_pose(rec.sim_handle)),
        }

    # ═══════════════════════════════════════════════════════
    # 验收
    # ═══════════════════════════════════════════════════════

    def acceptance(self) -> dict:
        tasks = self.queue.all()
        safety = {unit_id: arm.safety.latch.as_dict()
                  for unit_id, arm in self.runtime.arms.items()}
        any_latch = any(item.get("engaged") for item in safety.values())
        resources_released = all(
            owner is None for owner in self.resources.owners.values())
        finals = self.flow.final_records()
        target_uids: set[int] = set()
        if self.order is not None:
            for instance_id in self.order.target_instance_ids:
                instance = self.op_runtime.instance(instance_id)
                if instance is not None:
                    target_uids.update(int(uid)
                                       for uid in instance.output_uids)
        semi_wip = [rec for rec in self.flow.by_type(ItemType.SEMI_CE)
                    if rec.state is not RecordState.CONSUMED]
        loaded_routes = [row for row in self.agv_history
                         if row.get("spec", {}).get("loaded")]
        if self.order is not None:
            route_required = any(
                self._template(instance.template_id).executor_kind == "agv"
                for instance in self.op_runtime.instances())
            route_ok = (not route_required) or bool(
                loaded_routes and all(
                    row.get("metrics", {}).get("stop_error") is not None
                    and row["metrics"]["stop_error"]
                    <= cfg.AGV_ROUTE_ARRIVE_TOL
                    and abs(row["metrics"].get("heading_error") or 0.0)
                    <= cfg.AGV_ROUTE_HEADING_TOL
                    for row in loaded_routes))
        else:
            route_ok = bool(
                loaded_routes and all(
                    row.get("metrics", {}).get("stop_error") is not None
                    and row["metrics"]["stop_error"]
                    <= cfg.AGV_ROUTE_ARRIVE_TOL
                    and abs(row["metrics"].get("heading_error") or 0.0)
                    <= cfg.AGV_ROUTE_HEADING_TOL
                    for row in loaded_routes))
        priority_ok, priority_detail = self._priority_acceptance()
        scheduler_ok, scheduler_detail = self._scheduler_acceptance()
        graph_instances = self.op_runtime.graph.instances()
        batch_expectation = expected_flow_counts(self._batch)
        expected_final = int(batch_expectation["final"])
        expected_unpaired = int(batch_expectation["unpaired_semi_ce"])
        acceptance = {
            "scenario": self.scenario,
            "fault": self.fault,
            "tasks_done": self.queue.all_done(),
            "graph_complete": self.op_runtime.all_done(),
            "operation_instances": len(graph_instances),
            "operations_failed": sum(
                1 for row in graph_instances
                if row.state is TaskStatus.FAILED),
            "any_task_failed": self.queue.any_failed(),
            "any_task_blocked": self.queue.any_blocked(),
            "final_count": len(finals),
            "final_parents": [list(rec.parents) for rec in finals],
            "order_target_count": len(target_uids),
            "unpaired_semi_ce": len(semi_wip),
            "any_safety_latch": any_latch,
            "safety": safety,
            "resources_released": resources_released,
            "aborted": self.aborted,
            "frozen": self.frozen,
            "stop_reason": self.stop_reason,
            "order_status": self.order_status(),
            "failure_policy_version": FAILURE_POLICY_VERSION,
            "failures": list(self.failures),
            "failure_summary": self.op_runtime.failure_summary(),
            "loaded_routes": len(loaded_routes),
            "route_ok": route_ok,
            "priority_ok": priority_ok,
            "priority_detail": priority_detail,
            "scheduler_ok": scheduler_ok,
            "scheduler_detail": scheduler_detail,
            "injected": list(self.injected),
            "batch": dict(self._batch),
            "batch_input_version": spec.BATCH_INPUT_VERSION,
            "batch_expectation": batch_expectation,
            "spawned": list(self.spawned),
        }
        common = (not any_latch and resources_released and route_ok
                  and priority_ok and scheduler_ok
                  and acceptance["graph_complete"])
        if self.order is not None:
            acceptance["order"] = self.order.as_dict()
            acceptance["order_target_quantity"] = int(
                self.order.target_quantity)
            acceptance["ok"] = bool(
                self.order_status() == "SUCCEEDED" and common
                and len(target_uids) == int(self.order.target_quantity))
            return acceptance
        if self.scenario == cfg.SCENARIO_FULL_FLOW:
            acceptance["ok"] = bool(
                self.queue.all_done() and common
                and len(finals) == expected_final
                and len(semi_wip) == expected_unpaired
                and all(rec.parents for rec in finals))
        elif self.scenario == cfg.SCENARIO_STAGGERED:
            acceptance["max_concurrent_tasks"] = self._max_concurrent_tasks()
            acceptance["interleaved"] = bool(
                acceptance["max_concurrent_tasks"]
                >= cfg.STAGGERED_MIN_CONCURRENT_TASKS)
            acceptance["ok"] = bool(
                self.queue.all_done() and common
                and len(finals) == expected_final
                and len(semi_wip) == expected_unpaired
                and all(rec.parents for rec in finals)
                and acceptance["interleaved"])
        elif self.scenario == cfg.SCENARIO_NO_PAIR:
            acceptance["ok"] = bool(
                self.queue.all_done() and common
                and len(finals) == expected_final
                and all(rec.parents for rec in finals))
        elif self.scenario == cfg.SCENARIO_AGV_PRIORITY:
            acceptance["no_preemption"] = self._no_preemption()
            acceptance["ok"] = bool(
                self.queue.all_done() and common
                and len(finals) == expected_final
                and acceptance["no_preemption"])
        elif self.scenario == cfg.SCENARIO_AGV_BLOCKED:
            blocked = [item for item in self.injected
                       if item["kind"] == "agv_blocked"]
            resumed = any(
                task.evidence.get("resumes")
                for task in self.queue.all())
            acceptance["blocked_observed"] = bool(blocked)
            acceptance["resumed_after_clear"] = bool(resumed)
            acceptance["blocked_never_done"] = all(
                task.status is not TaskStatus.DONE
                for task in self.queue.all()
                if task.status is TaskStatus.BLOCKED)
            acceptance["ok"] = bool(
                self.queue.all_done() and common
                and len(finals) == expected_final
                and acceptance["blocked_observed"]
                and acceptance["resumed_after_clear"])
        elif self.scenario == cfg.SCENARIO_CONVEYOR_FAULT:
            blocked = [task for task in tasks
                       if task.status is TaskStatus.BLOCKED]
            preserved = [
                rec for rec in self.flow.all()
                if rec.zone == cfg.CONVEYOR_FAULT_LINE
                and rec.state is not RecordState.CONSUMED]
            acceptance.update({
                "fault_observed": self._line_faulted,
                "affected_task_blocked": any(
                    self._task_consumes_line(
                        task, cfg.CONVEYOR_FAULT_LINE)
                    for task in blocked),
                "site_preserved": bool(preserved),
                "no_final": len(finals) == 0,
            })
            acceptance["ok"] = bool(
                self._line_faulted and self.frozen
                and acceptance["affected_task_blocked"]
                and acceptance["site_preserved"]
                and acceptance["no_final"])
        return acceptance

    def _scheduler_acceptance(self) -> tuple[bool, dict]:
        """核对派工决策可解释: 每个已派工工序都有选定设备与策略记录."""
        errors: list[str] = []
        decided: dict[str, dict] = {}
        for row in self.schedule_decisions:
            instance_id = str(row.get("operation_instance_id", ""))
            decided[instance_id] = row
            if row.get("policy_version") != SCHEDULER_POLICY_VERSION:
                errors.append(
                    f"{instance_id}: 策略版本 "
                    f"{row.get('policy_version')!r} != "
                    f"{SCHEDULER_POLICY_VERSION!r}")
            accepted = [candidate.get("device_id") for candidate
                        in row.get("candidates", [])
                        if candidate.get("accepted")]
            if row.get("selected_device_id") not in accepted:
                errors.append(
                    f"{instance_id}: 选定设备 "
                    f"{row.get('selected_device_id')!r} 不在通过候选 "
                    f"{accepted}")
            if row.get("execution_id") and \
                    row.get("resource_result") != "ACQUIRED":
                errors.append(
                    f"{instance_id}: 已启动但资源结果 "
                    f"{row.get('resource_result')!r} != ACQUIRED")
        checked = 0
        for instance in self.op_runtime.graph.instances():
            if not instance.run_id:
                continue
            try:
                template = self._template(instance.template_id)
            except Exception:
                continue
            if template.executor_kind == "logic" or not instance.dispatched:
                continue
            checked += 1
            if not instance.selected_device_id:
                errors.append(f"{instance.instance_id}: 已派工但无选定设备")
            elif instance.selected_device_id not in \
                    instance.candidate_device_ids:
                errors.append(
                    f"{instance.instance_id}: 选定设备 "
                    f"{instance.selected_device_id!r} 不在 recipe 候选 "
                    f"{instance.candidate_device_ids}")
            elif instance.instance_id not in decided:
                errors.append(
                    f"{instance.instance_id}: 缺少调度决策记录")
        return not errors, {
            "checked_instances": checked,
            "decisions": len(self.schedule_decisions),
            "waits": len(self.schedule_waits),
            "policy_version": SCHEDULER_POLICY_VERSION,
            "errors": errors,
        }

    def _priority_acceptance(self) -> tuple[bool, list[dict]]:
        rows = []
        ok = True
        for decision in self.dispatch_decisions:
            chosen = decision["chosen_priority"]
            others = [row["priority"] for row in decision["pending"]]
            row_ok = all(chosen <= value for value in others)
            ok = ok and row_ok
            rows.append({**decision, "ok": row_ok})
        return ok, rows

    def _no_preemption(self) -> bool:
        """运行中的运输任务不得被更高优先级任务打断."""
        if any(event.get("kind") in ("ROUTE_ABORTED", "ROUTE_PREEMPTED")
               for event in self.events):
            return False
        for row in self.agv_history:
            if row.get("spec", {}).get("loaded") and \
                    row.get("state") != RouteState.DONE.value:
                return False
        return True

    def _max_concurrent_tasks(self) -> int:
        """时间线上同时处于执行态的物料/AGV 任务数峰值."""
        intervals: list[tuple[float, float]] = []
        now = self._sim_time()
        for task in self.queue.all():
            if task.started_sim_time is None:
                continue
            end = (task.finished_sim_time
                   if task.finished_sim_time is not None else now)
            intervals.append((float(task.started_sim_time), float(end)))
        points: list[tuple[float, int]] = []
        for start, end in intervals:
            points.append((start, 1))
            points.append((end, -1))
        points.sort(key=lambda item: (item[0], -item[1]))
        current = 0
        peak = 0
        for _time, delta in points:
            current += delta
            peak = max(peak, current)
        return peak

    # ═══════════════════════════════════════════════════════
    # 报告
    # ═══════════════════════════════════════════════════════

    def summary(self) -> dict:
        from ..domain.metrics import recompute_metrics
        material_events = [event.as_dict() for event in self.flow.events]
        all_events = sorted([*self.events, *material_events],
                            key=lambda event: event.get("sim_time") or 0.0)
        metrics = recompute_metrics(
            all_events, wip=self.flow.wip())
        return {
            "scenario": self.scenario,
            "fault": self.fault,
            "sim_time": round(self._sim_time(), 4),
            "aborted": self.aborted,
            "frozen": self.frozen,
            "stop_reason": self.stop_reason,
            "order_status": self.order_status(),
            "failure_policy_version": FAILURE_POLICY_VERSION,
            "failures": list(self.failures),
            "failure_summary": self.op_runtime.failure_summary(),
            "batch": dict(self._batch),
            "batch_input_version": spec.BATCH_INPUT_VERSION,
            "source_counts": dict(self._source_count),
            "agv_at_dock": self.agv_at_dock,
            "order": None if self.order is None else self.order.as_dict(),
            "order_plan": (None if self.order_plan is None else {
                "run_id": self.order_plan.run_id,
                "digest": self.order_plan.digest,
                "instance_counts": dict(self.order_plan.instance_counts),
                "material_plans": [row.as_dict()
                                   for row in self.order_plan.material_plans],
            }),
            "process_model": {
                "version": self.catalog.version,
                "recipe_id": self.recipe.id,
                "recipe_version": self.recipe.version,
                "validation": self.process_report,
                "catalog": self.catalog.as_dict(),
            },
            "operations": self.op_runtime.as_dict(),
            "tasks": self.queue.as_dict(),
            "resources": self.resources.as_dict(),
            "resource_checks": self.resources.validate(),
            "controllers": {
                unit_id: {
                    "task_id": record.task_id,
                    "state": record.controller.state.name,
                    "failure_reason": record.controller.failure_reason,
                    "release_attempted": bool(
                        getattr(record.controller, "release_attempted", False)),
                }
                for unit_id, record in self.controllers.items()},
            "controller_history": list(self.controller_history),
            "routes": list(self.agv_history),
            "dispatch_decisions": list(self.dispatch_decisions),
            "scheduler": {
                "policy_version": SCHEDULER_POLICY_VERSION,
                "decisions": list(self.schedule_decisions),
                "waits": list(self.schedule_waits),
            },
            "injected": list(self.injected),
            "spawned": list(self.spawned),
            "materials": self.flow.snapshot(),
            "events": all_events,
            "metrics": metrics,
            "acceptance": self.acceptance(),
        }

    # ── 小工具 ──────────────────────────────────────────────

    def _event(self, kind: str, *, task_id: str = "",
               uid: Optional[int] = None, item_type: str = "",
               zone: str = "", source: str = "", destination: str = "",
               device: str = "", evidence_id: str = "",
               failure_scope: str = "", node_id: str = "",
               detail: Optional[dict] = None) -> None:
        self._evidence_seq += 1
        payload = dict(detail or {})
        if failure_scope:
            payload.setdefault("failure_scope", failure_scope)
        if node_id:
            payload.setdefault("node_id", node_id)
        self.events.append({
            "sim_time": round(self._sim_time(), 4),
            "kind": str(kind),
            "task_id": str(task_id),
            "uid": uid,
            "item_type": str(item_type),
            "zone": str(zone),
            "source": str(source),
            "destination": str(destination),
            "device": str(device),
            "evidence_id": str(evidence_id or f"ev{self._evidence_seq:05d}"),
            "failure_scope": str(failure_scope),
            "node_id": str(node_id),
            "detail": payload,
        })
        if self.logger:
            self.logger.info("[STAGE4 t=%.2f] %s %s uid=%s %s",
                             self._sim_time(), kind, task_id, uid,
                             payload or {})

    def _sim_time(self) -> float:
        try:
            return float(self.sim.getSimulationTime())
        except Exception:
            return 0.0


def set_name(source_key: str) -> str:
    return {"s1a": "S1:A", "s1b": "S1:B", "s2d": "S2:D"}.get(
        source_key, source_key)
