"""v1 工件仓库 / manifest schema 纯逻辑测试 (不需要 CoppeliaSim)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from flexible_factory.artifacts import store as sa
from flexible_factory.util import stable_digest


def _config_summary():
    return {"v1": {"SCENE_BUILDER_VERSION": "test"}}


def _manifest(artifact_id="v1/L5_UFLOW/v001_test", **overrides):
    manifest = {
        "schema_version": sa.SCHEMA_VERSION,
        "product_version": "1.0.0",
        "scene_contract_version": "v1_contract_test",
        "artifact_id": artifact_id,
        "created_at": "2026-11-03T12:00:00+08:00",
        "source_phase": "v1",
        "protected": True,
        "parent_artifact": {"source_artifact_id": "stage4/L5_UFLOW/v004"},
        "code": {"commit": "abc", "dirty": False},
        "config": {
            "digest": stable_digest(_config_summary()),
            "summary": _config_summary(),
        },
        "simulator": {
            "product_version": "4.10.0.0",
            "version_major_minor": "4.10",
            "engine": "bullet",
            "simulation_step": 0.05,
        },
        "models": [],
        "layout": {"name": "L5_UFLOW"},
        "objects": [
            {"alias": "Floor", "type": "shape", "parent_alias": "",
             "required": True, "world_pos": [0.0, 0.0, 0.0]},
        ],
        "geometry": {},
        "tool": {},
        "physics": {},
        "scripts": {},
        "builder": {"version": "test"},
        "contract": {"ok": True},
        "artifact": {"scene_file": "scene.ttt", "sha256": "0" * 64,
                     "size": 0},
        "extra": {"v1": {"contract_version": "v1"}},
    }
    manifest.update(overrides)
    return manifest


def _publish(store, manifest=None, scene_bytes=b"fake-ttt",
             stamp="20261103_120000"):
    draft = store.create(phase="v1", layout="L5_UFLOW", stamp=stamp)
    scene = draft.path / "scene.ttt"
    scene.write_bytes(scene_bytes)
    data = manifest or _manifest(
        artifact_id=draft.artifact_id,
        artifact={"scene_file": "scene.ttt",
                  "sha256": sa.sha256_file(scene),
                  "size": scene.stat().st_size},
    )
    sa.write_json_atomic(draft.path / "manifest.json", data)
    return store.find(draft.artifact_id)


def test_create_versions_increment(tmp_path):
    store = sa.ArtifactStore(tmp_path)
    first = store.create(phase="v1", layout="L5_UFLOW",
                         stamp="20261103_120000")
    second = store.create(phase="v1", layout="L5_UFLOW",
                          stamp="20261103_120000")
    assert first.artifact_id.endswith("v001_20261103_120000")
    assert second.artifact_id.endswith("v002_20261103_120000")
    assert first.path != second.path


def test_artifact_path_traversal_rejected(tmp_path):
    store = sa.ArtifactStore(tmp_path / "root")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(sa.ArtifactPathError):
        store.resolve("../outside")
    with pytest.raises(sa.ArtifactPathError):
        store.resolve(str(outside))


def test_find_by_id_leaf_and_current(tmp_path):
    store = sa.ArtifactStore(tmp_path)
    artifact = _publish(store)
    assert store.find(artifact.artifact_id).artifact_id == artifact.artifact_id
    assert store.find(artifact.path.name).artifact_id == artifact.artifact_id
    store.write_current(artifact)
    assert store.read_current("v1").artifact_id == artifact.artifact_id
    pointer = json.loads((tmp_path / "v1" / "current.json").read_text(
        encoding="utf-8"))
    assert pointer["manifest_digest"] == artifact.manifest_digest


def test_unknown_artifact_and_current_raise(tmp_path):
    store = sa.ArtifactStore(tmp_path)
    with pytest.raises(sa.ArtifactNotFoundError):
        store.find("v1/L5_UFLOW/v999_missing")
    with pytest.raises(sa.ArtifactNotFoundError):
        store.read_current("v1")


def test_protected_artifact_cannot_be_overwritten(tmp_path):
    store = sa.ArtifactStore(tmp_path)
    artifact = _publish(store)
    with pytest.raises(sa.ProtectedArtifactError):
        store.check_writable(artifact, force=True)


def test_manifest_schema_accepts_valid(tmp_path):
    manifest = _manifest()
    checks = sa.validate_manifest_schema(manifest, ["Floor"])
    assert sa.checks_ok(checks), [c for c in checks if not c["ok"]]


def test_manifest_schema_rejects_slash_alias_and_bad_digest():
    manifest = _manifest()
    manifest["objects"][0]["alias"] = "Island/A"
    manifest["artifact"]["sha256"] = "not-a-digest"
    checks = sa.validate_manifest_schema(manifest, ["Island/A"])
    failed = {item["name"] for item in checks if not item["ok"]}
    assert "manifest_alias_format" in failed
    assert "manifest_scene_digest" in failed


def test_manifest_schema_rejects_missing_required_alias():
    checks = sa.validate_manifest_schema(_manifest(), ["Missing_Alias"])
    failed = {item["name"] for item in checks if not item["ok"]}
    assert "manifest_required_aliases" in failed


def test_manifest_schema_requires_product_and_contract_versions():
    manifest = _manifest()
    manifest.pop("product_version")
    manifest.pop("scene_contract_version")
    manifest["extra"] = {}
    checks = sa.validate_manifest_schema(manifest, ["Floor"])
    failed = {item["name"] for item in checks if not item["ok"]}
    assert "manifest_product_version" in failed
    assert "manifest_scene_contract_version" in failed


def test_manifest_schema_accepts_versions_from_extra_v1():
    manifest = _manifest()
    manifest.pop("product_version")
    manifest.pop("scene_contract_version")
    manifest["extra"] = {"v1": {"product_version": "1.0.0",
                                "contract_version": "v1_contract_test"}}
    checks = sa.validate_manifest_schema(manifest, ["Floor"])
    assert sa.checks_ok(checks), [c for c in checks if not c["ok"]]


def test_environment_compare_reports_config_drift():
    manifest = _manifest()
    changed = {"v1": {"SCENE_BUILDER_VERSION": "different"}}
    result = sa.validate_manifest_environment(
        manifest, config=changed, config_digest=stable_digest(changed),
        models=[], simulator={}, required_aliases=["Floor"])
    assert not result["ok"]
    assert any(diff["kind"] == "config" for diff in result["differences"])


def test_write_json_atomic_creates_parents(tmp_path):
    target = tmp_path / "nested" / "file.json"
    sa.write_json_atomic(target, {"a": 1})
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}
    assert not target.with_name("file.json.tmp").exists()
