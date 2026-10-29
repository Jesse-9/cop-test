"""阶段四配置/布局纯逻辑测试 (不需要 CoppeliaSim)."""

from __future__ import annotations

import math

from stage1 import config as base
from stage4 import config as cfg
from stage4.agv_map import make_route_spec, route_map
from stage4.layout import (UnitSpec4, arm_adjacency_pairs, default_layout,
                           layout_from_dict, line_specs, rect_clearance,
                           validate_layout, zones_for_unit)


def test_aliases_are_flat_and_unique():
    aliases = list(cfg.REQUIRED_ALIASES)
    assert len(aliases) == len(set(aliases))
    assert all("/" not in alias for alias in aliases)


def test_five_units_and_four_lines():
    layout = default_layout()
    assert layout.name == cfg.STAGE4_LAYOUT_NAME == "L5_UFLOW"
    assert layout.unit_ids == ("A", "B", "C", "D", "E")
    assert set(layout.lines) == set(cfg.LINE_IDS)
    assert set(layout.dock_positions) == {"A", "B", "D", "E"}
    # A/B 保持阶段三锚点; D/E 移到东侧, dock 不再共线
    assert layout.dock_positions["A"] == (0.40, 0.70)
    assert layout.dock_positions["B"] == (0.40, -0.70)
    assert layout.dock_positions["D"] == (2.90, -1.35)
    assert layout.dock_positions["E"] == (2.90, 1.35)
    assert len({pos[0] for pos in layout.dock_positions.values()}) == 2


def test_line_geometry_matches_entry_stop_rules():
    lines = line_specs()
    for line in lines.values():
        assert base.CONVEYOR_TOP_RUN_LOCAL[0] <= line.entry_local_x
        assert line.stop_local_x <= base.CONVEYOR_TOP_RUN_LOCAL[1]
        assert abs(line.entry[2] - base.CONVEYOR_SURFACE_Z) < 1e-9
        assert abs(line.stop[2] - base.CONVEYOR_SURFACE_Z) < 1e-9
    # Y 向线: 停止区在入口 +Y 方向
    assert lines["line_s1b"].stop[1] > lines["line_s1b"].entry[1]
    # X 向线 (阶段 4.1 固定物流沿 +X)
    assert lines["line_s2d"].stop[0] > lines["line_s2d"].entry[0]
    assert lines["line_ce"].stop[0] > lines["line_ce"].entry[0]


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
    assert "agv_docks_not_collinear" in names
    assert "height_datum_base" in names


def test_adjacency_pairs_match_distance_rule():
    pairs = {frozenset(pair) for pair in arm_adjacency_pairs(default_layout())}
    assert frozenset({"A", "B"}) in pairs
    assert frozenset({"A", "C"}) in pairs
    assert frozenset({"C", "E"}) not in pairs
    assert frozenset({"D", "A"}) not in pairs
    assert frozenset({"B", "C"}) not in pairs


def test_zones_for_unit_follow_resource_order():
    assert zones_for_unit("A") == ["zone_ab", "zone_ac"]
    assert zones_for_unit("B") == ["zone_ab"]
    assert zones_for_unit("C") == ["zone_ac"]
    assert zones_for_unit("D") == []
    assert zones_for_unit("E") == []


def test_loaded_routes_are_single_straight_legs():
    layout = default_layout()
    ba = make_route_spec(layout, "B", "A", True)
    assert ba.direction == -1                     # +Y 倒车
    assert len(ba.straight_legs) == 1 and not ba.turn_legs
    assert ba.lanes == ("agv_lane_west",)
    de = make_route_spec(layout, "D", "E", True)
    assert de.direction == -1
    assert len(de.straight_legs) == 1 and not de.turn_legs
    assert de.lanes == ("agv_lane_east",)


def test_empty_reposition_uses_turns_and_three_lanes():
    layout = default_layout()
    for start, end in (("A", "D"), ("D", "B"), ("B", "E"), ("E", "B")):
        spec = make_route_spec(layout, start, end, False)
        assert len(spec.turn_legs) == 2, (start, end, spec.as_dict())
        assert set(spec.lanes) == set(cfg.AGV_LANES), (start, end)
        assert spec.legs[-1].kind == "straight"
        assert abs(spec.legs[-1].heading - cfg.AGV_INITIAL_YAW) < 1e-9


def test_route_map_contains_network_and_all_pairs():
    layout = default_layout()
    data = route_map(layout)
    assert set(data["nodes"]) == set(layout.agv_nodes)
    assert {edge["lane"] for edge in data["edges"]} == set(cfg.AGV_LANES)
    assert data["turn_nodes"] == list(layout.agv_turn_nodes)
    assert len(data["routes"]) == 12


def test_layout_roundtrip():
    layout = default_layout()
    payload = layout.as_dict()
    restored = layout_from_dict(payload)
    assert restored.name == layout.name
    assert restored.unit_ids == layout.unit_ids
    assert restored.dock_positions == layout.dock_positions
    for spec in restored.units:
        assert abs(spec.arm_base[2] - cfg.Z_BASE) < 1e-9
    assert restored.agv_nodes == layout.agv_nodes
    assert [(edge.start, edge.end, edge.lane)
            for edge in restored.agv_edges] == \
        [(edge.start, edge.end, edge.lane) for edge in layout.agv_edges]
    assert restored.height_datum["Z_base"] == cfg.Z_BASE
    for line_id in cfg.LINE_IDS:
        assert abs(restored.lines[line_id].yaw
                   - layout.lines[line_id].yaw) < 1e-5
        assert math.dist(restored.lines[line_id].stop,
                         layout.lines[line_id].stop) < 1e-4


def test_turn_nodes_order_is_stable_after_json_roundtrip():
    """JSON 往返会改变 dict 键顺序; turn_nodes 必须按配置顺序输出."""
    layout = default_layout()
    payload = layout.as_dict()
    nodes = payload["agv_network"]["nodes"]
    payload["agv_network"]["nodes"] = dict(reversed(list(nodes.items())))
    restored = layout_from_dict(payload)
    assert restored.agv_turn_nodes == ("J_SW", "J_SE")
    assert restored.as_dict()["agv_network"]["turn_nodes"] == [
        "J_SW", "J_SE"]


def test_rect_clearance_sign():
    a = (0.0, 0.0, 1.0, 1.0)
    assert rect_clearance(a, (1.1, 0.0, 2.0, 1.0)) > 0.0
    assert rect_clearance(a, (0.5, 0.5, 1.5, 1.5)) < 0.0


def test_unit_spec_targets_within_reach():
    layout = default_layout()
    for spec in layout.units:
        assert isinstance(spec, UnitSpec4)
        for name, pose in spec.targets(layout.lines).items():
            distance = math.dist(spec.arm_base, pose)
            assert distance <= base.REACH_LIMIT, (spec.unit_id, name, distance)


def test_e_output_table_is_outside_arm_and_lane_envelopes():
    layout = default_layout()
    assert layout.e_output == cfg.E_OUTPUT["position"] == (3.50, 1.35)
    table = layout.e_output_table_rect
    assert all(abs(table[i] - v) <= 1e-9
               for i, v in enumerate((3.35, 1.25, 3.65, 1.45)))
    # 输出台在东直道扫掠矩形之外
    lane = layout.agv_lane_rects["D->E"]
    assert rect_clearance(lane, table) >= 0.02
    # 输出台在 E 臂可达范围之外, 不会被当作可抓取目标
    distance = math.dist(layout.unit("E").arm_base,
                         (layout.e_output[0], layout.e_output[1],
                          cfg.E_OUTPUT["material_z"]))
    assert distance > base.REACH_LIMIT


def test_height_datum_is_explicit_and_unified():
    layout = default_layout()
    datum = layout.height_datum
    assert datum["Z_base"] == cfg.Z_BASE == 0.30
    assert datum["Z_belt"] == base.CONVEYOR_SURFACE_Z
    assert datum["H_transfer"] == cfg.H_TRANSFER
    assert set(datum["Z_work"]) == set(cfg.UNIT_IDS)
    assert datum["exceptions"] == []
    for spec in layout.units:
        assert spec.island_top_z == cfg.Z_BASE
        assert spec.arm_base[2] == cfg.Z_BASE


def test_task_resources_cover_declared_order():
    from stage4.coordinator import TASK_RESOURCES
    order = list(cfg.RESOURCE_ORDER)
    for kind, resources in TASK_RESOURCES.items():
        assert list(resources) == sorted(resources, key=order.index), kind
        assert all(item in order for item in resources), kind
    assert "zone_ce" not in order


def test_task_layout_returns_full_pose_for_release():
    from types import SimpleNamespace
    from stage4.unit_controller import SinkSpec, TaskLayout
    material = (0.0, 1.0, 0.35)
    sink = SinkSpec(
        kind="station", tcp_pose=tuple([0.0, 1.0, 0.40, 0.0, 0.0, 0.0, 1.0]),
        material_pose=material, support_z=0.30, zone="handoff_ac")
    view = SimpleNamespace(arm_base=(0.0, 0.0, 0.30))
    layout = TaskLayout("L5_UFLOW/A", sink, view, "station")
    assert len(layout.material_pose_for_target("station")) == 7
    assert len(layout.tcp_pose_for_target("station")) == 7
    assert layout.material_pose_for_target("station")[:3] == list(material)
    assert layout.station_top_z == 0.30


def test_agv_place_pose_follows_measured_tray():
    """AGV 停靠有厘米级偏差时, 放置点必须跟随实测托盘中心."""
    from stage1 import config as base
    from stage4.unit_controller import agv_place_pose
    nominal = [2.90, -1.35, 0.357, 0.0, 0.0, 0.0, 1.0]
    tray = [2.896, -1.317, 0.247, 0.0, 0.0, -0.7066, 0.7076]
    pose = agv_place_pose(nominal, tray)
    assert pose[:2] == [2.896, -1.317]
    expected_z = (0.247 + base.AGV_TRAY_THICKNESS / 2.0
                  + 2.0 * base.MATERIAL_HALF_HEIGHT)
    assert abs(pose[2] - expected_z) < 1e-9
    assert pose[3:] == nominal[3:]


def test_agv_material_pose_matches_place_pose():
    """释放目标与放置点同源: 物料中心 = 放置 TCP - 物料半高."""
    from stage1 import config as base
    from stage4.unit_controller import agv_material_pose, agv_place_pose
    nominal_tcp = [2.90, -1.35, 0.357, 0.0, 0.0, 0.0, 1.0]
    nominal_mat = [2.90, -1.35, 0.307, 0.0, 0.0, 0.0, 1.0]
    tray = [2.896, -1.317, 0.247, 0.0, 0.0, -0.7066, 0.7076]
    tcp = agv_place_pose(nominal_tcp, tray)
    mat = agv_material_pose(nominal_mat, tray)
    assert mat[:2] == tcp[:2] == [2.896, -1.317]
    assert abs((tcp[2] - mat[2]) - base.MATERIAL_HALF_HEIGHT) < 1e-9
