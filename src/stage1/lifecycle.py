"""lifecycle.py — 场景生命周期状态机、会话状态与显式动作 (步骤 1.2).

动作语义 (与 CLI/MCP 一致):

    build_scene     → 从空白/当前场景构建并执行构建契约校验
    save_scene      → 停止态保存为新版本工件, 重新加载并校验
    load_scene      → 停止态加载指定 .ttt 工件
    validate_scene  → 只读校验当前场景与 manifest
    start_simulation→ 仅启动当前已加载且校验通过的场景
    stop_simulation → 停止仿真并确认停止态

start 绝不隐式 build/clear; 运行中拒绝 build/load/save/validate/重复 start。
"""

from __future__ import annotations

import enum
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from . import config as cfg
from . import scene_artifact as sa


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


# 允许的状态迁移; stop 的目标状态由调用方按是否持有工件决定。
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
            allowed = sorted(a for (s, a) in _TRANSITIONS if s is self.state)
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
# 会话状态 (跨 CLI 进程持久化)
# ═══════════════════════════════════════════════════════════════

@dataclass
class SessionState:
    state: LifecycleState = LifecycleState.DISCONNECTED
    artifact_id: Optional[str] = None
    scene_origin: Optional[str] = None       # BUILT | ARTIFACT
    manifest_digest: Optional[str] = None
    validated_at: Optional[str] = None
    run_id: Optional[str] = None
    pid: Optional[int] = None
    host: str = "localhost"
    port: int = 23000
    last_error: Optional[str] = None
    has_run_since_build: bool = False
    background: bool = False        # --keep-running: 无控制循环但仿真仍运行
    built_phase: Optional[str] = None
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
            "built_phase": self.built_phase,
            "built_layout": self.built_layout,
            "updated_at": self.updated_at or sa.utc_now(),
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
            built_phase=data.get("built_phase"),
            built_layout=data.get("built_layout"),
            updated_at=data.get("updated_at", ""),
        )


class SessionStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> SessionState:
        if not self.path.exists():
            return SessionState()
        data = sa.read_json(self.path)
        if not isinstance(data, dict):
            raise sa.ManifestError("会话状态文件顶层必须是对象")
        return SessionState.from_dict(data)

    def save(self, state: SessionState) -> Path:
        state.updated_at = sa.utc_now()
        return sa.write_json_atomic(self.path, state.to_dict())

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


# ═══════════════════════════════════════════════════════════════
# 跨进程运行锁
# ═══════════════════════════════════════════════════════════════

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
    """PID + run_id 独占锁; 持有进程死亡后可被显式清理。"""

    def __init__(self, path: Path,
                 pid_alive: Optional[Callable[[Optional[int]], bool]] = None):
        self.path = Path(path)
        self._pid_alive = pid_alive or _pid_alive_default
        self._run_id: Optional[str] = None

    def owner(self) -> Optional[dict]:
        if not self.path.exists():
            return None
        try:
            data = sa.read_json(self.path)
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
                # 持有进程已死亡 → 清理陈旧锁后重试一次
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                continue
            try:
                payload = {"run_id": run_id, "pid": os.getpid(),
                           "created_at": sa.utc_now()}
                payload.update(info or {})
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, sort_keys=True)
                    f.flush()
                    os.fsync(f.fileno())
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
            return False          # 只释放本实例持有的锁
        if owner.get("pid") not in (os.getpid(), None) \
                and self._pid_alive(owner.get("pid")):
            return False          # 其他存活进程持有
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        self._run_id = None
        return True

    def clear_stale(self) -> Optional[dict]:
        """清理持有进程已死亡的锁文件; 返回被清理的 owner, 无则 None。"""
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


# ═══════════════════════════════════════════════════════════════
# 生命周期管理器
# ═══════════════════════════════════════════════════════════════

def _new_run_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"run_{stamp}_{uuid.uuid4().hex[:6]}"


# ═══════════════════════════════════════════════════════════════
# 阶段二契约扩展点 (lazy import; 阶段一默认行为不变)
# ═══════════════════════════════════════════════════════════════

def _default_manifest_extra(phase: str, sim, scene, parent_info) -> Optional[dict]:
    if phase == "stage2":
        from stage2.scene_contract import build_stage2_extra
        return {"stage2": build_stage2_extra(
            sim=sim, scene=scene, parent_info=parent_info)}
    if phase == "stage3":
        from stage3.scene_contract import build_stage3_extra
        return {"stage3": build_stage3_extra(
            sim=sim, scene=scene, parent_info=parent_info)}
    return None


def _default_extra_validator(artifact, sim, scene) -> Optional[dict]:
    source_phase = artifact.manifest.get("source_phase")
    extra = (artifact.manifest.get("extra") or {}).get("stage3")
    if source_phase == "stage3" or extra is not None:
        from stage3.scene_contract import validate_stage3_contract
        return validate_stage3_contract(sim, artifact, scene=scene)
    extra = (artifact.manifest.get("extra") or {}).get("stage2")
    if extra is None:
        return None
    from stage2.scene_contract import validate_stage2_contract
    return validate_stage2_contract(sim, artifact, scene=scene)


def _merge_extra_validation(result: dict, extra: Optional[dict]) -> dict:
    if extra is None:
        return result
    result.setdefault("checks", []).extend(extra.get("checks", []))
    result.setdefault("differences", []).extend(extra.get("differences", []))
    contract = {
        "ok": bool(extra.get("ok")),
        "contract_version": extra.get("contract_version"),
    }
    result["stage_contract"] = contract
    if str(extra.get("contract_version", "")).startswith("stage3"):
        result["stage3_contract"] = contract
    else:
        # 阶段二与旧测试的兼容字段
        result["stage2_contract"] = contract
    result["ok"] = bool(result.get("ok")) and bool(extra.get("ok"))
    return result


class LifecycleManager:
    """把 sim 与工件仓库组合成显式生命周期动作。

    调用方负责建立/断开 ZMQ 连接; 本类不创建连接, 只按当前 sim 执行动作。
    """

    def __init__(self, sim, simIK=None, *,
                 artifact_root: Optional[Path] = None,
                 session_path: Optional[Path] = None,
                 actor: str = "cli",
                 host: str = "localhost", port: int = 23000,
                 report_dir: Optional[Path] = None,
                 store: Optional[sa.ArtifactStore] = None,
                 manifest_extra_provider=None,
                 extra_validator=None):
        self.sim = sim
        self.simIK = simIK
        self.store = store or sa.ArtifactStore(artifact_root)
        self.manifest_extra_provider = manifest_extra_provider
        self.extra_validator = extra_validator
        runtime = self.store.root / ".runtime"
        self.session_store = SessionStore(
            session_path or runtime / "session.json")
        self.run_lock = RunLock(runtime / "run.lock")
        self.runtime_log = runtime / "lifecycle.jsonl"
        self.actor = actor
        self.host = host
        self.port = port
        self.report_dir = Path(report_dir or
                               sa.repo_root() / "docs" / "implementation" / "runs")
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
        self.session.pid = os.getpid() if state is LifecycleState.SIMULATION_RUNNING else None
        self.session_store.save(self.session)
        self._event(event or state.value.lower(), state=state.value, **details)

    def _fail(self, error: str, event: str = "failed", **details) -> None:
        self.session.last_error = error
        self._apply(LifecycleState.ERROR, event=event, error=error, **details)

    def _event(self, event: str, **details) -> None:
        payload = {
            "ts": sa.utc_now(), "actor": self.actor, "event": event,
            "state": self.session.state.value,
            "artifact_id": self.session.artifact_id,
            "run_id": self.session.run_id,
            **details,
        }
        try:
            sa.append_jsonl(self.runtime_log, payload)
        except OSError:
            pass
        if (self.artifact is not None
                and not self.artifact.protected
                and self.artifact.lifecycle_path.parent.exists()):
            try:
                sa.append_jsonl(self.artifact.lifecycle_path, payload)
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
        """跨 CLI 进程恢复场景: 按会话来源与阶段重新解析句柄。"""
        if self.scene is not None:
            return self.scene
        from .layout import default_layout, layout_from_dict
        from .scene import adopt_scene

        layout = None
        phase = None
        if self.session.scene_origin == "ARTIFACT" and self.session.artifact_id:
            artifact = self.store.find(self.session.artifact_id)
            self.artifact = artifact
            phase = artifact.manifest.get("source_phase")
            if phase == "stage3":
                from stage3.layout import layout_from_dict as s3_layout
                from stage3.scene import adopt_stage3_scene
                self.scene = adopt_stage3_scene(
                    self.sim, layout=s3_layout(
                        artifact.manifest.get("layout") or {}))
                return self.scene
            layout = layout_from_dict(artifact.manifest.get("layout") or {})
        elif self.session.scene_origin == "BUILT":
            phase = self.session.built_phase or "stage1"
            if phase == "stage3":
                from stage3.layout import layout_from_dict as s3_layout
                from stage3.scene import adopt_stage3_scene
                payload = self.session.built_layout or {}
                self.scene = adopt_stage3_scene(
                    self.sim, layout=s3_layout(payload))
                return self.scene
            layout = default_layout()
        if layout is None:
            return None
        self.scene = adopt_scene(self.sim, layout=layout)
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
        """只检查状态是否允许动作, 不改变状态 (前置条件全部通过后再生效)。"""
        if not self.sm.allowed(action):
            allowed = sorted(a for (st, a) in _TRANSITIONS
                             if st is self.sm.state)
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
        """重新读取会话状态并清理明显陈旧的运行状态。"""
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
                # --keep-running 的正常后台状态: 没有控制循环, 但仿真仍开着。
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
    def build(self, clear: bool = True, layout=None,
              phase: str = "stage1", parent: Optional[str] = None) -> dict:
        self.refresh()
        self._guard("build")
        self._ensure_stopped("build")
        self._require_no_live_lock("build")
        if phase == "stage3":
            return self._build_stage3(layout=layout, parent=parent)
        if phase not in ("stage1", "stage2", "diagnostic"):
            raise LifecycleError(f"build 不支持阶段: {phase!r}")
        from .scene import build_scene

        try:
            scene = build_scene(self.sim, clear=clear, layout=layout)
        except Exception as exc:
            self._fail(f"build 失败: {type(exc).__name__}: {exc}",
                       event="build_failed")
            raise
        self.scene = scene
        self.artifact = None
        self.last_validation = None
        self.has_run_since_build = False
        self.session.has_run_since_build = False
        self.session.artifact_id = None
        self.session.manifest_digest = None
        self.session.validated_at = sa.utc_now()
        self.session.run_id = None
        self.session.last_error = None
        self.session.built_phase = phase
        self.session.built_layout = None
        self.sm.transition("build")
        self._apply(LifecycleState.SCENE_VALIDATED, origin="BUILT",
                    event="built", layout=scene.layout.name,
                    checks=len(scene.build_report.get("checks", [])),
                    built_phase=phase)
        return self.summary()

    def _build_stage3(self, *, layout=None, parent: Optional[str] = None
                      ) -> dict:
        """从已验收 stage2 工件派生 stage3 副本并增加第二单元.

        不覆盖父工件; 加载父 scene.ttt 后在停止态增加 B 单元, 然后执行
        阶段三构建契约. 保存仍由显式 `save --phase stage3 --parent ...`
        完成 (步骤 1.2 生命周期语义不变).
        """
        if not parent:
            raise LifecycleError(
                "stage3 build 必须指定 --parent (已验收 stage2 工件)")
        parent_art = self.store.find(parent, phase="stage2")
        if parent_art.manifest.get("source_phase") != "stage2":
            raise LifecycleError(
                f"stage3 parent 必须是 stage2 工件, 当前为 "
                f"{parent_art.manifest.get('source_phase')!r}")
        if not parent_art.scene_path.exists():
            raise sa.ArtifactNotFoundError(str(parent_art.scene_path))

        from .layout import layout_from_dict
        from .scene import adopt_scene
        from stage3.layout import default_layout as s3_default_layout
        from stage3.scene import build_second_unit

        try:
            self.sim.loadScene(str(parent_art.scene_path))
            self._ensure_stopped("build")
            base_scene = adopt_scene(
                self.sim,
                layout=layout_from_dict(parent_art.manifest.get("layout") or {}))
            stage3_layout = layout if layout is not None else s3_default_layout()
            scene = build_second_unit(self.sim, base_scene,
                                      layout=stage3_layout)
        except Exception as exc:
            self._fail(f"stage3 build 失败: {type(exc).__name__}: {exc}",
                       event="build_failed", parent=parent_art.artifact_id)
            raise

        self.scene = scene
        self.artifact = None
        self.last_validation = None
        self.has_run_since_build = False
        self.session.has_run_since_build = False
        self.session.artifact_id = None
        self.session.manifest_digest = None
        self.session.validated_at = sa.utc_now()
        self.session.run_id = None
        self.session.last_error = None
        self.session.built_phase = "stage3"
        self.session.built_layout = stage3_layout.as_dict()
        self.sm.transition("build")
        self._apply(LifecycleState.SCENE_VALIDATED, origin="BUILT",
                    event="built_stage3", layout=scene.layout.name,
                    checks=len(scene.build_report.get("checks", [])),
                    parent=parent_art.artifact_id)
        result = self.summary()
        result["parent_artifact"] = parent_art.artifact_id
        return result

    # ── 动作: load ──────────────────────────────────────────
    def load(self, artifact_ref: str, phase: Optional[str] = None) -> dict:
        """在仿真停止态加载已有工件，不重建、不清场。

        解析链: ``artifact_ref="current"`` → ``<artifact-root>/<phase>/
        current.json`` → ``artifact_id`` → 工件目录的 ``scene.ttt``；
        ``phase`` 默认 ``stage1``。随后用 ``sim.loadScene()`` 从磁盘把该
        ``.ttt`` 载入 CoppeliaSim（替换当前场景），再由 ``adopt_scene()``
        按 alias 重新建立句柄。加载后状态为 ``SCENE_LOADED``，必须再调用
        ``validate()`` 才能 ``start``。
        """
        self.refresh()
        self._guard("load")
        self._ensure_stopped("load")
        self._require_no_live_lock("load")
        artifact = self.store.find(artifact_ref, phase=phase)
        schema = sa.validate_manifest_schema(
            artifact.manifest,
            sa.required_aliases_for_phase(
                artifact.manifest.get("source_phase")))
        if not sa.checks_ok(schema):
            failed = [c for c in schema if not c["ok"]]
            self._fail(f"manifest schema 校验失败: {failed}",
                       event="load_failed")
            raise ValidationFailedError(
                "manifest schema 校验失败",
                report={"checks": schema})
        if not artifact.scene_path.exists():
            self._fail(f"场景文件缺失: {artifact.scene_path}",
                       event="load_failed")
            raise sa.ArtifactNotFoundError(str(artifact.scene_path))
        expected = (artifact.manifest.get("artifact") or {}).get("sha256")
        actual = sa.sha256_file(artifact.scene_path)
        if expected != actual:
            self._fail(f"场景文件摘要不匹配: expected={expected} actual={actual}",
                       event="load_failed")
            raise ValidationFailedError(
                "场景文件摘要不匹配",
                report={"expected": expected, "actual": actual})

        from .layout import layout_from_dict
        from .scene import adopt_scene

        try:
            self.sim.loadScene(str(artifact.scene_path))
        except Exception as exc:
            self._fail(f"loadScene 失败: {exc}", event="load_failed")
            raise
        self._ensure_stopped("load")
        try:
            if artifact.manifest.get("source_phase") == "stage3":
                from stage3.layout import layout_from_dict as s3_layout
                from stage3.scene import adopt_stage3_scene
                scene = adopt_stage3_scene(
                    self.sim,
                    layout=s3_layout(artifact.manifest.get("layout") or {}))
            else:
                layout = layout_from_dict(artifact.manifest.get("layout") or {})
                scene = adopt_scene(self.sim, layout=layout)
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
        self.session.built_phase = None
        self.session.built_layout = None
        self.sm.transition("load")
        self._apply(LifecycleState.SCENE_LOADED, origin="ARTIFACT",
                    event="loaded", path=str(artifact.scene_path))
        return {**self.summary(), "requires_validation": True}

    # ── 动作: validate ──────────────────────────────────────
    def validate(self, artifact_ref: Optional[str] = None) -> dict:
        self.refresh()
        if self.session.scene_origin is None:
            raise InvalidTransitionError(
                "当前会话没有已加载场景; 先 build 或 load")
        try:
            self._ensure_scene()
        except Exception:
            # 句柄重建失败时保留 None, 由 validate_artifact 生成差异报告,
            # 不在这里中断 (只读校验必须能报告缺失 alias)。
            pass
        if self.artifact is None and artifact_ref is None:
            raise InvalidTransitionError(
                "当前场景由 build 生成, 没有 manifest; 先 save 或 load 工件")
        if artifact_ref is not None:
            ref_artifact = self.store.find(artifact_ref)
            if (self.artifact is None
                    or ref_artifact.artifact_id != self.artifact.artifact_id):
                raise InvalidTransitionError(
                    f"请先 load 工件 {ref_artifact.artifact_id}; "
                    f"当前={self.artifact.artifact_id if self.artifact else None}")
        self._guard("validate")
        self._ensure_stopped("validate")
        artifact = self.artifact
        result = sa.validate_artifact(self.sim, artifact, scene=self.scene)
        result = _merge_extra_validation(
            result, self._extra_validation(artifact, self.scene))
        result["artifact_id"] = artifact.artifact_id
        self.last_validation = result
        self._write_validation_report(result)
        if not result["ok"]:
            self._fail("只读校验失败", event="validation_failed",
                       differences=len(result.get("differences", [])))
            raise ValidationFailedError("只读校验失败", report=result)
        self.session.validated_at = result["validated_at"]
        self.session.manifest_digest = artifact.manifest_digest
        self.session.last_error = None
        self.sm.transition("validate")
        self._apply(LifecycleState.SCENE_VALIDATED, event="validated",
                    differences=0)
        return result

    def _extra_validation(self, artifact, scene) -> Optional[dict]:
        """阶段二/扩展契约校验; 阶段一工件返回 None."""
        validator = self.extra_validator or _default_extra_validator
        try:
            return validator(artifact, self.sim, scene)
        except Exception as exc:
            return {
                "ok": False,
                "checks": [{"name": "stage_extra_contract_error",
                            "ok": False,
                            "detail": f"{type(exc).__name__}: {exc}"}],
                "differences": [],
            }

    def _write_validation_report(self, result: dict) -> None:
        try:
            self.report_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            name = (result.get("artifact_id") or "scene").replace("/", "_")
            sa.write_json_atomic(
                self.report_dir / f"scene_validation_{name}_{stamp}.json",
                result)
        except OSError:
            pass

    # ── 动作: save ──────────────────────────────────────────
    def save(self, *, phase: str = "stage1", parent: Optional[str] = None,
             note: str = "", protected: Optional[bool] = None,
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
            failed = [c for c in report.get("checks", []) if not c["ok"]]
            self._fail(f"保存前契约校验失败: {failed}", event="save_failed")
            raise ValidationFailedError("保存前契约校验失败",
                                        report=report)

        phase = phase or "stage1"
        if phase not in sa.PHASES:
            raise sa.ArtifactPathError(f"未知阶段: {phase!r}")
        if protected is None:
            protected = phase in sa.PROTECTED_PHASES

        parent_info = None
        parent_art = None
        if parent:
            parent_phase = None
            if phase == "stage3" and parent == "current":
                parent_phase = "stage2"
            parent_art = self.store.find(parent, phase=parent_phase)
            parent_extra = parent_art.manifest.get("extra") or {}
            parent_stage2 = parent_extra.get("stage2") or {}
            parent_info = {
                "artifact_id": parent_art.artifact_id,
                "manifest_digest": parent_art.manifest_digest,
                "source_phase": parent_art.manifest.get("source_phase"),
                "stage2_contract_version": parent_stage2.get(
                    "contract_version"),
                "stage2_config_digest": (parent_stage2.get("config") or {}
                                         ).get("digest"),
            }
        if phase == "stage2" and parent_art is None:
            raise LifecycleError(
                "stage2 工件必须指定 --parent (从阶段一只读基线派生)")
        if (phase == "stage2" and parent_art is not None
                and parent_art.manifest.get("source_phase") != "stage1"):
            raise LifecycleError(
                f"stage2 parent 必须是 stage1 工件, 当前为 "
                f"{parent_art.manifest.get('source_phase')!r}")
        if phase == "stage3" and parent_art is None:
            raise LifecycleError(
                "stage3 工件必须指定 --parent (从已验收 stage2 工件派生)")
        if (phase == "stage3" and parent_art is not None
                and parent_art.manifest.get("source_phase") != "stage2"):
            raise LifecycleError(
                f"stage3 parent 必须是 stage2 工件, 当前为 "
                f"{parent_art.manifest.get('source_phase')!r}")

        draft = self.store.create(phase, self.scene.layout.name)
        provider = self.manifest_extra_provider or _default_manifest_extra
        try:
            manifest_extra = provider(phase, self.sim, self.scene,
                                      parent_info)
        except Exception as exc:
            self._fail(f"阶段扩展契约生成失败: {type(exc).__name__}: {exc}",
                       event="save_failed")
            raise
        manifest = sa.build_manifest(
            self.sim, self.scene, source_phase=phase,
            artifact_id=draft.artifact_id, protected=protected,
            parent_artifact=parent_info, actor=self.actor, note=note,
            manifest_extra=manifest_extra)
        if parent_art is not None:
            if phase == "stage2":
                from stage2.scene_contract import (
                    diff_stage2_manifest, sanitize_manifest_sections)
                manifest = sanitize_manifest_sections(manifest)
                manifest["parent_artifact"]["differences"] = \
                    diff_stage2_manifest(parent_art.manifest, manifest)
            elif phase == "stage3":
                from stage3.scene_contract import (
                    diff_stage3_manifest, sanitize_manifest_sections)
                manifest = sanitize_manifest_sections(manifest)
                manifest["parent_artifact"]["differences"] = \
                    diff_stage3_manifest(parent_art.manifest, manifest)
            else:
                manifest["parent_artifact"]["differences"] = \
                    sa.diff_manifest(parent_art.manifest, manifest)

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
        sa.write_json_atomic(draft.path / sa.MANIFEST_FILE, manifest)

        # 保存后立即重新加载验证, 防止“看似有效的半成品”进入 current 指针
        from .layout import layout_from_dict
        from .scene import adopt_scene

        try:
            self.sim.loadScene(str(partial))
            self._ensure_stopped("save")
            if phase == "stage3":
                from stage3.layout import layout_from_dict as s3_layout
                from stage3.scene import adopt_stage3_scene
                loaded_scene = adopt_stage3_scene(
                    self.sim, layout=s3_layout(manifest.get("layout") or {}))
            else:
                loaded_scene = adopt_scene(
                    self.sim,
                    layout=layout_from_dict(manifest.get("layout") or {}))
            loaded_scene.verify()
            # 校验针对尚未改名的 partial 文件; 通过后再原子改名为 scene.ttt。
            validation_manifest = json.loads(json.dumps(manifest))
            validation_manifest["artifact"]["scene_file"] = partial.name
            artifact = sa.Artifact(artifact_id=draft.artifact_id,
                                   path=draft.path,
                                   manifest=validation_manifest)
            result = sa.validate_artifact(
                self.sim, artifact, scene=loaded_scene)
            result = _merge_extra_validation(
                result, self._extra_validation(artifact, loaded_scene))
        except Exception as exc:
            self._fail(f"保存后重载校验异常: {exc}", event="save_failed")
            sa.write_json_atomic(draft.path / sa.VALIDATION_FILE, {
                "ok": False, "error": f"{type(exc).__name__}: {exc}",
                "validated_at": sa.utc_now(),
            })
            raise
        sa.write_json_atomic(draft.path / sa.VALIDATION_FILE, result)

        if not result["ok"]:
            self._fail("保存后重载校验失败", event="save_failed",
                       differences=len(result.get("differences", [])))
            raise ValidationFailedError("保存后重载校验失败", report=result)

        # 工件内生命周期记录 (受保护工件之后不再追加, 保持基线不可变)
        sa.append_jsonl(draft.path / sa.LIFECYCLE_FILE, {
            "ts": manifest["created_at"], "actor": self.actor,
            "event": "saved", "artifact_id": draft.artifact_id,
            "source_phase": phase, "protected": protected,
            "scene_sha256": manifest["artifact"]["sha256"],
            "scene_size": manifest["artifact"]["size"],
            "parent_artifact": parent_info,
        })
        sa.append_jsonl(draft.path / sa.LIFECYCLE_FILE, {
            "ts": result["validated_at"], "actor": self.actor,
            "event": "validated", "artifact_id": draft.artifact_id,
            "ok": True, "checks": len(result.get("checks", [])),
            "differences": 0,
        })

        final_scene = draft.path / sa.SCENE_FILE
        os.replace(partial, final_scene)
        final_artifact = sa.Artifact(artifact_id=draft.artifact_id,
                                     path=draft.path, manifest=manifest)
        self.store.write_current(final_artifact, phase=phase)
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
        self.session.built_phase = None
        self.session.built_layout = None
        self.sm.transition("save")
        self._apply(LifecycleState.SCENE_VALIDATED, origin="ARTIFACT",
                    event="saved", path=str(final_scene),
                    protected=protected,
                    scene_sha256=manifest["artifact"]["sha256"])
        return {**self.summary(), "validation": result}

    # ── 动作: start 前置 ────────────────────────────────────
    def prepare_start(self, target: str = "station") -> dict:
        self.refresh()
        self._guard("start")                  # 先确认状态允许, 再执行慢校验
        self._ensure_stopped("start")
        self._require_no_live_lock("start")

        # 先占运行锁, 再做只读校验: 校验期间其他进程不得 load/build/save。
        run_id = _new_run_id()
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
                if self.artifact is not None:
                    result = sa.validate_artifact(
                        self.sim, self.artifact, scene=None)
                    result = _merge_extra_validation(
                        result,
                        self._extra_validation(self.artifact, None))
                    self.last_validation = result
                    self._write_validation_report(result)
                    self._fail("start 前句柄/契约校验失败",
                               event="start_refused",
                               differences=len(result.get("differences", [])))
                    raise ValidationFailedError(
                        "start 前句柄/契约校验失败", report=result)
                raise InvalidTransitionError("没有已加载场景; 先 build 或 load")
            if self.artifact is not None:
                result = sa.validate_artifact(
                    self.sim, self.artifact, scene=self.scene)
                result = _merge_extra_validation(
                    result,
                    self._extra_validation(self.artifact, self.scene))
                self.last_validation = result
                self._write_validation_report(result)
                if not result["ok"]:
                    self._fail("start 前只读校验失败",
                               event="start_refused",
                               differences=len(result.get("differences", [])))
                    raise ValidationFailedError("start 前只读校验失败",
                                                report=result)
            elif not self.scene.verify().get("ok"):
                self._fail("start 前构建契约未通过", event="start_refused")
                raise ValidationFailedError("start 前构建契约未通过")
        except Exception:
            self.run_lock.release()
            raise

        self.session.run_id = run_id
        self.session.pid = os.getpid()
        self.sm.transition("start")
        self._apply(LifecycleState.SIMULATION_RUNNING, event="started",
                    target=target, run_id=run_id)
        return {**self.summary(), "run_id": run_id}

    def enter_background(self) -> dict:
        """`--keep-running`: 释放控制循环锁, 保留运行中的仿真。

        CLI 进程退出后不再有控制循环, 因此不能继续持有 run.lock; 会话状态
        保持 SIMULATION_RUNNING 并标记 background=True, 下一次 stop 负责
        停止并回收。
        """
        if self.sm.state is not LifecycleState.SIMULATION_RUNNING:
            raise InvalidTransitionError(
                f"只有运行中才能转入后台, 当前 {self.sm.state.value}")
        self.run_lock.release()
        self.session.run_id = None
        self.session.pid = None
        self.session.background = True
        self.session_store.save(self.session)
        self._event("background", reason="keep_running")
        return self.summary()

    def finish_run(self, outcome: str = "STOPPED", error: Optional[str] = None
                   ) -> dict:
        """运行线程结束后由持有者调用; 幂等。"""
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
            if self.last_validation is None \
                    and self.artifact.validation_path.exists():
                try:
                    saved = sa.read_json(self.artifact.validation_path)
                    self.last_validation = saved
                except Exception:
                    pass
        else:
            out["artifact"] = None
        out["last_validation"] = (
            {"ok": self.last_validation.get("ok"),
             "validated_at": self.last_validation.get("validated_at"),
             "differences": len(self.last_validation.get("differences", []))}
            if self.last_validation else None)
        out["start_in_progress"] = bool(
            info.get("lock_alive")
            and self.sm.state is not LifecycleState.SIMULATION_RUNNING)
        out["artifacts"] = self.store.list()
        return out

    def disconnect(self) -> dict:
        try:
            if self._sim_state() not in (None,
                                         int(getattr(self.sim,
                                                     "simulation_stopped", 0))):
                self.stop()
        finally:
            self.run_lock.release()
            self.sm.transition("disconnect")
            self.session.state = LifecycleState.DISCONNECTED
            self.session.run_id = None
            self.session.pid = None
            self.session_store.save(self.session)
            self._event("disconnected")
        return self.summary()


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _connect(args):
    from .run import connect
    return connect(args.host, args.port)


def _manager(args, client, sim, simIK) -> LifecycleManager:
    return LifecycleManager(
        sim, simIK, artifact_root=args.artifact_root,
        session_path=args.session, actor=args.actor,
        host=args.host, port=args.port, report_dir=args.out_dir)


def _run_start(args, manager: LifecycleManager) -> dict:
    """在 CLI 进程内启动并执行已校验场景; 返回运行报告.

    按工件 ``source_phase`` 显式路由: stage2 工件使用阶段二 runner,
    stage3 工件使用阶段三双工位 runner; 不允许阶段一 runner 静默运行
    更高阶段场景 (06-09 §8.6 / 06-12 §3.0).
    """
    from .run import setup_logger

    prepared = manager.prepare_start(target=args.target)
    phase = (manager.artifact.manifest.get("source_phase")
             if manager.artifact is not None else None)
    logger = setup_logger(args.log)
    if phase == "stage3":
        from stage3.run import run_loaded_scene_stage3 as runner
        scenario = getattr(args, "scenario", None) or "closed_loop"
        try:
            summary = runner(
                manager.sim, manager.simIK, manager.scene,
                scenario=scenario, target=scenario,
                fault=args.fault, max_sim_time=args.time,
                repeat=1, out_dir=Path(args.out_dir), logger=logger,
                keep_running=False, run_id=prepared["run_id"],
                artifact_id=manager.session.artifact_id)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            try:
                manager.stop()
            except Exception:
                pass
            manager.finish_run("FAILED", error)
            raise
        finally:
            try:
                manager.stop()
            except Exception:
                manager.finish_run("STOP_UNCERTAIN")
        summary["lifecycle"] = manager.finish_run(
            "DONE" if summary.get("all_ok") else "FAILED")
        return summary

    if phase == "stage2":
        from stage2.run import run_loaded_scene_stage2 as runner
    else:
        from .run import run_loaded_scene as runner
    try:
        summary = runner(
            manager.sim, manager.simIK, manager.scene,
            target=args.target, fault=args.fault, max_sim_time=args.time,
            repeat=args.repeat, out_dir=Path(args.out_dir), logger=logger,
            keep_running=args.keep_running,
            run_id=prepared["run_id"],
            artifact_id=manager.session.artifact_id)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        try:
            manager.stop()
        except Exception:
            pass
        manager.finish_run("FAILED", error)
        raise
    finally:
        if not args.keep_running:
            try:
                manager.stop()
            except Exception:
                manager.finish_run("STOP_UNCERTAIN")
    if args.keep_running:
        summary["lifecycle"] = manager.enter_background()
    else:
        summary["lifecycle"] = manager.finish_run(
            "DONE" if summary.get("all_ok") else "FAILED")
    return summary


def build_parser():
    import argparse

    ap = argparse.ArgumentParser(
        description="阶段一场景生命周期: 显式 build/save/load/validate/start/stop")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=23000)
    ap.add_argument("--artifact-root", default=None)
    ap.add_argument("--session", default=None)
    ap.add_argument("--actor", default="cli")
    ap.add_argument("--out-dir", default=str(
        sa.repo_root() / "docs" / "implementation" / "runs"))
    ap.add_argument("--log", default=cfg.DEFAULT_LOG_FILE)
    sub = ap.add_subparsers(dest="action", required=True)

    p = sub.add_parser("build", help="显式构建场景并执行构建契约校验")
    p.add_argument("--no-clear", action="store_true",
                   help="不清场 (只允许在空场景/自有场景上使用)")
    p.add_argument("--phase", default="stage1",
                   choices=("stage1", "stage3"),
                   help="stage1: 从空场景构建; stage3: 从 stage2 工件派生")
    p.add_argument("--parent", default=None,
                   help="stage3 构建时的父 stage2 工件 ID")

    p = sub.add_parser("save", help="停止态保存为新版本工件并重载校验")
    p.add_argument("--phase", default="stage1",
                   choices=list(sa.PHASES))
    p.add_argument("--parent", default=None,
                   help="父工件 ID (阶段二派生副本时使用)")
    p.add_argument("--note", default="")
    p.add_argument("--no-protect", action="store_true",
                   help="不设置只读保护 (仅诊断/临时工件)")
    p.add_argument("--allow-after-run", action="store_true",
                   help="允许保存运行后的非初始位姿 (不作为基线)")

    p = sub.add_parser("load", help="在停止态加载指定工件")
    p.add_argument("artifact",
                   help="工件 ID / 目录名 / 工件根内路径 / current")
    p.add_argument("--phase", default=None, choices=list(sa.PHASES),
                   help="current 指针所属命名空间 (默认 stage1)")

    p = sub.add_parser("validate", help="只读校验当前场景与 manifest")
    p.add_argument("artifact", nargs="?", default=None)

    p = sub.add_parser("start", help="启动当前已加载且校验通过的场景")
    p.add_argument("--target", choices=("station", "agv", "both"),
                   default="station",
                   help="阶段一/二目标; 阶段三可用 --scenario 选择场景")
    p.add_argument("--scenario", default=None,
                   help="阶段三场景: closed_loop/contention/blocked/"
                        "unstable_unload/route_baseline")
    try:
        from stage2.config import STAGE2_FAULT_CHOICES
        from stage3.config import STAGE3_FAULT_CHOICES
        fault_choices = list(dict.fromkeys(
            [*STAGE2_FAULT_CHOICES, *STAGE3_FAULT_CHOICES]))
    except Exception:
        fault_choices = list(cfg.FAULT_CHOICES)
    p.add_argument("--fault", choices=fault_choices,
                   default=cfg.FAULT_NONE)
    p.add_argument("--time", type=float, default=90.0)
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--keep-running", action="store_true")

    sub.add_parser("stop", help="停止仿真并确认停止态")
    sub.add_parser("status", help="输出生命周期状态")
    p = sub.add_parser("list", help="列出场景工件")
    p.add_argument("--phase", default=None, choices=list(sa.PHASES))
    return ap


def main(argv=None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.action == "list":
        store = sa.ArtifactStore(args.artifact_root)
        _print({"artifacts": store.list(args.phase)})
        return 0

    try:
        client, sim, simIK = _connect(args)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"连接失败: {exc}"},
                         ensure_ascii=False))
        return 4

    manager = _manager(args, client, sim, simIK)
    try:
        if args.action == "build":
            result = manager.build(
                clear=not args.no_clear, phase=getattr(args, "phase", "stage1"),
                parent=getattr(args, "parent", None))
        elif args.action == "save":
            result = manager.save(
                phase=args.phase, parent=args.parent, note=args.note,
                protected=not args.no_protect,
                allow_after_run=args.allow_after_run)
        elif args.action == "load":
            result = manager.load(args.artifact, phase=args.phase)
        elif args.action == "validate":
            result = manager.validate(args.artifact)
        elif args.action == "start":
            result = _run_start(args, manager)
        elif args.action == "stop":
            result = manager.stop()
        elif args.action == "status":
            result = manager.status()
        else:                                # pragma: no cover
            ap.error(f"未知动作: {args.action}")
            return 2
        _print({"ok": True, **result})
        return 0
    except ValidationFailedError as exc:
        _print({"ok": False, "error": str(exc), "code": exc.code,
                "report": exc.report,
                "details": getattr(exc, "details", {})})
        return 3
    except RunConflictError as exc:
        _print({"ok": False, "error": str(exc), "code": exc.code,
                "owner": exc.owner,
                "details": getattr(exc, "details", {})})
        return 2
    except (LifecycleError, sa.ArtifactError) as exc:
        _print({"ok": False, "error": str(exc),
                "code": getattr(exc, "code", "ERROR"),
                "details": getattr(exc, "details", {})})
        return 2
    except Exception as exc:
        _print({"ok": False, "error": f"{type(exc).__name__}: {exc}",
                "code": "ERROR"})
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
