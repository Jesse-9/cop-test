"""migration.py — 从已验收 stage4 L5_UFLOW 工件生成独立 v1 工件.

迁移语义 (方案 §5.3):
  读取 stage4 工件 → 校验来源 manifest/scene 摘要 → 复制场景
  → 生成 v1 manifest/contract → 重载并全量只读校验
  → 原子更新 v1/current.

源工件只读; 任一步失败都不更新 current, 也不修改源工件.
迁移是幂等的: ``current`` 已指向同一来源且摘要一致时直接返回, 不建新版本.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Optional

from .. import spec
from ..simulation import config as cfg
from ..simulation import contract
from ..simulation.layout import layout_from_dict
from ..simulation.scene import adopt_stage4_scene
from ..util import append_jsonl, read_json, sha256_file, utc_now
from . import store as sa


class MigrationError(RuntimeError):
    code = "MIGRATION_ERROR"


def _resolve_source(source: Path | str) -> Path:
    path = Path(source).expanduser().resolve()
    if path.is_file() and path.name in sa.ARTIFACT_FILES:
        path = path.parent
    if not (path / sa.MANIFEST_FILE).exists():
        raise MigrationError(f"来源不是工件目录: {path}")
    if not (path / sa.SCENE_FILE).exists():
        raise MigrationError(f"来源缺少 {sa.SCENE_FILE}: {path}")
    return path


def _source_identity(source: Path) -> dict:
    manifest_path = source / sa.MANIFEST_FILE
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise MigrationError("来源 manifest 顶层必须是对象")
    if manifest.get("source_phase") != "stage4":
        raise MigrationError(
            f"来源必须是 stage4 工件, 当前 {manifest.get('source_phase')!r}")
    layout = manifest.get("layout") or {}
    layout_name = layout.get("name") or layout.get("id") or ""
    if layout_name != spec.LAYOUT_NAME:
        raise MigrationError(
            f"来源布局必须是 {spec.LAYOUT_NAME}, 当前 {layout_name!r}")
    scene_path = source / sa.SCENE_FILE
    actual_sha = sha256_file(scene_path)
    expected_sha = (manifest.get("artifact") or {}).get("sha256")
    if expected_sha and expected_sha != actual_sha:
        raise MigrationError(
            f"来源场景摘要不匹配: expected={expected_sha} actual={actual_sha}")
    parent = manifest.get("parent_artifact") or {}
    return {
        "manifest": manifest,
        "manifest_digest": sha256_file(manifest_path),
        "scene_sha256": actual_sha,
        "scene_size": scene_path.stat().st_size,
        "source_phase": manifest.get("source_phase"),
        "source_parent": parent,
    }


def build_provenance(source: Path) -> dict:
    identity = _source_identity(source)
    parent = identity["source_parent"]
    upstream = {
        "stage3": {
            "artifact_id": parent.get("artifact_id"),
            "manifest_digest": parent.get("manifest_digest"),
            "source_phase": parent.get("source_phase"),
        }
    }
    return {
        "source_phase": "stage4",
        "source_artifact_id": _artifact_id(source),
        "source_manifest_digest": identity["manifest_digest"],
        "source_scene_sha256": identity["scene_sha256"],
        "source_scene_size": identity["scene_size"],
        "migrated_at": utc_now(),
        "upstream": upstream,
        "policy": "behavior_equivalent_move; no new runtime behavior",
    }


def _artifact_id(source: Path) -> str:
    """来源工件 ID: 使用 manifest 的 artifact_id, 缺失时用目录名."""
    manifest = read_json(source / sa.MANIFEST_FILE)
    return str(manifest.get("artifact_id")
               or f"stage4/{source.parent.name}/{source.name}")


def _existing_current(store: sa.ArtifactStore,
                      provenance: dict) -> Optional[sa.Artifact]:
    if not (store.namespace_dir("v1") / sa.CURRENT_FILE).exists():
        return None
    try:
        current = store.read_current("v1")
    except sa.ArtifactError:
        return None
    extra = (current.manifest.get("extra") or {}).get("v1") or {}
    saved = extra.get("provenance") or {}
    same_source = (saved.get("source_artifact_id")
                   == provenance.get("source_artifact_id")
                   and saved.get("source_scene_sha256")
                   == provenance.get("source_scene_sha256"))
    if not same_source:
        return None
    return current


def migrate(source: Path | str, store: sa.ArtifactStore, sim, *,
            actor: str = "cli", note: str = "", force: bool = False,
            layout=None) -> dict:
    """执行 stage4 → v1 工件迁移; 返回摘要与校验结果."""
    source_path = _resolve_source(source)
    provenance = build_provenance(source_path)
    if not force:
        existing = _existing_current(store, provenance)
        if existing is not None:
            return {
                "ok": True,
                "migrated": False,
                "artifact_id": existing.artifact_id,
                "reason": "current 已指向同一来源且摘要一致",
                "provenance": provenance,
            }

    source_manifest = read_json(source_path / sa.MANIFEST_FILE)
    target_layout = layout or layout_from_dict(
        source_manifest.get("layout") or {})
    draft = store.create("v1", target_layout.name)

    partial = draft.path / "scene.partial.ttt"
    try:
        shutil.copyfile(source_path / sa.SCENE_FILE, partial)
        if sha256_file(partial) != provenance["source_scene_sha256"]:
            raise MigrationError("复制后的场景摘要与来源不一致")

        sim.loadScene(str(partial))
        if int(sim.getSimulationState()) != int(sim.simulation_stopped):
            raise MigrationError("迁移加载后仿真不在停止态")
        scene = adopt_stage4_scene(sim, layout=target_layout)
        report = scene.verify()
        if not report.get("ok"):
            failed = [item for item in report.get("checks", [])
                      if not item["ok"]]
            raise MigrationError(f"v1 场景构建契约未通过: {failed}")

        config_summary = contract.config_summary()
        extra = contract.build_contract_extra(
            sim=sim, scene=scene, provenance=provenance, notes=note)
        manifest = sa.build_manifest(
            sim, scene, artifact_id=draft.artifact_id,
            config_summary=config_summary,
            config_digest=contract.config_digest(),
            required_aliases=cfg.REQUIRED_ALIASES,
            protected=True, parent_artifact=provenance,
            actor=actor, note=note,
            manifest_extra={"v1": extra})
        manifest["artifact"]["sha256"] = sha256_file(partial)
        manifest["artifact"]["size"] = partial.stat().st_size
        sa.write_json_atomic(draft.path / sa.MANIFEST_FILE, manifest)

        # 用写入后的 manifest 重新加载并全量校验 (含 v1 契约).
        sim.loadScene(str(partial))
        loaded_scene = adopt_stage4_scene(sim, layout=target_layout)
        validation_manifest = json.loads(json.dumps(manifest))
        validation_manifest["artifact"]["scene_file"] = partial.name
        artifact = sa.Artifact(artifact_id=draft.artifact_id,
                               path=draft.path,
                               manifest=validation_manifest)
        result = sa.validate_artifact(
            sim, artifact, scene=loaded_scene,
            config=config_summary, config_digest=contract.config_digest(),
            required_aliases=cfg.REQUIRED_ALIASES)
        extra_result = contract.validate_contract(
            sim, artifact, scene=loaded_scene)
        result["checks"].extend(extra_result.get("checks", []))
        result["differences"].extend(extra_result.get("differences", []))
        result["contract"] = {
            "ok": bool(extra_result.get("ok")),
            "contract_version": extra_result.get("contract_version")}
        result["ok"] = (bool(result.get("ok"))
                        and bool(extra_result.get("ok")))
        sa.write_json_atomic(draft.path / sa.VALIDATION_FILE, result)
        if not result["ok"]:
            raise MigrationError(
                f"迁移后只读校验失败: {len(result.get('differences', []))} 项差异")
    except Exception as exc:
        # 失败草稿保留在 v1 命名空间下供诊断, 但不更新 current.
        sa.write_json_atomic(draft.path / sa.VALIDATION_FILE, {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "validated_at": sa.utc_now(),
            "source_artifact_id": provenance.get("source_artifact_id"),
        })
        raise

    append_jsonl(draft.path / sa.LIFECYCLE_FILE, {
        "ts": manifest["created_at"], "actor": actor, "event": "migrated",
        "artifact_id": draft.artifact_id, "source_phase": "v1",
        "protected": True, "scene_sha256": manifest["artifact"]["sha256"],
        "source": provenance,
    })
    append_jsonl(draft.path / sa.LIFECYCLE_FILE, {
        "ts": result["validated_at"], "actor": actor, "event": "validated",
        "artifact_id": draft.artifact_id, "ok": True,
        "checks": len(result.get("checks", [])), "differences": 0,
    })

    final_scene = draft.path / sa.SCENE_FILE
    os.replace(partial, final_scene)
    final_artifact = sa.Artifact(artifact_id=draft.artifact_id,
                                 path=draft.path, manifest=manifest)
    store.write_current(final_artifact, phase="v1")
    for name in sa.ARTIFACT_FILES:
        path = draft.path / name
        if path.exists():
            sa.set_readonly(path, True)

    return {
        "ok": True,
        "migrated": True,
        "artifact_id": final_artifact.artifact_id,
        "source_artifact_id": provenance["source_artifact_id"],
        "scene_sha256": manifest["artifact"]["sha256"],
        "manifest_digest": final_artifact.manifest_digest,
        "validation": result,
    }
