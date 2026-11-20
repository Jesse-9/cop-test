"""固定动作路径复用: 记录版本、精确匹配与三种模式 (不需要 CoppeliaSim)."""

from __future__ import annotations

import json
import math

import pytest

from flexible_factory.simulation import config_motion as cfg
from flexible_factory.simulation.motion.contact import LocalBox
from flexible_factory.simulation.motion.path_library import (
    PATH_RECORD_VERSION, PR_LOOKUP, PR_PLAN_FALLBACK, PR_REPLAY,
    PathKey, PathLibrary, PathRecordError, goals_digest,
    motion_config_version, record_from_plan, start_digest)
from flexible_factory.simulation.motion.path_reuse import (PathReusePolicy,
                                                           mark_replay_done)
from flexible_factory.simulation.motion.plan_types import (
    Goal, MotionContext, MotionPlan, MotionSegment, PlanStatus)
from flexible_factory.simulation.unit_controller_base import UnitTaskState
from flexible_factory.simulation.unit_controller_motion import (
    Stage2UnitController)

SCENE = "v1/L5_UFLOW/v001_test"
PHASE = "PLACING:place"
START_Q = [0.10, -0.20, 0.30, 0.0, math.pi / 2, 0.0]
START_POSE = [0.2, 0.1, 0.5, 0.0, 0.0, 0.0, 1.0]
GOAL_POSE = [0.3, 0.1, 0.4, 0.0, 0.0, 0.0, 1.0]
PAYLOAD = "box:test"
TOOL = "UR5+suction_pad:cup=0.023580"


def make_goal(pose=None, lock_joint=0):
    return Goal("place", list(pose or GOAL_POSE), cartesian=True,
                strict_orientation=True, allow_final_load_contact=True,
                lock_joint=lock_joint)


def make_context():
    return MotionContext(phase="PLACE", target_handle=-1, carrying=True)


def make_plan(start_q=None, goals=None):
    start = [float(v) for v in (start_q or START_Q)]
    goal = (goals or [make_goal()])[-1]
    q0 = list(start)
    q1 = list(start)
    q1[0] += 0.05
    q2 = list(start)
    q2[0] += 0.10
    segment = MotionSegment(
        "place", q0, q2, cartesian=True, samples=2, waypoint_count=2,
        target_pose=list(goal.pose), lock_joint=goal.lock_joint,
        lock_value=q0[goal.lock_joint] if goal.lock_joint is not None else None,
        lock_tol=goal.lock_tol if goal.lock_joint is not None else None)
    return MotionPlan(
        PlanStatus.DIRECT_OK, waypoints=[q0, q1, q2], segments=[segment],
        goal_pose=list(goal.pose), final_contact=goal.final_contact,
        strict_orientation=goal.strict_orientation,
        allow_final_load_contact=goal.allow_final_load_contact,
        strategy="direct", branch_anchor="positive",
        lock_policy="lock" if goal.lock_joint is not None else "free",
        locked_segments=["place"] if goal.lock_joint is not None else [],
        samples=7, configs=9, wall_s=0.01)


class FakePlanner:
    def __init__(self):
        self.calls = 0

    def plan(self, start_q, goals, context, **kwargs):
        self.calls += 1
        return make_plan(start_q, goals)


def key_for(start_q=None, goals=None, **kwargs):
    start = [float(v) for v in (start_q or START_Q)]
    return PathKey(
        scene=kwargs.get("scene", SCENE),
        phase=kwargs.get("phase", PHASE),
        unit=kwargs.get("unit", "A"),
        robot=kwargs.get("robot", "UR5"),
        tool=kwargs.get("tool", TOOL),
        payload=kwargs.get("payload", PAYLOAD),
        start_digest=start_digest(start),
        targets_digest=goals_digest(goals or [make_goal()]),
        motion_version=motion_config_version())


def policy(library, mode, **kwargs):
    kwargs.setdefault("scene_artifact_id", SCENE)
    kwargs.setdefault("enabled_units", ("A",))
    return PathReusePolicy(library, mode, **kwargs)


def run(policy_obj, planner, goals=None, start_q=None, context=None,
        **overrides):
    kwargs = {"phase": PHASE, "unit_id": "A", "tool": TOOL,
              "payload": PAYLOAD, "start_pose": START_POSE,
              "direct_only": True, "wall_s": 10.0}
    kwargs.update(overrides)
    return policy_obj.plan(planner, start_q or START_Q,
                           goals or [make_goal()],
                           context or make_context(), **kwargs)


# ── 记录读写 ────────────────────────────────────────────────

def test_record_roundtrip_preserves_waypoints_and_static_fields(tmp_path):
    library = PathLibrary(tmp_path)
    key = key_for()
    plan = make_plan()
    record = record_from_plan(plan, key)
    assert record is not None
    path = library.put(record)
    loaded = library.get_exact(key)
    assert loaded is not None
    assert loaded.waypoints == [[round(float(v), 6) for v in q]
                                for q in plan.waypoints]
    assert loaded.segments[0]["name"] == "place"
    assert loaded.segments[0]["waypoint_count"] == 2
    assert loaded.branch_anchor == "positive"
    assert loaded.format_version == PATH_RECORD_VERSION
    assert path.parent.name == "PLACING_place"
    assert path.parent.parent.name == "v1_L5_UFLOW_v001_test"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "contexts" not in data
    assert "handle" not in path.read_text(encoding="utf-8")


def test_record_with_inconsistent_segment_counts_is_rejected():
    plan = make_plan()
    plan.segments[0].waypoint_count = 1
    assert record_from_plan(plan, key_for()) is None


def test_record_with_start_not_matching_key_is_rejected():
    plan = make_plan()
    other = list(START_Q)
    other[0] += 0.5
    assert record_from_plan(plan, key_for(start_q=other)) is None


def test_library_reports_missing_corrupted_and_version_mismatch(tmp_path):
    library = PathLibrary(tmp_path)
    key = key_for()
    assert library.get_exact(key) is None
    assert library.last_error == ""

    path = library.path_for(key)
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert library.get_exact(key) is None
    assert "JSONDecodeError" in library.last_error

    record = record_from_plan(make_plan(), key)
    assert record is not None
    library.put(record)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["format_version"] = "v0"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert library.get_exact(key) is None
    assert "版本不匹配" in library.last_error


def test_library_rejects_non_finite_values(tmp_path):
    library = PathLibrary(tmp_path)
    key = key_for()
    record = record_from_plan(make_plan(), key)
    assert record is not None
    path = library.path_for(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = record.as_dict()
    data["waypoints"][0][0] = float("nan")
    path.write_text(json.dumps(data), encoding="utf-8")
    assert library.get_exact(key) is None
    assert "非有限" in library.last_error


def test_library_rejects_record_at_path_with_other_key(tmp_path):
    library = PathLibrary(tmp_path)
    record = record_from_plan(make_plan(), key_for())
    assert record is not None
    library.put(record)
    other = key_for(goals=[make_goal(pose=[0.4, 0.1, 0.4,
                                           0.0, 0.0, 0.0, 1.0])])
    path = library.path_for(other)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record.as_dict()), encoding="utf-8")
    assert library.get_exact(other) is None
    assert "键" in library.last_error


def test_library_rejects_non_success_record(tmp_path):
    library = PathLibrary(tmp_path)
    record = record_from_plan(make_plan(), key_for())
    assert record is not None
    record.status = PlanStatus.PATH_COLLISION.value
    with pytest.raises(PathRecordError):
        library.put(record)


# ── 三种模式 ────────────────────────────────────────────────

def test_plan_only_ignores_cache_and_marks_online(tmp_path):
    library = PathLibrary(tmp_path)
    planner = FakePlanner()
    first = run(policy(library, "PLAN_ONLY", export=False), planner)
    assert first.ok
    assert first.plan_source == "online"
    assert planner.calls == 1
    assert first.replay["phases"] == []
    # 即使库里有记录, PLAN_ONLY 也不读取.
    exporter = policy(library, "PLAN_ONLY", export=True)
    run(exporter, planner)
    assert exporter.lookup_count == 0


def test_cache_hit_replays_without_calling_planner(tmp_path):
    library = PathLibrary(tmp_path)
    exporter = policy(library, "PLAN_ONLY", export=True)
    online_planner = FakePlanner()
    online = run(exporter, online_planner)
    assert online.ok and online_planner.calls == 1

    cache_planner = FakePlanner()
    replay = run(policy(library, "CACHE_ONLY"), cache_planner)
    assert replay.ok
    assert replay.plan_source == "cache"
    assert cache_planner.calls == 0
    assert replay.status is PlanStatus.OK
    assert replay.replay["phases"] == [PR_LOOKUP, PR_REPLAY]
    assert replay.replay["record_id"]
    assert replay.replay["original_strategy"] == "direct"
    assert replay.waypoints[0] == pytest.approx(START_Q)
    assert replay.waypoints[-1][0] == pytest.approx(START_Q[0] + 0.10)
    # 回放使用当前实时 context, 不用记录中的旧 context.
    assert replay.contexts[0].phase == "PLACE"
    assert replay.contexts[-1].goal_pose == GOAL_POSE
    assert replay.contexts[-1].allow_final_load_contact is True
    assert replay.segments[0].waypoint_count == 2
    assert replay.segments[0].lock_joint == 0


def test_cache_only_miss_fails_without_planner(tmp_path):
    planner = FakePlanner()
    plan = run(policy(PathLibrary(tmp_path), "CACHE_ONLY"), planner)
    assert plan.status is PlanStatus.PATH_REPLAY_MISS
    assert not plan.ok
    assert plan.plan_source == "cache"
    assert planner.calls == 0
    assert "未命中" in plan.message
    assert plan.replay["phases"] == [PR_LOOKUP]


def test_cache_then_plan_calls_planner_once_then_hits(tmp_path):
    library = PathLibrary(tmp_path)
    planner = FakePlanner()
    policy_obj = policy(library, "CACHE_THEN_PLAN", export=True)
    first = run(policy_obj, planner)
    assert first.ok
    assert first.plan_source == "online"
    assert planner.calls == 1
    assert first.replay["phases"] == [PR_LOOKUP, PR_PLAN_FALLBACK]
    assert first.replay["fallback_reason"]
    assert first.replay["export"]["saved"] is True

    second = run(policy_obj, planner)
    assert second.ok
    assert second.plan_source == "cache"
    assert planner.calls == 1
    assert second.replay["phases"] == [PR_LOOKUP, PR_REPLAY]


def test_goal_change_is_a_cache_miss(tmp_path):
    library = PathLibrary(tmp_path)
    planner = FakePlanner()
    run(policy(library, "PLAN_ONLY", export=True), planner)
    other_goal = make_goal(pose=[0.4, 0.1, 0.4, 0.0, 0.0, 0.0, 1.0])
    plan = run(policy(library, "CACHE_ONLY"), planner,
               goals=[other_goal])
    assert plan.status is PlanStatus.PATH_REPLAY_MISS
    assert planner.calls == 1


def test_start_outside_execution_tolerance_is_a_cache_miss(tmp_path):
    library = PathLibrary(tmp_path)
    key = key_for()
    record = record_from_plan(make_plan(), key)
    assert record is not None
    record.start_q[0] += 2.0 * cfg.EXEC_JOINT_TOL
    record.waypoints[0][0] += 2.0 * cfg.EXEC_JOINT_TOL
    library.put(record)

    plan = run(policy(library, "CACHE_ONLY"), FakePlanner())
    assert plan.status is PlanStatus.PATH_REPLAY_MISS
    assert "起始关节偏差" in plan.replay["miss_reason"]


def test_branch_mismatch_is_a_cache_miss(tmp_path):
    library = PathLibrary(tmp_path)
    key = key_for()
    record = record_from_plan(make_plan(), key)
    assert record is not None
    record.branch_anchor = "negative"
    library.put(record)

    plan = run(policy(library, "CACHE_ONLY"), FakePlanner())
    assert plan.status is PlanStatus.PATH_REPLAY_MISS
    assert "腕部分支" in plan.replay["miss_reason"]


def test_multi_goal_replay_is_rejected(tmp_path):
    library = PathLibrary(tmp_path)
    goals = [make_goal(), make_goal(pose=[0.5, 0.1, 0.4,
                                          0.0, 0.0, 0.0, 1.0])]
    record = record_from_plan(make_plan(goals=goals),
                              key_for(goals=goals))
    assert record is not None
    library.put(record)
    plan = run(policy(library, "CACHE_ONLY"), FakePlanner(), goals=goals)
    assert plan.status is PlanStatus.PATH_REPLAY_MISS
    assert "单目标" in plan.replay["miss_reason"]


def test_policy_applies_only_to_enabled_unit_and_phase(tmp_path):
    policy_obj = policy(PathLibrary(tmp_path), "CACHE_ONLY")
    assert policy_obj.applies("A", "PLACING")
    assert not policy_obj.applies("B", "PLACING")
    assert not policy_obj.applies("A", "LIFTING")


def test_default_policy_is_limited_to_arm_a_placing(tmp_path):
    policy_obj = PathReusePolicy(PathLibrary(tmp_path), "CACHE_ONLY",
                                 scene_artifact_id=SCENE)
    assert policy_obj.applies("A", "PLACING")
    assert not policy_obj.applies("B", "PLACING")
    assert not policy_obj.applies("C", "PLACING")


def test_policy_requires_scene_artifact_id(tmp_path):
    with pytest.raises(ValueError):
        PathReusePolicy(PathLibrary(tmp_path), "CACHE_ONLY",
                        scene_artifact_id="")


def test_mark_replay_done_is_idempotent(tmp_path):
    library = PathLibrary(tmp_path)
    planner = FakePlanner()
    plan = run(policy(library, "PLAN_ONLY"), planner)
    plan.replay = {"phases": []}
    mark_replay_done(plan)
    mark_replay_done(plan)
    assert plan.replay["phases"] == ["PR_DONE"]


def test_motion_plan_dict_exposes_source_and_timings():
    plan = make_plan()
    plan.plan_source = "cache"
    plan.timings = {"search_wall_s": 0.0, "verify_wall_s": 0.0,
                    "postprocess_wall_s": 0.0}
    plan.wall_s = 0.1
    data = plan.as_dict()
    assert data["plan_source"] == "cache"
    assert data["timings"]["search_wall_s"] == 0.0
    assert data["wall_s"] == 0.1
    assert "contexts" not in data


# ── 控制器 PLACING 接入 ────────────────────────────────────

class _NoMaterials:
    def by_uid(self, _uid):
        return None


class _Runtime:
    cup_radius = cfg.CUP_RADIUS_DESIGN


class ControllerPlanner(FakePlanner):
    pass


def bare_controller():
    controller = object.__new__(Stage2UnitController)
    controller.state = UnitTaskState.PLACING
    controller.sim_time = 0.0
    controller.uid = 1
    controller.target = "station"
    controller.unit_id = "A"
    controller.materials = _NoMaterials()
    controller.runtime = _Runtime()
    controller._pick_box = LocalBox.from_size((0.10, 0.10, 0.10))
    controller._plan = None
    controller._last_plan = None
    controller._executor = None
    controller._motion_reports = []
    controller._motion_reported = False
    controller.events = []
    controller.failures = []
    controller._event = lambda message: controller.events.append(message)
    controller._fail = lambda reason: controller.failures.append(reason)
    controller._planning_start = lambda: (list(START_Q), list(START_POSE),
                                          "test")
    controller._place_pose = lambda: list(GOAL_POSE)
    return controller


def controller_goal():
    return Goal("place", list(GOAL_POSE), cartesian=True,
                strict_orientation=True, allow_final_load_contact=True,
                lock_joint=cfg.Q0_LOCK_JOINT)


def controller_key(controller):
    return PathKey(
        scene=SCENE, phase=PHASE, unit="A", robot="UR5",
        tool=controller._tool_id(), payload=controller._payload_id(),
        start_digest=start_digest(START_Q),
        targets_digest=goals_digest([controller_goal()]),
        motion_version=motion_config_version())


def test_controller_placing_replays_before_calling_planner(tmp_path):
    library = PathLibrary(tmp_path)
    controller = bare_controller()
    goal = controller_goal()
    record = record_from_plan(make_plan(goals=[goal]),
                              controller_key(controller))
    assert record is not None
    library.put(record)

    planner = ControllerPlanner()
    controller.planner = planner
    controller._path_reuse = PathReusePolicy(
        library, "CACHE_ONLY", scene_artifact_id=SCENE,
        enabled_units=("A",))
    plan = controller._build_plan()
    assert plan.ok
    assert plan.plan_source == "cache"
    assert plan.replay["phases"] == [PR_LOOKUP, PR_REPLAY]
    assert planner.calls == 0


def test_controller_placing_cache_miss_does_not_call_planner(tmp_path):
    controller = bare_controller()
    planner = ControllerPlanner()
    controller.planner = planner
    controller._path_reuse = PathReusePolicy(
        PathLibrary(tmp_path), "CACHE_ONLY", scene_artifact_id=SCENE,
        enabled_units=("A",))
    plan = controller._build_plan()
    assert plan.status is PlanStatus.PATH_REPLAY_MISS
    assert planner.calls == 0


def test_controller_without_policy_keeps_original_planner_call(tmp_path):
    controller = bare_controller()
    planner = ControllerPlanner()
    controller.planner = planner
    plan = controller._build_plan()
    assert plan.ok
    assert plan.plan_source == ""
    assert planner.calls == 1