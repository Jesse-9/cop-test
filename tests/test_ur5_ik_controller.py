"""simIK 控制器测试 (§2.7).

覆盖: 独立 environment/主备 group、场景→IK 同步、主组失败回退、
双组失败不写伪造角度、关节目标下发。使用 fake sim/simIK。
"""

import pytest

from stage1 import config as cfg
from stage1.ur5_ik_controller import IKResult, Ur5IkController


SCENE_JOINTS = [10, 11, 12, 13, 14, 15]
IK_JOINTS = [100, 101, 102, 103, 104, 105]
SOLUTION = [-1.0, 0.5, 1.2, 0.3, 1.0, -0.2]


class FakeSimIK:
    def __init__(self):
        self.calls = []
        self.main_ok = True
        self.backup_ok = True
        self.seed_ok = False
        self.seeded = False
        self.solution = list(SOLUTION)
        self.env_count = 0
        self.group_count = 0
        self.main_group = None
        self.backup_group = None

    def createEnvironment(self, *a):
        self.env_count += 1
        self.calls.append(("createEnvironment",))
        return 500 + self.env_count

    def createGroup(self, env, *a):
        self.group_count += 1
        self.calls.append(("createGroup", env))
        g = 600 + self.group_count
        if self.main_group is None:
            self.main_group = g
        elif self.backup_group is None:
            self.backup_group = g
        return g

    def setGroupCalculation(self, env, group, method, damping, max_iter):
        self.calls.append(("setGroupCalculation", group, method, damping, max_iter))

    def addElementFromScene(self, env, group, base, tip, target, constraints):
        self.calls.append(("addElementFromScene", env, group, base, tip, target,
                           constraints))
        mapping = {s: i for s, i in zip(SCENE_JOINTS, IK_JOINTS)}
        mapping[tip] = 200
        mapping[target] = 201
        return (0, mapping, {v: k for k, v in mapping.items()})

    def syncFromSim(self, env, groups):
        self.calls.append(("syncFromSim", env, tuple(groups)))

    def setJointPosition(self, env, ik_joint, value):
        self.calls.append(("setJointPosition", env, ik_joint, value))
        self.seeded = True

    def syncToSim(self, env, groups):
        self.calls.append(("syncToSim", env, tuple(groups)))

    def handleGroup(self, env, group, options=None):
        self.calls.append(("handleGroup", group, dict(options or {})))
        is_main = group == self.main_group
        if self.seeded:
            ok = self.seed_ok
            self.seeded = False
        else:
            ok = self.main_ok if is_main else self.backup_ok
        if ok:
            return (1, 32, [1e-6, 1e-6])
        return (0, 112, [0.4, 0.2])

    def getGroupJoints(self, env, group):
        return list(IK_JOINTS)

    def getJointPosition(self, env, ik_joint):
        return self.solution[IK_JOINTS.index(ik_joint)]

    # 常量
    method_pseudo_inverse = 1
    method_damped_least_squares = 2
    constraint_pose = 8
    result_success = 1
    result_fail = 0


class FakeSim:
    def __init__(self):
        self.target_pose = None
        self.joint_cmds = []
        self.joint_positions = list(SOLUTION)
        self.handle_world = -1

    def setObjectPose(self, handle, pose, rel=None):
        self.target_pose = list(pose)

    def setJointTargetPosition(self, handle, value, motion=None):
        self.joint_cmds.append((handle, value, tuple(motion or ())))

    def getJointPosition(self, handle):
        return self.joint_positions[SCENE_JOINTS.index(handle)]

    def getObjectPosition(self, handle, rel=None):
        return [0.0, 0.0, 0.0]

    def getObjectQuaternion(self, handle, rel=None):
        return [0.0, 0.0, 0.0, 1.0]


def _controller(main_ok=True, backup_ok=True):
    sim = FakeSim()
    ik = FakeSimIK()
    ik.main_ok = main_ok
    ik.backup_ok = backup_ok
    c = Ur5IkController(sim, ik, arm_root=1, tcp_handle=2, target_handle=3,
                        scene_joints=SCENE_JOINTS)
    c.initialize()
    return sim, ik, c


def test_initialize_creates_environment_and_two_groups():
    sim, ik, c = _controller()
    assert ik.env_count == 1
    assert ik.group_count == 2
    assert c.initialized
    x = [call for call in ik.calls if call[0] == "addElementFromScene"]
    assert len(x) == 2, "主组与备用组各建一个 IK element"


def test_solve_success_returns_scene_joint_angles():
    sim, ik, c = _controller()
    result = c.solve([0.5, 0.0, 0.4, 0, 0, 0, 1])
    assert result.success
    assert result.joints == pytest.approx(SOLUTION)
    assert result.used_backup is False
    assert not any(call[0] == "syncToSim" for call in ik.calls), (
        "控制器自己下发关节目标, 不能让 syncToSim 瞬时写关节")
    assert sim.target_pose[:3] == [0.5, 0.0, 0.4]


def test_main_failure_falls_back_to_damped_least_squares():
    sim, ik, c = _controller(main_ok=False, backup_ok=True)
    result = c.solve([0.5, 0.0, 0.4, 0, 0, 0, 1])
    assert result.success
    assert result.used_backup is True
    groups = [x[1] for x in ik.calls if x[0] == "handleGroup"]
    assert groups == [ik.main_group, ik.backup_group]


def test_both_groups_fail_returns_explicit_failure():
    sim, ik, c = _controller(main_ok=False, backup_ok=False)
    result = c.solve([5.0, 5.0, 5.0, 0, 0, 0, 1])
    assert result.success is False
    assert result.joints == []
    assert "不可达" in result.message or "失败" in result.message
    assert not sim.joint_cmds, "失败不得写回任何关节目标"


def test_seed_fallback_after_both_groups_fail():
    sim, ik, c = _controller(main_ok=False, backup_ok=False)
    ik.seed_ok = True
    result = c.solve([0.5, 0.0, 0.4, 0, 0, 0, 1])
    assert result.success
    assert result.used_seed is True
    assert any(call[0] == "setJointPosition" for call in ik.calls)


def test_apply_sends_joint_targets_with_motion_params():
    sim, ik, c = _controller()
    result = c.solve([0.5, 0.0, 0.4, 0, 0, 0, 1])
    c.apply_joints(result.joints)
    assert [cmd[0] for cmd in sim.joint_cmds] == SCENE_JOINTS
    assert all(cmd[2] == (cfg.ARM_JOINT_TARGET_VEL,
                          cfg.ARM_JOINT_TARGET_ACCEL,
                          cfg.ARM_JOINT_TARGET_JERK)
               for cmd in sim.joint_cmds)


def test_joint_limit_check():
    sim, ik, c = _controller()
    assert c.joints_within_limits([0.0] * 6)
    assert not c.joints_within_limits([10.0] + [0.0] * 5)


def test_converged_compares_actual_to_command():
    sim, ik, c = _controller()
    sim.joint_positions = list(SOLUTION)
    assert c.converged(SOLUTION)
    sim.joint_positions[0] += cfg.ARM_JOINT_CONVERGE_TOL * 3
    assert not c.converged(SOLUTION)
