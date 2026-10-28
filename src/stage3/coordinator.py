"""coordinator.py — 阶段三任务编排、资源互斥与 AGV 调度 (06-12 §3.2–§3.5).

固定单步顺序 (CLAUDE.md §8, Python 始终是 clock master):
  1. 轮询任务/资源, 启动可执行任务;
  2. 所有活动工位控制器 `pre_tick()` (传感器/传送带/物料/IK/吸附);
  3. AGV 路线控制器下发轮速;
  4. 故障注入 (受控阻塞 / 不稳定载荷);
  5. `sim.step()` — 每步只推进一次;
  6. 控制器 `post_tick()` (步后联锁);
  7. 任务状态、交接证据、资源释放、下一任务触发.

任务链:
    conveyor → A 装载 AGV (dock A)
             → AGV 带载 A→B
             → B 从 AGV 卸载并放到 Station B
`route_baseline` 额外执行 B→A 空载回程, 用于双向停靠/滑移测量.

阶段三不自动重试/续作; 安全锁存后冻结原任务并停止, 不释放安全锁存.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from stage1 import config as base
from stage1 import physics as phys
from stage1.material_manager import MaterialState
from stage1.scene import alias_of
from stage1.unit_controller import UnitTaskState

from . import config as cfg
from .agv_route import AGVRouteController, RouteState, pose_yaw, route_specs
from .resources import ResourceTable
from .scene import Stage3Scene
from .tasks import (Stage3Task, TaskKind, TaskQueue, TaskStatus, make_task)
from .unit_controller import Stage3UnitController


class CoordinatorError(RuntimeError):
    code = "STAGE3_COORDINATOR"


@dataclass
class ControllerRecord:
    unit_id: str
    task_id: str
    controller: Stage3UnitController


class Stage3Coordinator:
    """阶段三唯一调度器; 不直接写车体/关节位姿."""

    def __init__(self, sim, scene: Stage3Scene, comp, *,
                 scenario: str = cfg.SCENARIO_CLOSED_LOOP,
                 fault: str = cfg.FAULT_NONE,
                 logger=None, on_controller=None) -> None:
        if scenario not in cfg.STAGE3_SCENARIOS:
            raise CoordinatorError(f"未知 scenario: {scenario!r}")
        self.sim = sim
        self.scene = scene
        self.comp = comp
        self.materials = comp["materials"]
        self.agv = comp["agv"]
        self.runtime = comp["runtime"]
        self.ik = comp["ik"]
        self.suction = comp["suction"]
        self.layout = scene.stage3_layout
        self.logger = logger
        self.on_controller = on_controller
        self.scenario = scenario
        self.fault = fault

        self.queue = TaskQueue()
        self.resources = ResourceTable()
        self.controllers: dict[str, ControllerRecord] = {}
        self.controller_history: list[dict] = []
        self.route: Optional[AGVRouteController] = None
        self.route_task: Optional[Stage3Task] = None
        self.route_specs = route_specs(self.layout)
        self.route_history: list[dict] = []
        self.events: list[dict] = []
        self.injected: list[dict] = []
        self.aborted = False
        self.stop_reason = ""
        self.started_at: Optional[float] = None
        self._abort_settle_steps = 0
        self._fault_handles: list[int] = []
        self._block_since: Optional[float] = None
        self._unstable_steps = 0
        self._return_submitted = False

    # ═══════════════════════════════════════════════════════
    # 初始化任务
    # ═══════════════════════════════════════════════════════

    def start(self, sim_time: float = 0.0) -> None:
        self.started_at = float(sim_time)
        self._submit(self._task(
            TaskKind.LOAD_A, uid=1, source="conveyor",
            destination="AGV@dock_A", dock="A", sim_time=sim_time))
        self._submit(self._task(
            TaskKind.TRANSPORT, uid=1, source="dock_A",
            destination="dock_B", dock="B", sim_time=sim_time))
        self._submit(self._task(
            TaskKind.UNLOAD_B, uid=1, source="AGV@dock_B",
            destination="Station_B", dock="B", sim_time=sim_time))
        if self.scenario == cfg.SCENARIO_CONTENTION:
            # 同一 UID 的重复请求必须被拒绝, 不得产生第二次吸附/派车.
            self._submit(self._task(
                TaskKind.LOAD_A, uid=1, source="conveyor",
                destination="AGV@dock_A", dock="A", sim_time=sim_time))
            self._submit(self._task(
                TaskKind.TRANSPORT, uid=1, source="dock_A",
                destination="dock_B", dock="B", sim_time=sim_time))
            self._submit(self._task(
                TaskKind.UNLOAD_B, uid=1, source="AGV@dock_B",
                destination="Station_B", dock="B", sim_time=sim_time))

    def _task(self, kind, *, uid, source, destination, dock,
              sim_time) -> Stage3Task:
        return make_task(kind, uid, source=source, destination=destination,
                         dock=dock, sim_time=sim_time)

    def _submit(self, task: Stage3Task) -> bool:
        ok, reason = self.queue.submit(task, self._sim_time())
        if not ok:
            self._event(f"TASK_REJECTED {task.task_id}: {reason}")
        else:
            self._event(f"TASK_SUBMIT {task.task_id} kind={task.kind.value} "
                        f"uid={task.uid}")
        return ok

    # ═══════════════════════════════════════════════════════
    # 单步
    # ═══════════════════════════════════════════════════════

    def step(self) -> None:
        sim_time = self._sim_time()
        if self.started_at is None:
            self.started_at = sim_time
        self._poll_tasks(sim_time)
        # 不稳定载荷必须在控制器 pre_tick 的联锁检查之前注入, 这样
        # Stage2Safety 会在 RELEASING 执行释放之前检出载荷漂移并锁存.
        self._maybe_inject_fault(sim_time)

        for record in list(self.controllers.values()):
            controller = record.controller
            if not controller.finished:
                controller.pre_tick()

        self._advance_route(sim_time)

        self.sim.step()

        # 即使控制器已 FAILED 也要执行 post_tick: 锁存后的停止确认依赖它.
        for record in list(self.controllers.values()):
            record.controller.post_tick()
        if self.aborted:
            self._abort_settle_steps += 1

        self._after_step(sim_time)

    # ═══════════════════════════════════════════════════════
    # 任务轮询/启动
    # ═══════════════════════════════════════════════════════

    def _poll_tasks(self, sim_time: float) -> None:
        if self.aborted or self.queue.any_failed():
            return
        load_a = self._by_kind(TaskKind.LOAD_A)
        transport = self._by_kind(TaskKind.TRANSPORT)
        unload_b = self._by_kind(TaskKind.UNLOAD_B)
        return_task = self._by_kind(TaskKind.RETURN)

        if load_a is not None and load_a.status in (
                TaskStatus.PENDING, TaskStatus.WAITING):
            if self._acquire(load_a, "pick_zone", sim_time, "load_A start") \
                    and self._acquire(load_a, "dock_A", sim_time,
                                      "load_A transfer") \
                    and self._acquire(load_a, "arm_A", sim_time,
                                      "load_A motion"):
                self._start_unit_task(load_a, "A", source="conveyor",
                                      sink="agv", dock="A", sim_time=sim_time)
            else:
                self._mark_waiting(load_a, sim_time, "load_A 资源等待")

        if (transport is not None
                and transport.status in (TaskStatus.PENDING,
                                         TaskStatus.WAITING)
                and load_a is not None
                and load_a.status is TaskStatus.DONE):
            if not self._verify_loaded_on_tray(sim_time):
                self._mark_waiting(transport, sim_time,
                                   "等待 A 装载交接证据")
            elif self._acquire(transport, "agv_lane", sim_time,
                               "A→B transit") \
                    and self._acquire(transport, "dock_B", sim_time,
                                      "B dock reservation"):
                self._start_route(transport, "A_to_B", sim_time)
            else:
                self._mark_waiting(transport, sim_time, "AGV 通道/dock 等待")

        if (unload_b is not None
                and unload_b.status in (TaskStatus.PENDING,
                                        TaskStatus.WAITING)
                and transport is not None
                and transport.status is TaskStatus.DONE):
            if not self._agv_at_dock("B", sim_time):
                self._mark_waiting(unload_b, sim_time, "等待 AGV 在 dock B 停稳")
            elif self._acquire(unload_b, "dock_B", sim_time,
                               "B unload") \
                    and self._acquire(unload_b, "arm_B", sim_time,
                                      "B motion"):
                self._start_unit_task(unload_b, "B", source="agv",
                                      sink="station", dock="B",
                                      sim_time=sim_time)
            else:
                self._mark_waiting(unload_b, sim_time, "B dock/arm 资源等待")
        elif (unload_b is not None
              and unload_b.status in (TaskStatus.PENDING,
                                      TaskStatus.WAITING)
              and transport is not None
              and transport.status is TaskStatus.RUNNING
              and self.resources.owner("dock_B") == self._owner(transport)):
            # B 工位在 AGV 运输途中就提交 dock 请求; 记录真实等待, 但不
            # 提前获取资源 (否则会与运输任务形成死锁).
            self.resources.note_wait(
                "dock_B", self._owner(unload_b), unload_b.task_id, sim_time,
                "AGV 运输任务持有 dock_B 预留")
            self._mark_waiting(unload_b, sim_time,
                               "等待 AGV 到达 dock B 并释放预留")

        if (return_task is not None
                and return_task.status in (TaskStatus.PENDING,
                                           TaskStatus.WAITING)
                and unload_b is not None
                and unload_b.status is TaskStatus.DONE):
            if self._acquire(return_task, "agv_lane", sim_time,
                             "B→A return") \
                    and self._acquire(return_task, "dock_A", sim_time,
                                      "A dock reservation"):
                self._start_route(return_task, "B_to_A", sim_time)
            else:
                self._mark_waiting(return_task, sim_time,
                                   "回程通道/dock 等待")

    def _by_kind(self, kind: TaskKind) -> Optional[Stage3Task]:
        tasks = self.queue.by_kind(kind)
        return tasks[0] if tasks else None

    def _mark_waiting(self, task: Stage3Task, sim_time: float,
                      reason: str) -> None:
        if task.status is TaskStatus.PENDING:
            self.queue.wait(task.task_id, sim_time, reason)

    def _start_unit_task(self, task: Stage3Task, unit_id: str, *,
                         source: str, sink: str, dock: str,
                         sim_time: float) -> None:
        if unit_id in self.controllers:
            return
        view = self.runtime.arm(unit_id).view
        try:
            controller = Stage3UnitController(
                sim=self.sim, scene=view, materials=self.materials,
                conveyor=self.comp["conveyor"],
                belt_geometry=self.comp["geometry"],
                ik=self.ik[unit_id], suction=self.suction[unit_id],
                agv=self.agv, target=sink, uid=task.uid,
                fault=cfg.FAULT_NONE, layout=view.layout,
                logger=self.logger, runtime=self.runtime.arm(unit_id),
                unit_id=unit_id, source=source, sink=sink, dock_id=dock,
                resources=self.resources, task=task)
        except Exception as exc:
            self._release_task(task, sim_time, "controller build failed")
            self.queue.fail(task.task_id, sim_time,
                            f"CONTROLLER_BUILD_FAILED: "
                            f"{type(exc).__name__}: {exc}")
            self._abort(f"{unit_id} 控制器构建失败: {exc}")
            return
        self.queue.start(task.task_id, sim_time)
        self.controllers[unit_id] = ControllerRecord(
            unit_id=unit_id, task_id=task.task_id, controller=controller)
        task.phase = controller.state.name
        self._event(f"TASK_START {task.task_id} unit={unit_id} "
                    f"source={source} sink={sink} dock={dock}")
        if self.on_controller is not None:
            try:
                self.on_controller(unit_id, controller)
            except Exception:
                pass

    def _start_route(self, task: Stage3Task, spec_name: str,
                     sim_time: float) -> None:
        spec = self.route_specs[spec_name]
        if spec_name == "B_to_A":
            spec = type(spec)(route_id=spec.route_id, start=spec.start,
                              end=spec.end, heading=spec.heading,
                              direction=spec.direction, speed=spec.speed,
                              loaded=False)
        try:
            self.route = AGVRouteController(
                self.sim, self.agv, spec, material_manager=self.materials,
                uid=task.uid if spec.loaded else None, logger=self.logger)
        except Exception as exc:
            self._release_task(task, sim_time, "route build failed")
            self.queue.fail(task.task_id, sim_time,
                            f"ROUTE_BUILD_FAILED: {type(exc).__name__}: {exc}")
            self._abort(f"AGV 路线构建失败: {exc}")
            return
        self.route_task = task
        self.queue.start(task.task_id, sim_time)
        task.phase = f"ROUTE {spec.route_id}"
        self._event(f"TASK_START {task.task_id} route={spec.route_id} "
                    f"loaded={spec.loaded} direction={spec.direction:+d}")
        if self.fault == cfg.FAULT_AGV_BLOCKED and spec.loaded:
            self._inject_agv_obstacle()

    # ═══════════════════════════════════════════════════════
    # AGV 路线
    # ═══════════════════════════════════════════════════════

    def _advance_route(self, sim_time: float) -> None:
        if self.route is None:
            return
        if self.route_task is not None:
            self.route_task.phase = self.route.state.value
        self.route.step(sim_time)

    def _after_route(self, sim_time: float) -> None:
        route = self.route
        task = self.route_task
        if route is None or task is None:
            return
        if route.state is RouteState.DONE:
            evidence = self._route_evidence(route)
            ok, reason = self._verify_route(task, route)
            if ok:
                self.queue.done(task.task_id, sim_time, evidence)
                self._event(f"TASK_DONE {task.task_id}: {reason}")
            else:
                self.queue.fail(task.task_id, sim_time, reason, evidence)
                self._abort(reason)
            self.route_history.append(route.summary())
            self._release_task(task, sim_time, "route complete")
            self.route = None
            self.route_task = None
            if (task.kind is TaskKind.TRANSPORT
                    and self.scenario == cfg.SCENARIO_ROUTE_BASELINE
                    and not self._return_submitted):
                self._return_submitted = True
                self._submit(self._task(
                    TaskKind.RETURN, uid=task.uid, source="dock_B",
                    destination="dock_A", dock="A", sim_time=sim_time))
            return
        if route.state is RouteState.FAILED:
            self.queue.fail(task.task_id, sim_time,
                            f"AGV_ROUTE_FAILED: {route.failure_reason}",
                            self._route_evidence(route))
            self.route_history.append(route.summary())
            self._release_task(task, sim_time, "route failed")
            self.route = None
            self.route_task = None
            self._abort(f"AGV_ROUTE_FAILED: {route.failure_reason}")
            return
        if route.state is RouteState.BLOCKED:
            if task.status is not TaskStatus.BLOCKED:
                self.queue.block(task.task_id, sim_time,
                                 "AGV 路线受阻, 轮速已停止")
                self._block_since = sim_time
                self._event(f"AGV_BLOCKED {task.task_id}: 保持停车与资源所有权")
            if self._block_since is None:
                self._block_since = sim_time
            if (sim_time - self._block_since
                    >= cfg.AGV_BLOCK_CLEAR_AFTER_S):
                self._clear_agv_obstacle_and_resume(sim_time)
                if route.state is not RouteState.BLOCKED:
                    return
            if (self._block_since is not None
                    and sim_time - self._block_since
                    > cfg.AGV_ROUTE_TIMEOUT):
                self.queue.fail(task.task_id, sim_time,
                                "AGV_BLOCKED_TIMEOUT: 清障后仍未续行",
                                self._route_evidence(route))
                self._abort("AGV_BLOCKED_TIMEOUT")

    def _clear_agv_obstacle_and_resume(self, sim_time: float) -> None:
        route = self.route
        task = self.route_task
        if route is None or task is None:
            return
        removed = self._remove_agv_obstacle()
        clearance_ok = self._route_clearance_ok()
        self._event(
            f"AGV_BLOCK_CLEAR: removed={removed} clearance_ok={clearance_ok} "
            f"owner={self.resources.owner('agv_lane')}")
        if not clearance_ok:
            return
        if route.resume(sim_time, clearance_ok=True,
                        reason="obstacle_removed_and_ownership_reconfirmed"):
            self.queue.unblock(task.task_id, sim_time,
                               "障碍清除, 现场/资源所有权重新确认")
            self._block_since = None

    def _route_clearance_ok(self) -> bool:
        if any(arm.safety.engaged for arm in self.runtime.arms.values()):
            return False
        if self._fault_handles:
            return False
        owner = self.resources.owner("agv_lane")
        if self.route_task is None or owner is None:
            return False
        return owner == self._owner(self.route_task)

    def _verify_route(self, task: Stage3Task,
                      route: AGVRouteController) -> tuple[bool, str]:
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

    def _route_evidence(self, route: AGVRouteController) -> dict:
        return {
            "route": route.summary(),
            "material_state": self._material_state_dict(route.uid),
        }

    # ═══════════════════════════════════════════════════════
    # 控制器收尾
    # ═══════════════════════════════════════════════════════

    def _after_step(self, sim_time: float) -> None:
        if self.aborted:
            return
        for unit_id, record in list(self.controllers.items()):
            controller = record.controller
            task = self.queue.get(record.task_id)
            if task is None:
                continue
            task.phase = controller.state.name
            if controller.safety.engaged:
                self.queue.fail(
                    task.task_id, sim_time,
                    f"SAFETY_LATCH: {controller.safety.latch.reason}",
                    {"safety": controller.safety.latch.as_dict(),
                     "controller": controller.summary()})
                self._abort(f"{unit_id} 安全锁存: "
                            f"{controller.safety.latch.reason}")
                return
            if controller.state is UnitTaskState.FAILED:
                self.queue.fail(task.task_id, sim_time,
                                controller.failure_reason or "UNIT_FAILED",
                                {"controller": controller.summary()})
                self._abort(f"{unit_id} 任务失败: "
                            f"{controller.failure_reason}")
                return
            if controller.finished:
                ok, reason, evidence = self._verify_unit_task(task, controller)
                if ok:
                    self.queue.done(task.task_id, sim_time, evidence)
                    self._event(f"TASK_DONE {task.task_id}: {reason}")
                else:
                    self.queue.fail(task.task_id, sim_time, reason, evidence)
                    self._abort(reason)
                self._release_task(task, sim_time, "unit task complete")
                self.controller_history.append({
                    "unit_id": unit_id,
                    "task_id": task.task_id,
                    "summary": controller.summary(),
                })
                self.controllers.pop(unit_id, None)
            else:
                self._maybe_release_pick_zone(controller, sim_time)

        self._after_route(sim_time)

    def _verify_unit_task(self, task: Stage3Task,
                          controller: Stage3UnitController
                          ) -> tuple[bool, str, dict]:
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

        if task.kind is TaskKind.LOAD_A:
            if rec.state not in (MaterialState.ON_TRAY,
                                 MaterialState.RELEASED):
                return False, (f"LOAD_HANDOFF_STATE: {rec.state.value} "
                               f"not on AGV tray"), evidence
            check = self._tray_check(rec.sim_handle)
            evidence["tray_check"] = check
            if not check["ok"]:
                return False, f"LOAD_NOT_ON_TRAY: {check['detail']}", evidence
            if not self._material_physics_ok(rec.sim_handle):
                return False, "LOAD_PHYSICS_NOT_RESTORED", evidence
            return True, "A 装载交接确认 (UID/载台/物理属性)", evidence

        if task.kind is TaskKind.UNLOAD_B:
            expected = self.layout.unit("B").station_material_pose
            error = math.dist(pose[:3], expected) if pose else float("inf")
            evidence["place_error"] = error
            if rec.state is not MaterialState.RELEASED:
                return False, f"UNLOAD_STATE: {rec.state.value} != RELEASED", \
                    evidence
            if error > cfg.MATERIAL_PLACE_TOL:
                return False, (f"PLACE_ERROR: {error:.4f} > "
                               f"{cfg.MATERIAL_PLACE_TOL}"), evidence
            if not self._material_physics_ok(rec.sim_handle):
                return False, "UNLOAD_PHYSICS_NOT_RESTORED", evidence
            return True, "B 卸载/放置完成 (UID/位置/物理属性)", evidence

        return False, f"UNEXPECTED_TASK_KIND: {task.kind.value}", evidence

    def _maybe_release_pick_zone(self, controller: Stage3UnitController,
                                 sim_time: float) -> None:
        if controller.unit_id != "A":
            return
        owner = self._owner(controller.task)
        if self.resources.owner("pick_zone") != owner:
            return
        if controller.state not in (UnitTaskState.TRANSFERRING,
                                    UnitTaskState.PLACING,
                                    UnitTaskState.RELEASING,
                                    UnitTaskState.SETTLING,
                                    UnitTaskState.DONE):
            return
        try:
            tcp = list(self.ik["A"].tcp_pose())
        except Exception:
            return
        rect = self.layout.conveyor_rect
        margin = cfg.PICK_ZONE_RELEASE_MARGIN
        outside = (tcp[0] < rect[0] - margin or tcp[0] > rect[2] + margin
                   or tcp[1] < rect[1] - margin or tcp[1] > rect[3] + margin)
        if outside:
            self.resources.release("pick_zone", owner, sim_time,
                                   "TCP 已退出共享取料区")
            self._event("RESOURCE_RELEASE pick_zone: TCP 已退出 conveyor envelope")

    # ═══════════════════════════════════════════════════════
    # 故障注入
    # ═══════════════════════════════════════════════════════

    def _maybe_inject_fault(self, sim_time: float) -> None:
        if self.fault == cfg.FAULT_UNSTABLE_UNLOAD:
            self._maybe_inject_unstable_load(sim_time)

    def _maybe_inject_unstable_load(self, sim_time: float) -> None:
        if any(item["kind"] == "unstable_unload"
               for item in self.injected):
            return
        record = self.controllers.get("B")
        if (record is None
                or record.controller.state.name != cfg.UNSTABLE_INJECT_PHASE):
            self._unstable_steps = 0
            return
        self._unstable_steps += 1
        if self._unstable_steps < cfg.UNSTABLE_INJECT_DELAY_STEPS:
            return
        task = record.controller.task
        rec = self.materials.by_uid(task.uid)
        if rec is None or rec.sim_handle is None:
            return
        try:
            pose = list(self.sim.getObjectPose(rec.sim_handle,
                                               self.sim.handle_world))
            offset = list(cfg.UNSTABLE_LOAD_OFFSET)
            pose[0] += offset[0]
            pose[1] += offset[1]
            pose[2] += offset[2]
            self.sim.setObjectPose(rec.sim_handle, pose, self.sim.handle_world)
        except Exception as exc:
            self._event(f"FAULT_INJECT_FAILED unstable_unload: {exc}")
            return
        self.injected.append({
            "kind": "unstable_unload",
            "sim_time": round(float(sim_time), 4),
            "offset": offset,
            "material_pose": [round(float(v), 6) for v in pose],
            "phase": record.controller.state.name,
        })
        self._event(f"FAULT_INJECT unstable_unload: 载荷偏移 {offset}")

    def _inject_agv_obstacle(self) -> None:
        try:
            handle = int(self.sim.createPrimitiveShape(
                self.sim.primitiveshape_cuboid,
                list(cfg.AGV_BLOCK_OBSTACLE_SIZE)))
            self.sim.setObjectAlias(handle, cfg.AGV_BLOCK_OBSTACLE_ALIAS)
            self.sim.setObjectPosition(handle,
                                       list(cfg.AGV_BLOCK_OBSTACLE_POS),
                                       self.sim.handle_world)
            phys.apply_static_shape(self.sim, handle, respondable=True)
            self._fault_handles.append(handle)
            self.runtime.add_hazard(handle)
            self.injected.append({
                "kind": "agv_blocked",
                "sim_time": round(self._sim_time(), 4),
                "handle": handle,
                "alias": cfg.AGV_BLOCK_OBSTACLE_ALIAS,
                "position": list(cfg.AGV_BLOCK_OBSTACLE_POS),
            })
            self._event(f"FAULT_INJECT agv_blocked: {cfg.AGV_BLOCK_OBSTACLE_ALIAS}")
        except Exception as exc:
            self._event(f"FAULT_INJECT_FAILED agv_blocked: {exc}")

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
        removed = not remaining
        self.injected.append({
            "kind": "agv_unblock",
            "sim_time": round(self._sim_time(), 4),
            "removed": removed,
            "remaining": list(remaining),
        })
        return removed

    # ═══════════════════════════════════════════════════════
    # 资源
    # ═══════════════════════════════════════════════════════

    @staticmethod
    def _owner(task: Stage3Task) -> str:
        if task.kind in (TaskKind.LOAD_A, TaskKind.UNLOAD_B):
            unit = "A" if task.kind is TaskKind.LOAD_A else "B"
            return f"unit_{unit}/task_{task.task_id}"
        return f"agv/task_{task.task_id}"

    def _acquire(self, task: Stage3Task, resource: str,
                 sim_time: float, reason: str) -> bool:
        owner = self._owner(task)
        ok = self.resources.acquire(resource, owner, task.task_id,
                                    sim_time, reason)
        if ok and resource not in task.resources:
            task.resources.append(resource)
        return ok

    def _release_task(self, task: Stage3Task, sim_time: float,
                      reason: str) -> None:
        owner = self._owner(task)
        self.resources.release_all(owner, sim_time, reason)

    def _release_all(self, sim_time: float, reason: str) -> None:
        for owner in sorted(set(self.resources.owners.values())):
            self.resources.release_all(owner, sim_time, reason)

    # ═══════════════════════════════════════════════════════
    # 中止 / 完成
    # ═══════════════════════════════════════════════════════

    def _abort(self, reason: str) -> None:
        if self.aborted:
            return
        self.aborted = True
        self.stop_reason = str(reason)
        self._abort_settle_steps = 0
        self._event(f"ABORT: {reason}")
        try:
            self.agv.stop()
        except Exception:
            pass
        for record in list(self.controllers.values()):
            controller = record.controller
            if controller.state not in (UnitTaskState.DONE,
                                        UnitTaskState.FAILED):
                try:
                    controller._hold_after_latch()
                except Exception:
                    pass
                try:
                    controller._fail(f"STAGE3_ABORT: {reason}")
                except Exception:
                    pass
        if self.route is not None:
            self.route.agv.stop()
        sim_time = self._sim_time()
        for task in self.queue.all():
            if task.status.active:
                self.queue.fail(task.task_id, sim_time,
                                f"STAGE3_ABORT: {reason}")
        self._release_all(sim_time, "abort")
        if self.scenario != cfg.SCENARIO_UNSTABLE_UNLOAD:
            self._remove_agv_obstacle()

    @property
    def finished(self) -> bool:
        if self.aborted:
            # 锁存后至少再推进若干步, 让 post_tick 读取真实带速/车体速度
            # 并确认停止; 不重试、不恢复任务.
            if self._abort_settle_steps >= cfg.SAFETY_STOP_SETTLE_STEPS:
                return True
            latched = [arm for arm in self.runtime.arms.values()
                       if arm.safety.engaged]
            if latched and all(arm.safety.latch.stop_confirmed
                               for arm in latched):
                return True
            if not latched:
                return True
            return False
        return self.queue.finished() and self.route is None \
            and not self.controllers

    # ═══════════════════════════════════════════════════════
    # 校验辅助
    # ═══════════════════════════════════════════════════════

    def _verify_loaded_on_tray(self, sim_time: float) -> bool:
        task = self._by_kind(TaskKind.LOAD_A)
        if task is None or task.status is not TaskStatus.DONE:
            return False
        uid = task.uid
        if not self._material_on_tray(uid):
            return False
        rec = self.materials.by_uid(uid)
        return rec is not None and rec.state in (
            MaterialState.ON_TRAY, MaterialState.RELEASED)

    def _agv_at_dock(self, dock: str, sim_time: float) -> bool:
        unit_id = "A" if dock == "A" else "B"
        if not self.scene.agv_dock_hit_at(unit_id, self.scene.agv_root):
            return False
        if not self.agv.is_stopped() or not self.agv.is_upright():
            return False
        pose = self.agv.pose()
        target = self.layout.unit(unit_id).dock
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

    def _material_state_dict(self, uid: Optional[int]) -> dict:
        if uid is None:
            return {}
        rec = self.materials.by_uid(uid)
        if rec is None:
            return {"uid": uid, "registered": False}
        return {
            "uid": uid,
            "alias": rec.alias,
            "state": rec.state.value,
            "handle": rec.sim_handle,
            "parent": (None if rec.parent_handle is None
                       else int(rec.parent_handle)),
            "pose": (None if rec.sim_handle is None
                     else self._material_pose(rec.sim_handle)),
        }

    # ═══════════════════════════════════════════════════════
    # 验收
    # ═══════════════════════════════════════════════════════

    def acceptance(self) -> dict:
        tasks = {task.kind.value: task for task in self.queue.all()}
        load = tasks.get(TaskKind.LOAD_A.value)
        transport = tasks.get(TaskKind.TRANSPORT.value)
        unload = tasks.get(TaskKind.UNLOAD_B.value)
        return_task = tasks.get(TaskKind.RETURN.value)

        handoff_ok = bool(
            load and load.status is TaskStatus.DONE
            and transport and transport.status is TaskStatus.DONE
            and unload and unload.status is TaskStatus.DONE)
        safety = {unit_id: arm.safety.latch.as_dict()
                  for unit_id, arm in self.runtime.arms.items()}
        any_latch = any(item.get("engaged") for item in safety.values())
        safety_stop_confirmed = all(
            (not arm.safety.engaged) or arm.safety.latch.stop_confirmed
            for arm in self.runtime.arms.values())
        resources_released = all(
            owner is None for owner in self.resources.owners.values())
        resource_waits = [event.as_dict() for event in self.resources.events
                          if event.action == "WAIT"]
        uid_consistent = all(
            task.uid == 1 for task in self.queue.all()) and \
            len({task.uid for task in self.queue.all()}) == 1

        current = self._material_state_dict(1)
        acceptance = {
            "scenario": self.scenario,
            "fault": self.fault,
            "tasks_done": self.queue.all_done(),
            "handoff_ok": handoff_ok,
            "uid_consistent": uid_consistent,
            "any_safety_latch": any_latch,
            "safety_stop_confirmed": safety_stop_confirmed,
            "safety": safety,
            "resources_released": resources_released,
            "aborted": self.aborted,
            "stop_reason": self.stop_reason,
            "material": current,
            "duplicate_rejections": len(self.queue.rejections),
            "resource_waits": resource_waits,
            "injected": list(self.injected),
        }

        if self.scenario == cfg.SCENARIO_CONTENTION:
            acceptance["duplicate_rejected"] = len(self.queue.rejections) >= 3
            acceptance["resource_wait_recorded"] = len(resource_waits) >= 1
            acceptance["ok"] = bool(
                acceptance["tasks_done"] and handoff_ok and uid_consistent
                and not any_latch and resources_released
                and acceptance["duplicate_rejected"]
                and acceptance["resource_wait_recorded"])

        elif self.scenario == cfg.SCENARIO_BLOCKED:
            blocked = [item for item in self.injected
                       if item["kind"] == "agv_blocked"]
            resumed = any("resumes" in (task.evidence or {})
                          for task in self.queue.all())
            route = (self.route_history[0] if self.route_history else {})
            block = route.get("metrics", {}).get("blocked", {})
            acceptance["blocked_observed"] = bool(blocked) and bool(block)
            acceptance["resumed_after_clear"] = bool(
                resumed or route.get("metrics", {}).get("resumed"))
            acceptance["ok"] = bool(
                acceptance["tasks_done"] and handoff_ok and uid_consistent
                and not any_latch and resources_released
                and acceptance["blocked_observed"]
                and acceptance["resumed_after_clear"])

        elif self.scenario == cfg.SCENARIO_UNSTABLE_UNLOAD:
            rec = self.materials.by_uid(1)
            still_attached = bool(
                rec is not None and rec.state is MaterialState.HELD_BY_SUCTION)
            not_placed = bool(
                rec is None or rec.state is not MaterialState.RELEASED)
            unload_failed = bool(unload is not None
                                 and unload.status is TaskStatus.FAILED)
            acceptance.update({
                "unload_failed": unload_failed,
                "material_still_attached": still_attached,
                "material_not_placed": not_placed,
                "release_attempted": bool(
                    unload and unload.evidence.get("controller", {})
                    .get("release_attempted")),
            })
            acceptance["ok"] = bool(
                unload_failed and still_attached and not_placed
                and any_latch and safety_stop_confirmed)

        elif self.scenario == cfg.SCENARIO_ROUTE_BASELINE:
            routes = [row.get("metrics", {}) for row in self.route_history]
            route_ok = bool(len(routes) >= 2 and all(
                (m.get("stop_error") is not None
                 and m["stop_error"] <= cfg.AGV_ROUTE_ARRIVE_TOL
                 and abs(m.get("heading_error") or 0.0)
                 <= cfg.AGV_ROUTE_HEADING_TOL) for m in routes))
            acceptance["return_done"] = bool(
                return_task is not None
                and return_task.status is TaskStatus.DONE)
            acceptance["route_baseline_ok"] = route_ok
            acceptance["ok"] = bool(
                acceptance["tasks_done"] and handoff_ok and uid_consistent
                and not any_latch and resources_released
                and acceptance["return_done"] and route_ok)

        else:  # closed_loop
            acceptance["ok"] = bool(
                acceptance["tasks_done"] and handoff_ok and uid_consistent
                and not any_latch and resources_released
                and not self.aborted)

        return acceptance

    # ═══════════════════════════════════════════════════════
    # 报告
    # ═══════════════════════════════════════════════════════

    def summary(self) -> dict:
        return {
            "scenario": self.scenario,
            "fault": self.fault,
            "sim_time": round(self._sim_time(), 4),
            "aborted": self.aborted,
            "stop_reason": self.stop_reason,
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
            "routes": list(self.route_history),
            "injected": list(self.injected),
            "events": list(self.events),
            "acceptance": self.acceptance(),
        }

    # ── 小工具 ──────────────────────────────────────────────

    def _event(self, message: str) -> None:
        self.events.append({
            "sim_time": round(self._sim_time(), 4),
            "message": str(message),
        })
        if self.logger:
            self.logger.info("[STAGE3 t=%.2f] %s", self._sim_time(), message)

    def _sim_time(self) -> float:
        try:
            return float(self.sim.getSimulationTime())
        except Exception:
            return 0.0
