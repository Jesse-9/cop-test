"""v1 基础层配置不变量测试.

纯逻辑, 不需要 CoppeliaSim。
"""

import math

from flexible_factory.simulation import config_base as cfg


def test_all_aliases_are_flat_underscore_names():
    aliases = []
    for key, value in cfg.ALIASES.items():
        if key == "roots":
            aliases.extend(value)
        else:
            aliases.append(value)
    for a in aliases:
        assert a and "/" not in a, f"别名必须是扁平下划线形式: {a!r}"


def test_required_aliases_are_subset_of_declared_aliases():
    declared = set(cfg.REQUIRED_ALIASES)
    all_declared = set()
    for key, value in cfg.ALIASES.items():
        if key == "roots":
            all_declared.update(value)
        else:
            all_declared.add(value)
    assert declared <= all_declared


def test_required_aliases_are_unique():
    assert len(cfg.REQUIRED_ALIASES) == len(set(cfg.REQUIRED_ALIASES))


def test_conveyor_stop_time_is_short():
    assert cfg.CONVEYOR_ACCEL > 0
    stop_time = cfg.CONVEYOR_SPEED / cfg.CONVEYOR_ACCEL
    assert stop_time <= cfg.CONVEYOR_MAX_STOP_TIME, (
        f"停车时间 {stop_time:.3f}s 超过 {cfg.CONVEYOR_MAX_STOP_TIME:.3f}s")


def test_conveyor_geometry_uses_top_run_not_bbox():
    assert cfg.CONVEYOR_ENTRY_LOCAL_X > cfg.CONVEYOR_TOP_RUN_LOCAL[0]
    assert cfg.CONVEYOR_STOP_LOCAL_X < cfg.CONVEYOR_TOP_RUN_LOCAL[1]
    assert cfg.CONVEYOR_TRAVEL_LENGTH > 0.5


def test_pick_sensor_is_above_material_top_and_clear_of_belt():
    material_top = cfg.CONVEYOR_SURFACE_Z + cfg.MATERIAL_SIZE[2]
    assert cfg.PICK_SENSOR_Z > material_top
    # 体积下沿高于带面 (不误检 pad), 但能覆盖物料顶面
    assert cfg.PICK_SENSOR_Z - cfg.PICK_SENSOR_RANGE >= cfg.CONVEYOR_SURFACE_Z
    assert cfg.PICK_SENSOR_Z - cfg.PICK_SENSOR_RANGE <= material_top


def test_tcp_poses_are_material_top_centers():
    pick = cfg.PICK_TCP_POSE
    assert pick[:2] == list(cfg.MATERIAL_PICK_POS[:2])
    assert math.isclose(pick[2], cfg.MATERIAL_PICK_POS[2] + cfg.MATERIAL_HALF_HEIGHT)
    assert pick[3:] == cfg.TOOL_TARGET_QUATERNION

    station = cfg.STATION_TCP_POSE
    assert math.isclose(station[2], cfg.STATION_MATERIAL_POSE[2] + cfg.MATERIAL_HALF_HEIGHT)


def test_arm_reach_for_all_targets():
    for point in (cfg.PICK_TCP_POSE, cfg.STATION_TCP_POSE, cfg.AGV_TCP_POSE):
        d = cfg.arm_reach_to(point)
        assert d <= cfg.REACH_LIMIT, f"目标超出 UR5 可达域: {d:.3f} > {cfg.REACH_LIMIT:.3f}"


def test_agv_tray_top_is_above_chassis():
    assert cfg.AGV_TRAY_TOP_Z > cfg.AGV_ROOT_Z + cfg.AGV_CHASSIS_TOP_LOCAL_Z


def test_fault_choices_cover_spec():
    for required in (
        cfg.FAULT_NO_TARGET, cfg.FAULT_UID_MISMATCH, cfg.FAULT_IK_UNREACHABLE,
        cfg.FAULT_SENSOR_DISABLED, cfg.FAULT_AGV_BLOCKED,
        cfg.FAULT_RELEASE_UNSTABLE,
    ):
        assert required in cfg.FAULT_CHOICES


def test_target_tcp_pose_unknown_raises():
    try:
        cfg.target_tcp_pose("nowhere")
    except ValueError:
        pass
    else:
        raise AssertionError("未知目标必须抛 ValueError")
