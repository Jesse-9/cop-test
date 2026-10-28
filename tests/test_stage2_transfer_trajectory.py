"""06-15 水平带载转运与放置下探: 控制器状态/目标/失败归因测试.

全部使用轻量 fake, 不依赖 CoppeliaSim:
  - LIFTING → TRANSFERRING → PLACING 的状态边界;
  - TRANSFERRING 唯一目标 pre_place 的固定高度/姿态;
  - PLACING 的 pre_place → place 法向下探与最终接触许可;
  - AGV 未到站/未停稳时不生成下探命令;
  - 下探规划失败归因到 PLACING.
"""

import math

from stage1 import config as base
from stage1.unit_controller import UnitTaskState
from stage2 import config as cfg
from stage2.path_planning import MotionPlan, PlanStatus
from stage2.unit_controller import Stage2UnitController

PLACE = [-0.2, 1.2, 0.4, 0.0, 0.0, 0.0, 1.0]
START_TCP = [-0.3, 0.0, cfg.TRANSFER_HEIGHT, 0.0, 0.0, 0.0, 1.0]


class FakeMaterials:
    def by_uid(self, _uid):
        return None


class FakeRuntime:
    def __init__(self):
        self.carrying = None
        self.attach_transform = None

    def update_materials(self, _materials, _uid, carrying):
        self.carrying = bool(carrying)

    def set_attach_transform(self, transform):
        self.attach_transform = transform


class FakeSuction:
    attach_transform = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0]


class FakePlanner:
    def __init__(self, status=PlanStatus.OK, waypoints=None,
                 fail_direct=False):
        self.status = status
        self.waypoints = waypoints or [[0.0] * 6, [0.1] * 6]
        self.fail_direct = fail_direct
        self.calls = []

    def plan(self, q, goals, context, start_pose=None, **kwargs):
        self.calls.append({
            "q": list(q), "goals": list(goals), "context": context,
            "start_pose": None if start_pose is None else list(start_pose),
            "kwargs": dict(kwargs),
        })
        status = self.status
        message = "fake plan"
        if self.fail_direct and len(goals) == 1:
            status = PlanStatus.GOAL_CONFIG_INVALID
            message = "direct path blocked"
        return MotionPlan(status, waypoints=[list(w)
                                             for w in self.waypoints],
                          contexts=[context] * len(self.waypoints),
                          message=message)


class FixedHeightChecker:
    """TCP = q 的前三维 (z 固定为 TRANSFER_HEIGHT) + PLACE 姿态."""

    def __init__(self, height=None, quaternion=None):
        self.height = cfg.TRANSFER_HEIGHT if height is None else float(height)
        self.quaternion = list(PLACE[3:7] if quaternion is None
                               else quaternion)

    def tcp_at(self, values):
        return [float(values[0]), float(values[1]), self.height,
                *self.quaternion]


class FakeAGV:
    def __init__(self, stopped=True, upright=True):
        self.stopped = stopped
        self.upright = upright

    def is_stopped(self):
        return self.stopped

    def is_upright(self):
        return self.upright


class FakeScene:
    agv_root = 1

    def __init__(self, docked=True):
        self.docked = docked

    def agv_dock_hit(self, _handle):
        return self.docked


def bare_controller():
    controller = object.__new__(Stage2UnitController)
    controller.state = UnitTaskState.IDLE
    controller.sim_time = 0.0
    controller.target = "station"
    controller.uid = 1
    controller.layout = None
    controller.materials = FakeMaterials()
    controller.suction = FakeSuction()
    controller.runtime = FakeRuntime()
    controller._dock_wait_since = None
    controller._placing_ready = False
    controller._plan = None
    controller._last_plan = None
    controller._executor = None
    controller._motion_reports = []
    controller._motion_reported = False
    controller._transfer_check = None
    controller.events = []
    controller.failures = []
    controller._event = lambda message: controller.events.append(message)
    controller._fail = lambda reason: controller.failures.append(reason)
    return controller


# ── 状态边界 ────────────────────────────────────────────────

def test_motion_state_order_is_lifting_transferring_placing():
    controller = bare_controller()
    calls = []
    controller._begin_motion = lambda state, reason="": calls.append(
        ("begin", state))
    controller._enter = lambda state, reason: calls.append(
        ("enter", state))

    expected = {
        UnitTaskState.LIFTING: ("begin", UnitTaskState.TRANSFERRING),
        UnitTaskState.TRANSFERRING: ("begin", UnitTaskState.PLACING),
        UnitTaskState.PLACING: ("enter", UnitTaskState.RELEASING),
    }
    for state, outcome in expected.items():
        controller.state = state
        controller._finish_motion_state()
        assert calls[-1] == outcome


def test_placing_keeps_carrying_context():
    controller = bare_controller()
    controller._begin_motion(UnitTaskState.PLACING, reason="transfer done")
    assert controller.state is UnitTaskState.PLACING
    assert controller.runtime.carrying is True
    assert controller.runtime.attach_transform is not None
    assert controller._placing_ready is False


# ── 目标几何 ────────────────────────────────────────────────

def test_lifting_goal_ends_at_transfer_height():
    controller = bare_controller()
    controller.state = UnitTaskState.LIFTING
    controller._pick_pose = [-0.3, 0.0, 0.5, 0.0, 0.0, 0.0, 1.0]
    controller._pick_box = _unit_box()
    controller._planning_start = lambda: ([0.0] * 6, START_TCP, "test")
    controller._place_pose = lambda: list(PLACE)
    planner = FakePlanner()
    controller.planner = planner

    plan = controller._build_plan()
    goal = planner.calls[0]["goals"][0]
    assert goal.name == "lift"
    assert abs(goal.pose[2] - cfg.TRANSFER_HEIGHT) < 1e-9
    assert goal.pose[3:7] == PLACE[3:7]
    assert plan is not None


def test_transferring_plan_only_targets_pre_place():
    controller = bare_controller()
    controller.state = UnitTaskState.TRANSFERRING
    controller._place_pose = lambda: list(PLACE)
    controller.checker = FixedHeightChecker()
    planner = FakePlanner()
    controller.planner = planner

    plan = controller._plan_transfer([0.0] * 6, START_TCP, "test")
    call = planner.calls[0]
    assert [goal.name for goal in call["goals"]] == ["pre_place"]
    pre = call["goals"][0]
    assert pre.cartesian is True
    assert pre.strict_orientation is True
    assert pre.allow_final_load_contact is False
    assert abs(pre.pose[2] - cfg.TRANSFER_HEIGHT) < 1e-9
    assert pre.pose[0] == PLACE[0] and pre.pose[1] == PLACE[1]
    assert call["context"].phase == "TRANSFER"
    assert call["context"].carrying is True
    assert call["start_pose"] == START_TCP
    assert call["kwargs"].get("direct_only") is True
    assert call["kwargs"].get("wall_s") == cfg.TRANSFER_PLAN_WALL_BUDGET_S
    assert plan.transfer_path == "horizontal_direct"
    assert plan.metrics["horizontal_check"]["ok"] is True


def test_transferring_plan_falls_back_to_fixed_height_detour():
    controller = bare_controller()
    controller.state = UnitTaskState.TRANSFERRING
    controller._place_pose = lambda: list(PLACE)
    controller.checker = FixedHeightChecker()
    planner = FakePlanner(fail_direct=True)
    controller.planner = planner

    plan = controller._plan_transfer([0.0] * 6, START_TCP, "test")
    assert plan.ok is True
    assert plan.transfer_path.startswith("horizontal_detour_")
    assert len(planner.calls) == 2
    direct, detour = planner.calls
    assert [goal.name for goal in direct["goals"]] == ["pre_place"]
    assert [goal.name for goal in detour["goals"]] == [
        "detour", "pre_place"]
    for goal in detour["goals"]:
        assert abs(goal.pose[2] - cfg.TRANSFER_HEIGHT) < 1e-9
    assert detour["kwargs"].get("direct_only") is True
    offset = float(plan.transfer_path.rsplit("_", 1)[-1])
    assert abs(offset) == cfg.TRANSFER_HORIZONTAL_DETOUR_OFFSETS[0]
    assert plan.transfer_variants[0]["variant"] == "horizontal_direct"
    assert plan.transfer_variants[0]["status"] == \
        PlanStatus.GOAL_CONFIG_INVALID.value
    assert plan.transfer_variants[1]["horizontal_ok"] is True


def test_transfer_detour_stays_at_height_and_is_perpendicular():
    from stage2.unit_controller import transfer_detour_pose

    via = transfer_detour_pose(PLACE, START_TCP, 0.30)
    assert abs(via[2] - cfg.TRANSFER_HEIGHT) < 1e-9
    assert via[3:7] == PLACE[3:7]
    mid_x = (START_TCP[0] + PLACE[0]) / 2.0
    mid_y = (START_TCP[1] + PLACE[1]) / 2.0
    dx, dy = PLACE[0] - START_TCP[0], PLACE[1] - START_TCP[1]
    norm = math.hypot(dx, dy)
    rel_x, rel_y = via[0] - mid_x, via[1] - mid_y
    assert abs(math.hypot(rel_x, rel_y) - 0.30) < 1e-9
    assert abs(rel_x * dx / norm + rel_y * dy / norm) < 1e-9


def test_transferring_plan_rejects_height_drift():
    controller = bare_controller()
    controller.state = UnitTaskState.TRANSFERRING
    controller._place_pose = lambda: list(PLACE)
    controller.checker = FixedHeightChecker(
        height=cfg.TRANSFER_HEIGHT + 0.03)
    controller.planner = FakePlanner()

    plan = controller._plan_transfer([0.0] * 6, START_TCP, "test")
    assert plan.ok is False
    assert plan.status is PlanStatus.PATH_DEVIATION
    assert "水平转运路径偏差" in plan.message
    check = plan.metrics["horizontal_check"]
    assert abs(check["max_z"] - 0.03) < 1e-9


def test_transferring_unknown_query_stops_variant_search():
    controller = bare_controller()
    controller.state = UnitTaskState.TRANSFERRING
    controller._place_pose = lambda: list(PLACE)
    controller.checker = FixedHeightChecker()
    planner = FakePlanner(status=PlanStatus.QUERY_UNKNOWN)
    controller.planner = planner

    plan = controller._plan_transfer([0.0] * 6, START_TCP, "test")
    assert plan.status is PlanStatus.QUERY_UNKNOWN
    assert len(planner.calls) == 1
    assert plan.transfer_variants[-1]["status"] == "QUERY_UNKNOWN"


def test_transferring_horizontal_check_unknown_stops_variant_search():
    class UnknownChecker:
        def tcp_at(self, values):
            return None

    controller = bare_controller()
    controller.state = UnitTaskState.TRANSFERRING
    controller._place_pose = lambda: list(PLACE)
    controller.checker = UnknownChecker()
    planner = FakePlanner()
    controller.planner = planner

    plan = controller._plan_transfer([0.0] * 6, START_TCP, "test")
    assert plan.status is PlanStatus.QUERY_UNKNOWN
    assert len(planner.calls) == 1
    assert "查询未知" in plan.message


def test_placing_plan_is_normal_descent_with_final_contact():
    controller = bare_controller()
    controller.state = UnitTaskState.PLACING
    controller._planning_start = lambda: ([0.0] * 6, START_TCP, "test")
    controller._place_pose = lambda: list(PLACE)
    planner = FakePlanner()
    controller.planner = planner

    controller._build_plan()
    call = planner.calls[0]
    goal = call["goals"][0]
    assert goal.name == "place"
    assert goal.pose == PLACE
    assert goal.cartesian is True
    assert goal.strict_orientation is True
    assert goal.allow_final_load_contact is True
    assert goal.lock_joint == cfg.Q0_LOCK_JOINT
    assert call["context"].phase == "PLACE"
    assert call["context"].carrying is True
    assert call["start_pose"] == START_TCP
    assert call["kwargs"].get("direct_only") is True
    assert call["kwargs"].get("wall_s") == cfg.TRANSFER_PLAN_WALL_BUDGET_S


# ── PLACING 前的目标设备检查 ────────────────────────────────

def _placing_controller(target="agv", docked=True, stopped=True,
                        upright=True):
    controller = bare_controller()
    controller.state = UnitTaskState.PLACING
    controller.target = target
    controller.sim_time = 0.0
    controller.scene = FakeScene(docked=docked)
    controller.agv = FakeAGV(stopped=stopped, upright=upright)
    calls = []
    controller._run_motion = lambda: calls.append("motion")
    return controller, calls


def test_placing_waits_for_agv_before_any_descent():
    controller, calls = _placing_controller(docked=False)
    controller._advance_placing()
    assert calls == []
    assert controller.failures == []
    assert controller._dock_wait_since == 0.0

    controller.sim_time = base.AGV_DOCK_TIMEOUT + 0.1
    controller._advance_placing()
    assert calls == []
    assert controller.failures
    assert "AGV_DOCK_TIMEOUT" in controller.failures[0]
    assert "未生成 pre_place → place 命令" in controller.failures[0]


def test_placing_waits_when_agv_not_stopped():
    controller, calls = _placing_controller(stopped=False)
    controller._advance_placing()
    assert calls == []

    controller.sim_time = base.AGV_DOCK_TIMEOUT + 0.1
    controller._advance_placing()
    assert calls == []
    assert controller.failures
    assert "AGV_DOCK_TIMEOUT" in controller.failures[0]


def test_placing_starts_descent_after_agv_confirmed():
    controller, calls = _placing_controller()
    controller._advance_placing()
    assert calls == ["motion"]
    assert controller._placing_ready is True
    assert controller.failures == []


def test_station_placing_needs_no_agv_check():
    controller, calls = _placing_controller(target="station")
    controller.scene = None
    controller.agv = None
    controller._advance_placing()
    assert calls == ["motion"]


# ── 失败归因 ────────────────────────────────────────────────

def test_placing_descent_failure_is_attributed_to_placing():
    controller = bare_controller()
    controller.state = UnitTaskState.PLACING
    controller._build_plan = lambda: MotionPlan(
        PlanStatus.PATH_COLLISION, message="descent blocked")
    controller._run_motion()
    assert controller._motion_reports
    report = controller._motion_reports[0]
    assert report["state"] == "PLACING"
    assert report["status"] == "PATH_COLLISION"
    assert controller.failures
    assert "PATH_COLLISION" in controller.failures[0]


def test_placing_unknown_query_enters_safety_latch():
    controller = bare_controller()
    controller.state = UnitTaskState.PLACING
    controller._build_plan = lambda: MotionPlan(
        PlanStatus.QUERY_UNKNOWN, message="collision query unknown")
    controller._safety_fail = lambda reason, details=None: \
        controller.failures.append("SAFETY_LATCH: " + reason)
    controller._run_motion()
    assert controller.failures
    assert controller.failures[0].startswith("SAFETY_LATCH:")
    assert controller._motion_reports[0]["status"] == "QUERY_UNKNOWN"


def _unit_box():
    from stage2.contact import LocalBox
    return LocalBox(minimum=(-0.05, -0.05, -0.05),
                    maximum=(0.05, 0.05, 0.05))
