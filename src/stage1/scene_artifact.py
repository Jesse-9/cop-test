"""scene_artifact.py — 场景工件、manifest 与只读校验 (步骤 1.2 §4).

本模块只处理**文件与稳定身份信息**, 不执行构建/加载/启动:

    <scene-artifact>/
      scene.ttt
      manifest.json
      validation.json
      lifecycle.jsonl

对象句柄是当前仿真会话的运行时值, 不进入 manifest; manifest 只保存
alias/类型/父 alias/期望世界坐标, 加载后由调用方重新解析句柄。

对应方案: docs/wiki/06-工业案例/06-08-...步骤1.2...
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import stat
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from . import config as cfg

SCHEMA_VERSION = "1.0"
SUPPORTED_SCHEMA_VERSIONS = frozenset({SCHEMA_VERSION})
PHASES = ("stage1", "stage2", "diagnostic")
PROTECTED_PHASES = frozenset({"stage1"})
ARTIFACT_FILES = ("scene.ttt", "manifest.json", "validation.json",
                  "lifecycle.jsonl")
SCENE_FILE = "scene.ttt"
MANIFEST_FILE = "manifest.json"
VALIDATION_FILE = "validation.json"
LIFECYCLE_FILE = "lifecycle.jsonl"
CURRENT_FILE = "current.json"

# 不影响场景/控制行为或依赖本机安装路径, 不进入配置摘要
CONFIG_EXCLUDE = {"MODEL_BASE", "DEFAULT_LOG_FILE"}
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


# ═══════════════════════════════════════════════════════════════
# 异常
# ═══════════════════════════════════════════════════════════════

class ArtifactError(RuntimeError):
    """工件层异常基类"""
    code = "ARTIFACT_ERROR"


class ArtifactPathError(ArtifactError):
    """路径越界或命名不合法"""
    code = "ARTIFACT_PATH"


class ArtifactNotFoundError(ArtifactError):
    """找不到指定工件"""
    code = "ARTIFACT_NOT_FOUND"


class ArtifactExistsError(ArtifactError):
    """目标工件已存在且未显式授权覆盖"""
    code = "ARTIFACT_EXISTS"


class ProtectedArtifactError(ArtifactError):
    """受保护/已验收工件不允许覆盖"""
    code = "ARTIFACT_PROTECTED"


class ManifestError(ArtifactError):
    """manifest 缺失、损坏或不满足 schema"""
    code = "MANIFEST_INVALID"


# ═══════════════════════════════════════════════════════════════
# 基础工具
# ═══════════════════════════════════════════════════════════════

def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_artifact_root() -> Path:
    """场景工件根目录, 可由 ``SCENE_ARTIFACT_ROOT`` 覆盖。"""
    env = os.environ.get("SCENE_ARTIFACT_ROOT")
    return Path(env).expanduser() if env else repo_root() / "docs" / "scene_artifacts"


def utc_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _jsonable(dataclasses.asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "name") and hasattr(value, "value"):       # Enum
        return _jsonable(value.value)
    return repr(value)


def stable_digest(value: Any) -> str:
    payload = json.dumps(_jsonable(value), sort_keys=True,
                         ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def read_json(path: Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json_atomic(path: Path, data: Any) -> Path:
    """同目录临时文件 + os.replace, 避免中断留下半成品。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(_jsonable(data), f, ensure_ascii=False, indent=2,
                      sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return path


def append_jsonl(path: Path, event: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(_jsonable(event), ensure_ascii=False,
                           sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def set_readonly(path: Path, readonly: bool = True) -> None:
    """尽力设置/清除文件只读位 (Windows 与 POSIX)。"""
    path = Path(path)
    if not path.exists():
        return
    try:
        mode = path.stat().st_mode
        if readonly:
            path.chmod(mode & ~stat.S_IWRITE)
        else:
            path.chmod(mode | stat.S_IWRITE)
    except OSError:
        pass


# ═══════════════════════════════════════════════════════════════
# 配置 / 代码 / 模型摘要
# ═══════════════════════════════════════════════════════════════

def config_summary() -> dict:
    """场景/控制相关配置的稳定快照 (排除本机模型路径与日志文件名)。"""
    out: dict[str, Any] = {}
    for name, value in vars(cfg).items():
        if name.startswith("_") or name in CONFIG_EXCLUDE:
            continue
        if not name.isupper():
            continue
        if callable(value):
            continue
        out[name] = _jsonable(value)
    try:
        from .layout import default_layout
        out["__layout__"] = _jsonable(default_layout())
    except Exception as exc:                       # pragma: no cover
        out["__layout_error__"] = f"{type(exc).__name__}: {exc}"
    return out


def config_digest(summary: Optional[dict] = None) -> str:
    return stable_digest(config_summary() if summary is None else summary)


def diff_config(saved: dict, current: dict) -> list[dict]:
    diffs = []
    for key in sorted(set(saved) | set(current)):
        if saved.get(key) != current.get(key):
            diffs.append({"key": key, "saved": saved.get(key),
                          "current": current.get(key)})
    return diffs


def collect_code_info(root: Optional[Path] = None) -> dict:
    cwd = str(root or repo_root())

    def _git(*args):
        try:
            r = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                               text=True, timeout=5)
            return r.stdout.strip() if r.returncode == 0 else None
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
    for key, rel in cfg.MODEL_PATHS.items():
        path = Path(cfg.MODEL_BASE) / rel
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
# manifest
# ═══════════════════════════════════════════════════════════════

def normalize_objects(rows: Iterable[dict], required: Iterable[str],
                      type_names: Optional[dict[int, str]] = None) -> list[dict]:
    """把 ``Stage1Scene.describe()`` 行转成稳定对象身份 (剥离句柄)。"""
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
    out.sort(key=lambda o: o["alias"])
    return out


_TOP_FIELDS = ("schema_version", "artifact_id", "created_at", "source_phase",
               "protected", "parent_artifact", "code", "config", "simulator",
               "models", "layout", "objects", "geometry", "tool", "physics",
               "scripts", "builder", "contract", "artifact")


def diff_manifest(parent: dict, current: dict) -> list[dict]:
    """父工件/当前工件的章节级差异 (阶段二副本必须记录)。

    只比较 manifest 的稳定章节摘要; 字段级物理差异由阶段二在 `extra`
    中补充。objects 额外给出新增/移除的 alias。
    """
    out: list[dict] = []
    for section in ("config", "layout", "models", "objects", "geometry",
                    "tool", "physics", "scripts"):
        parent_digest = stable_digest(parent.get(section))
        current_digest = stable_digest(current.get(section))
        if parent_digest == current_digest:
            continue
        entry = {"section": section, "parent_digest": parent_digest,
                 "current_digest": current_digest}
        if section == "objects":
            parent_aliases = {o.get("alias") for o in
                              (parent.get("objects") or [])}
            current_aliases = {o.get("alias") for o in
                               (current.get("objects") or [])}
            entry["added_aliases"] = sorted(current_aliases - parent_aliases)
            entry["removed_aliases"] = sorted(parent_aliases - current_aliases)
        out.append(entry)
    return out


def _check(checks: list[dict], name: str, ok: bool, detail: str = "") -> None:
    checks.append({"name": name, "ok": bool(ok), "detail": detail})


def checks_ok(checks: Iterable[dict]) -> bool:
    return all(c.get("ok") for c in checks)


def validate_manifest_schema(manifest: dict,
                             required_aliases: Optional[Iterable[str]] = None
                             ) -> list[dict]:
    """只校验 manifest 自身结构, 不访问仿真器。"""
    checks: list[dict] = []
    _check(checks, "manifest_is_object", isinstance(manifest, dict))
    if not isinstance(manifest, dict):
        return checks

    missing = [f for f in _TOP_FIELDS if f not in manifest]
    _check(checks, "manifest_required_fields", not missing,
           f"missing={missing}")
    _check(checks, "manifest_schema_version",
           manifest.get("schema_version") in SUPPORTED_SCHEMA_VERSIONS,
           f"schema={manifest.get('schema_version')!r} "
           f"supported={sorted(SUPPORTED_SCHEMA_VERSIONS)}")
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

    sim = manifest.get("simulator") or {}
    _check(checks, "manifest_simulator_version",
           isinstance(sim, dict) and bool(sim.get("product_version")),
           f"version={sim.get('product_version') if isinstance(sim, dict) else None}")

    layout = manifest.get("layout") or {}
    layout_id = (layout.get("id") or layout.get("name")
                 if isinstance(layout, dict) else None)
    _check(checks, "manifest_layout_id", bool(layout_id),
           f"layout={layout_id}")

    art = manifest.get("artifact") or {}
    digest = art.get("sha256") if isinstance(art, dict) else None
    _check(checks, "manifest_scene_digest",
           isinstance(digest, str) and len(digest) == 64
           and all(c in "0123456789abcdef" for c in digest),
           f"sha256={digest!r}")
    _check(checks, "manifest_scene_file",
           isinstance(art, dict) and bool(art.get("scene_file")),
           f"scene_file={art.get('scene_file') if isinstance(art, dict) else None}")

    objects = manifest.get("objects")
    _check(checks, "manifest_objects_list", isinstance(objects, list))
    if isinstance(objects, list):
        aliases = [str(o.get("alias") or "") for o in objects
                   if isinstance(o, dict)]
        bad = [a for a in aliases if not a or "/" in a]
        _check(checks, "manifest_alias_format", not bad, f"bad={bad}")
        dupes = sorted({a for a in aliases if aliases.count(a) > 1})
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
                                  models: Optional[list[dict]] = None,
                                  simulator: Optional[dict] = None,
                                  required_aliases: Optional[Iterable[str]] = None
                                  ) -> dict:
    """只读比较 manifest 与当前环境; 返回 checks + differences。"""
    checks = validate_manifest_schema(manifest, required_aliases)
    differences: list[dict] = []

    saved_summary = (manifest.get("config") or {}).get("summary") or {}
    current_summary = config_summary() if config is None else config
    diffs = diff_config(saved_summary, current_summary)
    _check(checks, "config_digest_matches",
           config_digest(current_summary) == (manifest.get("config") or {}).get("digest"),
           f"{len(diffs)} 项配置差异")
    differences.extend({"kind": "config", **d} for d in diffs)

    saved_models = {m.get("key"): m for m in manifest.get("models") or []}
    current_models = {m.get("key"): m for m in (
        collect_model_info() if models is None else models)}
    for key, saved in sorted(saved_models.items()):
        cur = current_models.get(key)
        if cur is None:
            _check(checks, f"model_present:{key}", False, "当前配置缺少该模型")
            differences.append({"kind": "model", "key": key,
                                "saved": saved, "current": None})
            continue
        ok = (bool(cur.get("exists"))
              and cur.get("sha256") == saved.get("sha256"))
        _check(checks, f"model_digest:{key}", ok,
               f"saved={saved.get('sha256')} current={cur.get('sha256')}")
        if not ok:
            differences.append({"kind": "model", "key": key,
                                "saved": saved, "current": cur})

    saved_sim = manifest.get("simulator") or {}
    cur_sim = simulator_info_placeholder() if simulator is None else simulator
    if cur_sim:
        version_ok = (saved_sim.get("version_major_minor")
                      == cur_sim.get("version_major_minor"))
        _check(checks, "simulator_version_matches", version_ok,
               f"saved={saved_sim.get('version_major_minor')} "
               f"current={cur_sim.get('version_major_minor')}")
        if not version_ok:
            differences.append({"kind": "simulator", "key": "version",
                                "saved": saved_sim.get("version_major_minor"),
                                "current": cur_sim.get("version_major_minor")})
        saved_engine = saved_sim.get("engine")
        cur_engine = cur_sim.get("engine")
        if saved_engine and saved_engine != "unknown" and cur_engine:
            engine_ok = saved_engine == cur_engine
            _check(checks, "simulator_engine_matches", engine_ok,
                   f"saved={saved_engine} current={cur_engine}")
            if not engine_ok:
                differences.append({"kind": "simulator", "key": "engine",
                                    "saved": saved_engine,
                                    "current": cur_engine})
    return {"checks": checks, "differences": differences,
            "ok": checks_ok(checks) and not differences}


def simulator_info_placeholder() -> dict:
    """无 sim 时用于纯离线比较; 跳过版本/引擎检查。"""
    return {}


# ═══════════════════════════════════════════════════════════════
# manifest 构建与场景只读校验
# ═══════════════════════════════════════════════════════════════

def _geometry_summary(scene) -> dict:
    conv = dict(getattr(scene, "conveyor_meta", {}) or {})
    return {
        "conveyor": _jsonable(conv),
        "arm_base": _jsonable(getattr(scene.layout, "arm_base", None)),
        "station_pos": _jsonable(getattr(scene.layout, "station_pos", None)),
        "agv_dock": _jsonable(getattr(scene.layout, "agv_dock", None)),
        "tcp_local_pos": list(cfg.TCP_LOCAL_POS),
        "tcp_local_quaternion": list(cfg.TCP_LOCAL_QUATERNION),
        "suction_mount_z": cfg.SUCTION_MOUNT_Z,
        "suction_mount_quaternion": list(cfg.SUCTION_MOUNT_QUATERNION),
    }


def _physics_summary(sim, scene) -> dict:
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
    conv = getattr(scene, "conveyor_meta", {}) or {}
    return {
        "conveyor_script_retained": conv.get("script_handle", -1) >= 0,
        "conveyor_drive_mode": conv.get("drive_mode"),
        "stripped_models": ["ur5", "suction_pad", "pioneer_p3dx"],
        "suction_model_sensor_renamed": "SuctionPad_01_ModelSensor",
        "suction_builtin_script_enabled": False,
    }


def build_manifest(sim, scene, *, source_phase: str, artifact_id: str,
                   protected: bool, parent_artifact: Optional[dict] = None,
                   actor: str = "cli", note: str = "",
                   manifest_extra: Optional[dict] = None) -> dict:
    """根据当前已通过构建契约的场景生成 manifest (不含运行时句柄)。"""
    if source_phase not in PHASES:
        raise ArtifactPathError(f"未知阶段: {source_phase!r}")
    type_names = object_type_names(sim)
    objects = normalize_objects(scene.describe(), cfg.REQUIRED_ALIASES,
                                type_names)
    report = dict(getattr(scene, "build_report", {}) or {})
    layout = dataclasses.asdict(scene.layout) if scene.layout else {}
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_id": artifact_id,
        "created_at": utc_now(),
        "source_phase": source_phase,
        "protected": bool(protected),
        "parent_artifact": parent_artifact,
        "note": note,
        "actor": actor,
        "code": collect_code_info(),
        "config": {
            "digest": config_digest(),
            "summary": config_summary(),
        },
        "simulator": simulator_info(sim),
        "models": collect_model_info(),
        "layout": _jsonable(layout),
        "objects": objects,
        "geometry": _geometry_summary(scene),
        "tool": _jsonable(scene.tool_geometry()),
        "physics": _physics_summary(sim, scene),
        "scripts": _script_policy(scene),
        "builder": {
            "module": "stage1.scene",
            "version": getattr(cfg, "SCENE_BUILDER_VERSION", "unknown"),
            "clear_scene": True,
        },
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


_POS_TOL_STATIC = 0.002
_POS_TOL_DYNAMIC = 0.02


def validate_scene_against_manifest(sim, manifest: dict, scene=None,
                                    layout=None) -> dict:
    """只读校验当前已加载场景与 manifest 的对象身份/契约。

    不修复、不重命名、不补建任何对象。
    """
    checks: list[dict] = []
    differences: list[dict] = []

    try:
        state = int(sim.getSimulationState())
        stopped = state == int(sim.simulation_stopped)
    except Exception as exc:
        state, stopped = None, False
        _check(checks, "sim_state_readable", False, str(exc))
    _check(checks, "simulation_stopped", stopped, f"state={state}")

    type_names = object_type_names(sim)
    objects = manifest.get("objects") or []
    aliases_by_handle: dict[int, str] = {}

    # 枚举一次全场景, 建立 alias → handles
    from .scene import all_objects, alias_of, adopt_scene

    by_alias: dict[str, list[int]] = {}
    for h in all_objects(sim):
        a = alias_of(sim, h)
        if a:
            by_alias.setdefault(a, []).append(h)
            aliases_by_handle[h] = a

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
        h = hits[0]
        actual_type = type_names.get(int(sim.getObjectType(h)),
                                     str(sim.getObjectType(h)))
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
            parent = int(sim.getObjectParent(h))
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
                actual_pos = list(sim.getObjectPosition(h, sim.handle_world))
            except Exception:
                actual_pos = []
            if actual_pos:
                tol = _POS_TOL_STATIC
                try:
                    static = int(sim.getObjectInt32Param(
                        h, sim.shapeintparam_static))
                    if not static:
                        tol = _POS_TOL_DYNAMIC
                except Exception:
                    pass
                pos_ok = all(abs(float(a) - float(b)) <= tol
                             for a, b in zip(actual_pos, expected_pos))
                _check(checks, f"world_pos:{alias}", pos_ok,
                       f"expected={expected_pos} actual="
                       f"{[round(float(v), 5) for v in actual_pos]} "
                       f"tol={tol}")
                if not pos_ok:
                    differences.append({
                        "kind": "world_pos", "alias": alias,
                        "expected": expected_pos,
                        "actual": [round(float(v), 6) for v in actual_pos],
                    })

    legacy_stopper = [h for h in all_objects(sim)
                      if alias_of(sim, h) == cfg.LEGACY_STOPPER_ALIAS]
    _check(checks, "no_legacy_stopper", not legacy_stopper,
           f"found={len(legacy_stopper)}")

    extra = sorted(set(by_alias) - {o.get("alias") for o in objects})

    if scene is None:
        try:
            from .layout import layout_from_dict
            layout = layout or layout_from_dict(manifest.get("layout") or {})
            scene = adopt_scene(sim, layout=layout)
        except Exception as exc:
            _check(checks, "scene_adopt", False,
                   f"{type(exc).__name__}: {exc}")
            scene = None
    if scene is not None:
        try:
            report = scene.verify()
            _check(checks, "scene_contract", bool(report.get("ok")),
                   f"{sum(1 for c in report.get('checks', []) if c['ok'])}"
                   f"/{len(report.get('checks', []))} checks")
            for c in report.get("checks", []):
                if not c.get("ok"):
                    differences.append({"kind": "contract",
                                        "name": c.get("name"),
                                        "detail": c.get("detail")})
        except Exception as exc:
            _check(checks, "scene_contract", False,
                   f"{type(exc).__name__}: {exc}")

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
                      layout=None) -> dict:
    """完整只读校验: 文件摘要 + 环境 + 当前场景对象契约。"""
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
        artifact.manifest,
        simulator=simulator_info(sim),
        required_aliases=cfg.REQUIRED_ALIASES)
    checks.extend(env["checks"])
    differences.extend(env["differences"])

    scene_result = validate_scene_against_manifest(
        sim, artifact.manifest, scene=scene, layout=layout)
    checks.extend(scene_result["checks"])
    differences.extend(scene_result["differences"])

    return {
        "ok": checks_ok(checks) and not differences,
        "artifact_id": artifact.artifact_id,
        "manifest_digest": artifact.manifest_digest,
        "validated_at": utc_now(),
        "checks": checks,
        "differences": differences,
        "missing_aliases": scene_result.get("missing_aliases", []),
        "extra_aliases": scene_result.get("extra_aliases", []),
    }


# ═══════════════════════════════════════════════════════════════
# 工件仓库
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


class ArtifactStore:
    """版本化工件仓库; 所有路径必须位于 root 之内。"""

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root or default_artifact_root()).expanduser().resolve()

    # ── 路径 ────────────────────────────────────────────────
    def namespace_dir(self, phase: str) -> Path:
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

    # ── 创建 ────────────────────────────────────────────────
    def create(self, phase: str, layout: str, stamp: Optional[str] = None
               ) -> ArtifactDraft:
        layout = self._validate_layout_name(layout)
        parent = self.namespace_dir(phase) / layout
        parent.mkdir(parents=True, exist_ok=True)
        version = 1
        for child in parent.iterdir():
            if child.is_dir():
                m = re.match(r"^v(\d{3})_", child.name)
                if m:
                    version = max(version, int(m.group(1)) + 1)
        stamp = stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"v{version:03d}_{stamp}"
        path = parent / name
        path.mkdir(parents=False, exist_ok=False)
        return ArtifactDraft(f"{phase}/{layout}/{name}", path)

    # ── 解析 ────────────────────────────────────────────────
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
        # 完整工件 ID 形如 stage1/L1_S1/v001_...; 不能当作文件系统路径。
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
        for ph in phases:
            ns = self.namespace_dir(ph)
            if not ns.exists():
                continue
            for manifest in ns.rglob(MANIFEST_FILE):
                candidate = manifest.parent
                rel_id = str(candidate.relative_to(self.root)).replace(
                    os.sep, "/")
                if ref in (candidate.name, rel_id, rel_id.split("/", 1)[-1]):
                    hits.append(candidate)
        if not hits:
            raise ArtifactNotFoundError(f"找不到工件 {ref!r} (phase={phase})")
        if len(hits) > 1:
            raise ArtifactPathError(
                f"工件引用 {ref!r} 不唯一: "
                f"{[str(h.relative_to(self.root)) for h in hits]}")
        return self._load(hits[0])

    def find(self, ref: str, phase: Optional[str] = None) -> Artifact:
        if ref == "current":
            return self.read_current(phase or "stage1")
        return self.resolve(ref, phase)

    def list(self, phase: Optional[str] = None) -> list[dict]:
        phases = [phase] if phase else list(PHASES)
        out = []
        for ph in phases:
            ns = self.namespace_dir(ph)
            if not ns.exists():
                continue
            for manifest in sorted(ns.rglob(MANIFEST_FILE)):
                try:
                    m = read_json(manifest)
                except Exception:
                    continue
                out.append({
                    "artifact_id": str(manifest.parent.relative_to(
                        self.root)).replace(os.sep, "/"),
                    "path": str(manifest.parent),
                    "created_at": m.get("created_at"),
                    "protected": bool(m.get("protected")),
                    "source_phase": m.get("source_phase"),
                    "layout": ((m.get("layout") or {}).get("id")
                               or (m.get("layout") or {}).get("name")),
                })
        out.sort(key=lambda a: (a["source_phase"] or "", a["artifact_id"]))
        return out

    # ── current 指针 ────────────────────────────────────────
    def read_current(self, phase: str = "stage1") -> Artifact:
        pointer = self.namespace_dir(phase) / CURRENT_FILE
        if not pointer.exists():
            raise ArtifactNotFoundError(f"没有当前工件指针: {pointer}")
        data = read_json(pointer)
        artifact_id = data.get("artifact_id")
        if not artifact_id:
            raise ManifestError(f"current 指针缺少 artifact_id: {pointer}")
        return self.resolve(artifact_id)

    def write_current(self, artifact: Artifact, phase: Optional[str] = None
                      ) -> Path:
        phase = phase or artifact.manifest.get("source_phase") or "stage1"
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

    # ── 覆盖保护 ────────────────────────────────────────────
    def check_writable(self, artifact: Artifact, force: bool = False) -> None:
        if artifact.protected:
            raise ProtectedArtifactError(
                f"工件 {artifact.artifact_id} 是只读/已验收基线, 拒绝覆盖; "
                "请创建新版本或派生副本")
        if artifact.path.exists() and not force:
            raise ArtifactExistsError(
                f"工件目录已存在: {artifact.path}; 需要 --force-overwrite")
