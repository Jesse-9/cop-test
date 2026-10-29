"""unit_controller.py — 阶段四单岛控制器.

在阶段三 :class:`stage3.unit_controller.Stage3UnitController` 之上扩展
三种取料源与三种放置汇:

取料源:
  - ``conveyor``: 本岛入口传送带 (A/B/D/E 各一条线), 传感器命中目标 UID
    后主动停带 → 稳定 → 实测位姿 → simIK 吸附;
  - ``agv``: AGV 在指定 dock 停稳, 校验同一 UID 仍在载台后吸附
    (A 卸载 SEMI_AB, E 卸载 PART_DE);
  - ``station``: A→C 固定交接台, 交接传感器命中且 UID/类型/稳定性一致后
    按实测位姿吸附 (C)。

放置汇:
  - ``station``: 本岛台面/交接台/E 缓冲 (物料释放到静态承载面);
  - ``agv``: AGV 载台 (B 装载 SEMI_AB, D 装载 PART_DE);
  - ``conveyor``: 传送带入口 (C 放到 C→E 线; 放料前必须停带并确认)。

控制器**不驱动 AGV**; 阶段四 AGV 由协调器航线控制器独占. 所有线与臂的
启停都由协调器按资源表调度, 控制器只执行本任务.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from stage1 import config as base
from stage1.conveyor_controller import compensation_allowed, TransportFault
from stage1.material_manager import MaterialState
from stage1.unit_controller import (MismatchReason, PickMismatch,
                                    Stage1UnitController, UnitTaskState,
                                    quat_tilt)
from stage2 import config as s2cfg
from stage2.contact import contact_pose
from stage2.conveyor_safety import (ConveyorStopMonitor, StopStatus,
                                    bounded_transport_step,
                                    read_conveyor_speed)
from stage2.path_planning import Goal, MotionContext, PlanStatus
from stage2.unit_controller import (Stage2TaskState, _motion_params,
                                    lift_pose)
from stage3.unit_controller import Stage3UnitController

from . import config as cfg
from .layout import LineSpec, UnitView4
from .materials import ItemType, MaterialRegistry


class Stage4UnitControllerError(RuntimeError):
    code = "STAGE4_UNIT"


@dataclass(frozen=True)
class SinkSpec:
    """本任务的放置目标 (台面/AGV/传送带) 与承载面高度."""

    kind: str
    tcp_pose: tuple[float, ...]
    material_pose: tuple[float, ...]
    support_z: float
    zone: str
    line: Optional[LineSpec] = None


class TaskLayout:
    """控制器期望的最小 layout 接口 (sink 专属位姿 + 承载面高度)."""

    def __init__(self, name: str, sink: SinkSpec, unit_view: UnitView4,
                 target: str) -> None:
        self.name = name
        self.sink = sink
        self.unit_view = unit_view
        self._label = target

    @property
    def station_top_z(self) -> float:
        return float(self.sink.support_z)

    @property
    def arm_base(self):
        return self.unit_view.arm_base

    def tcp_pose_for_target(self, target: str) -> list[float]:
        return [float(v) for v in self.sink.tcp_pose]

    def material_pose_for_target(self, target: str) -> list[float]:
        values = [float(v) for v in self.sink.material_pose]
        if len(values) == 3:
            values.extend(base.TOOL_TARGET_QUATERNION)
        return values

    def targets(self) -> dict[str, list[float]]:
        return {self._label: list(self.sink.tcp_pose)}


class Stage4UnitController(Stage3UnitController):
    """阶段四单岛控制器 (独立 IK/规划/碰撞/锁存由 runtime 注入)."""

    def __init__(self, *args, unit_id: str, source: str, sink: str,
                 dock_id: str, resources, task, line: LineSpec,
                 sink_spec: SinkSpec, flow: MaterialRegistry,
                 expected_types, park_tcp_pose=None, **kwargs) -> None:
        if source not in ("conveyor", "agv", "station"):
            raise ValueError(f"未知 source: {source!r}")
        if sink not in ("station", "agv", "conveyor"):
            raise ValueError(f"未知 sink: {sink!r}")
        target_label = "agv" if sink == "agv" else "station"
        kwargs["target"] = target_label
        # 阶段三构造器只接受 conveyor/agv 源与 station/agv 汇; station 源和
        # conveyor 汇在构造后再启用, 仍由本类的分流/放置覆盖实现.
        passthrough_source = "conveyor" if source == "station" else source
        passthrough_sink = "agv" if sink == "agv" else "station"
        super().__init__(*args, unit_id=unit_id,
                         source=passthrough_source, sink=passthrough_sink,
                         dock_id=dock_id, resources=resources, task=task,
                         **kwargs)
        self.source = source
        self.sink = sink
        self.target = target_label
        self.line = line
        self.sink_spec = sink_spec
        self.flow = flow
        self.expected_types = tuple(ItemType(item) for item in expected_types)
        self.zone = str(sink_spec.zone)
        self.park_tcp_pose = (None if park_tcp_pose is None
                              else [float(v) for v in park_tcp_pose])
        self.line_fault = False
        self._line_stop_count = 0
        self._line_stop_since: Optional[float] = None
        self._type_checked = False

    # ═══════════════════════════════════════════════════════
    # 取料源分流
    # ═══════════════════════════════════════════════════════

    def _advance_task(self) -> None:
        if (self.source == "station"
                and self.state is UnitTaskState.WAITING_FOR_ITEM):
            self._advance_wait_station_pick()
            return
        if (self.source == "agv"
                and self.state is UnitTaskState.WAITING_FOR_ITEM):
            self._advance_wait_agv_pick()
            return
        if self.state is UnitTaskState.RELEASING:
            self.release_attempted = True
        super()._advance_task()

    # ── 传送带取料: 本岛线别名 ──────────────────────────────

    def _update_conveyor(self) -> None:
        if self.source != "conveyor":
            return
        if self.line_fault:
            return
        rec = self.materials.by_uid(self.uid)
        if rec is None:
            self._fail("UID_NOT_REGISTERED")
            return
        if self.state not in (UnitTaskState.IDLE,
                              UnitTaskState.WAITING_FOR_ITEM):
            return
        if rec.state not in (MaterialState.SPAWNED,
                             MaterialState.ON_CONVEYOR):
            return
        self.conveyor.set_speed(self.line.conveyor, cfg.LINE_SPEED)
        if rec.sim_handle is None:
            self._fail("MATERIAL_HAS_NO_HANDLE")
            return
        try:
            pose = list(self.sim.getObjectPose(rec.sim_handle,
                                               self.sim.handle_world))
        except Exception as exc:
            self._fail(f"MATERIAL_POSE_UNREADABLE: {exc}")
            return
        geometry = self.line.geometry
        try:
            compensation_allowed(pose[:3], base.CONVEYOR_SURFACE_Z,
                                 geometry=geometry)
        except TransportFault as exc:
            rec.transition_to(MaterialState.FAULT, self.sim_time,
                              f"CONVEYOR_FAULT: {exc}")
            self._fail(f"CONVEYOR_FAULT: {exc}")
            return
        previous = self._material_prev_pos or pose
        step = bounded_transport_step(
            pose[:3], previous, geometry, cfg.LINE_SPEED, base.SIM_STEP)
        if step.delta > 0.0:
            self.sim.setObjectPosition(rec.sim_handle, step.new_position,
                                       self.sim.handle_world)
            self.compensation_count += 1
            self._compensation_steps += 1
            self._compensation_consecutive += 1
            if self._compensation_consecutive > \
                    s2cfg.COMPENSATION_MAX_CONSECUTIVE:
                self._long_compensation = True
            self._event(
                f"HYBRID_DYNAMIC compensation Δ={step.delta:.4f} "
                f"(physical={step.physical_delta:.4f}, "
                f"shortfall={step.shortfall:.4f})")
            self._material_prev_pos = list(step.new_position)
        else:
            self._compensation_consecutive = 0
            self._material_prev_pos = list(pose[:3])
        if step.overspeed:
            self._overspeed_steps += 1
        if rec.state is MaterialState.SPAWNED:
            rec.transition_to(MaterialState.ON_CONVEYOR, self.sim_time,
                              f"entered {self.line.line_id} effective zone")
        if step.at_stop:
            self._fail("NO_SENSOR_TRIGGER_AT_STOP: "
                       f"{self.line.line_id} 物料到达停止区但传感器未命中")

    def _read_pick_sensor(self) -> None:
        if self.source != "conveyor":
            return
        if self.state is not UnitTaskState.WAITING_FOR_ITEM:
            return
        rec = self.materials.by_uid(self.uid)
        if rec is None or rec.sim_handle is None:
            return
        expected = int(rec.sim_handle)
        hits = [h for h in self.materials.handles()
                if self.scene.check_sensor(self.line.sensor_alias, h)
                is not None]
        if expected in hits:
            self.last_mismatch = None
            self._mismatch_logged = False
            self._mismatch_since = None
            self._command_pick_stop("sensor_hit")
            return
        if hits and expected not in hits:
            wrong = int(hits[0])
            self.last_mismatch = PickMismatch(
                self.sim_time, MismatchReason.UID_MISMATCH, wrong, expected)
            if self._mismatch_since is None:
                self._mismatch_since = self.sim_time
            if not self._mismatch_logged and self.logger:
                self.logger.warning(
                    "[MISMATCH] %s 传感器命中 %s, 任务 uid=%s; 不吸附",
                    self.line.line_id, wrong, self.uid)
                self._mismatch_logged = True
            return
        if self.last_mismatch is None or \
                self.last_mismatch.reason is MismatchReason.UID_MISMATCH:
            self.last_mismatch = PickMismatch(
                self.sim_time, MismatchReason.NO_HIT, -1, expected)
        self._mismatch_since = None

    def _command_pick_stop(self, reason: str) -> None:
        if self._stop_command_time is not None:
            return
        rec = self.materials.by_uid(self.uid)
        position = None
        if rec is not None and rec.sim_handle is not None:
            try:
                position = list(self.sim.getObjectPosition(
                    rec.sim_handle, self.sim.handle_world))
            except Exception:
                position = None
        if position is None:
            self._safety_fail("STOP_POSE_UNREADABLE: 停带前无法读取物料位姿")
            return
        speed = read_conveyor_speed(
            self.sim, self.scene.handle(self.line.conveyor),
            base.CONVEYOR_STATE_KEY)
        self.conveyor.stop(self.line.conveyor)
        self._stop_command_time = self.sim_time
        self._stop_monitor = ConveyorStopMonitor(self.line.geometry)
        self._stop_monitor.on_stop_command(
            self.sim_time, position,
            speed.value if speed.valid else cfg.LINE_SPEED)
        self._pad_pos_at_stop = self._pad_position()
        self._pad_prev_pos = self._pad_pos_at_stop
        self._enter(UnitTaskState.STOPPING,
                    f"{self.line.line_id} conveyor stop ({reason})")

    def _advance_stopping(self) -> None:
        if self._stop_command_time is None:
            self._command_pick_stop("unspecified")
            return
        if self._stop_monitor is None:
            self._safety_fail("STOP_MONITOR_MISSING")
            return
        if self.sim_time - self._stop_command_time > \
                s2cfg.CONVEYOR_STOP_TIMEOUT:
            self._fail("PAD_STOP_TIMEOUT: 带面未在限时内停止")
            return
        rec = self.materials.by_uid(self.uid)
        position = None
        if rec is not None and rec.sim_handle is not None:
            try:
                position = list(self.sim.getObjectPosition(
                    rec.sim_handle, self.sim.handle_world))
            except Exception:
                position = None
        if position is None:
            self._safety_fail("STOP_POSITION_UNKNOWN: 停带观测缺失物料位姿")
            return
        speed = read_conveyor_speed(
            self.sim, self.scene.handle(self.line.conveyor),
            base.CONVEYOR_STATE_KEY)
        status = self._stop_monitor.observe(
            self.sim_time, speed, position,
            pad_position=self._pad_position())
        if status is StopStatus.PENDING:
            return
        measurement = self._stop_monitor.measurement
        if status is StopStatus.OK:
            self._event(f"CONVEYOR_STOP OK: {measurement.as_dict()}")
            self._stability = None
            self._stable_prev_pos = list(position)
            self._stable_count = 0
            self._settle_started_at = self.sim_time
            self._enter(UnitTaskState.MATERIAL_SETTLING,
                        "conveyor stopped, waiting material stable")
            return
        if status is StopStatus.STOP_UNCONFIRMED:
            self._safety_fail(
                f"STOP_UNCONFIRMED: {measurement.message}",
                measurement.as_dict())
            return
        if rec is not None:
            rec.transition_to(MaterialState.FAULT, self.sim_time,
                              f"CONVEYOR_STOP_{status.value}")
        self._fail(f"CONVEYOR_STOP_{status.value}: {measurement.message}")

    def _advance_material_settling(self) -> None:
        rec = self.materials.by_uid(self.uid)
        if rec is None or rec.sim_handle is None:
            self._fail("UID_NOT_REGISTERED")
            return
        speed = self._read_material_speed(rec.sim_handle)
        position = self._read_material_position(rec.sim_handle)
        if speed is None or position is None:
            self._safety_fail("MATERIAL_STATE_UNREADABLE: "
                              "无法读取物料速度/位姿")
            return
        from stage2.conveyor_safety import MaterialStabilityTracker
        if self._stability is None:
            self._stability = MaterialStabilityTracker(
                self.sim_time, position)
        state = self._stability.update(self.sim_time, position, speed)
        if state.timed_out:
            self._fail(
                "MATERIAL_SETTLE_TIMEOUT: 物料未在限时内稳定 "
                f"(speed={speed:.3f} m/s, step={state.step_moved:.4f} m)")
            return
        if not state.stable:
            return
        try:
            pose = [float(v) for v in self.sim.getObjectPose(
                rec.sim_handle, self.sim.handle_world)]
            box = self.suction.material_box(rec.sim_handle)
        except Exception as exc:
            self._fail(f"MATERIAL_GEOMETRY_UNREADABLE: {exc}")
            return
        tilt = quat_tilt(pose[3:7])
        self._assert_expected_type()
        if self.failure_reason:
            return
        if not self.line.pick_region.contains(pose):
            self._fail(
                "OUT_OF_ALLOWED_REGION: 物料停稳后不在允许抓取区 "
                f"(x={pose[0]:.4f}, y={pose[1]:.4f}, z={pose[2]:.4f})")
            return
        if tilt > base.MATERIAL_MAX_TILT:
            self._fail(f"MATERIAL_TILT: 物料倾斜 {tilt:.3f} rad")
            return
        self._pick_pose = pose
        self._pick_box = box
        self._pick_tcp = contact_pose(pose, box, 0.0)
        if rec.state is MaterialState.SPAWNED:
            rec.transition_to(MaterialState.ON_CONVEYOR, self.sim_time,
                              "settled on conveyor")
        if rec.state is MaterialState.ON_CONVEYOR:
            rec.transition_to(MaterialState.READY_FOR_PICK, self.sim_time,
                              "stopped and stable, measured pose")
        self._event(
            f"material stable at ({pose[0]:.4f},{pose[1]:.4f},"
            f"{pose[2]:.4f}) tilt={tilt:.3f}; surface target fixed")
        self._begin_motion(UnitTaskState.APPROACHING,
                           "measured pose after active stop")

    # ── 固定交接台取料 ──────────────────────────────────────

    def _advance_wait_station_pick(self) -> None:
        if self._wait_started_at is None:
            self._wait_started_at = self.sim_time
        if self.sim_time - self._wait_started_at > self.pick_timeout:
            self._fail("STATION_PICK_TIMEOUT: 交接台未在限时内提供目标 UID")
            return
        rec = self.materials.by_uid(self.uid)
        if rec is None or rec.sim_handle is None:
            self._fail("UID_NOT_REGISTERED")
            return
        if rec.state is MaterialState.READY_FOR_PICK:
            self._begin_motion(UnitTaskState.APPROACHING,
                               "handoff material already measured")
            return
        if rec.state not in (MaterialState.RELEASED,):
            self._fail(
                f"MATERIAL_STATE_INVALID: uid={self.uid} 状态 "
                f"{rec.state.value} 不可从固定交接台取料")
            return
        flow = self.flow.by_uid(self.uid)
        if flow is None:
            self._fail("FLOW_RECORD_MISSING")
            return
        if flow.zone != cfg.HANDOFF_AC["id"]:
            self._fail(f"ZONE_MISMATCH: uid={self.uid} 不在 A→C 交接台 "
                       f"(zone={flow.zone})")
            return
        if self._assert_expected_type():
            return
        hit = self.scene.check_sensor(cfg.HANDOFF_AC["sensor"],
                                      rec.sim_handle)
        if hit is None:
            return
        speed = self._read_material_speed(rec.sim_handle)
        position = self._read_material_position(rec.sim_handle)
        if speed is None or position is None:
            self._safety_fail("STATION_MATERIAL_UNREADABLE")
            return
        step = (math.dist(position, self._tray_prev_pos)
                if self._tray_prev_pos is not None else 0.0)
        self._tray_prev_pos = list(position)
        if (speed <= base.MATERIAL_STABLE_SPEED_TOL
                and step <= base.MATERIAL_STABLE_POS_TOL):
            self._tray_stable_count += 1
        else:
            self._tray_stable_count = 0
        if self._tray_stable_count < base.MATERIAL_STABLE_STEPS:
            return
        try:
            pose = [float(v) for v in self.sim.getObjectPose(
                rec.sim_handle, self.sim.handle_world)]
            box = self.suction.material_box(rec.sim_handle)
        except Exception as exc:
            self._fail(f"MATERIAL_GEOMETRY_UNREADABLE: {exc}")
            return
        tilt = quat_tilt(pose[3:7])
        if tilt > base.MATERIAL_MAX_TILT:
            self._fail(f"MATERIAL_TILT: 交接物料倾斜 {tilt:.3f} rad")
            return
        self._pick_pose = pose
        self._pick_box = box
        self._pick_tcp = contact_pose(pose, box, 0.0)
        rec.transition_to(MaterialState.READY_FOR_PICK, self.sim_time,
                          "handoff material present, stable and measured")
        self._event(
            f"STATION_PICK: uid={self.uid} zone={flow.zone} "
            f"pose=({pose[0]:.4f},{pose[1]:.4f},{pose[2]:.4f})")
        self._begin_motion(UnitTaskState.APPROACHING,
                           "measured handoff pose")

    # ── AGV 载台取料 (按本岛 dock) ──────────────────────────

    def _advance_wait_agv_pick(self) -> None:
        if self._wait_started_at is None:
            self._wait_started_at = self.sim_time
        if self.sim_time - self._wait_started_at > self.pick_timeout:
            self._fail("AGV_PICK_TIMEOUT: AGV 未在限时内携带目标 UID 到站")
            return
        rec = self.materials.by_uid(self.uid)
        if rec is None or rec.sim_handle is None:
            self._fail("UID_NOT_REGISTERED")
            return
        if rec.state is MaterialState.READY_FOR_PICK:
            self._begin_motion(UnitTaskState.APPROACHING,
                               "tray material already measured")
            return
        if rec.state not in (MaterialState.ON_TRAY,
                             MaterialState.RELEASED):
            self._fail(
                f"MATERIAL_STATE_INVALID: uid={self.uid} 状态 "
                f"{rec.state.value} 不可从 AGV 取料")
            return
        flow = self.flow.by_uid(self.uid)
        if flow is None:
            self._fail("FLOW_RECORD_MISSING")
            return
        if flow.zone != "agv":
            self._fail(f"ZONE_MISMATCH: uid={self.uid} 不在 AGV 载台 "
                       f"(zone={flow.zone})")
            return
        if self._assert_expected_type():
            return
        docked = self.scene.agv_dock_hit(self.scene.agv_root)
        stopped = self._agv_stationary()
        upright = self._agv_upright()
        if not (docked and stopped and upright):
            return
        check = self._check_material_on_tray(rec.sim_handle)
        self._tray_last_check = check
        if not check.ok:
            self._fail(f"LOAD_NOT_ON_TRAY: {check.detail}")
            return
        if rec.state is MaterialState.RELEASED:
            rec.transition_to(MaterialState.ON_TRAY, self.sim_time,
                              f"stage4 confirmed on AGV tray at dock "
                              f"{self.dock_id}")
        speed = self._read_material_speed(rec.sim_handle)
        position = self._read_material_position(rec.sim_handle)
        if speed is None or position is None:
            self._safety_fail("LOAD_STATE_UNREADABLE: 无法读取载台物料位姿/速度")
            return
        step = (math.dist(position, self._tray_prev_pos)
                if self._tray_prev_pos is not None else 0.0)
        self._tray_prev_pos = list(position)
        if (speed <= base.MATERIAL_STABLE_SPEED_TOL
                and step <= base.MATERIAL_STABLE_POS_TOL):
            self._tray_stable_count += 1
        else:
            self._tray_stable_count = 0
        if self._tray_stable_count < base.MATERIAL_STABLE_STEPS:
            return
        try:
            pose = [float(v) for v in self.sim.getObjectPose(
                rec.sim_handle, self.sim.handle_world)]
            box = self.suction.material_box(rec.sim_handle)
        except Exception as exc:
            self._fail(f"MATERIAL_GEOMETRY_UNREADABLE: {exc}")
            return
        tilt = quat_tilt(pose[3:7])
        if tilt > base.MATERIAL_MAX_TILT:
            self._fail(f"MATERIAL_TILT: 载台物料倾斜 {tilt:.3f} rad")
            return
        self._pick_pose = pose
        self._pick_box = box
        self._pick_tcp = contact_pose(pose, box, 0.0)
        rec.transition_to(MaterialState.READY_FOR_PICK, self.sim_time,
                          "AGV tray material docked, stable and measured")
        self._event(
            f"AGV_TRAY_PICK: uid={self.uid} dock={self.dock_id} "
            f"pose=({pose[0]:.4f},{pose[1]:.4f},{pose[2]:.4f})")
        self._begin_motion(UnitTaskState.APPROACHING,
                           "measured pose on AGV tray")

    # ── 类型校验 ────────────────────────────────────────────

    def _assert_expected_type(self) -> bool:
        """返回 True 表示失败 (已记录), 便于调用方提前返回."""
        flow = self.flow.by_uid(self.uid)
        if flow is None:
            self._fail("FLOW_RECORD_MISSING")
            return True
        if flow.item_type not in self.expected_types:
            self._fail(
                f"ITEM_TYPE_MISMATCH: uid={self.uid} 类型 "
                f"{flow.item_type.value} 不在本工位允许 "
                f"{[item.value for item in self.expected_types]}")
            return True
        return False

    # ── 放置 ────────────────────────────────────────────────

    def _build_plan(self):
        """A 工位共享交接台放置后, 撤离到布局派生的 park 位姿.
        阶段四 LIFTING 固定为垂直直连抬升: 吸附点仍在取料面上, 载荷与
        conveyor pad 的正常接触会在“中转构型”起点的载荷检查中被误判为
        碰撞; 垂直笛卡尔抬升只允许起点载荷接触, 仍逐点做 IK/碰撞/分支
        检查 (与 06-15 的 direct_only 转运一致).
        """
        if self.state is UnitTaskState.LIFTING:
            try:
                current_q, current_tcp, start_source = self._planning_start()
            except Exception as exc:
                self._fail(f"POSE_UNREADABLE: {exc}")
                return None
            if self._pick_pose is None or self._pick_box is None:
                self._fail("PICK_TARGET_MISSING")
                return None
            self._event(
                f"LIFT START {start_source} "
                f"q={[round(v, 4) for v in current_q]}")
            pick = contact_pose(self._pick_pose, self._pick_box, 0.0)
            lift = lift_pose(pick, self._place_pose()[3:7])
            goals = [Goal("lift", lift, cartesian=True,
                          lock_joint=self._lock_for("lift"))]
            context = MotionContext(
                phase="LIFT", target_handle=-1, carrying=True,
                snapshot=f"LIFT@t{self.sim_time:.2f}",
                allow_start_load_contact=True)
            plan = self.planner.plan(current_q, goals, context,
                                     start_pose=current_tcp,
                                     direct_only=True)
            if plan.ok:
                return plan
            # D 工位取料点在水平方向较远, 竖直笛卡尔抬升的关节插值偏差
            # 约 6–8 mm, 被 CARTESIAN_PATH_POS_TOL=5 mm 拒绝; 改用同一
            # lift 位姿的关节目标 (cartesian=False), 仍检查关节限位/碰撞/
            # 分支/终点 TCP 和 q0 锁, 不做未检查的关节直赋.
            if plan.status in (PlanStatus.GOAL_CONFIG_INVALID,
                               PlanStatus.PATH_DEVIATION) and \
                    "法向插值偏差" in (plan.message or ""):
                joint_goals = [Goal("lift", lift, cartesian=False,
                                    lock_joint=self._lock_for("lift"))]
                fallback = self.planner.plan(
                    current_q, joint_goals, context,
                    start_pose=current_tcp, direct_only=True)
                fallback.metrics.setdefault("lift_fallback", {
                    "reason": "cartesian_deviation",
                    "cartesian_status": plan.status.value,
                    "cartesian_message": plan.message,
                })
                self._event(
                    f"LIFT fallback joint goal: {plan.status.value} -> "
                    f"{fallback.status.value}")
                return fallback
            # AGV 卸货点的 IK 可能落在与上一任务末端构型最近的“镜像”
            # 肩部解; 该构型下竖直抬升需要转动基座 q[0], 锁定后无解
            # (实测 E dock 在 q[0]=-1.8235 处失败)。解除 q[0] 锁定后
            # 仍按垂直笛卡尔直线逐层求解, 每层做 IK/关节限位/碰撞/
            # 分支连续性检查, 只放弃“基座不动”这一优化约束, 不做
            # 未检查的关节直赋.
            if plan.status is PlanStatus.JOINT_LOCK_UNAVAILABLE:
                free_goals = [Goal("lift", lift, cartesian=True,
                                   lock_joint=None)]
                fallback = self.planner.plan(
                    current_q, free_goals, context,
                    start_pose=current_tcp, direct_only=True)
                fallback.metrics.setdefault("lift_fallback", {
                    "reason": "joint_lock_unavailable",
                    "cartesian_status": plan.status.value,
                    "cartesian_message": plan.message,
                })
                self._event(
                    f"LIFT fallback unlocked q[0]: {plan.status.value} -> "
                    f"{fallback.status.value}")
                return fallback
            return plan
        if (self.state is Stage2TaskState.RETREATING
                and self.park_tcp_pose is not None):
            try:
                current_q, current_tcp, start_source = self._planning_start()
            except Exception as exc:
                self._fail(f"POSE_UNREADABLE: {exc}")
                return None
            self._event(
                f"PARK START {start_source} "
                f"q={[round(v, 4) for v in current_q]}")
            goals = [Goal("park", list(self.park_tcp_pose), cartesian=True,
                          allow_target_contact=True)]
            context = MotionContext(
                phase="RETREAT", target_handle=-1, carrying=False,
                snapshot=f"PARK@t{self.sim_time:.2f}")
            return self.planner.plan(current_q, goals, context,
                                     start_pose=current_tcp)
        return super()._build_plan()

    def _placing_target_ready(self) -> bool:
        if self.sink == "agv":
            docked = self.scene.agv_dock_hit(self.scene.agv_root)
            stopped = self._agv_stationary() and self._agv_upright()
            if docked and stopped:
                self._dock_wait_since = None
                return True
            if self._dock_wait_since is None:
                self._dock_wait_since = self.sim_time
            elif (self.sim_time - self._dock_wait_since
                  > base.AGV_DOCK_TIMEOUT):
                self._fail(
                    f"AGV_DOCK_TIMEOUT: 下探前 AGV 未就绪 "
                    f"(dock_sensor={docked}, stopped={stopped})")
            return False

        if self.sink == "conveyor":
            if self.line_fault:
                self._fail("CONVEYOR_FAULT: 放料线已被故障停带")
                return False
            self.conveyor.stop(self.line.conveyor)
            speed = read_conveyor_speed(
                self.sim, self.scene.handle(self.line.conveyor),
                base.CONVEYOR_STATE_KEY)
            still = False
            if speed.valid and abs(float(speed.value or 0.0)) <= \
                    cfg.LINE_STOP_VEL_TOL:
                still = True
            elif not speed.valid:
                pad = self._pad_position()
                still = (pad is not None and self._latch_pad_prev is not None
                         and math.dist(pad, self._latch_pad_prev)
                         <= s2cfg.CONVEYOR_STOP_POS_TOL)
                self._latch_pad_prev = pad
            if still:
                self._line_stop_count += 1
            else:
                self._line_stop_count = 0
            if self._line_stop_count >= cfg.LINE_STOP_CONFIRM_STEPS:
                return True
            if self._line_stop_since is None:
                self._line_stop_since = self.sim_time
            elif (self.sim_time - self._line_stop_since
                  > cfg.LINE_STOP_TIMEOUT):
                self._fail(
                    "SINK_CONVEYOR_NOT_STOPPED: 放料前带面未停止 "
                    f"(speed={speed.value}, valid={speed.valid})")
            return False

        return True

    # ── 线/安全 ─────────────────────────────────────────────

    def _stop_line(self) -> bool:
        try:
            if self.line_fault:
                return True
            self.conveyor.stop(self.line.conveyor)
            return True
        except Exception:
            return False

    def _hold_after_latch(self) -> None:
        try:
            actual = self.ik.joint_positions()
            self.ik.apply_joints(actual, motion=_motion_params())
        except Exception:
            pass
        self._stop_line()
        if not self._hold_logged:
            self._hold_logged = True
            self._event("SAFETY_HOLD: 已取消剩余轨迹并保持最后实测构型")

    def _safety_fail(self, reason: str,
                     details: Optional[dict] = None) -> None:
        if self.safety.engaged:
            return
        self._capture_motion_report(f"{s2cfg.SAFETY_LATCH_CODE}: {reason}")
        self.safety.request_stop(reason, sim_time=self.sim_time,
                                 details=details or {})
        if self._executor is not None:
            self._executor.cancel()
            self._executor = None
        self._plan = None
        ok = self._stop_line()
        self.safety.note_stop_command(ok, sim_time=self.sim_time)
        self._hold_after_latch()
        self._fail(f"{s2cfg.SAFETY_LATCH_CODE}: {reason}")

    def _confirm_latch_stop(self) -> None:
        speed = read_conveyor_speed(
            self.sim, self.scene.handle(self.line.conveyor),
            base.CONVEYOR_STATE_KEY)
        agv_stopped = self._agv_stationary() if self.agv is not None else True
        if speed.valid:
            confirmed = self.safety.confirm_stop(
                speed_known=True, speed=speed.value,
                agv_stopped=agv_stopped, source=speed.source)
        else:
            pad = self._pad_position()
            pad_still = (pad is not None and self._latch_pad_prev is not None
                         and math.dist(pad, self._latch_pad_prev)
                         <= s2cfg.CONVEYOR_STOP_POS_TOL)
            self._latch_pad_prev = pad
            confirmed = self.safety.confirm_stop_from_position(
                pad_still=bool(pad_still), agv_stopped=agv_stopped)
        if confirmed and not self._stop_confirmed_logged:
            self._stop_confirmed_logged = True
            self._event(
                f"SAFETY_STOP_CONFIRMED: speed={self.safety.latch.stop_speed} "
                f"source={self.safety.latch.stop_source}")

    def _fail(self, reason: str) -> None:
        if self.state is UnitTaskState.FAILED:
            return
        self._capture_motion_report(reason)
        if self._executor is not None:
            self._executor.cancel()
            self._executor = None
        self._plan = None
        self._stop_line()
        self._retry_at = None
        self.state = UnitTaskState.FAILED
        self.failure_reason = reason
        self._event(f"FAILED: {reason}")
        if self.logger:
            self.logger.warning("[STAGE4][%s] uid=%s 任务失败: %s",
                                self.unit_id, self.uid, reason)

    # ── 板载工具 ────────────────────────────────────────────

    def _update_agv(self) -> None:
        """阶段四 AGV 由协调器航线独占, 单岛控制器绝不 set_cmd/stop."""
        return None

    def _pad_velocity(self) -> float:
        try:
            return float(self.conveyor.read_actual_velocity(
                self.line.conveyor))
        except Exception:
            return 0.0

    def _pad_position(self) -> Optional[list[float]]:
        if self._pad_handle is None:
            self._pad_handle = self._find_pad()
        if self._pad_handle < 0:
            return None
        try:
            belt = self.scene.handle(self.line.conveyor)
            return [float(v) for v in
                    self.sim.getObjectPosition(self._pad_handle, belt)]
        except Exception:
            self._pad_handle = -1
            return None

    def _find_pad(self) -> int:
        from stage1.scene import alias_of, subtree
        try:
            belt = self.scene.handle(self.line.conveyor)
        except Exception:
            return -1
        if belt < 0:
            return -1
        for handle in subtree(self.sim, belt):
            try:
                if (self.sim.getObjectType(handle)
                        == self.sim.object_shape_type
                        and alias_of(self.sim, handle) == "pad"):
                    return int(handle)
            except Exception:
                continue
        return -1

    def summary(self) -> dict:
        data = super().summary()
        data.update({
            "controller_id": cfg.STAGE4_CONTROLLER_ID,
            "phase": cfg.STAGE4_PHASE,
            "line": self.line.line_id,
            "line_fault": bool(self.line_fault),
            "expected_types": [item.value for item in self.expected_types],
            "sink_kind": self.sink,
            "sink_zone": self.zone,
            "flow": (self.flow.by_uid(self.uid).as_dict()
                     if self.flow.by_uid(self.uid) is not None else None),
        })
        return data
