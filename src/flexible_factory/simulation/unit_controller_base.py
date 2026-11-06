"""unit_controller_base.py — v1 基础层单站单步闭环.

固定执行顺序 (sim.step() 永远最后):
  1. 读取通信/仿真状态
  2. conveyor 驱动 + HYBRID_DYNAMIC 物料输送
  3. 读取 Sensor_PickStation; 命中目标 UID 立即下发零速
  4. 推进任务状态机:
       停带确认 → 物料稳定 → 读取实测位姿 → simIK 抓取
  5. TCP/吸盘传感器确认 + 吸附
  6. 抬升 / 转运 / 放置
  7. 释放 + 稳定等待
  8. AGV 轮速
  9. 事件日志
 10. sim.step()

抓取流程采用步骤 1.1 B 组结论: 不设挡停体, 传感器命中后主动停带,
等待带面和物料稳定, 再按仿真器实测位姿生成 TCP 目标。抓取目标在
停稳时刻固定一次, 后续接近/抬升都复用该目标。
传感器命中只表示物理条件满足; 吸附必须同时匹配当前任务 UID。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional

from . import config_base as cfg
from .conveyor import BeltGeometry, TransportFault, compensation_allowed, transport_step
from .layout_base import default_layout
from .material_manager import MaterialState
from .pioneer import AGVState
from .scene_base import alias_of, subtree


class UnitTaskState(Enum):
    IDLE              = auto()
    WAITING_FOR_ITEM  = auto()
    STOPPING          = auto()
    MATERIAL_SETTLING = auto()
    APPROACHING       = auto()
    ATTACHING         = auto()
    LIFTING           = auto()
    TRANSFERRING      = auto()
    PLACING           = auto()
    RELEASING         = auto()
    SETTLING          = auto()
    DONE              = auto()
    FAILED            = auto()


class MismatchReason(Enum):
    NO_HIT       = auto()
    UID_MISMATCH = auto()


@dataclass
class PickMismatch:
    sim_time: float
    reason: MismatchReason
    hit_handle: int
    expected_handle: int


def _dist3(a, b) -> float:
    return math.sqrt(sum((float(a[i]) - float(b[i])) ** 2 for i in range(3)))


def quat_tilt(q) -> float:
    """物体局部 Z 轴与世界 Z 轴的夹角 (rad)。"""
    x, y = float(q[0]), float(q[1])
    zz = max(-1.0, min(1.0, 1.0 - 2.0 * (x * x + y * y)))
    return math.acos(zz)


_MOTION_STATES = {
    UnitTaskState.APPROACHING: UnitTaskState.ATTACHING,
    UnitTaskState.LIFTING: UnitTaskState.TRANSFERRING,
    UnitTaskState.TRANSFERRING: UnitTaskState.PLACING,
}


class Stage1UnitController:
    def __init__(self, sim, scene, materials, conveyor, belt_geometry: BeltGeometry,
                 ik, suction, agv=None, target: str = "station", uid: int = 1,
                 fault: str = cfg.FAULT_NONE,
                 layout=None,
                 logger: Optional[logging.Logger] = None,
                 pick_timeout: Optional[float] = None,
                 task_timeout: Optional[float] = None) -> None:
        self.sim = sim
        self.scene = scene
        self.materials = materials
        self.conveyor = conveyor
        self.belt_geometry = belt_geometry
        self.ik = ik
        self.suction = suction
        self.agv = agv
        self.target = target
        self.uid = uid
        self.fault = fault
        self.layout = default_layout() if layout is None else layout
        self.logger = logger
        self.pick_timeout = cfg.PICK_SENSOR_TIMEOUT if pick_timeout is None else pick_timeout
        self.task_timeout = cfg.TASK_TIMEOUT if task_timeout is None else task_timeout

        self.state = UnitTaskState.IDLE
        self.failure_reason = ""
        self.last_mismatch: Optional[PickMismatch] = None
        self.sim_time = 0.0
        self.agv_blocked = False
        self.compensation_count = 0
        self.event_log: list[tuple[float, str]] = []

        self._started_at: Optional[float] = None
        self._wait_started_at: Optional[float] = None
        self._motion_started = False
        self._stages: list[list[float]] = []
        self._stage_index = 0
        self._motion_pose: Optional[list[float]] = None
        self._motion_cmd: Optional[list[float]] = None
        self._motion_started_at = 0.0
        self._ik_retries = 0
        self._retry_at: Optional[float] = None
        self._settle_until = 0.0
        self._settle_max_speed = 0.0
        self._settle_step = 0
        self._mismatch_logged = False
        self._mismatch_since: Optional[float] = None
        self._dock_wait_since: Optional[float] = None
        self._attach_wait_since: Optional[float] = None
        self._agv_started = False
        self._agv_start_pos: Optional[list[float]] = None
        self._agv_material_start: Optional[list[float]] = None
        self._agv_started_at = 0.0
        self._agv_done = False

        # 停带 → 稳定 → 实测位姿 (B 组流程)
        self._pick_tcp: Optional[list[float]] = None
        self._pick_pose: Optional[list[float]] = None
        self._stop_command_time: Optional[float] = None
        self._pad_handle: Optional[int] = None   # None=未查找, -1=未找到
        self._pad_pos_at_stop: Optional[list[float]] = None
        self._pad_prev_pos: Optional[list[float]] = None
        self._pad_confirm = 0
        self._vel_mismatch = 0
        self._stable_prev_pos: Optional[list[float]] = None
        self._stable_count = 0
        self._settle_started_at: Optional[float] = None

    # ── 单步 ────────────────────────────────────────────────

    def step(self) -> None:
        """阶段一单控制器循环: 全部逻辑 + 推进一个仿真步."""
        self.pre_tick()
        self.sim.step()
        self.post_tick()

    def pre_tick(self) -> None:
        """本控制步的全部逻辑, **不**推进物理引擎.

        阶段三需要两台控制器共享同一个时钟: 协调器先对所有活动控制器调用
        `pre_tick()`, 再统一下发 AGV 命令、`sim.step()`, 最后调用
        `post_tick()`. 阶段一/二单控制器路径的 `step()` 行为保持不变.
        """
        self.sim_time = self._sim_time()
        if self._started_at is None:
            self._started_at = self.sim_time

        self._probe_comm()                 # 1
        self._update_conveyor()            # 2
        self._read_pick_sensor()           # 3
        self._advance_task()               # 4-8
        self._update_agv()                 # 9
        self._log_step()                   # 10

    def post_tick(self) -> None:
        """物理步进后的只读检查; 阶段一没有运行期联锁."""
        return None

    # ── 1. 通信/仿真状态 ────────────────────────────────────

    def _probe_comm(self) -> None:
        try:
            state = int(self.sim.getSimulationState())
        except Exception as exc:
            self._fail(f"COMMUNICATION_LOST: {exc}")
            return
        stopped = getattr(self.sim, "simulation_stopped", None)
        if stopped is not None and state == stopped:
            self._fail("SIMULATION_STOPPED")
        if self.sim_time - self._started_at > self.task_timeout:
            self._fail("TASK_TIMEOUT")

    # ── 2. conveyor ─────────────────────────────────────────

    def _update_conveyor(self) -> None:
        rec = self.materials.by_uid(self.uid)
        if rec is None:
            self._fail("UID_NOT_REGISTERED")
            return
        if self.state not in (UnitTaskState.IDLE, UnitTaskState.WAITING_FOR_ITEM):
            return
        if rec.state not in (MaterialState.SPAWNED, MaterialState.ON_CONVEYOR):
            return

        if self.fault == cfg.FAULT_NO_TARGET:
            self.conveyor.set_speed(cfg.ALIASES["conveyor"], 0.0)
            return
        if self.fault == cfg.FAULT_UID_MISMATCH:
            # 故障注入: 目标留在入口, 取料位由另一物料占用
            self.conveyor.set_speed(cfg.ALIASES["conveyor"], 0.0)
            return

        self.conveyor.set_speed(cfg.ALIASES["conveyor"], cfg.CONVEYOR_SPEED)
        if rec.sim_handle is None:
            self._fail("MATERIAL_HAS_NO_HANDLE")
            return

        pose = list(self.sim.getObjectPose(rec.sim_handle, self.sim.handle_world))
        try:
            compensation_allowed(pose[:3], cfg.CONVEYOR_SURFACE_Z,
                                 geometry=self.belt_geometry)
        except TransportFault as exc:
            rec.transition_to(MaterialState.FAULT, self.sim_time,
                              f"CONVEYOR_FAULT: {exc}")
            self._fail(f"CONVEYOR_FAULT: {exc}")
            return

        step = transport_step(pose[:3], self.belt_geometry,
                              cfg.CONVEYOR_SPEED, cfg.SIM_STEP)
        if step.delta > 0.0:
            self.sim.setObjectPosition(rec.sim_handle, step.new_position,
                                       self.sim.handle_world)
            self.compensation_count += 1
            self._event(
                f"HYBRID_DYNAMIC compensation Δ={step.delta:.4f} m "
                f"progress={step.progress:.3f}")
        if rec.state is MaterialState.SPAWNED:
            rec.transition_to(MaterialState.ON_CONVEYOR, self.sim_time,
                              "entered conveyor effective zone")
        if step.at_stop:
            # B 组流程只允许传感器命中触发停带。物料走到行程末端仍未命中,
            # 必须立即失败并停带, 不能继续把物料带出带尾。
            self._fail("NO_SENSOR_TRIGGER_AT_STOP: "
                       "物料到达停止区但传感器未命中目标")

    # ── 3. 取料传感器 ───────────────────────────────────────

    def _read_pick_sensor(self) -> None:
        if self.state is not UnitTaskState.WAITING_FOR_ITEM:
            return
        rec = self.materials.by_uid(self.uid)
        if rec is None or rec.sim_handle is None:
            return
        expected = int(rec.sim_handle)
        hits = self.scene.pick_sensor_hits(self.materials.handles())
        if expected in hits:
            # 步骤 1.1 B 组: 传感器命中目标 UID 即下发零速, 不等行程进度。
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
                    "[MISMATCH] PICK_MISMATCH: 传感器命中 %s, 当前任务 uid=%s "
                    "handle=%s; 不吸附", wrong, self.uid, expected)
                self._mismatch_logged = True
            return
        if self.last_mismatch is None or \
                self.last_mismatch.reason is MismatchReason.UID_MISMATCH:
            self.last_mismatch = PickMismatch(
                self.sim_time, MismatchReason.NO_HIT, -1, expected)
        self._mismatch_since = None

    # ── 3b. 主动停带与物料稳定 (步骤 1.1 B 组) ──────────────

    def _command_pick_stop(self, reason: str) -> None:
        """传感器命中目标后主动零速; 记录停带时刻与 pad 参考位姿。"""
        if self._stop_command_time is not None:
            return
        self.conveyor.stop(cfg.ALIASES["conveyor"])
        self._stop_command_time = self.sim_time
        self._pad_pos_at_stop = self._pad_position()
        self._pad_prev_pos = self._pad_pos_at_stop
        self._pad_confirm = 0
        self._vel_mismatch = 0
        self._enter(UnitTaskState.STOPPING,
                    f"conveyor stop commanded ({reason})")

    def _advance_stopping(self) -> None:
        """等待带面实际停止; 速度回读不可信时用 pad 位姿差确认。"""
        if self._stop_command_time is None:
            self._command_pick_stop("unspecified")
            return
        if self.sim_time - self._stop_command_time > cfg.PICK_STOP_TIMEOUT:
            self._fail("PAD_STOP_TIMEOUT: 带面未在限时内停止")
            return

        vel = self._pad_velocity()
        pos = self._pad_position()
        moved = None
        pos_ok = True
        if pos is not None and self._pad_prev_pos is not None:
            moved = _dist3(pos, self._pad_prev_pos)
            pos_ok = moved <= cfg.PICK_STOP_POS_TOL
        self._pad_prev_pos = pos
        vel_ok = vel <= cfg.PICK_STOP_VEL_TOL

        if pos_ok and not vel_ok:
            self._vel_mismatch += 1
        else:
            self._vel_mismatch = 0
        # 速度回读可信时以速度为主; 若 pad 已连续多步不动而回读仍非零,
        # 视为回读不可信, 改用位姿差确认 (06-07 §4.3-3)。
        velocity_trusted = (vel_ok
                            or self._vel_mismatch < cfg.PICK_STOP_CONFIRM_STEPS)
        if pos_ok and (vel_ok or not velocity_trusted):
            self._pad_confirm += 1
        else:
            self._pad_confirm = 0
        if self._pad_confirm < cfg.PICK_STOP_CONFIRM_STEPS:
            return

        rec = self.materials.by_uid(self.uid)
        if rec is None or rec.sim_handle is None:
            self._fail("UID_NOT_REGISTERED")
            return
        stop_travel = (_dist3(pos, self._pad_pos_at_stop)
                       if pos is not None and self._pad_pos_at_stop is not None
                       else None)
        self._event(f"pad stopped: vel={vel:.4f} m/s "
                    f"step_moved={moved} stop_travel={stop_travel}")
        self._settle_started_at = self.sim_time
        self._stable_count = 0
        self._stable_prev_pos = self._material_position(rec.sim_handle)
        self._enter(UnitTaskState.MATERIAL_SETTLING,
                    "pad stopped, waiting material stable")

    def _advance_material_settling(self) -> None:
        """连续稳定若干步后读取实测位姿并固定抓取目标。"""
        rec = self.materials.by_uid(self.uid)
        if rec is None or rec.sim_handle is None:
            self._fail("UID_NOT_REGISTERED")
            return

        speed = self._material_speed(rec.sim_handle)
        pos = self._material_position(rec.sim_handle)
        step_moved = (_dist3(pos, self._stable_prev_pos)
                      if self._stable_prev_pos is not None else 0.0)
        self._stable_prev_pos = list(pos)
        if (speed <= cfg.MATERIAL_STABLE_SPEED_TOL
                and step_moved <= cfg.MATERIAL_STABLE_POS_TOL):
            self._stable_count += 1
        else:
            self._stable_count = 0

        if self._stable_count < cfg.MATERIAL_STABLE_STEPS:
            if (self._settle_started_at is not None
                    and self.sim_time - self._settle_started_at
                    > cfg.MATERIAL_SETTLE_TIMEOUT):
                self._fail(
                    "MATERIAL_SETTLE_TIMEOUT: 物料未在限时内稳定 "
                    f"(speed={speed:.3f} m/s, step={step_moved:.4f} m)")
            return

        pose = [float(v) for v in
                self.sim.getObjectPose(rec.sim_handle, self.sim.handle_world)]
        tilt = quat_tilt(pose[3:7])
        if not cfg.ALLOWED_PICK_REGION.contains(pose):
            self._fail(
                "OUT_OF_ALLOWED_REGION: 物料停稳后不在允许抓取区 "
                f"(x={pose[0]:.4f}, y={pose[1]:.4f}, z={pose[2]:.4f})")
            return
        if tilt > cfg.MATERIAL_MAX_TILT:
            self._fail(f"MATERIAL_TILT: 物料倾斜 {tilt:.3f} rad")
            return

        self._pick_pose = pose
        self._pick_tcp = [pose[0], pose[1],
                          pose[2] + cfg.MATERIAL_HALF_HEIGHT,
                          *cfg.TOOL_TARGET_QUATERNION]
        if rec.state is MaterialState.SPAWNED:
            rec.transition_to(MaterialState.ON_CONVEYOR, self.sim_time,
                              "settled on conveyor")
        if rec.state is MaterialState.ON_CONVEYOR:
            rec.transition_to(MaterialState.READY_FOR_PICK, self.sim_time,
                              "stopped and stable, measured pose")
        self._event(
            f"material stable at ({pose[0]:.4f},{pose[1]:.4f},"
            f"{pose[2]:.4f}) tilt={tilt:.3f}; pick target fixed")
        self._begin_motion(UnitTaskState.APPROACHING,
                           "measured pose after active stop")

    # ── 4-8. 任务状态机 ─────────────────────────────────────

    def _advance_task(self) -> None:
        if self.state is UnitTaskState.FAILED:
            self._maybe_retry()
            return
        if self.state is UnitTaskState.DONE:
            return

        if self.state is UnitTaskState.IDLE:
            self._enter(UnitTaskState.WAITING_FOR_ITEM, "task started")

        if self.state is UnitTaskState.WAITING_FOR_ITEM:
            if self._wait_started_at is None:
                self._wait_started_at = self.sim_time
            if (self._mismatch_since is not None
                    and self.sim_time - self._mismatch_since
                    > cfg.MISMATCH_TIMEOUT):
                self._fail("PICK_MISMATCH: 取料位被非目标物料占用")
                return
            if self.sim_time - self._wait_started_at > self.pick_timeout:
                if self.fault == cfg.FAULT_SENSOR_DISABLED:
                    self._fail("SENSOR_TIMEOUT: 传感器无读数")
                elif self.last_mismatch and self.last_mismatch.reason is MismatchReason.UID_MISMATCH:
                    self._fail("PICK_MISMATCH: 目标 UID 未到达取料位")
                else:
                    self._fail("SENSOR_TIMEOUT: 目标物料未到达取料位")
            return

        if self.state is UnitTaskState.STOPPING:
            self._advance_stopping()
            return

        if self.state is UnitTaskState.MATERIAL_SETTLING:
            self._advance_material_settling()
            return

        if self.state in _MOTION_STATES:
            self._run_motion()
            return

        if self.state is UnitTaskState.ATTACHING:
            rec = self.materials.by_uid(self.uid)
            if rec is None or rec.sim_handle is None:
                self._fail("UID_NOT_REGISTERED")
                return
            tool_speed = self._tool_speed()
            if tool_speed > cfg.SUCTION_MAX_TOOL_SPEED:
                if self._attach_wait_since is None:
                    self._attach_wait_since = self.sim_time
                elif (self.sim_time - self._attach_wait_since
                      > cfg.ATTACH_SETTLE_TIMEOUT):
                    self._fail(
                        f"TOOL_NOT_SETTLED: TCP 速度 {tool_speed:.3f} m/s")
                    return
                return
            self._attach_wait_since = None
            result = self.suction.attach(self.uid, self.sim_time)
            if not result.ok:
                self._fail(f"ATTACH_REJECTED: {result.code} ({result.message})")
                return
            self._begin_motion(UnitTaskState.LIFTING, reason="attached")
            return

        if self.state is UnitTaskState.PLACING:
            pose = self._place_pose()
            if not self.ik.reached(pose):
                self._fail("PLACE_POSITION_ERROR: TCP 未到达放置位")
                return
            if self.target == "agv":
                docked = self.scene.agv_dock_hit(self.scene.agv_root)
                stopped = self._agv_stationary() and self._agv_upright()
                if not (docked and stopped):
                    if self._dock_wait_since is None:
                        self._dock_wait_since = self.sim_time
                    elif (self.sim_time - self._dock_wait_since
                          > cfg.AGV_DOCK_TIMEOUT):
                        self._fail(
                            "AGV_DOCK_TIMEOUT: dock_sensor="
                            f"{docked}, stopped={stopped}")
                        return
                    return
                self._dock_wait_since = None
            self._enter(UnitTaskState.RELEASING, "at place pose")
            return

        if self.state is UnitTaskState.RELEASING:
            material_pose = self._material_pose_for_target()
            if self.fault == cfg.FAULT_RELEASE_UNSTABLE:
                material_pose[2] += 0.30
            support_z = (self.layout.station_top_z
                         if self.target == "station" else cfg.AGV_TRAY_TOP_Z)
            self._settle_max_speed = 0.0
            self._settle_step = 0
            result = self.suction.release(self.uid, self.sim_time,
                                          material_pose, support_z=support_z)
            if not result.ok:
                self._fail(f"RELEASE_FAILED: {result.message}")
                return
            if result.penetration:
                self._fail("RELEASE_FAILED: 物料穿透承载面")
                return
            if not result.stable:
                self._fail("RELEASE_UNSTABLE: 载荷未稳定")
                return
            self._settle_until = self.sim_time + cfg.SUCTION_SETTLE_STEPS * cfg.SIM_STEP
            self._enter(UnitTaskState.SETTLING, "released, waiting settle")
            return

        if self.state is UnitTaskState.SETTLING:
            rec = self.materials.by_uid(self.uid)
            self._settle_step += 1
            if rec is not None and rec.sim_handle is not None:
                speed = self._material_speed(rec.sim_handle)
                if self._settle_step > cfg.SUCTION_SETTLE_GRACE_STEPS:
                    self._settle_max_speed = max(self._settle_max_speed, speed)
                if self.logger:
                    pos = self.sim.getObjectPosition(
                        rec.sim_handle, self.sim.handle_world)
                    self.logger.debug(
                        "[SETTLE] uid=%s speed=%.4f pos=(%.4f,%.4f,%.4f)",
                        self.uid, speed, pos[0], pos[1], pos[2])
            if self.sim_time >= self._settle_until:
                if self._settle_max_speed > cfg.SUCTION_RELEASE_MAX_SPEED:
                    self._fail(
                        "RELEASE_UNSTABLE: 稳定窗口内物料最大速度 "
                        f"{self._settle_max_speed:.3f} m/s")
                    return
                if rec is not None and rec.sim_handle is not None:
                    z = float(self.sim.getObjectPosition(
                        rec.sim_handle, self.sim.handle_world)[2])
                    support = (self.layout.station_top_z
                               if self.target == "station"
                               else cfg.AGV_TRAY_TOP_Z)
                    if z - cfg.MATERIAL_HALF_HEIGHT < \
                            support - cfg.SUCTION_SETTLE_Z_TOL:
                        self._fail("RELEASE_FAILED: 物料穿透承载面")
                        return
                next_state = self._settling_complete_state()
                if self.state is not UnitTaskState.FAILED:
                    self._enter(
                        next_state,
                        "task complete" if next_state is UnitTaskState.DONE
                        else "settling complete")
            return

    # ── 运动 ────────────────────────────────────────────────

    def _begin_motion(self, state: UnitTaskState, reason: str = "") -> None:
        """进入一个运动状态, 生成预接近/目标两级路径。

        先到目标点上方 ARM_APPROACH_CLEARANCE, 再垂直下探; 避免机械臂
        从侧面扫过把物料推离取料位 (4.10 实测会发生)。
        """
        if state is UnitTaskState.APPROACHING and self._pick_tcp is None:
            self._fail("PICK_TARGET_MISSING: 未读取到实测抓取目标")
            return
        self.state = state
        self._stages = self._stages_for(state)
        self._stage_index = 0
        self._motion_started = False
        self._motion_pose = None
        self._motion_cmd = None
        self._event(f"{state.name}: {reason or 'motion started'}")

    def _stages_for(self, state: UnitTaskState) -> list[list[float]]:
        if state is UnitTaskState.APPROACHING:
            # 抓取目标在停稳时刻固定一次, 接近与下探使用同一目标,
            # 不在运动过程中重新读取物料位姿。
            pick = list(self._pick_tcp)
            pre = list(pick)
            pre[2] += cfg.ARM_APPROACH_CLEARANCE
            return [pre, pick]
        if state is UnitTaskState.LIFTING:
            lift = list(self._pick_tcp)
            lift[2] += cfg.ARM_APPROACH_CLEARANCE
            return [lift]
        if state is UnitTaskState.TRANSFERRING:
            place = self._place_pose()
            pre = list(place)
            pre[2] += cfg.ARM_APPROACH_CLEARANCE
            return [pre, place]
        return [self._place_pose()]

    def _run_motion(self) -> None:
        if not self._stages:
            self._stages = self._stages_for(self.state)

        if not self._motion_started:
            pose = self._stages[self._stage_index]
            result = self.ik.solve(pose)
            if not result.success:
                self._ik_failed(result.message)
                return
            self.ik.apply_joints(result.joints)
            self._motion_pose = pose
            self._motion_cmd = list(result.joints)
            self._motion_started = True
            self._motion_started_at = self.sim_time
            return

        if (self.ik.converged(self._motion_cmd)
                and self.ik.reached(self._motion_pose)
                and self._ik_limits_and_stop()):
            self._motion_started = False
            self._motion_pose = None
            self._motion_cmd = None
            if self._stage_index + 1 < len(self._stages):
                self._stage_index += 1
                self._event(
                    f"{self.state.name}: 阶段 {self._stage_index}/"
                    f"{len(self._stages) - 1} 完成")
                return
            nxt = _MOTION_STATES[self.state]
            self._stages = []
            self._stage_index = 0
            if nxt in _MOTION_STATES:
                self._begin_motion(nxt, reason=f"{self.state.name} done")
            else:
                self._enter(nxt, f"motion to {self.state.name} done")
            return

        if self.sim_time - self._motion_started_at > cfg.ARM_MOVE_TIMEOUT:
            # 重新求解一次; 连续超时才失败
            self._motion_started = False
            self._ik_retries += 1
            if self._ik_retries > cfg.ARM_IK_MAX_RETRIES:
                self._fail("IK_TIMEOUT: 关节未在超时内收敛")

    def _place_pose(self) -> list[float]:
        pose = self.layout.tcp_pose_for_target(self.target)
        if self.fault == cfg.FAULT_IK_UNREACHABLE:
            pose[0] += cfg.FAULT_IK_OFFSET[0]
            pose[1] += cfg.FAULT_IK_OFFSET[1]
            pose[2] += cfg.FAULT_IK_OFFSET[2]
        return pose

    def _material_pose_for_target(self) -> list[float]:
        return self.layout.material_pose_for_target(self.target)

    # ── 9. AGV ──────────────────────────────────────────────

    def _update_agv(self) -> None:
        if self.agv is None:
            return
        if self.logger and self.agv is not None and hasattr(self.agv, "pose"):
            try:
                p = self.agv.pose()
                self.logger.debug(
                    "[AGVPOSE] t=%.2f pos=(%.3f,%.3f,%.3f) quat=(%.3f,%.3f,%.3f,%.3f)",
                    self.sim_time, p[0], p[1], p[2], p[3], p[4], p[5], p[6])
            except Exception:
                pass
        if self.state in (UnitTaskState.PLACING, UnitTaskState.RELEASING,
                          UnitTaskState.SETTLING):
            self.agv.stop()      # 装卸期间车体必须静止
            return
        if self.target != "agv" or self.state is not UnitTaskState.DONE:
            return
        if self._agv_done:
            return

        if not self._agv_started:
            self._agv_started = True
            self._agv_started_at = self.sim_time
            self._agv_start_pos = self._agv_position()
            rec = self.materials.by_uid(self.uid)
            if rec is not None and rec.sim_handle is not None:
                self._agv_material_start = list(self.sim.getObjectPosition(
                    rec.sim_handle, self.sim.handle_world))
            if self._agv_start_pos is None:
                self._fail("AGV_POSE_UNAVAILABLE: 无法读取车体初始位姿")
                return

        self.agv.set_cmd(cfg.AGV_DRIVE_SPEED, 0.0)
        pos = self._agv_position()
        if pos is None:
            self.agv.stop()
            self._fail("AGV_POSE_UNAVAILABLE: 行驶中无法读取车体位姿")
            return
        travelled = pos[0] - self._agv_start_pos[0]
        elapsed = self.sim_time - self._agv_started_at
        if travelled >= cfg.AGV_DRIVE_DISTANCE:
            self.agv.stop()
            self._agv_done = True
            self._event(f"AGV 轮速行驶完成: {travelled:.3f} m / {elapsed:.2f} s")
        elif elapsed > cfg.AGV_BLOCK_TIMEOUT and self._agv_stationary():
            self.agv.stop()
            self.agv.state = AGVState.BLOCKED
            self.agv_blocked = True
            self._agv_done = True
            self._event(
                f"AGV_BLOCKED: 轮速已下发但车体停滞 "
                f"(travelled={travelled:.3f} m, elapsed={elapsed:.2f} s)")

    def _agv_stationary(self) -> bool:
        try:
            return bool(self.agv.is_stopped())
        except Exception:
            return False

    def _agv_upright(self) -> bool:
        try:
            return bool(self.agv.is_upright())
        except Exception:
            return True

    def _ik_limits_and_stop(self) -> bool:
        try:
            return bool(self.ik.joints_within_limits() and self.ik.stopped())
        except Exception:
            return True

    def _tool_speed(self) -> float:
        try:
            return float(self.ik.tcp_speed())
        except Exception:
            return 0.0

    def _material_speed(self, handle: int) -> float:
        try:
            v = self.sim.getObjectVelocity(handle)
            lin = v[0] if isinstance(v, (tuple, list)) and len(v) == 2 else list(v)[:3]
            return math.sqrt(sum(float(c) * float(c) for c in lin[:3]))
        except Exception:
            return 0.0

    def _material_position(self, handle: int) -> list[float]:
        try:
            return [float(v) for v in
                    self.sim.getObjectPosition(handle, self.sim.handle_world)]
        except Exception:
            return [0.0, 0.0, 0.0]

    def _conveyor_pick_alias(self) -> str:
        """本控制器取料/停带所针对的传送带别名.

        基础层只有一条线; 双站/五岛层按实际物流线覆盖, 使 pad 读数与
        停带监视读取同一条带.
        """
        return cfg.ALIASES["conveyor"]

    def _pad_velocity(self) -> float:
        try:
            return float(self.conveyor.read_actual_velocity(
                self._conveyor_pick_alias()))
        except Exception:
            return 0.0

    def _pad_position(self) -> Optional[list[float]]:
        """一个 pad 相对传送带根的位姿; 句柄在首次使用时查找并缓存。

        内置 conveyor 脚本会在仿真启动时重建 pad, 因此不能使用构建期句柄。
        """
        if self._pad_handle is None:
            self._pad_handle = self._find_pad()
        if self._pad_handle < 0:
            return None
        try:
            belt = self.scene.handle(self._conveyor_pick_alias())
            return [float(v) for v in
                    self.sim.getObjectPosition(self._pad_handle, belt)]
        except Exception:
            self._pad_handle = -1
            return None

    def _find_pad(self) -> int:
        try:
            belt = self.scene.handle(self._conveyor_pick_alias())
        except Exception:
            return -1
        if belt < 0:
            return -1
        for h in subtree(self.sim, belt):
            try:
                if (self.sim.getObjectType(h) == self.sim.object_shape_type
                        and alias_of(self.sim, h) == "pad"):
                    return int(h)
            except Exception:
                continue
        return -1

    def _agv_position(self):
        try:
            return list(self.agv.position())
        except Exception:
            return None

    # ── 失败/重试 ───────────────────────────────────────────

    def _ik_failed(self, message: str) -> None:
        rec = self.materials.by_uid(self.uid)
        if rec is not None and rec.state is MaterialState.HELD_BY_SUCTION:
            # 物料已在吸盘上, 不再重试 (避免带着物料反复重规划)
            self._fail(f"IK_FAILED: {message} (物料已吸附, 终止)")
            return
        self._ik_retries += 1
        if self._ik_retries > cfg.ARM_IK_MAX_RETRIES:
            self._fail(f"IK_FAILED: {message}")
            return
        self.state = UnitTaskState.FAILED
        self.failure_reason = f"IK_FAILED: {message}"
        self._retry_at = self.sim_time + cfg.ARM_IK_RETRY_DELAY
        self._event(f"IK 失败, {cfg.ARM_IK_RETRY_DELAY:.1f}s 后重试 "
                    f"({self._ik_retries}/{cfg.ARM_IK_MAX_RETRIES})")

    def _maybe_retry(self) -> None:
        if self._retry_at is None or self.sim_time < self._retry_at:
            return
        rec = self.materials.by_uid(self.uid)
        if rec is None or rec.state is not MaterialState.READY_FOR_PICK:
            return
        if self._pick_tcp is None:
            return
        # 停带流程已经把物料固定在允许区内; 重试只重新求解/执行接近运动,
        # 不得重新启动传送带或重新测量位姿。
        self._retry_at = None
        self._motion_started = False
        self._stages = []
        self._motion_pose = None
        self._motion_cmd = None
        self.failure_reason = ""
        self._begin_motion(UnitTaskState.APPROACHING, "retry after IK failure")

    def _fail(self, reason: str) -> None:
        if self.state is UnitTaskState.FAILED:
            return
        # 失败时把执行机构置于安全状态: 停带、停车
        try:
            self.conveyor.stop(cfg.ALIASES["conveyor"])
        except Exception:
            pass
        if self.agv is not None:
            try:
                self.agv.stop()
            except Exception:
                pass
        self._retry_at = None
        self.state = UnitTaskState.FAILED
        self.failure_reason = reason
        self._event(f"FAILED: {reason}")
        if self.logger:
            self.logger.warning("[UNIT] uid=%s 任务失败: %s", self.uid, reason)

    # ── 日志/工具 ───────────────────────────────────────────

    def _settling_complete_state(self):
        """稳定窗口结束后进入的状态; 阶段二覆盖为 RETREATING."""
        return UnitTaskState.DONE

    def _enter(self, state: UnitTaskState, reason: str) -> None:
        self.state = state
        self._event(f"{state.name}: {reason}")

    def _event(self, message: str) -> None:
        self.event_log.append((self.sim_time, message))
        if self.logger:
            self.logger.info("[UNIT t=%.2f uid=%s] %s",
                             self.sim_time, self.uid, message)

    def _log_step(self) -> None:
        if self.logger:
            self.logger.debug("[UNIT t=%.2f uid=%s state=%s target=%s]",
                              self.sim_time, self.uid, self.state.name, self.target)

    def _sim_time(self) -> float:
        try:
            return float(self.sim.getSimulationTime())
        except Exception:
            return self.sim_time

    def summary(self) -> dict:
        rec = self.materials.by_uid(self.uid)
        return {
            "uid": self.uid,
            "target": self.target,
            "layout": self.layout.name,
            "state": self.state.name,
            "failure_reason": self.failure_reason,
            "material_state": rec.state.value if rec else None,
            "compensation_count": self.compensation_count,
            "pick_pose": ([round(float(v), 5) for v in self._pick_pose[:3]]
                          if self._pick_pose is not None else None),
            "pick_tcp": ([round(float(v), 5) for v in self._pick_tcp]
                         if self._pick_tcp is not None else None),
            "agv_blocked": self.agv_blocked,
            "events": list(self.event_log),
        }

    @property
    def finished(self) -> bool:
        """任务是否已经结束 (AGV 目标还需等待轮速行驶完成)."""
        if self.state is UnitTaskState.FAILED:
            # IK 失败在重试窗口内时任务未结束。
            return self._retry_at is None
        if self.state is not UnitTaskState.DONE:
            return False
        if self.target == "agv" and self.agv is not None:
            return self._agv_done
        return True
