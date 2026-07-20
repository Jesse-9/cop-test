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


class TestRunLuaExecution:
    def test_simple_arithmetic(self, mcp_server):
        from factory_mcp_server import run_lua
        r = run_lua(code='sim.setStringSignal("mcp_run_lua_result", tostring(6*7))')
        d = json.loads(r)
        assert d["ok"] is True
        assert "42" in d["result"]
        assert d["lingering"] is False

    def test_capture_error(self, mcp_server):
        from factory_mcp_server import run_lua
        r = run_lua(code='error("intentional boom")')
        d = json.loads(r)
        assert d["ok"] is False
        assert "boom" in d["result"]

    def test_dangerous_blocked_without_confirm(self, mcp_server):
        from factory_mcp_server import run_lua
        r = run_lua(code='os.execute("echo hi")', confirm=False)
        d = json.loads(r)
        assert d["ok"] is False
        assert "confirm" in d["result"].lower()

    def test_dry_run_does_not_execute(self, mcp_server):
        from factory_mcp_server import run_lua
        r = run_lua(code='error("should not run")', dry_run=True)
        d = json.loads(r)
        assert d["ok"] is True
        assert "dry" in d["result"].lower() or "未执行" in d["result"]
