# tests/conftest.py
"""MCP 工具测试共享 fixture — shared fixtures for MCP tool tests."""
import sys
from pathlib import Path

# 让 tests/ 能 import src/factory_*
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest
from factory_mcp_server import FactoryMCPServer


def _coppeliasim_up(host: str = "localhost", port: int = 23000) -> bool:
    """探测 CoppeliaSim ZMQ 是否在线 — 不抛,只返回 bool。"""
    try:
        from coppeliasim_zmqremoteapi_client import RemoteAPIClient
        c = RemoteAPIClient(host=host, port=port)
        sim = c.require("sim")
        sim.getSimulationState()  # 探针
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def coppeliasim_available():
    return _coppeliasim_up()


@pytest.fixture(scope="session")
def mcp_server(coppeliasim_available):
    """已连接的 FactoryMCPServer,断开在 session 结束。"""
    if not coppeliasim_available:
        pytest.skip("CoppeliaSim 未运行,跳过集成测试")
    srv = FactoryMCPServer()
    assert srv.ensure_connected(), "无法连接 CoppeliaSim"
    yield srv
    # 清理: 停仿真 + 断双连接
    if srv.is_running():
        srv.stop()
    # NOTE: ro_connector 由后续任务 (Task 2) 引入, 用 getattr 守护以兼容当前阶段。
    ro = getattr(srv, "ro_connector", None)
    if ro is not None:
        try:
            ro.client = None
        except Exception:
            pass
    srv.connector = None
