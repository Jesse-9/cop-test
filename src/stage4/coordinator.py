"""coordinator.py — 阶段四多岛任务编排、AGV 调度、配对与背压 (06-16 §F4-4).

固定单步顺序 (CLAUDE.md §8, Python 始终是 clock master):
  1. 来源生成 (有限批次, 区域未满才生成);
  2. 任务轮询: 单元任务一次性申请资源; AGV 调度按优先级选下一段路线;
  3. 故障注入 (AGV 阻塞 / 传送带停带);
  4. 所有活动单元控制器 `pre_tick()`;
  5. AGV 航线 `step()` (轮速);
  6. `sim.step()` — 每步只推进一次;
  7. 单元 `post_tick()` (步后联锁);
  8. 任务完成校验、物料区域/类型提交、E 岛配对、资源释放、指标事件.

任务链 (06-16 §1):
    S1→A ─► A ─固定交接─► C ─C→E─► E ┐
    S1→B ─► B ─AGV B→A─► A ────────┤
    S2→D ─► D ─AGV D→E──────────────┴─► E 配对 → FINAL

阶段四不自动重试/续作; 安全锁存、航线失败或传送带停带故障后冻结现场,
任务不得改写成 DONE. 统一复位仍属最终故障治理阶段.
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

from stage3.agv_route import RouteState
from stage3.unit_controller import Stage3UnitController

from . import config as cfg
from .agv_map import AGVRouteController4, make_route_spec, pose_yaw
from .layout import UnitSpec4, handoff_park_tcp, line_specs, zones_for_unit
from .materials import (ItemType, MaterialFlowError, MaterialRegistry,
                        RecordState)
from .resources import ResourceTable
from .scene import Stage4Scene
from .tasks import (Stage4Task, TaskQueue, TaskStatus, make_task)
from .unit_controller import (SinkSpec, Stage4UnitController, TaskLayout,
                              agv_material_position)


class CoordinatorError(RuntimeError):
    code = "STAGE4_COORDINATOR"


# ═══════════════════════════════════════════════════════════════
# 任务 → 工位/资源/目标映射
# ═══════════════════════════════════════════════════════════════

UNIT_TASKS = {
    "A": (cfg.TASK_KIND_A_AGV, cfg.TASK_KIND_A_CONVEYOR),
    "B": (cfg.TASK_KIND_B,),
    "C": (cfg.TASK_KIND_C,),
    "D": (cfg.TASK_KIND_D,),
    "E": (cfg.TASK_KIND_E_AGV, cfg.TASK_KIND_E_CONVEYOR),
}

TASK_UNIT = {kind: unit_id for unit_id, kinds in UNIT_TASKS.items()
             for kind in kinds}

TASK_RESOURCES = {
    cfg.TASK_KIND_A_CONVEYOR: ("line_s1a", "handoff_ac", "zone_ab",
                                "zone_ac", "arm_A"),
    cfg.TASK_KIND_A_AGV: ("handoff_ac", "dock_A", "zone_ab", "zone_ac",
                           "arm_A"),
    cfg.TASK_KIND_B: ("line_s1b", "dock_B", "zone_ab", "arm_B"),
    cfg.TASK_KIND_C: ("line_ce", "handoff_ac", "zone_ac", "arm_C"),
    cfg.TASK_KIND_D: ("line_s2d", "dock_D", "arm_D"),
    cfg.TASK_KIND_E_CONVEYOR: ("line_ce", "buffer_e_c", "arm_E"),
    cfg.TASK_KIND_E_AGV: ("buffer_e_d", "dock_E", "arm_E"),
}

TASK_TYPES = {
    cfg.TASK_KIND_A_CONVEYOR: (ItemType.RAW_A,),
    cfg.TASK_KIND_A_AGV: (ItemType.SEMI_AB,),
    cfg.TASK_KIND_B: (ItemType.RAW_B,),
    cfg.TASK_KIND_C: (ItemType.SEMI_AC,),
    cfg.TASK_KIND_D: (ItemType.RAW_D,),
    cfg.TASK_KIND_E_CONVEYOR: (ItemType.SEMI_CE,),
    cfg.TASK_KIND_E_AGV: (ItemType.PART_DE,),
}

TASK_SOURCE = {
    cfg.TASK_KIND_A_CONVEYOR: "conveyor",
    cfg.TASK_KIND_A_AGV: "agv",
    cfg.TASK_KIND_B: "conveyor",
    cfg.TASK_KIND_C: "station",
    cfg.TASK_KIND_D: "conveyor",
    cfg.TASK_KIND_E_CONVEYOR: "conveyor",
    cfg.TASK_KIND_E_AGV: "agv",
}

TASK_SINK = {
    cfg.TASK_KIND_A_CONVEYOR: "station",
    cfg.TASK_KIND_A_AGV: "station",
    cfg.TASK_KIND_B: "agv",
    cfg.TASK_KIND_C: "conveyor",
    cfg.TASK_KIND_D: "agv",
    cfg.TASK_KIND_E_CONVEYOR: "station",
    cfg.TASK_KIND_E_AGV: "station",
}

TASK_DOCK = {
    cfg.TASK_KIND_A_CONVEYOR: "A",
    cfg.TASK_KIND_A_AGV: "A",
    cfg.TASK_KIND_B: "B",
    cfg.TASK_KIND_C: "",
    cfg.TASK_KIND_D: "D",
    cfg.TASK_KIND_E_CONVEYOR: "E",
    cfg.TASK_KIND_E_AGV: "E",
}

UNIT_LINE = {
    "A": "line_s1a",
    "B": "line_s1b",
    "C": "line_ce",
    "D": "line_s2d",
    "E": "line_ce",
}

# 完成任务后提交的下一道任务 (kind → (next_kind, uid 来源))
FOLLOWUP = {
    cfg.TASK_KIND_A_CONVEYOR: cfg.TASK_KIND_C,
    cfg.TASK_KIND_A_AGV: cfg.TASK_KIND_C,
    cfg.TASK_KIND_B: cfg.TASK_KIND_TRANSPORT_BA,
    cfg.TASK_KIND_C: cfg.TASK_KIND_E_CONVEYOR,
    cfg.TASK_KIND_D: cfg.TASK_KIND_TRANSPORT_DE,
    cfg.TASK_KIND_TRANSPORT_BA: cfg.TASK_KIND_A_AGV,
    cfg.TASK_KIND_TRANSPORT_DE: cfg.TASK_KIND_E_AGV,
}

SOURCE_KEYS = ("s1a", "s1b", "s2d")
SOURCE_RAW = {"s1a": "RAW_A", "s1b": "RAW_B", "s2d": "RAW_D"}
SOURCE_INITIAL_TASK = {
    "s1a": cfg.TASK_KIND_A_CONVEYOR,
    "s1b": cfg.TASK_KIND_B,
    "s2d": cfg.TASK_KIND_D,
}


TASK_SOURCE_ZONE = {
    cfg.TASK_KIND_A_CONVEYOR: "line_s1a",
    cfg.TASK_KIND_A_AGV: "agv",
    cfg.TASK_KIND_B: "line_s1b",
    cfg.TASK_KIND_C: "handoff_ac",
    cfg.TASK_KIND_D: "line_s2d",
    cfg.TASK_KIND_E_CONVEYOR: "line_ce",
    cfg.TASK_KIND_E_AGV: "agv",
}

TASK_DEST_ZONE = {
    cfg.TASK_KIND_A_CONVEYOR: "handoff_ac",
    cfg.TASK_KIND_A_AGV: "handoff_ac",
    cfg.TASK_KIND_B: "agv",
    cfg.TASK_KIND_C: "line_ce",
    cfg.TASK_KIND_D: "agv",
    cfg.TASK_KIND_E_CONVEYOR: "buffer_e_c",
    cfg.TASK_KIND_E_AGV: "buffer_e_d",
}


@dataclass
class ControllerRecord:
    unit_id: str
    task_id: str
    controller: Stage4UnitController


class Stage4Coordinator:
    """阶段四唯一调度器; 不直接写车体/关节位姿."""

    def __init__(self, sim, scene: Stage4Scene, comp, *,
                 scenario: str = cfg.SCENARIO_FULL_FLOW,
                 fault: str = cfg.FAULT_NONE,
                 logger=None, on_controller=None) -> None:
        if scenario not in cfg.STAGE4_SCENARIOS:
            raise CoordinatorError(f"未知 scenario: {scenario!r}")
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

        self.queue = TaskQueue()
        self.resources = ResourceTable()
        self.controllers: dict[str, ControllerRecord] = {}
        self.controller_history: list[dict] = []
        self.route: Optional[AGVRouteController4] = None
        self.route_task: Optional[Stage4Task] = None
        self.agv_at_dock = cfg.AGV_INITIAL_DOCK
        self.agv_history: list[dict] = []
        self.dispatch_decisions: list[dict] = []
        self.events: list[dict] = []
        self.injected: list[dict] = []
        self.spawned: list[dict] = []
        self.final_count = 0
        self.aborted = False
        self.frozen = False
        self.stop_reason = ""
        self.started_at: Optional[float] = None
        self._abort_settle_steps = 0
        self._frozen_since: Optional[float] = None
        self._fault_handles: list[int] = []
        self._block_since: Optional[float] = None
        self._line_faulted = False
        self._unstable_steps = 0
        self._next_uid = 1
        self._batch = dict(cfg.SCENARIO_BATCH[scenario])
        self._source_count = {key: 0 for key in SOURCE_KEYS}
        self._source_last_spawn = {key: None for key in SOURCE_KEYS}
        self._wait_reason: dict[str, str] = {}
        self._evidence_seq = 0

    # ═══════════════════════════════════════════════════════
    # 初始化
    # ═══════════════════════════════════════════════════════

    def start(self, sim_time: float = 0.0) -> None:
        self.started_at = float(sim_time)
        self._event("COORDINATOR_START", detail={
            "scenario": self.scenario, "fault": self.fault,
            "batch": dict(self._batch)})

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
        self._poll_sources(sim_time)
        self._poll_tasks(sim_time)
        self._maybe_inject_fault(sim_time)

        for record in list(self.controllers.values()):
            if not record.controller.finished:
                record.controller.pre_tick()

        self._advance_route(sim_time)

        self.sim.step()

        for record in list(self.controllers.values()):
            record.controller.post_tick()

        self._after_step(sim_time)
        self._timeout_check(sim_time)

    def _settle_step(self) -> None:
        for record in list(self.controllers.values()):
            record.controller.post_tick()
        self.sim.step()
        self._abort_settle_steps += 1

    # ═══════════════════════════════════════════════════════
    # 来源生成 (有限批次)
    # ═══════════════════════════════════════════════════════

    def _poll_sources(self, sim_time: float) -> None:
        for key in SOURCE_KEYS:
            planned = int(self._batch.get(SOURCE_RAW[key], 0))
            if self._source_count[key] >= planned:
                continue
            last = self._source_last_spawn[key]
            if last is not None and \
                    sim_time - last < cfg.SOURCE_SPAWN_INTERVAL_S:
                continue
            zone = self.lines[cfg.SOURCE_ITEMS[key]["line"]].line_id
            if not self.flow.zone_free(zone):
                self._event("SOURCE_BACKPRESSURE", zone=zone,
                            detail={"source": key,
                                    "reason": cfg.BACKPRESSURE_REASONS.get(
                                        zone, "区域占用")})
                continue
            self._spawn_source(key, sim_time)

    def _spawn_source(self, key: str, sim_time: float) -> None:
        spec = cfg.SOURCE_ITEMS[key]
        item_type = ItemType(spec["item_type"])
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
            uid, item_type, alias, sim_handle=handle, source=spec["line"],
            destination="", zone=spec["line"], sim_time=sim_time)
        self._source_count[key] += 1
        self._source_last_spawn[key] = sim_time
        self.spawned.append({
            "sim_time": round(sim_time, 4), "source": key, "uid": uid,
            "item_type": item_type.value, "position": list(spec["position"]),
            "handle": int(handle)})
        self._event("MATERIAL_SPAWN", uid=uid, item_type=item_type.value,
                    zone=spec["line"], source=set_name(key),
                    detail={"alias": alias, "handle": int(handle),
                            "spawn": spec["position"]})
        task = make_task(
            SOURCE_INITIAL_TASK[key], uid, source=spec["line"],
            destination="", device=TASK_UNIT[SOURCE_INITIAL_TASK[key]],
            sim_time=sim_time)
        self._submit(task, sim_time)

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
        for unit_id in cfg.UNIT_IDS:
            self._poll_unit(unit_id, sim_time)
        self._poll_agv(sim_time)

    def _poll_unit(self, unit_id: str, sim_time: float) -> None:
        if unit_id in self.controllers:
            return
        kinds = UNIT_TASKS[unit_id]
        candidates: list[Stage4Task] = []
        for kind in kinds:
            candidates.extend([
                task for task in self.queue.by_kind(kind)
                if task.status in (TaskStatus.PENDING, TaskStatus.WAITING)])
        candidates.sort(key=lambda task: (
            cfg.TASK_PRIORITY.get(task.kind, 9),
            task.created_sim_time, task.task_id))
        for task in candidates:
            if task.kind == cfg.TASK_KIND_E_CONVEYOR and self._line_faulted:
                self._note_wait(task, sim_time, "line_ce 故障停带")
                continue
            ok, reason = self._preconditions(task)
            if not ok:
                self._note_wait(task, sim_time, reason)
                continue
            owner = self._owner(task)
            acquired, busy = self.resources.acquire_all(
                TASK_RESOURCES[task.kind], owner, task.task_id, sim_time,
                f"{task.kind} start")
            if not acquired:
                self._note_wait(task, sim_time,
                                f"资源等待: {busy}")
                continue
            task.resources = list(TASK_RESOURCES[task.kind])
            self._start_unit_task(task, unit_id, sim_time)
            return

    def _preconditions(self, task: Stage4Task) -> tuple[bool, str]:
        flow = self.flow.by_uid(task.uid)
        if flow is None:
            return False, "FLOW_RECORD_MISSING"
        if flow.item_type not in TASK_TYPES[task.kind]:
            return False, (f"ITEM_TYPE_MISMATCH: {flow.item_type.value} "
                           f"not in "
                           f"{[item.value for item in TASK_TYPES[task.kind]]}")
        expected_zone = self._source_zone(task)
        if flow.zone != expected_zone:
            return False, (f"ZONE_MISMATCH: uid={task.uid} 在 {flow.zone} "
                           f"而非 {expected_zone}")
        if task.kind != cfg.TASK_KIND_E_CONVEYOR:
            # E 传送带任务的物料可能已在线上移动; 由控制器完成停带.
            pass
        dest_zone = self._destination_zone(task)
        if dest_zone and not self.flow.zone_free(dest_zone):
            return False, cfg.BACKPRESSURE_REASONS.get(
                dest_zone, f"{dest_zone} 占用")
        if task.kind in (cfg.TASK_KIND_A_AGV, cfg.TASK_KIND_E_AGV):
            dock = TASK_DOCK[task.kind]
            if self.agv_at_dock != dock:
                return False, f"AGV 不在 dock {dock}"
            if not self._agv_at_dock(dock):
                return False, f"等待 AGV 在 dock {dock} 停稳"
        if task.kind == cfg.TASK_KIND_B:
            if self.agv_at_dock != "B":
                return False, "AGV 不在 dock B"
            if not self._agv_at_dock("B"):
                return False, "等待 AGV 在 dock B 停稳"
        if task.kind == cfg.TASK_KIND_D:
            if self.agv_at_dock != "D":
                return False, "AGV 不在 dock D"
            if not self._agv_at_dock("D"):
                return False, "等待 AGV 在 dock D 停稳"
        rec = self.materials.by_uid(task.uid)
        if rec is None or rec.sim_handle is None:
            return False, "UID_NOT_REGISTERED"
        return True, ""

    def _source_zone(self, task: Stage4Task) -> str:
        return TASK_SOURCE_ZONE[task.kind]

    def _destination_zone(self, task: Stage4Task) -> str:
        return TASK_DEST_ZONE[task.kind]

    def _start_unit_task(self, task: Stage4Task, unit_id: str,
                         sim_time: float) -> None:
        arm = self.runtime.arm(unit_id)
        unit = self.scene.unit(unit_id)
        source = TASK_SOURCE[task.kind]
        sink = TASK_SINK[task.kind]
        line_id = (self._source_line_id(task, unit_id)
                   if source == "conveyor" else UNIT_LINE[unit_id])
        line = self.lines[line_id]
        sink_spec = self._sink_spec(task, unit_id)
        task_layout = TaskLayout(
            name=f"{self.layout.name}/{unit_id}", sink=sink_spec,
            unit_view=unit.layout,
            target="agv" if sink == "agv" else "station")
        try:
            controller = Stage4UnitController(
                sim=self.sim, scene=arm.view, materials=self.materials,
                conveyor=self.conveyor, belt_geometry=line.geometry,
                ik=self.ik[unit_id], suction=self.suction[unit_id],
                agv=self.agv, target=("agv" if sink == "agv" else "station"),
                uid=task.uid, fault=cfg.FAULT_NONE, layout=task_layout,
                logger=self.logger, runtime=arm, unit_id=unit_id,
                source=source, sink=sink, dock_id=TASK_DOCK[task.kind],
                resources=self.resources, task=task, line=line,
                sink_spec=sink_spec, flow=self.flow,
                expected_types=TASK_TYPES[task.kind],
                park_tcp_pose=handoff_park_tcp(unit_id),
                pick_timeout=cfg.TASK_TIMEOUT)
        except Exception as exc:
            self._release_task(task, sim_time, "controller build failed")
            self.queue.fail(task.task_id, sim_time,
                            f"CONTROLLER_BUILD_FAILED: "
                            f"{type(exc).__name__}: {exc}")
            self._freeze(f"{unit_id} 控制器构建失败: {exc}", sim_time,
                         abort=True)
            return
        self.queue.start(task.task_id, sim_time)
        self.controllers[unit_id] = ControllerRecord(
            unit_id=unit_id, task_id=task.task_id, controller=controller)
        task.phase = controller.state.name
        self._event("TASK_START", task_id=task.task_id, uid=task.uid,
                    item_type=(self.flow.by_uid(task.uid).item_type.value
                               if self.flow.by_uid(task.uid) else ""),
                    zone=self._source_zone(task), device=f"arm_{unit_id}",
                    detail={"kind": task.kind, "source": source,
                            "sink": sink, "line": line_id,
                            "resources": list(task.resources)})
        if self.on_controller is not None:
            try:
                self.on_controller(unit_id, controller)
            except Exception:
                pass

    def _source_line_id(self, task: Stage4Task, unit_id: str) -> str:
        if task.kind in (cfg.TASK_KIND_A_CONVEYOR,):
            return "line_s1a"
        if task.kind == cfg.TASK_KIND_B:
            return "line_s1b"
        if task.kind == cfg.TASK_KIND_D:
            return "line_s2d"
        if task.kind == cfg.TASK_KIND_E_CONVEYOR:
            return "line_ce"
        return UNIT_LINE[unit_id]

    def _sink_spec(self, task: Stage4Task, unit_id: str) -> SinkSpec:
        sink = TASK_SINK[task.kind]
        if sink == "agv":
            unit = self.scene.unit(unit_id)
            return SinkSpec(
                kind="agv", tcp_pose=tuple(unit.spec.agv_tcp_pose),
                material_pose=tuple(unit.spec.agv_material_pose),
                support_z=base.AGV_TRAY_TOP_Z,
                zone=self._destination_zone(task))
        if sink == "conveyor":
            line = self.lines[UNIT_LINE[unit_id]]
            pose = line.place_material_pose
            tcp = base.tcp_pose_above(pose)
            return SinkSpec(
                kind="conveyor", tcp_pose=tuple(tcp),
                material_pose=tuple(pose),
                support_z=base.CONVEYOR_SURFACE_Z,
                zone=self._destination_zone(task), line=line)
        # station: A→C 交接 / E 缓冲
        if task.kind in (cfg.TASK_KIND_A_CONVEYOR, cfg.TASK_KIND_A_AGV):
            zone = "handoff_ac"
            pos = cfg.HANDOFF_AC["position"]
            material = (pos[0], pos[1],
                        base.STATION_TOP_Z + base.MATERIAL_HALF_HEIGHT)
            support = base.STATION_TOP_Z
        elif task.kind == cfg.TASK_KIND_E_CONVEYOR:
            zone = "buffer_e_c"
            pos = cfg.E_BUFFERS["buffer_e_c"]["position"]
            material = (pos[0], pos[1],
                        base.ISLAND_TOP_Z + base.MATERIAL_HALF_HEIGHT)
            support = base.ISLAND_TOP_Z
        else:
            zone = "buffer_e_d"
            pos = cfg.E_BUFFERS["buffer_e_d"]["position"]
            material = (pos[0], pos[1],
                        base.ISLAND_TOP_Z + base.MATERIAL_HALF_HEIGHT)
            support = base.ISLAND_TOP_Z
        return SinkSpec(
            kind="station", tcp_pose=tuple(base.tcp_pose_above(material)),
            material_pose=tuple(material), support_z=support, zone=zone)

    def _note_wait(self, task: Stage4Task, sim_time: float,
                   reason: str) -> None:
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

    # ═══════════════════════════════════════════════════════
    # AGV 调度
    # ═══════════════════════════════════════════════════════

    def _poll_agv(self, sim_time: float) -> None:
        if self.route is not None:
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
            origin = self._transport_origin(task)
            destination = self._transport_destination(task)
            if origin is None or destination is None:
                continue
            flow = self.flow.by_uid(task.uid)
            if flow is None or flow.zone != "agv":
                continue
            requests.append({
                "kind": "transport", "transport": task, "task": task,
                "dock": origin, "destination": destination,
                "uid": task.uid, "created": task.created_sim_time,
                "priority": task.effective_priority(sim_time),
            })
        # 装载型单元任务 (B/D) 需要 AGV 先空载到位.
        for kind in (cfg.TASK_KIND_B, cfg.TASK_KIND_D):
            for task in self.queue.by_kind(kind):
                if task.status not in (TaskStatus.PENDING,
                                       TaskStatus.WAITING):
                    continue
                flow = self.flow.by_uid(task.uid)
                if flow is None or flow.zone != TASK_SOURCE_ZONE[kind]:
                    continue
                dock = TASK_DOCK[kind]
                requests.append({
                    "kind": "load", "task": task, "dock": dock,
                    "destination": dock, "uid": task.uid,
                    "created": task.created_sim_time,
                    "priority": cfg.TASK_PRIORITY.get(kind, 1),
                })
        # 卸载型单元任务 (A/E) 需要 AGV 留在目标 dock.
        for kind in (cfg.TASK_KIND_A_AGV, cfg.TASK_KIND_E_AGV):
            for task in self.queue.by_kind(kind):
                if task.status not in (TaskStatus.PENDING,
                                       TaskStatus.WAITING):
                    continue
                flow = self.flow.by_uid(task.uid)
                if flow is None or flow.zone != "agv":
                    continue
                dock = TASK_DOCK[kind]
                requests.append({
                    "kind": "unload", "task": task, "dock": dock,
                    "destination": dock, "uid": task.uid,
                    "created": task.created_sim_time,
                    "priority": cfg.TASK_PRIORITY.get(kind, 0),
                })
        return requests

    def _transport_origin(self, task: Stage4Task) -> Optional[str]:
        if task.kind == cfg.TASK_KIND_TRANSPORT_BA:
            return "B"
        if task.kind == cfg.TASK_KIND_TRANSPORT_DE:
            return "D"
        return None

    def _transport_destination(self, task: Stage4Task) -> Optional[str]:
        if task.kind == cfg.TASK_KIND_TRANSPORT_BA:
            return "A"
        if task.kind == cfg.TASK_KIND_TRANSPORT_DE:
            return "E"
        return None

    def _route_resources(self, spec) -> tuple[str, ...]:
        """路线实际经过的直道 + 全部 dock (车辆可能穿过中间 dock)."""
        resources = list(spec.lanes)
        if cfg.AGV_ROUTE_REQUIRES_ALL_DOCKS:
            resources.extend(f"dock_{dock}" for dock in
                             ("A", "B", "D", "E"))
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
            self._note_wait(task, sim_time, f"资源等待: {busy}")
            return False
        task.resources = list(self._route_resources(spec))
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
            self._note_wait(task, sim_time, f"资源等待: {busy}")
            return False
        task.resources = list(self._route_resources(spec))
        return self._launch_route(task, spec, sim_time)

    def _launch_route(self, task: Stage4Task, spec, sim_time: float) -> bool:
        try:
            route = AGVRouteController4(
                self.sim, self.agv, spec,
                material_manager=self.materials,
                uid=task.uid if spec.loaded else None,
                logger=self.logger)
        except Exception as exc:
            self._release_task(task, sim_time, "route build failed")
            self.queue.fail(task.task_id, sim_time,
                            f"ROUTE_BUILD_FAILED: {type(exc).__name__}: {exc}")
            self._freeze(f"AGV 路线构建失败: {exc}", sim_time)
            return False
        self.route = route
        self.route_task = task
        self.queue.start(task.task_id, sim_time)
        task.phase = f"ROUTE {spec.route_id}"
        self._event("TASK_START", task_id=task.task_id, uid=task.uid,
                    source=spec.start_dock, destination=spec.end_dock,
                    device="agv",
                    detail={"kind": task.kind,
                            "route_id": spec.route_id,
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
            task.upgraded = task.effective_priority(sim_time) < task.priority
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
        if self.route is None:
            return
        if self.route_task is not None:
            self.route_task.phase = self.route.state.value
        self.route.step(sim_time)

    # ═══════════════════════════════════════════════════════
    # 步后处理
    # ═══════════════════════════════════════════════════════

    def _after_step(self, sim_time: float) -> None:
        if self.aborted or self.frozen:
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
                self._event("TASK_FAILED", task_id=task.task_id,
                            uid=task.uid, device=f"arm_{unit_id}",
                            detail={"reason": "SAFETY_LATCH",
                                    "latch": controller.safety.latch.reason})
                self._freeze(f"{unit_id} 安全锁存: "
                             f"{controller.safety.latch.reason}", sim_time,
                             abort=True)
                return
            if controller.state is UnitTaskState.FAILED:
                self.queue.fail(task.task_id, sim_time,
                                controller.failure_reason or "UNIT_FAILED",
                                {"controller": controller.summary()})
                self._event("TASK_FAILED", task_id=task.task_id,
                            uid=task.uid, device=f"arm_{unit_id}",
                            detail={"reason": controller.failure_reason})
                self._freeze(f"{unit_id} 任务失败: "
                             f"{controller.failure_reason}", sim_time,
                             abort=True)
                return
            if controller.finished:
                ok, reason, evidence = self._verify_unit_task(task, controller,
                                                              unit_id)
                if ok:
                    self.queue.done(task.task_id, sim_time, evidence)
                    self._event("TASK_DONE", task_id=task.task_id,
                                uid=task.uid,
                                item_type=self.flow.by_uid(
                                    task.uid).item_type.value
                                if self.flow.by_uid(task.uid) else "",
                                device=f"arm_{unit_id}",
                                detail={"reason": reason})
                    self._commit_unit_result(task, unit_id, sim_time)
                    self._release_task(task, sim_time, "unit task complete")
                    self.controller_history.append({
                        "unit_id": unit_id, "task_id": task.task_id,
                        "summary": controller.summary()})
                    self.controllers.pop(unit_id, None)
                    self._maybe_inject_conveyor_fault(task, sim_time)
                    if self.frozen or self.aborted:
                        return
                else:
                    self.queue.fail(task.task_id, sim_time, reason, evidence)
                    self._event("TASK_FAILED", task_id=task.task_id,
                                uid=task.uid, device=f"arm_{unit_id}",
                                detail={"reason": reason})
                    self._freeze(reason, sim_time, abort=True)
                    return
        self._after_route(sim_time)
        self._check_join(sim_time)

    def _verify_unit_task(self, task: Stage4Task,
                          controller: Stage4UnitController,
                          unit_id: str) -> tuple[bool, str, dict]:
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
        if flow.item_type not in TASK_TYPES[task.kind]:
            return False, (f"ITEM_TYPE_MISMATCH: {flow.item_type.value}"), \
                evidence
        sink = TASK_SINK[task.kind]
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
            expected = self._expected_material_pose(task, unit_id)
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

    def _expected_material_pose(self, task: Stage4Task,
                                unit_id: str) -> tuple[float, float, float]:
        sink = self._sink_spec(task, unit_id)
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
                            sim_time: float) -> None:
        flow = self.flow.by_uid(task.uid)
        rec = self.materials.by_uid(task.uid)
        if flow is None:
            return
        if task.kind in (cfg.TASK_KIND_A_CONVEYOR, cfg.TASK_KIND_A_AGV,
                         cfg.TASK_KIND_B, cfg.TASK_KIND_C,
                         cfg.TASK_KIND_D):
            try:
                self.flow.process(task.uid, unit_id, sim_time,
                                  task_id=task.task_id)
            except MaterialFlowError as exc:
                self._freeze(f"PROCESS_FAILED: {exc}", sim_time, abort=True)
                return
            flow = self.flow.by_uid(task.uid)
        dest = self._destination_zone(task)
        if task.kind == cfg.TASK_KIND_C:
            # C 放到 C→E 入口: 物理状态回到 ON_CONVEYOR, 由 E 任务启动带.
            if rec is not None and rec.state is MaterialState.RELEASED:
                try:
                    rec.transition_to(
                        MaterialState.ON_CONVEYOR, sim_time,
                        "placed on C→E entry; handoff to line_ce")
                except Exception as exc:
                    self._freeze(f"CONVEYOR_STATE: {exc}", sim_time,
                                 abort=True)
                    return
        try:
            self.flow.occupy(
                task.uid, dest, sim_time, reason=f"{task.kind} done",
                task_id=task.task_id,
                state=(RecordState.READY if dest not in ("line_ce",)
                       else RecordState.IN_TRANSIT))
        except MaterialFlowError as exc:
            self._freeze(f"ZONE_COMMIT_FAILED: {exc}", sim_time, abort=True)
            return
        self._event("MATERIAL_PLACE", task_id=task.task_id, uid=task.uid,
                    item_type=flow.item_type.value if flow else "",
                    zone=dest, source=self._source_zone(task),
                    destination=dest, device=f"arm_{unit_id}",
                    detail={"task_kind": task.kind})
        followup = FOLLOWUP.get(task.kind)
        if followup:
            self._submit_followup(followup, task.uid, sim_time)

    def _submit_followup(self, kind: str, uid: int, sim_time: float) -> None:
        if kind in (cfg.TASK_KIND_TRANSPORT_BA, cfg.TASK_KIND_TRANSPORT_DE):
            source = "dock_B" if kind == cfg.TASK_KIND_TRANSPORT_BA else \
                "dock_D"
            destination = "dock_A" if kind == cfg.TASK_KIND_TRANSPORT_BA \
                else "dock_E"
            task = make_task(kind, uid, source=source,
                             destination=destination, dock=destination[-1],
                             device="agv", sim_time=sim_time)
        else:
            task = make_task(
                kind, uid, source=TASK_SOURCE_ZONE[kind],
                destination=TASK_DEST_ZONE[kind],
                dock=TASK_DOCK[kind], device=TASK_UNIT[kind],
                sim_time=sim_time)
        self._submit(task, sim_time)

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
        route = self.route
        task = self.route_task
        if route is None or task is None:
            return
        spec = route.spec
        if route.state is RouteState.DONE:
            ok, reason = self._verify_route(task, route)
            if ok:
                self.agv_at_dock = spec.end_dock
                self.queue.done(task.task_id, sim_time,
                                {"route": route.summary()})
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
                    followup = FOLLOWUP.get(task.kind)
                    if followup:
                        self._submit_followup(followup, task.uid, sim_time)
                else:
                    self._event("TASK_DONE", task_id=task.task_id,
                                uid=task.uid, device="agv",
                                detail={"reason": reason,
                                        "kind": cfg.TASK_KIND_REPOSITION})
            else:
                self.queue.fail(task.task_id, sim_time, reason,
                                {"route": route.summary()})
                self._event("TASK_FAILED", task_id=task.task_id,
                            uid=task.uid, device="agv",
                            detail={"reason": reason})
                self._freeze(reason, sim_time, abort=True)
            self.agv_history.append(route.summary())
            self._release_task(task, sim_time, "route complete")
            self.route = None
            self.route_task = None
            return
        if route.state is RouteState.FAILED:
            self.queue.fail(task.task_id, sim_time,
                            f"AGV_ROUTE_FAILED: {route.failure_reason}",
                            {"route": route.summary()})
            self.agv_history.append(route.summary())
            self._event("TASK_FAILED", task_id=task.task_id, uid=task.uid,
                        device="agv",
                        detail={"reason": route.failure_reason})
            self._release_task(task, sim_time, "route failed")
            self.route = None
            self.route_task = None
            self._freeze(f"AGV_ROUTE_FAILED: {route.failure_reason}",
                         sim_time, abort=True)
            return
        if route.state is RouteState.BLOCKED:
            if task.status is not TaskStatus.BLOCKED:
                self.queue.block(task.task_id, sim_time,
                                 "AGV 路线受阻, 轮速已停止")
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
                if clearance_ok and route.resume(
                        sim_time, clearance_ok=True,
                        reason="obstacle_removed_and_ownership_reconfirmed"):
                    self.queue.unblock(task.task_id, sim_time,
                                       "障碍清除, 现场/资源所有权重新确认")
                    self._event("TASK_UNBLOCK", task_id=task.task_id,
                                uid=task.uid, device="agv")
                    self._block_since = None
                    return
            if sim_time - self._block_since > cfg.AGV_ROUTE_TIMEOUT:
                self.queue.fail(task.task_id, sim_time,
                                "AGV_BLOCKED_TIMEOUT: 清障后仍未续行",
                                {"route": route.summary()})
                self._freeze("AGV_BLOCKED_TIMEOUT", sim_time, abort=True)

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
        for dock in ("A", "B", "D", "E"):
            if self.resources.owner(f"dock_{dock}") not in (None, owner):
                return False
        return True

    # ═══════════════════════════════════════════════════════
    # E 岛配对
    # ═══════════════════════════════════════════════════════

    def _check_join(self, sim_time: float) -> None:
        if self.frozen or self.aborted:
            return
        semi, part = self.flow.pair_candidates()
        if semi is None or part is None:
            return
        uid = self._next_uid
        self._next_uid += 1
        alias = f"Part_{uid:04d}"
        out = cfg.E_OUTPUT
        try:
            handle = self.scene.spawn_material(
                uid, alias,
                position=(out["position"][0], out["position"][1],
                          out["material_z"]))
        except Exception as exc:
            self._freeze(f"FINAL_SPAWN_FAILED: {exc}", sim_time)
            return
        self._set_material_color(handle, ItemType.FINAL)
        self.materials.register(uid, alias, sim_handle=handle)
        result = self.flow.commit_join(
            final_uid=uid, sim_time=sim_time, final_alias=alias,
            final_handle=handle)
        if not result.ok:
            self.scene.remove_material(handle)
            self.materials.remove(uid)
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
        self._event("FINAL_CREATED", uid=uid,
                    item_type=ItemType.FINAL.value, zone="output_e",
                    device="E", evidence_id=result.pair_key,
                    detail={"parents": [result.semi_uid, result.part_uid],
                            "pair_key": result.pair_key})

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
        if task.kind not in (cfg.TASK_KIND_C, cfg.TASK_KIND_A_CONVEYOR,
                             cfg.TASK_KIND_A_AGV):
            return
        if self._line_faulted:
            return
        if task.kind != cfg.TASK_KIND_C:
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
        for other in self.queue.by_kind(cfg.TASK_KIND_E_CONVEYOR):
            if other.status in (TaskStatus.PENDING, TaskStatus.WAITING,
                                TaskStatus.RUNNING):
                if other.status is not TaskStatus.BLOCKED:
                    self.queue.block(other.task_id, sim_time,
                                     "CONVEYOR_FAULT: line_ce 停带")
                    self._event("TASK_BLOCK", task_id=other.task_id,
                                uid=other.uid, device="line_ce",
                                detail={"reason": "CONVEYOR_FAULT"})
        # 冻结整线现场: 停止 AGV/所有线, 活动任务停在当前位置, 保留 UID.
        self._freeze("CONVEYOR_FAULT: line_ce 停止", sim_time)

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

    @staticmethod
    def _owner(task: Stage4Task) -> str:
        if task.kind in TASK_UNIT:
            return f"unit_{TASK_UNIT[task.kind]}/task_{task.task_id}"
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
        self._event("FREEZE", detail={"reason": reason, "abort": abort})
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
                self.queue.fail(task.task_id, sim_time,
                                f"STAGE4_FROZEN: {reason}")
            elif task.status in (TaskStatus.PENDING, TaskStatus.WAITING):
                self.queue.block(task.task_id, sim_time,
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
                    self.queue.fail(
                        task.task_id, sim_time,
                        f"BACKPRESSURE_TIMEOUT: {task.phase}")
                    self._event("TASK_FAILED", task_id=task.task_id,
                                uid=task.uid,
                                detail={"reason": "BACKPRESSURE_TIMEOUT"})
                    self._freeze("BACKPRESSURE_TIMEOUT", sim_time,
                                 abort=True)
                    return
            elif task.status is TaskStatus.RUNNING:
                if sim_time - (task.started_sim_time or sim_time) > \
                        cfg.TASK_TIMEOUT:
                    self.queue.fail(task.task_id, sim_time,
                                    "TASK_TIMEOUT")
                    self._event("TASK_FAILED", task_id=task.task_id,
                                uid=task.uid,
                                detail={"reason": "TASK_TIMEOUT"})
                    self._freeze("TASK_TIMEOUT", sim_time, abort=True)
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
        return all(self._source_count[key] >=
                   int(self._batch.get(SOURCE_RAW[key], 0))
                   for key in SOURCE_KEYS)

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
        semi_wip = [rec for rec in self.flow.by_type(ItemType.SEMI_CE)
                    if rec.state is not RecordState.CONSUMED]
        loaded_routes = [row for row in self.agv_history
                         if row.get("spec", {}).get("loaded")]
        route_ok = bool(
            loaded_routes and all(
                row.get("metrics", {}).get("stop_error") is not None
                and row["metrics"]["stop_error"]
                <= cfg.AGV_ROUTE_ARRIVE_TOL
                and abs(row["metrics"].get("heading_error") or 0.0)
                <= cfg.AGV_ROUTE_HEADING_TOL
                for row in loaded_routes))
        priority_ok, priority_detail = self._priority_acceptance()
        acceptance = {
            "scenario": self.scenario,
            "fault": self.fault,
            "tasks_done": self.queue.all_done(),
            "any_task_failed": self.queue.any_failed(),
            "any_task_blocked": self.queue.any_blocked(),
            "final_count": len(finals),
            "final_parents": [list(rec.parents) for rec in finals],
            "unpaired_semi_ce": len(semi_wip),
            "any_safety_latch": any_latch,
            "safety": safety,
            "resources_released": resources_released,
            "aborted": self.aborted,
            "frozen": self.frozen,
            "stop_reason": self.stop_reason,
            "loaded_routes": len(loaded_routes),
            "route_ok": route_ok,
            "priority_ok": priority_ok,
            "priority_detail": priority_detail,
            "injected": list(self.injected),
            "batch": dict(self._batch),
            "spawned": list(self.spawned),
        }
        expected_final = 2 if self.scenario == cfg.SCENARIO_STAGGERED else (
            1 if self.scenario in (
                cfg.SCENARIO_FULL_FLOW, cfg.SCENARIO_NO_PAIR,
                cfg.SCENARIO_AGV_PRIORITY, cfg.SCENARIO_AGV_BLOCKED) else 0)
        common = (not any_latch and resources_released and route_ok
                  and priority_ok)
        if self.scenario == cfg.SCENARIO_FULL_FLOW:
            acceptance["ok"] = bool(
                self.queue.all_done() and common
                and len(finals) == expected_final
                and len(semi_wip) == 1
                and all(rec.parents for rec in finals))
        elif self.scenario == cfg.SCENARIO_STAGGERED:
            acceptance["max_concurrent_tasks"] = self._max_concurrent_tasks()
            acceptance["interleaved"] = bool(
                acceptance["max_concurrent_tasks"]
                >= cfg.STAGGERED_MIN_CONCURRENT_TASKS)
            acceptance["ok"] = bool(
                self.queue.all_done() and common
                and len(finals) == expected_final
                and len(semi_wip) == 1
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
                    task.kind == cfg.TASK_KIND_E_CONVEYOR for task in blocked),
                "site_preserved": bool(preserved),
                "no_final": len(finals) == 0,
            })
            acceptance["ok"] = bool(
                self._line_faulted and self.frozen
                and acceptance["affected_task_blocked"]
                and acceptance["site_preserved"]
                and acceptance["no_final"])
        return acceptance

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
        from .metrics import recompute_metrics
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
            "batch": dict(self._batch),
            "source_counts": dict(self._source_count),
            "agv_at_dock": self.agv_at_dock,
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
               detail: Optional[dict] = None) -> None:
        self._evidence_seq += 1
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
            "detail": detail or {},
        })
        if self.logger:
            self.logger.info("[STAGE4 t=%.2f] %s %s uid=%s %s",
                             self._sim_time(), kind, task_id, uid,
                             detail or {})

    def _sim_time(self) -> float:
        try:
            return float(self.sim.getSimulationTime())
        except Exception:
            return 0.0


def set_name(source_key: str) -> str:
    return {"s1a": "S1:A", "s1b": "S1:B", "s2d": "S2:D"}.get(
        source_key, source_key)
