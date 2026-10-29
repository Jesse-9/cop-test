"""阶段四配置/布局纯逻辑测试 (不需要 CoppeliaSim)."""

from __future__ import annotations

import math

from stage1 import config as base
from stage4 import config as cfg
from stage4.layout import (L4_COLUMN, UnitSpec4, arm_adjacency_pairs,
                           default_layout, layout_from_dict, line_specs,
                           rect_clearance, route_specs, validate_layout,
                           zones_for_unit)


def test_aliases_are_flat_and_unique():
    aliases = list(cfg.REQUIRED_ALIASES)
    assert len(aliases) == len(set(aliases))
    assert all("/" not in alias for alias in aliases)


def test_five_units_and_four_lines():
    layout = default_layout()
    assert layout.name == cfg.STAGE4_LAYOUT_NAME
    assert layout.unit_ids == ("A", "B", "C", "D", "E")
    assert set(layout.lines) == set(cfg.LINE_IDS)
    assert set(layout.dock_positions) == {"A", "B", "D", "E"}
    assert layout.dock_positions["A"] == (0.40, 0.70)
    assert layout.dock_positions["B"] == (0.40, -0.70)
    assert layout.dock_positions["D"] == (0.40, -2.45)
    assert layout.dock_positions["E"] == (0.40, 2.45)


def test_line_geometry_matches_entry_stop_rules():
    lines = line_specs()
    for line in lines.values():
        assert base.CONVEYOR_TOP_RUN_LOCAL[0] <= line.entry_local_x
        assert line.stop_local_x <= base.CONVEYOR_TOP_RUN_LOCAL[1]
        assert abs(line.entry[2] - base.CONVEYOR_SURFACE_Z) < 1e-9
        assert abs(line.stop[2] - base.CONVEYOR_SURFACE_Z) < 1e-9
    # Y 向线: 停止区在入口 +Y 方向
    assert lines["line_s1b"].stop[1] > lines["line_s1b"].entry[1]
    assert lines["line_ce"].stop[1] > lines["line_ce"].entry[1]
    # X 向线
    assert lines["line_s2d"].stop[0] > lines["line_s2d"].entry[0]


def test_pick_regions_cover_stop_and_respect_direction():
    lines = line_specs()
    for line in lines.values():
        region = line.pick_region
        x, y, z = line.pick_material_pose
        assert region.x[0] <= x <= region.x[1]
        assert region.y[0] <= y <= region.y[1]
        assert abs(region.z_center - z) < 1e-9


def test_layout_static_validation_passes():
    report = validate_layout(default_layout())
    assert report["ok"], [c for c in report["checks"] if not c["ok"]]
    names = {c["check"] for c in report["checks"]}
    for unit_id in cfg.UNIT_IDS:
        assert f"arm_base_on_island:{unit_id}" in names
    for line_id in cfg.LINE_IDS:
        destination = line_specs()[line_id].destination
        assert f"line_reach:{line_id}:{destination}" in names


def test_adjacency_pairs_match_distance_rule():
    pairs = {frozenset(pair) for pair in arm_adjacency_pairs(default_layout())}
    assert frozenset({"A", "B"}) in pairs
    assert frozenset({"A", "C"}) in pairs
    assert frozenset({"C", "E"}) in pairs
    assert frozenset({"D", "A"}) not in pairs
    assert frozenset({"B", "C"}) not in pairs


def test_zones_for_unit_follow_resource_order():
    assert zones_for_unit("A") == ["zone_ab", "zone_ac"]
    assert zones_for_unit("B") == ["zone_ab"]
    assert zones_for_unit("C") == ["zone_ac", "zone_ce"]
    assert zones_for_unit("D") == []
    assert zones_for_unit("E") == ["zone_ce"]


def test_route_specs_direction_and_loading():
    specs = route_specs(default_layout())
    assert specs[("B", "A", True)].direction == -1     # +Y 倒车
    assert specs[("A", "B", True)].direction == 1      # -Y 前进
    assert specs[("D", "E", True)].direction == -1
    assert specs[("E", "D", True)].direction == 1
    assert specs[("D", "E", False)].loaded is False


def test_layout_roundtrip():
    layout = default_layout()
    payload = layout.as_dict()
    restored = layout_from_dict(payload)
    assert restored.name == layout.name
    assert restored.unit_ids == layout.unit_ids
    assert restored.dock_positions == layout.dock_positions
    assert restored.unit("C").island_top_z == 0.45
    assert restored.unit("C").arm_base[2] == 0.45
    for line_id in cfg.LINE_IDS:
        assert abs(restored.lines[line_id].yaw
                   - layout.lines[line_id].yaw) < 1e-5
        assert math.dist(restored.lines[line_id].stop,
                         layout.lines[line_id].stop) < 1e-4


def test_rect_clearance_sign():
    a = (0.0, 0.0, 1.0, 1.0)
    assert rect_clearance(a, (1.1, 0.0, 2.0, 1.0)) > 0.0
    assert rect_clearance(a, (0.5, 0.5, 1.5, 1.5)) < 0.0


def test_unit_spec_targets_within_reach():
    for spec in default_layout().units:
        assert isinstance(spec, UnitSpec4)
        for name, pose in spec.targets().items():
            distance = math.dist(spec.arm_base, pose)
            assert distance <= base.REACH_LIMIT, (spec.unit_id, name, distance)


def test_task_resources_cover_declared_order():
    from stage4.coordinator import TASK_RESOURCES
    order = list(cfg.RESOURCE_ORDER)
    for kind, resources in TASK_RESOURCES.items():
        assert list(resources) == sorted(resources, key=order.index), kind
        assert all(item in order for item in resources), kind


def test_task_layout_returns_full_pose_for_release():
    from types import SimpleNamespace
    from stage4.unit_controller import SinkSpec, TaskLayout
    material = (0.0, 1.0, 0.35)
    sink = SinkSpec(
        kind="station", tcp_pose=tuple([0.0, 1.0, 0.40, 0.0, 0.0, 0.0, 1.0]),
        material_pose=material, support_z=0.30, zone="handoff_ac")
    view = SimpleNamespace(arm_base=(0.0, 0.0, 0.30))
    layout = TaskLayout("L4_COLUMN/A", sink, view, "station")
    assert len(layout.material_pose_for_target("station")) == 7
    assert len(layout.tcp_pose_for_target("station")) == 7
    assert layout.material_pose_for_target("station")[:3] == list(material)
    assert layout.station_top_z == 0.30
