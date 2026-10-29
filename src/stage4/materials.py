"""materials.py — 阶段四多类型物料谱系、区域占用与 E 岛配对 (06-16 §F4-3).

纯 Python 逻辑, 不导入 sim. 负责:
  - 物料类型 (RAW_A/RAW_B/SEMI_AB/SEMI_AC/SEMI_CE/RAW_D/PART_DE/FINAL);
  - UID 谱系 (父 UID、工序转换、来源与目标区域);
  - 区域容量与背压 (每条线/交接/缓冲/载台最多一个占位);
  - E 岛配对契约 (SEMI_CE + PART_DE → FINAL, 原子消费, 重复拒绝).

与阶段一 :class:`stage1.material_manager.MaterialManager` 的关系:
  - MaterialManager 继续管理 7 状态物理生命周期 (SPAWNED..RELEASED);
  - 本模块管理阶段四的类型/区域/谱系, 两者以 UID 关联;
  - 类型转换只在放置证据确认后提交, 不凭计划或位置推断.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Optional

from . import config as cfg


class ItemType(str, Enum):
    RAW_A = cfg.ITEM_TYPE_RAW_A
    RAW_B = cfg.ITEM_TYPE_RAW_B
    SEMI_AB = cfg.ITEM_TYPE_SEMI_AB
    SEMI_AC = cfg.ITEM_TYPE_SEMI_AC
    SEMI_CE = cfg.ITEM_TYPE_SEMI_CE
    RAW_D = cfg.ITEM_TYPE_RAW_D
    PART_DE = cfg.ITEM_TYPE_PART_DE
    FINAL = cfg.ITEM_TYPE_FINAL


class RecordState(str, Enum):
    CREATED = "CREATED"          # 刚生成, 未进入线
    IN_TRANSIT = "IN_TRANSIT"    # 在传送带/AGV 上
    READY = "READY"              # 在位等待下一道工序
    HELD = "HELD"                # 被机械臂吸附
    DELIVERED = "DELIVERED"      # 到达输出 (FINAL)
    CONSUMED = "CONSUMED"        # 被装配逻辑消费


class MaterialFlowError(RuntimeError):
    code = "STAGE4_MATERIAL"


@dataclass(frozen=True)
class FlowEvent:
    """阶段四事件 (可由报告重算指标)."""

    sim_time: float
    kind: str
    uid: Optional[int] = None
    item_type: str = ""
    zone: str = ""
    task_id: str = ""
    source: str = ""
    destination: str = ""
    device: str = ""
    evidence_id: str = ""
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "sim_time": round(float(self.sim_time), 4),
            "kind": self.kind,
            "uid": self.uid,
            "item_type": self.item_type,
            "zone": self.zone,
            "task_id": self.task_id,
            "source": self.source,
            "destination": self.destination,
            "device": self.device,
            "evidence_id": self.evidence_id,
            "detail": self.detail,
        }


@dataclass
class MaterialRecord4:
    uid: int
    item_type: ItemType
    alias: str
    state: RecordState = RecordState.CREATED
    zone: str = ""
    parents: tuple[int, ...] = ()
    source: str = ""
    destination: str = ""
    sim_handle: Optional[int] = None
    history: list[FlowEvent] = field(default_factory=list)
    process_stage: str = ""

    def as_dict(self) -> dict:
        return {
            "uid": int(self.uid),
            "alias": self.alias,
            "item_type": self.item_type.value,
            "state": self.state.value,
            "zone": self.zone,
            "parents": list(self.parents),
            "source": self.source,
            "destination": self.destination,
            "sim_handle": self.sim_handle,
            "process_stage": self.process_stage,
            "history": [event.as_dict() for event in self.history],
        }


@dataclass(frozen=True)
class JoinResult:
    ok: bool
    reason: str = ""
    final_uid: Optional[int] = None
    semi_uid: Optional[int] = None
    part_uid: Optional[int] = None
    pair_key: str = ""

    def as_dict(self) -> dict:
        return {
            "ok": bool(self.ok), "reason": self.reason,
            "final_uid": self.final_uid,
            "semi_uid": self.semi_uid, "part_uid": self.part_uid,
            "pair_key": self.pair_key,
        }


def _zone_capacity(zone: str) -> int:
    if zone in cfg.ZONE_CAPACITY:
        return int(cfg.ZONE_CAPACITY[zone])
    if zone.startswith("arm_") or zone.startswith("source_"):
        return 1
    return 1


class MaterialRegistry:
    """阶段四物料登记、区域容量与配对契约."""

    def __init__(self, capacity: Optional[dict] = None) -> None:
        self._by_uid: dict[int, MaterialRecord4] = {}
        self._by_alias: dict[str, int] = {}
        self.capacity = dict(cfg.ZONE_CAPACITY)
        if capacity:
            self.capacity.update({k: int(v) for k, v in capacity.items()})
        self.consumed_pairs: set[str] = set()
        self.join_history: list[dict] = []
        self.events: list[FlowEvent] = []

    # ── 登记/查询 ───────────────────────────────────────────

    def register(self, uid: int, item_type: ItemType, alias: str, *,
                 sim_handle: Optional[int] = None, source: str = "",
                 destination: str = "", zone: str = "source",
                 parents: Iterable[int] = (),
                 sim_time: float = 0.0) -> MaterialRecord4:
        if uid in self._by_uid:
            raise MaterialFlowError(f"UID {uid} 已登记")
        if alias in self._by_alias:
            raise MaterialFlowError(f"别名 {alias!r} 已存在")
        record = MaterialRecord4(
            uid=int(uid), item_type=ItemType(item_type), alias=str(alias),
            state=RecordState.CREATED, zone=str(zone),
            parents=tuple(int(v) for v in parents), source=str(source),
            destination=str(destination), sim_handle=sim_handle)
        self._by_uid[record.uid] = record
        self._by_alias[record.alias] = record.uid
        self._event("MATERIAL_CREATED", record, sim_time=sim_time, zone=zone,
                    detail={"source": source, "destination": destination})
        return record

    def by_uid(self, uid: Optional[int]) -> Optional[MaterialRecord4]:
        if uid is None:
            return None
        return self._by_uid.get(int(uid))

    def by_alias(self, alias: str) -> Optional[MaterialRecord4]:
        uid = self._by_alias.get(alias)
        return self._by_uid.get(uid) if uid is not None else None

    def all(self) -> list[MaterialRecord4]:
        return list(self._by_uid.values())

    def by_type(self, item_type: ItemType) -> list[MaterialRecord4]:
        item_type = ItemType(item_type)
        return [rec for rec in self._by_uid.values()
                if rec.item_type is item_type]

    def consumed(self) -> list[MaterialRecord4]:
        return [rec for rec in self._by_uid.values()
                if rec.state is RecordState.CONSUMED]

    def final_records(self) -> list[MaterialRecord4]:
        return [rec for rec in self._by_uid.values()
                if rec.item_type is ItemType.FINAL
                and rec.state is not RecordState.CONSUMED]

    # ── 区域容量 ────────────────────────────────────────────

    def capacity_of(self, zone: str) -> int:
        return int(self.capacity.get(zone, _zone_capacity(zone)))

    def occupants(self, zone: str) -> list[MaterialRecord4]:
        return [rec for rec in self._by_uid.values()
                if rec.zone == zone and rec.state is not RecordState.CONSUMED]

    def zone_occupant(self, zone: str) -> Optional[MaterialRecord4]:
        rows = self.occupants(zone)
        return rows[0] if rows else None

    def zone_free(self, zone: str) -> bool:
        return len(self.occupants(zone)) < self.capacity_of(zone)

    def occupy(self, uid: int, zone: str, sim_time: float, *,
               state: Optional[RecordState] = None, reason: str = "",
               task_id: str = "", device: str = "") -> MaterialRecord4:
        """把物料移动到新区域; 区域已满时拒绝并记录."""
        record = self._require(uid)
        if record.state is RecordState.CONSUMED:
            raise MaterialFlowError(f"UID {uid} 已消费, 不能再移动")
        if zone == record.zone:
            if state is not None:
                record.state = state
            return record
        others = [rec.uid for rec in self.occupants(zone) if rec.uid != uid]
        if len(others) >= self.capacity_of(zone):
            raise MaterialFlowError(
                f"区域 {zone!r} 已满 (占用: {others})")
        old_zone = record.zone
        record.zone = str(zone)
        if state is not None:
            record.state = state
        self._event("MATERIAL_MOVE", record, sim_time=sim_time, zone=zone,
                    task_id=task_id, device=device,
                    detail={"from": old_zone, "reason": reason})
        return record

    def set_state(self, uid: int, state: RecordState, sim_time: float, *,
                  reason: str = "", task_id: str = "") -> MaterialRecord4:
        record = self._require(uid)
        record.state = RecordState(state)
        self._event("MATERIAL_STATE", record, sim_time=sim_time,
                    zone=record.zone, task_id=task_id,
                    detail={"state": record.state.value, "reason": reason})
        return record

    # ── 类型/工序 ───────────────────────────────────────────

    def process(self, uid: int, island: str, sim_time: float, *,
                task_id: str = "") -> MaterialRecord4:
        """按 PROCESS_TRANSITIONS 提交工序转换; 未定义转换拒绝."""
        record = self._require(uid)
        key = (str(island), record.item_type.value)
        if key not in cfg.PROCESS_TRANSITIONS:
            raise MaterialFlowError(
                f"工位 {island} 未定义对 {record.item_type.value} 的工序")
        new_type = ItemType(cfg.PROCESS_TRANSITIONS[key])
        old_type = record.item_type
        record.item_type = new_type
        record.process_stage = str(island)
        self._event("MATERIAL_PROCESS", record, sim_time=sim_time,
                    task_id=task_id, device=str(island),
                    detail={"from": old_type.value, "to": new_type.value})
        return record

    # ── E 岛配对 ────────────────────────────────────────────

    def pair_candidates(self) -> tuple[Optional[MaterialRecord4],
                                       Optional[MaterialRecord4]]:
        semi = None
        part = None
        for rec in self._by_uid.values():
            if rec.state in (RecordState.CONSUMED,):
                continue
            if rec.item_type is ItemType.SEMI_CE and rec.zone == "buffer_e_c":
                semi = rec if semi is None else semi
            elif rec.item_type is ItemType.PART_DE and rec.zone == "buffer_e_d":
                part = rec if part is None else part
        return semi, part

    def commit_join(self, *, final_uid: int, sim_time: float,
                    final_alias: str, final_handle: Optional[int] = None,
                    final_zone: str = "output_e") -> JoinResult:
        """原子消费 C/D 两件输入并生成 FINAL; 缺件/重复/错型拒绝."""
        semi, part = self.pair_candidates()
        if semi is None or part is None:
            missing = []
            if semi is None:
                missing.append(ItemType.SEMI_CE.value)
            if part is None:
                missing.append(ItemType.PART_DE.value)
            return JoinResult(False, f"MISSING_INPUT: {missing}")
        pair_key = f"{semi.uid}+{part.uid}"
        if pair_key in self.consumed_pairs:
            return JoinResult(False, "DUPLICATE_PAIR", semi_uid=semi.uid,
                              part_uid=part.uid, pair_key=pair_key)
        if final_uid in self._by_uid:
            return JoinResult(False, f"FINAL_UID_EXISTS: {final_uid}",
                              semi_uid=semi.uid, part_uid=part.uid,
                              pair_key=pair_key)
        semi.state = RecordState.CONSUMED
        part.state = RecordState.CONSUMED
        self._event("MATERIAL_CONSUMED", semi, sim_time=sim_time,
                    task_id="join_e",
                    detail={"pair_key": pair_key, "final_uid": final_uid})
        self._event("MATERIAL_CONSUMED", part, sim_time=sim_time,
                    task_id="join_e",
                    detail={"pair_key": pair_key, "final_uid": final_uid})
        final = self.register(
            final_uid, ItemType.FINAL, final_alias, sim_handle=final_handle,
            source="E", destination="output_e", zone=final_zone,
            parents=(semi.uid, part.uid), sim_time=sim_time)
        final.state = RecordState.DELIVERED
        self.consumed_pairs.add(pair_key)
        row = {
            "sim_time": round(float(sim_time), 4),
            "pair_key": pair_key,
            "semi_uid": semi.uid, "part_uid": part.uid,
            "final_uid": final_uid,
        }
        self.join_history.append(row)
        self._event("JOIN_COMMIT", final, sim_time=sim_time,
                    task_id="join_e", evidence_id=pair_key, detail=row)
        return JoinResult(True, "JOIN_OK", final_uid=final_uid,
                          semi_uid=semi.uid, part_uid=part.uid,
                          pair_key=pair_key)

    # ── 谱系/统计 ───────────────────────────────────────────

    def genealogy(self, uid: int) -> dict:
        record = self._require(uid)
        parents = [self._by_uid[parent].as_dict()
                   for parent in record.parents if parent in self._by_uid]
        return {
            "uid": record.uid,
            "item_type": record.item_type.value,
            "parents": parents,
        }

    def wip(self) -> dict:
        rows = [rec for rec in self._by_uid.values()
                if rec.state not in (RecordState.CONSUMED,)
                and rec.item_type is not ItemType.FINAL]
        by_type: dict[str, int] = {}
        for rec in rows:
            by_type[rec.item_type.value] = by_type.get(
                rec.item_type.value, 0) + 1
        return {"total": len(rows), "by_type": by_type,
                "zones": {rec.uid: rec.zone for rec in rows}}

    def snapshot(self) -> dict:
        return {
            "model_version": cfg.STAGE4_MATERIAL_MODEL_VERSION,
            "materials": [rec.as_dict() for rec in self.all()],
            "join_history": list(self.join_history),
            "consumed_pairs": sorted(self.consumed_pairs),
            "wip": self.wip(),
        }

    # ── 内部 ────────────────────────────────────────────────

    def _require(self, uid: int) -> MaterialRecord4:
        record = self._by_uid.get(int(uid))
        if record is None:
            raise MaterialFlowError(f"UID {uid} 未登记")
        return record

    def _event(self, kind: str, record: MaterialRecord4, *,
               sim_time: float = 0.0, zone: str = "", task_id: str = "",
               device: str = "", evidence_id: str = "",
               detail: Optional[dict] = None) -> None:
        event = FlowEvent(
            sim_time=float(sim_time), kind=kind, uid=record.uid,
            item_type=record.item_type.value, zone=zone or record.zone,
            task_id=task_id, source=record.source,
            destination=record.destination, device=device,
            evidence_id=evidence_id, detail=detail or {})
        record.history.append(event)
        self.events.append(event)


def material_type_color(item_type: ItemType) -> tuple[float, float, float]:
    return tuple(cfg.ITEM_COLORS[ItemType(item_type).value])
