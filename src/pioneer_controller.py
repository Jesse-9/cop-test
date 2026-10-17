"""pioneer_controller.py — pioneer p3dx 差速驱动 (阶段一 §2.10)

内置 `pioneer p3dx.ttm` 的左右轮速度控制。**禁止**用 setObjectPosition /
setObjectPose 瞬移车体 (CLAUDE.md §7)；车体位姿只用于诊断与到站判定。

本模块的 `wheel_velocities()` 是纯函数，可离线测试。

方案依据:
  docs/wiki/06-工业案例/06-05-柔性工业车间分阶段执行步骤.md §2.10
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum, auto

import factory_config as cfg


class AGVState(Enum):
    """车体运动状态 (阶段一 §2.10)"""
    IDLE     = auto()   # 轮速为零
    MOVING   = auto()   # 前进
    TURNING  = auto()   # 转向
    BLOCKED  = auto()   # 路径受阻 (故障注入可进入)
    DOCKED   = auto()   # 已在装卸位


class PioneerError(Exception):
    """p3dx 控制异常"""


@dataclass
class WheelSpeeds:
    left: float
    right: float


def wheel_velocities(v: float, w: float, wheel_radius: float,
                     wheel_base: float) -> WheelSpeeds:
    """差速换算: (v, w) → 左右轮角速度 (rad/s)。

    Args:
        v:            期望线速度 (m/s)，沿车体 +X 前方为正。
        w:            期望角速度 (rad/s)，逆时针为正。
        wheel_radius: 驱动轮半径 (m)。
        wheel_base:   左右轮间距 (m)。

    Returns:
        WheelSpeeds(left_rad_s, right_rad_s)。

    Raises:
        PioneerError: wheel_radius 或 wheel_base 非正。
    """
    if wheel_radius <= 0 or wheel_base <= 0:
        raise PioneerError(
            f"轮半径/轮距必须为正: r={wheel_radius}, b={wheel_base}"
        )
    left_v  = v - w * (wheel_base / 2.0)
    right_v = v + w * (wheel_base / 2.0)
    return WheelSpeeds(
        left  = left_v  / wheel_radius,
        right = right_v / wheel_radius,
    )


class PioneerController:
    """pioneer p3dx 差速驱动控制器。

    只通过 `sim.setJointTargetVelocity` 驱动左右驱动轮；车体世界位姿
    仅由物理引擎产生。

    Attributes:
        sim:            Remote API sim 模块。
        left_joint:     左驱动轮关节句柄。
        right_joint:    右驱动轮关节句柄。
        wheel_radius:   轮半径 (m)，构建期测量。
        wheel_base:     轮距 (m)，构建期测量。
        max_wheel_vel:  单轮最大角速度 (rad/s)，限幅用。
        max_linear_vel: 最大线速度 (m/s)，限幅用。
        max_yaw_rate:   最大角速度 (rad/s)，限幅用。
        max_accel:      加速度斜坡 (m/s²)，避免打滑。
        chassis_handle: 车体句柄 (只读诊断)。
    """

    def __init__(self, sim, left_joint: int, right_joint: int,
                 wheel_radius: float, wheel_base: float,
                 chassis_handle: int = -1) -> None:
        self.sim = sim
        self.left_joint = left_joint
        self.right_joint = right_joint
        self.wheel_radius = wheel_radius
        self.wheel_base = wheel_base
        self.chassis_handle = chassis_handle

        self.max_linear_vel = cfg.AGV_MAX_LINEAR_VEL
        self.max_wheel_vel  = cfg.AGV_MAX_WHEEL_VEL
        self.max_yaw_rate   = cfg.AGV_MAX_YAW_RATE
        self.max_accel      = cfg.AGV_MAX_ACCEL

        self._cmd_v = 0.0
        self._cmd_w = 0.0
        self._current_v = 0.0     # 斜坡后的实际下发线速度
        self._current_w = 0.0
        self.state = AGVState.IDLE

    # ── 命令接口 ─────────────────────────────────────────────

    def set_cmd(self, v: float, w: float) -> WheelSpeeds:
        """设定 (v, w)，做限幅与加速度斜坡后下发到左右轮。

        限幅顺序: 先按 max_linear_vel / max_yaw_rate 限幅，
        再按 max_accel 做每步斜坡 (dt 由 set_cmd 的 sim_time 差给出)。
        """
        v = _clamp(v, -self.max_linear_vel, self.max_linear_vel)
        w = _clamp(w, -self.max_yaw_rate, self.max_yaw_rate)
        self._cmd_v, self._cmd_w = v, w

        # 状态判定（用于诊断/日志，不驱动行为）
        if abs(v) < 1e-6 and abs(w) < 1e-6:
            self.state = AGVState.IDLE
        elif abs(w) > abs(v):
            self.state = AGVState.TURNING
        else:
            self.state = AGVState.MOVING
        return self.apply()

    def apply(self, dt: float = cfg.SIM_STEP) -> WheelSpeeds:
        """按斜坡把当前命令下发到左右轮 (rad/s)。"""
        max_dv = self.max_accel * dt
        self._current_v = _approach(self._current_v, self._cmd_v, max_dv)
        self._current_w = _approach(self._current_w, self._cmd_w,
                                    self.max_accel * self.wheel_base / 2.0 * dt)
        speeds = wheel_velocities(self._current_v, self._current_w,
                                  self.wheel_radius, self.wheel_base)
        speeds = WheelSpeeds(
            left  = _clamp(speeds.left,  -self.max_wheel_vel, self.max_wheel_vel),
            right = _clamp(speeds.right, -self.max_wheel_vel, self.max_wheel_vel),
        )
        self.sim.setJointTargetVelocity(self.left_joint,  speeds.left)
        self.sim.setJointTargetVelocity(self.right_joint, speeds.right)
        return speeds

    def stop(self) -> None:
        """立即停机（轮速归零）——用于 BLOCKED / 任务结束。"""
        self._cmd_v = self._cmd_w = 0.0
        self._current_v = self._current_w = 0.0
        self.sim.setJointTargetVelocity(self.left_joint, 0.0)
        self.sim.setJointTargetVelocity(self.right_joint, 0.0)
        self.state = AGVState.IDLE

    # ── 只读诊断 ─────────────────────────────────────────────

    def chassis_pose(self) -> list[float]:
        """车体世界位姿 [x, y, z, qx, qy, qz, qw]（只读，不用于瞬移）"""
        return self.sim.getObjectPose(self.chassis_handle)

    def is_stopped(self, lin_tol: float = 0.01,
                   ang_tol: float = 0.05) -> bool:
        """车体线/角速度是否已稳定 (§2.10 装卸前必须确认)"""
        v_lin = self.sim.getObjectVelocity(self.chassis_handle)
        lin = math.sqrt(v_lin[0] ** 2 + v_lin[1] ** 2)
        return lin <= lin_tol and abs(v_lin[2]) <= ang_tol

    def snapshot(self) -> dict:
        """基线快照：质量、惯量、轮半径、轮距（§2.10 步骤4）"""
        return {
            "chassis_handle": self.chassis_handle,
            "left_joint": self.left_joint,
            "right_joint": self.right_joint,
            "wheel_radius": self.wheel_radius,
            "wheel_base": self.wheel_base,
        }


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


def _approach(current: float, target: float, max_delta: float) -> float:
    """把 current 以不超过 max_delta 的步长逼近 target（加速度斜坡）"""
    diff = target - current
    if abs(diff) <= max_delta:
        return target
    return current + math.copysign(max_delta, diff)