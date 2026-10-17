"""material_manager.py — 物料 UID 与生命周期状态机 (§2.5).

纯 Python 逻辑: 管理 UID、别名、7 状态机与事件历史。与 CoppeliaSim
的交互 (句柄、位姿、物理属性) 由调用层负责, 本模块不导入 sim。

状态链 (§2.5 步骤5):
    SPAWNED → ON_CONVEYOR → READY_FOR_PICK → HELD_BY_SUCTION
            → ON_TRAY → RELEASED
任意状态可进入 FAULT; FAULT 只能通过显式 reset_fault() 复位。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ═══════════════════════════════════════════════════════════════
# 异常
# ═══════════════════════════════════════════════════════════════

class MaterialError(Exception):
    """物料管理异常基类"""


class InvalidTransition(MaterialError, ValueError):
    """非法状态转换 — 例如跳过吸附直接进入 ON_TRAY"""


class IllegalStateError(MaterialError):
    """对不存在或不可操作的物料记录执行操作"""


# ═══════════════════════════════════════════════════════════════
# 状态
# ═══════════════════════════════════════════════════════════════

class MaterialState(str, Enum):
    SPAWNED         = "SPAWNED"
    ON_CONVEYOR     = "ON_CONVEYOR"
    READY_FOR_PICK  = "READY_FOR_PICK"
    HELD_BY_SUCTION = "HELD_BY_SUCTION"
    ON_TRAY         = "ON_TRAY"
    RELEASED        = "RELEASED"
    FAULT           = "FAULT"


ALL_STATES = tuple(MaterialState)

_ALLOWED: dict[MaterialState, frozenset] = {
    MaterialState.SPAWNED:         frozenset({MaterialState.ON_CONVEYOR,
                                              MaterialState.FAULT}),
    MaterialState.ON_CONVEYOR:     frozenset({MaterialState.READY_FOR_PICK,
                                              MaterialState.FAULT}),
    MaterialState.READY_FOR_PICK:  frozenset({MaterialState.HELD_BY_SUCTION,
                                              MaterialState.FAULT}),
    MaterialState.HELD_BY_SUCTION: frozenset({MaterialState.ON_TRAY,
                                              MaterialState.FAULT}),
    MaterialState.ON_TRAY:         frozenset({MaterialState.RELEASED,
                                              MaterialState.FAULT}),
    MaterialState.RELEASED:        frozenset({MaterialState.FAULT}),
    MaterialState.FAULT:           frozenset({MaterialState.FAULT}),
}


# ═══════════════════════════════════════════════════════════════
# 记录
# ═══════════════════════════════════════════════════════════════

@dataclass
class TransitionEvent:
    sim_time: float
    from_state: MaterialState
    to_state: MaterialState
    reason: str


@dataclass
class MaterialRecord:
    uid: int
    alias: str
    state: MaterialState = MaterialState.SPAWNED
    sim_handle: Optional[int] = None
    parent_handle: Optional[int] = None
    history: list[TransitionEvent] = field(default_factory=list)

    def transition_to(self, new_state: MaterialState, sim_time: float,
                      reason: str) -> None:
        if not isinstance(new_state, MaterialState):
            raise InvalidTransition(f"未知状态: {new_state!r}")
        if new_state is not self.state and new_state not in _ALLOWED[self.state]:
            allowed = sorted(s.value for s in _ALLOWED[self.state])
            raise InvalidTransition(
                f"非法转换 {self.state.value} → {new_state.value} "
                f"(允许: {allowed})")
        self.history.append(TransitionEvent(
            sim_time=float(sim_time),
            from_state=self.state,
            to_state=new_state,
            reason=str(reason),
        ))
        self.state = new_state

    def replay(self) -> list[tuple[float, MaterialState, str]]:
        return [(e.sim_time, e.to_state, e.reason) for e in self.history]


# ═══════════════════════════════════════════════════════════════
# 管理器
# ═══════════════════════════════════════════════════════════════

class MaterialManager:
    """按 UID 管理物料。任务只能通过 `by_uid()` 引用目标物料 (§2.5 步骤7)。"""

    def __init__(self) -> None:
        self._by_uid: dict[int, MaterialRecord] = {}
        self._by_alias: dict[str, int] = {}

    # ── 登记/移除 ────────────────────────────────────────────

    def register(self, uid: int, alias: str,
                 sim_handle: Optional[int] = None) -> MaterialRecord:
        if uid in self._by_uid:
            raise ValueError(f"UID {uid} 已注册")
        if alias in self._by_alias:
            raise ValueError(f"别名 {alias!r} 已存在")
        if sim_handle is not None and sim_handle < 0:
            raise ValueError(f"物料 {alias!r} 的句柄无效: {sim_handle}")
        rec = MaterialRecord(uid=uid, alias=alias, sim_handle=sim_handle)
        self._by_uid[uid] = rec
        self._by_alias[alias] = uid
        return rec

    def remove(self, uid: int) -> None:
        rec = self._by_uid.pop(uid, None)
        if rec is not None:
            self._by_alias.pop(rec.alias, None)

    # ── 查询 ────────────────────────────────────────────────

    def by_uid(self, uid: int) -> Optional[MaterialRecord]:
        return self._by_uid.get(uid)

    def by_alias(self, alias: str) -> Optional[MaterialRecord]:
        uid = self._by_alias.get(alias)
        return self._by_uid.get(uid) if uid is not None else None

    def all_records(self) -> list[MaterialRecord]:
        return list(self._by_uid.values())

    def handles(self) -> list[int]:
        return [r.sim_handle for r in self._by_uid.values()
                if r.sim_handle is not None]

    def count(self) -> int:
        return len(self._by_uid)

    # ── 状态转换 ────────────────────────────────────────────

    def _require(self, uid: int) -> MaterialRecord:
        rec = self._by_uid.get(uid)
        if rec is None:
            raise IllegalStateError(f"UID {uid} 不存在")
        return rec

    def transition(self, uid: int, new_state: MaterialState,
                   sim_time: float, reason: str) -> MaterialRecord:
        rec = self._require(uid)
        rec.transition_to(new_state, sim_time, reason)
        return rec

    def reset_fault(self, uid: int, sim_time: float, reason: str) -> MaterialRecord:
        """显式故障复位 — 只允许 FAULT → SPAWNED, 防止隐式恢复。"""
        rec = self._require(uid)
        if rec.state is not MaterialState.FAULT:
            raise InvalidTransition(
                f"UID {uid} 当前 {rec.state.value}, 不是 FAULT, 无法复位")
        rec.history.append(TransitionEvent(
            sim_time=float(sim_time), from_state=MaterialState.FAULT,
            to_state=MaterialState.SPAWNED, reason=str(reason)))
        rec.state = MaterialState.SPAWNED
        return rec

    # ── 场景绑定 ────────────────────────────────────────────

    def attach_sim(self, uid: int, handle: int) -> MaterialRecord:
        rec = self._require(uid)
        rec.sim_handle = int(handle)
        return rec

    def detach_sim(self, uid: int) -> None:
        rec = self._by_uid.get(uid)
        if rec is not None:
            rec.sim_handle = None

    def set_parent(self, uid: int, parent_handle: int) -> None:
        self._require(uid).parent_handle = int(parent_handle)
