"""store.py — v1 场景工件仓库、manifest 与只读校验.

工件布局::

    <root>/v1/L5_UFLOW/v001_<timestamp>/
        scene.ttt
        manifest.json
        validation.json
        lifecycle.jsonl
    <root>/v1/current.json

manifest 结构版本 (``schema_version``) 与本模块绑定; 产品版本、场景契约
版本和算法/策略版本由 :mod:`flexible_factory.spec` 分别维护.

本模块不导入 CoppeliaSim 客户端、场景类或 CLI; 调用方负责提供当前
配置摘要、必需 alias 和已 adopt 的场景对象.
"""

from __future__ import annotations

import dataclasses
import os
import re
import stat
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from .. import spec
from ..util import (append_jsonl, check as _check, checks_ok,
                    diff_config, jsonable as _jsonable, read_json,
                    repo_root, set_readonly, sha256_file, stable_digest,
                    utc_now, write_json_atomic)

SCHEMA_VERSION = spec.SCHEMA_VERSION
SUPPORTED_SCHEMA_VERSIONS = frozenset({SCHEMA_VERSION})
PHASES = ("v1",)
PROTECTED_PHASES = frozenset({"v1"})
ARTIFACT_FILES = ("scene.ttt", "manifest.json", "validation.json",
                  "lifecycle.jsonl")
SCENE_FILE = "scene.ttt"
MANIFEST_FILE = "manifest.json"
VALIDATION_FILE = "validation.json"
LIFECYCLE_FILE = "lifecycle.jsonl"
CURRENT_FILE = "current.json"
CONFIG_EXCLUDE = {"MODEL_BASE", "DEFAULT_LOG_FILE"}
MODEL_BASE = spec.MODEL_BASE
MODEL_PATHS = dict(spec.MODEL_PATHS)

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


class ArtifactError(RuntimeError):
    code = "ARTIFACT_ERROR"


class ArtifactPathError(ArtifactError):
    code = "ARTIFACT_PATH"


class ArtifactNotFoundError(ArtifactError):
    code = "ARTIFACT_NOT_FOUND"


class ArtifactExistsError(ArtifactError):
    code = "ARTIFACT_EXISTS"


class ProtectedArtifactError(ArtifactError):
    code = "PROTECTED_ARTIFACT"


class ManifestError(ArtifactError):
    code = "MANIFEST_ERROR"


def default_artifact_root() -> Path:
    env = os.environ.get("FLEXIBLE_FACTORY_ARTIFACT_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return (repo_root() / spec.ARTIFACT_ROOT).resolve()


# ═══════════════════════════════════════════════════════════════
# 代码 / 模型 / 仿真器信息
# ═══════════════════════════════════════════════════════════════

def collect_code_info(root: Optional[Path] = None) -> dict:
    cwd = str(root or repo_root())

    def _git(*args):
        try:
            result = subprocess.run(["git", *args], cwd=cwd,
                                    capture_output=True, text=True,
                                    timeout=5)
            return result.stdout.strip() if result.returncode == 0 else None
        except Exception:
            return None

    commit = _git("rev-parse", "HEAD")
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    status = _git("status", "--porcelain")
    return {
        "commit": commit,
        "branch": branch,
        "dirty": bool(status),
        "dirty_files": status.splitlines() if status else [],
    }


def collect_model_info() -> list[dict]:
    out = []
    for key, rel in MODEL_PATHS.items():
        path = Path(MODEL_BASE) / rel
        info = {"key": key, "relative_path": rel,
                "path": str(path), "exists": path.exists()}
        if path.exists():
            info["sha256"] = sha256_file(path)
            info["size"] = path.stat().st_size
        out.append(info)
    return out


def object_type_names(sim) -> dict[int, str]:
    names = {}
    for attr in ("object_shape_type", "object_dummy_type", "object_joint_type",
                 "object_proximitysensor_type", "object_visionsensor_type",
                 "object_forcesensor_type", "object_graph_type",
                 "object_light_type", "object_camera_type", "object_mirror_type",
                 "object_octree_type", "object_pointcloud_type",
                 "object_script_type"):
        value = getattr(sim, attr, None)
        if value is None:
            continue
        leaf = attr[len("object_"):-len("_type")]
        names[int(value)] = leaf
    return names


def simulator_info(sim) -> dict:
    version = ""
    try:
        version = sim.getStringProperty(sim.handle_app, "productVersion") or ""
    except Exception:
        try:
            version = sim.getStringParam(
                getattr(sim, "stringparam_productversion", 0)) or ""
        except Exception:
            version = ""
    engine_code = None
    try:
        engine_code = int(sim.getInt32Param(
            getattr(sim, "intparam_dynamic_engine", -1)))
    except Exception:
        pass
    engine_names = {0: "bullet", 1: "ode", 2: "vortex", 3: "newton",
                    4: "mujoco"}
    step = None
    try:
        step = float(sim.getSimulationTimeStep())
    except Exception:
        pass
    return {
        "product_version": str(version),
        "version_major_minor": ".".join(str(version).split(".")[:2]),
        "engine_code": engine_code,
        "engine": engine_names.get(engine_code, "unknown"),
        "simulation_step": step,
    }


# ═══════════════════════════════════════════════════════════════
# manifest 构建
# ═══════════════════════════════════════════════════════════════

def normalize_objects(rows: Iterable[dict], required: Iterable[str],
                      type_names: Optional[dict[int, str]] = None) -> list[dict]:
    """把场景 ``describe()`` 行转成稳定对象身份 (剥离句柄)."""
    type_names = type_names or {}
    required = set(required)
    out: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        alias = str(row.get("alias") or "")
        if not alias:
            raise ManifestError("场景对象缺少 alias")
        if "/" in alias:
            raise ManifestError(f"alias 含 '/' (4.10 会转下划线): {alias!r}")
        if alias in seen:
            raise ManifestError(f"alias 重复, 无法写入 manifest: {alias!r}")
        seen.add(alias)
        raw_type = row.get("type")
        type_name = (type_names.get(int(raw_type), str(raw_type))
                     if isinstance(raw_type, int) else str(raw_type))
        out.append({
            "alias": alias,
            "type": type_name,
            "parent_alias": str(row.get("parent_alias")
                                or row.get("parent") or ""),
            "required": alias in required,
            "world_pos": [round(float(v), 6)
                          for v in (row.get("world_pos") or [])],
        })
    out.sort(key=lambda item: item["alias"])
    return out


_TOP_FIELDS = ("schema_version", "artifact_id", "created_at", "source_phase",
               "protected", "parent_artifact", "code", "config", "simulator",
               "models", "layout", "objects", "geometry", "tool", "physics",
               "scripts", "builder", "contract", "artifact")


def _topology_summary(scene) -> dict:
    conveyor = dict(getattr(scene, "conveyor_meta", {}) or {})
    layout = getattr(scene, "layout", None)
    return {
        "conveyor": _jsonable(conveyor),
        "arm_base": _jsonable(getattr(layout, "arm_base", None)),
        "station_pos": _jsonable(getattr(layout, "station_pos", None)),
        "agv_dock": _jsonable(getattr(layout, "agv_dock", None)),
        "tcp_local_pos": list(_scene_config(scene).TCP_LOCAL_POS),
    }


def _scene_config(scene):
    from ..simulation import config_base
    return config_base


def _physics_summary(sim, scene) -> dict:
    cfg = _scene_config(scene)
    out = {
        "transport_mode": cfg.TRANSPORT_MODE,
        "material_size": list(cfg.MATERIAL_SIZE),
        "material_mass": cfg.MATERIAL_MASS,
        "material_friction": cfg.MATERIAL_FRICTION,
        "material_respondable_mask": cfg.MATERIAL_RESPONDABLE_MASK,
        "agv_wheel_radius": cfg.AGV_WHEEL_RADIUS,
        "agv_wheel_base": cfg.AGV_WHEEL_BASE,
        "agv_tray_friction": cfg.AGV_TRAY_FRICTION,
    }
    agv = getattr(scene, "agv_root", -1)
    if agv is not None and agv >= 0:
        try:
            out["agv_mass"] = float(sim.getShapeMass(agv))
        except Exception:
            try:
                out["agv_mass"] = float(sim.getObjectMass(agv))
            except Exception:
                out["agv_mass"] = None
        try:
            mode = sim.getJointMode(scene.agv_left_wheel)
            out["agv_left_joint_mode"] = int(
                mode[0] if isinstance(mode, (tuple, list)) else mode)
        except Exception:
            out["agv_left_joint_mode"] = None
    if getattr(scene, "robot_joints", None):
        modes = []
        for joint in scene.robot_joints:
            try:
                mode = sim.getJointMode(joint)
                modes.append(int(mode[0] if isinstance(mode, (tuple, list))
                                 else mode))
            except Exception:
                modes.append(None)
        out["ur5_joint_modes"] = modes
    return out


def _script_policy(scene) -> dict:
    conveyor = getattr(scene, "conveyor_meta", {}) or {}
    return {
        "conveyor_script_retained": conveyor.get("script_handle", -1) >= 0,
        "conveyor_drive_mode": conveyor.get("drive_mode"),
        "stripped_models": ["ur5", "suction_pad", "pioneer_p3dx"],
        "suction_model_sensor_renamed": "SuctionPad_01_ModelSensor",
        "suction_builtin_script_enabled": False,
    }


def build_manifest(sim, scene, *, artifact_id: str,
                   config_summary: dict, config_digest: str,
                   required_aliases: Iterable[str],
                   protected: bool = True,
                   parent_artifact: Optional[dict] = None,
                   actor: str = "cli", note: str = "",
                   manifest_extra: Optional[dict] = None,
                   source_phase: str = "v1") -> dict:
    """根据当前已通过构建契约的场景生成 manifest (不含运行时句柄)."""
    if source_phase not in PHASES:
        raise ArtifactPathError(f"未知阶段: {source_phase!r}")
    type_names = object_type_names(sim)
    objects = normalize_objects(scene.describe(), required_aliases,
                                type_names)
    report = dict(getattr(scene, "build_report", {}) or {})
    layout = dataclasses.asdict(scene.layout) if scene.layout else {}
    builder = getattr(scene, "builder_info", None) or {
        "module": "flexible_factory.simulation.scene",
        "version": spec.SCENE_BUILDER_VERSION,
        "clear_scene": True,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "product_version": spec.PRODUCT_VERSION,
        "scene_contract_version": spec.SCENE_CONTRACT_VERSION,
        "artifact_id": artifact_id,
        "created_at": utc_now(),
        "source_phase": source_phase,
        "protected": bool(protected),
        "parent_artifact": parent_artifact,
        "note": note,
        "actor": actor,
        "code": collect_code_info(),
        "config": {"digest": config_digest, "summary": _jsonable(config_summary)},
        "simulator": simulator_info(sim),
        "models": collect_model_info(),
        "layout": _jsonable(layout),
        "objects": objects,
        "geometry": _topology_summary(scene),
        "tool": _jsonable(scene.tool_geometry()),
        "physics": _physics_summary(sim, scene),
        "scripts": _script_policy(scene),
        "builder": builder,
        "contract": {
            "ok": bool(report.get("ok")),
            "checks": _jsonable(report.get("checks", [])),
        },
        "artifact": {
            "scene_file": SCENE_FILE,
            "sha256": None,
            "size": None,
        },
        "extra": _jsonable(manifest_extra or {}),
    }


# ═══════════════════════════════════════════════════════════════
# manifest / 场景只读校验
# ═══════════════════════════════════════════════════════════════

def validate_manifest_schema(manifest: dict,
                             required_aliases: Optional[Iterable[str]] = None
                             ) -> list[dict]:
    checks: list[dict] = []
    _check(checks, "manifest_is_object", isinstance(manifest, dict))
    if not isinstance(manifest, dict):
        return checks

    missing = [name for name in _TOP_FIELDS if name not in manifest]
    _check(checks, "manifest_required_fields", not missing,
           f"missing={missing}")
    _check(checks, "manifest_schema_version",
           manifest.get("schema_version") in SUPPORTED_SCHEMA_VERSIONS,
           f"schema={manifest.get('schema_version')!r} "
           f"supported={sorted(SUPPORTED_SCHEMA_VERSIONS)}")
    extra_v1 = (manifest.get("extra") or {}).get("v1") or {}
    product_version = (manifest.get("product_version")
                       or extra_v1.get("product_version"))
    contract_version = (manifest.get("scene_contract_version")
                        or extra_v1.get("contract_version"))
    _check(checks, "manifest_product_version", bool(product_version),
           f"product_version={product_version!r}")
    _check(checks, "manifest_scene_contract_version", bool(contract_version),
           f"scene_contract_version={contract_version!r}")
    _check(checks, "manifest_artifact_id",
           bool(manifest.get("artifact_id")), str(manifest.get("artifact_id")))
    _check(checks, "manifest_source_phase",
           manifest.get("source_phase") in PHASES,
           str(manifest.get("source_phase")))
    _check(checks, "manifest_protected_bool",
           isinstance(manifest.get("protected"), bool))

    conf = manifest.get("config") or {}
    _check(checks, "manifest_config_digest",
           isinstance(conf, dict) and bool(conf.get("digest")),
           f"digest={conf.get('digest') if isinstance(conf, dict) else None}")

    simulator = manifest.get("simulator") or {}
    _check(checks, "manifest_simulator_version",
           isinstance(simulator, dict) and bool(simulator.get("product_version")),
           f"version={simulator.get('product_version') if isinstance(simulator, dict) else None}")

    layout = manifest.get("layout") or {}
    layout_id = (layout.get("id") or layout.get("name")
                 if isinstance(layout, dict) else None)
    _check(checks, "manifest_layout_id", bool(layout_id),
           f"layout={layout_id}")

    artifact = manifest.get("artifact") or {}
    digest = artifact.get("sha256") if isinstance(artifact, dict) else None
    _check(checks, "manifest_scene_digest",
           isinstance(digest, str) and len(digest) == 64
           and all(c in "0123456789abcdef" for c in digest),
           f"sha256={digest!r}")
    _check(checks, "manifest_scene_file",
           isinstance(artifact, dict) and bool(artifact.get("scene_file")),
           f"scene_file={artifact.get('scene_file') if isinstance(artifact, dict) else None}")

    objects = manifest.get("objects")
    _check(checks, "manifest_objects_list", isinstance(objects, list))
    if isinstance(objects, list):
        aliases = [str(item.get("alias") or "") for item in objects
                   if isinstance(item, dict)]
        bad = [alias for alias in aliases if not alias or "/" in alias]
        _check(checks, "manifest_alias_format", not bad, f"bad={bad}")
        dupes = sorted({alias for alias in aliases if aliases.count(alias) > 1})
        _check(checks, "manifest_duplicate_aliases", not dupes,
               f"duplicates={dupes}")
        present = set(aliases)
        required = set(required_aliases or [])
        absent = sorted(required - present)
        _check(checks, "manifest_required_aliases", not absent,
               f"missing={absent}")
    return checks


def validate_manifest_environment(manifest: dict, *,
                                  config: Optional[dict] = None,
                                  config_digest: Optional[str] = None,
                                  models: Optional[list[dict]] = None,
                                  simulator: Optional[dict] = None,
                                  required_aliases: Optional[Iterable[str]] = None
                                  ) -> dict:
    checks = validate_manifest_schema(manifest, required_aliases)
    differences: list[dict] = []

    saved_summary = (manifest.get("config") or {}).get("summary") or {}
    current_summary = saved_summary if config is None else config
    diffs = diff_config(saved_summary, current_summary)
    saved_digest = (manifest.get("config") or {}).get("digest")
    current_digest = (stable_digest(current_summary) if config_digest is None
                      else config_digest)
    _check(checks, "config_digest_matches", current_digest == saved_digest,
           f"{len(diffs)} 项配置差异")
    differences.extend({"kind": "config", **diff} for diff in diffs)

    saved_models = {model.get("key"): model
                    for model in manifest.get("models") or []}
    current_models = {model.get("key"): model for model in (
        collect_model_info() if models is None else models)}
    for key, saved in sorted(saved_models.items()):
        current = current_models.get(key)
        if current is None:
            _check(checks, f"model_present:{key}", False, "当前配置缺少该模型")
            differences.append({"kind": "model", "key": key,
                                "saved": saved, "current": None})
            continue
        ok = (bool(current.get("exists"))
              and current.get("sha256") == saved.get("sha256"))
        _check(checks, f"model_digest:{key}", ok,
               f"saved={saved.get('sha256')} current={current.get('sha256')}")
        if not ok:
            differences.append({"kind": "model", "key": key,
                                "saved": saved, "current": current})

    saved_sim = manifest.get("simulator") or {}
    current_sim = {} if simulator is None else simulator
    if current_sim:
        version_ok = (saved_sim.get("version_major_minor")
                      == current_sim.get("version_major_minor"))
        _check(checks, "simulator_version_matches", version_ok,
               f"saved={saved_sim.get('version_major_minor')} "
               f"current={current_sim.get('version_major_minor')}")
        if not version_ok:
            differences.append({"kind": "simulator", "key": "version",
                                "saved": saved_sim.get("version_major_minor"),
                                "current": current_sim.get("version_major_minor")})
        saved_engine = saved_sim.get("engine")
        current_engine = current_sim.get("engine")
        if saved_engine and saved_engine != "unknown" and current_engine:
            engine_ok = saved_engine == current_engine
            _check(checks, "simulator_engine_matches", engine_ok,
                   f"saved={saved_engine} current={current_engine}")
            if not engine_ok:
                differences.append({"kind": "simulator", "key": "engine",
                                    "saved": saved_engine,
                                    "current": current_engine})
    return {"checks": checks, "differences": differences,
            "ok": checks_ok(checks) and not differences}


_POS_TOL_STATIC = 0.002
_POS_TOL_DYNAMIC = 0.02


def validate_scene_against_manifest(sim, manifest: dict, scene=None, *,
                                    required_aliases: Optional[Iterable[str]] = None,
                                    context=None
                                    ) -> dict:
    """只读校验当前已加载场景与 manifest 的对象身份/契约.

    ``context`` 由 :func:`build_validation_context` 建立; 未传入时本函数
    自行建立一次, 保持独立调用兼容.
    """
    from ..simulation.scene_base import alias_of

    checks: list[dict] = []
    differences: list[dict] = []

    try:
        state = int(sim.getSimulationState())
        stopped = state == int(sim.simulation_stopped)
    except Exception as exc:
        state, stopped = None, False
        _check(checks, "sim_state_readable", False, str(exc))
    _check(checks, "simulation_stopped", stopped, f"state={state}")

    if context is None:
        from ..simulation.scene_base import build_validation_context
        context = build_validation_context(sim, scene)

    type_names = object_type_names(sim)
    objects = manifest.get("objects") or []
    by_alias: dict[str, list[int]] = context.alias_index

    missing = []
    mismatch = []
    for obj in objects:
        alias = obj.get("alias")
        hits = by_alias.get(alias, [])
        if len(hits) != 1:
            _check(checks, f"alias_unique:{alias}", False,
                   f"{len(hits)} 个匹配")
            if not hits:
                missing.append(alias)
            differences.append({"kind": "alias", "alias": alias,
                                "expected": 1, "actual": len(hits)})
            continue
        _check(checks, f"alias_unique:{alias}", True, "1 个匹配")
        handle = hits[0]
        type_code = int(sim.getObjectType(handle))
        actual_type = type_names.get(type_code, str(type_code))
        expected_type = obj.get("type")
        type_ok = actual_type == expected_type
        _check(checks, f"object_type:{alias}", type_ok,
               f"expected={expected_type} actual={actual_type}")
        if not type_ok:
            mismatch.append(alias)
            differences.append({"kind": "type", "alias": alias,
                                "expected": expected_type,
                                "actual": actual_type})

        expected_parent = obj.get("parent_alias") or ""
        try:
            parent = int(sim.getObjectParent(handle))
            actual_parent = alias_of(sim, parent) if parent >= 0 else ""
        except Exception:
            actual_parent = ""
        parent_ok = actual_parent == expected_parent
        _check(checks, f"object_parent:{alias}", parent_ok,
               f"expected={expected_parent!r} actual={actual_parent!r}")
        if not parent_ok:
            differences.append({"kind": "parent", "alias": alias,
                                "expected": expected_parent,
                                "actual": actual_parent})

        expected_pos = obj.get("world_pos") or []
        if expected_pos:
            try:
                actual_pos = list(sim.getObjectPosition(handle, sim.handle_world))
            except Exception:
                actual_pos = []
            if actual_pos:
                tolerance = _POS_TOL_STATIC
                try:
                    static = int(sim.getObjectInt32Param(
                        handle, sim.shapeintparam_static))
                    if not static:
                        tolerance = _POS_TOL_DYNAMIC
                except Exception:
                    pass
                pos_ok = all(abs(float(a) - float(b)) <= tolerance
                             for a, b in zip(actual_pos, expected_pos))
                _check(checks, f"world_pos:{alias}", pos_ok,
                       f"expected={expected_pos} actual="
                       f"{[round(float(v), 5) for v in actual_pos]} "
                       f"tol={tolerance}")
                if not pos_ok:
                    differences.append({
                        "kind": "world_pos", "alias": alias,
                        "expected": expected_pos,
                        "actual": [round(float(v), 6) for v in actual_pos],
                    })

    legacy_stopper = [handle for handle in context.objects
                      if alias_of(sim, handle) == "Conveyor_In_PickupStopper"]
    _check(checks, "no_legacy_stopper", not legacy_stopper,
           f"found={len(legacy_stopper)}")

    extra = sorted(set(by_alias) - {obj.get("alias") for obj in objects})

    if scene is not None:
        report = context.scene_report
        error = context.scene_error
        if report is None and not error:
            try:
                report = scene.verify()
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                context.scene_error = error
        if error:
            _check(checks, "scene_contract", False, error)
        else:
            report = report or {}
            _check(checks, "scene_contract", bool(report.get("ok")),
                   f"{sum(1 for item in report.get('checks', []) if item['ok'])}"
                   f"/{len(report.get('checks', []))} checks")
            for item in report.get("checks", []):
                if not item.get("ok"):
                    differences.append({"kind": "contract",
                                        "name": item.get("name"),
                                        "detail": item.get("detail")})

    return {
        "ok": checks_ok(checks) and not missing and not mismatch,
        "checks": checks,
        "differences": differences,
        "missing_aliases": missing,
        "type_mismatches": mismatch,
        "extra_aliases": extra,
        "manifest_artifact_id": manifest.get("artifact_id"),
    }


def validate_artifact(sim, artifact: "Artifact", *, scene=None,
                      config: Optional[dict] = None,
                      config_digest: Optional[str] = None,
                      required_aliases: Optional[Iterable[str]] = None,
                      context=None) -> dict:
    """完整只读校验: 文件摘要 + 环境 + 当前场景对象契约.

    ``context`` 可由调用方在同一次启动校验内复用, 避免重复全场景枚举和
    ``scene.verify()``; 未传入时保持独立调用行为.
    """
    checks: list[dict] = []
    differences: list[dict] = []

    scene_path = artifact.scene_path
    if not scene_path.exists():
        _check(checks, "scene_file_exists", False, str(scene_path))
    else:
        digest = sha256_file(scene_path)
        expected = (artifact.manifest.get("artifact") or {}).get("sha256")
        ok = digest == expected
        _check(checks, "scene_file_digest", ok,
               f"expected={expected} actual={digest}")
        if not ok:
            differences.append({"kind": "artifact", "key": "sha256",
                                "expected": expected, "actual": digest})

    env = validate_manifest_environment(
        artifact.manifest, config=config, config_digest=config_digest,
        required_aliases=required_aliases)
    checks.extend(env["checks"])
    differences.extend(env["differences"])

    scene_result = validate_scene_against_manifest(
        sim, artifact.manifest, scene=scene,
        required_aliases=required_aliases, context=context)
    checks.extend(scene_result["checks"])
    differences.extend(scene_result["differences"])

    return {
        "ok": checks_ok(checks) and not differences,
        "artifact_id": artifact.artifact_id,
        "manifest_digest": artifact.manifest_digest,
        "validated_at": utc_now(),
        "checks": checks,
        "differences": differences,
        "missing_aliases": scene_result["missing_aliases"],
        "type_mismatches": scene_result["type_mismatches"],
        "extra_aliases": scene_result["extra_aliases"],
    }


# ═══════════════════════════════════════════════════════════════
# 工件对象与仓库
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ArtifactDraft:
    artifact_id: str
    path: Path


@dataclass(frozen=True)
class Artifact:
    artifact_id: str
    path: Path
    manifest: dict = field(repr=False)

    @property
    def scene_path(self) -> Path:
        return self.path / (self.manifest.get("artifact", {})
                            .get("scene_file") or SCENE_FILE)

    @property
    def manifest_path(self) -> Path:
        return self.path / MANIFEST_FILE

    @property
    def validation_path(self) -> Path:
        return self.path / VALIDATION_FILE

    @property
    def lifecycle_path(self) -> Path:
        return self.path / LIFECYCLE_FILE

    @property
    def protected(self) -> bool:
        return bool(self.manifest.get("protected"))

    @property
    def manifest_digest(self) -> str:
        return sha256_file(self.manifest_path)

    def as_dict(self) -> dict:
        return {"artifact_id": self.artifact_id, "path": str(self.path),
                "protected": self.protected,
                "manifest_digest": self.manifest_digest}


class ArtifactStore:
    """版本化 v1 工件仓库; 所有路径必须位于 root 之内."""

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root or default_artifact_root()).expanduser().resolve()

    def namespace_dir(self, phase: str = "v1") -> Path:
        if phase not in PHASES:
            raise ArtifactPathError(
                f"未知阶段 {phase!r}; 可选 {list(PHASES)}")
        return self.root / phase

    def _validate_layout_name(self, layout: str) -> str:
        if not layout or not _SAFE_NAME.match(layout):
            raise ArtifactPathError(f"布局名不合法: {layout!r}")
        return layout

    def _within_root(self, path: Path) -> Path:
        resolved = Path(path).expanduser().resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise ArtifactPathError(
                f"路径越界: {resolved} 不在工件根目录 {self.root} 内") from exc
        return resolved

    def create(self, phase: str = "v1", layout: str = spec.LAYOUT_NAME,
               stamp: Optional[str] = None) -> ArtifactDraft:
        layout = self._validate_layout_name(layout)
        parent = self.namespace_dir(phase) / layout
        parent.mkdir(parents=True, exist_ok=True)
        version = 1
        for child in parent.iterdir():
            if child.is_dir():
                match = re.match(r"^v(\d{3})_", child.name)
                if match:
                    version = max(version, int(match.group(1)) + 1)
        stamp = stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"v{version:03d}_{stamp}"
        path = parent / name
        path.mkdir(parents=False, exist_ok=False)
        return ArtifactDraft(f"{phase}/{layout}/{name}", path)

    def _load(self, path: Path) -> Artifact:
        path = self._within_root(path)
        manifest_path = path / MANIFEST_FILE
        if not manifest_path.exists():
            raise ArtifactNotFoundError(f"缺少 {manifest_path}")
        try:
            manifest = read_json(manifest_path)
        except Exception as exc:
            raise ManifestError(f"manifest 无法解析: {exc}") from exc
        if not isinstance(manifest, dict):
            raise ManifestError("manifest 顶层必须是对象")
        artifact_id = str(path.relative_to(self.root)).replace(os.sep, "/")
        return Artifact(artifact_id=artifact_id, path=path, manifest=manifest)

    def resolve(self, ref: str, phase: Optional[str] = None) -> Artifact:
        ref = str(ref or "").strip()
        if not ref:
            raise ArtifactNotFoundError("工件引用为空")
        first_segment = re.split(r"[\\/]", ref, maxsplit=1)[0]
        is_artifact_id = first_segment in PHASES
        looks_like_path = (not is_artifact_id
                           and ("/" in ref or "\\" in ref
                                or ref.startswith(".")
                                or Path(ref).is_absolute()))
        if looks_like_path:
            raw = Path(ref).expanduser()
            path = raw if raw.is_absolute() else (Path.cwd() / raw)
            path = self._within_root(path)
            if path.is_file() and path.name in ARTIFACT_FILES:
                path = path.parent
            return self._load(path)

        phases = [phase] if phase else list(PHASES)
        hits: list[Path] = []
        for current_phase in phases:
            namespace = self.namespace_dir(current_phase)
            if not namespace.exists():
                continue
            for manifest_path in namespace.rglob(MANIFEST_FILE):
                candidate = manifest_path.parent
                relative_id = str(candidate.relative_to(self.root)).replace(
                    os.sep, "/")
                if ref in (candidate.name, relative_id,
                           relative_id.split("/", 1)[-1]):
                    hits.append(candidate)
        if not hits:
            raise ArtifactNotFoundError(f"找不到工件 {ref!r} (phase={phase})")
        if len(hits) > 1:
            raise ArtifactPathError(
                f"工件引用 {ref!r} 不唯一: "
                f"{[str(hit.relative_to(self.root)) for hit in hits]}")
        return self._load(hits[0])

    def find(self, ref: str, phase: Optional[str] = None) -> Artifact:
        if ref == "current":
            return self.read_current(phase or "v1")
        return self.resolve(ref, phase)

    def list(self, phase: Optional[str] = None) -> list[dict]:
        phases = [phase] if phase else list(PHASES)
        out = []
        for current_phase in phases:
            namespace = self.namespace_dir(current_phase)
            if not namespace.exists():
                continue
            for manifest_path in sorted(namespace.rglob(MANIFEST_FILE)):
                try:
                    manifest = read_json(manifest_path)
                except Exception:
                    continue
                out.append({
                    "artifact_id": str(manifest_path.parent.relative_to(
                        self.root)).replace(os.sep, "/"),
                    "path": str(manifest_path.parent),
                    "created_at": manifest.get("created_at"),
                    "protected": bool(manifest.get("protected")),
                    "source_phase": manifest.get("source_phase"),
                    "layout": ((manifest.get("layout") or {}).get("id")
                               or (manifest.get("layout") or {}).get("name")),
                })
        out.sort(key=lambda item: (item["source_phase"] or "",
                                   item["artifact_id"]))
        return out

    def read_current(self, phase: str = "v1") -> Artifact:
        pointer = self.namespace_dir(phase) / CURRENT_FILE
        if not pointer.exists():
            raise ArtifactNotFoundError(f"没有当前工件指针: {pointer}")
        data = read_json(pointer)
        artifact_id = data.get("artifact_id")
        if not artifact_id:
            raise ManifestError(f"current 指针缺少 artifact_id: {pointer}")
        return self.resolve(artifact_id)

    def write_current(self, artifact: Artifact,
                      phase: Optional[str] = None) -> Path:
        phase = phase or "v1"
        pointer = self.namespace_dir(phase) / CURRENT_FILE
        return write_json_atomic(pointer, {
            "artifact_id": artifact.artifact_id,
            "path": str(artifact.path.relative_to(
                self.namespace_dir(phase))).replace(os.sep, "/"),
            "manifest_digest": artifact.manifest_digest,
            "scene_sha256": (artifact.manifest.get("artifact") or {}).get(
                "sha256"),
            "updated_at": utc_now(),
        })

    def check_writable(self, artifact: Artifact, force: bool = False) -> None:
        if artifact.protected:
            raise ProtectedArtifactError(
                f"工件 {artifact.artifact_id} 是只读/已验收基线, 拒绝覆盖; "
                "请创建新版本或派生副本")
        if artifact.path.exists() and not force:
            raise ArtifactExistsError(
                f"工件目录已存在: {artifact.path}; 需要 --force-overwrite")

    def remove(self, artifact: Artifact, force: bool = False) -> None:
        self.check_writable(artifact, force=force)

        def _onerror(func, path, _exc):
            try:
                os.chmod(path, stat.S_IWRITE)
                func(path)
            except OSError:
                pass

        import shutil
        shutil.rmtree(artifact.path, onerror=_onerror)


__all__ = [
    "Artifact", "ArtifactDraft", "ArtifactStore", "ArtifactError",
    "ArtifactExistsError", "ArtifactNotFoundError", "ArtifactPathError",
    "ManifestError", "ProtectedArtifactError", "append_jsonl",
    "build_manifest", "checks_ok", "collect_code_info", "collect_model_info",
    "default_artifact_root", "normalize_objects", "object_type_names",
    "sha256_file", "simulator_info", "stable_digest", "utc_now",
    "validate_artifact", "validate_manifest_environment",
    "validate_manifest_schema", "validate_scene_against_manifest",
    "write_json_atomic",
]
