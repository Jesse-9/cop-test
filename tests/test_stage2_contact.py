"""阶段二接触几何测试 (06-09 §2 / §12 关键逻辑)."""

import math

import pytest

from stage2 import config as cfg
from stage2.contact import (ContactLimits, LocalBox, contact_pose,
                            evaluate_contact, quat_angle_between,
                            rotate_about_axis, top_surface)


def limits(**overrides):
    values = dict(
        max_gap=cfg.CONTACT_MAX_GAP,
        min_signed_distance=cfg.CONTACT_MIN_SIGNED_DISTANCE,
        max_lateral=cfg.CONTACT_MAX_LATERAL,
        max_normal_angle=cfg.CONTACT_MAX_NORMAL_ANGLE,
        face_tolerance=cfg.CONTACT_FACE_TOLERANCE,
        require_cup_coverage=cfg.CONTACT_REQUIRE_CUP_COVERAGE,
    )
    values.update(overrides)
    return ContactLimits(**values)


BOX = LocalBox.from_size((0.1, 0.1, 0.1))
MAT = [0.0, 0.0, 0.5, 0.0, 0.0, 0.0, 1.0]
CUP = 0.047159 / 2.0


def eval_at(z, x=0.0, y=0.0, quat=(0.0, 0.0, 0.0, 1.0),
            material=MAT, box=BOX):
    tcp = [x, y, z, *quat]
    return evaluate_contact(tcp, material, box, limits(), CUP)


def test_top_surface_center_and_normal():
    frame = top_surface(MAT, BOX)
    assert frame.center_world == pytest.approx((0.0, 0.0, 0.55))
    assert frame.normal_world == pytest.approx((0.0, 0.0, 1.0))
    assert frame.half_u == pytest.approx(0.05)
    assert frame.half_v == pytest.approx(0.05)


def test_valid_contact_at_top_center():
    result = eval_at(0.55)
    assert result.valid, result.reasons
    assert result.signed_distance == pytest.approx(0.0, abs=1e-9)
    assert result.normal_angle == pytest.approx(0.0, abs=1e-9)
    assert result.cup_covered is True


def test_gap_exceeded():
    result = eval_at(0.55 + cfg.CONTACT_MAX_GAP + 0.001)
    assert not result.valid
    assert any("gap_exceeded" in reason for reason in result.reasons)


def test_penetration_within_budget_is_valid():
    result = eval_at(0.55 - 0.001)
    assert result.valid, result.reasons
    assert result.signed_distance < 0


def test_penetration_beyond_budget_rejected():
    result = eval_at(0.55 + cfg.CONTACT_MIN_SIGNED_DISTANCE - 0.001)
    assert not result.valid
    assert any("penetration_exceeded" in reason for reason in result.reasons)


def test_reversed_tool_rejected():
    # Rx(pi): 工具 -Z 变成世界 +Z, 与 -n 反向
    result = eval_at(0.55, quat=(1.0, 0.0, 0.0, 0.0))
    assert not result.valid
    assert any("normal_angle_exceeded" in reason for reason in result.reasons)
    assert result.normal_angle == pytest.approx(math.pi, abs=1e-6)


def test_lateral_offset_rejected():
    result = eval_at(0.55, x=cfg.CONTACT_MAX_LATERAL + 0.005)
    assert not result.valid
    assert any("lateral_offset_exceeded" in reason
               for reason in result.reasons)


def test_cup_not_covered_rejected():
    # 0.03 + 杯口半径 > 0.05, 但横向偏移仍在 max_lateral 内
    result = eval_at(0.55, x=0.030)
    assert not result.valid
    assert result.cup_covered is False
    assert any("cup_not_covered" in reason for reason in result.reasons)


def test_rotated_material_contact_pose_roundtrip():
    yaw = math.pi / 2
    mat = [0.2, -0.1, 0.5, 0.0, 0.0, math.sin(yaw / 2), math.cos(yaw / 2)]
    pose = contact_pose(mat, BOX, clearance=0.002)
    result = evaluate_contact(pose, mat, BOX, limits(), CUP)
    assert result.valid, result.reasons
    assert result.signed_distance == pytest.approx(0.002, abs=1e-9)
    assert result.lateral_offset == pytest.approx(0.0, abs=1e-9)
    assert result.normal_angle == pytest.approx(0.0, abs=1e-6)


def test_contact_pose_yaw_keeps_normal_alignment():
    pose = contact_pose(MAT, BOX, clearance=0.0, yaw=math.pi / 3)
    result = evaluate_contact(pose, MAT, BOX, limits(), CUP)
    assert result.valid, result.reasons
    # 绕法向 yaw 不改变工具探测方向
    assert result.normal_angle == pytest.approx(0.0, abs=1e-6)


def test_non_centered_bbox_uses_measured_min_max():
    box = LocalBox(minimum=(-0.02, -0.06, -0.1),
                   maximum=(0.08, 0.04, 0.02))
    frame = top_surface(MAT, box)
    assert frame.center_world == pytest.approx((0.03, -0.01, 0.52))
    pose = contact_pose(MAT, box, clearance=0.0)
    result = evaluate_contact(pose, MAT, box, limits(), CUP)
    assert result.valid, result.reasons


def test_invalid_bbox_rejected():
    with pytest.raises(ValueError):
        LocalBox(minimum=(0.1, 0.0, 0.0), maximum=(0.0, 0.1, 0.1))
