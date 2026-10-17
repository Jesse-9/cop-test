"""HYBRID_DYNAMIC 传送带输送与驱动测试 (§2.4 / §2.5).

覆盖: 有效区几何、按带速的受限补偿、到站判定、掉落故障、
内置 conveyor 的 `customData.__ctrl__` 控制接口。
"""

import json
import math

import pytest

from stage1 import config as cfg
from stage1.conveyor_controller import (
    BeltGeometry, ConveyorController, ConveyorError, TransportFault,
    compensation_allowed, configure_conveyor_model, transport_step,
)


# ── 几何 ──────────────────────────────────────────────────────

def _geometry():
    return BeltGeometry(cfg.CONVEYOR_ENTRY_WORLD, cfg.CONVEYOR_STOP_WORLD)


def test_progress_endpoints_and_midpoint():
    g = _geometry()
    assert math.isclose(g.progress(cfg.CONVEYOR_ENTRY_WORLD), 0.0, abs_tol=1e-9)
    assert math.isclose(g.progress(cfg.CONVEYOR_STOP_WORLD), 1.0, abs_tol=1e-9)
    mid = [(a + b) / 2 for a, b in zip(cfg.CONVEYOR_ENTRY_WORLD,
                                       cfg.CONVEYOR_STOP_WORLD)]
    assert math.isclose(g.progress(mid), 0.5, abs_tol=1e-9)


def test_progress_clamped_outside_segment():
    g = _geometry()
    before = (cfg.CONVEYOR_ENTRY_WORLD[0] - 1.0, 0.0, cfg.CONVEYOR_SURFACE_Z)
    after = (cfg.CONVEYOR_STOP_WORLD[0] + 1.0, 0.0, cfg.CONVEYOR_SURFACE_Z)
    assert g.progress(before) == 0.0
    assert g.progress(after) == 1.0


# ── 受限补偿 ──────────────────────────────────────────────────

def test_transport_step_is_bounded_by_belt_speed():
    g = _geometry()
    pos = list(cfg.CONVEYOR_ENTRY_WORLD)
    pos[2] = cfg.CONVEYOR_SURFACE_Z + cfg.MATERIAL_HALF_HEIGHT
    step = transport_step(pos, g, cfg.CONVEYOR_SPEED, cfg.SIM_STEP)
    max_delta = cfg.CONVEYOR_SPEED * cfg.SIM_STEP
    assert step.delta <= max_delta + 1e-9
    assert step.progress > 0.0
    assert step.new_position[:2] != pos[:2]


def test_transport_does_not_overshoot_stop():
    g = _geometry()
    pos = list(cfg.CONVEYOR_STOP_WORLD)
    pos[2] = cfg.CONVEYOR_SURFACE_Z + cfg.MATERIAL_HALF_HEIGHT
    # 已到站: 再走一步不应超过终点
    pos[0] -= 1e-6
    step = transport_step(pos, g, 1.0, cfg.SIM_STEP)
    assert step.new_position[0] <= cfg.CONVEYOR_STOP_WORLD[0] + 1e-9
    assert step.at_stop is True
    assert step.progress <= 1.0


def test_transport_preserves_lateral_and_height_from_physics():
    g = _geometry()
    pos = [cfg.CONVEYOR_ENTRY_WORLD[0], 0.012, cfg.CONVEYOR_SURFACE_Z + 0.049]
    step = transport_step(pos, g, cfg.CONVEYOR_SPEED, cfg.SIM_STEP)
    assert step.new_position[1] == pytest.approx(pos[1])
    assert step.new_position[2] == pytest.approx(pos[2])


def test_fault_when_material_falls_below_belt():
    pos = (cfg.CONVEYOR_ENTRY_WORLD[0], 0.0,
           cfg.CONVEYOR_SURFACE_Z - cfg.CONVEYOR_FAULT_DROP_Z - 0.01)
    with pytest.raises(TransportFault):
        compensation_allowed(pos, cfg.CONVEYOR_SURFACE_Z,
                             cfg.CONVEYOR_FAULT_DROP_Z)


def test_lateral_zone_violation_raises():
    g = _geometry()
    pos = (cfg.CONVEYOR_ENTRY_WORLD[0], 0.10,
           cfg.CONVEYOR_SURFACE_Z + cfg.MATERIAL_HALF_HEIGHT)
    with pytest.raises(TransportFault):
        compensation_allowed(pos, cfg.CONVEYOR_SURFACE_Z, geometry=g)


def test_compensation_allowed_when_material_in_valid_zone():
    pos = (cfg.CONVEYOR_ENTRY_WORLD[0], 0.0,
           cfg.CONVEYOR_SURFACE_Z + cfg.MATERIAL_HALF_HEIGHT)
    assert compensation_allowed(pos, cfg.CONVEYOR_SURFACE_Z,
                                cfg.CONVEYOR_FAULT_DROP_Z) is True


# ── 内置 conveyor 控制接口 ────────────────────────────────────

class FakeSim:
    def __init__(self):
        self.buffers = {}
        self.state = {"vel": 0.0}

    def setBufferProperty(self, handle, key, value):
        self.buffers[(handle, key)] = value

    def readCustomTableData(self, handle, key):
        return dict(self.state)


def _controller(sim=None):
    sim = sim or FakeSim()
    c = ConveyorController(sim)
    c.register("Conveyor_In", handle=7, entry=cfg.CONVEYOR_ENTRY_WORLD,
               stop=cfg.CONVEYOR_STOP_WORLD, surface_z=cfg.CONVEYOR_SURFACE_Z)
    return c, sim


def test_set_speed_writes_packed_ctrl_buffer():
    c, sim = _controller()
    c.set_speed("Conveyor_In", 0.2)
    key = ("Conveyor_In",)
    payload = sim.buffers[(7, cfg.CONVEYOR_CTRL_KEY)]
    if isinstance(payload, dict):
        assert payload["vel"] == pytest.approx(0.2)
    else:
        assert json.loads(payload)["vel"] == pytest.approx(0.2)


def test_read_actual_velocity_prefers_model_state():
    c, sim = _controller()
    sim.state = {"vel": 0.137}
    assert c.read_actual_velocity("Conveyor_In") == pytest.approx(0.137)


class FakeConfigSim:
    def __init__(self):
        self.config = {"accel": 0.05, "targetVel": 0.1, "length": 1.0}

    def readCustomTableData(self, handle, key):
        return dict(self.config) if key == "__config__" else {}

    def writeCustomTableData(self, handle, key, value):
        if key == "__config__":
            self.config = dict(value)


def test_configure_conveyor_model_sets_accel_and_target_vel():
    sim = FakeConfigSim()
    out = configure_conveyor_model(sim, 7, cfg.CONVEYOR_ACCEL,
                                   cfg.CONVEYOR_SPEED)
    assert out["accel"] == pytest.approx(cfg.CONVEYOR_ACCEL)
    assert out["targetVel"] == pytest.approx(cfg.CONVEYOR_SPEED)
    assert sim.config["length"] == 1.0


def test_unknown_conveyor_raises():
    c, _ = _controller()
    with pytest.raises(ConveyorError):
        c.set_speed("Nope", 0.1)


def test_stop_sets_zero_speed():
    c, sim = _controller()
    c.stop("Conveyor_In")
    payload = sim.buffers[(7, cfg.CONVEYOR_CTRL_KEY)]
    if isinstance(payload, dict):
        assert payload["vel"] == 0.0
    else:
        assert json.loads(payload)["vel"] == 0.0
