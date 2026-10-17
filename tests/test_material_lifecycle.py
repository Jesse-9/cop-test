"""
test_material_lifecycle.py — 物料生命周期状态机测试 (阶段一 §2.5)
=====================================================================

测试 `material_manager.MaterialManager` 与 `MaterialState` 的纯逻辑部分。
这些测试不需要 CoppeliaSim 连接 —— 物料的创建/查询/删除需要 sim，
但状态转换、UID 绑定、事件历史完全是 Python 逻辑。

方案依据: docs/wiki/06-工业案例/06-05-柔性工业车间分阶段执行步骤.md §2.5
"""

import pytest

from material_manager import (
    MaterialState,
    MaterialRecord,
    MaterialManager,
    InvalidTransition,
)


# ═══════════════════════════════════════════════════════════════
# §2.5 步骤5: 定义状态 SPAWNED/ON_CONVEYOR/READY_FOR_PICK/
#           HELD_BY_SUCTION/ON_TRAY/RELEASED/FAULT
# ═══════════════════════════════════════════════════════════════

def test_all_seven_states_exist():
    """方案 §2.5 要求的 7 个状态全部存在"""
    names = {s.name for s in MaterialState}
    assert names == {
        "SPAWNED", "ON_CONVEYOR", "READY_FOR_PICK",
        "HELD_BY_SUCTION", "ON_TRAY", "RELEASED", "FAULT",
    }


def test_initial_state_is_spawned():
    """新建物料从 SPAWNED 开始"""
    rec = MaterialRecord(uid=1, alias="Part_0001")
    assert rec.state is MaterialState.SPAWNED


# ═══════════════════════════════════════════════════════════════
# §2.5 步骤6: 每次状态变化记录仿真时间和原因
# ═══════════════════════════════════════════════════════════════

def test_transition_records_time_and_reason():
    """状态变化必须带仿真时间和原因 (§2.5 步骤6)"""
    rec = MaterialRecord(uid=1, alias="Part_0001")
    rec.transition_to(MaterialState.ON_CONVEYOR, sim_time=1.25, reason="placed on belt")
    last = rec.history[-1]
    assert last.sim_time == 1.25
    assert last.reason == "placed on belt"
    assert last.from_state is MaterialState.SPAWNED
    assert last.to_state is MaterialState.ON_CONVEYOR


# ═══════════════════════════════════════════════════════════════
# §2.5 步骤5: 状态转换不得跳过吸附或释放
# ═══════════════════════════════════════════════════════════════

def test_cannot_skip_straight_to_held():
    """禁止 SPAWNED → HELD_BY_SUCTION: 必须经过 ON_CONVEYOR/READY_FOR_PICK"""
    rec = MaterialRecord(uid=1, alias="Part_0001")
    with pytest.raises(InvalidTransition):
        rec.transition_to(MaterialState.HELD_BY_SUCTION, sim_time=2.0, reason="jump")


def test_cannot_skip_released_from_spawned():
    """禁止 SPAWNED → RELEASED: 释放必须先持有"""
    rec = MaterialRecord(uid=1, alias="Part_0001")
    with pytest.raises(InvalidTransition):
        rec.transition_to(MaterialState.RELEASED, sim_time=2.0, reason="jump")


def test_full_happy_path_succeeds():
    """完整闭环路径合法: SPAWNED→ON_CONVEYOR→READY_FOR_PICK→
       HELD_BY_SUCTION→ON_TRAY→RELEASED"""
    rec = MaterialRecord(uid=1, alias="Part_0001")
    for i, s in enumerate([
        MaterialState.ON_CONVEYOR, MaterialState.READY_FOR_PICK,
        MaterialState.HELD_BY_SUCTION, MaterialState.ON_TRAY,
        MaterialState.RELEASED,
    ]):
        rec.transition_to(s, sim_time=float(i), reason=f"-> {s.name}")
    assert rec.state is MaterialState.RELEASED
    assert len(rec.history) == 5


# ═══════════════════════════════════════════════════════════════
# §2.5 步骤4/7: UID 绑定, 禁止通过 alias 猜测目标物料
# ═══════════════════════════════════════════════════════════════

def test_get_by_uid_returns_record():
    mgr = MaterialManager()
    rec = mgr.register(uid=7, alias="Part_0007")
    assert mgr.by_uid(7) is rec


def test_by_uid_unknown_returns_none():
    mgr = MaterialManager()
    assert mgr.by_uid(999) is None


def test_alias_is_not_a_lookup_key():
    """§2.5 步骤7: 控制器禁止用 alias 推断目标 —— by_uid 是唯一查询入口。
    别名只用于诊断日志, 不作为可被控制器直接解析的句柄来源。"""
    mgr = MaterialManager()
    rec = mgr.register(uid=7, alias="Part_0007")
    # 拿到 UID 之后可以查询
    assert mgr.by_uid(rec.uid) is rec
    # 注册两个相同 alias 必须被拒绝 (唯一性检查, §2.3/§2.5)
    with pytest.raises(DuplicateAliasError := ValueError):
        mgr.register(uid=8, alias="Part_0007")


# ═══════════════════════════════════════════════════════════════
# §2.5 通过条件: 创建、查询、删除; 完整历史可还原
# ═══════════════════════════════════════════════════════════════

def test_remove_by_uid_drops_record():
    mgr = MaterialManager()
    mgr.register(uid=1, alias="Part_0001")
    mgr.remove(1)
    assert mgr.by_uid(1) is None


def test_history_replays_full_lifecycle():
    """测试可还原物料完整历史 (§2.5 通过条件)"""
    mgr = MaterialManager()
    rec = mgr.register(uid=1, alias="Part_0001")
    timeline = [
        (MaterialState.ON_CONVEYOR, 0.5, "spawned on belt"),
        (MaterialState.READY_FOR_PICK, 3.0, "Sensor_PickStation confirmed"),
        (MaterialState.HELD_BY_SUCTION, 3.4, "attach uid=1"),
        (MaterialState.ON_TRAY, 6.1, "placed on tray"),
        (MaterialState.RELEASED, 6.2, "release uid=1"),
    ]
    for state, t, reason in timeline:
        rec.transition_to(state, sim_time=t, reason=reason)
    replay = [(e.sim_time, e.to_state, e.reason) for e in rec.history]
    expected = [(t, state, reason) for state, t, reason in timeline]
    assert replay == expected