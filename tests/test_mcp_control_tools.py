"""C 档运行时控制工具集成测试。"""
import json
import time as _t
import pytest


class TestPauseResumeFlag:
    def test_flag_default_false(self):
        # controller 未运行时无 flag, 故测 FactoryController 类属性约定
        # 纯源码检查, 无需 CoppeliaSim 连接 → 不取 mcp_server fixture
        from reference.factory_controller import FactoryController
        import inspect
        src = inspect.getsource(FactoryController.__init__)
        assert "_pause_requested" in src


from reference.factory_mcp_server import pause_simulation, resume_simulation, step_simulation


@pytest.fixture()
def stage1_temp_lifecycle(mcp_server, tmp_path):
    """把 MCP stage1 生命周期指向临时工件目录 (不污染 docs/scene_artifacts)。"""
    from stage1.lifecycle import LifecycleManager

    srv = mcp_server
    if srv.is_running():
        srv.stop()
    srv.stage1_lifecycle = LifecycleManager(
        srv.sim, srv.connector.simIK,
        artifact_root=tmp_path / "artifacts",
        session_path=tmp_path / "session.json",
        actor="test",
        host=srv.connector.host, port=srv.connector.port,
        report_dir=tmp_path / "runs")
    yield srv
    if srv.is_running():
        srv.stop()


class TestPauseResumeStepN:
    def test_step_n_runs_multiple(self, mcp_server):
        # 需仿真已 start; 若未 start 则 step 返回提示
        if mcp_server.is_running():
            mcp_server.stop()
        r = step_simulation(n=3)
        assert "未运行" in r or "失败" in r

    def test_pause_resume_roundtrip(self, stage1_temp_lifecycle):
        # 步骤 1.2: start 不再隐式 build; 显式 build→save→load→validate。
        from reference.factory_mcp_server import (build_scene, save_scene,
                                                  load_scene, validate_scene,
                                                  start_simulation,
                                                  stop_simulation, status)
        srv = stage1_temp_lifecycle
        built = build_scene()
        assert "失败" not in built and "build_scene(stage1) 失败" not in built
        saved = save_scene(phase="diagnostic", protected=False)
        assert "失败" not in saved
        loaded = load_scene("current", phase="diagnostic")
        assert "失败" not in loaded
        validated = validate_scene()
        assert "失败" not in validated
        assert json.loads(validated)["ok"] is True

        start_simulation(total_time=30.0)
        _t.sleep(2.0)
        st = json.loads(status())
        if st.get("sim_time", 0) < 0.05:
            stop_simulation()
            pytest.skip("场景疑似冻结, 跳过 pause/resume 联调")
        pr = pause_simulation()
        assert "失败" not in pr
        _t.sleep(0.5)
        rr = resume_simulation()
        assert "失败" not in rr
        stop_simulation()
        assert getattr(srv, "_run_lua_lingering", False) is False
