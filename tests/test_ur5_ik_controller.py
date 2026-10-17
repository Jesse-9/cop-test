"""tests/test_ur5_ik_controller.py — simIK 控制器逻辑测试 (阶段一 §2.7)

用 FakeSimIK 模拟 4.10 的 simIK 返回形态 (实测三元组):
  handleGroup → (result_code, reason, [linear_error, angular_error])
  result_success = 1

验证:
  - 主组成功 → 直接返回
  - 主组失败、备用组成功 → used_backup=True
  - 两组均失败 → success=False, joints=[], 不写回任何伪造角度 (§2.7 步骤9)
  - 目标位移限幅 (ARM_IK_TARGET_STEP 逐步逼近)
"""

import pytest

from ur5_ik_controller import (
    Ur5IkController,
    IKResult,
    IKError,
    UR5_REACH_RADIUS,
)


class FakeSimIK:
    """模拟 simIK 命名空间 + sim 对象的组合"""

    def __init__(self, main_result=(1, 0, [0.0, 0.0]),
                 backup_result=(1, 0, [0.0, 0.0])):
        self.calls = []
        self.main_result = main_result
        self.backup_result = backup_result
        self.group_ids = {}
        self._next_group = 1
        self._next_env = 1000
        self.joint_positions = {}

    # ── simIK 命名空间 ──
    simIK_pseudoinverse = 1
    simIK_dampedLeastSquares = 2
    simIKconstraint_position = 10
    simIKconstraint_pose = 11
    result_success = 1

    def simIKCreateEnvironment(self):
        self._next_env += 1
        self.calls.append(("createEnv", self._next_env))
        return self._next_env

    def simIKCreateGroup(self, env):
        self._next_group += 1
        gid = self._next_group
        # 第一个建的组是主组, 第二个是备用
        self.calls.append(("createGroup", env, gid))
        return gid

    def simIKSetGroupCalculation(self, group, method, iterations):
        self.calls.append(("setCalc", group, method, iterations))

    def simIKAddElementFromScene(self, env, group, base, target, constraint):
        self.calls.append(("addElement", env, group, base, target, constraint))
        return (0, {target: 1}, {1: target})   # 实测返回三元组

    def simIKSyncFromSim(self, env, groups):
        self.calls.append(("syncFromSim", env, tuple(groups)))

    def simIKSyncToSim(self, env, groups):
        self.calls.append(("syncToSim", env, tuple(groups)))

    def simIKHandleGroup(self, group):
        self.calls.append(("handleGroup", group))
        # 第一个创建的组 (主) vs 第二个 (备用)
        main_group = 2
        if group == main_group:
            return self.main_result
        return self.backup_result

    # ── sim 对象部分 ──
    handle_world = "world"

    def getJointPosition(self, h):
        self.calls.append(("getJointPosition", h))
        return self.joint_positions.get(h, 0.0)

    def setObjectPosition(self, h, pos, rel):
        self.calls.append(("setObjectPosition", h, tuple(pos), rel))

    def setObjectQuaternion(self, h, q, rel):
        self.calls.append(("setObjectQuaternion", h, tuple(q), rel))

    def getObjectPosition(self, h, rel):
        self.calls.append(("getObjectPosition", h, rel))
        return [0.0, 0.0, 0.0]


JOINTS = [1, 2, 3, 4, 5, 6]


def _ctl(fake=None):
    fake = fake or FakeSimIK()
    c = Ur5IkController(fake, root_handle=100, base_handle=101,
                        tcp_handle=102, target_handle=103,
                        joint_handles=JOINTS)
    c.initialize()
    return c, fake


def test_initialize_creates_env_and_two_groups():
    c, fake = _ctl()
    assert c.initialized
    creates = [x for x in fake.calls if x[0] == "createGroup"]
    assert len(creates) == 2
    calcs = [x for x in fake.calls if x[0] == "setCalc"]
    # 主组伪逆, 备用阻尼
    assert calcs[0][2] == fake.simIK_pseudoinverse
    assert calcs[1][2] == fake.simIK_dampedLeastSquares


def test_solve_target_main_success():
    c, fake = _ctl()
    r = c.solve_target([0.3, 0.0, 0.8, 0, 0, 0, 1])
    assert r.success
    assert not r.used_backup
    assert len(r.joints) == 6


def test_solve_target_falls_back_to_backup():
    """主组失败 → 备用组成功 (§2.7 步骤8)"""
    fake = FakeSimIK(main_result=(0, 112, [0.2, 0.1]),
                     backup_result=(1, 0, [0.001, 0.001]))
    c, _ = _ctl(fake)
    r = c.solve_target([0.3, 0.0, 0.8, 0, 0, 0, 1])
    assert r.success
    assert r.used_backup


def test_solve_target_both_fail_returns_explicit_failure():
    """两组均失败 → 明确失败状态, 不写回伪造角度 (§2.7 步骤9)"""
    fake = FakeSimIK(main_result=(0, 112, [0.4, 0.2]),
                     backup_result=(0, 112, [0.4, 0.2]))
    c, _ = _ctl(fake)
    r = c.solve_target([2.0, 0.0, 0.8, 0, 0, 0, 1])   # 远离工作半径
    assert not r.success
    assert r.joints == []           # 不写回任何角度
    assert "不可达" in r.message
    assert r.used_backup


def test_target_step_limiting():
    """§2.7: 一次跳 0.7 m 解不出来 → 逐步逼近, 每步 ≤ ARM_IK_TARGET_STEP"""
    fake = FakeSimIK()
    c, _ = _ctl(fake)
    c.solve_target([0.0, 0.0, 0.8, 0, 0, 0, 1])
    # 第二次给一个远目标
    c.solve_target([0.7, 0.0, 0.8, 0, 0, 0, 1])

    positions = [x for x in fake.calls if x[0] == "setObjectPosition"]
    # 最后一个下发的目标必须距上一个不超过 max_target_step
    last = positions[-1][2]
    prev = positions[-2][2]
    import math
    d = math.sqrt(sum((last[k] - prev[k]) ** 2 for k in range(3)))
    assert d <= c.max_target_step + 1e-9


def test_reachable_uses_work_radius():
    c, fake = _ctl()
    assert c.reachable([0.3, 0.0, 0.4, 0, 0, 0, 1]) is True
    assert c.reachable([2.0, 0.0, 0.4, 0, 0, 0, 1]) is False