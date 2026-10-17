"""build_scene 机械臂构建范围选择 + 纯运动学改造的单元测试。

- 构建范围 (§9.7 第 5 条): 阶段一带动验证只启用 A 岛机械臂。
- 纯运动学改造: 把机械臂整条子树的 shape 设为非动态 (4.10 实测必需, 见 CLAUDE.md 坑 9)。
"""

import reference.build_scene as bs
import reference.factory_config as cfg


ROBOTS = {
    "A": {"alias": "Island_A_Assembly_Robot_A"},
    "B": {"alias": "Island_B_Preprocess_Robot_B"},
    "E_C": {"alias": "Island_E_FinalAssembly_Robot_E_C"},
    "E_D": {"alias": "Island_E_FinalAssembly_Robot_E_D"},
}


def test_arm_keys_defaults_to_every_configured_arm():
    assert bs.arm_keys_to_build(None, ROBOTS) == list(ROBOTS)


def test_arm_keys_restricts_to_requested_islands():
    assert bs.arm_keys_to_build(["A"], ROBOTS) == ["A"]


def test_arm_keys_ignores_undefined_islands():
    assert bs.arm_keys_to_build(["A", "Z"], ROBOTS) == ["A"]


def test_arm_keys_returns_config_order_and_dedupes():
    assert bs.arm_keys_to_build(["E_D", "A", "E_D"], ROBOTS) == ["A", "E_D"]


def test_arm_keys_of_real_config_covers_all_six_arms():
    assert bs.arm_keys_to_build(None) == list(cfg.ISLAND_ROBOTS)
    assert bs.arm_keys_to_build(["A"]) == ["A"]


# ── 纯运动学改造: make_arm_kinematic ────────────────────────────


class FakeShapeSim:
    """只覆盖 make_arm_kinematic 所需的子树枚举 + static int-param 读写。"""

    object_shape_type = 0
    object_joint_type = 1
    shapeintparam_static = 3003
    handle_all = -2

    def __init__(self, static_flags: dict, joints=()):
        self.flags = dict(static_flags)
        self.joints = set(joints)

    def getObjectsInTree(self, root, _t, _o):
        # 真实 API 的子树列表不含重复项 (根只出现一次)
        return list(dict.fromkeys([root, *self.flags, *self.joints]))

    def getObjectType(self, h):
        return self.object_joint_type if h in self.joints else self.object_shape_type

    def getObjectInt32Param(self, h, param):
        if param != self.shapeintparam_static:
            raise ValueError(f"只支持 shapeintparam_static, 收到 {param}")
        return self.flags[h]

    def setObjectInt32Param(self, h, param, value):
        if param != self.shapeintparam_static:
            raise ValueError(f"只支持 shapeintparam_static, 收到 {param}")
        self.flags[h] = value


def test_make_arm_kinematic_converts_only_dynamic_shapes():
    sim = FakeShapeSim({10: 0, 11: 0, 12: 1})

    report = bs.make_arm_kinematic(sim, 10)

    assert sim.flags == {10: 1, 11: 1, 12: 1}      # 只动 static=0 的
    assert report == {"total": 3, "changed": 2}


def test_make_arm_kinematic_leaves_joints_untouched():
    sim = FakeShapeSim({10: 0}, joints=(20, 21))

    report = bs.make_arm_kinematic(sim, 10)

    assert sim.flags == {10: 1}
    assert report == {"total": 1, "changed": 1}


def test_make_arm_kinematic_is_idempotent():
    sim = FakeShapeSim({10: 0, 11: 1})
    bs.make_arm_kinematic(sim, 10)

    report = bs.make_arm_kinematic(sim, 10)

    assert report == {"total": 2, "changed": 0}
