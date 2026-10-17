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


class TestGetObjectPose:
    def test_pose_default_components(self, mcp_server):
        from reference.factory_mcp_server import get_object_pose
        r = get_object_pose(alias_or_path="Floor")  # build_scene 创建的地板
        if r.startswith("失败"):
            pytest.skip("场景未构建,跳过")
        d = json.loads(r)
        assert "alias" in d
        assert "pose" in d or "position" in d

    def test_unknown_alias_returns_failure_string(self, mcp_server):
        from reference.factory_mcp_server import get_object_pose
        r = get_object_pose(alias_or_path="NoSuchObject_xyz")
        assert r.startswith("失败") or "找不到" in r


class TestListObjects:
    def test_list_all(self, mcp_server):
        from reference.factory_mcp_server import list_objects
        r = list_objects()
        if r.startswith("失败"):
            pytest.skip("未连接")
        d = json.loads(r)
        assert "objects" in d
        assert isinstance(d["objects"], list)
        assert d["count"] == len(d["objects"])

    def test_filter_prefix(self, mcp_server):
        from reference.factory_mcp_server import list_objects
        r = list_objects(filter="Island_*")
        if r.startswith("失败"):
            pytest.skip("未连接")
        d = json.loads(r)
        for obj in d["objects"]:
            assert "Island_" in obj["alias"] or obj["alias"].startswith("_Island_")


class TestReadSignal:
    def test_read_integer_signal(self, mcp_server):
        from reference.factory_mcp_server import read_signal
        server = mcp_server
        server.ensure_ro_connected()
        sim = server.ro_sim
        sim.setIntegerSignal("mcp_test_sig", 123)
        r = read_signal(name="mcp_test_sig")
        d = json.loads(r)
        assert d["name"] == "mcp_test_sig"
        assert d["type"] == "integer"
        assert d["value"] == 123

    def test_read_missing_signal(self, mcp_server):
        from reference.factory_mcp_server import read_signal
        r = read_signal(name="definitely_no_such_signal_xyz")
        d = json.loads(r)
        assert d["value"] is None
        assert d["type"] == "none"


class TestGetObjectTree:
    def test_tree_of_root(self, mcp_server):
        from reference.factory_mcp_server import get_object_tree
        from reference.factory_mcp_server import list_objects
        lo = list_objects(filter="*")
        d_lo = json.loads(lo)
        if not d_lo["objects"]:
            pytest.skip("场景为空")
        root_alias = d_lo["objects"][0]["alias"].lstrip("_")
        r = get_object_tree(root_alias=root_alias)
        d = json.loads(r)
        assert "root" in d
        assert "children" in d
        assert isinstance(d["children"], list)
