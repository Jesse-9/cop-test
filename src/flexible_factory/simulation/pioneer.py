"""pioneer.py — v1 基础层 pioneer p3dx 差速轮速控制.

只通过 `sim.setJointTargetVelocity` 驱动左右驱动轮。车体世界位姿只读,
任何 `setObjectPosition` / `setObjectPose` 写车体都是违规 (CLAUDE.md §7)。
驱动关节保持 dynamic + velocity 控制模式 (4.10 实测: 改成 kinematic 后
车轮打滑、车体不动)。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum, auto

from . import config_base as cfg


class PioneerError(Exception):
    """p3dx 控制异常"""


class AGVState(Enum):
    IDLE     = auto()
    MOVING   = auto()
    TURNING  = auto()
    BLOCKED  = auto()
    DOCKED   = auto()


@dataclass
class WheelSpeeds:
    left: float
    right: float


def wheel_velocities(v: float, w: float, wheel_radius: float,
                     wheel_base: float) -> WheelSpeeds:
    """差速换算 (v, w) → 左右轮角速度 (rad/s)。

    v: 车体线速度 (+X 前进), w: 偏航角速度 (逆时针为正)。
    """
    if wheel_radius <= 0 or wheel_base <= 0:
        raise PioneerError(
            f"轮半径/轮距必须为正: r={wheel_radius}, b={wheel_base}")
    left_v = v - w * wheel_base / 2.0
    right_v = v + w * wheel_base / 2.0
    return WheelSpeeds(left=left_v / wheel_radius,
                       right=right_v / wheel_radius)


def clamp_cmd(v: float, w: float, max_v: float, max_w: float) -> tuple[float, float]:
    return (_clamp(v, -max_v, max_v), _clamp(w, -max_w, max_w))


def approach(current: float, target: float, max_delta: float) -> float:
    """以不超过 max_delta 的步长逼近 target。"""
    diff = target - current
    if abs(diff) <= max_delta:
        return target
    return current + math.copysign(max_delta, diff)


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


class PioneerController:
    """p3dx 差速控制器。

    驱动关节必须保持 dynamic 模式; `apply()` 每步下发左右轮目标速度。
    车体位姿仅通过 `pose()` 读取用于诊断和到站判定。
    """

    def __init__(self, sim, left_joint: int, right_joint: int,
                 wheel_radius: float, wheel_base: float,
                 chassis_handle: int = -1) -> None:
        self.sim = sim
        self.left_joint = int(left_joint)
        self.right_joint = int(right_joint)
        self.chassis_handle = int(chassis_handle)
        self.wheel_radius = float(wheel_radius)
        self.wheel_base = float(wheel_base)

        self.max_linear_vel = cfg.AGV_MAX_LINEAR_VEL
        self.max_yaw_rate = cfg.AGV_MAX_YAW_RATE
        self.max_wheel_vel = cfg.AGV_MAX_WHEEL_VEL
        self.max_accel = cfg.AGV_MAX_ACCEL

        self._cmd_v = 0.0
        self._cmd_w = 0.0
        self._current_v = 0.0
        self._current_w = 0.0
        self.state = AGVState.IDLE
        self.last_speeds = WheelSpeeds(0.0, 0.0)

    # ── 命令 ────────────────────────────────────────────────

    def set_cmd(self, v: float, w: float, dt: float = cfg.SIM_STEP) -> WheelSpeeds:
        v, w = clamp_cmd(v, w, self.max_linear_vel, self.max_yaw_rate)
        self._cmd_v, self._cmd_w = v, w

        if abs(v) < 1e-9 and abs(w) < 1e-9:
            self.state = AGVState.IDLE
        elif abs(w) > abs(v):
            self.state = AGVState.TURNING
        else:
            self.state = AGVState.MOVING
        return self.apply(dt)

    def apply(self, dt: float = cfg.SIM_STEP) -> WheelSpeeds:
        self._current_v = approach(self._current_v, self._cmd_v,
                                   self.max_accel * dt)
        # 角加速度换算: 轮缘线加速度 = max_accel → dω = 2*max_accel/b * dt
        max_dw = 2.0 * self.max_accel / self.wheel_base * dt
        self._current_w = approach(self._current_w, self._cmd_w, max_dw)

        speeds = wheel_velocities(self._current_v, self._current_w,
                                  self.wheel_radius, self.wheel_base)
        speeds = WheelSpeeds(
            left=_clamp(speeds.left, -self.max_wheel_vel, self.max_wheel_vel),
            right=_clamp(speeds.right, -self.max_wheel_vel, self.max_wheel_vel),
        )
        self.sim.setJointTargetVelocity(self.left_joint, speeds.left)
        self.sim.setJointTargetVelocity(self.right_joint, speeds.right)
        self.last_speeds = speeds
        return speeds

    def stop(self) -> None:
        self._cmd_v = self._cmd_w = 0.0
        self._current_v = self._current_w = 0.0
        self.sim.setJointTargetVelocity(self.left_joint, 0.0)
        self.sim.setJointTargetVelocity(self.right_joint, 0.0)
        self.last_speeds = WheelSpeeds(0.0, 0.0)
        self.state = AGVState.IDLE

    # ── 只读诊断 ────────────────────────────────────────────

    def pose(self) -> list[float]:
        return list(self.sim.getObjectPose(self.chassis_handle, self.sim.handle_world))

    def position(self) -> list[float]:
        return self.pose()[:3]

    def is_upright(self, max_tilt: float = cfg.AGV_MAX_TILT) -> bool:
        """车体 Z 轴是否仍接近竖直 (防止翻车后继续放料)。"""
        q = self.pose()[3:7]
        x, y = float(q[0]), float(q[1])
        zz = 1.0 - 2.0 * (x * x + y * y)
        return zz >= math.cos(max_tilt)

    def is_stopped(self, lin_tol: float = 0.01, ang_tol: float = 0.05) -> bool:
        lin, ang = self._velocity()
        return (math.sqrt(sum(v * v for v in lin)) <= lin_tol
                and math.sqrt(sum(v * v for v in ang)) <= ang_tol)

    def _velocity(self) -> tuple[list[float], list[float]]:
        v = self.sim.getObjectVelocity(self.chassis_handle)
        if isinstance(v, (tuple, list)) and len(v) == 2:
            return list(v[0]), list(v[1])
        # 兼容部分封装的 6 元素形式
        return list(v[:3]), list(v[3:6])

    def snapshot(self) -> dict:
        return {
            "chassis_handle": self.chassis_handle,
            "left_joint": self.left_joint,
            "right_joint": self.right_joint,
            "wheel_radius": self.wheel_radius,
            "wheel_base": self.wheel_base,
        }
