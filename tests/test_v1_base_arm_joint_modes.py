"""基础层 UR5 关节模式契约测试 (步骤 1.2 暂缓项)."""

from __future__ import annotations


class _JointModeSim:
    jointmode_kinematic = 0
    jointmode_dynamic = 5

    def __init__(self, modes):
        self.modes = list(modes)

    def getJointMode(self, handle):
        # 4.10 实测返回 (mode, options); 也覆盖标量返回
        mode = self.modes[handle]
        return mode if isinstance(mode, tuple) else mode


def test_arm_joint_modes_ok_all_kinematic():
    from flexible_factory.simulation.scene_base import arm_joint_modes_ok

    sim = _JointModeSim([(0, 1), (0, 1), (0, 1), (0, 1), (0, 1), (0, 1)])
    ok, modes = arm_joint_modes_ok(sim, list(range(6)))
    assert ok is True
    assert modes == [0] * 6


def test_arm_joint_modes_ok_rejects_dynamic_joint():
    from flexible_factory.simulation.scene_base import arm_joint_modes_ok

    sim = _JointModeSim([(0, 1), (0, 1), (0, 1), (0, 1), (5, 1), (0, 1)])
    ok, modes = arm_joint_modes_ok(sim, list(range(6)))
    assert ok is False
    assert modes[4] == 5


def test_arm_joint_modes_ok_handles_scalar_return():
    from flexible_factory.simulation.scene_base import arm_joint_modes_ok

    sim = _JointModeSim([0, 0, 0, 0, 0, 0])
    ok, modes = arm_joint_modes_ok(sim, list(range(6)))
    assert ok is True
    assert modes == [0] * 6
