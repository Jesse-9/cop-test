"""tests/test_stage1_layout.py — LayoutSpec 派生坐标与几何校验.

纯逻辑, 不需要 CoppeliaSim。步骤 1.1 选定的 L1_S1 是当前生产布局;
其坐标和派生目标必须与场景/控制器的假设一致。
"""

import math

from stage1 import config as cfg
from stage1.layout import (
    L1_S1_LAYOUT,
    LayoutSpec,
    default_layout,
    overlap_rects,
    rect_of,
    validate_layout,
)


def test_default_layout_is_l1_s1():
    layout = default_layout()
    assert layout == L1_S1_LAYOUT
    assert layout.name == "L1_S1"
    assert layout.arm_base == (-0.20, 0.55, cfg.ISLAND_TOP_Z)
    assert layout.island_pos == (-0.20, 0.55)
    assert layout.station_pos == (-0.20, 1.20)
    assert layout.agv_dock == (0.40, 0.75)
    assert layout.conveyor_center == tuple(cfg.CONVEYOR_CENTER)
    assert layout.include_stopper is False
    assert layout.pick_sensor_local_x == cfg.CONVEYOR_STOP_LOCAL_X


def test_default_layout_derived_poses_use_l1_s1_coordinates():
    layout = default_layout()
    assert layout.pick_material_pose == tuple(cfg.MATERIAL_PICK_POS)
    assert layout.pick_tcp_pose == list(cfg.PICK_TCP_POSE)
    assert layout.station_material_pose[:2] == (-0.20, 1.20)
    assert layout.agv_material_pose[:2] == (0.40, 0.75)
    assert layout.tcp_pose_for_target("station") == layout.station_tcp_pose
    assert layout.tcp_pose_for_target("agv") == layout.agv_tcp_pose
    assert layout.material_pose_for_target("station") == [
        *layout.station_material_pose, 0.0, 0.0, 0.0, 1.0]


def test_historical_baseline_layout_derived_poses_match_config():
    layout = LayoutSpec(
        name="stage1_baseline",
        arm_base=tuple(cfg.ARM_BASE_POS),
        island_pos=tuple(cfg.ISLAND_POS),
        station_pos=tuple(cfg.STATION_POS),
        agv_dock=tuple(cfg.AGV_DOCK_POS),
        include_stopper=True,
    )
    assert layout.pick_material_pose == tuple(cfg.MATERIAL_PICK_POS)
    assert layout.pick_tcp_pose == list(cfg.PICK_TCP_POSE)
    assert layout.station_material_pose == tuple(cfg.STATION_MATERIAL_POSE)
    assert layout.station_tcp_pose == list(cfg.STATION_TCP_POSE)
    assert layout.agv_material_pose == tuple(cfg.AGV_MATERIAL_POSE)
    assert layout.agv_tcp_pose == list(cfg.AGV_TCP_POSE)


def test_derived_station_tcp_is_material_top_center():
    layout = default_layout()
    material = layout.station_material_pose
    tcp = layout.station_tcp_pose
    assert math.isclose(tcp[2], material[2] + cfg.MATERIAL_HALF_HEIGHT)
    assert tcp[:2] == list(material[:2])
    assert tcp[3:] == cfg.TOOL_TARGET_QUATERNION


def test_derived_agv_tcp_is_material_top_center():
    layout = default_layout()
    material = layout.agv_material_pose
    tcp = layout.agv_tcp_pose
    assert math.isclose(tcp[2], material[2] + cfg.MATERIAL_HALF_HEIGHT)


def test_side_mounted_layout_changes_targets_but_keeps_pick_on_belt():
    layout = LayoutSpec(
        name="side",
        arm_base=(-0.20, 0.55, cfg.ISLAND_TOP_Z),
        island_pos=(-0.20, 0.55),
        station_pos=(-0.20, 1.20),
        agv_dock=(0.40, 0.75),
    )
    assert layout.pick_material_pose == tuple(cfg.MATERIAL_PICK_POS)
    assert layout.station_material_pose[:2] == (-0.20, 1.20)
    assert layout.agv_material_pose[:2] == (0.40, 0.75)


def test_default_layout_passes_validation():
    report = validate_layout(default_layout())
    assert report["ok"], [c for c in report["checks"] if not c["ok"]]


def test_station_overlapping_conveyor_is_rejected():
    layout = LayoutSpec(
        name="bad_station",
        arm_base=(-0.20, 0.55, cfg.ISLAND_TOP_Z),
        island_pos=(-0.20, 0.55),
        station_pos=(-0.62, 0.0),          # 直接压在传送带上方
        agv_dock=(0.40, 0.75),
    )
    report = validate_layout(layout)
    failed = {c["check"] for c in report["checks"] if not c["ok"]}
    assert "station_vs_conveyor" in failed
    assert report["ok"] is False


def test_agv_overlapping_island_is_rejected():
    layout = LayoutSpec(
        name="bad_agv",
        arm_base=(-0.20, 0.55, cfg.ISLAND_TOP_Z),
        island_pos=(-0.20, 0.55),
        station_pos=(-0.20, 1.20),
        agv_dock=(-0.20, 0.55),            # 与机械臂基座/岛体重叠
    )
    report = validate_layout(layout)
    failed = {c["check"] for c in report["checks"] if not c["ok"]}
    assert "agv_vs_island" in failed


def test_unreachable_station_is_rejected():
    layout = LayoutSpec(
        name="far_station",
        arm_base=(-0.20, 0.55, cfg.ISLAND_TOP_Z),
        island_pos=(-0.20, 0.55),
        station_pos=(-0.20, 2.50),         # 超出 0.80 m 可达余量
        agv_dock=(0.40, 0.75),
    )
    report = validate_layout(layout)
    failed = {c["check"] for c in report["checks"] if not c["ok"]}
    assert "arm_reach:station" in failed


def test_rect_helpers_are_xy_only_and_touch_is_not_overlap():
    a = rect_of((0.0, 0.0), (1.0, 1.0))
    b = rect_of((1.0, 0.0), (1.0, 1.0))
    assert a == (-0.5, -0.5, 0.5, 0.5)
    assert not overlap_rects(a, b), "边界相切不算压占"
    assert overlap_rects(a, rect_of((0.9, 0.0), (1.0, 1.0)))
