"""MCP stage1 后端路由与状态单测 (不需要 CoppeliaSim)."""

import json
import types

import pytest

import reference.factory_mcp_server as mcp_server
from reference.factory_mcp_server import FactoryMCPServer


class FakeScene:
    build_report = {"ok": True, "checks": [{"name": "fake", "ok": True}]}
    layout = types.SimpleNamespace(name="L1_S1")
    robot_joints = [1, 2, 3, 4, 5, 6]

    def tool_geometry(self):
        return {"version": "test", "ok": True}


def _server_with_fake_connector():
    srv = FactoryMCPServer()
    srv.connector = types.SimpleNamespace(sim=object())
    srv.ensure_connected = lambda *a, **k: True
    return srv


def test_build_scene_defaults_to_stage1(monkeypatch):
    import stage1.scene as stage1_scene

    monkeypatch.setattr(stage1_scene, "build_scene",
                        lambda sim, clear=True: FakeScene())
    srv = _server_with_fake_connector()
    out = json.loads(srv.build_scene(clear=True))
    assert out["backend"] == "stage1"
    assert out["ok"] is True
    assert out["layout"] == "L1_S1"
    assert srv.backend == "stage1"
    assert srv.stage1_scene is not None


def test_build_scene_legacy_routing(monkeypatch):
    import reference.build_scene as legacy_scene

    calls = []
    monkeypatch.setattr(legacy_scene, "clear_scene",
                        lambda sim: calls.append("clear"))
    monkeypatch.setattr(legacy_scene, "build_scene",
                        lambda sim: calls.append("build"))
    srv = _server_with_fake_connector()
    out = srv.build_scene(clear=True, backend="legacy")
    assert "legacy" in out
    assert calls == ["clear", "build"]
    assert srv.backend == "legacy"


def test_build_scene_rejects_unknown_backend():
    srv = _server_with_fake_connector()
    assert "未知 backend" in srv.build_scene(backend="nope")


def test_start_rejects_unknown_backend():
    srv = FactoryMCPServer()
    assert "未知 backend" in srv.start(0.0, "x.log", backend="nope")


def test_stage1_status_fields():
    class Rec:
        state = types.SimpleNamespace(value="RELEASED")
        sim_handle = 123

    class Materials:
        def by_uid(self, uid):
            return Rec()

    class Agv:
        state = types.SimpleNamespace(name="IDLE")

    class Conveyor:
        def aliases(self):
            return ["Conveyor_In"]

        def read_actual_velocity(self, alias):
            return 0.2

    ctrl = types.SimpleNamespace(
        sim_time=12.3, state=types.SimpleNamespace(name="DONE"),
        failure_reason="", uid=1, target="station", fault="none",
        agv_blocked=False, compensation_count=0,
        materials=Materials(), agv=Agv(), conveyor=Conveyor(),
    )
    srv = FactoryMCPServer()
    srv.backend = "stage1"
    srv.stage1_scene = FakeScene()
    srv.stage1_controller = ctrl
    srv.stage1_results = [{"ok": True, "target": "station"}]
    srv.stage1_report_path = "docs/implementation/runs/x.json"

    st = srv.status()
    assert st["backend"] == "stage1"
    assert st["unit_state"] == "DONE"
    assert st["material_state"] == "RELEASED"
    assert st["agv_state"] == "IDLE"
    assert st["conveyor_velocity"] == 0.2
    assert st["results"][0]["ok"] is True
    assert st["report_path"].endswith("x.json")


def test_get_config_stage1_and_legacy():
    d = json.loads(mcp_server.get_config(backend="stage1"))
    assert "SUCTION_MOUNT_Z" in d
    assert "SUCTION_TOOL_VERSION" in d
    legacy = json.loads(mcp_server.get_config(backend="legacy"))
    assert "TAKT_TIME" in legacy


def test_set_config_stage1_roundtrip():
    import stage1.config as s1cfg

    old = s1cfg.SUCTION_CONTACT_TOL
    try:
        msg = mcp_server.set_config(
            "SUCTION_CONTACT_TOL", old + 0.001, backend="stage1")
        assert "→" in msg
        assert s1cfg.SUCTION_CONTACT_TOL == pytest.approx(old + 0.001)
    finally:
        s1cfg.SUCTION_CONTACT_TOL = old
