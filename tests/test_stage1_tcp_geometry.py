"""tests/test_stage1_tcp_geometry.py — suction pad 安装几何回归.

覆盖 SUCTION_PAD_MOUNT_REVIEW.md 步骤 9 要求的几何关系:
  - Rx(π) 安装把 pad 接触法向 (-Z) 映射到法兰外向 (+Z)
  - TCP/pad/sensor 轴向一致时接受, 反装/局部四元数错误/传感器反向时拒绝
  - 吸附在几何检查失败前不修改物料物理状态

只做矩阵组合与轴向关系断言, 不断言某个固定的安装距离常量。
"""

import math

import pytest

from stage1 import config as cfg
from stage1.material_manager import MaterialManager, MaterialState
from stage1.scene import _quat_matrix, _quat_equivalent
from stage1.suction_controller import (
    REJECT_TOOL_GEOMETRY,
    REJECT_TOO_TILTED,
    SuctionController,
)


MAT = 100
TCP = 101
SENSOR = 102
PAD = 104
PART_ROOT = 1

Q_ID = (0.0, 0.0, 0.0, 1.0)
Q_RX_PI = (1.0, 0.0, 0.0, 0.0)          # 绕 X 轴 180°
Q_RX_90 = (math.sin(math.pi / 4), 0.0, 0.0, math.cos(math.pi / 4))


def _rotate(q, v):
    r = _quat_matrix(q)
    return tuple(sum(r[i][j] * v[j] for j in range(3)) for i in range(3))


class GeomSim:
    """只提供几何检查需要的读数与常量。"""

    handle_world = -1
    shapeintparam_static = 0
    shapeintparam_respondable = 1
    shapeintparam_respondable_mask = 2
    shapefloatparam_mass = 10

    def __init__(self, pad_q=Q_ID, tcp_q=Q_ID, sensor_q=Q_RX_PI):
        self.quats = {PAD: pad_q, TCP: tcp_q, SENSOR: sensor_q}
        self.parent_calls = []
        self.static_writes = []
        self.mat_pos = [0.0, 0.0, cfg.MATERIAL_PICK_POS[2]]
        self.tcp_pos = [0.0, 0.0,
                        self.mat_pos[2] + cfg.MATERIAL_HALF_HEIGHT]

    def getObjectQuaternion(self, handle, ref=None):
        return list(self.quats[handle])

    def getObjectPosition(self, handle, ref=None):
        if handle == MAT:
            return list(self.mat_pos)
        if handle == TCP:
            return list(self.tcp_pos)
        raise KeyError(handle)

    def checkProximitySensor(self, sensor, entity):
        return 1, 0.0, [0, 0, 0], MAT, [0, 0, 1]

    def getObjectVelocity(self, handle):
        return [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]

    def setObjectParent(self, handle, parent, keep=True):
        self.parent_calls.append((handle, parent))

    def setObjectInt32Param(self, handle, param, value):
        self.static_writes.append((handle, param, value))


def _controller(sim):
    mgr = MaterialManager()
    mgr.register(1, "Part_0001", sim_handle=MAT)
    mgr.transition(1, MaterialState.ON_CONVEYOR, 0.1, "belt")
    mgr.transition(1, MaterialState.READY_FOR_PICK, 0.2, "pick")
    return mgr, SuctionController(
        sim, mgr, tcp_handle=TCP, sensor_handle=SENSOR,
        release_parent=PART_ROOT, pad_handle=PAD)


# ═══════════════════════════════════════════════════════════════
# 安装旋转与轴向关系
# ═══════════════════════════════════════════════════════════════

def test_rx_pi_maps_contact_normal_to_flange_outward():
    # pad 接触面在本地 -Z; Rx(π) 后应指向 connection +Z
    assert _rotate(Q_RX_PI, (0.0, 0.0, -1.0)) == pytest.approx(
        (0.0, 0.0, 1.0))
    assert _rotate(Q_RX_PI, (0.0, 0.0, 1.0)) == pytest.approx(
        (0.0, 0.0, -1.0))


def test_quat_equivalent_accepts_negated_quaternion():
    assert _quat_equivalent(Q_RX_PI, tuple(-v for v in Q_RX_PI))
    assert not _quat_equivalent(Q_RX_PI, Q_ID)


def test_geometry_accepts_correct_tool():
    _, c = _controller(GeomSim())
    assert c._tool_geometry_error() is None
    assert c._tool_tilt() == pytest.approx(0.0, abs=1e-6)


def test_geometry_rejects_tcp_local_rotation_error():
    # pad 正确, 但 TCP 局部四元数被写成 Rx(π)
    _, c = _controller(GeomSim(tcp_q=Q_RX_PI, sensor_q=Q_ID))
    assert "TCP" in c._tool_geometry_error()


def test_geometry_rejects_reversed_sensor():
    # sensor 仍是 TCP 的 +Z 同向, 与 pad -Z 相反
    _, c = _controller(GeomSim(sensor_q=Q_ID))
    assert "sensor" in c._tool_geometry_error()


def test_reversed_pad_with_aligned_tcp_rejects_by_real_normal():
    # 反装 pad, 但 TCP/pad/sensor 相对关系一致: 几何关系通过, 倾角必须拒绝
    _, c = _controller(GeomSim(
        pad_q=Q_RX_PI, tcp_q=Q_RX_PI, sensor_q=Q_ID))
    assert c._tool_geometry_error() is None
    assert c._tool_tilt() == pytest.approx(math.pi)


def test_horizontal_tool_rejects_by_tilt():
    _, c = _controller(GeomSim(
        pad_q=Q_RX_90, tcp_q=Q_RX_90,
        sensor_q=(math.sin(3 * math.pi / 4), 0.0, 0.0,
                  math.cos(3 * math.pi / 4))))
    assert c._tool_geometry_error() is None
    assert c._tool_tilt() == pytest.approx(math.pi / 2)


# ═══════════════════════════════════════════════════════════════
# 吸附拒绝前不得写物理状态
# ═══════════════════════════════════════════════════════════════

def test_attach_rejects_geometry_before_physics_mutation():
    sim = GeomSim(tcp_q=Q_RX_PI, sensor_q=Q_ID)
    mgr, c = _controller(sim)
    result = c.attach(1, sim_time=1.0)
    assert result.code == REJECT_TOOL_GEOMETRY
    assert mgr.by_uid(1).state is MaterialState.READY_FOR_PICK
    assert sim.parent_calls == []
    assert sim.static_writes == []


def test_attach_rejects_reversed_pad_by_tilt():
    sim = GeomSim(pad_q=Q_RX_PI, tcp_q=Q_RX_PI, sensor_q=Q_ID)
    mgr, c = _controller(sim)
    result = c.attach(1, sim_time=1.0)
    assert result.code == REJECT_TOO_TILTED
    assert mgr.by_uid(1).state is MaterialState.READY_FOR_PICK
    assert sim.parent_calls == []
    assert sim.static_writes == []