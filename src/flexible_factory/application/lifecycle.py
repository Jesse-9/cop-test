"""lifecycle.py — v1 场景生命周期: build/save/load/validate/start/stop.

动作语义与已验证阶段一至四保持一致:

    build    → 从父场景构建 v1 场景并执行构建契约校验
    save     → 停止态保存为 v1 工件, 重新加载并只读校验
    load     → 停止态加载 v1 工件, 不重建、不清场
    validate → 只读校验当前场景与 manifest (可选诊断)
    start    → 仅启动当前已加载场景; 先取运行锁再执行启动点全量校验
    stop     → 停止仿真并确认停止态

start 绝不隐式 build/clear; 运行中拒绝 build/load/save/validate/重复 start.
"""

from __future__ import annotations

import enum
import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .. import spec
from ..artifacts import store as sa
from ..simulation import config as cfg
from ..simulation import contract
from ..simulation.layout import layout_from_dict
from ..simulation.scene import adopt_stage4_scene
from ..simulation.scene_base import build_validation_context
from ..util import (append_jsonl, jsonable, read_json,
                    repo_root, utc_now, write_json_atomic)


# ═══════════════════════════════════════════════════════════════
# 异常
# ═══════════════════════════════════════════════════════════════

class LifecycleError(RuntimeError):
    code = "LIFECYCLE_ERROR"

    def __init__(self, message: str, **details):
        super().__init__(message)
        self.details = details


class InvalidTransitionError(LifecycleError):
    code = "INVALID_STATE"


class RunConflictError(LifecycleError):
    code = "RUN_CONFLICT"

    def __init__(self, message: str, owner: Optional[dict] = None, **details):
        super().__init__(message, **details)
        self.owner = owner


class ValidationFailedError(LifecycleError):
    code = "VALIDATION_FAILED"

    def __init__(self, message: str, report: Optional[dict] = None, **details):
        super().__init__(message, **details)
        self.report = report


# ═══════════════════════════════════════════════════════════════
# 状态机
# ═══════════════════════════════════════════════════════════════

class LifecycleState(str, enum.Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTED_STOPPED = "CONNECTED_STOPPED"
    SCENE_LOADED = "SCENE_LOADED"
    SCENE_VALIDATED = "SCENE_VALIDATED"
    SIMULATION_RUNNING = "SIMULATION_RUNNING"
    ERROR = "ERROR"


_TRANSITIONS: dict[tuple[LifecycleState, str], LifecycleState] = {
    (LifecycleState.DISCONNECTED, "connect"):
        LifecycleState.CONNECTED_STOPPED,
    (LifecycleState.CONNECTED_STOPPED, "build"):
        LifecycleState.SCENE_VALIDATED,
    (LifecycleState.CONNECTED_STOPPED, "load"):
        LifecycleState.SCENE_LOADED,
    (LifecycleState.SCENE_LOADED, "load"):
        LifecycleState.SCENE_LOADED,
    (LifecycleState.SCENE_LOADED, "build"):
        LifecycleState.SCENE_VALIDATED,
    (LifecycleState.SCENE_LOADED, "validate"):
        LifecycleState.SCENE_VALIDATED,
    (LifecycleState.SCENE_LOADED, "start"):
        LifecycleState.SIMULATION_RUNNING,
    (LifecycleState.SCENE_VALIDATED, "build"):
        LifecycleState.SCENE_VALIDATED,
    (LifecycleState.SCENE_VALIDATED, "load"):
        LifecycleState.SCENE_LOADED,
    (LifecycleState.SCENE_VALIDATED, "validate"):
        LifecycleState.SCENE_VALIDATED,
    (LifecycleState.SCENE_VALIDATED, "save"):
        LifecycleState.SCENE_VALIDATED,
    (LifecycleState.SCENE_VALIDATED, "start"):
        LifecycleState.SIMULATION_RUNNING,
    (LifecycleState.SIMULATION_RUNNING, "stop"):
        LifecycleState.SCENE_VALIDATED,
    (LifecycleState.ERROR, "connect"):
        LifecycleState.CONNECTED_STOPPED,
    (LifecycleState.ERROR, "build"):
        LifecycleState.SCENE_VALIDATED,
    (LifecycleState.ERROR, "load"):
        LifecycleState.SCENE_LOADED,
}


class LifecycleStateMachine:
    def __init__(self, state: LifecycleState = LifecycleState.DISCONNECTED):
        self.state = LifecycleState(state)

    def allowed(self, action: str) -> bool:
        if action in ("disconnect", "fail"):
            return True
        return (self.state, action) in _TRANSITIONS

    def transition(self, action: str,
                   target: Optional[LifecycleState] = None
                   ) -> LifecycleState:
        if action == "disconnect":
            self.state = LifecycleState.DISCONNECTED
            return self.state
        if action == "fail":
            self.state = LifecycleState.ERROR
            return self.state
        key = (self.state, action)
        if key not in _TRANSITIONS:
            allowed = sorted(action_name for (state, action_name)
                             in _TRANSITIONS if state is self.state)
            raise InvalidTransitionError(
                f"状态 {self.state.value} 不允许动作 {action!r}; "
                f"允许: {allowed}")
        if action == "stop" and target is not None:
            if target not in (LifecycleState.SCENE_VALIDATED,
                              LifecycleState.CONNECTED_STOPPED):
                raise InvalidTransitionError(
                    f"stop 目标状态不合法: {target.value}")
            self.state = LifecycleState(target)
        else:
            self.state = _TRANSITIONS[key]
        return self.state


# ═══════════════════════════════════════════════════════════════
# 会话状态与运行锁
# ═══════════════════════════════════════════════════════════════

@dataclass
class SessionState:
    state: LifecycleState = LifecycleState.DISCONNECTED
    artifact_id: Optional[str] = None
    scene_origin: Optional[str] = None
    manifest_digest: Optional[str] = None
    validated_at: Optional[str] = None
    run_id: Optional[str] = None
    pid: Optional[int] = None
    host: str = "localhost"
    port: int = 23000
    last_error: Optional[str] = None
    has_run_since_build: bool = False
    background: bool = False
    built_layout: Optional[dict] = None
    updated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "state": self.state.value,
            "artifact_id": self.artifact_id,
            "scene_origin": self.scene_origin,
            "manifest_digest": self.manifest_digest,
            "validated_at": self.validated_at,
            "run_id": self.run_id,
            "pid": self.pid,
            "host": self.host,
            "port": self.port,
            "last_error": self.last_error,
            "has_run_since_build": bool(self.has_run_since_build),
            "background": bool(self.background),
            "built_layout": self.built_layout,
            "updated_at": self.updated_at or utc_now(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SessionState":
        try:
            state = LifecycleState(data.get("state", "DISCONNECTED"))
        except ValueError as exc:
            raise sa.ManifestError(
                f"未知会话状态: {data.get('state')!r}") from exc
        return cls(
            state=state,
            artifact_id=data.get("artifact_id"),
            scene_origin=data.get("scene_origin"),
            manifest_digest=data.get("manifest_digest"),
            validated_at=data.get("validated_at"),
            run_id=data.get("run_id"),
            pid=data.get("pid"),
            host=data.get("host", "localhost"),
            port=int(data.get("port", 23000)),
            last_error=data.get("last_error"),
            has_run_since_build=bool(data.get("has_run_since_build")),
            background=bool(data.get("background")),
            built_layout=data.get("built_layout"),
            updated_at=data.get("updated_at", ""),
        )


class SessionStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> SessionState:
        if not self.path.exists():
            return SessionState()
        data = read_json(self.path)
        if not isinstance(data, dict):
            raise sa.ManifestError("会话状态文件顶层必须是对象")
        return SessionState.from_dict(data)

    def save(self, state: SessionState) -> Path:
        state.updated_at = utc_now()
        return write_json_atomic(self.path, state.to_dict())

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def _pid_alive_default(pid: Optional[int]) -> bool:
    if not pid:
        return False
    if int(pid) == os.getpid():
        return True
    if os.name == "nt":                       # pragma: no cover - Windows
        try:
            import ctypes
            STILL_ACTIVE = 259
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
                return bool(ok) and code.value == STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:                                      # pragma: no cover - POSIX
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


class RunLock:
    """PID + run_id 独占锁; 持有进程死亡后可被显式清理."""

    def __init__(self, path: Path,
                 pid_alive: Optional[Callable[[Optional[int]], bool]] = None):
        self.path = Path(path)
        self._pid_alive = pid_alive or _pid_alive_default
        self._run_id: Optional[str] = None

    def owner(self) -> Optional[dict]:
        if not self.path.exists():
            return None
        try:
            data = read_json(self.path)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def owner_alive(self, owner: Optional[dict] = None) -> bool:
        owner = self.owner() if owner is None else owner
        return bool(owner) and self._pid_alive(owner.get("pid"))

    def acquire(self, run_id: str, info: Optional[dict] = None) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(str(self.path),
                             os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                owner = self.owner()
                if owner is not None and self._pid_alive(owner.get("pid")):
                    return False
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                continue
            try:
                payload = {"run_id": run_id, "pid": os.getpid(),
                           "created_at": utc_now()}
                payload.update(info or {})
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False,
                              sort_keys=True)
                    handle.flush()
                    os.fsync(handle.fileno())
            except Exception:
                try:
                    self.path.unlink()
                except OSError:
                    pass
                raise
            self._run_id = run_id
            return True
        return False

    def release(self) -> bool:
        owner = self.owner()
        if owner is None:
            return False
        if self._run_id is None or owner.get("run_id") != self._run_id:
            return False
        if (owner.get("pid") not in (os.getpid(), None)
                and self._pid_alive(owner.get("pid"))):
            return False
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        self._run_id = None
        return True

    def clear_stale(self) -> Optional[dict]:
        owner = self.owner()
        if owner is None:
            return None
        if self._pid_alive(owner.get("pid")):
            return None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        self._run_id = None
        return owner


def _new_run_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"run_{stamp}_{uuid.uuid4().hex[:6]}"


# ═══════════════════════════════════════════════════════════════
# 生命周期管理器
# ═══════════════════════════════════════════════════════════════

class LifecycleManager:
    """把 sim 与 v1 工件仓库组合成显式生命周期动作."""

    def __init__(self, sim, simIK=None, *,
                 artifact_root: Optional[Path] = None,
                 session_path: Optional[Path] = None,
                 actor: str = "cli",
                 host: str = "localhost", port: int = 23000,
                 report_dir: Optional[Path] = None,
                 store: Optional[sa.ArtifactStore] = None):
        self.sim = sim
        self.simIK = simIK
        self.store = store or sa.ArtifactStore(artifact_root)
        runtime = self.store.namespace_dir("v1") / ".runtime"
        self.session_store = SessionStore(
            session_path or runtime / "session.json")
        self.run_lock = RunLock(runtime / "run.lock")
        self.runtime_log = runtime / "lifecycle.jsonl"
        self.actor = actor
        self.host = host
        self.port = port
        self.report_dir = Path(report_dir or (repo_root() / spec.REPORT_DIR))
        self.session = self.session_store.load()
        self.session.host, self.session.port = host, port
        self.sm = LifecycleStateMachine(self.session.state)
        self.scene = None
        self.artifact: Optional[sa.Artifact] = None
        self.last_validation: Optional[dict] = None
        self.has_run_since_build = bool(self.session.has_run_since_build)
        if sim is not None and self.sm.state is LifecycleState.DISCONNECTED:
            self._apply(LifecycleState.CONNECTED_STOPPED,
                        origin=self.session.scene_origin, event="connected")

    # ── 内部状态 ────────────────────────────────────────────

    def _apply(self, state: LifecycleState, *, origin=None, event=None,
               **details) -> None:
        self.sm.state = state
        self.session.state = state
        if origin is not None:
            self.session.scene_origin = origin
        self.session.pid = (os.getpid()
                            if state is LifecycleState.SIMULATION_RUNNING
                            else None)
        self.session_store.save(self.session)
        self._event(event or state.value.lower(), state=state.value, **details)

    def _fail(self, error: str, event: str = "failed", **details) -> None:
        self.session.last_error = error
        self._apply(LifecycleState.ERROR, event=event, error=error, **details)

    def _event(self, event: str, **details) -> None:
        payload = {
            "ts": utc_now(), "actor": self.actor, "event": event,
            "state": self.session.state.value,
            "artifact_id": self.session.artifact_id,
            "run_id": self.session.run_id,
            **details,
        }
        try:
            append_jsonl(self.runtime_log, payload)
        except OSError:
            pass
        if (self.artifact is not None
                and not self.artifact.protected
                and self.artifact.lifecycle_path.parent.exists()):
            try:
                append_jsonl(self.artifact.lifecycle_path, payload)
            except OSError:
                pass

    def _reload_session(self) -> None:
        if not self.session_store.path.exists():
            return
        try:
            disk = self.session_store.load()
        except Exception:
            return
        if (disk.updated_at != self.session.updated_at
                or disk.state is not self.session.state):
            self.session = disk
            self.sm.state = disk.state
            if disk.artifact_id:
                try:
                    self.artifact = self.store.find(disk.artifact_id)
                except sa.ArtifactError:
                    self.artifact = None

    def _ensure_scene(self):
        """跨 CLI 进程恢复场景: 按会话来源重新解析句柄."""
        if self.scene is not None:
            return self.scene
        payload = None
        if self.session.scene_origin == "ARTIFACT" and self.session.artifact_id:
            artifact = self.store.find(self.session.artifact_id)
            self.artifact = artifact
            payload = artifact.manifest.get("layout") or {}
        elif self.session.scene_origin == "BUILT":
            payload = self.session.built_layout or {}
        if payload is None:
            return None
        self.scene = adopt_stage4_scene(
            self.sim, layout=layout_from_dict(payload))
        return self.scene

    def _sim_state(self) -> Optional[int]:
        try:
            return int(self.sim.getSimulationState())
        except Exception:
            return None

    def _ensure_stopped(self, action: str) -> None:
        state = self._sim_state()
        stopped = getattr(self.sim, "simulation_stopped", 0)
        if state is None or state != int(stopped):
            raise InvalidTransitionError(
                f"{action} 需要仿真停止态, 当前 getSimulationState()={state}")

    def _guard(self, action: str) -> None:
        if not self.sm.allowed(action):
            allowed = sorted(action_name for (state, action_name)
                             in _TRANSITIONS if state is self.sm.state)
            raise InvalidTransitionError(
                f"状态 {self.sm.state.value} 不允许动作 {action!r}; "
                f"允许: {allowed}")

    def _require_no_live_lock(self, action: str) -> None:
        owner = self.run_lock.owner()
        if owner is not None and self.run_lock.owner_alive(owner):
            raise RunConflictError(
                f"{action} 被拒绝: 另一个控制循环正在运行 (pid="
                f"{owner.get('pid')}, run_id={owner.get('run_id')})",
                owner=owner)

    def refresh(self) -> dict:
        self._reload_session()
        sim_state = self._sim_state()
        stopped = (sim_state == int(getattr(self.sim, "simulation_stopped", 0)))
        owner = self.run_lock.owner()
        owner_alive = self.run_lock.owner_alive(owner)
        cleared_owner = None
        if owner is not None and not owner_alive:
            cleared_owner = self.run_lock.clear_stale()
            if cleared_owner is not None:
                owner = None
                self._event("stale_lock_cleared", owner=cleared_owner)
        recovered = False
        stale_run = False
        if self.sm.state is LifecycleState.SIMULATION_RUNNING and not owner_alive:
            if stopped:
                stale_run = not self.session.background
                self.session.background = False
                target = (LifecycleState.SCENE_VALIDATED
                          if (self.session.artifact_id
                              or self.session.scene_origin == "BUILT")
                          else LifecycleState.CONNECTED_STOPPED)
                self.session.run_id = None
                self._apply(target, event="stale_run_recovered",
                            reason="owner_dead_and_sim_stopped")
                recovered = True
            elif self.session.background:
                pass
            else:
                stale_run = True
                if cleared_owner is not None:
                    self.session.last_error = (
                        f"stale_run: 持有进程 pid={cleared_owner.get('pid')} "
                        f"(run_id={cleared_owner.get('run_id')}) 已退出, "
                        "但仿真仍在运行; 请执行 stop")
                    self.session_store.save(self.session)
                    self._event("stale_run_detected", owner=cleared_owner)
        return {
            "state": self.sm.state.value,
            "sim_state": sim_state,
            "sim_stopped": stopped,
            "lock_owner": owner,
            "lock_alive": owner_alive,
            "recovered": recovered,
            "stale_run": stale_run,
            "stale_lock_cleared": cleared_owner,
            "background": bool(self.session.background),
        }

    # ── 动作: build ─────────────────────────────────────────

    def build(self, *, parent_scene: Path, parent_layout: dict,
              layout=None) -> dict:
        """从父场景 (历史 stage3 工件) 增加 C/D/E 与物流线, 生成 v1 场景.

        v1 默认用户流程是加载已验收 v1 工件; build 仅用于显式重建/迁移.
        父场景只读, 不修改父工件.
        """
        self.refresh()
        self._guard("build")
        self._ensure_stopped("build")
        self._require_no_live_lock("build")
        parent_scene = Path(parent_scene)
        if not parent_scene.exists():
            raise sa.ArtifactNotFoundError(str(parent_scene))

        from ..simulation.layout_dual import layout_from_dict as s3_layout
        from ..simulation.scene_dual import adopt_stage3_scene
        from ..simulation.scene import build_stage4_scene

        try:
            self.sim.loadScene(str(parent_scene))
            self._ensure_stopped("build")
            base_scene = adopt_stage3_scene(
                self.sim, layout=s3_layout(parent_layout))
            target_layout = layout or None
            if target_layout is None:
                from ..simulation.layout import default_layout
                target_layout = default_layout()
            scene = build_stage4_scene(self.sim, base_scene,
                                       layout=target_layout)
            report = scene.verify()
            if not report.get("ok"):
                raise ValidationFailedError("v1 构建契约未通过",
                                            report=report)
        except Exception as exc:
            self._fail(f"v1 build 失败: {type(exc).__name__}: {exc}",
                       event="build_failed")
            raise

        self.scene = scene
        self.artifact = None
        self.last_validation = None
        self.has_run_since_build = False
        self.session.has_run_since_build = False
        self.session.artifact_id = None
        self.session.manifest_digest = None
        self.session.validated_at = utc_now()
        self.session.run_id = None
        self.session.last_error = None
        self.session.built_layout = target_layout.as_dict()
        self.sm.transition("build")
        self._apply(LifecycleState.SCENE_VALIDATED, origin="BUILT",
                    event="built", layout=scene.layout.name,
                    checks=len(scene.build_report.get("checks", [])))
        return self.summary()

    # ── 动作: load ──────────────────────────────────────────

    def load(self, artifact_ref: str = "current") -> dict:
        """在仿真停止态加载 v1 工件, 不重建、不清场."""
        self.refresh()
        self._guard("load")
        self._ensure_stopped("load")
        self._require_no_live_lock("load")
        artifact = self.store.find(artifact_ref)
        schema = sa.validate_manifest_schema(
            artifact.manifest, cfg.REQUIRED_ALIASES)
        if not sa.checks_ok(schema):
            failed = [item for item in schema if not item["ok"]]
            self._fail(f"manifest schema 校验失败: {failed}",
                       event="load_failed")
            raise ValidationFailedError("manifest schema 校验失败",
                                        report={"checks": schema})
        if not artifact.scene_path.exists():
            self._fail(f"场景文件缺失: {artifact.scene_path}",
                       event="load_failed")
            raise sa.ArtifactNotFoundError(str(artifact.scene_path))
        expected = (artifact.manifest.get("artifact") or {}).get("sha256")
        actual = sa.sha256_file(artifact.scene_path)
        if expected != actual:
            self._fail(
                f"场景文件摘要不匹配: expected={expected} actual={actual}",
                event="load_failed")
            raise ValidationFailedError(
                "场景文件摘要不匹配",
                report={"expected": expected, "actual": actual})

        try:
            self.sim.loadScene(str(artifact.scene_path))
        except Exception as exc:
            self._fail(f"loadScene 失败: {exc}", event="load_failed")
            raise
        self._ensure_stopped("load")
        try:
            scene = adopt_stage4_scene(
                self.sim,
                layout=layout_from_dict(artifact.manifest.get("layout") or {}))
        except Exception as exc:
            self._fail(f"加载后句柄解析失败: {exc}", event="load_failed")
            raise
        self.scene = scene
        self.artifact = artifact
        self.last_validation = None
        self.has_run_since_build = False
        self.session.has_run_since_build = False
        self.session.artifact_id = artifact.artifact_id
        self.session.manifest_digest = artifact.manifest_digest
        self.session.validated_at = None
        self.session.run_id = None
        self.session.last_error = None
        self.session.built_layout = None
        self.sm.transition("load")
        self._apply(LifecycleState.SCENE_LOADED, origin="ARTIFACT",
                    event="loaded", path=str(artifact.scene_path))
        return {**self.summary(), "validation_at_start": True}

    # ── 动作: validate ──────────────────────────────────────

    def _validate_current(self) -> dict:
        self._ensure_scene()
        if self.scene is None:
            raise InvalidTransitionError("没有已加载场景; 先 build 或 load")
        if self.artifact is None:
            raise InvalidTransitionError(
                "当前场景由 build 生成, 没有 manifest; 先 save 或 load 工件")
        context = build_validation_context(self.sim, self.scene)
        result = sa.validate_artifact(
            self.sim, self.artifact, scene=self.scene,
            config=contract.config_summary(),
            config_digest=contract.config_digest(),
            required_aliases=cfg.REQUIRED_ALIASES,
            context=context)
        extra = contract.validate_contract(self.sim, self.artifact,
                                           scene=self.scene,
                                           context=context)
        result["checks"].extend(extra.get("checks", []))
        result["differences"].extend(extra.get("differences", []))
        result["contract"] = {"ok": bool(extra.get("ok")),
                              "contract_version": extra.get("contract_version")}
        result["ok"] = bool(result.get("ok")) and bool(extra.get("ok"))
        return result

    def validate(self, artifact_ref: Optional[str] = None) -> dict:
        self.refresh()
        if self.session.scene_origin is None:
            raise InvalidTransitionError(
                "当前会话没有已加载场景; 先 build 或 load")
        if artifact_ref is not None:
            ref_artifact = self.store.find(artifact_ref)
            if (self.artifact is None
                    or ref_artifact.artifact_id != self.artifact.artifact_id):
                raise InvalidTransitionError(
                    f"请先 load 工件 {ref_artifact.artifact_id}; "
                    f"当前={self.artifact.artifact_id if self.artifact else None}")
        self._guard("validate")
        self._ensure_stopped("validate")
        try:
            result = self._validate_current()
        except InvalidTransitionError:
            raise
        except Exception as exc:
            self._fail(f"只读校验异常: {type(exc).__name__}: {exc}",
                       event="validation_failed")
            raise
        self.last_validation = result
        self._write_validation_report(result)
        if not result["ok"]:
            self._fail("只读校验失败", event="validation_failed",
                       differences=len(result.get("differences", [])))
            raise ValidationFailedError("只读校验失败", report=result)
        self.session.validated_at = result["validated_at"]
        self.session.manifest_digest = self.artifact.manifest_digest
        self.session.last_error = None
        self.sm.transition("validate")
        self._apply(LifecycleState.SCENE_VALIDATED, event="validated",
                    differences=0)
        return result

    def _write_validation_report(self, result: dict) -> None:
        try:
            self.report_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            name = (result.get("artifact_id") or "scene").replace("/", "_")
            write_json_atomic(
                self.report_dir / f"scene_validation_{name}_{stamp}.json",
                result)
        except OSError:
            pass

    # ── 动作: save ──────────────────────────────────────────

    def save(self, *, note: str = "", protected: bool = True,
             provenance: Optional[dict] = None,
             allow_after_run: bool = False) -> dict:
        self.refresh()
        self._guard("save")
        self._ensure_stopped("save")
        self._require_no_live_lock("save")
        self._ensure_scene()
        if self.scene is None:
            raise InvalidTransitionError("没有可保存的已校验场景")
        if self.has_run_since_build and not allow_after_run:
            raise LifecycleError(
                "场景在构建后已经运行, 不能作为基线工件保存; "
                "请重新 build (或用 --allow-after-run 显式接受非初始位姿)")

        report = self.scene.verify()
        if not report.get("ok"):
            failed = [item for item in report.get("checks", [])
                      if not item["ok"]]
            self._fail(f"保存前契约校验失败: {failed}", event="save_failed")
            raise ValidationFailedError("保存前契约校验失败", report=report)

        draft = self.store.create("v1", self.scene.layout.name)
        config_summary = contract.config_summary()
        extra = contract.build_contract_extra(
            sim=self.sim, scene=self.scene,
            provenance=provenance or {}, notes=note)
        manifest = sa.build_manifest(
            self.sim, self.scene, artifact_id=draft.artifact_id,
            config_summary=config_summary,
            config_digest=contract.config_digest(),
            required_aliases=cfg.REQUIRED_ALIASES,
            protected=protected, parent_artifact=provenance,
            actor=self.actor, note=note,
            manifest_extra={"v1": extra})

        partial = draft.path / "scene.partial.ttt"
        try:
            self.sim.saveScene(str(partial))
        except Exception as exc:
            self._fail(f"saveScene 失败: {exc}", event="save_failed")
            raise
        if not partial.exists() or partial.stat().st_size <= 0:
            self._fail("saveScene 未产生有效文件", event="save_failed")
            raise sa.ArtifactError(f"saveScene 未产生有效文件: {partial}")

        manifest["artifact"]["sha256"] = sa.sha256_file(partial)
        manifest["artifact"]["size"] = partial.stat().st_size
        write_json_atomic(draft.path / sa.MANIFEST_FILE, manifest)

        # 保存后立即重新加载验证, 防止半成品进入 current 指针.
        try:
            self.sim.loadScene(str(partial))
            self._ensure_stopped("save")
            loaded_scene = adopt_stage4_scene(
                self.sim,
                layout=layout_from_dict(manifest.get("layout") or {}))
            context = build_validation_context(self.sim, loaded_scene)
            if context.scene_error:
                raise RuntimeError(context.scene_error)
            validation_manifest = json.loads(json.dumps(manifest))
            validation_manifest["artifact"]["scene_file"] = partial.name
            artifact = sa.Artifact(artifact_id=draft.artifact_id,
                                   path=draft.path,
                                   manifest=validation_manifest)
            result = sa.validate_artifact(
                self.sim, artifact, scene=loaded_scene,
                config=config_summary,
                config_digest=contract.config_digest(),
                required_aliases=cfg.REQUIRED_ALIASES,
                context=context)
            extra_result = contract.validate_contract(
                self.sim, artifact, scene=loaded_scene, context=context)
            result["checks"].extend(extra_result.get("checks", []))
            result["differences"].extend(extra_result.get("differences", []))
            result["contract"] = {
                "ok": bool(extra_result.get("ok")),
                "contract_version": extra_result.get("contract_version")}
            result["ok"] = (bool(result.get("ok"))
                            and bool(extra_result.get("ok")))
        except Exception as exc:
            self._fail(f"保存后重载校验异常: {exc}", event="save_failed")
            write_json_atomic(draft.path / sa.VALIDATION_FILE, {
                "ok": False, "error": f"{type(exc).__name__}: {exc}",
                "validated_at": utc_now(),
            })
            raise
        write_json_atomic(draft.path / sa.VALIDATION_FILE, result)

        if not result["ok"]:
            self._fail("保存后重载校验失败", event="save_failed",
                       differences=len(result.get("differences", [])))
            raise ValidationFailedError("保存后重载校验失败", report=result)

        append_jsonl(draft.path / sa.LIFECYCLE_FILE, {
            "ts": manifest["created_at"], "actor": self.actor,
            "event": "saved", "artifact_id": draft.artifact_id,
            "source_phase": "v1", "protected": protected,
            "scene_sha256": manifest["artifact"]["sha256"],
            "scene_size": manifest["artifact"]["size"],
            "provenance": provenance,
        })
        append_jsonl(draft.path / sa.LIFECYCLE_FILE, {
            "ts": result["validated_at"], "actor": self.actor,
            "event": "validated", "artifact_id": draft.artifact_id,
            "ok": True, "checks": len(result.get("checks", [])),
            "differences": 0,
        })

        final_scene = draft.path / sa.SCENE_FILE
        os.replace(partial, final_scene)
        final_artifact = sa.Artifact(artifact_id=draft.artifact_id,
                                     path=draft.path, manifest=manifest)
        self.store.write_current(final_artifact, phase="v1")
        if protected:
            for name in sa.ARTIFACT_FILES:
                path = draft.path / name
                if path.exists():
                    sa.set_readonly(path, True)

        self.scene = loaded_scene
        self.artifact = final_artifact
        self.last_validation = result
        self.session.artifact_id = final_artifact.artifact_id
        self.session.manifest_digest = final_artifact.manifest_digest
        self.session.validated_at = result["validated_at"]
        self.session.last_error = None
        self.session.built_layout = None
        self.sm.transition("save")
        self._apply(LifecycleState.SCENE_VALIDATED, origin="ARTIFACT",
                    event="saved", path=str(final_scene),
                    protected=protected,
                    scene_sha256=manifest["artifact"]["sha256"])
        return {**self.summary(), "validation": result}

    # ── 动作: start 前置 ────────────────────────────────────

    def prepare_start(self, target: str = "full_flow",
                      run_id: Optional[str] = None) -> dict:
        """取得运行锁并执行启动点校验, 通过后进入 SIMULATION_RUNNING.

        ``run_id`` 可由上层订单服务预先分配, 使订单运行关联与生命周期
        运行锁/运行记录使用同一 ID; 留空时自动生成.
        """
        self.refresh()
        self._guard("start")
        self._ensure_stopped("start")
        self._require_no_live_lock("start")

        run_id = str(run_id or "").strip() or _new_run_id()
        if not self.run_lock.acquire(run_id, {
                "artifact_id": self.session.artifact_id,
                "host": self.host, "port": self.port,
                "target": target}):
            raise RunConflictError(
                "无法获取运行锁: 另一个控制循环正在运行",
                owner=self.run_lock.owner())
        try:
            try:
                self._ensure_scene()
            except Exception:
                pass
            if self.scene is None:
                raise InvalidTransitionError("没有已加载场景; 先 build 或 load")
            if self.artifact is None:
                if not self.scene.verify().get("ok"):
                    self._fail("start 前构建契约未通过",
                               event="start_refused")
                    raise ValidationFailedError("start 前构建契约未通过")
            else:
                result = self._validate_current()
                self.last_validation = result
                self._write_validation_report(result)
                if not result["ok"]:
                    self._fail("start 前只读校验失败",
                               event="start_refused",
                               differences=len(result.get("differences", [])))
                    raise ValidationFailedError("start 前只读校验失败",
                                                report=result)
                self.session.validated_at = result.get("validated_at")
                self.session.manifest_digest = self.artifact.manifest_digest
                self.session.last_error = None
        except Exception:
            self.run_lock.release()
            raise

        self.session.run_id = run_id
        self.session.pid = os.getpid()
        self.sm.transition("start")
        self._apply(LifecycleState.SIMULATION_RUNNING, event="started",
                    target=target, run_id=run_id)
        return {**self.summary(), "run_id": run_id}

    def finish_run(self, outcome: str = "STOPPED",
                   error: Optional[str] = None) -> dict:
        """运行线程结束后由持有者调用; 幂等."""
        if self.sm.state is LifecycleState.SIMULATION_RUNNING:
            target = (LifecycleState.SCENE_VALIDATED
                      if (self.session.artifact_id
                          or self.session.scene_origin == "BUILT")
                      else LifecycleState.CONNECTED_STOPPED)
            self.session.run_id = None
            self.session.pid = None
            self.session.background = False
            self.session.last_error = error
            self._apply(target, event="stopped", outcome=outcome,
                        error=error)
        self.run_lock.release()
        self.has_run_since_build = True
        self.session.has_run_since_build = True
        if error is not None:
            self.session.last_error = error
        self.session_store.save(self.session)
        return self.summary()

    # ── 动作: stop ──────────────────────────────────────────

    def stop(self, timeout: float = 10.0) -> dict:
        self.refresh()
        state = self._sim_state()
        stopped = getattr(self.sim, "simulation_stopped", 0)
        owner = self.run_lock.owner()
        stopped_now = False
        if state is not None and state != int(stopped):
            try:
                self.sim.stopSimulation()
            except Exception as exc:
                raise LifecycleError(f"stopSimulation 失败: {exc}") from exc
            deadline = time.time() + timeout
            while time.time() < deadline:
                if self._sim_state() == int(stopped):
                    stopped_now = True
                    break
                time.sleep(0.05)
            if self._sim_state() != int(stopped):
                raise LifecycleError(
                    "停止未确认: Remote API 未返回停止态")
        else:
            stopped_now = True

        own_lock = (owner is not None and owner.get("pid") == os.getpid()
                    and owner.get("run_id") == self.session.run_id)
        if self.sm.state is LifecycleState.SIMULATION_RUNNING:
            if own_lock or not self.run_lock.owner_alive(owner):
                return self.finish_run("STOPPED")
            self._event("stop_requested", owner=owner)
            return {**self.summary(),
                    "stop_requested": True, "owner": owner,
                    "message": "已停止物理引擎; 等待持有者线程收尾并释放锁"}
        return {**self.summary(), "stopped": stopped_now}

    # ── 查询 ────────────────────────────────────────────────

    def allow_stop_at(self) -> LifecycleState:
        return (LifecycleState.SCENE_VALIDATED
                if self.session.artifact_id
                else LifecycleState.CONNECTED_STOPPED)

    def allowed_actions(self) -> list[str]:
        actions = []
        has_manifest = (self.artifact is not None
                        or self.session.scene_origin == "ARTIFACT")
        for action in ("build", "save", "load", "validate", "start", "stop"):
            if action == "stop":
                if self.sm.state is LifecycleState.SIMULATION_RUNNING:
                    actions.append(action)
                continue
            if action == "validate" and not has_manifest:
                continue
            if self.sm.allowed(action):
                actions.append(action)
        return actions

    def summary(self) -> dict:
        return {
            "lifecycle_state": self.sm.state.value,
            "artifact_id": self.session.artifact_id,
            "scene_origin": self.session.scene_origin,
            "manifest_digest": self.session.manifest_digest,
            "validated_at": self.session.validated_at,
            "run_id": self.session.run_id,
            "pid": self.session.pid,
            "host": self.host,
            "port": self.port,
            "last_error": self.session.last_error,
            "background": bool(self.session.background),
            "sim_state": self._sim_state(),
            "allowed_actions": self.allowed_actions(),
        }

    def status(self) -> dict:
        info = self.refresh()
        out = {**self.summary(), **info}
        if self.artifact is None and self.session.artifact_id:
            try:
                self.artifact = self.store.find(self.session.artifact_id)
            except sa.ArtifactError:
                pass
        if self.artifact is not None:
            out["artifact"] = self.artifact.manifest.get("artifact")
            if (self.last_validation is None
                    and self.artifact.validation_path.exists()):
                try:
                    self.last_validation = read_json(
                        self.artifact.validation_path)
                except Exception:
                    pass
        else:
            out["artifact"] = None
        out["validation"] = self.last_validation
        return out
