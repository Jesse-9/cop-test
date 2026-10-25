"""阶段三工件契约、manifest 阶段与生命周期扩展点测试 (06-12 §3.0/§6)."""

from dataclasses import dataclass

from stage1 import config as base
from stage1 import scene_artifact as sa
from stage1.lifecycle import (SessionState, _default_extra_validator,
                              _default_manifest_extra, _merge_extra_validation)
from stage2.scene_contract import stage2_config_digest
from stage2 import config as s2cfg
from stage3 import config as cfg
from stage3.scene_contract import (build_stage3_extra,
                                   stage3_config_digest,
                                   stage3_layout_digest,
                                   validate_stage3_contract)


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
    handle_all = -1

    def __init__(self, aliases=()):
        self.aliases = list(aliases)

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


def _parent_info():
    return {
        "artifact_id": "stage2/L1_S1/v013",
        "manifest_digest": "abc123",
        "source_phase": "stage2",
        "stage2_contract_version": s2cfg.STAGE2_CONTRACT_VERSION,
        "stage2_config_digest": stage2_config_digest(),
    }


def _extra():
    return build_stage3_extra(parent_info=_parent_info())


def _artifact(extra):
    return FakeArtifact({
        "source_phase": "stage3",
        "parent_artifact": {"artifact_id": "stage2/L1_S1/v013"},
        "extra": {"stage3": extra},
    })


def test_extra_is_deterministic_and_records_parent():
    first = _extra()
    again = _extra()
    assert first["contract_version"] == cfg.STAGE3_CONTRACT_VERSION
    assert first["controller"]["id"] == cfg.STAGE3_CONTROLLER_ID
    assert first["controller"]["coordinator"] == cfg.STAGE3_COORDINATOR_ID
    assert first["controller"]["runner"] == cfg.STAGE3_RUNNER_ID
    assert first["config"]["digest"] == stage3_config_digest()
    assert first["layout"]["digest"] == stage3_layout_digest()
    assert first["config"]["digest"] == again["config"]["digest"]
    assert first["inheritance"]["parent_artifact"] == "stage2/L1_S1/v013"
    assert first["inheritance"]["scene_changed"] is True
    assert first["inheritance"]["parent_stage2_contract_version"] == \
        s2cfg.STAGE2_CONTRACT_VERSION


def test_validator_passes_for_current_contract_and_aliases():
    result = validate_stage3_contract(
        FakeSim(cfg.REQUIRED_ALIASES), _artifact(_extra()))
    assert result["ok"], [c for c in result["checks"] if not c["ok"]]


def test_validator_rejects_contract_and_config_drift():
    extra = _extra()
    extra["contract_version"] = "stage3_old"
    result = validate_stage3_contract(FakeSim(cfg.REQUIRED_ALIASES),
                                      _artifact(extra))
    assert not result["ok"]
    assert any(c["name"] == "stage3_contract_version" and not c["ok"]
               for c in result["checks"])

    extra = _extra()
    extra["config"]["digest"] = "deadbeef"
    result = validate_stage3_contract(FakeSim(cfg.REQUIRED_ALIASES),
                                      _artifact(extra))
    assert not result["ok"]
    assert any(d["kind"] == "stage3_config"
               for d in result["differences"])

    extra = _extra()
    extra["layout"]["digest"] = "deadbeef"
    result = validate_stage3_contract(FakeSim(cfg.REQUIRED_ALIASES),
                                      _artifact(extra))
    assert not result["ok"]
    assert any(d["kind"] == "stage3_layout"
               for d in result["differences"])


def test_validator_rejects_stage2_dependency_drift():
    extra = _extra()
    extra["inheritance"]["parent_stage2_config_digest"] = "old"
    extra["inheritance"]["parent_stage2_contract_version"] = "old"
    result = validate_stage3_contract(FakeSim(cfg.REQUIRED_ALIASES),
                                      _artifact(extra))
    assert not result["ok"]
    names = {c["name"] for c in result["checks"] if not c["ok"]}
    assert "stage3_stage2_config_digest" in names
    assert "stage3_stage2_contract_version" in names


def test_validator_rejects_persisted_shadow_resources():
    aliases = list(cfg.REQUIRED_ALIASES) + ["Shadow_UR5_02"]
    result = validate_stage3_contract(FakeSim(aliases), _artifact(_extra()))
    assert not result["ok"]
    assert any(c["name"] == "stage3_no_session_resources" and not c["ok"]
               for c in result["checks"])


def test_validator_rejects_missing_extra_or_aliases():
    result = validate_stage3_contract(FakeSim(), FakeArtifact({
        "source_phase": "stage3", "parent_artifact": {},
        "extra": {}}))
    assert not result["ok"]
    result = validate_stage3_contract(
        FakeSim(cfg.REQUIRED_ALIASES[:-1]), _artifact(_extra()))
    assert not result["ok"]


def test_phases_and_required_aliases_include_stage3():
    assert "stage3" in sa.PHASES
    required = sa.required_aliases_for_phase("stage3")
    assert "Robot_UR5_01" in required
    assert "Robot_UR5_02" in required
    assert "Sensor_AGV_Dock_B" in required
    stage1_required = sa.required_aliases_for_phase("stage1")
    assert "Robot_UR5_02" not in stage1_required


def test_lifecycle_extra_provider_and_validator_route_stage3():
    extra = _default_manifest_extra("stage3", None, None, _parent_info())
    assert set(extra) == {"stage3"}
    assert extra["stage3"]["controller"]["id"] == cfg.STAGE3_CONTROLLER_ID
    artifact = _artifact(_extra())
    result = _default_extra_validator(artifact, FakeSim(cfg.REQUIRED_ALIASES),
                                      None)
    assert result["ok"]


def test_merge_extra_validation_stage3_fails_closed():
    base_result = {"ok": True, "checks": [], "differences": []}
    merged = _merge_extra_validation(base_result, {
        "ok": False,
        "checks": [{"name": "x", "ok": False}],
        "differences": [{"kind": "x"}],
        "contract_version": cfg.STAGE3_CONTRACT_VERSION,
    })
    assert merged["ok"] is False
    assert merged["stage3_contract"]["ok"] is False
    assert "stage2_contract" not in merged


def test_session_state_roundtrip_keeps_built_phase_and_layout():
    from stage1.lifecycle import LifecycleState
    state = SessionState(state=LifecycleState.SCENE_VALIDATED)
    state.built_phase = "stage3"
    state.built_layout = {"name": "L2_MIRROR", "units": []}
    restored = SessionState.from_dict(state.to_dict())
    assert restored.built_phase == "stage3"
    assert restored.built_layout == {"name": "L2_MIRROR", "units": []}


def test_material_chain_allows_stage3_tray_reconfirmation():
    from stage1.material_manager import MaterialManager, MaterialState
    manager = MaterialManager()
    manager.register(1, "Part_0001")
    for state in (MaterialState.ON_CONVEYOR, MaterialState.READY_FOR_PICK,
                  MaterialState.HELD_BY_SUCTION, MaterialState.ON_TRAY,
                  MaterialState.RELEASED):
        manager.transition(1, state, 1.0, "chain")
    manager.transition(1, MaterialState.ON_TRAY, 2.0, "confirmed on AGV")
    manager.transition(1, MaterialState.READY_FOR_PICK, 3.0, "stage3 pick")
    assert manager.by_uid(1).state is MaterialState.READY_FOR_PICK
