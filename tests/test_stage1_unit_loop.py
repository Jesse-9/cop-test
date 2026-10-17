"""tests/test_stage1_unit_loop.py — 阶段一单步控制循环测试 (§2.11)

不需要 CoppeliaSim: 用 fake conveyor/IK/suction/AGV/sim 组装一个单元闭环,
验证 §2.11 的 11 步顺序与任务状态机行为:
  - 目标 UID 不匹配时不吸附 (记录 PICK_MISMATCH)
  - IK 失败 → 任务 FAILED + 退避重试 (CONVEYOR_PICKUP_RETRY_DELAY)
  - 正常完成 = 吸附→抬升→转运→放置→释放, 物料 RELEASED
  - sim.step() 永远是最后一步
"""

import pytest

from material_manager import MaterialManager, MaterialState
from suction_controller import SuctionController, REJECT_TCP_TOO_FAR
from stage1_unit_controller import (
    Stage1UnitController,
    UnitTaskState,
    PickMismatch,
    MismatchReason,
)

from fakes import make_downward_tcp_sim


# ═══════════════════════════════════════════════════════════════
# Fakes
# ═══════════════════════════════════════════════════════════════

class FakeSim:
    def __init__(self):
        self.calls = []
        self.t = 0.0

    def getSimulationState(self):
        self.calls.append("getSimulationState")
        return 4

    def getSimulationTime(self):
        return self.t

    def step(self):
        self.calls.append("step")
        self.t += 0.05

    def setJointTargetPosition(self, h, v, *a):
        self.calls.append(("setJointTargetPosition", h))

    def setJointTargetVelocity(self, h, v):
        self.calls.append(("setJointTargetVelocity", h, v))


class FakeConveyorCtl:
    def __init__(self):
        self.speed = 0.0
        self.states = []

    def set_speed(self, alias, v):
        self.speed = v
        self.states.append(("set_speed", alias, v))

    def read_actual_velocity(self, alias):
        return self.speed


class FakeIK:
    def __init__(self, fail_times=0):
        self.fail_times = fail_times
        self.calls = []

    def solve_target(self, pose):
        self.calls.append(("solve_target", tuple(pose[:3])))
        if self.fail_times > 0:
            self.fail_times -= 1
            from ur5_ik_controller import IKResult
            return IKResult(False, [], 0, 112, [0.4, 0.2], True, "不可达")
        from ur5_ik_controller import IKResult
        return IKResult(True, [0.1] * 6, 1, 0, [0.0, 0.0], False, "ok")


class FakeSuction:
    """占位: 现在用真实 SuctionController + FakeSuctionSim (见 _build)"""


class FakeAGV:
    def __init__(self):
        self.calls = []

    def set_cmd(self, v, w):
        self.calls.append(("set_cmd", v, w))

    def stop(self):
        self.calls.append(("stop",))


class FakeHandles:
    def __init__(self, pick_handle=99, suction_sensor_hit=99):
        self.pick_handle = pick_handle
        self.suction_sensor_hit = suction_sensor_hit
        self.calls = []

    def read_pick_sensor(self):
        self.calls.append("read_pick_sensor")
        return self.pick_handle


# ═══════════════════════════════════════════════════════════════
# 组装
# ═══════════════════════════════════════════════════════════════

MAT_H = 99
LINK_H = 50
TCP_H = 51
SENSOR_H = 52


def _build(ik_fail_times=0, pick_hit=99, tcp_gap=0.0, place_pose=None):
    sim = FakeSim()
    conv = FakeConveyorCtl()
    ik = FakeIK(fail_times=ik_fail_times)
    agv = FakeAGV()
    handles = FakeHandles(pick_handle=pick_hit)

    mgr = MaterialManager()
    rec = mgr.register(uid=1, alias="Part_0001")
    rec.transition_to(MaterialState.ON_CONVEYOR, sim_time=0.0, reason="spawn")
    mgr.attach_sim(1, MAT_H)

    # 真实吸附控制器 + 共享 FakeSuctionSim (吸盘朝下, 物料在其下方)
    sim_s = make_downward_tcp_sim(material_handle=MAT_H, tcp_handle=TCP_H,
                                  tcp_gap=tcp_gap)
    suc = SuctionController(sim_s, mgr, link_handle=LINK_H,
                            tcp_handle=TCP_H, sensor_handle=SENSOR_H,
                            logger=None)

    ctl = Stage1UnitController(
        sim=sim, handles=handles, materials=mgr, conveyor=conv,
        ik=ik, suction=suc, agv=agv,
        target_uid=1,
        conveyor_alias="Conveyor_Test",
        pick_pose=[0.5, 0.0, 0.9, 0, 0, 0, 1],
        place_pose=place_pose or [1.0, 1.0, 0.5, 0, 0, 0, 1],
        logger=None,
    )
    return ctl, dict(sim=sim, conv=conv, ik=ik, suc=suc, sim_s=sim_s,
                     agv=agv, handles=handles, mgr=mgr, rec=rec)


def _run_until(ctl, parts, cond, max_steps=400):
    for _ in range(max_steps):
        ctl.step()
        if cond(parts):
            return True
    return False


# ═══════════════════════════════════════════════════════════════
# §2.11 顺序与不变量
# ═══════════════════════════════════════════════════════════════

def test_step_calls_sim_step_last():
    """§2.11 步骤11: sim.step() 必须是本步最后动作"""
    ctl, p = _build()
    ctl.step()
    assert p["sim"].calls[-1] == "step"


def test_conveyor_running_during_wait():
    """§2.11 步骤2: 循环驱动传送带"""
    ctl, p = _build()
    ctl.step()
    assert p["conv"].speed == pytest.approx(0.5) or p["conv"].states


# ═══════════════════════════════════════════════════════════════
# 目标 UID 不匹配 (§2.8 步骤5)
# ═══════════════════════════════════════════════════════════════

def test_pick_mismatch_prevents_attach():
    """传感器命中另一物料 → PICK_MISMATCH, 不进入吸附"""
    ctl, p = _build(pick_hit=77)   # 命中 77, 目标句柄 99
    for _ in range(50):
        ctl.step()
    # 从未发生父子绑定 (即从未吸附)
    assert p["sim_s"].called("setObjectParent") == []
    assert ctl.last_mismatch is not None
    assert ctl.last_mismatch.reason is MismatchReason.UID_MISMATCH
    assert p["rec"].state is MaterialState.ON_CONVEYOR


def test_no_hit_keeps_waiting():
    """传感器无命中 → 保持等待, 不吸附"""
    ctl, p = _build(pick_hit=-1)
    for _ in range(50):
        ctl.step()
    assert p["sim_s"].called("setObjectParent") == []
    assert ctl.task_state in (UnitTaskState.WAITING_FOR_ITEM,)


# ═══════════════════════════════════════════════════════════════
# 正常闭环: 吸附→抬升→转运→放置→释放
# ═══════════════════════════════════════════════════════════════

def test_happy_path_completes_and_releases():
    ctl, p = _build()
    ok = _run_until(ctl, p,
                    lambda pr: pr["rec"].state is MaterialState.RELEASED)
    assert ok, f"闭环未完成, 终态={p['rec'].state}"
    # 吸附发生 (绑定到吸盘 link), 释放发生 (回到原父), IK 被调用
    binds = p["sim_s"].called("setObjectParent")
    assert binds and binds[0][2] == LINK_H
    assert p["ik"].calls, "IK 应被调用"
    # 释放后物理属性恢复 (§2.9 步骤8): static 回到快照值 0
    assert p["sim_s"].static.get(MAT_H, 0) == 0
    assert p["sim_s"].called("resetDynamicObject")


def test_ik_failure_schedules_retry_not_fake_success():
    """§2.7 步骤9 + §2.12: IK 失败 → FAILED 退避, 不伪造到位"""
    ctl, p = _build(ik_fail_times=999)     # 永久失败
    for _ in range(200):
        ctl.step()
    assert ctl.task_state is UnitTaskState.FAILED
    assert p["rec"].state is not MaterialState.RELEASED
    assert p["sim_s"].called("setObjectParent") == []   # 从未吸附
    assert ctl.retry_at is not None and ctl.retry_at > 0


def test_ik_recovers_after_transient_failure():
    """IK 短暂失败后重试成功 → 闭环仍能完成

    每次失败 → 退避 CONVEYOR_PICKUP_RETRY_DELAY=2s → 重试。
    5 次失败约需 5×41 + 若干步, 在 800 步预算内可完成。
    """
    ctl, p = _build(ik_fail_times=5)
    ok = _run_until(ctl, p,
                    lambda pr: pr["rec"].state is MaterialState.RELEASED,
                    max_steps=800)
    assert ok
    assert ctl.retry_at is None or ctl.task_state is UnitTaskState.DONE


def test_attach_rejected_marks_failed():
    """吸附被拒 (TCP 距离超限) → FAILED, 不继续抬升"""
    ctl, p = _build(tcp_gap=1.0)   # TCP 远离物料
    for _ in range(400):
        ctl.step()
    assert ctl.task_state is UnitTaskState.FAILED
    assert p["rec"].state is not MaterialState.RELEASED