"""pickup.py — A/B/C 无挡板停带与按实测位姿抓取试验 (06-07 §4).

一次 `PickupTrial` 只处理一件物料、一个组别、一个初始位置。事件顺序严格为:

    sensor 命中 → 校验目标 UID → 向 conveyor 发零速 → 等待 pad 实际停止
    → 等待物料稳定 → 读取物料实际世界位姿 → 生成 TCP → simIK → 吸附前置检查
    → (可选) 吸附/抬升/放置/释放

停带和稳定等待期间不下发机械臂抓取运动; 整个抓取目标固定使用同一 UID。
`sim.step()` 永远是每步最后一条动作 (与阶段一单步循环一致)。

组别:
  A: 保留挡停体, HYBRID_DYNAMIC 补偿, 阶段一当前停带判据 (行程进度 1.0);
  B: 移除挡停体, 传感器命中后主动零速, 等待稳定后按实测位姿抓取;
  C: 移除挡停体, 关闭位置补偿, 只做物理输送诊断, 不宣称摩擦输送成功。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

from stage1 import config as base
from stage1.conveyor_controller import BeltGeometry, transport_step
from stage1.material_manager import MaterialState
from . import config as cfg


class TrialState(Enum):
    SETTLING          = auto()
    RUNNING           = auto()
    OBSERVING         = auto()
    STOPPING          = auto()
    MATERIAL_SETTLING = auto()
    POSE_READ         = auto()
    ARM_MOVING        = auto()
    PRECHECK          = auto()
    ATTACHING         = auto()
    TRANSFER          = auto()
    RELEASING         = auto()
    PLACE_SETTLING    = auto()
    RESET_ARM         = auto()
    DONE              = auto()
    FAILED            = auto()


def quat_from_yaw(yaw: float) -> list[float]:
    return [0.0, 0.0, math.sin(yaw / 2.0), math.cos(yaw / 2.0)]


def quat_tilt(q) -> float:
    """物体局部 Z 轴与世界 Z 轴的夹角 (rad)。"""
    x, y = float(q[0]), float(q[1])
    zz = max(-1.0, min(1.0, 1.0 - 2.0 * (x * x + y * y)))
    return math.acos(zz)


def _linear_speed(sim, handle: int) -> float:
    try:
        v = sim.getObjectVelocity(handle)
        lin = v[0] if isinstance(v, (tuple, list)) and len(v) == 2 \
            else list(v)[:3]
        return math.sqrt(sum(float(c) * float(c) for c in lin[:3]))
    except Exception:
        return 0.0


def _angular_speed(sim, handle: int) -> float:
    try:
        v = sim.getObjectVelocity(handle)
        ang = v[1] if isinstance(v, (tuple, list)) and len(v) == 2 \
            else list(v)[3:6]
        return math.sqrt(sum(float(c) * float(c) for c in ang[:3]))
    except Exception:
        return 0.0


def _dist(a, b) -> float:
    return math.sqrt(sum((float(a[i]) - float(b[i])) ** 2 for i in range(3)))


@dataclass
class TrialRecord:
    run_id: str
    group: str
    group_name: str
    config_version: str
    layout: str
    repeat_index: int
    random_seed: int
    speed_mps: float
    initial_position: str
    initial_x: float
    initial_yaw: float
    material_uid: int
    material_alias: str = ""
    material_handle: int = -1

    spawn_pose: Optional[list[float]] = None
    settled_pose: Optional[list[float]] = None
    initial_pose: Optional[list[float]] = None
    initial_linear_speed: Optional[float] = None
    initial_angular_speed: Optional[float] = None

    sensor_trigger_time: Optional[float] = None
    sensor_trigger_pose: Optional[list[float]] = None
    sensor_trigger_pad_velocity: Optional[float] = None
    sensor_hits: list[int] = field(default_factory=list)
    stop_command_time: Optional[float] = None
    stop_command_reason: str = ""
    pad_stop_time: Optional[float] = None
    pad_stop_duration_s: Optional[float] = None
    pad_stop_distance_m: Optional[float] = None
    pad_stop_mode: str = ""
    material_stop_pose: Optional[list[float]] = None
    material_residual_speed: Optional[float] = None
    material_max_speed: Optional[float] = None
    material_settle_time_s: Optional[float] = None
    material_settle_steps: Optional[int] = None
    material_displacement_after_stop_m: Optional[float] = None
    material_displacement_from_trigger_m: Optional[float] = None
    material_final_pose: Optional[list[float]] = None
    material_tilt_rad: Optional[float] = None
    final_tcp_target: Optional[list[float]] = None

    inside_allowed_region: Optional[bool] = None
    allowed_region: dict = field(default_factory=dict)
    ik_result: Optional[dict] = None
    motion_records: list[dict] = field(default_factory=list)
    suction_precheck: Optional[dict] = None
    attach_result: Optional[dict] = None
    release_result: Optional[dict] = None
    place_position_error_m: Optional[float] = None

    stopper_present: bool = False
    compensation_enabled: bool = True
    compensation_count: int = 0
    compensation_amount_m: float = 0.0
    transported: Optional[bool] = None
    reached_detection_zone: Optional[bool] = None
    displacement_x: Optional[float] = None

    state: str = ""
    success: bool = False
    gate_ok: Optional[bool] = None
    failure_reason: str = ""
    events: list[list] = field(default_factory=list)
    trajectory: list[list[float]] = field(default_factory=list)
    trajectory_every_steps: int = cfg.MATERIAL_TRAJECTORY_EVERY
    steps: int = 0
    sim_time_start: float = 0.0
    sim_time_end: float = 0.0

    def as_dict(self) -> dict:
        return dict(self.__dict__)


class PickupTrial:
    def __init__(
        self,
        sim,
        scene,
        conveyor,
        geometry: BeltGeometry,
        layout,
        group: str,
        initial_x: float,
        initial_label: str = "middle",
        initial_yaw: float = 0.0,
        repeat_index: int = 0,
        uid: int = 1,
        materials=None,
        ik=None,
        suction=None,
        pad_handle: int = -1,
        full_pick: bool = False,
        logger: Optional[logging.Logger] = None,
        speed_mps: Optional[float] = None,
    ) -> None:
        if group not in cfg.GROUP_SPECS:
            raise ValueError(f"未知组别: {group!r}")
        self.sim = sim
        self.scene = scene
        self.conveyor = conveyor
        self.geometry = geometry
        self.layout = layout
        self.group = group
        self.spec = cfg.GROUP_SPECS[group]
        self.initial_x = float(initial_x)
        self.initial_label = str(initial_label)
        self.initial_yaw = float(initial_yaw)
        self.repeat_index = int(repeat_index)
        self.uid = int(uid)
        self.materials = materials
        self.ik = ik
        self.suction = suction
        self.pad_handle = int(pad_handle)
        self.full_pick = bool(full_pick)
        self.logger = logger
        self.speed = (base.CONVEYOR_SPEED if speed_mps is None
                      else float(speed_mps))
        self.region = cfg.ALLOWED_PICK_REGION

        self.state = TrialState.SETTLING
        self.steps = 0
        self.sim_time = 0.0
        self.failure_reason = ""
        self.record = TrialRecord(
            run_id=f"{self.spec['name']}_{self.initial_label}_r{self.repeat_index}",
            group=group,
            group_name=self.spec["name"],
            config_version=cfg.CONFIG_VERSION,
            layout=getattr(layout, "name", ""),
            repeat_index=self.repeat_index,
            random_seed=cfg.RANDOM_SEED + self.repeat_index,
            speed_mps=self.speed,
            initial_position=self.initial_label,
            initial_x=self.initial_x,
            initial_yaw=self.initial_yaw,
            material_uid=self.uid,
            stopper_present=bool(self.spec["stopper"]),
            compensation_enabled=bool(self.spec["compensation"]),
        )

        self.material_handle = -1
        self._settle_left = cfg.SETTLE_STEPS
        self._run_started_at = 0.0
        self._observe_started_at = 0.0
        self._mismatch_since: Optional[float] = None
        self._triggered = False
        self._stop_at: Optional[float] = None
        self._pad_pos_at_command: Optional[list[float]] = None
        self._pad_prev_pos: Optional[list[float]] = None
        self._pad_pos_at_trigger: Optional[list[float]] = None
        self._pad_confirm = 0
        self._vel_mismatch = 0
        self._material_stable = 0
        self._material_max_speed = 0.0
        self._settle_started_at: Optional[float] = None
        self._stop_pose: Optional[list[float]] = None
        self._stable_prev_pos: Optional[list[float]] = None
        self._last_pos: Optional[list[float]] = None
        self._motion_chain: list[tuple[str, list[float]]] = []
        self._motion_final = TrialState.DONE
        self._motion_label = ""
        self._motion_target: Optional[list[float]] = None
        self._motion_cmd: Optional[list[float]] = None
        self._motion_started_at = 0.0
        self._place_step = 0
        self._reset_started_at: Optional[float] = None
        self._sample_index = 0

    # ── 对外 ────────────────────────────────────────────────

    @property
    def finished(self) -> bool:
        return self.state in (TrialState.DONE, TrialState.FAILED)

    def start(self) -> None:
        if self.materials is None:
            raise ValueError("PickupTrial 需要 MaterialManager")
        alias = f"Part_{self.uid:04d}"
        pos = [self.initial_x, 0.0,
               base.CONVEYOR_SURFACE_Z + base.MATERIAL_HALF_HEIGHT
               + base.MATERIAL_SPAWN_GAP]
        self.material_handle = self.scene.spawn_material(
            self.uid, alias, position=pos,
            orientation=quat_from_yaw(self.initial_yaw))
        rec = self.materials.register(self.uid, alias,
                                      sim_handle=self.material_handle)
        self.record.material_alias = alias
        self.record.material_handle = int(self.material_handle)
        self.record.spawn_pose = self._pose()
        self.record.sim_time_start = float(self.sim.getSimulationTime())
        self.sim_time = self.record.sim_time_start
        self.conveyor.stop(base.ALIASES["conveyor"])
        self._event(f"spawn uid={self.uid} at x={self.initial_x:.3f} "
                    f"yaw={self.initial_yaw:.3f} group={self.group}")
        # SPAWNED → ON_CONVEYOR 在落稳后执行, 保证生命周期日志可追踪
        self._rec = rec

    def step(self) -> None:
        self.sim_time = float(self.sim.getSimulationTime())
        self._observe()
        self._advance()
        self._sample_trajectory()
        self.record.steps = self.steps
        self.record.sim_time_end = self.sim_time
        self.steps += 1
        self.sim.step()             # 永远最后

    def result(self) -> dict:
        self.record.state = self.state.name
        self.record.failure_reason = self.failure_reason
        self.record.events = list(self.record.events)
        self.record.allowed_region = {
            "x": list(self.region.x), "y": list(self.region.y),
            "z_center": self.region.z_center, "z_tol": self.region.z_tol,
        }
        return self.record.as_dict()

    def abort(self, reason: str) -> None:
        """外部运行器在步数/墙钟超时时终止试验。"""
        self._fail(reason)

    # ── 观测 ────────────────────────────────────────────────

    def _pose(self, handle: Optional[int] = None) -> list[float]:
        h = self.material_handle if handle is None else handle
        return [float(v) for v in
                self.sim.getObjectPose(h, self.sim.handle_world)]

    def _position(self, handle: int) -> list[float]:
        return [float(v) for v in
                self.sim.getObjectPosition(handle, self.sim.handle_world)]

    def _material_speed(self) -> float:
        return _linear_speed(self.sim, self.material_handle)

    def _pad_velocity(self) -> float:
        try:
            return float(self.conveyor.read_actual_velocity(
                base.ALIASES["conveyor"]))
        except Exception:
            return 0.0

    def _pad_position(self) -> Optional[list[float]]:
        if self.pad_handle < 0:
            return None
        try:
            belt = self.scene.handle(base.ALIASES["conveyor"])
            return [float(v) for v in
                    self.sim.getObjectPosition(self.pad_handle, belt)]
        except Exception:
            return None

    def _observe(self) -> None:
        self._last_pos = self._position(self.material_handle)
        self._last_speed = self._material_speed()
        self._last_pad_vel = self._pad_velocity()
        self._last_pad_pos = self._pad_position()

    def _sensor_hits(self) -> list[int]:
        try:
            return list(self.scene.pick_sensor_hits(self.materials.handles()))
        except Exception:
            return []

    # ── 状态推进 ────────────────────────────────────────────

    def _advance(self) -> None:
        state = self.state
        if state in (TrialState.DONE, TrialState.FAILED):
            return

        if state is TrialState.SETTLING:
            self._advance_settling()
        elif state is TrialState.RUNNING:
            self._advance_running()
        elif state is TrialState.OBSERVING:
            self._advance_observing()
        elif state is TrialState.STOPPING:
            self._advance_stopping()
        elif state is TrialState.MATERIAL_SETTLING:
            self._advance_material_settling()
        elif state is TrialState.POSE_READ:
            self._advance_pose_read()
        elif state is TrialState.ARM_MOVING:
            self._advance_motion()
        elif state is TrialState.PRECHECK:
            self._advance_precheck()
        elif state is TrialState.ATTACHING:
            self._advance_attaching()
        elif state is TrialState.TRANSFER:
            self._start_motion(
                [("station_approach",
                  self._with_clearance(self.layout.station_tcp_pose)),
                 ("station_place", list(self.layout.station_tcp_pose))],
                TrialState.RELEASING)
        elif state is TrialState.RELEASING:
            self._advance_releasing()
        elif state is TrialState.PLACE_SETTLING:
            self._advance_place_settling()
        elif state is TrialState.RESET_ARM:
            self._advance_reset_arm()

    def _advance_settling(self) -> None:
        self.conveyor.stop(base.ALIASES["conveyor"])
        self._settle_left -= 1
        if self._settle_left > 0:
            return
        self.record.settled_pose = self._pose()
        self.record.initial_pose = list(self.record.settled_pose)
        self.record.initial_linear_speed = round(self._material_speed(), 5)
        self.record.initial_angular_speed = round(
            _angular_speed(self.sim, self.material_handle), 5)
        if self._rec.state is MaterialState.SPAWNED:
            self._rec.transition_to(MaterialState.ON_CONVEYOR, self.sim_time,
                                    "settled on conveyor")
        self.conveyor.set_speed(base.ALIASES["conveyor"], self.speed)
        self._run_started_at = self.sim_time
        self._event(f"conveyor started at {self.speed:.3f} m/s")
        if self.group == "C":
            self._observe_started_at = self.sim_time
            self.state = TrialState.OBSERVING
        else:
            self.state = TrialState.RUNNING

    def _advance_running(self) -> None:
        hits = self._sensor_hits()
        expected = self.material_handle
        if expected in hits:
            self._record_trigger(hits)
            if self.spec["stop_mode"] == "sensor":
                self._command_stop("sensor_hit")
                return
        elif hits:
            if self._mismatch_since is None:
                self._mismatch_since = self.sim_time
            self.record.sensor_hits = hits
            if self.sim_time - self._mismatch_since > base.MISMATCH_TIMEOUT:
                self._fail(f"PICK_MISMATCH: 传感器命中 {hits}, "
                           f"目标 handle={expected}")
                return

        if self.spec["compensation"]:
            step = transport_step(self._last_pos, self.geometry,
                                  self.speed, base.SIM_STEP)
            if step.delta > 0.0:
                self.sim.setObjectPosition(self.material_handle,
                                           step.new_position,
                                           self.sim.handle_world)
                self.record.compensation_count += 1
                self.record.compensation_amount_m += step.delta
            if step.at_stop:
                if self.spec["stop_mode"] == "progress":
                    self._command_stop("progress>=1.0")
                    return
                # B 组只允许 sensor 触发停带; 到达行程末端仍未命中是故障。
                # 若正在等待 UID 误配超时, 保持等待以给出 PICK_MISMATCH。
                if (self._mismatch_since is None
                        and self.record.sensor_trigger_time is None):
                    self._fail("NO_SENSOR_TRIGGER_AT_STOP: "
                               "物料到达停止区但传感器未命中目标")
                    return

        if (self.spec["stop_mode"] == "sensor"
                and self.record.sensor_trigger_time is None
                and self.sim_time - self._run_started_at > cfg.RUN_TIMEOUT):
            self._fail("NO_SENSOR_TRIGGER: 目标物料未进入检测区")

    def _advance_observing(self) -> None:
        hits = self._sensor_hits()
        if self.material_handle in hits and not self._triggered:
            self._record_trigger(hits)
        elapsed = self.sim_time - self._observe_started_at
        reached_stop_x = float(self._last_pos[0]) >= cfg.C_STOP_X
        if elapsed < cfg.C_OBSERVE_TIME and not reached_stop_x:
            return
        self.conveyor.stop(base.ALIASES["conveyor"])
        final = self._pose()
        self.record.material_final_pose = final
        dx = float(final[0]) - float(self.record.initial_pose[0])
        self.record.displacement_x = round(dx, 5)
        self.record.transported = bool(dx >= cfg.C_TRANSPORT_MIN_DX)
        self.record.reached_detection_zone = bool(
            self.record.sensor_trigger_time is not None)
        self.record.gate_ok = self.record.transported
        self.record.success = True
        self._event(f"C observation finished dx={dx:.4f} m "
                    f"reached_x={final[0]:.4f} "
                    f"sensor={self.record.reached_detection_zone} "
                    f"transported={self.record.transported}")
        self.state = TrialState.DONE

    def _record_trigger(self, hits: list[int]) -> None:
        if self._triggered:
            return
        self._triggered = True
        self.record.sensor_trigger_time = round(self.sim_time, 5)
        self.record.sensor_trigger_pose = self._pose()
        self.record.sensor_trigger_pad_velocity = round(self._last_pad_vel, 5)
        self.record.sensor_hits = list(hits)
        self._pad_pos_at_trigger = self._last_pad_pos
        self._event(f"sensor hit uid={self.uid} handle={self.material_handle} "
                    f"pad_vel={self._last_pad_vel:.4f}")

    def _command_stop(self, reason: str) -> None:
        if self.record.stop_command_time is not None:
            return
        self.conveyor.stop(base.ALIASES["conveyor"])
        self.record.stop_command_time = round(self.sim_time, 5)
        self.record.stop_command_reason = reason
        self._pad_pos_at_command = self._last_pad_pos
        self._pad_prev_pos = self._last_pad_pos
        self._stop_pose = list(self._last_pos)
        self._pad_confirm = 0
        self._vel_mismatch = 0
        self._event(f"conveyor stop commanded ({reason}) "
                    f"pad_vel={self._last_pad_vel:.4f}")
        self.state = TrialState.STOPPING

    def _advance_stopping(self) -> None:
        if self.record.stop_command_time is None:
            self._command_stop("unspecified")
            return
        if self.sim_time - self.record.stop_command_time > cfg.PAD_STOP_TIMEOUT:
            self._fail("PAD_STOP_TIMEOUT: 带面未在限时内停止")
            return

        vel = self._last_pad_vel
        pos = self._last_pad_pos
        if pos is not None and self._pad_prev_pos is not None:
            moved = _dist(pos, self._pad_prev_pos)
            pos_ok = moved <= cfg.PAD_STOP_POS_TOL
        else:
            moved = None
            pos_ok = True
        vel_ok = vel <= cfg.PAD_STOP_VEL_TOL
        self._pad_prev_pos = pos

        if pos_ok and not vel_ok:
            self._vel_mismatch += 1
        else:
            self._vel_mismatch = 0
        # 速度回读可信时以速度为主; 若 pad 已连续多步不动而回读仍非零,
        # 视为回读不可信, 改用位姿差确认 (06-07 §4.3-3)。
        velocity_trusted = vel_ok or self._vel_mismatch < cfg.PAD_STOP_CONFIRM_STEPS
        if pos_ok and (vel_ok or not velocity_trusted):
            self._pad_confirm += 1
        else:
            self._pad_confirm = 0
        if self._pad_confirm < cfg.PAD_STOP_CONFIRM_STEPS:
            return

        self.record.pad_stop_time = round(self.sim_time, 5)
        self.record.pad_stop_duration_s = round(
            self.sim_time - self.record.stop_command_time, 5)
        if pos is not None and self._pad_pos_at_command is not None:
            self.record.pad_stop_distance_m = round(
                _dist(pos, self._pad_pos_at_command), 5)
            self.record.pad_stop_mode = (
                "velocity+position" if vel_ok else "position_fallback")
        else:
            self.record.pad_stop_mode = "velocity_only"
        if self.record.sensor_trigger_pose is not None:
            self.record.material_displacement_from_trigger_m = round(
                _dist(self._pose()[:3], self.record.sensor_trigger_pose[:3]), 5)
        self._event(f"pad stopped in {self.record.pad_stop_duration_s:.3f}s "
                    f"distance={self.record.pad_stop_distance_m}")
        self._settle_started_at = self.sim_time
        self._material_stable = 0
        self._material_max_speed = 0.0
        self._stable_prev_pos = list(self._last_pos)
        self.state = TrialState.MATERIAL_SETTLING

    def _advance_material_settling(self) -> None:
        speed = self._last_speed
        pos = self._last_pos
        # 稳定性看“连续步内是否还在动”, 不是看距停带命令的总位移:
        # 停带瞬间的最后一次补偿 (<=1 cm) 不应让物料永远判为不稳定。
        step_moved = (_dist(pos, self._stable_prev_pos)
                      if self._stable_prev_pos is not None else 0.0)
        self._stable_prev_pos = list(pos)
        total_moved = (_dist(pos, self._stop_pose)
                       if self._stop_pose is not None else 0.0)
        self._material_max_speed = max(self._material_max_speed, speed)
        if (speed <= cfg.MATERIAL_STABLE_SPEED_TOL
                and step_moved <= cfg.MATERIAL_STABLE_POS_TOL):
            self._material_stable += 1
        else:
            self._material_stable = 0
        if self._material_stable >= cfg.MATERIAL_STABLE_STEPS:
            self.record.material_stop_pose = list(self._stop_pose)
            self.record.material_residual_speed = round(speed, 5)
            self.record.material_max_speed = round(self._material_max_speed, 5)
            self.record.material_settle_time_s = round(
                self.sim_time - self._settle_started_at, 5)
            self.record.material_settle_steps = cfg.MATERIAL_STABLE_STEPS
            self.record.material_displacement_after_stop_m = round(
                total_moved, 5)
            self._event("material stable")
            self.state = TrialState.POSE_READ
            return
        if self.sim_time - self._settle_started_at > cfg.MATERIAL_SETTLE_TIMEOUT:
            self._fail(
                f"MATERIAL_SETTLE_TIMEOUT: 物料未在限时内稳定 "
                f"(max_speed={self._material_max_speed:.3f} m/s)")

    def _advance_pose_read(self) -> None:
        pose = self._pose()
        self.record.material_final_pose = pose
        self.record.material_tilt_rad = round(quat_tilt(pose[3:7]), 5)
        self.record.inside_allowed_region = bool(self.region.contains(pose))
        self.record.final_tcp_target = [
            pose[0], pose[1], pose[2] + base.MATERIAL_HALF_HEIGHT,
            *base.TOOL_TARGET_QUATERNION]
        self._event(f"final pose xyz=({pose[0]:.4f},{pose[1]:.4f},"
                    f"{pose[2]:.4f}) inside={self.record.inside_allowed_region} "
                    f"tilt={self.record.material_tilt_rad:.3f}")

        if self._rec.state is MaterialState.ON_CONVEYOR:
            self._rec.transition_to(MaterialState.READY_FOR_PICK,
                                    self.sim_time,
                                    "stopped and stable, UID confirmed")

        if self.group == "A":
            self.record.gate_ok = bool(
                self.record.inside_allowed_region
                and self.record.material_tilt_rad <= cfg.MATERIAL_MAX_TILT)
            self.record.success = True
            self.state = TrialState.DONE
            return

        if not self.record.inside_allowed_region:
            self._fail("OUT_OF_ALLOWED_REGION: 物料停稳后不在允许抓取区")
            return
        if self.record.material_tilt_rad > cfg.MATERIAL_MAX_TILT:
            self._fail("MATERIAL_TILT: 物料倾斜超过阈值")
            return
        if self.ik is None:
            self._fail("IK_NOT_CONFIGURED: B 组需要 UR5 IK 控制器")
            return
        self._start_motion(
            [("pick_approach", self._with_clearance(
                self.record.final_tcp_target)),
             ("pick", list(self.record.final_tcp_target))],
            TrialState.PRECHECK)

    def _advance_motion(self) -> None:
        if self._motion_cmd is None:
            self._begin_next_motion()
            return
        if (self.ik.converged(self._motion_cmd)
                and self.ik.reached(self._motion_target)
                and self.ik.joints_within_limits()
                and self.ik.stopped()):
            self.record.motion_records.append({
                "label": self._motion_label,
                "target": [round(float(v), 5) for v in self._motion_target],
                "tcp_pos_error_m": round(
                    float(self.ik.tcp_position_error(self._motion_target)), 5),
                "tcp_ang_error_rad": round(
                    float(self.ik.tcp_orientation_error(self._motion_target)), 5),
                "joints": [round(float(v), 5)
                           for v in self.ik.joint_positions()],
                "ik_result": dict(self.record.ik_result or {}),
                "success": True,
            })
            self._event(f"motion {self._motion_label} reached")
            self._motion_cmd = None
            if self._motion_chain:
                self._begin_next_motion()
            else:
                self.state = self._motion_final
            return
        if self.sim_time - self._motion_started_at > cfg.ARM_MOVE_TIMEOUT:
            self.record.motion_records.append({
                "label": self._motion_label,
                "target": [round(float(v), 5) for v in self._motion_target],
                "ik_result": dict(self.record.ik_result or {}),
                "success": False,
                "failure_reason": "ARM_MOVE_TIMEOUT",
            })
            self._fail(f"ARM_MOVE_TIMEOUT: {self._motion_label}")

    def _begin_next_motion(self) -> None:
        if not self._motion_chain:
            self.state = self._motion_final
            return
        self._motion_label, target = self._motion_chain.pop(0)
        self._motion_target = list(target)
        self._motion_started_at = self.sim_time
        res = self.ik.solve(self._motion_target)
        self.record.ik_result = {
            "label": self._motion_label,
            "success": bool(res.success),
            "message": res.message,
            "used_backup": bool(res.used_backup),
            "used_seed": bool(res.used_seed),
            "precision": [round(float(v), 5) for v in res.precision],
        }
        if not res.success:
            self._fail(f"IK_FAILED: {self._motion_label}: {res.message}")
            return
        self.ik.apply_joints(res.joints)
        self._motion_cmd = list(res.joints)
        self._event(f"motion {self._motion_label} started "
                    f"(backup={res.used_backup}, seed={res.used_seed})")

    def _start_motion(self, chain, final_state: TrialState) -> None:
        self._motion_chain = list(chain)
        self._motion_final = final_state
        self._motion_cmd = None
        self.state = TrialState.ARM_MOVING
        self._begin_next_motion()

    def _with_clearance(self, tcp_pose) -> list[float]:
        pose = list(tcp_pose)
        pose[2] += cfg.PICK_APPROACH_CLEARANCE
        return pose

    def _advance_precheck(self) -> None:
        pre = self._suction_precheck()
        self.record.suction_precheck = pre
        if not pre["ok"]:
            self._fail(f"SUCTION_PRECHECK_FAILED: {pre['failed']}")
            return
        self.record.gate_ok = True
        if self.full_pick:
            self.state = TrialState.ATTACHING
        else:
            self.state = TrialState.RESET_ARM
            self._reset_started_at = self.sim_time
            self.ik.apply_joints(list(base.ARM_SEED_JOINTS))

    def _suction_precheck(self) -> dict:
        handle = self.material_handle
        tcp_pos = self._position(self.scene.tcp)
        mat_pos = self._position(handle)
        top = [mat_pos[0], mat_pos[1],
               mat_pos[2] + base.MATERIAL_HALF_HEIGHT]
        gap = _dist(tcp_pos, top)
        q = self.sim.getObjectQuaternion(self.scene.tcp,
                                         self.sim.handle_world)
        tilt = quat_tilt(q)
        mat_speed = self._material_speed()
        tool_speed = float(self.ik.tcp_speed()) if self.ik else 0.0
        hit = self.scene.check_sensor(base.ALIASES["suction_sensor"], handle)
        state_ok = (self._rec.state is MaterialState.READY_FOR_PICK)
        checks = {
            "sensor_hit": hit is not None and int(hit) == int(handle),
            "uid_match": int(hit) == int(handle) if hit is not None else False,
            "contact_gap_ok": gap <= base.SUCTION_CONTACT_TOL,
            "tool_tilt_ok": tilt <= base.SUCTION_TILT_TOL,
            "material_speed_ok": mat_speed <= base.SUCTION_MAX_ATTACH_SPEED,
            "tool_speed_ok": tool_speed <= base.SUCTION_MAX_TOOL_SPEED,
            "material_state_ok": state_ok,
        }
        failed = [k for k, ok in checks.items() if not ok]
        return {
            "ok": not failed,
            "failed": failed,
            "sensor_hit": hit,
            "contact_gap_m": round(gap, 5),
            "tool_tilt_rad": round(tilt, 5),
            "material_speed_mps": round(mat_speed, 5),
            "tool_speed_mps": round(tool_speed, 5),
            "material_state": self._rec.state.value,
            "tolerances": {
                "contact_gap_m": base.SUCTION_CONTACT_TOL,
                "tool_tilt_rad": base.SUCTION_TILT_TOL,
                "material_speed_mps": base.SUCTION_MAX_ATTACH_SPEED,
                "tool_speed_mps": base.SUCTION_MAX_TOOL_SPEED,
            },
        }

    def _advance_attaching(self) -> None:
        if self.suction is None:
            self._fail("SUCTION_NOT_CONFIGURED")
            return
        res = self.suction.attach(self.uid, self.sim_time)
        self.record.attach_result = {
            "ok": bool(res.ok), "code": res.code, "message": res.message,
        }
        if not res.ok:
            self._fail(f"ATTACH_FAILED: {res.code} ({res.message})")
            return
        lift = list(self.record.final_tcp_target)
        lift[2] += cfg.PICK_LIFT_CLEARANCE
        self._start_motion([("lift", lift)], TrialState.TRANSFER)

    def _advance_releasing(self) -> None:
        if self.suction is None:
            self._fail("SUCTION_NOT_CONFIGURED")
            return
        target = [*self.layout.station_material_pose, 0.0, 0.0, 0.0, 1.0]
        res = self.suction.release(
            self.uid, self.sim_time, target,
            support_z=self.layout.station_top_z)
        self.record.release_result = {
            "ok": bool(res.ok), "message": res.message,
            "stable": bool(res.stable), "penetration": bool(res.penetration),
        }
        if not res.ok or res.penetration or not res.stable:
            self._fail(f"RELEASE_FAILED: {res.message} "
                       f"(stable={res.stable}, penetration={res.penetration})")
            return
        self._place_step = 0
        self.state = TrialState.PLACE_SETTLING

    def _advance_place_settling(self) -> None:
        self._place_step += 1
        if self._place_step < base.SUCTION_SETTLE_STEPS:
            return
        final = self._pose()
        self.record.material_final_pose = final
        self.record.place_position_error_m = round(
            _dist(final[:3], self.layout.station_material_pose), 5)
        self.record.gate_ok = bool(
            self.record.gate_ok
            and self.record.place_position_error_m <= base.MATERIAL_PLACE_TOL)
        self.record.success = bool(self.record.gate_ok)
        self._event(f"placed error={self.record.place_position_error_m:.4f} m")
        self._reset_started_at = None
        self.state = TrialState.RESET_ARM

    def _advance_reset_arm(self) -> None:
        if self.ik is None:
            self.record.success = bool(self.record.gate_ok)
            self.state = TrialState.DONE
            return
        if self._reset_started_at is None:
            self._reset_started_at = self.sim_time
            self.ik.apply_joints(list(base.ARM_SEED_JOINTS))
            return
        if (self.ik.converged(list(base.ARM_SEED_JOINTS))
                and self.ik.stopped()):
            self.record.success = bool(self.record.gate_ok)
            self.state = TrialState.DONE
            return
        if self.sim_time - self._reset_started_at > cfg.ARM_RESET_TIMEOUT:
            # 复位失败不改变抓取结论, 但必须留下明确记录
            self._event("arm reset timeout (trial data retained)")
            self.record.success = bool(self.record.gate_ok)
            self.state = TrialState.DONE

    # ── 失败/事件/采样 ──────────────────────────────────────

    def _fail(self, reason: str) -> None:
        if self.state is TrialState.FAILED:
            return
        try:
            self.conveyor.stop(base.ALIASES["conveyor"])
        except Exception:
            pass
        if self.ik is not None and self._motion_cmd is not None:
            try:
                # 保持当前位置, 不再继续下探
                self.ik.apply_joints(self.ik.joint_positions())
            except Exception:
                pass
        self.state = TrialState.FAILED
        self.failure_reason = reason
        self.record.success = False
        self.record.gate_ok = False
        self._event(f"FAILED: {reason}")

    def _event(self, message: str) -> None:
        self.record.events.append([round(self.sim_time, 4), message])
        if self.logger:
            self.logger.info("[TRIAL %s t=%.2f uid=%s] %s",
                             self.group, self.sim_time, self.uid, message)

    def _sample_trajectory(self) -> None:
        if self._sample_index % cfg.MATERIAL_TRAJECTORY_EVERY != 0:
            self._sample_index += 1
            return
        self._sample_index += 1
        pos = self._last_pos if self._last_pos is not None else \
            self._position(self.material_handle)
        self.record.trajectory.append([
            round(self.sim_time, 3),
            round(float(pos[0]), 5), round(float(pos[1]), 5),
            round(float(pos[2]), 5),
            round(float(self._last_speed), 5),
            round(float(self._last_pad_vel), 5),
        ])
