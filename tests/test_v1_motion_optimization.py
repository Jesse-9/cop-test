"""v1 运动层: 腕部分支、逐段指标、锁轴与轨迹压缩测试."""

import math

from flexible_factory.simulation import config_motion as cfg
from flexible_factory.simulation.motion.branch import (NEGATIVE, POSITIVE, SINGULAR, branch_ok,
                           wrist_branch)
from flexible_factory.simulation.motion.metrics import joint_stats, segment_metrics
from flexible_factory.simulation.motion.path_planning import (MotionPlan, MotionSegment, PlanStatus,
                                  SegmentCheck, compact_plan_waypoints)
from flexible_factory.simulation.motion.trajectory import ExecutorStatus, JointStreamExecutor
from flexible_factory.simulation.unit_controller_motion import (horizontal_transfer_check, lift_pose,
                                    pre_place_pose, transfer_goals)

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


# ── 转运语义 waypoint (06-15) ───────────────────────────────

PLACE = [-0.2, 1.2, 0.4, 0.0, 0.0, 0.0, 1.0]


def test_transfer_goals_only_reach_pre_place_at_shared_height():
    goals = transfer_goals(PLACE)
    assert [goal.name for goal in goals] == ["pre_place"]
    pre = goals[0].pose
    assert goals[0].cartesian is True
    assert goals[0].strict_orientation is True
    assert goals[0].allow_final_load_contact is False
    assert abs(pre[2] - cfg.TRANSFER_HEIGHT) < 1e-9
    assert pre[0] == PLACE[0] and pre[1] == PLACE[1]
    assert pre[3:7] == PLACE[3:7]
    assert goals[0].lock_joint is None


def test_lift_and_pre_place_share_height_and_tool_orientation():
    pick_tcp = [-0.3, 0.0, 0.55, 0.0, 0.0, 0.0, 1.0]
    orientation = PLACE[3:7]
    lift = lift_pose(pick_tcp, orientation)
    pre = pre_place_pose(PLACE)
    assert abs(lift[2] - pre[2]) < 1e-9
    assert abs(lift[2] - cfg.TRANSFER_HEIGHT) < 1e-9
    assert lift[0] == pick_tcp[0] and lift[1] == pick_tcp[1]
    assert lift[3:7] == orientation
    assert pre[3:7] == orientation


def test_horizontal_transfer_check_accepts_fixed_height_and_attitude():
    class Checker:
        def tcp_at(self, values):
            return [float(values[0]), float(values[1]),
                    cfg.TRANSFER_HEIGHT, *PLACE[3:7]]

    waypoints = [[0.0, 0.0, 0.0, 0.0, math.pi / 2, 0.0],
                 [0.1, 0.1, 0.0, 0.0, math.pi / 2, 0.0]]
    report = horizontal_transfer_check(waypoints, Checker(),
                                       orientation=PLACE[3:7])
    assert report["ok"] is True
    assert report["points"] == 2
    assert report["max_z"] == 0.0
    assert report["max_ang"] == 0.0


def test_horizontal_transfer_check_rejects_height_or_attitude_drift():
    class HeightDrift:
        def tcp_at(self, values):
            return [float(values[0]), float(values[1]),
                    cfg.TRANSFER_HEIGHT + 0.02, *PLACE[3:7]]

    waypoints = [[0.0, 0.0, 0.0, 0.0, math.pi / 2, 0.0]]
    report = horizontal_transfer_check(waypoints, HeightDrift(),
                                       orientation=PLACE[3:7])
    assert report["ok"] is False
    assert report["max_z"] == 0.02

    class YawDrift:
        def tcp_at(self, values):
            return [float(values[0]), float(values[1]),
                    cfg.TRANSFER_HEIGHT, 0.0, 0.0, 0.2, 1.0]

    report = horizontal_transfer_check(waypoints, YawDrift(),
                                       orientation=PLACE[3:7])
    assert report["ok"] is False
    assert report["max_ang"] > cfg.EXEC_TCP_ANG_TOL


def test_horizontal_transfer_check_reports_unknown_query():
    class Unknown:
        def tcp_at(self, values):
            return None

    report = horizontal_transfer_check([[0.0] * 6], Unknown())
    assert report["ok"] is False
    assert report["query_unknown"] is True


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
