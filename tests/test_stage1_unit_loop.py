"""阶段一单步控制循环测试 (§2.11 / §2.12).

用 fake sim / 传感器 / IK / 吸附 / AGV 验证:
  - 状态机按 §2.11 顺序推进
  - 传感器 UID 不匹配时不吸附
  - IK 不可达 → FAILED, 不调用吸附
  - 传感器无读数 → 超时 FAILED
  - AGV 目标时通过轮速接口, 不写车体位姿
  - sim.step() 永远是最后一步
"""

import pytest

from stage1 import config as cfg
from stage1.material_manager import MaterialManager, MaterialState
from stage1.unit_controller import Stage1UnitController, UnitTaskState
from stage1.suction_controller import ATTACH_OK, ReleaseResult, AttachResult


MAT = 100
WRONG = 200


class FakeSim:
    handle_world = -1
    simulation_stopped = 0

    def __init__(self):
        self.t = 0.0
        self.calls = []
        self.material_pose = [*cfg.MATERIAL_SPAWN_POS, 0, 0, 0, 1]
        self.steps = 0

    def getSimulationTime(self):
        return self.t

    def getSimulationState(self):
        return 4

    def getObjectPose(self, handle, rel=None):
        return list(self.material_pose)

    def getObjectPosition(self, handle, rel=None):
        return list(self.material_pose[:3])

    def setObjectPose(self, handle, pose, rel=None):
        self.calls.append(("setObjectPose", handle))
        self.material_pose = list(pose)

    def setObjectPosition(self, handle, pos, rel=None):
        self.calls.append("setObjectPosition")
        self.material_pose[:3] = list(pos)

    def step(self):
        self.calls.append("step")
        self.t += cfg.SIM_STEP
        self.steps += 1


class FakeScene:
    def __init__(self):
        self.pick_hits = set()
        self.suction_hits = set()
        self.part_root = 1
        self.agv_root = 300

    def pick_sensor_hits(self, handles):
        # 测试桩直接返回配置的命中集合 (可包含未注册的"错误物料")
        return sorted(self.pick_hits)

    def suction_sensor_hits(self, handles):
        return [h for h in handles if h in self.suction_hits]

    def agv_dock_hit(self, agv_handle):
        return True


class FakeIK:
    def __init__(self, ok=True):
        self.ok = ok
        self.solved = []
        self.applied = []
        self.command = [0.0] * 6

    def solve(self, pose):
        from stage1.ur5_ik_controller import IKResult
        self.solved.append(tuple(pose))
        if not self.ok:
            return IKResult(False, [], 0, 112, [0.4, 0.2], True, "IK 不可达")
        return IKResult(True, list(self.command), 1, 0, [0, 0], False, "ok")

    def apply_joints(self, joints):
        self.applied.append(list(joints))

    def converged(self, joints):
        return True

    def tcp_error(self, pose):
        return 0.0

    def tcp_speed(self):
        return 0.0

    def joints_within_limits(self):
        return True

    def stopped(self):
        return True

    def reached(self, pose):
        return True


class FakeSuction:
    def __init__(self, manager, reject=False):
        self.mgr = manager
        self.reject = reject
        self.attach_uids = []
        self.release_uids = []

    def attach(self, uid, sim_time):
        self.attach_uids.append(uid)
        if self.reject:
            from stage1.suction_controller import REJECT_NO_SENSOR_HIT
            return AttachResult(REJECT_NO_SENSOR_HIT, message="no hit")
        self.mgr.transition(uid, MaterialState.HELD_BY_SUCTION, sim_time, "fake attach")
        return AttachResult(ATTACH_OK)

    def release(self, uid, sim_time, pose, support_z=None):
        self.release_uids.append(uid)
        self.mgr.transition(uid, MaterialState.ON_TRAY, sim_time, "fake place")
        self.mgr.transition(uid, MaterialState.RELEASED, sim_time, "fake release")
        return ReleaseResult(True, stable=True)


class FakeConveyor:
    def __init__(self):
        self.speeds = []

    def set_speed(self, alias, v):
        self.speeds.append(v)

    def stop(self, alias):
        self.speeds.append(0.0)


class FakeAGV:
    def __init__(self):
        self.cmds = []
        self.stopped = 0
        self.x = 0.0
        self.moving = False

    def set_cmd(self, v, w):
        self.cmds.append((v, w))
        self.moving = True
        self.x += 0.5 * cfg.SIM_STEP

    def stop(self):
        self.stopped += 1
        self.moving = False

    def position(self):
        return [self.x, 0.0, 0.0]

    def is_stopped(self):
        return not self.moving

    def is_upright(self):
        return True


def _setup(target="station", pick_hit=True, ik_ok=True, reject_attach=False):
    sim = FakeSim()
    scene = FakeScene()
    mgr = MaterialManager()
    mgr.register(1, "Part_0001", sim_handle=MAT)
    mgr.transition(1, MaterialState.ON_CONVEYOR, 0.1, "belt")
    if pick_hit:
        scene.pick_hits.add(MAT)
    scene.suction_hits.add(MAT)
    ik = FakeIK(ok=ik_ok)
    suction = FakeSuction(mgr, reject=reject_attach)
    conveyor = FakeConveyor()
    agv = FakeAGV()
    from stage1.conveyor_controller import BeltGeometry
    geometry = BeltGeometry(cfg.CONVEYOR_ENTRY_WORLD, cfg.CONVEYOR_STOP_WORLD)
    c = Stage1UnitController(
        sim=sim, scene=scene, materials=mgr, conveyor=conveyor,
        belt_geometry=geometry, ik=ik, suction=suction, agv=agv,
        target=target, uid=1, pick_timeout=10.0, task_timeout=60.0,
    )
    return sim, scene, mgr, c, ik, suction, conveyor, agv


def _run_until_done(controller, sim, max_steps=400):
    for _ in range(max_steps):
        controller.step()
        if controller.state in (UnitTaskState.DONE, UnitTaskState.FAILED):
            return
    raise AssertionError(f"未在 {max_steps} 步内结束: {controller.state}")


def test_normal_station_flow_reaches_done():
    sim, scene, mgr, c, ik, suction, conveyor, agv = _setup("station")
    _run_until_done(c, sim)
    assert c.state is UnitTaskState.DONE
    assert mgr.by_uid(1).state is MaterialState.RELEASED
    assert suction.attach_uids == [1]
    assert suction.release_uids == [1]
    assert len(ik.solved) >= 3        # 取料 / 抬升 / 放置
    assert sim.calls[-1] == "step"
    assert any(v == 0.0 for v in conveyor.speeds)


def test_sim_step_is_last_every_step():
    sim, scene, mgr, c, ik, suction, conveyor, agv = _setup("station")
    for _ in range(5):
        c.step()
        assert sim.calls[-1] == "step"


def test_uid_mismatch_does_not_attach():
    sim, scene, mgr, c, ik, suction, conveyor, agv = _setup("station", pick_hit=False)
    scene.pick_hits.add(WRONG)
    _run_until_done(c, sim)
    assert c.state is UnitTaskState.FAILED
    assert suction.attach_uids == []
    assert c.last_mismatch is not None


def test_ik_failure_stops_before_attach():
    sim, scene, mgr, c, ik, suction, conveyor, agv = _setup("station", ik_ok=False)
    _run_until_done(c, sim)
    assert c.state is UnitTaskState.FAILED
    assert suction.attach_uids == []
    assert "IK" in c.failure_reason or "不可达" in c.failure_reason


def test_sensor_disabled_times_out_without_attach():
    sim, scene, mgr, c, ik, suction, conveyor, agv = _setup("station", pick_hit=False)
    _run_until_done(c, sim)
    assert c.state is UnitTaskState.FAILED
    assert suction.attach_uids == []
    assert "超时" in c.failure_reason or "sensor" in c.failure_reason.lower()


def test_attach_rejection_fails_task():
    sim, scene, mgr, c, ik, suction, conveyor, agv = _setup(
        "station", reject_attach=True)
    _run_until_done(c, sim)
    assert c.state is UnitTaskState.FAILED
    assert suction.attach_uids == [1]
    assert suction.release_uids == []


def test_agv_dock_timeout_prevents_release():
    sim, scene, mgr, c, ik, suction, conveyor, agv = _setup("agv")
    scene.agv_dock_hit = lambda h: False
    _run_until_done(c, sim)
    assert c.state is UnitTaskState.FAILED
    assert "AGV_DOCK_TIMEOUT" in c.failure_reason
    assert suction.release_uids == []


def test_agv_target_uses_wheel_commands_only():
    sim, scene, mgr, c, ik, suction, conveyor, agv = _setup("agv")
    _run_until_done(c, sim)
    assert c.state is UnitTaskState.DONE
    assert c.target == "agv"
    assert len(agv.cmds) > 0, "AGV 目标必须通过轮速命令驱动"
    # 只允许把物料本体放到载台; 绝不允许写 AGV 车体位姿
    assert all(call[1] == MAT for call in sim.calls
               if isinstance(call, tuple) and call[0] == "setObjectPose")
