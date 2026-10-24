"""阶段 2.01: 腕部分支、逐段指标、锁轴与轨迹压缩测试 (06-10 §3–§5)."""

import math

from stage2 import config as cfg
from stage2.branch import (NEGATIVE, POSITIVE, SINGULAR, branch_ok,
                           wrist_branch)
from stage2.metrics import joint_stats, segment_metrics
from stage2.path_planning import (MotionPlan, MotionSegment, PlanStatus,
                                  SegmentCheck, compact_plan_waypoints)
from stage2.trajectory import ExecutorStatus, JointStreamExecutor
from stage2.unit_controller import transfer_goals

WRIST_UP = math.pi / 2


def q(q0=0.0, q1=0.0, q2=0.0, q3=0.0, q4=WRIST_UP, q5=0.0):
    return [q0, q1, q2, q3, q4, q5]


# ── 腕部分支 ────────────────────────────────────────────────

def test_wrist_branch_uses_wrapped_q4():
    label, margin = wrist_branch(q(q4=WRIST_UP))
    assert label == POSITIVE
    assert abs(margin - math.pi / 2) < 1e-9
    # 4.7124 ≡ -pi/2 (mod 2pi): 同一物理构型必须判为 negative, 而不是按
    # 原始正数误判.
    label, margin = wrist_branch(q(q4=4.7124))
    assert label == NEGATIVE
    assert abs(margin - math.pi / 2) < 1e-3


def test_near_singular_candidate_is_rejected():
    # 4.10 候选实测中的近奇异解 -6.1363 → 归一化 0.147 rad
    label, margin = wrist_branch(q(q4=-6.1363))
    assert label == POSITIVE
    assert margin < cfg.WRIST_BRANCH_MARGIN
    assert not branch_ok(q(q4=-6.1363), POSITIVE)[0]


def test_exact_boundary_is_singular():
    label, margin = wrist_branch(q(q4=0.0))
    assert label == SINGULAR
    assert margin == 0.0
    assert not branch_ok(q(q4=0.0), POSITIVE)[0]


def test_cross_branch_is_rejected():
    ok, label, _ = branch_ok(q(q4=-WRIST_UP), POSITIVE)
    assert not ok
    assert label == NEGATIVE


# ── 关节指标 ────────────────────────────────────────────────

def test_joint_stats_do_not_wrap_across_zero():
    stats = joint_stats([q(q0=4.7), q(q0=4.9)])
    assert abs(stats["tv"][0] - 0.2) < 1e-9
    assert abs(stats["tv_total"] - 0.2) < 1e-9
    stats = joint_stats([q(q4=-1.5), q(q4=1.5)])
    assert abs(stats["tv"][4] - 3.0) < 1e-9
    assert stats["points"] == 2


def test_segment_metrics_report_lock_deviation_and_branch():
    stats = segment_metrics(
        [q(q0=0.10), q(q0=0.105), q(q0=0.11)], name="place",
        lock_joint=0, lock_value=0.10, lock_tol=0.02)
    assert stats["lock_ok"] is True
    assert abs(stats["lock_deviation_max"] - 0.01) < 1e-9
    assert stats["branch_start"] == POSITIVE
    assert stats["wrist_margin_min"] > cfg.WRIST_BRANCH_MARGIN


# ── 转运语义 waypoint ──────────────────────────────────────

def test_transfer_goals_split_horizontal_and_vertical_segments(monkeypatch):
    monkeypatch.setattr(cfg, "TRANSFER_SPLIT_HIGH_ABOVE_PLACE", True)
    place = [-0.2, 1.2, 0.4, 0.0, 0.0, 0.0, 1.0]
    current_tcp = [-0.3, 0.0, 0.7, 0.0, 0.0, 0.0, 1.0]
    goals = transfer_goals(place, current_tcp, (-0.2, 0.55, 0.3))
    assert [goal.name for goal in goals] == [
        "rise", "transit", "high_above_place", "pre_place", "place"]
    transit = goals[1].pose
    high = goals[2].pose
    pre = goals[3].pose
    assert high[0] == place[0] and high[1] == place[1]
    assert abs(high[2] - transit[2]) < 1e-9
    assert abs(pre[2] - (place[2] + cfg.PRE_APPROACH_CLEARANCE)) < 1e-9
    assert goals[0].pose[2] >= transit[2]
    # 默认锁定竖直段; 水平/中转/对角段允许 q[0] 参与
    assert goals[0].lock_joint == cfg.Q0_LOCK_JOINT
    assert goals[1].lock_joint is None
    assert goals[2].lock_joint is None
    assert goals[3].lock_joint is None
    assert goals[4].lock_joint == cfg.Q0_LOCK_JOINT


def test_transfer_goals_default_keeps_measured_safe_route():
    place = [-0.2, 1.2, 0.4, 0.0, 0.0, 0.0, 1.0]
    current_tcp = [-0.3, 0.0, 0.7, 0.0, 0.0, 0.0, 1.0]
    goals = transfer_goals(place, current_tcp, (-0.2, 0.55, 0.3))
    assert [goal.name for goal in goals] == [
        "rise", "transit", "pre_place", "place"]
    assert goals[2].lock_joint is None


# ── 轨迹压缩 ────────────────────────────────────────────────

class LinearChecker:
    def tcp_at(self, values):
        return [float(values[0]), float(values[1]), float(values[2]),
                0.0, 0.0, 0.0, 1.0]


def test_compaction_removes_only_collinear_points():
    waypoints = [q(q0=0.1 * i) for i in range(6)]
    segment = MotionSegment("rise", waypoints[0], waypoints[-1],
                            cartesian=True, waypoint_count=5)
    plan = MotionPlan(PlanStatus.OK, waypoints=waypoints, segments=[segment],
                      contexts=[object()])
    stats = compact_plan_waypoints(plan, LinearChecker())
    assert stats["removed"] > 0
    assert segment.waypoint_count < 5
    assert plan.waypoints[0] == waypoints[0]
    assert plan.waypoints[-1] == waypoints[-1]


def test_compaction_keeps_curved_points():
    class CurvedChecker(LinearChecker):
        def tcp_at(self, values):
            pose = super().tcp_at(values)
            pose[2] = float(values[0]) ** 2
            return pose

    waypoints = [q(q0=0.1 * i) for i in range(6)]
    segment = MotionSegment("rise", waypoints[0], waypoints[-1],
                            cartesian=True, waypoint_count=5)
    plan = MotionPlan(PlanStatus.OK, waypoints=waypoints, segments=[segment],
                      contexts=[object()])
    stats = compact_plan_waypoints(plan, CurvedChecker())
    assert stats["removed"] == 0
    assert segment.waypoint_count == 5


# ── 执行指标 ────────────────────────────────────────────────

class OkChecker:
    def check_segment(self, q0, q1, context, **kwargs):
        return SegmentCheck(PlanStatus.OK, list(q0), list(q1), samples=2)


def test_executor_reports_actual_per_segment_metrics():
    waypoints = [q(0.0, 0.0), q(0.1, 0.0), q(0.1, 0.1), q(0.1, 0.2)]
    segments = [
        MotionSegment("rise", waypoints[0], waypoints[1],
                      cartesian=True, waypoint_count=1),
        MotionSegment("place", waypoints[1], waypoints[3],
                      cartesian=True, waypoint_count=2, lock_joint=0,
                      lock_value=0.1, lock_tol=0.05),
    ]
    plan = MotionPlan(PlanStatus.OK, waypoints=waypoints, segments=segments,
                      contexts=[object(), object()])
    executor = JointStreamExecutor(plan, OkChecker(), plan.contexts,
                                   max_joint_step=0.05, pose_at=lambda v: [
                                       float(v[0]), float(v[1]), float(v[2]),
                                       0.0, 0.0, 0.0, 1.0])
    actual = list(waypoints[0])
    step = executor.next_command(actual, 0.0)
    for index in range(1, 400):
        if step.done:
            break
        actual = list(step.q)
        step = executor.next_command(actual, index * 0.05)
    assert step.status is ExecutorStatus.DONE
    metrics = executor.execution_metrics()
    names = [segment["name"] for segment in metrics["segments"]]
    assert names == ["rise", "place"]
    assert metrics["totals"]["tv_total"] > 0
    assert metrics["totals"]["steps"] > 0
    place = metrics["segments"][1]
    assert place["commands"] > 0
    assert abs(place["q_delta"][1] - 0.2) < 0.02
    assert place["branch_start"] == POSITIVE
    assert place["lock_ok"] is True
