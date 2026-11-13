"""v1 契约校验纯逻辑测试: 不连接 CoppeliaSim."""

from __future__ import annotations

from types import SimpleNamespace

from flexible_factory.artifacts import store as sa
from flexible_factory.simulation import config as cfg
from flexible_factory.simulation import contract, scene_base


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


class _IndexedFakeSim(_FakeSim):
    """带一个 shape 对象的最小假 sim, 用于校验上下文复用测试."""

    simulation_stopped = 0
    handle_world = 0

    def __init__(self):
        super().__init__()
        self.objects = {10: "Floor"}
        self.get_objects_calls = 0

    def getObjects(self, index, object_type):
        self.get_objects_calls += 1
        if object_type == self.object_shape_type and index == 0:
            return 10
        return -1

    def getSimulationState(self):
        return 0

    def getObjectAlias(self, handle):
        return self.objects.get(int(handle), "")

    def getObjectType(self, handle):
        return self.object_shape_type

    def getObjectParent(self, handle):
        return -1

    def getObjectPosition(self, handle, reference):
        return [0.0, 0.0, 0.0]

    def getObjectInt32Param(self, handle, param):
        return 0


class _CountingScene:
    def __init__(self):
        self.verify_calls = 0

    def verify(self):
        self.verify_calls += 1
        return {"ok": True, "checks": [], "differences": []}


class _BrokenScene:
    def verify(self):
        raise RuntimeError("boom")


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


def test_validation_context_reuses_scene_enumeration(monkeypatch):
    monkeypatch.setattr(cfg, "REQUIRED_ALIASES", [])
    sim = _IndexedFakeSim()
    scene = _CountingScene()
    context = scene_base.build_validation_context(sim, scene)
    assert scene.verify_calls == 1
    calls = sim.get_objects_calls

    manifest = {"artifact_id": "test", "objects": [{
        "alias": "Floor", "type": "shape", "parent_alias": "",
        "world_pos": [0.0, 0.0, 0.0]}]}
    result = sa.validate_scene_against_manifest(
        sim, manifest, scene=scene, required_aliases=[], context=context)
    assert result["ok"], result["differences"]
    assert sim.get_objects_calls == calls
    assert scene.verify_calls == 1

    extra = contract.build_contract_extra(
        provenance={"source_artifact_id": "stage4/L5_UFLOW/v004"})
    contract.validate_contract(
        sim, _artifact(extra), scene=scene, context=context)
    assert sim.get_objects_calls == calls
    assert scene.verify_calls == 1


def test_validation_context_records_scene_error():
    sim = _IndexedFakeSim()
    context = scene_base.build_validation_context(sim, _BrokenScene())
    assert context.scene_report is None
    assert context.scene_error == "RuntimeError: boom"

    result = sa.validate_scene_against_manifest(
        sim, {"artifact_id": "test", "objects": []},
        scene=_BrokenScene(), required_aliases=[], context=context)
    assert not result["ok"]
    assert any(item["name"] == "scene_contract" and not item["ok"]
               for item in result["checks"])


class _NoEnumSim:
    def getObjects(self, index, object_type):
        raise AssertionError("cached context must not enumerate the scene")


class _CachedScene:
    _validation_objects = (10,)
    _validation_alias_index = {"Floor": [10]}

    def verify(self):
        return {"ok": True, "checks": [], "differences": []}


def test_validation_context_reuses_cached_scene_index():
    scene = _CachedScene()
    context = scene_base.build_validation_context(_NoEnumSim(), scene)

    assert context.objects == (10,)
    assert context.alias_index == {"Floor": [10]}

    scene_base.clear_scene_index(scene)
    assert scene._validation_objects is None
    assert scene._validation_alias_index is None
