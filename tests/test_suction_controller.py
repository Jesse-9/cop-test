"""逻辑吸附/释放测试 (§2.9).

覆盖: UID/状态/传感器/距离/倾角/速度前置检查、属性快照与恢复、
释放稳定性与穿透判定。使用 fake sim, 不需要 CoppeliaSim。
"""

import math

import pytest

from stage1 import config as cfg
from stage1.material_manager import MaterialManager, MaterialState
from stage1.suction_controller import (
    ATTACH_OK, REJECT_MOVING, REJECT_NO_SENSOR_HIT, REJECT_STATE,
    REJECT_TCP_TOO_FAR, REJECT_TOO_TILTED, REJECT_UID_MISMATCH,
    REJECT_TOOL_MOVING,
    AttachResult, SuctionController,
)


MAT = 100
TCP = 101
SENSOR = 102
LINK = 103
PAD = 104
PART_ROOT = 1


class FakeSim:
    def __init__(self):
        self.calls = []
        self.mat_pos = [0.0, 0.0, cfg.MATERIAL_PICK_POS[2]]
        self.tcp_pos = [0.0, 0.0, self.mat_pos[2] + cfg.MATERIAL_HALF_HEIGHT]
        self.tcp_quat = [0.0, 0.0, 0.0, 1.0]
        # 正确安装: TCP +Z 与 pad +Z 同向; 专用 sensor +Z 与 pad -Z 同向
        self.pad_quat = [0.0, 0.0, 0.0, 1.0]
        self.sensor_quat = [1.0, 0.0, 0.0, 0.0]
        self.sensor_hit = MAT
        self.material_speed = 0.0
        self.tool_speed = 0.0
        self.release_speed = 0.0
        self.static = 0
        self.respondable = 1
        self.mass = 0.1
        self.friction = 0.6
        self.mask = 0xFFFF
        self.parent = PART_ROOT
        self.pose = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0]

    # --- 位置/姿态 ---
    def getObjectPosition(self, handle, rel=None):
        if handle == MAT:
            return list(self.mat_pos)
        if handle == TCP:
            return list(self.tcp_pos)
        raise KeyError(handle)

    def getObjectQuaternion(self, handle, rel=None):
        if handle == PAD:
            return list(self.pad_quat)
        if handle == SENSOR:
            return list(self.sensor_quat)
        return list(self.tcp_quat)

    def getObjectPose(self, handle, rel=None):
        return list(self.pose)

    def setObjectPose(self, handle, pose, rel=None):
        self.calls.append(("setObjectPose", handle, list(pose)))

    def setObjectPosition(self, handle, pos, rel=None):
        self.calls.append(("setObjectPosition", handle, list(pos)))

    # --- 层级 ---
    def getObjectParent(self, handle):
        return self.parent

    def setObjectParent(self, handle, parent, keep=True):
        self.calls.append(("setObjectParent", handle, parent))
        self.parent = parent

    # --- 物理属性 ---
    def getObjectInt32Param(self, handle, param):
        if param == 0:
            return self.static
        if param == 1:
            return self.respondable
        if param == 2:
            return self.mask
        return 0

    def setObjectInt32Param(self, handle, param, value):
        self.calls.append(("setObjectInt32Param", handle, param, value))
        if param == 0:
            self.static = value
        elif param == 1:
            self.respondable = value
        elif param == 2:
            self.mask = value

    def getObjectFloatParam(self, handle, param):
        return self.mass

    def setObjectFloatParam(self, handle, param, value):
        self.calls.append(("setObjectFloatParam", handle, param, value))
        self.mass = value

    def setFloatProperty(self, handle, name, value):
        self.calls.append(("setFloatProperty", handle, name, value))
        self.friction = value

    def getFloatProperty(self, handle, name):
        return self.friction

    def setIntProperty(self, handle, name, value):
        self.calls.append(("setIntProperty", handle, name, value))

    def resetDynamicObject(self, handle):
        self.calls.append(("resetDynamicObject", handle))

    # --- 传感器/速度 ---
    def checkProximitySensor(self, sensor, entity):
        if self.sensor_hit is None:
            return 0, 1.0, [0, 0, 0], -1, [0, 0, 1]
        return 1, 0.005, [0, 0, 0], self.sensor_hit, [0, 0, 1]

    def getObjectVelocity(self, handle):
        if handle == TCP:
            return [self.tool_speed, 0.0, 0.0], [0.0, 0.0, 0.0]
        speed = self.release_speed if self.calls and any(
            c[0] == "resetDynamicObject" for c in self.calls) else self.material_speed
        return [speed, 0.0, 0.0], [0.0, 0.0, 0.0]

    # --- 常量 (与 4.10 数值一致) ---
    shapeintparam_static = 0
    shapeintparam_respondable = 1
    shapeintparam_respondable_mask = 2
    shapefloatparam_mass = 10
    handle_all = -1
    handle_world = -1


def _setup(state=MaterialState.READY_FOR_PICK):
    sim = FakeSim()
    mgr = MaterialManager()
    mgr.register(1, "Part_0001", sim_handle=MAT)
    if state is MaterialState.ON_CONVEYOR:
        mgr.transition(1, MaterialState.ON_CONVEYOR, 0.1, "belt")
        mgr.transition(1, MaterialState.READY_FOR_PICK, 0.2, "pick")
        mgr.transition(1, MaterialState.HELD_BY_SUCTION, 0.3, "held")
        mgr.transition(1, MaterialState.ON_TRAY, 0.4, "tray")
        mgr.transition(1, MaterialState.RELEASED, 0.5, "released")
    elif state is MaterialState.READY_FOR_PICK:
        mgr.transition(1, MaterialState.ON_CONVEYOR, 0.1, "belt")
        mgr.transition(1, MaterialState.READY_FOR_PICK, 0.2, "pick")
    c = SuctionController(sim, mgr, tcp_handle=TCP, sensor_handle=SENSOR,
                          link_handle=LINK, release_parent=PART_ROOT,
                          pad_handle=PAD)
    return sim, mgr, c


def test_attach_success_saves_snapshot_and_disables_dynamics():
    sim, mgr, c = _setup()
    result = c.attach(1, sim_time=1.0)
    assert result.ok and result.code == ATTACH_OK
    assert mgr.by_uid(1).state is MaterialState.HELD_BY_SUCTION
    snap = c.snapshot(1)
    assert snap["parent"] == PART_ROOT
    assert snap["static"] == 0
    assert snap["respondable"] == 1
    assert snap["mass"] == pytest.approx(0.1)
    assert ("setObjectParent", MAT, TCP) in sim.calls
    assert sim.static == 1, "吸附期间必须关闭物料动态响应"


def test_attach_rejects_wrong_state():
    sim, mgr, c = _setup(state=MaterialState.ON_CONVEYOR)
    r = c.attach(1, 1.0)
    assert r.code == REJECT_STATE


def test_attach_rejects_sensor_no_hit():
    sim, mgr, c = _setup()
    sim.sensor_hit = None
    r = c.attach(1, 1.0)
    assert r.code == REJECT_NO_SENSOR_HIT
    assert mgr.by_uid(1).state is MaterialState.READY_FOR_PICK


def test_attach_rejects_uid_mismatch():
    sim, mgr, c = _setup()
    sim.sensor_hit = 999
    r = c.attach(1, 1.0)
    assert r.code == REJECT_UID_MISMATCH


def test_attach_rejects_moving_tool():
    sim, mgr, c = _setup()
    sim.tool_speed = cfg.SUCTION_MAX_TOOL_SPEED + 0.1
    r = c.attach(1, 1.0)
    assert r.code == REJECT_TOOL_MOVING
    assert mgr.by_uid(1).state is MaterialState.READY_FOR_PICK


def test_attach_rejects_tcp_too_far():
    sim, mgr, c = _setup()
    sim.tcp_pos = [0.0, 0.0, sim.mat_pos[2] + 0.5]
    r = c.attach(1, 1.0)
    assert r.code == REJECT_TCP_TOO_FAR


def test_attach_rejects_tilted_tool():
    sim, mgr, c = _setup()
    # 整个工具绕 X 轴 90°: TCP/pad/sensor 相对关系不变, 但真实法向水平
    q90 = [math.sin(math.pi / 4), 0.0, 0.0, math.cos(math.pi / 4)]
    q270 = [math.sin(3 * math.pi / 4), 0.0, 0.0,
            math.cos(3 * math.pi / 4)]
    sim.tcp_quat = q90
    sim.pad_quat = q90
    sim.sensor_quat = q270
    r = c.attach(1, 1.0)
    assert r.code == REJECT_TOO_TILTED


def test_attach_rejects_moving_material():
    sim, mgr, c = _setup()
    sim.material_speed = cfg.SUCTION_MAX_ATTACH_SPEED + 0.1
    r = c.attach(1, 1.0)
    assert r.code == REJECT_MOVING


def test_release_restores_physics_and_state():
    sim, mgr, c = _setup()
    c.attach(1, 1.0)
    target = [*cfg.STATION_MATERIAL_POSE, 0.0, 0.0, 0.0, 1.0]
    r = c.release(1, 2.0, target)
    assert r.ok and r.stable
    assert mgr.by_uid(1).state is MaterialState.RELEASED
    assert sim.static == 0, "释放后必须恢复动态响应"
    assert sim.respondable == 1
    assert ("resetDynamicObject", MAT) in sim.calls
    assert ("setObjectParent", MAT, PART_ROOT) in sim.calls
    assert ("setObjectPose", MAT, target) in sim.calls


def test_release_reports_unstable_without_claiming_success():
    sim, mgr, c = _setup()
    c.attach(1, 1.0)
    sim.release_speed = cfg.SUCTION_RELEASE_MAX_SPEED + 0.2
    r = c.release(1, 2.0, [*cfg.STATION_MATERIAL_POSE, 0, 0, 0, 1])
    assert r.ok is True
    assert r.stable is False, "速度超过阈值必须明确报告不稳定"


def test_release_reports_penetration():
    sim, mgr, c = _setup()
    c.attach(1, 1.0)
    # 物料被放到台面以下
    target = [cfg.STATION_MATERIAL_POSE[0], cfg.STATION_MATERIAL_POSE[1],
              cfg.STATION_TOP_Z - 0.05, 0, 0, 0, 1]
    r = c.release(1, 2.0, target)
    assert r.penetration is True


def test_release_requires_held_state():
    sim, mgr, c = _setup()
    with pytest.raises(Exception):
        c.release(1, 2.0, [0, 0, 0, 0, 0, 0, 1])
