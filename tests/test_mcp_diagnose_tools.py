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
