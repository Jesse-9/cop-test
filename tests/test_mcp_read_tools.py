"""A 档读取工具集成测试。"""
import json
import pytest


@pytest.mark.usefixtures("mcp_server")
class TestRoConnector:
    def test_ro_connector_lazy_init(self, mcp_server):
        assert mcp_server.ensure_ro_connected() is True
        assert mcp_server.ro_connector is not None
        assert mcp_server.ro_connector.check_alive() is True

    def test_ro_sim_available(self, mcp_server):
        mcp_server.ensure_ro_connected()
        assert mcp_server.ro_sim is not None
