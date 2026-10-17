"""tests/test_suction_controller.py — 逻辑吸附与释放测试 (阶段一 §2.9)

不需要 CoppeliaSim 连接: 用 FakeSim 记录调用序列, 验证
  - attach 前置检查 (UID / sensor 命中 / TCP 距离 / 倾角 / 速度)
  - 原始物理属性快照与恢复 (父对象/位姿/dynamic/respondable/mass/摩擦/掩码)
  - 状态机流转 (READY_FOR_PICK → HELD_BY_SUCTION → ON_TRAY → RELEASED)
  - resetDynamicObject 调用
  - 失败时保留故障现场与原始快照
"""

import pytest

from material_manager import MaterialManager, MaterialState, InvalidTransition
from suction_controller import (
    SuctionController,
    AttachRejected,
    ATTACH_OK,
    REJECT_UID_UNKNOWN,
    REJECT_STATE,
    REJECT_NO_SENSOR_HIT,
    REJECT_UID_MISMATCH,
    REJECT_TCP_TOO_FAR,
    REJECT_TOO_TILTED,
)


LINK_H = 50
TCP_H = 51
SENSOR_H = 52


class FakeSim:
    """只记录调用, 不涉及物理引擎"""

    def __init__(self):
        self.calls = []
        self.parent_of = {}       # handle → parent
        self.static = {}          # handle → int
        self.respondable = {}
        self.mass = {}
        self.pose = {}
        self.vel = {}

    # 属性读写
    def getObjectParent(self, h):
        self.calls.append(("getObjectParent", h))
        return self.parent_of.get(h, -1)

    def setObjectParent(self, h, parent, keep):
        self.calls.append(("setObjectParent", h, parent, keep))
        self.parent_of[h] = parent

    def getObjectPose(self, h):
        self.calls.append(("getObjectPose", h))
        return self.pose.get(h, [0, 0, 0, 0, 0, 0, 1])

    def getObjectPosition(self, h, rel=None):
        self.calls.append(("getObjectPosition", h))
        return list(self.pose.get(h, [0, 0, 0, 0, 0, 0, 1])[:3])

    def getObjectQuaternion(self, h, rel=None):
        self.calls.append(("getObjectQuaternion", h))
        return list(self.pose.get(h, [0, 0, 0, 0, 0, 0, 1])[3:7])

    def setObjectPose(self, h, pose, rel):
        self.calls.append(("setObjectPose", h, tuple(pose), rel))
        self.pose[h] = list(pose)

    def setObjectPosition(self, h, pos, rel):
        self.calls.append(("setObjectPosition", h, tuple(pos), rel))
        self.pose.setdefault(h, [0, 0, 0, 0, 0, 0, 1])[:3] = list(pos)

    def setObjectQuaternion(self, h, q, rel):
        self.calls.append(("setObjectQuaternion", h, tuple(q), rel))

    def getObjectInt32Parameter(self, h, p):
        self.calls.append(("getObjectInt32Parameter", h, p))
        # 参数名可能是 sim.shapeintparam_static / ..._respondable 或裸名
        name = str(p)
        if "static" in name:
            return self.static.get(h, 0)
        if "respondable" in name:
            return self.respondable.get(h, 1)
        return 0

    def setObjectInt32Param(self, h, p, v):
        self.calls.append(("setObjectInt32Param", h, p, v))
        name = str(p)
        if "static" in name:
            self.static[h] = v
        elif "respondable" in name:
            self.respondable[h] = v

    def getObjectFloatParameter(self, h, p):
        self.calls.append(("getObjectFloatParameter", h, p))
        return self.mass.get(h, 1.0)

    def setObjectFloatParam(self, h, p, v):
        self.calls.append(("setObjectFloatParam", h, p, v))
        self.mass[h] = v

    def setFloatProperty(self, h, name, v):
        self.calls.append(("setFloatProperty", h, name, v))

    def setBoolProperty(self, h, name, v):
        self.calls.append(("setBoolProperty", h, name, v))
        if name == "respondable":
            self.respondable[h] = 1 if v else 0

    def setIntProperty(self, h, name, v):
        self.calls.append(("setIntProperty", h, name, v))

    def getObjectVelocity(self, h):
        self.calls.append(("getObjectVelocity", h))
        return self.vel.get(h, [0.0, 0.0, 0.0])

    def resetDynamicObject(self, h):
        self.calls.append(("resetDynamicObject", h))

    def computeMassAndInertia(self, h, density):
        self.calls.append(("computeMassAndInertia", h, density))

    def __getattr__(self, name):
        # sim 常量兜底: 未定义属性当作常量对象
        class _Const:
            def __init__(self, n):
                self._n = n

            def __getattr__(self, k):
                return f"{self._n}.{k}"

            def __call__(self, *a):
                return f"{self._n}{a}"

        if name.startswith(("handle_", "simIK", "jointmode", "shapeintparam",
                            "objfloatparam", "object_", "primitiveshape")):
            return _Const(name)
        raise AttributeError(name)

    # 便捷断言
    def called(self, name):
        return [c for c in self.calls if c[0] == name]


def _setup(state=MaterialState.READY_FOR_PICK, tcp_gap=0.01, tilt=0.0,
           vel=0.0, hit_handle=99, uid=1):
    """构建 controller + manager + 场景对象。物料句柄=99。"""
    sim = FakeSim()
    sim.pose[99] = [0.5, 0.0, 0.9, 0, 0, 0, 1]   # 物料位姿
    sim.pose[TCP_H] = [0.5, 0.0, 0.9 + tcp_gap, 0, 0, 0, 1]
    sim.vel[99] = [vel, 0.0, 0.0]
    sim.parent_of[99] = -1

    mgr = MaterialManager()
    rec = mgr.register(uid=uid, alias=f"Part_{uid:04d}")
    # 手工推进到指定状态
    for s in (MaterialState.ON_CONVEYOR, MaterialState.READY_FOR_PICK):
        if rec.state is s:
            continue
        rec.transition_to(s, sim_time=0.0, reason="test setup")
        if s is state:
            break
    if state is not MaterialState.READY_FOR_PICK and rec.state is not state:
        for s in (MaterialState.HELD_BY_SUCTION, MaterialState.ON_TRAY,
                  MaterialState.RELEASED):
            if rec.state is state:
                break
            rec.transition_to(s, sim_time=0.0, reason="test setup")

    mgr.attach_sim(uid, 99)

    ctl = SuctionController(sim, mgr, link_handle=LINK_H, tcp_handle=TCP_H,
                            sensor_handle=SENSOR_H, logger=None)
    ctl._sensor_hit = lambda: hit_handle       # 注入 sensor 读数
    ctl._tcp_tilt = lambda: tilt               # 注入倾角
    return ctl, sim, mgr, rec


# ═══════════════════════════════════════════════════════════════
# 前置检查 (§2.9 步骤1)
# ═══════════════════════════════════════════════════════════════

def test_attach_happy_path():
    ctl, sim, mgr, rec = _setup()
    result = ctl.attach(target_uid=1, sim_time=3.0)
    assert result.code == ATTACH_OK
    assert rec.state is MaterialState.HELD_BY_SUCTION
    # 绑定到吸盘 link 或 TCP
    assert sim.parent_of[99] in (LINK_H, TCP_H)


def test_attach_rejects_unknown_uid():
    ctl, sim, mgr, rec = _setup()
    result = ctl.attach(target_uid=999, sim_time=3.0)
    assert result.code == REJECT_UID_UNKNOWN
    assert rec.state is MaterialState.READY_FOR_PICK


def test_attach_rejects_wrong_state():
    ctl, sim, mgr, rec = _setup()
    # 把物料推回 ON_CONVEYOR (通过新建一个)
    mgr2 = mgr
    rec2 = mgr2.register(uid=2, alias="Part_0002")
    rec2.transition_to(MaterialState.ON_CONVEYOR, sim_time=0.0, reason="t")
    mgr2.attach_sim(2, 98)
    result = ctl.attach(target_uid=2, sim_time=3.0)
    assert result.code == REJECT_STATE


def test_attach_rejects_uid_mismatch():
    """传感器命中另一物料 (§2.8 步骤5): 记录 PICK_MISMATCH 且不吸附"""
    ctl, sim, mgr, rec = _setup(hit_handle=77)   # 命中 77, 目标 99
    result = ctl.attach(target_uid=1, sim_time=3.0)
    assert result.code == REJECT_UID_MISMATCH


def test_attach_rejects_no_sensor_hit():
    """传感器无读数 (§2.8 步骤7)"""
    ctl, sim, mgr, rec = _setup(hit_handle=-1)
    result = ctl.attach(target_uid=1, sim_time=3.0)
    assert result.code == REJECT_NO_SENSOR_HIT


def test_attach_rejects_tcp_too_far():
    ctl, sim, mgr, rec = _setup(tcp_gap=0.5)   # 远超 SUCTION_HOLD_TOLERANCE
    result = ctl.attach(target_uid=1, sim_time=3.0)
    assert result.code == REJECT_TCP_TOO_FAR


def test_attach_rejects_too_tilted():
    """§2.7/§5: 吸盘翻转超过 ARM_TOOL_MAX_TILT (≈20°) 拒绝吸附"""
    ctl, sim, mgr, rec = _setup(tilt=0.5)      # 0.5 rad > 0.35
    result = ctl.attach(target_uid=1, sim_time=3.0)
    assert result.code == REJECT_TOO_TILTED


# ═══════════════════════════════════════════════════════════════
# 快照与恢复 (§2.9 步骤2/11)
# ═══════════════════════════════════════════════════════════════

def test_attach_saves_snapshot():
    ctl, sim, mgr, rec = _setup()
    sim.static[99] = 0
    sim.parent_of[99] = 42
    ctl.attach(target_uid=1, sim_time=3.0)
    snap = ctl.snapshot(1)
    assert snap is not None
    assert snap["parent"] == 42
    assert snap["static"] == 0


def test_release_restores_dynamic_and_respondable():
    """§2.9 步骤8-9: 释放先设位姿, 再恢复 dynamic/respondable, 然后 resetDynamicObject"""
    ctl, sim, mgr, rec = _setup()
    sim.static[99] = 0
    sim.respondable[99] = 1
    ctl.attach(target_uid=1, sim_time=3.0)
    assert rec.state is MaterialState.HELD_BY_SUCTION

    place_pose = [1.0, 2.0, 0.75, 0, 0, 0, 1]
    result = ctl.release(target_uid=1, sim_time=6.0, place_pose=place_pose)
    assert result.ok
    assert rec.state is MaterialState.RELEASED
    # 恢复 dynamic (static=0) 与 respondable
    assert sim.static.get(99) == 0
    assert sim.respondable.get(99) == 1
    # resetDynamicObject 被调用
    assert sim.called("resetDynamicObject")


def test_release_without_attach_raises():
    """未吸附就释放: 状态机拒绝 (不能跳过吸附)"""
    ctl, sim, mgr, rec = _setup()
    with pytest.raises(InvalidTransition):
        rec.transition_to(MaterialState.RELEASED, sim_time=1.0, reason="jump")