"""tests/test_step1_1_pickup.py — A/B/C 试验状态机的 fake-sim 测试.

验证事件顺序与门槛, 不依赖 CoppeliaSim:
  - B: sensor → UID → 零速 → pad 停止 → 物料稳定 → 读位姿 → IK;
  - A: 传感器命中不触发停带, 行程进度 1.0 才发零速;
  - C: 关闭补偿, 物料不移动, 只记录诊断;
  - pad 未停 / 物料未稳 / 越界 / UID 误配 / IK 前置检查失败。
"""

import pytest

from stage1 import config as base
from stage1.conveyor_controller import BeltGeometry
from stage1.material_manager import MaterialManager
from stage1.suction_controller import ATTACH_OK, AttachResult, ReleaseResult
from stage1.ur5_ik_controller import IKResult
from stage1_1 import config as cfg
from stage1_1.pickup import PickupTrial, TrialState


MATERIAL = 100
WRONG = 200
TCP = 300
PAD = 999


class FakeConveyor:
    def __init__(self, sim, stop_lag_steps=0, never_stop=False):
        self.sim = sim
        self.velocity = 0.0
        self.pad_pos = 0.0
        self.speeds = []
        self.stop_lag_steps = stop_lag_steps
        self.never_stop = never_stop
        self._lag = 0

    def set_speed(self, alias, v):
        self.speeds.append(float(v))
        if v == 0.0:
            if self.never_stop:
                return
            if self.stop_lag_steps > 0 and self.velocity > 0:
                self._lag = self.stop_lag_steps
            else:
                self.velocity = 0.0
        else:
            self.velocity = float(v)
            self._lag = 0

    def stop(self, alias):
        self.set_speed(alias, 0.0)

    def read_actual_velocity(self, alias):
        return self.velocity

    def on_step(self):
        if self._lag > 0:
            self._lag -= 1
            if self._lag == 0:
                self.velocity = 0.0
        self.pad_pos += self.velocity * base.SIM_STEP


class FakeSim:
    handle_world = -1
    simulation_stopped = 0

    def __init__(self):
        self.t = 0.0
        self.calls = []
        self.material_pose = [0.0, 0.0, 0.50, 0.0, 0.0, 0.0, 1.0]
        self.tcp_pose = [0.0, 0.0, 0.55, 0.0, 0.0, 0.0, 1.0]
        self.tcp_follows_material = True
        self.material_speed = 0.0
        self.sensor_hit = True
        self.wrong_hit = None
        self.suction_hit = True
        self.conveyor = None
        self.tcp_handle = TCP
        self.material_handle = MATERIAL
        self.pad_handle = PAD

    def getSimulationTime(self):
        return self.t

    def getSimulationState(self):
        return 4

    def getObjectPose(self, handle, rel=None):
        if handle == self.material_handle:
            return list(self.material_pose)
        if handle == self.tcp_handle:
            if self.tcp_follows_material:
                return [*self.material_pose[:2],
                        self.material_pose[2] + base.MATERIAL_HALF_HEIGHT,
                        *self.material_pose[3:]]
            return list(self.tcp_pose)
        return [0.0] * 7

    def getObjectPosition(self, handle, rel=None):
        if handle == self.pad_handle:
            return [self.conveyor.pad_pos, 0.0, 0.0]
        return self.getObjectPose(handle, rel)[:3]

    def getObjectQuaternion(self, handle, rel=None):
        return self.getObjectPose(handle, rel)[3:7]

    def getObjectVelocity(self, handle):
        if handle == self.material_handle:
            return ([self.material_speed, 0.0, 0.0], [0.0, 0.0, 0.0])
        return ([0.0, 0.0, 0.0], [0.0, 0.0, 0.0])

    def setObjectPosition(self, handle, pos, rel=None):
        self.calls.append(("setObjectPosition", handle, list(pos)))
        if handle == self.material_handle:
            self.material_pose[:3] = list(pos)

    def step(self):
        self.calls.append("step")
        self.t += base.SIM_STEP
        if self.conveyor is not None:
            self.conveyor.on_step()


class FakeScene:
    def __init__(self, sim):
        self.sim = sim
        self.tcp = TCP
        self.handles = {base.ALIASES["conveyor"]: 500}

    def handle(self, alias, required=False):
        return self.handles.get(alias, -1)

    def _pick_hit(self):
        flag = self.sim.sensor_hit
        if callable(flag):
            return bool(flag(self.sim.material_pose[0]))
        return bool(flag)

    def spawn_material(self, uid, alias, position=None, orientation=None):
        self.sim.material_pose = [*list(position), *list(orientation or [0, 0, 0, 1])]
        self.sim.calls.append(("spawn_material", uid))
        return MATERIAL

    def check_sensor(self, alias, entity):
        if alias == base.ALIASES["pick_sensor"]:
            return MATERIAL if self._pick_hit() else None
        if alias == base.ALIASES["suction_sensor"]:
            return entity if self.sim.suction_hit else None
        return None

    def pick_sensor_hits(self, handles):
        if self.sim.wrong_hit is not None:
            return [self.sim.wrong_hit]
        return [MATERIAL] if self._pick_hit() else []

    def remove_material(self, handle):
        pass


class FakeIK:
    def __init__(self, sim, ok=True):
        self.sim = sim
        self.ok = ok
        self.solves = []
        self.applied = []

    def solve(self, pose):
        self.solves.append((self.sim.getSimulationTime(), tuple(pose)))
        if not self.ok:
            return IKResult(False, [], 0, 112, [0.4, 0.2], False, "unreachable")
        return IKResult(True, [0.0] * 6, 1, 0, [0.001, 0.001], False, "ok")

    def apply_joints(self, joints):
        self.applied.append(list(joints))

    def converged(self, joints):
        return True

    def reached(self, pose):
        return True

    def joints_within_limits(self, joints=None):
        return True

    def stopped(self):
        return True

    def joint_positions(self):
        return [0.0] * 6

    def tcp_position_error(self, pose):
        return 0.0

    def tcp_orientation_error(self, pose):
        return 0.0

    def tcp_speed(self):
        return 0.0


class FakeSuction:
    def __init__(self, sim):
        self.sim = sim
        self.attached = []
        self.released = []

    def attach(self, uid, sim_time):
        self.attached.append((uid, sim_time))
        return AttachResult(ATTACH_OK, "ok")

    def release(self, uid, sim_time, pose, support_z=None):
        self.released.append((uid, sim_time, list(pose), support_z))
        self.sim.material_pose = list(pose)
        return ReleaseResult(True, "ok", stable=True, penetration=False)


def _layout(name="L1_S1"):
    return next(l for l in cfg.CANDIDATE_LAYOUTS if l.name == name)


def _trial(group, initial_x, *, full_pick=False, stop_lag=0,
           never_stop=False, sensor_hit=True, wrong_hit=None,
           material_speed=0.0, ik_ok=True):
    sim = FakeSim()
    conveyor = FakeConveyor(sim, stop_lag_steps=stop_lag,
                            never_stop=never_stop)
    sim.conveyor = conveyor
    scene = FakeScene(sim)
    materials = MaterialManager()
    ik = FakeIK(sim, ok=ik_ok)
    suction = FakeSuction(sim)
    sim.sensor_hit = sensor_hit
    sim.wrong_hit = wrong_hit
    sim.material_speed = material_speed
    trial = PickupTrial(
        sim=sim, scene=scene, conveyor=conveyor,
        geometry=BeltGeometry(base.CONVEYOR_ENTRY_WORLD,
                              base.CONVEYOR_STOP_WORLD),
        layout=_layout(), group=group, initial_x=initial_x,
        initial_label="front", uid=1, materials=materials, ik=ik,
        suction=suction, pad_handle=PAD, full_pick=full_pick, speed_mps=0.2)
    trial.start()
    return sim, scene, conveyor, materials, ik, suction, trial


def _run(trial, sim, max_steps=600):
    for _ in range(max_steps):
        trial.step()
        assert sim.calls[-1] == "step", "sim.step() 必须是每步最后动作"
        if trial.finished:
            return
    raise AssertionError(f"未在 {max_steps} 步内结束: {trial.state}")


def test_b_group_stops_on_sensor_and_moves_arm_only_after_stability():
    # 从允许区上游边界出发, 让 HYBRID_DYNAMIC 补偿把物料送进传感器区
    sim, scene, conveyor, materials, ik, suction, trial = _trial(
        "B", -0.40, sensor_hit=lambda x: x >= -0.33)
    _run(trial, sim)
    rec = trial.result()
    assert trial.state is TrialState.DONE
    assert rec["success"] is True and rec["gate_ok"] is True
    assert rec["stop_command_reason"] == "sensor_hit"
    assert rec["stop_command_time"] >= rec["sensor_trigger_time"]
    assert rec["pad_stop_time"] >= rec["stop_command_time"]
    assert rec["material_settle_time_s"] >= 0
    assert rec["inside_allowed_region"] is True
    # 机械臂只在停带+稳定之后才求解
    assert ik.solves, "B 组应执行取料 IK"
    first_solve_t = ik.solves[0][0]
    assert first_solve_t >= rec["pad_stop_time"]
    assert first_solve_t >= rec["stop_command_time"]
    # 不启动补偿之外的物位移移; 补偿沿带面
    assert rec["compensation_enabled"] is True
    assert rec["compensation_count"] > 0
    assert suction.attached == [], "未要求完整抓取时不得吸附"


def test_a_group_current_flow_stops_at_progress_not_at_sensor_hit():
    sim, scene, conveyor, materials, ik, suction, trial = _trial("A", -0.32)
    _run(trial, sim)
    rec = trial.result()
    assert trial.state is TrialState.DONE
    assert rec["success"] is True
    assert rec["sensor_trigger_time"] is not None
    assert rec["stop_command_reason"] == "progress>=1.0"
    assert rec["stop_command_time"] > rec["sensor_trigger_time"], (
        "阶段一当前流程在行程进度 1.0 才发零速, 不是 sensor 命中即停")
    assert rec["material_final_pose"][0] == pytest.approx(-0.20, abs=0.01)
    assert rec["compensation_count"] > 0
    assert ik.solves == [], "A 组只记录停带行为, 不驱动机械臂"


def test_c_group_disables_compensation_and_records_no_transport():
    sim, scene, conveyor, materials, ik, suction, trial = _trial("C", -0.32)
    _run(trial, sim)
    rec = trial.result()
    assert trial.state is TrialState.DONE
    assert rec["compensation_enabled"] is False
    assert rec["compensation_count"] == 0
    assert rec["transported"] is False
    assert rec["displacement_x"] == pytest.approx(0.0, abs=1e-6)
    assert not [c for c in sim.calls
                if isinstance(c, tuple) and c[0] == "setObjectPosition"]
    assert ik.solves == []


def test_pad_stop_timeout_fails_before_ik():
    sim, scene, conveyor, materials, ik, suction, trial = _trial(
        "B", -0.32, never_stop=True)
    _run(trial, sim)
    assert trial.state is TrialState.FAILED
    assert "PAD_STOP_TIMEOUT" in trial.failure_reason
    assert ik.solves == []


def test_material_not_stable_fails_before_ik():
    sim, scene, conveyor, materials, ik, suction, trial = _trial(
        "B", -0.32, material_speed=0.20)
    _run(trial, sim)
    assert trial.state is TrialState.FAILED
    assert "MATERIAL_SETTLE_TIMEOUT" in trial.failure_reason
    assert ik.solves == []


def test_out_of_allowed_region_fails_before_ik():
    sim, scene, conveyor, materials, ik, suction, trial = _trial(
        "B", -0.45)
    _run(trial, sim)
    assert trial.state is TrialState.FAILED
    assert "OUT_OF_ALLOWED_REGION" in trial.failure_reason
    assert ik.solves == []


def test_uid_mismatch_does_not_stop_or_attach():
    sim, scene, conveyor, materials, ik, suction, trial = _trial(
        "B", -0.32, sensor_hit=False, wrong_hit=WRONG)
    _run(trial, sim)
    assert trial.state is TrialState.FAILED
    assert "PICK_MISMATCH" in trial.failure_reason
    assert trial.result()["stop_command_time"] is None
    assert ik.solves == []
    assert suction.attached == []


def test_ik_failure_before_attach():
    sim, scene, conveyor, materials, ik, suction, trial = _trial(
        "B", -0.32, ik_ok=False)
    _run(trial, sim)
    assert trial.state is TrialState.FAILED
    assert "IK_FAILED" in trial.failure_reason
    assert suction.attached == []


def test_b_group_full_pick_attaches_lifts_places_and_resets():
    sim, scene, conveyor, materials, ik, suction, trial = _trial(
        "B", -0.32, full_pick=True, stop_lag=1)
    _run(trial, sim)
    rec = trial.result()
    assert trial.state is TrialState.DONE
    assert rec["success"] is True and rec["gate_ok"] is True
    assert len(suction.attached) == 1 and len(suction.released) == 1
    assert rec["place_position_error_m"] == pytest.approx(0.0, abs=1e-6)
    # 抬升 + 工位接近 + 工位放置 = IK 目标至少 5 个 (取料接近/取料/抬升/接近/放置)
    assert len(ik.solves) >= 5
    assert rec["attach_result"]["ok"] is True
    assert rec["release_result"]["ok"] is True


def test_b_group_suction_precheck_failure_blocks_attach():
    sim, scene, conveyor, materials, ik, suction, trial = _trial("B", -0.32)
    sim.suction_hit = False
    _run(trial, sim)
    assert trial.state is TrialState.FAILED
    assert "SUCTION_PRECHECK_FAILED" in trial.failure_reason
    assert suction.attached == []
