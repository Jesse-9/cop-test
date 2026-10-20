"""tests/test_step1_1_config.py — 步骤 1.1 配置不变量.

纯逻辑, 不需要 CoppeliaSim。这里冻结的数值是后续实机试验的判定门槛:
候选布局、允许抓取区域、初始位置、重复次数和稳定阈值一旦运行就不得
按观察结果放宽 (06-07 §5)。
"""

import math

import pytest

from stage1 import config as base
from stage1.layout import (
    LayoutSpec,
    default_layout,
    overlap_rects,
    validate_layout,
)
from stage1_1 import config as cfg


def test_candidate_names_are_unique():
    names = [lay.name for lay in cfg.CANDIDATE_LAYOUTS]
    assert len(names) == len(set(names))


def test_each_candidate_passes_static_layout_validation():
    for layout in cfg.CANDIDATE_LAYOUTS:
        report = validate_layout(layout)
        assert report["ok"], (layout.name,
                              [c for c in report["checks"] if not c["ok"]])


def test_candidates_are_side_mounted_on_both_conveyor_sides():
    lateral = {1 if lay.arm_base[1] > 0 else -1
               for lay in cfg.CANDIDATE_LAYOUTS}
    assert lateral == {1, -1}, "候选必须覆盖传送带横向两侧"
    for layout in cfg.CANDIDATE_LAYOUTS:
        island_edge = abs(layout.arm_base[1]) - layout.island_size[1] / 2.0
        belt_edge = base.CONVEYOR_NATIVE_WIDTH / 2.0
        assert island_edge > belt_edge, (
            f"{layout.name}: 岛体仍压占传送带横向空间 "
            f"({island_edge:.3f} <= {belt_edge:.3f})")


def test_two_arm_candidates_each_have_two_station_options():
    arms = {(lay.arm_base, lay.island_pos) for lay in cfg.CANDIDATE_LAYOUTS}
    assert len(arms) == 2
    for arm in arms:
        stations = {lay.station_pos for lay in cfg.CANDIDATE_LAYOUTS
                    if (lay.arm_base, lay.island_pos) == arm}
        assert len(stations) == 2, f"{arm} 需要至少两个置物台候选"


def test_candidate_agv_dock_is_on_same_side_as_arm():
    """AGV 装卸点必须在机械臂可达余量内; 不允许为了通过而删掉 AGV 目标。"""
    for layout in cfg.CANDIDATE_LAYOUTS:
        d = math.dist(layout.arm_base, layout.agv_tcp_pose[:3])
        assert d <= base.REACH_LIMIT, (layout.name, d)


def test_allowed_pick_region_is_inside_top_run_with_material_margin():
    region = cfg.ALLOWED_PICK_REGION
    top_min = (base.CONVEYOR_CENTER[0] + base.CONVEYOR_TOP_RUN_LOCAL[0])
    top_max = (base.CONVEYOR_CENTER[0] + base.CONVEYOR_TOP_RUN_LOCAL[1])
    half = base.MATERIAL_SIZE[0] / 2.0
    assert region.x[0] - half >= top_min
    assert region.x[1] + half <= top_max
    assert region.x[0] < region.x[1]

    belt_half = base.CONVEYOR_NATIVE_WIDTH / 2.0
    assert region.y[0] >= -belt_half + half
    assert region.y[1] <= belt_half - half
    assert region.y[0] < region.y[1]


def test_allowed_pick_region_contains_current_stop_and_boundaries():
    region = cfg.ALLOWED_PICK_REGION
    assert region.contains(base.MATERIAL_PICK_POS)
    points = region.boundary_points()
    assert len(points) == 9, "中心 + 4 角 + 4 边中点"
    for p in points:
        assert region.contains(p)
        assert math.isclose(p[2], region.z_center)


def test_allowed_pick_region_rejects_outside_points():
    region = cfg.ALLOWED_PICK_REGION
    assert not region.contains((region.x[0] - 0.01, 0.0, region.z_center))
    assert not region.contains((0.0, region.y[1] + 0.01, region.z_center))
    assert not region.contains(
        (0.0, 0.0, region.z_center + region.z_tol + 0.001))


def test_initial_positions_are_inside_sensor_zone_and_allowed_region():
    region = cfg.ALLOWED_PICK_REGION
    sensor_x = base.CONVEYOR_CENTER[0] + base.CONVEYOR_STOP_LOCAL_X
    detection_reach = base.PICK_SENSOR_RADIUS + base.MATERIAL_SIZE[0] / 2.0
    for name, x in cfg.INITIAL_POSITIONS.items():
        assert abs(x - sensor_x) <= detection_reach + 1e-9, name
        assert region.contains((x, 0.0, region.z_center)), name


def test_experiment_matrix_uses_only_frozen_nominal_speed():
    assert cfg.SPEEDS_MPS == (base.CONVEYOR_SPEED,)
    assert cfg.SPEEDS_PENDING == ("min", "max"), (
        "最低/最高速度尚无冻结值, 只能记录为待冻结参数")
    assert cfg.REPEATS_PER_COMBO == 10
    assert set(cfg.GROUP_SPECS) == {"A", "B", "C"}
    for spec in cfg.GROUP_SPECS.values():
        assert spec["repeats"] == cfg.REPEATS_PER_COMBO
        assert spec["positions"] == tuple(cfg.INITIAL_POSITIONS)


def test_group_isolation_flags():
    a = cfg.GROUP_SPECS["A"]
    b = cfg.GROUP_SPECS["B"]
    c = cfg.GROUP_SPECS["C"]
    assert a["stopper"] is True and a["compensation"] is True
    assert a["stop_mode"] == "progress"
    assert b["stopper"] is False and b["compensation"] is True
    assert b["stop_mode"] == "sensor"
    assert c["stopper"] is False and c["compensation"] is False
    assert c["stop_mode"] == "none"


def test_stability_thresholds_match_stage1_contract():
    assert cfg.MATERIAL_STABLE_SPEED_TOL == base.SUCTION_MAX_ATTACH_SPEED
    assert cfg.PAD_STOP_VEL_TOL < base.CONVEYOR_SPEED
    assert cfg.MATERIAL_STABLE_STEPS >= 3
    assert cfg.PAD_STOP_CONFIRM_STEPS >= 2
    assert cfg.MATERIAL_MAX_TILT <= base.SUCTION_TILT_TOL


def test_baseline_layout_is_stage1_default():
    assert cfg.BASELINE_LAYOUT == default_layout()
