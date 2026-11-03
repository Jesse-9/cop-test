"""v1 双站层布局与单工位适配器测试."""

import math

from flexible_factory.simulation import config_base as base
from flexible_factory.simulation import config_dual as cfg
from flexible_factory.simulation.layout_dual import (L2_MIRROR, L3_SAME_SIDE, Stage3Layout,
                           UnitLayoutView, default_layout, layout_from_dict,
                           quat_from_yaw, screen_candidates, validate_layout,
                           yaw_from_quat)


def test_default_layout_has_two_mirrored_units():
    layout = default_layout()
    assert layout.name == cfg.STAGE3_LAYOUT_NAME
    a, b = layout.unit("A"), layout.unit("B")
    assert a.arm_base == (-0.20, 0.55, base.ISLAND_TOP_Z)
    assert b.arm_base == (-0.20, -0.55, base.ISLAND_TOP_Z)
    assert a.station_pos == (-0.20, 1.20)
    assert b.station_pos == (-0.20, -1.20)
    assert a.dock == cfg.AGV_DOCK_A
    assert b.dock == cfg.AGV_DOCK_B
    assert math.isclose(layout.agv_initial_yaw, -math.pi / 2)


def test_default_layout_static_validation_passes():
    report = validate_layout(default_layout())
    assert report["ok"], [c for c in report["checks"] if not c["ok"]]
    names = {c["check"] for c in report["checks"]}
    for required in ("arm_reach:A:pick", "arm_reach:B:pick",
                     "arm_reach:A:agv", "arm_reach:B:agv",
                     "agv_route_vs_island:A", "agv_route_vs_station:B",
                     "pick_zone_shared", "agv_initial_heading"):
        assert required in names


def test_same_side_candidate_is_rejected_statically():
    report = validate_layout(L3_SAME_SIDE)
    assert not report["ok"]
    failed = {c["check"] for c in report["checks"] if not c["ok"]}
    assert "arm_reach:B:pick" in failed
    screen = screen_candidates()
    assert screen["selected"] == cfg.STAGE3_LAYOUT_NAME
    rows = {row["name"]: row for row in screen["candidates"]}
    assert rows[cfg.STAGE3_LAYOUT_NAME]["ok"] is True
    assert rows["L3_SAME_SIDE"]["ok"] is False


def test_agv_route_rect_does_not_overlap_stations_or_islands():
    layout = default_layout()
    route = layout.agv_route_rect
    for spec in layout.units:
        for rect in (spec.island_rect, spec.station_rect):
            assert (route[2] <= rect[0] or rect[2] <= route[0]
                    or route[3] <= rect[1] or rect[3] <= route[1])
    assert route[2] - route[0] > 0.4


def test_unit_layout_view_targets_and_support():
    layout = default_layout()
    a = layout.unit_view("A")
    b = layout.unit_view("B")
    assert isinstance(a, UnitLayoutView)
    assert a.name.endswith("/A") and b.name.endswith("/B")
    assert a.tcp_pose_for_target("station")[0] == -0.20
    assert b.tcp_pose_for_target("station")[1] == -1.20
    assert a.material_pose_for_target("agv")[1] == cfg.AGV_DOCK_A[1]
    assert b.material_pose_for_target("agv")[1] == cfg.AGV_DOCK_B[1]
    assert a.station_top_z == base.STATION_TOP_Z


def test_layout_roundtrip_and_digest_payload():
    layout = default_layout()
    payload = layout.as_dict()
    restored = layout_from_dict(payload)
    assert restored.as_dict() == payload
    assert restored.unit("B").dock == cfg.AGV_DOCK_B
    assert isinstance(layout, Stage3Layout)


def test_yaw_quaternion_roundtrip():
    for yaw in (-math.pi / 2, 0.0, 0.3, math.pi):
        q = quat_from_yaw(yaw)
        got = yaw_from_quat(q)
        assert abs((got - yaw + math.pi) % (2 * math.pi) - math.pi) < 1e-9


def test_dock_rect_uses_rotated_agv_footprint():
    layout = default_layout()
    rect = layout.unit("A").dock_rect
    width = rect[2] - rect[0]
    height = rect[3] - rect[1]
    assert math.isclose(width, base.AGV_FOOTPRINT_SIZE[1], abs_tol=1e-9)
    assert math.isclose(height, base.AGV_FOOTPRINT_SIZE[0], abs_tol=1e-9)
