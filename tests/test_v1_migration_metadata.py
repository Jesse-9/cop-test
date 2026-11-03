"""v1 迁移 provenance/幂等判断离线测试 (不需要 CoppeliaSim)."""

from __future__ import annotations

import json
from pathlib import Path

from flexible_factory.artifacts import migration
from flexible_factory.artifacts import store as sa
from flexible_factory.util import sha256_file


def _stage4_source(root: Path) -> Path:
    source = root / "stage4" / "L5_UFLOW" / "v004_test"
    source.mkdir(parents=True)
    scene = source / "scene.ttt"
    scene.write_bytes(b"stage4-scene")
    manifest = {
        "artifact_id": "stage4/L5_UFLOW/v004_test",
        "source_phase": "stage4",
        "layout": {"name": "L5_UFLOW"},
        "artifact": {"scene_file": "scene.ttt",
                     "sha256": sha256_file(scene), "size": scene.stat().st_size},
        "parent_artifact": {
            "artifact_id": "stage3/L2_MIRROR/v008_test",
            "manifest_digest": "a" * 64,
            "source_phase": "stage3",
        },
    }
    sa.write_json_atomic(source / "manifest.json", manifest)
    return source


def test_provenance_records_source_and_upstream(tmp_path):
    source = _stage4_source(tmp_path)
    provenance = migration.build_provenance(source)
    assert provenance["source_artifact_id"] == "stage4/L5_UFLOW/v004_test"
    assert provenance["source_scene_sha256"] == sha256_file(source / "scene.ttt")
    assert provenance["upstream"]["stage3"]["artifact_id"] == \
        "stage3/L2_MIRROR/v008_test"
    assert provenance["source_manifest_digest"]


def test_source_identity_rejects_wrong_layout(tmp_path):
    source = _stage4_source(tmp_path)
    manifest_path = source / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["layout"] = {"name": "L4_COLUMN"}
    sa.write_json_atomic(manifest_path, manifest)
    try:
        migration.build_provenance(source)
    except migration.MigrationError as exc:
        assert "L5_UFLOW" in str(exc)
    else:                                     # pragma: no cover
        raise AssertionError("expected MigrationError")


def test_existing_current_matches_same_source(tmp_path):
    source = _stage4_source(tmp_path)
    provenance = migration.build_provenance(source)
    store = sa.ArtifactStore(tmp_path / "v1root")
    draft = store.create("v1", "L5_UFLOW", stamp="20261103_120000")
    scene = draft.path / "scene.ttt"
    scene.write_bytes(b"v1-scene")
    manifest = {
        "schema_version": sa.SCHEMA_VERSION,
        "artifact_id": draft.artifact_id,
        "source_phase": "v1",
        "config": {"digest": "d", "summary": {}},
        "simulator": {"product_version": "4.10.0.0"},
        "layout": {"name": "L5_UFLOW"},
        "objects": [],
        "artifact": {"scene_file": "scene.ttt",
                     "sha256": sha256_file(scene)},
        "extra": {"v1": {"provenance": provenance}},
    }
    sa.write_json_atomic(draft.path / "manifest.json", manifest)
    artifact = store.find(draft.artifact_id)
    store.write_current(artifact)
    existing = migration._existing_current(store, provenance)
    assert existing is not None
    assert existing.artifact_id == artifact.artifact_id
