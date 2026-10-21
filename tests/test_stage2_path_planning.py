"""阶段二规划器候选/预算/失败测试 (06-09 §4–§5 / §12)."""

import pytest

from stage1.ur5_ik_controller import IKResult
from stage2.path_planning import (ConfigCheck, Goal, LocalPlanner,
                                  MotionContext, PlanBudgetExceeded,
                                  PlanStatus, SegmentCheck)


class FakeIK:
    def solve_candidates(self, pose, seeds, max_candidates=6,
                         dedupe_tol=0.05):
        q = [float(pose[0]), float(pose[1]), float(pose[2]), 0.0, 0.0, 0.0]
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


def goal():
    return Goal("lift", [0.2, 0.0, 0.7, 0.0, 0.0, 0.0, 1.0], cartesian=True)


def test_direct_plan_passes_when_clear():
    planner = LocalPlanner(FakeChecker(), FakeIK())
    plan = planner.plan([0.0, 0.0, 0.5, 0.0, 0.0, 0.0], [goal()],
                        context(), start_pose=[0.0, 0.0, 0.5, 0, 0, 0, 1])
    assert plan.ok
    assert plan.status is PlanStatus.DIRECT_OK
    assert len(plan.waypoints) > 1


def test_middle_segment_collision_is_not_treated_as_endpoint_only():
    def block(q):
        if 0.05 < q[0] < 0.15 and abs(q[1]) < 1e-6:
            return (PlanStatus.PATH_COLLISION, "middle blocked")
        return None

    planner = LocalPlanner(FakeChecker(block=block), FakeIK())
    plan = planner.plan([0.0, 0.0, 0.5, 0.0, 0.0, 0.0], [goal()],
                        context(), start_pose=[0.0, 0.0, 0.5, 0, 0, 0, 1])
    # 直接路径中段被挡; HOME 中转在 z=-1.57 处经过, 应被接受
    assert plan.ok
    assert plan.status is PlanStatus.AVOIDED
    assert plan.strategy != "direct"


def test_all_strategies_blocked_returns_failure_without_partial_path():
    def block(q):
        return (PlanStatus.PATH_COLLISION, "always blocked")

    planner = LocalPlanner(FakeChecker(block=block), FakeIK())
    plan = planner.plan([0.0, 0.0, 0.5, 0.0, 0.0, 0.0], [goal()],
                        context(), start_pose=[0.0, 0.0, 0.5, 0, 0, 0, 1])
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
    plan = planner.plan([0.0, 0.0, 0.5, 0.0, 0.0, 0.0], [goal()],
                        context(), start_pose=[0.0, 0.0, 0.5, 0, 0, 0, 1])
    assert not plan.ok
    assert plan.status is PlanStatus.QUERY_UNKNOWN


def test_budget_exhaustion_is_not_success():
    planner = LocalPlanner(FakeChecker(fail_after=3), FakeIK())
    plan = planner.plan([0.0, 0.0, 0.5, 0.0, 0.0, 0.0], [goal()],
                        context(), start_pose=[0.0, 0.0, 0.5, 0, 0, 0, 1])
    assert not plan.ok
    assert plan.status is PlanStatus.PLAN_BUDGET_EXCEEDED
