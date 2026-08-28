"""机械臂关节采集和目标位置控制的纯单元测试。"""

import logging

import factory_config as cfg
from factory_robot import RobotArmAnimator


class FakeRobotSim:
    object_shape_type = 0
    object_joint_type = 1
    object_dummy_type = 4
    handle_all = -2

    def __init__(self):
        self.root = 100
        self.joints = [101, 102, 103, 104, 105, 106]
        self.parents = {101: 100, 102: 101, 103: 102,
                        104: 103, 105: 104, 106: 105}
        self.targets = []

    def getObjects(self, index, object_type):
        if object_type == self.object_shape_type:
            return self.root if index == 0 else -1
        return -1

    def getObjectAlias(self, handle):
        if handle == self.root:
            return "Test_Robot_A"
        return "joint"

    def getObjectsInTree(self, root, _object_type, _options):
        # 故意逆序，验证动画器按父链深度恢复 joint1..joint6。
        return [root, *reversed(self.joints)]

    def getObjectType(self, handle):
        return self.object_shape_type if handle == self.root else self.object_joint_type

    def getObjectParent(self, handle):
        return self.parents.get(handle, -1)

    def setJointTargetPosition(self, handle, angle):
        self.targets.append((handle, angle))


def _make_animator(monkeypatch):
    monkeypatch.setattr(cfg, "ISLAND_ROBOTS", {
        "A": {"alias": "Test_Robot_A"},
    })
    sim = FakeRobotSim()
    animator = RobotArmAnimator(sim, logging.getLogger("robot-test"))
    return sim, animator


def test_collects_ur5_joints_in_parent_chain_order(monkeypatch):
    sim, animator = _make_animator(monkeypatch)

    assert animator.arms["A"]["joints"] == sim.joints
    assert [handle for handle, _ in sim.targets[-6:]] == sim.joints


def test_uses_target_positions_and_applies_exact_final_frame(monkeypatch):
    sim, animator = _make_animator(monkeypatch)
    sim.targets.clear()

    animator.trigger("A", "pick_and_place")
    duration = cfg.ARM_FRAME_DURATION * len(cfg.ARM_ANIMATIONS["pick_and_place"])
    animator._step_arm(animator.arms["A"], duration)

    assert animator.arms["A"]["anim"] is None
    final = cfg.ARM_ANIMATIONS["pick_and_place"][-1]
    assert sim.targets[-6:] == list(zip(sim.joints, final))
