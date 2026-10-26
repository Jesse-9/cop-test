"""LifecycleManager 状态与动作单测 (不需要 CoppeliaSim)。

真实 build/save/load 的几何与物理行为由 CoppeliaSim 集成测试覆盖;
这里验证状态转换、运行锁、工件保护与失败分类。
"""

import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import stage1.config as cfg
import stage1.scene_artifact as sa
from stage1.layout import default_layout
from stage1.lifecycle import (
    InvalidTransitionError,
    LifecycleError,
    LifecycleManager,
    LifecycleState,
    RunConflictError,
    ValidationFailedError,
)


class FakeSim:
    simulation_stopped = 0
    simulation_running = 1

    def __init__(self, state=0):
        self._state = state
        self.loaded = None
        self.saved = None

    def getSimulationState(self):
        return self._state

    def stopSimulation(self):
        self._state = 0

    def saveScene(self, path):
        Path(path).write_bytes(b"fake-ttt")
        self.saved = str(path)

    def loadScene(self, path):
        self.loaded = str(path)


class FakeScene:
    layout = SimpleNamespace(name="L1_S1")
    build_report = {"ok": True, "checks": []}
    conveyor_meta = {}
    agv_root = -1
    agv_left_wheel = -1

    def verify(self):
        return {"ok": True, "checks": []}

    def describe(self):
        return []

    def tool_geometry(self):
        return {}


def _manager(tmp_path, sim=None):
    return LifecycleManager(
        sim or FakeSim(), None,
        artifact_root=tmp_path / "artifacts",
        session_path=tmp_path / "session.json",
        report_dir=tmp_path / "runs")


def _publish(tmp_path, protected=True, digest=None):
    store = sa.ArtifactStore(tmp_path / "artifacts")
    draft = store.create("stage1", "L1_S1", "20261020_120000")
    scene = draft.path / "scene.ttt"
    scene.write_bytes(b"fake-ttt")
    manifest = {
        "schema_version": sa.SCHEMA_VERSION,
        "artifact_id": draft.artifact_id,
        "created_at": sa.utc_now(),
        "source_phase": "stage1",
        "protected": protected,
        "parent_artifact": None,
        "code": {}, "config": {"digest": "x", "summary": {}},
        "simulator": {"product_version": "4.10.0.0",
                      "version_major_minor": "4.10"},
        "models": [],
        "layout": dataclasses.asdict(default_layout()),
        "objects": [
            {"alias": a, "type": "shape", "parent_alias": "",
             "required": True, "world_pos": [0, 0, 0]}
            for a in cfg.REQUIRED_ALIASES
        ],
        "geometry": {}, "tool": {}, "physics": {},
        "scripts": {}, "builder": {}, "contract": {"ok": True},
        "artifact": {"scene_file": "scene.ttt",
                     "sha256": digest or sa.sha256_file(scene),
                     "size": scene.stat().st_size},
    }
    sa.write_json_atomic(draft.path / "manifest.json", manifest)
    return store.find(draft.artifact_id)


def test_build_sets_validated_state(tmp_path, monkeypatch):
    import stage1.scene as scene_mod

    monkeypatch.setattr(scene_mod, "build_scene",
                        lambda sim, clear=True, layout=None: FakeScene())
    mgr = _manager(tmp_path)
    result = mgr.build()
    assert result["lifecycle_state"] == "SCENE_VALIDATED"
    assert mgr.session.scene_origin == "BUILT"
    assert mgr.session.artifact_id is None
    assert mgr.store.root.exists()


def test_build_rejected_while_sim_running(tmp_path):
    mgr = _manager(tmp_path, FakeSim(state=1))
    with pytest.raises(InvalidTransitionError) as exc:
        mgr.build()
    assert exc.value.code == "INVALID_STATE"


def test_load_sets_loaded_not_validated(tmp_path, monkeypatch):
    import stage1.scene as scene_mod

    art = _publish(tmp_path)
    monkeypatch.setattr(scene_mod, "adopt_scene",
                        lambda sim, layout=None: FakeScene())
    sim = FakeSim()
    mgr = _manager(tmp_path, sim)
    result = mgr.load(art.artifact_id)
    assert result["lifecycle_state"] == "SCENE_LOADED"
    assert result["validation_at_start"] is True
    assert sim.loaded == str(art.scene_path)
    assert mgr.session.artifact_id == art.artifact_id
    actions = mgr.allowed_actions()
    assert "start" in actions
    assert "validate" in actions


def test_load_rejects_digest_mismatch(tmp_path):
    art = _publish(tmp_path, digest="0" * 64)
    mgr = _manager(tmp_path)
    with pytest.raises(ValidationFailedError):
        mgr.load(art.artifact_id)
    assert mgr.sm.state is LifecycleState.ERROR


def test_load_rejected_while_running(tmp_path):
    art = _publish(tmp_path)
    mgr = _manager(tmp_path, FakeSim(state=1))
    with pytest.raises(InvalidTransitionError):
        mgr.load(art.artifact_id)


def test_validate_requires_loaded_artifact(tmp_path):
    mgr = _manager(tmp_path)
    with pytest.raises(InvalidTransitionError):
        mgr.validate()


def test_start_after_load_in_separate_process(tmp_path, monkeypatch):
    """CLI 每条命令独立进程: 新 manager 从会话恢复并完成启动点校验。"""
    import stage1.scene as scene_mod

    art = _publish(tmp_path)
    monkeypatch.setattr(scene_mod, "adopt_scene",
                        lambda sim, layout=None: FakeScene())
    seen = {}

    def fake_validate(sim, artifact, **kwargs):
        seen["owner"] = mgr2.run_lock.owner()
        return {"ok": True, "artifact_id": artifact.artifact_id,
                "manifest_digest": artifact.manifest_digest,
                "validated_at": sa.utc_now(), "checks": [],
                "differences": []}

    monkeypatch.setattr(sa, "validate_artifact", fake_validate)
    mgr1 = _manager(tmp_path)
    mgr1.load(art.artifact_id)
    mgr2 = _manager(tmp_path)               # 模拟新的 start 进程
    assert mgr2.sm.state is LifecycleState.SCENE_LOADED
    prepared = mgr2.prepare_start()
    assert prepared["lifecycle_state"] == "SIMULATION_RUNNING"
    assert mgr2.artifact is not None
    assert mgr2.scene is not None
    assert seen["owner"]["run_id"] == prepared["run_id"]
    mgr2.finish_run("DONE")
    assert mgr2.run_lock.owner() is None


def test_start_without_loaded_scene_is_rejected(tmp_path):
    mgr = _manager(tmp_path)
    with pytest.raises(InvalidTransitionError):
        mgr.prepare_start()


def test_start_from_loaded_artifact_validates_under_lock(tmp_path, monkeypatch):
    """load 后可直接 start; 校验必须发生在取得运行锁之后。"""
    import stage1.scene as scene_mod

    art = _publish(tmp_path)
    monkeypatch.setattr(scene_mod, "adopt_scene",
                        lambda sim, layout=None: FakeScene())
    seen = {}

    def fake_validate(sim, artifact, **kwargs):
        seen["owner"] = mgr.run_lock.owner()
        seen["scene"] = kwargs.get("scene")
        return {"ok": True, "artifact_id": artifact.artifact_id,
                "manifest_digest": artifact.manifest_digest,
                "validated_at": sa.utc_now(), "checks": [],
                "differences": []}

    monkeypatch.setattr(sa, "validate_artifact", fake_validate)
    mgr = _manager(tmp_path)
    mgr.load(art.artifact_id)
    assert mgr.sm.state is LifecycleState.SCENE_LOADED
    prepared = mgr.prepare_start()
    assert prepared["lifecycle_state"] == "SIMULATION_RUNNING"
    assert seen["owner"] is not None
    assert seen["owner"]["run_id"] == prepared["run_id"]
    assert seen["scene"] is mgr.scene
    assert mgr.session.validated_at == mgr.last_validation["validated_at"]
    assert mgr.session.manifest_digest == art.manifest_digest
    mgr.finish_run("DONE")
    assert mgr.sm.state is LifecycleState.SCENE_VALIDATED
    assert mgr.run_lock.owner() is None


def test_start_from_loaded_validation_failure_releases_lock(tmp_path,
                                                           monkeypatch):
    import stage1.scene as scene_mod

    art = _publish(tmp_path)
    monkeypatch.setattr(scene_mod, "adopt_scene",
                        lambda sim, layout=None: FakeScene())
    monkeypatch.setattr(sa, "validate_artifact", lambda *a, **k: {
        "ok": False, "artifact_id": art.artifact_id, "checks": [],
        "differences": [{"kind": "config", "key": "X"}],
        "validated_at": sa.utc_now()})
    mgr = _manager(tmp_path)
    mgr.load(art.artifact_id)
    with pytest.raises(ValidationFailedError):
        mgr.prepare_start()
    assert mgr.run_lock.owner() is None
    assert mgr.sm.state is LifecycleState.ERROR
    assert list((tmp_path / "runs").glob("scene_validation_*.json"))


def test_validate_then_start_acquires_lock(tmp_path, monkeypatch):
    import stage1.scene as scene_mod

    art = _publish(tmp_path)
    monkeypatch.setattr(scene_mod, "adopt_scene",
                        lambda sim, layout=None: FakeScene())
    monkeypatch.setattr(sa, "validate_artifact", lambda *a, **k: {
        "ok": True, "artifact_id": art.artifact_id,
        "manifest_digest": art.manifest_digest,
        "validated_at": sa.utc_now(), "checks": [], "differences": [],
    })
    mgr = _manager(tmp_path)
    mgr.load(art.artifact_id)
    mgr.validate()
    assert mgr.sm.state is LifecycleState.SCENE_VALIDATED
    prepared = mgr.prepare_start()
    assert prepared["lifecycle_state"] == "SIMULATION_RUNNING"
    assert mgr.run_lock.owner()["run_id"] == prepared["run_id"]
    mgr.finish_run("DONE")
    assert mgr.sm.state is LifecycleState.SCENE_VALIDATED
    assert mgr.run_lock.owner() is None


def test_start_refused_when_another_lock_is_live(tmp_path, monkeypatch):
    import stage1.scene as scene_mod

    art = _publish(tmp_path)
    monkeypatch.setattr(scene_mod, "adopt_scene",
                        lambda sim, layout=None: FakeScene())
    monkeypatch.setattr(sa, "validate_artifact", lambda *a, **k: {
        "ok": True, "artifact_id": art.artifact_id,
        "manifest_digest": art.manifest_digest,
        "validated_at": sa.utc_now(), "checks": [], "differences": [],
    })
    mgr = _manager(tmp_path)
    mgr.load(art.artifact_id)
    mgr.validate()
    # 另一个仍存活的进程持有锁 (pid 用当前进程模拟)
    mgr.run_lock.path.parent.mkdir(parents=True, exist_ok=True)
    mgr.run_lock.path.write_text(json.dumps(
        {"pid": __import__("os").getpid(), "run_id": "other"}), encoding="utf-8")
    with pytest.raises(RunConflictError) as exc:
        mgr.prepare_start()
    assert exc.value.code == "RUN_CONFLICT"
    assert mgr.sm.state is LifecycleState.SCENE_VALIDATED


def test_save_requires_pristine_build(tmp_path, monkeypatch):
    import stage1.scene as scene_mod

    monkeypatch.setattr(scene_mod, "build_scene",
                        lambda sim, clear=True, layout=None: FakeScene())
    mgr = _manager(tmp_path)
    mgr.build()
    mgr.has_run_since_build = True
    with pytest.raises(LifecycleError):
        mgr.save()


def test_save_writes_artifact_and_current_pointer(tmp_path, monkeypatch):
    import stage1.scene as scene_mod

    monkeypatch.setattr(scene_mod, "build_scene",
                        lambda sim, clear=True, layout=None: FakeScene())
    monkeypatch.setattr(scene_mod, "adopt_scene",
                        lambda sim, layout=None: FakeScene())

    def fake_manifest(sim, scene, **kwargs):
        return {
            "schema_version": sa.SCHEMA_VERSION,
            "artifact_id": kwargs["artifact_id"],
            "created_at": sa.utc_now(),
            "source_phase": kwargs["source_phase"],
            "protected": kwargs["protected"],
            "parent_artifact": kwargs.get("parent_artifact"),
            "code": {}, "config": {"digest": "x", "summary": {}},
            "simulator": {"product_version": "4.10.0.0"},
            "models": [],
            "layout": dataclasses.asdict(default_layout()),
            "objects": [], "geometry": {}, "tool": {}, "physics": {},
            "scripts": {}, "builder": {}, "contract": {"ok": True},
            "artifact": {"scene_file": "scene.ttt", "sha256": None,
                         "size": None},
        }

    monkeypatch.setattr(sa, "build_manifest", fake_manifest)
    monkeypatch.setattr(sa, "validate_artifact", lambda *a, **k: {
        "ok": True, "artifact_id": "x", "manifest_digest": "y",
        "validated_at": sa.utc_now(), "checks": [], "differences": [],
    })
    mgr = _manager(tmp_path)
    mgr.build()
    result = mgr.save(phase="stage1", protected=False)
    assert "/v001_" in result["artifact_id"]
    art = mgr.store.find(result["artifact_id"])
    assert art.scene_path.exists()
    assert art.manifest["artifact"]["sha256"] == sa.sha256_file(art.scene_path)
    assert mgr.store.read_current("stage1").artifact_id == art.artifact_id
    assert mgr.sm.state is LifecycleState.SCENE_VALIDATED
    assert mgr.session.scene_origin == "ARTIFACT"


def test_stop_when_not_running_is_not_success(tmp_path):
    mgr = _manager(tmp_path)
    result = mgr.stop()
    assert result.get("stopped") is True
    assert mgr.sm.state is LifecycleState.CONNECTED_STOPPED


def test_prepare_start_holds_lock_before_validation(tmp_path, monkeypatch):
    """校验期间必须已持有运行锁, 防止其他进程在窗口中 load/build。"""
    import stage1.scene as scene_mod

    art = _publish(tmp_path)
    monkeypatch.setattr(scene_mod, "adopt_scene",
                        lambda sim, layout=None: FakeScene())
    seen = {}

    def fake_validate(sim, artifact, **kwargs):
        seen["owner"] = mgr.run_lock.owner()
        seen["calls"] = seen.get("calls", 0) + 1
        return {"ok": True, "artifact_id": artifact.artifact_id,
                "manifest_digest": artifact.manifest_digest,
                "validated_at": sa.utc_now(), "checks": [],
                "differences": []}

    monkeypatch.setattr(sa, "validate_artifact", fake_validate)
    mgr = _manager(tmp_path)
    mgr.load(art.artifact_id)
    mgr.validate()
    prepared = mgr.prepare_start()
    assert seen["owner"] is not None
    assert seen["owner"]["run_id"] == prepared["run_id"]
    assert seen["calls"] == 2            # 显式 validate + 启动点重新校验
    mgr.finish_run()


def test_prepare_start_releases_lock_on_validation_failure(tmp_path, monkeypatch):
    import stage1.scene as scene_mod

    art = _publish(tmp_path)
    monkeypatch.setattr(scene_mod, "adopt_scene",
                        lambda sim, layout=None: FakeScene())
    monkeypatch.setattr(sa, "validate_artifact", lambda *a, **k: {
        "ok": False, "artifact_id": art.artifact_id, "checks": [],
        "differences": [{"kind": "config", "key": "X"}],
        "validated_at": sa.utc_now()})
    mgr = _manager(tmp_path)
    mgr.load(art.artifact_id)
    monkeypatch.setattr(sa, "validate_artifact", lambda *a, **k: {
        "ok": True, "artifact_id": art.artifact_id,
        "manifest_digest": art.manifest_digest,
        "validated_at": sa.utc_now(), "checks": [], "differences": []})
    mgr.validate()
    monkeypatch.setattr(sa, "validate_artifact", lambda *a, **k: {
        "ok": False, "artifact_id": art.artifact_id, "checks": [],
        "differences": [{"kind": "config", "key": "X"}],
        "validated_at": sa.utc_now()})
    with pytest.raises(ValidationFailedError):
        mgr.prepare_start()
    assert mgr.run_lock.owner() is None
    assert mgr.sm.state is LifecycleState.ERROR


def test_stage2_requires_parent(tmp_path, monkeypatch):
    import stage1.scene as scene_mod

    monkeypatch.setattr(scene_mod, "build_scene",
                        lambda sim, clear=True, layout=None: FakeScene())
    mgr = _manager(tmp_path)
    mgr.build()
    with pytest.raises(LifecycleError):
        mgr.save(phase="stage2")


def test_start_built_scene_reverifies_contract(tmp_path, monkeypatch):
    import stage1.scene as scene_mod

    monkeypatch.setattr(scene_mod, "build_scene",
                        lambda sim, clear=True, layout=None: FakeScene())
    mgr = _manager(tmp_path)
    mgr.build()
    # 模拟跨进程: build_report 未持久化
    mgr.scene.build_report = {}
    prepared = mgr.prepare_start()
    assert prepared["lifecycle_state"] == "SIMULATION_RUNNING"
    mgr.finish_run()
    assert mgr.sm.state is LifecycleState.SCENE_VALIDATED


def test_start_built_scene_uses_build_contract_not_artifact_validation(
        tmp_path, monkeypatch):
    """临时构建场景没有 manifest, 启动点只能复核 scene.verify()。"""
    import stage1.scene as scene_mod

    class CheckingScene(FakeScene):
        def __init__(self):
            self.verify_calls = 0

        def verify(self):
            self.verify_calls += 1
            return {"ok": True, "checks": []}

    scene = CheckingScene()
    monkeypatch.setattr(scene_mod, "build_scene",
                        lambda sim, clear=True, layout=None: scene)
    artifact_validations = []
    monkeypatch.setattr(sa, "validate_artifact",
                        lambda *a, **k: artifact_validations.append(1))
    mgr = _manager(tmp_path)
    mgr.build()
    mgr.prepare_start()
    assert scene.verify_calls == 1
    assert artifact_validations == []
    mgr.finish_run()


def test_enter_background_releases_lock_but_keeps_running_state(tmp_path):
    import stage1.scene as scene_mod

    monkeypatch_ok = True
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(scene_mod, "build_scene",
                        lambda sim, clear=True, layout=None: FakeScene())
    try:
        mgr = _manager(tmp_path)
        mgr.build()
        mgr.prepare_start()
        mgr.enter_background()
        assert mgr.run_lock.owner() is None
        assert mgr.sm.state is LifecycleState.SIMULATION_RUNNING
        assert mgr.session.run_id is None
        assert mgr.session.background is True
        # 之后 stop 必须能回收
        mgr.sim._state = 1
        mgr.stop()
        assert mgr.sm.state is LifecycleState.SCENE_VALIDATED
        assert mgr.session.background is False
    finally:
        monkeypatch.undo()


def test_refresh_clears_dead_lock_and_reports_stale_run(tmp_path):
    mgr = _manager(tmp_path, FakeSim(state=1))
    mgr.session.state = LifecycleState.SIMULATION_RUNNING
    mgr.session.scene_origin = "BUILT"
    mgr.session_store.save(mgr.session)
    mgr.sm.state = LifecycleState.SIMULATION_RUNNING
    mgr.run_lock.path.parent.mkdir(parents=True, exist_ok=True)
    mgr.run_lock.path.write_text(
        json.dumps({"pid": 999999, "run_id": "dead"}), encoding="utf-8")
    mgr.run_lock._pid_alive = lambda pid: False

    info = mgr.refresh()
    assert info["stale_run"] is True
    assert not mgr.run_lock.path.exists()
    assert mgr.sm.state is LifecycleState.SIMULATION_RUNNING

    mgr.sim._state = 0
    mgr.stop()
    assert mgr.sm.state is LifecycleState.SCENE_VALIDATED
    assert mgr.session.background is False


def test_refresh_clears_dead_lock_even_when_not_running(tmp_path):
    mgr = _manager(tmp_path)
    assert mgr.sm.state is LifecycleState.CONNECTED_STOPPED
    mgr.run_lock.path.parent.mkdir(parents=True, exist_ok=True)
    mgr.run_lock.path.write_text(
        json.dumps({"pid": 999999, "run_id": "dead"}), encoding="utf-8")
    mgr.run_lock._pid_alive = lambda pid: False

    info = mgr.refresh()
    assert info["stale_lock_cleared"]["pid"] == 999999
    assert not mgr.run_lock.path.exists()
