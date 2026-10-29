"""conveyor_safety.py — 主动停带、越程、摩擦输送与物料稳定 (06-09 §10).

阶段一的停带读数有两处“未知当安全”:
  - ``_pad_velocity()`` 异常时返回 0.0;
  - ``_advance_stopping()`` 缺失 pad 位姿时初始 ``pos_ok=True``.

本模块用明确的 ``SpeedReading`` 区分“有效速度 / 回读不可用”, 并要求:
  1. 速度有效时以速度为主判据;
  2. 速度不可用时只能用**独立 pad 位移**在连续窗口内确认停止;
  3. 两者都未知或相互矛盾 → ``STOP_UNCONFIRMED``, 由控制器锁存;
  4. 沿带面切线的物料越程、横向/高度漂移独立判定.

``bounded_transport_step`` 只补偿“物理输送落后于带速”的差额, 不再固定
叠加 1 cm/步 (阶段一已知问题: 物理 0.20 m/s + 补偿 0.20 m/s ≈ 0.38 m/s).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from stage1 import config as base

from . import config as cfg


class StopStatus(str, Enum):
    PENDING = "PENDING"
    OK = "OK"
    LATENCY_EXCEEDED = "LATENCY_EXCEEDED"
    OVERRUN_EXCEEDED = "OVERRUN_EXCEEDED"
    STOP_FAILED = "STOP_FAILED"
    STOP_UNCONFIRMED = "STOP_UNCONFIRMED"
    COLLISION = "COLLISION"


@dataclass(frozen=True)
class SpeedReading:
    value: Optional[float]
    valid: bool
    source: str
    message: str = ""

    @classmethod
    def unavailable(cls, source: str, message: str = "") -> "SpeedReading":
        return cls(None, False, source, message)

    def as_dict(self) -> dict:
        return {"value": self.value, "valid": self.valid,
                "source": self.source, "message": self.message}


def read_conveyor_speed(sim, handle: int, state_key: str) -> SpeedReading:
    """读取模型脚本的 ``__state__.vel``; 不回退到命令速度."""
    try:
        state = sim.readCustomTableData(handle, state_key)
    except Exception as exc:
        return SpeedReading.unavailable("state_read_error", str(exc))
    if isinstance(state, str):
        try:
            state = json.loads(state)
        except Exception as exc:
            return SpeedReading.unavailable("state_parse_error", str(exc))
    if not isinstance(state, dict):
        return SpeedReading.unavailable(
            "state_missing", f"__state__ 不是表: {type(state).__name__}")
    if "vel" not in state:
        return SpeedReading.unavailable("velocity_missing", "缺少 vel 字段")
    try:
        value = float(state["vel"])
    except (TypeError, ValueError):
        return SpeedReading.unavailable("velocity_not_numeric",
                                        f"vel={state.get('vel')!r}")
    if not math.isfinite(value):
        return SpeedReading.unavailable("velocity_not_finite", f"vel={value}")
    return SpeedReading(value=value, valid=True, source="model_state")


# ═══════════════════════════════════════════════════════════════
# 停带测量
# ═══════════════════════════════════════════════════════════════

@dataclass
class StopMeasurement:
    status: StopStatus = StopStatus.PENDING
    command_time: Optional[float] = None
    material_start: Optional[list[float]] = None
    material_end: Optional[list[float]] = None
    belt_speed_before: float = 0.0
    latency: Optional[float] = None
    overrun: float = 0.0
    lateral_drift: float = 0.0
    height_drift: float = 0.0
    steps: int = 0
    speed_source: str = ""
    speed_valid: bool = False
    pad_still: Optional[bool] = None
    message: str = ""

    def as_dict(self) -> dict:
        return {
            "status": self.status.value,
            "command_time": self.command_time,
            "material_start": (None if self.material_start is None else
                               [round(v, 5) for v in self.material_start]),
            "material_end": (None if self.material_end is None else
                             [round(v, 5) for v in self.material_end]),
            "belt_speed_before": round(self.belt_speed_before, 5),
            "latency": (None if self.latency is None
                        else round(self.latency, 4)),
            "overrun": round(self.overrun, 5),
            "lateral_drift": round(self.lateral_drift, 5),
            "height_drift": round(self.height_drift, 5),
            "steps": self.steps,
            "speed_source": self.speed_source,
            "speed_valid": self.speed_valid,
            "pad_still": self.pad_still,
            "message": self.message,
        }


class ConveyorStopMonitor:
    def __init__(self, geometry,
                 max_latency: float = cfg.CONVEYOR_STOP_MAX_LATENCY,
                 max_overrun: float = cfg.CONVEYOR_STOP_MAX_OVERRUN,
                 vel_tol: float = cfg.CONVEYOR_STOP_VEL_TOL,
                 pos_tol: float = cfg.CONVEYOR_STOP_POS_TOL,
                 confirm_steps: int = cfg.CONVEYOR_STOP_CONFIRM_STEPS,
                 max_steps: int = cfg.CONVEYOR_STOP_OBSERVE_STEPS,
                 max_lateral: float = cfg.CONVEYOR_MAX_LATERAL_DRIFT,
                 max_height: float = cfg.CONVEYOR_MAX_HEIGHT_DRIFT) -> None:
        self.geometry = geometry
        self.max_latency = float(max_latency)
        self.max_overrun = float(max_overrun)
        self.vel_tol = float(vel_tol)
        self.pos_tol = float(pos_tol)
        self.confirm_steps = max(1, int(confirm_steps))
        self.max_steps = max(1, int(max_steps))
        self.max_lateral = float(max_lateral)
        self.max_height = float(max_height)
        self._active = False
        self._confirm = 0
        self._fallback_confirm = 0
        self._pad_prev: Optional[list[float]] = None
        self.measurement = StopMeasurement()

    # ── 状态 ────────────────────────────────────────────────

    @property
    def active(self) -> bool:
        return self._active

    @property
    def status(self) -> StopStatus:
        return self.measurement.status

    @property
    def finished(self) -> bool:
        return self.measurement.status is not StopStatus.PENDING

    def reset(self) -> None:
        self._active = False
        self._confirm = 0
        self._fallback_confirm = 0
        self._pad_prev = None
        self.measurement = StopMeasurement()

    # ── 命令/观测 ───────────────────────────────────────────

    def on_stop_command(self, sim_time: float, material_position,
                        belt_speed: float) -> StopMeasurement:
        self._active = True
        self._confirm = 0
        self._fallback_confirm = 0
        self._pad_prev = None
        self.measurement = StopMeasurement(
            command_time=float(sim_time),
            material_start=[float(v) for v in material_position[:3]],
            material_end=[float(v) for v in material_position[:3]],
            belt_speed_before=float(belt_speed))
        return self.measurement

    def observe(self, sim_time: float, speed: SpeedReading,
                material_position, pad_position=None,
                collision: bool = False) -> StopStatus:
        if not self._active:
            return self.measurement.status
        m = self.measurement
        m.steps += 1
        m.material_end = [float(v) for v in material_position[:3]]
        m.speed_source = speed.source
        m.speed_valid = bool(speed.valid)
        if collision:
            return self._finish(StopStatus.COLLISION, "停带过程中发生碰撞")

        self._update_drift()
        if m.overrun > self.max_overrun:
            return self._finish(
                StopStatus.OVERRUN_EXCEEDED,
                f"物料沿带面越程 {m.overrun:.4f} m > "
                f"{self.max_overrun:.4f} m")
        if m.lateral_drift > self.max_lateral or \
                m.height_drift > self.max_height:
            return self._finish(
                StopStatus.OVERRUN_EXCEEDED,
                f"物料漂移 lateral={m.lateral_drift:.4f} "
                f"height={m.height_drift:.4f}")

        pad_still = None
        if pad_position is not None and self._pad_prev is not None:
            pad_still = math.dist([float(v) for v in pad_position[:3]],
                                  self._pad_prev) <= self.pos_tol
        self._pad_prev = (None if pad_position is None
                          else [float(v) for v in pad_position[:3]])
        m.pad_still = pad_still

        confirmed = False
        if speed.valid:
            if abs(float(speed.value or 0.0)) <= self.vel_tol:
                self._confirm += 1
            else:
                self._confirm = 0
            if self._confirm >= self.confirm_steps:
                confirmed = True
        elif pad_still is True:
            # 速度回读不可用: 独立 pad 位移连续静止才可回退确认
            self._fallback_confirm += 1
            if self._fallback_confirm >= self.confirm_steps:
                confirmed = True
                m.speed_source = "pad_displacement_fallback"
        else:
            self._fallback_confirm = 0

        if confirmed:
            command_time = (float(m.command_time)
                            if m.command_time is not None else float(sim_time))
            latency = float(sim_time) - command_time
            m.latency = latency
            if latency > self.max_latency:
                return self._finish(
                    StopStatus.LATENCY_EXCEEDED,
                    f"停车延迟 {latency:.3f}s > {self.max_latency:.3f}s")
            return self._finish(StopStatus.OK,
                                "零速停带且越程/漂移在容差内")

        if m.steps >= self.max_steps:
            if not speed.valid and pad_still is None:
                return self._finish(
                    StopStatus.STOP_UNCONFIRMED,
                    "速度回读不可用且缺失 pad 位移, 无法确认停止")
            if not speed.valid:
                return self._finish(
                    StopStatus.STOP_UNCONFIRMED,
                    f"速度回读不可用且 pad 未连续静止 "
                    f"(pad_still={pad_still})")
            return self._finish(
                StopStatus.STOP_FAILED,
                f"带速在 {self.max_steps} 步内未降到 {self.vel_tol} m/s")
        return StopStatus.PENDING

    def force(self, status: StopStatus, message: str) -> StopStatus:
        return self._finish(status, message)

    # ── 内部 ────────────────────────────────────────────────

    def _update_drift(self) -> None:
        m = self.measurement
        if m.material_start is None or m.material_end is None:
            return
        d = self.geometry.direction
        rel = [m.material_end[i] - m.material_start[i] for i in range(3)]
        along = sum(rel[i] * d[i] for i in range(3))
        m.overrun = max(m.overrun, max(0.0, along))
        perp = [rel[i] - along * d[i] for i in range(3)]
        m.lateral_drift = max(m.lateral_drift, math.hypot(perp[0], perp[1]))
        m.height_drift = max(m.height_drift, abs(perp[2]))

    def _finish(self, status: StopStatus, message: str) -> StopStatus:
        self.measurement.status = status
        self.measurement.message = message
        self._active = False
        return status


# ═══════════════════════════════════════════════════════════════
# 受限补偿
# ═══════════════════════════════════════════════════════════════

@dataclass
class CompensationStep:
    delta: float
    physical_delta: float
    shortfall: float
    progress: float
    new_position: list[float]
    at_stop: bool = False
    overspeed: bool = False

    def as_dict(self) -> dict:
        return {
            "delta": round(self.delta, 6),
            "physical_delta": round(self.physical_delta, 6),
            "shortfall": round(self.shortfall, 6),
            "progress": round(self.progress, 4),
            "at_stop": self.at_stop,
            "overspeed": self.overspeed,
        }


def bounded_transport_step(position, previous_position, geometry,
                           belt_speed: float, dt: float,
                           lag_tol: float = cfg.COMPENSATION_LAG_TOL,
                           max_step: float = cfg.COMPENSATION_MAX_STEP
                           ) -> CompensationStep:
    """只补物理输送相对带速的差额; 物料领先时不推、不居中."""
    current = [float(v) for v in position[:3]]
    previous = [float(v) for v in previous_position[:3]]
    d = geometry.direction
    rel = [current[i] - previous[i] for i in range(3)]
    physical = sum(rel[i] * d[i] for i in range(3))
    expected = abs(float(belt_speed)) * float(dt)
    shortfall = expected - physical
    progress = geometry.progress(current)
    remaining = max(0.0, (1.0 - progress) * geometry.length)
    delta = 0.0
    if shortfall > lag_tol and remaining > 1e-9:
        delta = min(float(shortfall), float(max_step), remaining)
    new_progress = progress + delta / geometry.length \
        if geometry.length > 1e-9 else progress
    new_position = geometry.advance(current, delta)
    return CompensationStep(
        delta=delta, physical_delta=physical, shortfall=shortfall,
        progress=new_progress, new_position=new_position,
        at_stop=new_progress >= 1.0 - 1e-9,
        overspeed=physical > expected + max(0.005, 2.0 * lag_tol))


# ═══════════════════════════════════════════════════════════════
# 物料稳定
# ═══════════════════════════════════════════════════════════════

@dataclass
class StabilityState:
    stable: bool = False
    count: int = 0
    step_moved: float = 0.0
    speed: float = 0.0
    drift: float = 0.0
    elapsed: float = 0.0
    timed_out: bool = False

    def as_dict(self) -> dict:
        return {
            "stable": self.stable,
            "count": self.count,
            "step_moved": round(self.step_moved, 6),
            "speed": round(self.speed, 6),
            "drift": round(self.drift, 6),
            "elapsed": round(self.elapsed, 4),
            "timed_out": self.timed_out,
        }


class MaterialStabilityTracker:
    def __init__(self, start_time: float, position,
                 steps: int = cfg.CONVEYOR_MATERIAL_STABLE_STEPS,
                 speed_tol: float = cfg.CONVEYOR_MATERIAL_STABLE_SPEED,
                 pos_tol: float = base.MATERIAL_STABLE_POS_TOL,
                 timeout: float = cfg.CONVEYOR_MATERIAL_SETTLE_TIMEOUT
                 ) -> None:
        self.start_time = float(start_time)
        self.steps = max(1, int(steps))
        self.speed_tol = float(speed_tol)
        self.pos_tol = float(pos_tol)
        self.timeout = float(timeout)
        self.prev = [float(v) for v in position[:3]]
        self.start = list(self.prev)
        self.count = 0
        self.state = StabilityState()

    def update(self, sim_time: float, position, speed: float) -> StabilityState:
        current = [float(v) for v in position[:3]]
        moved = math.dist(current, self.prev)
        self.prev = current
        elapsed = float(sim_time) - self.start_time
        drift = math.dist(current, self.start)
        if moved <= self.pos_tol and float(speed) <= self.speed_tol:
            self.count += 1
        else:
            self.count = 0
        self.state = StabilityState(
            stable=self.count >= self.steps,
            count=self.count, step_moved=moved, speed=float(speed),
            drift=drift, elapsed=elapsed,
            timed_out=elapsed > self.timeout and self.count < self.steps)
        return self.state
