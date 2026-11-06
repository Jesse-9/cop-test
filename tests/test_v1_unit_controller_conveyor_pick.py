"""v1 控制器输送带取料接缝测试.

验证运动层与五岛层共用同一套停带/稳定流程, 只通过
``ConveyorPickContext`` 区分线别名、几何、带速、抓取区与传感器;
不启动 CoppeliaSim.
"""

from __future__ import annotations

import pytest

from flexible_factory.simulation import config as v1cfg
from flexible_factory.simulation import config_base as base
from flexible_factory.simulation.conveyor import BeltGeometry
from flexible_factory.simulation.layout import line_specs
from flexible_factory.simulation.material_manager import MaterialState
from flexible_factory.simulation.unit_controller import Stage4UnitController
from flexible_factory.simulation.unit_controller_base import (
    MismatchReason, UnitTaskState)
from flexible_factory.simulation.unit_controller_motion import (
    Stage2UnitController)


class _Material:
    def __init__(self, handle: int) -> None:
        self.sim_handle = handle
        self.state = MaterialState.ON_CONVEYOR


class _Materials:
    def __init__(self, handle: int, handles=None) -> None:
        self._material = _Material(handle)
        self._handles = ([handle] if handles is None else list(handles))

    def by_uid(self, uid: int):
        return self._material

    def handles(self):
        return list(self._handles)


class _Scene:
    def __init__(self, hits_by_alias) -> None:
        self._hits = {alias: set(handles)
                      for alias, handles in hits_by_alias.items()}

    def check_sensor(self, alias: str, handle: int):
        return handle if handle in self._hits.get(alias, set()) else None


def _stage2():
    controller = Stage2UnitController.__new__(Stage2UnitController)
    controller.belt_geometry = BeltGeometry((0.0, 0.0, 0.45),
                                            (1.0, 0.0, 0.45))
    return controller


def _stage4(line_id: str):
    controller = Stage4UnitController.__new__(Stage4UnitController)
    controller.line = line_specs()[line_id]
    controller.belt_geometry = controller.line.geometry
    return controller


def test_motion_layer_pick_context_uses_base_line():
    ctx = _stage2()._pick_context()
    assert ctx.conveyor_alias == base.ALIASES["conveyor"]
    assert ctx.belt_speed == base.CONVEYOR_SPEED
    assert ctx.pick_region is base.ALLOWED_PICK_REGION
    assert ctx.sensor_alias is None
    assert ctx.label == ""


def test_v1_pick_context_follows_its_own_line():
    line = line_specs()["line_s1b"]
    ctx = _stage4("line_s1b")._pick_context()
    assert ctx.conveyor_alias == line.conveyor
    assert ctx.belt_geometry.entry == pytest.approx(line.geometry.entry)
    assert ctx.belt_geometry.stop == pytest.approx(line.geometry.stop)
    assert ctx.belt_speed == v1cfg.LINE_SPEED
    assert ctx.pick_region.x == pytest.approx(line.pick_region.x)
    assert ctx.pick_region.y == pytest.approx(line.pick_region.y)
    assert ctx.sensor_alias == line.sensor_alias
    assert ctx.label == f"{line.line_id} "


def test_v1_pick_sensor_reads_only_its_line():
    line = line_specs()["line_s1b"]
    controller = _stage4("line_s1b")
    controller.source = "conveyor"
    controller.state = UnitTaskState.WAITING_FOR_ITEM
    controller.uid = 1
    controller.sim_time = 1.0
    controller.materials = _Materials(handle=100)
    controller.scene = _Scene({
        line.sensor_alias: {100},
        line_specs()["line_s1a"].sensor_alias: {200},
    })
    controller.last_mismatch = None
    controller._mismatch_logged = False
    controller._mismatch_since = None
    controller.logger = None
    stops: list[str] = []
    controller._command_pick_stop = stops.append

    controller._read_pick_sensor()

    assert stops == ["sensor_hit"]
    assert controller.last_mismatch is None


def test_v1_ignores_hits_on_other_lines():
    line = line_specs()["line_s1b"]
    controller = _stage4("line_s1b")
    controller.source = "conveyor"
    controller.state = UnitTaskState.WAITING_FOR_ITEM
    controller.uid = 1
    controller.sim_time = 1.0
    controller.materials = _Materials(handle=100)
    controller.scene = _Scene({
        line_specs()["line_s1a"].sensor_alias: {100},
    })
    controller.last_mismatch = None
    controller._mismatch_logged = False
    controller._mismatch_since = None
    controller.logger = None
    stops: list[str] = []
    controller._command_pick_stop = stops.append

    controller._read_pick_sensor()

    assert stops == []
    assert controller.last_mismatch.reason is MismatchReason.NO_HIT


def test_v1_wrong_material_on_own_line_does_not_stop():
    line = line_specs()["line_s1b"]
    controller = _stage4("line_s1b")
    controller.source = "conveyor"
    controller.state = UnitTaskState.WAITING_FOR_ITEM
    controller.uid = 1
    controller.sim_time = 1.0
    controller.materials = _Materials(handle=100, handles=[100, 200])
    controller.scene = _Scene({line.sensor_alias: {200}})
    controller.last_mismatch = None
    controller._mismatch_logged = False
    controller._mismatch_since = None
    controller.logger = None
    stops: list[str] = []
    controller._command_pick_stop = stops.append

    controller._read_pick_sensor()

    assert stops == []
    assert controller.last_mismatch.reason is MismatchReason.UID_MISMATCH
    assert controller.last_mismatch.hit_handle == 200
