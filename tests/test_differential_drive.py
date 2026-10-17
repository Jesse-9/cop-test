"""tests/test_differential_drive.py — pioneer p3dx 差速驱动纯逻辑测试 (阶段一 §2.10)

不需要 CoppeliaSim：轮速换算是纯数学，控制器用轻量 fake sim 驱动。
CLAUDE.md §7 要求 AGV 必须通过左右轮 setJointTargetVelocity 驱动，
本测试同时守住「不得位置瞬移」这条不变量。
"""

import pytest

from pioneer_controller import (
    PioneerController,
    PioneerError,
    wheel_velocities,
    AGVState,
)


# 测试用几何参数 (对应 docs/measurement/pioneer_p3dx.json, 需实测复核)
R = 0.0981     # 轮半径 (m)
B = 0.1995     # 轮距 (m)


class FakeSim:
    """记录关节速度下发，不涉及物理引擎"""

    def __init__(self):
        self.joint_targets = {}
        self.pose_calls = 0

    def setJointTargetVelocity(self, joint, velocity):
        self.joint_targets[joint] = velocity

    def getObjectPose(self, handle):
        self.pose_calls += 1
        return [0.0] * 7

    def getObjectVelocity(self, handle):
        return [0.0] * 3


# ═══════════════════════════════════════════════════════════════
# 差速换算 (纯函数)
# ═══════════════════════════════════════════════════════════════

def test_straight_forward_wheels_equal():
    """直线前进: 左右轮角速度相等"""
    s = wheel_velocities(v=0.5, w=0.0, wheel_radius=R, wheel_base=B)
    assert s.left == pytest.approx(s.right)
    assert s.left == pytest.approx(0.5 / R)


def test_pivot_turn_left_wheels_opposite():
    """原地左转 (w>0): 右轮正转、左轮反转"""
    s = wheel_velocities(v=0.0, w=1.0, wheel_radius=R, wheel_base=B)
    assert s.left < 0 < s.right
    # 角速度 ω = (v_r - v_l) / B
    assert (s.right - s.left) * R == pytest.approx(1.0 * B)


def test_reverse_uses_negative_wheel_speeds():
    """倒车: 两轮均为负"""
    s = wheel_velocities(v=-0.3, w=0.0, wheel_radius=R, wheel_base=B)
    assert s.left < 0 and s.right < 0


def test_invalid_geometry_raises():
    """轮半径/轮距非正必须报错，而不是除零或静默"""
    with pytest.raises(PioneerError):
        wheel_velocities(v=0.5, w=0.0, wheel_radius=0.0, wheel_base=B)
    with pytest.raises(PioneerError):
        wheel_velocities(v=0.5, w=0.0, wheel_radius=R, wheel_base=-1.0)


# ═══════════════════════════════════════════════════════════════
# 控制器：限幅与斜坡
# ═══════════════════════════════════════════════════════════════

def _ctl(sim=None):
    sim = sim or FakeSim()
    return PioneerController(sim, left_joint=11, right_joint=12,
                             wheel_radius=R, wheel_base=B, chassis_handle=99), sim


def test_speed_clamped_to_max_linear():
    """超出 max_linear_vel 的命令被限幅 (CLAUDE.md §7 轮速上限)"""
    ctl, sim = _ctl()
    ctl.max_linear_vel = 0.8
    ctl.max_accel = 1e6   # 关闭斜坡便于断言
    ctl.set_cmd(v=5.0, w=0.0)
    s = wheel_velocities(0.8, 0.0, R, B)
    assert sim.joint_targets[11] == pytest.approx(s.left)
    assert abs(sim.joint_targets[11]) <= ctl.max_wheel_vel


def test_yaw_rate_clamped():
    ctl, sim = _ctl()
    ctl.max_yaw_rate = 2.0
    ctl.max_accel = 1e6
    ctl.set_cmd(v=0.0, w=10.0)
    s = wheel_velocities(0.0, 2.0, R, B)
    assert sim.joint_targets[11] == pytest.approx(s.left)
    assert sim.joint_targets[12] == pytest.approx(s.right)


def test_acceleration_ramp_limits_step():
    """加速度斜坡: 单步速度变化不超过 max_accel * dt"""
    ctl, sim = _ctl()
    ctl.max_accel = 0.5
    ctl.max_linear_vel = 10.0
    ctl.set_cmd(v=5.0, w=0.0)          # 目标 5 m/s
    first = sim.joint_targets[11] * R   # 实际下发线速度
    # 首步只走到 max_accel * dt，且远小于目标 5 m/s
    assert first == pytest.approx(ctl.max_accel * 0.05, abs=1e-6)
    assert first < 5.0


def test_ramp_converges_to_command():
    """斜坡最终收敛到目标线速度。

    注意: 可达线速度上限 = max_wheel_vel * R = 12.0 * 0.0981 ≈ 1.177 m/s,
    高于配置的最大线速度 0.8 m/s, 故 0.8 m/s 是可达目标。
    """
    ctl, sim = _ctl()
    ctl.max_accel = 0.5
    ctl.max_linear_vel = 10.0
    ctl.set_cmd(v=0.8, w=0.0)
    for _ in range(200):
        ctl.apply(dt=0.05)
    assert sim.joint_targets[11] * R == pytest.approx(0.8, abs=1e-6)


def test_wheel_speed_limit_caps_reachable_linear_speed():
    """限幅顺序: max_linear_vel (0.8) 先于 max_wheel_vel 生效，
    故 5 m/s 的命令最终收敛到 0.8 m/s。"""
    ctl, sim = _ctl()
    ctl.max_accel = 1e6      # 关闭斜坡
    ctl.set_cmd(v=5.0, w=0.0)
    assert abs(sim.joint_targets[11]) <= ctl.max_wheel_vel
    assert sim.joint_targets[11] * R == pytest.approx(
        ctl.max_linear_vel, abs=1e-9)


def test_stop_zeroes_both_wheels():
    ctl, sim = _ctl()
    ctl.max_accel = 1e6
    ctl.set_cmd(v=0.5, w=0.0)
    ctl.stop()
    assert sim.joint_targets[11] == 0.0
    assert sim.joint_targets[12] == 0.0
    assert ctl.state is AGVState.IDLE


def test_state_classification():
    ctl, _ = _ctl()
    ctl.max_accel = 1e6
    ctl.set_cmd(v=0.5, w=0.0)
    assert ctl.state is AGVState.MOVING
    ctl.set_cmd(v=0.0, w=1.0)
    assert ctl.state is AGVState.TURNING
    ctl.set_cmd(v=0.0, w=0.0)
    assert ctl.state is AGVState.IDLE


# ═══════════════════════════════════════════════════════════════
# 不变量: 控制器从不设置车体世界位姿 (§7 禁止位置瞬移)
# ═══════════════════════════════════════════════════════════════

def test_controller_never_teleports_chassis():
    """FakeSim 只提供 getObjectPose (读), 不提供 setObjectPose;
    跑完整控制流程后 chassis 的位姿写入次数必须为 0。"""
    sim = FakeSim()
    assert not hasattr(sim, "setObjectPose"), "FakeSim 应不提供位姿写入接口"
    ctl = PioneerController(sim, left_joint=11, right_joint=12,
                            wheel_radius=R, wheel_base=B, chassis_handle=99)
    ctl.set_cmd(v=0.5, w=0.2)
    ctl.apply()
    ctl.stop()
    assert sim.pose_calls == 0


def test_snapshot_keeps_baseline_geometry():
    ctl, _ = _ctl()
    snap = ctl.snapshot()
    assert snap["wheel_radius"] == R
    assert snap["wheel_base"] == B