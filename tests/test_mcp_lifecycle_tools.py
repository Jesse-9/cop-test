"""MCP 场景生命周期工具路由单测 (不需要 CoppeliaSim)。"""

import json
import types

import reference.factory_mcp_server as mcp_server
from reference.factory_mcp_server import FactoryMCPServer


class FakeManager:
    def __init__(self):
        self.scene = types.SimpleNamespace(
            build_report={"ok": True, "checks": []},
            layout=types.SimpleNamespace(name="L1_S1"),
            robot_joints=[1, 2, 3, 4, 5, 6],
            tool_geometry=lambda: {"ok": True})
        self.sm = types.SimpleNamespace(
            state=types.SimpleNamespace(value="SCENE_VALIDATED"))
        self.session = types.SimpleNamespace(
            artifact_id="stage1/L1_S1/v001_test", run_id=None,
            scene_origin="ARTIFACT", manifest_digest="abc",
            validated_at="now")
        self.calls = []

    def save(self, **kwargs):
        self.calls.append(("save", kwargs))
        return {"artifact_id": "stage1/L1_S1/v002_test",
                "lifecycle_state": "SCENE_VALIDATED"}

    def load(self, artifact, phase=None):
        self.calls.append(("load", artifact, phase))
        return {"artifact_id": artifact,
                "lifecycle_state": "SCENE_LOADED",
                "requires_validation": True}

    def validate(self, artifact=None):
        self.calls.append(("validate", artifact))
        return {"ok": True, "artifact_id": artifact or "current",
                "checks": [], "differences": []}

    def status(self):
        return {"lifecycle_state": "SCENE_VALIDATED",
                "allowed_actions": ["start", "load"]}

    def stop(self):
        self.calls.append(("stop",))
        return {"lifecycle_state": "SCENE_VALIDATED", "stopped": True}

    def allowed_actions(self):
        return ["start", "load"]


def _server(manager=None):
    srv = FactoryMCPServer()
    manager = manager or FakeManager()
    srv.stage1_lifecycle = manager
    srv._ensure_lifecycle = lambda: manager
    return srv, manager


def test_save_scene_delegates_and_updates_scene():
    srv, manager = _server()
    out = json.loads(srv.save_scene(phase="stage2", parent="p1",
                                    note="n", protected=False))
    assert out["ok"] is True
    assert out["artifact_id"].endswith("v002_test")
    assert manager.calls == [("save", {
        "phase": "stage2", "parent": "p1", "note": "n",
        "protected": False})]
    assert srv.backend == "stage1"


def test_save_scene_rejected_while_running():
    srv, manager = _server()
    srv.is_running = lambda: True
    assert "运行中" in srv.save_scene()
    assert manager.calls == []


def test_load_scene_delegates_and_clears_components():
    srv, manager = _server()
    srv.stage1_comp = object()
    out = json.loads(srv.load_scene("current", phase="stage1"))
    assert out["lifecycle_state"] == "SCENE_LOADED"
    assert out["requires_validation"] is True
    assert manager.calls == [("load", "current", "stage1")]
    assert srv.stage1_comp is None


def test_load_scene_rejected_while_running():
    srv, manager = _server()
    srv.is_running = lambda: True
    assert "运行中" in srv.load_scene("x")
    assert manager.calls == []


def test_validate_scene_delegates():
    srv, manager = _server()
    out = json.loads(srv.validate_scene())
    assert out["ok"] is True
    assert manager.calls == [("validate", None)]


def test_lifecycle_status_delegates():
    srv, _ = _server()
    out = json.loads(srv.lifecycle_status())
    assert out["lifecycle_state"] == "SCENE_VALIDATED"


def test_lifecycle_tools_return_error_when_not_connected():
    srv = FactoryMCPServer()
    assert srv.sim is None
    out = srv.lifecycle_status()
    assert "error" in json.loads(out)


def test_stop_without_local_runner_delegates_to_lifecycle():
    """显式 stop 也应清理外部/遗留运行, 而不是直接返回“未运行”。"""
    srv, manager = _server()
    out = json.loads(srv.stop())
    assert out["stopped"] is True
    assert manager.calls == [("stop",)]
