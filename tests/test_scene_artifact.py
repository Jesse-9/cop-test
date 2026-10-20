"""步骤 1.2 场景工件层单测 (不需要 CoppeliaSim)。

覆盖: 工件版本/路径保护/manifest schema/配置摘要/原子写入。
"""

import json
import os
import stat
from pathlib import Path

import pytest

import stage1.config as cfg
from stage1 import scene_artifact as sa


def _manifest(**overrides):
    m = {
        "schema_version": sa.SCHEMA_VERSION,
        "artifact_id": "stage1/L1_S1/v001_test",
        "created_at": "2026-10-20T12:00:00",
        "source_phase": "stage1",
        "protected": True,
        "parent_artifact": None,
        "code": {"commit": "abc", "dirty": False},
        "config": {
            "digest": sa.config_digest(),
            "summary": sa.config_summary(),
        },
        "simulator": {
            "product_version": "4.10.0.0",
            "engine": "bullet",
            "simulation_step": 0.05,
        },
        "models": [],
        "layout": {"id": "L1_S1"},
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
    }
    m.update(overrides)
    return m


def _publish(store, phase="stage1", layout="L1_S1", stamp="20261020_120000",
             manifest=None, scene_bytes=b"fake-ttt"):
    draft = store.create(phase=phase, layout=layout, stamp=stamp)
    scene = draft.path / "scene.ttt"
    scene.write_bytes(scene_bytes)
    m = manifest or _manifest(
        artifact_id=draft.artifact_id,
        artifact={"scene_file": "scene.ttt",
                  "sha256": sa.sha256_file(scene),
                  "size": scene.stat().st_size},
    )
    sa.write_json_atomic(draft.path / "manifest.json", m)
    return store.find(draft.artifact_id)


# ── 路径与版本 ────────────────────────────────────────────────

def test_create_versions_increment(tmp_path):
    store = sa.ArtifactStore(tmp_path)
    a = store.create(phase="stage1", layout="L1_S1", stamp="20261020_120000")
    b = store.create(phase="stage1", layout="L1_S1", stamp="20261020_120000")
    assert a.artifact_id.endswith("v001_20261020_120000")
    assert b.artifact_id.endswith("v002_20261020_120000")
    assert a.path != b.path
    assert a.path.is_dir() and b.path.is_dir()


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
    art = _publish(store)
    assert store.find(art.artifact_id).artifact_id == art.artifact_id
    assert store.find(art.path.name).artifact_id == art.artifact_id
    store.write_current(art)
    assert store.read_current("stage1").artifact_id == art.artifact_id


def test_unknown_artifact_raises(tmp_path):
    store = sa.ArtifactStore(tmp_path)
    with pytest.raises(sa.ArtifactNotFoundError):
        store.find("stage1/L1_S1/v999_missing")
    with pytest.raises(sa.ArtifactNotFoundError):
        store.read_current("stage1")


def test_protected_artifact_cannot_be_overwritten(tmp_path):
    store = sa.ArtifactStore(tmp_path)
    art = _publish(store)          # protected=True
    with pytest.raises(sa.ProtectedArtifactError):
        store.check_writable(art, force=True)


def test_unprotected_requires_force(tmp_path):
    store = sa.ArtifactStore(tmp_path)
    art = _publish(store, manifest=_manifest(
        protected=False,
        artifact={"scene_file": "scene.ttt", "sha256": "0" * 64, "size": 0},
    ))
    with pytest.raises(sa.ArtifactExistsError):
        store.check_writable(art)
    store.check_writable(art, force=True)       # 不抛即通过


def test_current_pointer_is_atomic_and_has_no_temp(tmp_path):
    store = sa.ArtifactStore(tmp_path)
    art = _publish(store)
    store.write_current(art)
    current = store.namespace_dir("stage1") / "current.json"
    data = json.loads(current.read_text(encoding="utf-8"))
    assert data["artifact_id"] == art.artifact_id
    assert not list(current.parent.glob("*.tmp*"))


# ── manifest schema ──────────────────────────────────────────

def test_manifest_schema_ok():
    checks = sa.validate_manifest_schema(_manifest(), required_aliases=["Floor"])
    assert sa.checks_ok(checks), checks


def test_manifest_rejects_unsupported_schema():
    checks = sa.validate_manifest_schema(
        _manifest(schema_version="99.0"), required_aliases=["Floor"])
    assert not sa.checks_ok(checks)
    assert any(c["name"] == "manifest_schema_version" and not c["ok"]
               for c in checks)


def test_manifest_rejects_missing_and_duplicate_aliases():
    missing = sa.validate_manifest_schema(
        _manifest(objects=[]), required_aliases=["Floor"])
    assert not sa.checks_ok(missing)

    dup = _manifest(objects=[
        {"alias": "Floor", "type": "shape", "parent_alias": "",
         "required": True, "world_pos": [0, 0, 0]},
        {"alias": "Floor", "type": "shape", "parent_alias": "",
         "required": True, "world_pos": [0, 0, 0]},
    ])
    checks = sa.validate_manifest_schema(dup, required_aliases=["Floor"])
    assert not sa.checks_ok(checks)
    assert any("duplicate" in c["name"] for c in checks if not c["ok"])


def test_manifest_requires_scene_digest():
    m = _manifest()
    m["artifact"].pop("sha256")
    checks = sa.validate_manifest_schema(m, required_aliases=["Floor"])
    assert not sa.checks_ok(checks)


def test_manifest_rejects_slash_alias():
    m = _manifest(objects=[
        {"alias": "Island/A", "type": "shape", "parent_alias": "",
         "required": True, "world_pos": [0, 0, 0]},
    ])
    checks = sa.validate_manifest_schema(m, required_aliases=["Island/A"])
    assert not sa.checks_ok(checks)
    assert any("alias" in c["name"] and not c["ok"] for c in checks)


def test_normalize_objects_strips_handles_and_sorts():
    rows = [
        {"alias": "B", "handle": 2, "type": 1, "parent": "R",
         "world_pos": [1, 2, 3]},
        {"alias": "A", "handle": 1, "type": 1, "parent": "",
         "world_pos": [0, 0, 0]},
    ]
    out = sa.normalize_objects(rows, required={"A"})
    assert [o["alias"] for o in out] == ["A", "B"]
    assert all("handle" not in o for o in out)
    assert out[0]["required"] is True and out[1]["required"] is False


def test_normalize_objects_rejects_duplicates():
    rows = [
        {"alias": "A", "handle": 1, "type": 1, "parent": "",
         "world_pos": [0, 0, 0]},
        {"alias": "A", "handle": 2, "type": 1, "parent": "",
         "world_pos": [0, 0, 0]},
    ]
    with pytest.raises(sa.ManifestError):
        sa.normalize_objects(rows, required={"A"})


# ── 配置/模型摘要 ────────────────────────────────────────────

def test_config_digest_is_stable_and_detects_change(monkeypatch):
    d1 = sa.config_digest()
    d2 = sa.config_digest()
    assert d1 == d2
    saved = sa.config_summary()
    monkeypatch.setattr(cfg, "MATERIAL_MASS", cfg.MATERIAL_MASS + 1.0)
    assert sa.config_digest() != d1
    diff = sa.diff_config(saved, sa.config_summary())
    assert any(d["key"] == "MATERIAL_MASS" for d in diff)


def test_config_summary_excludes_machine_path():
    summary = sa.config_summary()
    assert "MODEL_BASE" not in summary
    assert "DEFAULT_LOG_FILE" not in summary
    assert "SIM_STEP" in summary


def test_sha256_file(tmp_path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"abc")
    assert sa.sha256_file(p) == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")


def test_write_json_atomic_roundtrip(tmp_path):
    p = tmp_path / "sub" / "x.json"
    sa.write_json_atomic(p, {"b": 1, "a": 2})
    assert json.loads(p.read_text(encoding="utf-8")) == {"a": 2, "b": 1}
    assert not list(p.parent.glob("*.tmp*"))


def test_protect_files_sets_readonly_and_restore(tmp_path):
    p = tmp_path / "scene.ttt"
    p.write_bytes(b"x")
    sa.set_readonly(p, True)
    assert not (p.stat().st_mode & stat.S_IWRITE)
    sa.set_readonly(p, False)
    assert p.stat().st_mode & stat.S_IWRITE


def test_diff_manifest_reports_sections_and_aliases():
    parent = {"objects": [{"alias": "A"}, {"alias": "B"}],
              "config": {"x": 1}, "layout": {"name": "L1_S1"}}
    current = {"objects": [{"alias": "B"}, {"alias": "C"}],
               "config": {"x": 2}, "layout": {"name": "L1_S1"}}
    diffs = sa.diff_manifest(parent, current)
    by_section = {d["section"]: d for d in diffs}
    assert set(by_section) == {"objects", "config"}
    assert by_section["objects"]["added_aliases"] == ["C"]
    assert by_section["objects"]["removed_aliases"] == ["A"]
