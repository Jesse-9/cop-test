# tests/conftest.py
"""MCP 工具测试共享 fixture — shared fixtures for MCP tool tests."""
import sys
from pathlib import Path

# 让 tests/ 能 import src/factory_*
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest
import factory_mcp_server
from factory_mcp_server import FactoryMCPServer


def _coppeliasim_up(host: str = "localhost", port: int = 23000,
                    timeout: float = 3.0) -> bool:
    """探测 CoppeliaSim ZMQ 是否在线 — 不抛,只返回 bool。

    带超时: 若 ZMQ 端口存活但引擎冻结 (探针阻塞), 超时后返回 False
    让测试 skip 而非挂起整个 suite。
    """
    import threading
    result = {"up": False}

    def _probe():
        try:
            from coppeliasim_zmqremoteapi_client import RemoteAPIClient
            c = RemoteAPIClient(host=host, port=port)
            sim = c.require("sim")
            sim.getSimulationState()  # 探针
            result["up"] = True
        except Exception:
            result["up"] = False

    t = threading.Thread(target=_probe, daemon=True)
    t.start()
    t.join(timeout=timeout)
    # 线程仍活 = 探针阻塞 (引擎冻结) → 视为不可用, 让测试 skip
    return result["up"] and not t.is_alive()


@pytest.fixture(scope="session")
def coppeliasim_available():
    return _coppeliasim_up()


@pytest.fixture(scope="session")
def mcp_server(coppeliasim_available):
    """已连接的 FactoryMCPServer,断开在 session 结束。

    必须绑定模块级单例 `factory_mcp_server.server` — MCP 工具函数
    (get_object_pose / list_objects 等) 通过闭包引用该单例, 若 fixture
    创建新实例则工具仍操作未连接的单例, 导致测试静默 skip-by-failure。
    """
    if not coppeliasim_available:
        pytest.skip("CoppeliaSim 未运行,跳过集成测试")
    srv = factory_mcp_server.server  # 模块级单例, 与 @mcp.tool 函数引用一致
    assert srv.ensure_connected(), "无法连接 CoppeliaSim"
    yield srv
    # 清理: 停仿真 + 断双连接 (null 引用以免泄漏到下一轮测试)
    if srv.is_running():
        srv.stop()
    ro = getattr(srv, "ro_connector", None)
    if ro is not None:
        try:
            ro.client = None
        except Exception:
            pass
        srv.ro_connector = None
    srv.connector = None
