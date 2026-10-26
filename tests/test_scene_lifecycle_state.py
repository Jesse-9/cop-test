"""步骤 1.2 生命周期状态机/会话状态/运行锁单测 (不需要 CoppeliaSim)。"""

import json
import os

import pytest

from stage1.lifecycle import (
    InvalidTransitionError,
    LifecycleState,
    LifecycleStateMachine,
    RunConflictError,
    RunLock,
    SessionState,
    SessionStore,
)


def test_allowed_sequence():
    sm = LifecycleStateMachine()
    assert sm.state is LifecycleState.DISCONNECTED
    assert sm.transition("connect") is LifecycleState.CONNECTED_STOPPED
    assert sm.transition("build") is LifecycleState.SCENE_VALIDATED
    assert sm.transition("save") is LifecycleState.SCENE_VALIDATED
    assert sm.transition("start") is LifecycleState.SIMULATION_RUNNING
    assert sm.transition("stop") is LifecycleState.SCENE_VALIDATED
    assert sm.transition("disconnect") is LifecycleState.DISCONNECTED


def test_load_validate_sequence():
    sm = LifecycleStateMachine()
    sm.transition("connect")
    assert sm.transition("load") is LifecycleState.SCENE_LOADED
    assert sm.transition("validate") is LifecycleState.SCENE_VALIDATED
    assert sm.transition("start") is LifecycleState.SIMULATION_RUNNING
    assert sm.transition("stop", target=LifecycleState.CONNECTED_STOPPED) \
        is LifecycleState.CONNECTED_STOPPED


@pytest.mark.parametrize("action", ["build", "save", "load", "validate", "start"])
def test_rejects_mutating_actions_while_running(action):
    sm = LifecycleStateMachine(LifecycleState.SIMULATION_RUNNING)
    with pytest.raises(InvalidTransitionError) as exc:
        sm.transition(action)
    assert exc.value.code == "INVALID_STATE"
    assert sm.state is LifecycleState.SIMULATION_RUNNING


def test_start_requires_loaded_scene():
    for state in (LifecycleState.DISCONNECTED,
                  LifecycleState.CONNECTED_STOPPED):
        sm = LifecycleStateMachine(state)
        with pytest.raises(InvalidTransitionError):
            sm.transition("start")


def test_loaded_scene_allows_start_transition():
    """SCENE_LOADED 可直接 start; 全量校验由 prepare_start() 在锁内完成。"""
    sm = LifecycleStateMachine(LifecycleState.SCENE_LOADED)
    assert sm.transition("start") is LifecycleState.SIMULATION_RUNNING


def test_validate_requires_loaded_scene():
    sm = LifecycleStateMachine(LifecycleState.CONNECTED_STOPPED)
    with pytest.raises(InvalidTransitionError):
        sm.transition("validate")


def test_error_state_can_recover_by_explicit_build_or_load():
    sm = LifecycleStateMachine(LifecycleState.SCENE_VALIDATED)
    assert sm.transition("fail") is LifecycleState.ERROR
    assert sm.transition("load") is LifecycleState.SCENE_LOADED
    sm.transition("fail")
    assert sm.transition("build") is LifecycleState.SCENE_VALIDATED


def test_unknown_action_rejected():
    sm = LifecycleStateMachine(LifecycleState.CONNECTED_STOPPED)
    with pytest.raises(InvalidTransitionError):
        sm.transition("teleport")


def test_disconnect_from_any_state():
    for state in LifecycleState:
        sm = LifecycleStateMachine(state)
        assert sm.transition("disconnect") is LifecycleState.DISCONNECTED


# ── SessionState / SessionStore ─────────────────────────────

def test_session_state_roundtrip(tmp_path):
    store = SessionStore(tmp_path / "session.json")
    st = SessionState(state=LifecycleState.SCENE_VALIDATED,
                      artifact_id="stage1/L1_S1/v001_x",
                      scene_origin="ARTIFACT",
                      manifest_digest="abc",
                      validated_at="2026-10-20T12:00:00")
    store.save(st)
    back = store.load()
    assert back.state is LifecycleState.SCENE_VALIDATED
    assert back.artifact_id == st.artifact_id
    assert back.scene_origin == "ARTIFACT"


def test_session_store_missing_returns_disconnected(tmp_path):
    assert SessionStore(tmp_path / "none.json").load().state \
        is LifecycleState.DISCONNECTED


def test_session_store_rejects_corrupt_file(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(Exception):
        SessionStore(p).load()


def test_session_store_atomic_no_temp(tmp_path):
    p = tmp_path / "nested" / "session.json"
    store = SessionStore(p)
    store.save(SessionState(state=LifecycleState.CONNECTED_STOPPED))
    assert json.loads(p.read_text(encoding="utf-8"))["state"] \
        == "CONNECTED_STOPPED"
    assert not list(p.parent.glob("*.tmp*"))


# ── RunLock ─────────────────────────────────────────────────

def test_run_lock_exclusive(tmp_path):
    path = tmp_path / "run.lock"
    lock = RunLock(path, pid_alive=lambda pid: pid == os.getpid())
    assert lock.acquire("run-1", {"artifact_id": "a"})
    other = RunLock(path, pid_alive=lambda pid: pid == os.getpid())
    assert not other.acquire("run-2", {})
    assert other.owner()["run_id"] == "run-1"
    lock.release()
    assert other.acquire("run-2", {})


def test_run_lock_steals_stale_lock(tmp_path):
    path = tmp_path / "run.lock"
    path.write_text(json.dumps({"pid": 999999, "run_id": "dead"}), encoding="utf-8")
    lock = RunLock(path, pid_alive=lambda pid: False)
    assert lock.acquire("run-new", {})
    assert json.loads(path.read_text(encoding="utf-8"))["run_id"] == "run-new"
    lock.release()


def test_run_lock_release_only_own(tmp_path):
    path = tmp_path / "run.lock"
    lock = RunLock(path, pid_alive=lambda pid: True)
    lock.acquire("mine", {})
    other = RunLock(path, pid_alive=lambda pid: True)
    other.release()                      # 不持有 → 不删除别人的锁
    assert lock.owner()["run_id"] == "mine"
    lock.release()


def test_run_lock_conflict_error_has_code():
    err = RunConflictError("held", owner={"pid": 1})
    assert err.code == "RUN_CONFLICT"
    assert err.owner == {"pid": 1}
