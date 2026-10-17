"""物料生命周期状态机测试 (§2.5).

覆盖: 7 状态、合法/非法转换、事件历史、UID 唯一性与故障复位。
纯逻辑, 不需要 CoppeliaSim。
"""

import pytest

from stage1.material_manager import (
    MaterialManager, MaterialState, InvalidTransition, IllegalStateError,
)


def _manager_with(uid=1, alias="Part_0001"):
    m = MaterialManager()
    m.register(uid, alias)
    return m


def test_main_chain_transitions_are_allowed():
    m = _manager_with()
    t = 0.0
    for state in (MaterialState.ON_CONVEYOR,
                  MaterialState.READY_FOR_PICK,
                  MaterialState.HELD_BY_SUCTION,
                  MaterialState.ON_TRAY,
                  MaterialState.RELEASED):
        t += 1.0
        m.transition(1, state, t, reason=f"to {state.value}")
    rec = m.by_uid(1)
    assert rec.state is MaterialState.RELEASED
    assert [e[1] for e in rec.replay()] == [
        MaterialState.ON_CONVEYOR, MaterialState.READY_FOR_PICK,
        MaterialState.HELD_BY_SUCTION, MaterialState.ON_TRAY,
        MaterialState.RELEASED,
    ]


def test_skip_suction_is_rejected():
    m = _manager_with()
    m.transition(1, MaterialState.ON_CONVEYOR, 1.0, "on belt")
    m.transition(1, MaterialState.READY_FOR_PICK, 2.0, "at pick")
    with pytest.raises(InvalidTransition):
        m.transition(1, MaterialState.ON_TRAY, 3.0, "skip suction")
    assert m.by_uid(1).state is MaterialState.READY_FOR_PICK


def test_skip_release_is_rejected():
    m = _manager_with()
    m.transition(1, MaterialState.ON_CONVEYOR, 1.0, "on belt")
    m.transition(1, MaterialState.READY_FOR_PICK, 2.0, "at pick")
    m.transition(1, MaterialState.HELD_BY_SUCTION, 3.0, "attached")
    with pytest.raises(InvalidTransition):
        m.transition(1, MaterialState.RELEASED, 4.0, "skip tray")


_CHAIN = [MaterialState.ON_CONVEYOR, MaterialState.READY_FOR_PICK,
          MaterialState.HELD_BY_SUCTION, MaterialState.ON_TRAY,
          MaterialState.RELEASED]


def _advance_to(m, uid, target, t=1.0):
    """沿合法主链路走到 target; target=SPAWNED 时为初始状态。"""
    if target is MaterialState.SPAWNED:
        return
    for state in _CHAIN:
        m.transition(uid, state, t, reason=f"to {state.value}")
        if state is target:
            return
    raise AssertionError(f"不可达状态 {target}")


def test_fault_reachable_from_every_state():
    for target in (MaterialState.SPAWNED, MaterialState.ON_CONVEYOR,
                   MaterialState.READY_FOR_PICK, MaterialState.HELD_BY_SUCTION,
                   MaterialState.ON_TRAY, MaterialState.RELEASED):
        m = _manager_with(uid=7)
        _advance_to(m, 7, target)
        m.transition(7, MaterialState.FAULT, 10.0, "fault injection")
        assert m.by_uid(7).state is MaterialState.FAULT


def test_fault_can_be_reset_explicitly():
    m = _manager_with()
    m.transition(1, MaterialState.FAULT, 1.0, "inject")
    m.reset_fault(1, 2.0, "operator reset")
    assert m.by_uid(1).state is MaterialState.SPAWNED
    assert m.by_uid(1).replay()[-1][1] is MaterialState.SPAWNED


def test_history_records_sim_time_and_reason():
    m = _manager_with()
    m.transition(1, MaterialState.ON_CONVEYOR, 1.25, "belt entry")
    event = m.by_uid(1).history[-1]
    assert event.sim_time == 1.25
    assert event.reason == "belt entry"
    assert event.from_state is MaterialState.SPAWNED
    assert event.to_state is MaterialState.ON_CONVEYOR


def test_uid_and_alias_must_be_unique():
    m = _manager_with()
    with pytest.raises(ValueError):
        m.register(1, "Part_0002")
    with pytest.raises(ValueError):
        m.register(2, "Part_0001")


def test_by_uid_is_the_only_task_lookup():
    m = _manager_with()
    assert m.by_uid(1).alias == "Part_0001"
    assert m.by_uid(999) is None
    assert m.by_alias("Part_0001").uid == 1
    assert m.by_alias("missing") is None


def test_unknown_uid_operations_raise_illegal_state():
    m = MaterialManager()
    with pytest.raises(IllegalStateError):
        m.transition(42, MaterialState.ON_CONVEYOR, 1.0, "no such uid")
