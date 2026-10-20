"""tests/test_step1_1_layout_eval.py — 目标集合、IK 扫描与布局选择.

纯逻辑, 不需要 CoppeliaSim。实机扫描由 `run_step1_1 --phase layout` 执行。
"""

import pytest

from stage1 import config as base
from stage1.ur5_ik_controller import IKResult
from stage1_1 import config as cfg
from stage1_1.layout_eval import (
    boundary_scan,
    layout_targets,
    scan_targets,
    select_layout,
)


class FakeIK:
    """按回调判断目标是否可达; 用于验证扫描/选择逻辑."""

    def __init__(self, reachable, joints=None):
        self.reachable = reachable
        self.joints = list(joints or [0.0] * 6)
        self.solved = []

    def solve(self, pose):
        self.solved.append(tuple(pose))
        ok = bool(self.reachable(pose))
        if ok:
            return IKResult(True, list(self.joints), 1, 0, [0.001, 0.001],
                            False, "ok")
        return IKResult(False, [], 0, 112, [0.5, 0.2], False, "unreachable")

    def joints_within_limits(self, joints):
        return all(abs(float(q)) <= 2.0 * 3.141592653589793 for q in joints)


def _layout(name="L1_S1"):
    return next(l for l in cfg.CANDIDATE_LAYOUTS if l.name == name)


def test_layout_targets_cover_region_center_corners_approaches_and_places():
    layout = _layout()
    targets = layout_targets(layout)
    pick = {k: v for k, v in targets.items() if k.startswith("pick_")}
    # 9 个边界点 × (抓取 + 接近 + 抬升)
    assert len(pick) == 27
    assert "pick_center" in pick
    assert "pick_center_approach" in pick
    assert "pick_center_lift" in pick
    assert "station" in targets and "station_approach" in targets
    assert "agv" in targets and "agv_approach" in targets
    assert len(targets) == 31


def test_pick_targets_stay_inside_frozen_region():
    layout = _layout()
    region = cfg.ALLOWED_PICK_REGION
    for name, pose in layout_targets(layout).items():
        if not name.startswith("pick_") or name.endswith(("_approach", "_lift")):
            continue
        material_center = [pose[0], pose[1],
                           pose[2] - base.MATERIAL_HALF_HEIGHT]
        assert region.contains(material_center), name


def test_approach_and_lift_are_above_the_pick_target():
    layout = _layout()
    targets = layout_targets(layout)
    for name, pose in targets.items():
        if name.startswith("pick_") and name.endswith("_approach"):
            pick = targets[name.replace("_approach", "")]
            assert pose[2] == pytest.approx(
                pick[2] + cfg.PICK_APPROACH_CLEARANCE)
        if name.startswith("pick_") and name.endswith("_lift"):
            pick = targets[name.replace("_lift", "")]
            assert pose[2] == pytest.approx(
                pick[2] + cfg.PICK_LIFT_CLEARANCE)


def test_scan_targets_records_pose_and_position_probes():
    layout = _layout()
    targets = layout_targets(layout)
    pose_ik = FakeIK(lambda pose: True)
    position_ik = FakeIK(lambda pose: True)
    report = scan_targets(pose_ik, position_ik, targets)
    assert report["all_pose_ok"] is True
    assert report["all_position_ok"] is True
    assert len(report["targets"]) == len(targets)
    first = report["targets"][0]
    assert set(first) >= {
        "name", "pose", "pose_success", "pose_precision",
        "pose_joints", "joints_within_limits", "position_success",
    }


def test_scan_targets_marks_failed_target_without_fake_joints():
    layout = _layout()
    targets = layout_targets(layout)
    bad_name = "pick_xmin_ymin"
    pose_ik = FakeIK(lambda pose: not (
        abs(pose[0] - targets[bad_name][0]) < 1e-9
        and abs(pose[1] - targets[bad_name][1]) < 1e-9))
    position_ik = FakeIK(lambda pose: True)
    report = scan_targets(pose_ik, position_ik, targets)
    assert report["all_pose_ok"] is False
    failed = [t for t in report["targets"] if t["name"] == bad_name][0]
    assert failed["pose_success"] is False
    assert failed["pose_joints"] is None
    assert bad_name in report["failed_names"]


def test_boundary_scan_reports_last_reachable_and_first_failure():
    layout = _layout()
    center = layout.pick_tcp_pose
    region = cfg.ALLOWED_PICK_REGION
    # 在 X 方向 0.20 m、Y 方向 0.10 m 处不可达
    def reachable(pose):
        dx = abs(pose[0] - center[0])
        dy = abs(pose[1] - center[1])
        return dx <= 0.20 + 1e-9 and dy <= 0.10 + 1e-9

    pose_ik = FakeIK(reachable)
    report = boundary_scan(pose_ik, center, region, step=0.01, max_distance=0.30)
    assert report["+x"]["last_reachable_m"] == pytest.approx(0.20)
    assert report["+x"]["first_failure_m"] == pytest.approx(0.21)
    assert report["+y"]["last_reachable_m"] == pytest.approx(0.10)
    assert report["-x"]["last_reachable_m"] == pytest.approx(0.20)
    assert report["+x"]["margin_m"] == pytest.approx(
        0.20 - (region.x[1] - center[0]))
    assert report["+y"]["margin_m"] == pytest.approx(
        0.10 - (region.y[1] - center[1]))


def _candidate_report(name, margin, failed=False):
    return {
        "layout": name,
        "static_ok": True,
        "all_pose_ok": not failed,
        "all_joints_within_limits": True,
        "boundary": {
            "+x": {"margin_m": margin}, "-x": {"margin_m": margin},
            "+y": {"margin_m": margin}, "-y": {"margin_m": margin},
        },
        "reach_margins": {"pick": margin, "station": margin, "agv": margin},
    }


def test_select_layout_prefers_larger_boundary_margin():
    reports = [_candidate_report("A", 0.10), _candidate_report("B", 0.20)]
    decision = select_layout(reports)
    assert decision["ok"] is True
    assert decision["selected"] == "B"
    assert decision["score"] == pytest.approx(0.20)


def test_select_layout_rejects_candidate_with_failed_required_target():
    reports = [_candidate_report("A", 0.30, failed=True),
               _candidate_report("B", 0.10)]
    decision = select_layout(reports)
    assert decision["ok"] is True
    assert decision["selected"] == "B"
    assert "A" in decision["ineligible"]


def test_select_layout_without_eligible_candidate_is_explicit():
    reports = [_candidate_report("A", 0.10, failed=True)]
    decision = select_layout(reports)
    assert decision["ok"] is False
    assert decision["selected"] is None
    assert "没有候选" in decision["reason"]


def test_select_layout_tie_breaks_deterministically_by_name():
    reports = [_candidate_report("L1_S2", 0.20),
               _candidate_report("L1_S1", 0.20)]
    decision = select_layout(reports)
    assert decision["selected"] == "L1_S1"
