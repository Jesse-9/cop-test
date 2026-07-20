# tests/test_mcp_diagnose_tools.py
"""B 档诊断工具集成测试。"""
import json
import pytest


class TestInspectSceneState:
    def test_summary_mode(self, mcp_server):
        from factory_mcp_server import inspect_scene_state
        r = inspect_scene_state(summary=True)
        if r.startswith("失败"):
            pytest.skip("未连接")
        d = json.loads(r)
        assert "state_code" in d
        assert "sim_time" in d
        assert "object_count" in d
        assert "poses" not in d  # summary 模式不带全量位姿

    def test_full_mode(self, mcp_server):
        from factory_mcp_server import inspect_scene_state
        r = inspect_scene_state(summary=False)
        if r.startswith("失败"):
            pytest.skip("未连接")
        d = json.loads(r)
        assert "poses" in d
        assert isinstance(d["poses"], list)


from factory_mcp_server import _audit_lua_code, _LUA_MAX_CHARS


class TestAuditLua:
    def test_safe_code_passes(self):
        ok, msg = _audit_lua_code('sim.setStringSignal("x", "hi")')
        assert ok is True

    def test_dangerous_needs_confirm(self):
        ok, msg = _audit_lua_code('os.execute("rm -rf /")')
        assert ok is False
        assert "confirm" in msg.lower()

    def test_dangerous_with_confirm_passes(self):
        ok, msg = _audit_lua_code('os.execute("rm -rf /")', confirm=True)
        assert ok is True

    def test_too_long_rejected(self):
        ok, msg = _audit_lua_code("x = " + "1" * (_LUA_MAX_CHARS + 1))
        assert ok is False
        assert "长度" in msg or "len" in msg.lower() or "8000" in msg

    def test_popen_caught(self):
        ok, msg = _audit_lua_code('local f = io.popen("ls")')
        assert ok is False
