"""机械臂设备层(关节采集/控制模式/反馈)与动画器的纯单元测试。"""

import logging
import math

import factory_config as cfg
from factory_robot import RobotArmAnimator, RobotArmDevice


class FakeRobotSim:
    """假 CoppeliaSim — 覆盖 RobotArmDevice 所需的关节/模式/反馈 API。"""

    object_shape_type = 0
    object_joint_type = 1
    object_dummy_type = 4
    object_forcesensor_type = 12
    handle_all = -2
    # 4.10 实测常量: kinematic=0, dependent=4, dynamic=5 (非 1/2)
    jointmode_kinematic = 0
    jointmode_dependent = 4
    jointmode_dynamic = 5

    def __init__(self, with_arm: bool = True, joint_count: int = 6,
                 fail_feedback: bool = False):
        self.with_arm = with_arm
        self.joint_count = joint_count
        self.fail_feedback = fail_feedback
        self.root = 100
        self.joints = [101, 102, 103, 104, 105, 106][:joint_count]
        self.gripper = 200
        self.gripper_joints = [201, 202]
        self.tcp = 300
        self.parents = {}
        prev = self.root
        for j in self.joints:
            self.parents[j] = prev
            prev = j
        self.parents[self.gripper] = prev
        self.parents[201] = self.gripper
        self.parents[202] = self.gripper
        self.parents[self.tcp] = prev
        # 仿真启动时关节处于模型零位; HOME 由动画器/设备层下发
        self.aliases = {self.root: "Test_Robot_A", self.gripper: "Gripper",
                        self.tcp: "connection"}
        for i, j in enumerate(self.joints, start=1):
            self.aliases[j] = f"joint{i}"
        self.targets = []          # (handle, angle) — 兼容既有断言
        self.calls = []            # (handle, angle, motion_params)
        self.modes = {j: self.jointmode_dynamic for j in self.joints}
        self.modes.update({j: self.jointmode_dynamic for j in self.gripper_joints})
        self.positions = {j: 0.0 for j in self.joints}
        self.target_positions = {j: 0.0 for j in self.joints}

    def getObjects(self, index, object_type):
        if not self.with_arm:
            return -1
        if object_type == self.object_shape_type:
            return self.root if index == 0 else -1
        return -1

    def getObjectAlias(self, handle, options=None):
        return self.aliases.get(handle, "joint")

    def getObjectsInTree(self, root, _object_type, _options):
        # 只返回该 root 的真实子树 (与 CoppeliaSim 语义一致):
        # 夹爪根 → 仅夹爪自身 + 夹爪关节。
        if root == self.gripper:
            return [self.gripper, *reversed(self.gripper_joints)]
        # 机械臂根 → 故意乱序 + 混入夹爪/末端对象: 验证设备层按父链深度
        # 恢复 joint1..joint6, 且夹爪关节被排除在外。
        return [root, self.tcp, *reversed(self.gripper_joints),
                self.gripper, *reversed(self.joints)]

    def getObjectType(self, handle):
        if handle in (self.root, self.gripper):
            return self.object_shape_type
        if handle == self.tcp:
            return self.object_forcesensor_type
        return self.object_joint_type

    def getObjectParent(self, handle):
        return self.parents.get(handle, -1)

    def setJointTargetPosition(self, handle, angle, motion_params=None):
        self.targets.append((handle, angle))
        self.calls.append((handle, angle, motion_params))
        self.target_positions[handle] = angle

    def setJointPosition(self, handle, angle):
        self.positions[handle] = angle

    def getJointPosition(self, handle):
        if self.fail_feedback:
            raise RuntimeError("反馈读取失败 (模拟断连/引擎冻结)")
        return self.positions.get(handle, 0.0)

    def getJointTargetPosition(self, handle):
        if self.fail_feedback:
            raise RuntimeError("反馈读取失败 (模拟断连/引擎冻结)")
        return self.target_positions.get(handle, 0.0)

    def setJointMode(self, handle, mode):
        self.modes[handle] = mode

    def getJointMode(self, handle):
        # 4.10 实测: sim.getJointMode 返回 (jointMode, options) 二元组, 非标量
        return (self.modes[handle], 0)


def _joint_mode(sim, handle: int) -> int:
    """读取关节模式 — 兼容 4.10 的 (mode, options) 二元组返回。"""
    value = sim.getJointMode(handle)
    return value[0] if isinstance(value, (tuple, list)) else value


def _robot_cfg(monkeypatch):
    monkeypatch.setattr(cfg, "ISLAND_ROBOTS", {"A": {"alias": "Test_Robot_A"}})
    return cfg.ISLAND_ROBOTS["A"]


def _make_device(monkeypatch, sim=None) -> RobotArmDevice:
    entry = _robot_cfg(monkeypatch)
    return RobotArmDevice(sim or FakeRobotSim(), "A", entry,
                          logging.getLogger("robot-test"))


def _make_animator(monkeypatch):
    _robot_cfg(monkeypatch)
    sim = FakeRobotSim()
    animator = RobotArmAnimator(sim, logging.getLogger("robot-test"))
    return sim, animator


# ── 动画器 (既有行为) ────────────────────────────────────────────


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


# ── RobotArmDevice: 定位与可用性 (§9.3) ─────────────────────────


def test_device_reports_six_body_joints_tcp_and_gripper(monkeypatch):
    sim = FakeRobotSim()
    dev = _make_device(monkeypatch, sim)

    assert dev.enabled is True
    assert dev.joints == sim.joints          # 夹爪关节 201/202 被排除
    assert dev.tcp_handle == sim.tcp         # 末端 connection (force sensor)
    assert dev.gripper_handle == sim.gripper


def test_device_without_arm_is_disabled_and_silent(monkeypatch):
    dev = _make_device(monkeypatch, FakeRobotSim(with_arm=False))

    assert dev.enabled is False
    assert dev.apply_control_mode("kinematic") is False
    assert dev.command([0.1] * 6) is False   # 不抛异常, 不阻塞控制器


# ── RobotArmDevice: 控制模式 (§9.2) ─────────────────────────────


def test_device_applies_kinematic_mode_to_all_six_joints(monkeypatch):
    sim = FakeRobotSim()
    dev = _make_device(monkeypatch, sim)

    assert dev.apply_control_mode("kinematic") is True
    assert [_joint_mode(sim, j) for j in sim.joints] == [0] * 6
    assert dev.joint_modes == [0] * 6
    assert dev.control_mode == "kinematic"
    # 夹爪从动关节不参与本体模式设置
    assert _joint_mode(sim, sim.gripper_joints[0]) == sim.jointmode_dynamic


def test_device_reads_back_joint_modes(monkeypatch):
    sim = FakeRobotSim()
    dev = _make_device(monkeypatch, sim)

    assert dev.read_modes() == [sim.jointmode_dynamic] * 6


# ── RobotArmDevice: 目标下发与反馈 (§9.3/§9.4 判据基础) ─────────


def test_device_command_passes_motion_params(monkeypatch):
    sim = FakeRobotSim()
    dev = _make_device(monkeypatch, sim)

    dev.command([0.2] * 6, motion_params=[0.5, 1.0, 5.0])

    assert sim.calls[-6:] == [(j, 0.2, [0.5, 1.0, 5.0]) for j in sim.joints]


def test_device_command_without_motion_params_uses_two_arg_form(monkeypatch):
    sim = FakeRobotSim()
    dev = _make_device(monkeypatch, sim)

    dev.command([0.3] * 6)

    assert sim.calls[-6:] == [(j, 0.3, None) for j in sim.joints]


def test_device_reports_positions_targets_and_max_error(monkeypatch):
    sim = FakeRobotSim()
    dev = _make_device(monkeypatch, sim)
    dev.command([0.3] * 6)
    sim.positions.update({j: 0.3 for j in sim.joints})   # 先全部到位
    sim.positions[sim.joints[0]] = 0.1                   # 偏差 0.20 (最大)
    sim.positions[sim.joints[1]] = 0.25                  # 偏差 0.05

    assert dev.read_positions()[0] == 0.1
    assert dev.read_targets() == [0.3] * 6
    assert abs(dev.max_error() - 0.2) < 1e-9


# ── §9.3 "恰好六个本体关节" 不变量 ─────────────────────────────


def test_device_disabled_when_joint_count_is_not_six(monkeypatch):
    """§9.3: 最终必须校验恰好得到六个本体关节 —— 否则整臂静默乱动。"""
    sim = FakeRobotSim(joint_count=3)
    dev = _make_device(monkeypatch, sim)

    assert len(dev.joints) == 3          # 采集到了, 但不满足不变量
    assert dev.enabled is False
    assert dev.command([0.1] * 6) is False


def test_device_exposes_each_joint_alias_parent_and_mode(monkeypatch):
    """§9.3: 输出每个关节的别名、父对象和模式。"""
    sim = FakeRobotSim()
    dev = _make_device(monkeypatch, sim)

    assert [i["alias"] for i in dev.joint_info] == \
        [f"joint{n}" for n in range(1, 7)]
    assert dev.joint_info[0]["parent_alias"] == "Test_Robot_A"
    assert dev.joint_info[1]["parent_alias"] == "joint1"
    assert [i["mode"] for i in dev.joint_info] == [sim.jointmode_dynamic] * 6


# ── 反馈读取失败必须可见 (不得谎报到位, §9.4 判据) ─────────────


def test_reads_return_none_when_joint_feedback_fails(monkeypatch):
    sim = FakeRobotSim(fail_feedback=True)
    dev = _make_device(monkeypatch, sim)

    assert dev.read_positions() is None
    assert dev.read_targets() is None


def test_max_error_is_infinite_when_feedback_fails(monkeypatch):
    """§9.4 判据是 max_error() < tolerance; 读失败时必须在容差之外,
    绝不能因为两路读取都退回 0.0 而谎报"已到位"。"""
    sim = FakeRobotSim(fail_feedback=True)
    dev = _make_device(monkeypatch, sim)

    assert math.isinf(dev.max_error())
    assert not (dev.max_error() < cfg.ARM_POSITION_TOLERANCE)


def test_command_joint_refuses_when_targets_unreadable(monkeypatch):
    """目标位读不到时不得下发 —— 否则会把其余五轴写成 0.0。"""
    sim = FakeRobotSim(fail_feedback=True)
    dev = _make_device(monkeypatch, sim)
    before = len(sim.calls)

    assert dev.command_joint(0, 0.5) is False
    assert len(sim.calls) == before


# ── 动画器与设备层接线 (§9.7: 加载后立即配置控制模式) ───────────


def test_animator_applies_configured_control_mode(monkeypatch):
    monkeypatch.setattr(cfg, "ARM_CONTROL_MODE", "kinematic")
    sim, animator = _make_animator(monkeypatch)

    assert animator.devices["A"].joint_modes == [sim.jointmode_kinematic] * 6
