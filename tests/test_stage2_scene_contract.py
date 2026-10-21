"""阶段二工件契约与生命周期扩展点测试 (06-09 §8 / §12)."""

import copy
from dataclasses import dataclass

from stage1.lifecycle import (_default_extra_validator,
                              _default_manifest_extra,
                              _merge_extra_validation)
from stage2 import config as cfg
from stage2.scene_contract import (build_stage2_extra,
                                   stage2_config_digest,
                                   validate_stage2_contract)


class FakeSim:
    object_shape_type = 0
    object_dummy_type = 1
    object_joint_type = 2
    object_proximitysensor_type = 3
    object_visionsensor_type = 4
    object_graph_type = 5
    object_light_type = 6
    object_camera_type = 7
    object_mirror_type = 8
    object_octree_type = 9
    object_pointcloud_type = 10
    object_forcesensor_type = 11
    object_script_type = 12

    def __init__(self, aliases=()):
        self.aliases = list(aliases)
        self.handle_all = -1

    def getObjects(self, index, what):
        if what == self.object_dummy_type and index < len(self.aliases):
            return 100 + index
        return -1

    def getObjectAlias(self, handle):
        return self.aliases[handle - 100]

    def getObjectType(self, handle):
        return self.object_dummy_type


@dataclass
class FakeArtifact:
    manifest: dict


def artifact_with(extra):
    return FakeArtifact({"extra": {"stage2": extra},
                         "parent_artifact": {"artifact_id":
                                             "stage1/L1_S1/v001"}})


def test_stage2_extra_has_contract_and_is_deterministic():
    extra = build_stage2_extra(parent_info={"artifact_id":
                                            "stage1/L1_S1/v001"})
    assert extra["contract_version"] == cfg.STAGE2_CONTRACT_VERSION
    assert extra["controller"]["id"] == cfg.STAGE2_CONTROLLER_ID
    assert extra["config"]["digest"] == stage2_config_digest()
    assert extra["inheritance"]["parent_artifact"] == "stage1/L1_S1/v001"
    again = build_stage2_extra(parent_info={"artifact_id":
                                            "stage1/L1_S1/v001"})
    assert again["config"]["digest"] == extra["config"]["digest"]


def test_validate_stage2_contract_passes_for_current_contract():
    extra = build_stage2_extra(parent_info={
        "artifact_id": "stage1/L1_S1/v001"})
    result = validate_stage2_contract(FakeSim(), artifact_with(extra))
    assert result["ok"], [c for c in result["checks"] if not c["ok"]]


def test_version_mismatch_rejected():
    extra = build_stage2_extra()
    extra["contract_version"] = "stage2_contract_old"
    result = validate_stage2_contract(FakeSim(), artifact_with(extra))
    assert not result["ok"]
    assert any(c["name"] == "stage2_contract_version" and not c["ok"]
               for c in result["checks"])


def test_controller_mismatch_rejected():
    extra = build_stage2_extra()
    extra["controller"]["id"] = "stage1.unit_controller.Stage1UnitController"
    result = validate_stage2_contract(FakeSim(), artifact_with(extra))
    assert not result["ok"]


def test_config_digest_mismatch_rejected():
    extra = build_stage2_extra()
    extra["config"]["digest"] = "deadbeef"
    result = validate_stage2_contract(FakeSim(), artifact_with(extra))
    assert not result["ok"]
    assert any(d["kind"] == "stage2_config"
               for d in result["differences"])


def test_persisted_session_resource_rejected():
    extra = build_stage2_extra()
    result = validate_stage2_contract(
        FakeSim(["Shadow_Load_01"]), artifact_with(extra))
    assert not result["ok"]
    assert any(c["name"] == "no_session_resources_persisted"
               and not c["ok"] for c in result["checks"])


def test_missing_extra_rejected():
    artifact = FakeArtifact({"extra": {}, "parent_artifact": {}})
    result = validate_stage2_contract(FakeSim(), artifact)
    assert not result["ok"]


def test_lifecycle_default_extra_provider_only_for_stage2():
    assert _default_manifest_extra("stage1", None, None, None) is None
    extra = _default_manifest_extra("stage2", None, None,
                                    {"artifact_id": "stage1/x"})
    assert set(extra) == {"stage2"}
    assert extra["stage2"]["controller"]["id"] == cfg.STAGE2_CONTROLLER_ID


def test_lifecycle_extra_validator_skips_stage1():
    artifact = FakeArtifact({"extra": {}, "parent_artifact": {}})
    assert _default_extra_validator(artifact, None, None) is None


def test_merge_extra_validation_fails_closed():
    base = {"ok": True, "checks": [], "differences": []}
    merged = _merge_extra_validation(base, {
        "ok": False,
        "checks": [{"name": "x", "ok": False}],
        "differences": [{"kind": "x"}],
        "contract_version": "v",
    })
    assert merged["ok"] is False
    assert merged["stage2_contract"]["ok"] is False
    assert len(merged["differences"]) == 1
