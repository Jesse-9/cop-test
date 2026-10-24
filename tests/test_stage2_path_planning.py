"""阶段二规划器候选/预算/失败测试 (06-09 §4–§5 / §12, 06-10 §3–§5)."""

import math

import pytest

from stage1.ur5_ik_controller import IKResult
from stage2.branch import branch_ok
from stage2.path_planning import (ConfigCheck, Goal, LocalPlanner,
                                  MotionContext, PlanBudgetExceeded,
                                  PlanStatus, SegmentCheck)

WRIST_UP = math.pi / 2


class FakeIK:
    """按目标位置返回构型; 可选模拟段内固定 q[0] 的可解性."""

    def __init__(self, q4=WRIST_UP, respect_lock=True):
        self.q4 = float(q4)
        self.respect_lock = respect_lock

    def solve_candidates(self, pose, seeds, max_candidates=6,
                         dedupe_tol=0.05, lock_joint=None,
                         lock_value=None, lock_tol=None):
        q = [float(pose[0]), float(pose[1]), float(pose[2]),
             0.0, self.q4, 0.0]
        if (self.respect_lock and lock_joint is not None
                and abs(q[int(lock_joint)] - float(lock_value))
                > float(lock_tol) + 1e-9):
            return []
        return [IKResult(True, q, 1, 0, [0.0, 0.0], False, "ok")]


class FakeChecker:
    def __init__(self, block=None, fail_after=None):
        self.block = block or (lambda q: None)
        self.fail_after = fail_after
        self.calls = 0
        self.budget = None

    def tcp_at(self, q):
        return [float(q[0]), float(q[1]), float(q[2]), 0.0, 0.0, 0.0, 1.0]

    def _result(self, q, context, **kwargs):
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise PlanBudgetExceeded("test budget")
        if getattr(context, "branch", None) is not None:
            ok, label, margin = branch_ok(
                q, context.branch, context.branch_margin)
            if not ok:
                return ConfigCheck(
                    PlanStatus.WRIST_BRANCH_MISMATCH, list(q),
                    tcp=self.tcp_at(q),
                    message=f"branch {label} margin {margin:.3f}")
        state = self.block(q)
        if state is None:
            return None
        status, message = state
        return ConfigCheck(status, list(q), tcp=self.tcp_at(q),
                           message=message)

    def check(self, q, context, **kwargs):
        return self._result(q, context, **kwargs) or ConfigCheck(
            PlanStatus.OK, list(q), tcp=self.tcp_at(q))

    def check_segment(self, q0, q1, context, **kwargs):
        start = self._result(q0, context, **kwargs)
        if start is not None and not start.ok:
            return SegmentCheck(start.status, list(q0),
                                list(q1), samples=1, first_failure=start,
                                message=start.message)
        end = self._result(q1, context, **kwargs)
        if end is not None and not end.ok:
            return SegmentCheck(end.status, list(q0),
                                list(q1), samples=2, first_failure=end,
                                message=end.message)
        mid = [(float(a) + float(b)) / 2 for a, b in zip(q0, q1)]
        mid_result = self._result(mid, context, **kwargs)
        if mid_result is not None and not mid_result.ok:
            return SegmentCheck(mid_result.status, list(q0), list(q1),
                                samples=3, first_failure=mid_result,
                                message=mid_result.message)
        return SegmentCheck(PlanStatus.OK, list(q0), list(q1), samples=3)


def context():
    return MotionContext(phase="LIFT", target_handle=-1, carrying=True)


def goal(**kwargs):
    pose = kwargs.pop("pose", [0.2, 0.0, 0.7, 0.0, 0.0, 0.0, 1.0])
    return Goal("lift", pose, cartesian=True, **kwargs)


def start_q(x=0.0):
    return [x, 0.0, 0.5, 0.0, WRIST_UP, 0.0]


class DirectOnlyPlanner(LocalPlanner):
    """只尝试直接策略, 用于确定性的锁轴回退测试."""

    def _strategies(self, goals):
        yield ("direct", None, list(goals))


def test_direct_plan_passes_when_clear():
    planner = LocalPlanner(FakeChecker(), FakeIK())
    plan = planner.plan(start_q(), [goal()], context(),
                        start_pose=[0.0, 0.0, 0.5, 0, 0, 0, 1])
    assert plan.ok
    assert plan.status is PlanStatus.DIRECT_OK
    assert len(plan.waypoints) > 1
    assert plan.branch_anchor == "positive"
    assert plan.metrics["totals"]["tv_total"] > 0


def test_middle_segment_collision_is_not_treated_as_endpoint_only():
    def block(q):
        if 0.05 < q[0] < 0.15 and abs(q[1]) < 1e-6:
            return (PlanStatus.PATH_COLLISION, "middle blocked")
        return None

    planner = LocalPlanner(FakeChecker(block=block), FakeIK())
    plan = planner.plan(start_q(), [goal()], context(),
                        start_pose=[0.0, 0.0, 0.5, 0, 0, 0, 1])
    # 直接路径中段被挡; HOME 中转在 z=-1.57 处经过, 应被接受
    assert plan.ok
    assert plan.status is PlanStatus.AVOIDED
    assert plan.strategy != "direct"


def test_all_strategies_blocked_returns_failure_without_partial_path():
    def block(q):
        return (PlanStatus.PATH_COLLISION, "always blocked")

    planner = LocalPlanner(FakeChecker(block=block), FakeIK())
    plan = planner.plan(start_q(), [goal()], context(),
                        start_pose=[0.0, 0.0, 0.5, 0, 0, 0, 1])
    assert not plan.ok
    assert plan.status in (PlanStatus.PATH_COLLISION,
                           PlanStatus.LOCAL_AVOIDANCE_FAILED,
                           PlanStatus.START_CONFIG_INVALID)


def test_unknown_query_is_never_safe():
    def block(q):
        if q[0] > 0.05:
            return (PlanStatus.QUERY_UNKNOWN, "unknown")
        return None

    planner = LocalPlanner(FakeChecker(block=block), FakeIK())
    plan = planner.plan(start_q(), [goal()], context(),
                        start_pose=[0.0, 0.0, 0.5, 0, 0, 0, 1])
    assert not plan.ok
    assert plan.status is PlanStatus.QUERY_UNKNOWN


def test_budget_exhaustion_is_not_success():
    planner = LocalPlanner(FakeChecker(fail_after=3), FakeIK())
    plan = planner.plan(start_q(), [goal()], context(),
                        start_pose=[0.0, 0.0, 0.5, 0, 0, 0, 1])
    assert not plan.ok
    assert plan.status is PlanStatus.PLAN_BUDGET_EXCEEDED


def test_singular_start_is_rejected():
    planner = LocalPlanner(FakeChecker(), FakeIK())
    q = start_q()
    q[4] = 0.05
    plan = planner.plan(q, [goal()], context(),
                        start_pose=[0.0, 0.0, 0.5, 0, 0, 0, 1])
    assert not plan.ok
    assert plan.status is PlanStatus.WRIST_SINGULARITY


def test_wrist_branch_mismatch_is_not_accepted():
    planner = DirectOnlyPlanner(FakeChecker(), FakeIK(q4=-WRIST_UP))
    plan = planner.plan(start_q(), [goal()], context(),
                        start_pose=[0.0, 0.0, 0.5, 0, 0, 0, 1])
    assert not plan.ok
    assert plan.status is PlanStatus.WRIST_BRANCH_MISMATCH


def test_locked_segment_keeps_q0_and_reports_policy():
    planner = LocalPlanner(FakeChecker(), FakeIK())
    plan = planner.plan(start_q(x=0.2), [goal(lock_joint=0)], context(),
                        start_pose=[0.2, 0.0, 0.5, 0, 0, 0, 1])
    assert plan.ok
    assert plan.lock_policy == "lock"
    assert plan.locked_segments == ["lift"]
    segment = plan.segments[0]
    assert segment.lock_joint == 0
    assert abs(segment.q_start[0] - 0.2) < 1e-6
    assert abs(segment.q_end[0] - 0.2) < 1e-6
    assert plan.metrics["segments"][0]["lock_ok"] is True


def test_lock_unavailable_falls_back_to_free_same_branch():
    planner = DirectOnlyPlanner(FakeChecker(), FakeIK())
    plan = planner.plan(start_q(x=0.0), [goal(lock_joint=0)], context(),
                        start_pose=[0.0, 0.0, 0.5, 0, 0, 0, 1])
    assert plan.ok
    assert plan.lock_policy == "free"
    failed = [attempt for attempt in plan.attempts
              if attempt["strategy"].startswith("lock:")]
    assert failed and failed[0]["status"] == \
        PlanStatus.JOINT_LOCK_UNAVAILABLE.value
    assert plan.branch_anchor == "positive"


def test_lock_unavailable_without_fallback_fails():
    planner = DirectOnlyPlanner(FakeChecker(), FakeIK())
    plan = planner.plan(start_q(x=0.0), [goal(lock_joint=0)], context(),
                        start_pose=[0.0, 0.0, 0.5, 0, 0, 0, 1],
                        lock_fallback=False)
    assert not plan.ok
    assert plan.status is PlanStatus.JOINT_LOCK_UNAVAILABLE
