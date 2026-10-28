"""阶段 2.02/06-15: 整链搜索、逐点诊断、平滑与工件契约测试.

覆盖分层代价、有界整链 beam 选链、逐点诊断、平滑与契约字段. 全部使用
fake IK/checker, 不依赖 CoppeliaSim. 06-15 已移除 rise/transit 路径变体,
本文件不再覆盖旧版 A/B 开关.
"""

import math

from stage1.ur5_ik_controller import IKResult
from stage2 import config as cfg
from stage2.branch import POSITIVE
from stage2.collisions import QueryResult, QueryState
from stage2.diagnostics import build_plan_trace
from stage2.metrics import (chain_cost, chain_cost_increment,
                            direction_reversals, motion_profile)
from stage2.path_planning import (ConfigCheck, Goal, LocalPlanner,
                                  MotionContext, MotionPlan, MotionSegment,
                                  PlanStatus, SegmentCheck,
                                  smooth_plan_waypoints)
from stage2.scene_contract import build_stage2_extra
from stage2.trajectory import ExecutorStatus, JointStreamExecutor

WRIST_UP = math.pi / 2


def q(q3=0.0, q0=0.0):
    return [q0, 0.0, 0.0, q3, WRIST_UP, 0.0]


# ── 分层代价 ────────────────────────────────────────────────

def test_chain_cost_matches_documented_formula():
    samples = [q(), q(q3=0.2, q0=0.1), q(q3=0.4, q0=0.2)]
    cost = chain_cost(samples)
    assert abs(cost["tv_total"] - 0.6) < 1e-9
    assert abs(cost["tv_q0"] - 0.2) < 1e-9
    assert abs(cost["tv_q3"] - 0.4) < 1e-9
    assert cost["b_all"] == 0.0 and cost["a_all"] == 0.0
    expected = 0.6 + 0.5 * 0.2 + 0.5 * 0.4
    assert abs(cost["cost"] - expected) < 1e-9


def test_chain_cost_increment_accumulates_to_full_cost():
    samples = [q(), q(q3=0.2, q0=0.1), q(q3=0.4, q0=0.2)]
    first = chain_cost(samples[:2])
    increment = chain_cost_increment(samples[:2], samples[2])
    total = chain_cost(samples)
    assert abs(first["cost"] + increment["cost"] - total["cost"]) < 1e-9
    for key in ("tv_total", "tv_q0", "tv_q3", "b_all", "a_all"):
        assert abs(first[key] + increment[key] - total[key]) < 1e-9


def test_chain_cost_does_not_wrap_joint_angles():
    samples = [q(q0=4.7), q(q0=4.9)]
    cost = chain_cost(samples)
    assert abs(cost["tv"][0] - 0.2) < 1e-9


def test_direction_reversals_ignore_zero_delta():
    samples = [q(q0=0.0), q(q0=1.0), q(q0=1.0), q(q0=0.5),
               q(q0=0.6)]
    assert direction_reversals(samples)[0] == 2


def test_motion_profile_reports_velocity_acceleration_and_limits():
    samples = [{"t": 0.0, "q": q(q0=0.0)},
               {"t": 0.1, "q": q(q0=0.1)},
               {"t": 0.2, "q": q(q0=0.2)}]
    profile = motion_profile(samples, limits={"vel": 2.0, "acc": 10.0,
                                              "jerk": 100.0})
    assert abs(profile["max"]["vel"] - 1.0) < 1e-9
    assert profile["max"]["acc"] == 0.0
    assert profile["max"]["jerk"] == 0.0
    assert profile["within_limits"] == {"vel": True, "acc": True,
                                        "jerk": True}


# ── 有界整链候选选择 ────────────────────────────────────────

class ChainIK:
    """第 1 层返回两条分支; 之后按 seed 返回, 用于区分 greedy/beam."""

    def solve_candidates(self, pose, seeds, max_candidates=6,
                         dedupe_tol=0.05, **_kwargs):
        x = float(pose[0])
        out: list[list[float]] = []
        if x < 0.15:
            out = [[x, 0.0, 0.0, 0.0, WRIST_UP, 0.0],
                   [x, 0.0, 0.0, 0.5, WRIST_UP, 0.0]]
        else:
            for seed in seeds:
                q3 = 0.5 if abs(float(seed[3]) - 0.5) < 0.1 else 0.0
                out.append([x, 0.0, 0.0, q3, WRIST_UP, 0.0])
        unique: list[list[float]] = []
        for value in out:
            if any(math.dist(value, known) <= dedupe_tol
                   for known in unique):
                continue
            unique.append(value)
        return [IKResult(True, value, 1, 0, [0.0, 0.0], False, "ok")
                for value in unique[:max_candidates]]


class ChainChecker:
    """x>0.15 时 q[3]≈0 的构型被拒绝, 迫使路径在中途切换分支."""

    def __init__(self):
        self.budget = None

    def tcp_at(self, values):
        return [float(values[0]), float(values[1]), float(values[2]),
                0.0, 0.0, 0.0, 1.0]

    def _result(self, values):
        if float(values[0]) > 0.15 and abs(float(values[3])) < 0.1:
            return ConfigCheck(PlanStatus.PATH_COLLISION, list(values),
                               tcp=self.tcp_at(values), message="blocked")
        return ConfigCheck(PlanStatus.OK, list(values),
                           tcp=self.tcp_at(values))

    def check(self, values, context, **_kwargs):
        return self._result(values)

    def check_segment(self, q0, q1, context, **_kwargs):
        for values in (q0, q1, [(float(a) + float(b)) / 2
                                for a, b in zip(q0, q1)]):
            result = self._result(values)
            if not result.ok:
                return SegmentCheck(result.status, list(q0), list(q1),
                                    samples=3, first_failure=result,
                                    message=result.message)
        return SegmentCheck(PlanStatus.OK, list(q0), list(q1), samples=3)


def _chain_planner():
    return LocalPlanner(ChainChecker(), ChainIK())


def test_greedy_chain_cannot_recover_from_dead_end():
    planner = _chain_planner()
    goal = Goal("place", [0.3, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
                cartesian=True)
    ok, chain, status, _message = planner._cartesian_chain_greedy(
        q(), [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0], goal,
        MotionContext(phase="PLACE", carrying=True))
    assert not ok
    assert status is PlanStatus.PATH_COLLISION
    assert planner.last_chain_search["mode"] == "greedy"


def test_beam_selects_feasible_low_cost_chain(monkeypatch):
    monkeypatch.setattr(cfg, "CARTESIAN_BEAM_WIDTH", 4)
    monkeypatch.setattr(cfg, "CARTESIAN_CHAIN_MAX_NODES", 4096)
    planner = _chain_planner()
    goal = Goal("place", [0.3, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
                cartesian=True)
    ok, chain, status, _message = planner._cartesian_chain_beam(
        q(), [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0], goal,
        MotionContext(phase="PLACE", carrying=True))
    assert ok, planner.last_chain_search
    assert status is PlanStatus.OK
    assert abs(chain[-1][3] - 0.5) < 1e-9
    stats = planner.last_chain_search
    assert stats["mode"] == "beam"
    assert stats["layers"] > 0
    assert stats["final_cost"] is not None
    assert stats["alternatives"]


def test_beam_reports_failure_when_every_branch_blocked(monkeypatch):
    class BlockAll(ChainChecker):
        def _result(self, values):
            return ConfigCheck(PlanStatus.PATH_COLLISION, list(values),
                               tcp=self.tcp_at(values), message="always")

    planner = LocalPlanner(BlockAll(), ChainIK())
    goal = Goal("place", [0.3, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
                cartesian=True)
    ok, _chain, status, message = planner._cartesian_chain_beam(
        q(), [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0], goal,
        MotionContext(phase="PLACE", carrying=True))
    assert not ok
    assert status is PlanStatus.PATH_COLLISION
    assert "无可行候选" in message
    assert planner.last_chain_search["failure_layer"] == 1


# ── 逐点诊断 ────────────────────────────────────────────────

class _Probe:
    def __init__(self):
        self.values = [0.0] * 6

    def set_configuration(self, values):
        self.values = [float(v) for v in values]

    def tcp_pose(self):
        return [self.values[0], self.values[1], self.values[2],
                0.0, 0.0, 0.0, 1.0]

    def load_pose(self):
        return [self.values[0], self.values[1], self.values[2] - 0.05,
                0.0, 0.0, 0.0, 1.0]


class _Collisions:
    def distance_margin(self, _threshold):
        return QueryResult("robot_distance_margin", QueryState.CLEAR,
                           distance=0.05)

    def load_distance_margin(self, _threshold):
        return QueryResult("load_distance_margin", QueryState.CLEAR,
                           distance=0.02)


class _TraceChecker(_Collisions):
    def __init__(self):
        self.probe = _Probe()
        self.collisions = _Collisions()

    def set_configuration(self, values):
        self.probe.set_configuration(values)
        return None

    def tcp_at(self, values):
        self.probe.set_configuration(values)
        return self.probe.tcp_pose()


def test_plan_trace_records_per_waypoint_evidence():
    waypoints = [q(), q(q0=0.1), q(q0=0.2)]
    segment = MotionSegment("lift", waypoints[0], waypoints[-1],
                            cartesian=True, waypoint_count=2,
                            target_pose=[0.2, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0])
    context = MotionContext(phase="LIFT", carrying=True)
    plan = MotionPlan(PlanStatus.OK, waypoints=waypoints,
                      segments=[segment], contexts=[context] * 3)
    trace = build_plan_trace(plan, _TraceChecker())
    assert trace["enabled"] is True
    assert trace["points"] == 3
    assert trace["waypoints"][0]["q"] == [round(v, 6) for v in waypoints[0]]
    assert trace["waypoints"][0]["wrist_branch"] == POSITIVE
    assert trace["waypoints"][0]["tcp"] is not None
    assert trace["waypoints"][0]["load_pose"] is not None
    assert trace["waypoints"][0]["min_workcell_distance"] == 0.05
    assert trace["waypoints"][0]["min_load_distance"] == 0.02
    assert "tcp_error_pos" in trace["waypoints"][-1]


# ── 平滑 (06-10 §8.3.5) ─────────────────────────────────────

def test_smoothing_preserves_endpoints_and_reports_deviation(monkeypatch):
    monkeypatch.setattr(cfg, "TRAJECTORY_SMOOTHING_ENABLED", True)
    monkeypatch.setattr(cfg, "TRAJECTORY_SMOOTHING_PASSES", 1)
    monkeypatch.setattr(cfg, "TRAJECTORY_SMOOTHING_WEIGHT", 0.25)
    waypoints = [q(q0=0.0), q(q0=0.1), q(q0=0.0), q(q0=0.2)]
    waypoints[1][1] = 0.5
    segment = MotionSegment("transit", waypoints[0], waypoints[-1],
                            waypoint_count=3)
    plan = MotionPlan(PlanStatus.OK, waypoints=waypoints,
                      segments=[segment], contexts=[object()] * 4)
    stats = smooth_plan_waypoints(plan, _TraceChecker())
    assert stats["applied"] is True
    assert stats["changed_points"] == 2
    assert plan.waypoints[0] == waypoints[0]
    assert plan.waypoints[-1] == waypoints[-1]
    assert abs(plan.waypoints[1][1] - 0.25) < 1e-9
    assert stats["max_tcp_pos_deviation"] > 0.0
    assert stats["_original_waypoints"]


def test_smoothing_is_off_by_default():
    plan = MotionPlan(PlanStatus.OK, waypoints=[q(), q(q0=0.1)],
                      segments=[], contexts=[])
    stats = smooth_plan_waypoints(plan, _TraceChecker())
    assert stats["enabled"] is False
    assert stats["applied"] is False


# ── 执行剖面与采样 trace ────────────────────────────────────

class _OkChecker:
    def check_segment(self, q0, q1, context, **_kwargs):
        return SegmentCheck(PlanStatus.OK, list(q0), list(q1), samples=2)


def test_executor_reports_profile_and_trace():
    waypoints = [q(q0=0.0), q(q0=0.1), q(q0=0.2)]
    segment = MotionSegment("place", waypoints[0], waypoints[-1],
                            cartesian=True, waypoint_count=2)
    plan = MotionPlan(PlanStatus.OK, waypoints=waypoints,
                      segments=[segment],
                      contexts=[object()] * 3)
    executor = JointStreamExecutor(plan, _OkChecker(), plan.contexts,
                                   max_joint_step=0.05,
                                   pose_at=lambda v: [v[0], v[1], v[2],
                                                      0.0, 0.0, 0.0, 1.0])
    actual = list(waypoints[0])
    step = executor.next_command(actual, 0.0)
    for index in range(1, 200):
        if step.done:
            break
        actual = list(step.q)
        step = executor.next_command(actual, index * 0.05)
    assert step.status is ExecutorStatus.DONE
    metrics = executor.execution_metrics()
    assert metrics["trace"]["enabled"] is True
    assert metrics["trace"]["samples"]["points"] > 0
    assert metrics["totals"]["profile"]["points"] > 0
    assert "within_limits" in metrics["segments"][0]["profile"]


# ── 工件契约 ────────────────────────────────────────────────

def test_stage2_contract_records_horizontal_transfer_trajectory():
    extra = build_stage2_extra(parent_info={"artifact_id":
                                            "stage1/L1_S1/v002"})
    planning = extra["planning"]
    transfer = planning["transfer"]
    assert transfer["height"] == cfg.TRANSFER_HEIGHT
    assert transfer["path"] == "fixed_height_horizontal_cartesian"
    assert transfer["lift_ends_at_transfer_height"] is True
    assert transfer["placing_descends_from_pre_place"] is True
    assert transfer["horizontal_detour_offsets"] == list(
        cfg.TRANSFER_HORIZONTAL_DETOUR_OFFSETS)
    assert transfer["plan_wall_budget_s"] == cfg.TRANSFER_PLAN_WALL_BUDGET_S
    assert "agv_transfer_opt" not in planning
    assert "transfer_split" not in planning
    assert planning["chain_search"]["mode"] == "greedy"
    assert planning["smoothing"]["enabled"] is False
    assert extra["controller"]["transfer_trajectory_version"] == "06-15"
    assert extra["contract_version"].endswith("_v4")
