"""material_manager.py — 物料生命周期状态机 (阶段一 §2.5)

纯 Python 逻辑层：管理物料记录的 UID、别名、状态机与事件历史。
CoppeliaSim 交互仅通过 `attach_sim()` / `detach_sim()` 等薄层隔离。

方案依据:
  docs/wiki/06-工业案例/06-05-柔性工业车间分阶段执行步骤.md §2.5
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ═══════════════════════════════════════════════════════════════
# 异常
# ═══════════════════════════════════════════════════════════════

class InvalidTransition(ValueError):
    """非法状态转换 — 例如 SPAWNED → HELD_BY_SUCTION (跳过吸附前置)"""


# ═══════════════════════════════════════════════════════════════
# 状态枚举 — §2.5 步骤5 (7 个状态, 由方案硬性规定)
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


# ═══════════════════════════════════════════════════════════════
# 合法转换表 — §2.5 步骤5「状态转换没有跳过吸附或释放」
#   主链路: SPAWNED → ON_CONVEYOR → READY_FOR_PICK → HELD_BY_SUCTION
#           → ON_TRAY → RELEASED
#   任意状态均可进入 FAULT; FAULT 只能自持 (需人工/重试逻辑显式复位)
# ═══════════════════════════════════════════════════════════════

_ALLOWED_TRANSITIONS: dict[MaterialState, frozenset] = {
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
# 单条物料记录 — §2.5 步骤4
# ═══════════════════════════════════════════════════════════════

@dataclass
class TransitionEvent:
    """一次状态转换 — 记录仿真时间、原因、前后状态 (§2.5 步骤6)"""
    sim_time: float
    reason: str
    from_state: MaterialState
    to_state: MaterialState


@dataclass
class MaterialRecord:
    """一个物料的完整生命周期记录。

    方案 §2.5 步骤4 要求记录: UID、句柄、当前父对象、生命周期状态、历史事件。
    """
    uid: int
    alias: str
    state: MaterialState = MaterialState.SPAWNED
    sim_handle: Optional[int] = None       # CoppeliaSim 对象句柄, None = 未绑定
    parent_handle: Optional[int] = None    # 当前父对象句柄 (吸附时改变)
    history: list[TransitionEvent] = field(default_factory=list)

    def transition_to(self, new_state: MaterialState, sim_time: float,
                      reason: str) -> None:
        """执行状态转换, 非法转换抛 InvalidTransition 并保持原状态。

        Args:
            new_state: 目标状态 (MaterialState 成员).
            sim_time:  当前仿真时间 (秒).
            reason:    转换原因 (写入历史).

        Raises:
            InvalidTransition: 目标状态非法或不在允许的转换集合中.
        """
        if not isinstance(new_state, MaterialState):
            raise InvalidTransition(f"未知状态: {new_state!r}")
        if new_state is not self.state:
            allowed = _ALLOWED_TRANSITIONS[self.state]
            if new_state not in allowed:
                raise InvalidTransition(
                    f"非法转换 {self.state.value} → {new_state.value} "
                    f"(允许: {sorted(s.value for s in allowed)})"
                )
        self.history.append(TransitionEvent(
            sim_time=sim_time, reason=reason,
            from_state=self.state, to_state=new_state,
        ))
        self.state = new_state

    def replay(self) -> list[tuple]:
        """还原完整历史 — [(sim_time, to_state, reason), ...]"""
        return [(e.sim_time, e.to_state, e.reason) for e in self.history]


# ═══════════════════════════════════════════════════════════════
# 集中管理器 — §2.5 步骤4/7
# ═══════════════════════════════════════════════════════════════

class MaterialManager:
    """按 UID 集中管理物料记录。

    §2.5 步骤7 要求「禁止控制器通过 alias 猜测目标物料, 必须引用当前任务的 UID」,
    因此查询唯一入口是 `by_uid()`; 别名仅用于诊断日志与唯一性校验。
    """

    def __init__(self) -> None:
        self._by_uid: dict[int, MaterialRecord] = {}
        self._by_alias: dict[str, int] = {}

    # ── 登记 / 移除 ──────────────────────────────────────────

    def register(self, uid: int, alias: str) -> MaterialRecord:
        """登记新物料; uid 与 alias 均必须唯一。

        Raises:
            ValueError: uid 已存在, 或 alias 已被占用.
        """
        if uid in self._by_uid:
            raise ValueError(f"UID {uid} 已注册")
        if alias in self._by_alias:
            raise ValueError(f"别名 {alias!r} 已存在")
        rec = MaterialRecord(uid=uid, alias=alias)
        self._by_uid[uid] = rec
        self._by_alias[alias] = uid
        return rec

    def remove(self, uid: int) -> None:
        """注销物料 (场景对象的删除由调用方负责)"""
        rec = self._by_uid.pop(uid, None)
        if rec is not None:
            self._by_alias.pop(rec.alias, None)

    # ── 查询 ────────────────────────────────────────────────

    def by_uid(self, uid: int) -> Optional[MaterialRecord]:
        """§2.5 步骤7: 唯一合法的目标物料查询入口"""
        return self._by_uid.get(uid)

    def all_records(self) -> list[MaterialRecord]:
        return list(self._by_uid.values())

    def count(self) -> int:
        return len(self._by_uid)

    # ── 场景句柄绑定 (实物层) ────────────────────────────────

    def attach_sim(self, uid: int, handle: int) -> None:
        """把物料记录绑定到 CoppeliaSim 句柄"""
        rec = self._by_uid.get(uid)
        if rec is None:
            raise InvalidTransition(f"UID {uid} 不存在")
        rec.sim_handle = handle

    def detach_sim(self, uid: int) -> None:
        rec = self._by_uid.get(uid)
        if rec is not None:
            rec.sim_handle = None

    def set_parent(self, uid: int, parent_handle: int) -> None:
        rec = self._by_uid.get(uid)
        if rec is None:
            raise InvalidTransition(f"UID {uid} 不存在")
        rec.parent_handle = parent_handle
