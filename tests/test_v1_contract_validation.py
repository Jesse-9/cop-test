"""v1 契约校验纯逻辑测试: 不连接 CoppeliaSim."""

from __future__ import annotations

from types import SimpleNamespace

from flexible_factory.simulation import config as cfg
from flexible_factory.simulation import contract


class _FakeSim:
    """满足 all_objects() 的最小假 sim; 场景为空."""

    def __init__(self):
        for index, name in enumerate((
                "object_shape_type", "object_dummy_type", "object_joint_type",
                "object_proximitysensor_type", "object_visionsensor_type",
                "object_graph_type", "object_light_type", "object_camera_type",
                "object_mirror_type", "object_octree_type",
                "object_pointcloud_type", "object_forcesensor_type",
                "object_script_type"), start=1):
            setattr(self, name, index)

    def getObjects(self, index, object_type):
        return -1


def _artifact(extra):
    return SimpleNamespace(manifest={
        "source_phase": "v1",
        "extra": {"v1": extra},
    })


def test_contract_extra_passes_validation(monkeypatch):
    monkeypatch.setattr(cfg, "REQUIRED_ALIASES", [])
    extra = contract.build_contract_extra(
        provenance={"source_artifact_id": "stage4/L5_UFLOW/v004"})
    result = contract.validate_contract(_FakeSim(), _artifact(extra))
    assert result["ok"], [c for c in result["checks"] if not c["ok"]]
    assert result["contract_version"] == extra["contract_version"]


def test_contract_rejects_wrong_version(monkeypatch):
    monkeypatch.setattr(cfg, "REQUIRED_ALIASES", [])
    extra = contract.build_contract_extra()
    extra["contract_version"] = "tampered"
    result = contract.validate_contract(_FakeSim(), _artifact(extra))
    assert not result["ok"]
    failed = {item["name"] for item in result["checks"] if not item["ok"]}
    assert "v1_contract_version" in failed


def test_contract_rejects_resource_order_drift(monkeypatch):
    monkeypatch.setattr(cfg, "REQUIRED_ALIASES", [])
    extra = contract.build_contract_extra()
    extra["resources"]["order"] = list(reversed(extra["resources"]["order"]))
    result = contract.validate_contract(_FakeSim(), _artifact(extra))
    assert not result["ok"]
    failed = {item["name"] for item in result["checks"] if not item["ok"]}
    assert "v1_resource_order" in failed


def test_contract_rejects_missing_provenance(monkeypatch):
    monkeypatch.setattr(cfg, "REQUIRED_ALIASES", [])
    extra = contract.build_contract_extra()
    extra["provenance"] = {}
    result = contract.validate_contract(_FakeSim(), _artifact(extra))
    assert not result["ok"]
    failed = {item["name"] for item in result["checks"] if not item["ok"]}
    assert "v1_provenance_present" in failed
