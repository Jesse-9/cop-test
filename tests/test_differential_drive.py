"""pioneer p3dx 差速驱动测试 (§2.10).

覆盖: 差速换算、限幅、加速度斜坡、轮速下发与"绝不瞬移车体"约束。
"""

import math

import pytest

from stage1 import config as cfg
from stage1.pioneer_controller import (
    AGVState, PioneerController, PioneerError, WheelSpeeds,
    approach, clamp_cmd, wheel_velocities,
)


# ── 纯运动学 ──────────────────────────────────────────────────

def test_straight_forward_gives_equal_wheel_speeds():
    s = wheel_velocities(v=0.2, w=0.0, wheel_radius=0.1, wheel_base=0.4)
    assert math.isclose(s.left, 2.0)
    assert math.isclose(s.right, 2.0)


def test_positive_yaw_turns_left():
    s = wheel_velocities(v=0.0, w=1.0, wheel_radius=0.1, wheel_base=0.4)
    assert s.left < 0.0 < s.right


def test_formula_uses_measured_geometry():
    s = wheel_velocities(0.3, 0.5, cfg.AGV_WHEEL_RADIUS, cfg.AGV_WHEEL_BASE)
    expected_left = (0.3 - 0.5 * cfg.AGV_WHEEL_BASE / 2.0) / cfg.AGV_WHEEL_RADIUS
    expected_right = (0.3 + 0.5 * cfg.AGV_WHEEL_BASE / 2.0) / cfg.AGV_WHEEL_RADIUS
    assert math.isclose(s.left, expected_left)
    assert math.isclose(s.right, expected_right)


def test_non_positive_geometry_raises():
    with pytest.raises(PioneerError):
        wheel_velocities(0.1, 0.0, 0.0, 0.3)
    with pytest.raises(PioneerError):
        wheel_velocities(0.1, 0.0, 0.1, 0.0)


def test_clamp_cmd_limits_velocity_and_yaw():
    v, w = clamp_cmd(10.0, -10.0, 0.4, 1.2)
    assert v == 0.4 and w == -1.2


def test_approach_is_symmetric_and_bounded():
    assert approach(0.0, 1.0, 0.3) == 0.3
    assert approach(0.0, -1.0, 0.3) == -0.3
    assert approach(0.9, 1.0, 0.3) == 1.0


# ── 控制器 ────────────────────────────────────────────────────

class FakeSim:
    """只允许关节速度接口; 任何位姿写入都会被记录为违规。"""

    def __init__(self):
        self.wheel_cmds = []
        self.forbidden = []

    def setJointTargetVelocity(self, handle, velocity):
        self.wheel_cmds.append((handle, velocity))

    def getObjectPose(self, handle):
        return [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0]

    def getObjectVelocity(self, handle):
        return [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]

    def setObjectPosition(self, *args, **kwargs):
        self.forbidden.append(("setObjectPosition", args))

    def setObjectPose(self, *args, **kwargs):
        self.forbidden.append(("setObjectPose", args))


def _controller(sim=None):
    sim = sim or FakeSim()
    return PioneerController(
        sim, left_joint=11, right_joint=12,
        wheel_radius=cfg.AGV_WHEEL_RADIUS, wheel_base=cfg.AGV_WHEEL_BASE,
        chassis_handle=10,
    )


def test_controller_ramps_towards_command():
    sim = FakeSim()
    c = _controller(sim)
    speeds1 = c.set_cmd(0.2, 0.0)
    speeds2 = c.set_cmd(0.2, 0.0)
    assert abs(speeds2.left) > abs(speeds1.left)
    assert c.state is AGVState.MOVING
    assert len(sim.wheel_cmds) == 4      # 每步左右各一次
    assert not sim.forbidden, "AGV 控制不得写车体位姿"


def test_turning_state_and_wheel_limit():
    sim = FakeSim()
    c = _controller(sim)
    for _ in range(40):
        s = c.set_cmd(0.0, 1.0)
    assert c.state is AGVState.TURNING
    assert abs(s.left) <= cfg.AGV_MAX_WHEEL_VEL + 1e-9
    assert abs(s.right) <= cfg.AGV_MAX_WHEEL_VEL + 1e-9


def test_stop_zeroes_wheels_immediately():
    sim = FakeSim()
    c = _controller(sim)
    c.set_cmd(0.2, 0.2)
    sim.wheel_cmds.clear()
    c.stop()
    assert sim.wheel_cmds == [(11, 0.0), (12, 0.0)]
    assert c.state is AGVState.IDLE


def test_is_stopped_uses_velocity_tuple():
    class MovingSim(FakeSim):
        def getObjectVelocity(self, handle):
            return [0.2, 0.0, 0.0], [0.0, 0.0, 0.0]

    c = _controller(MovingSim())
    assert c.is_stopped() is False
    c2 = _controller(FakeSim())
    assert c2.is_stopped() is True


def test_never_writes_chassis_pose_during_long_run():
    sim = FakeSim()
    c = _controller(sim)
    for i in range(200):
        c.set_cmd(0.15, 0.1 * math.sin(i / 10.0))
    assert not sim.forbidden
