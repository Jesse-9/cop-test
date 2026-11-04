"""scheduling.py — 阶段 4 纯调度策略: 候选筛选、排序与设备选择 (06-22).

本模块只做**纯函数决策**: 消费协调器整理的只读快照, 输出可 JSON 序列化的
派工决策. 不导入 CoppeliaSim、场景句柄、执行适配器或
:mod:`flexible_factory.application.coordinator`; 不申请资源、不改工序状态、
不推进仿真.

筛选顺序 (06-22 §能力匹配模型, 由测试固定):

  1. recipe allowlist — 设备必须在该工序的 ``candidate_device_ids`` 中;
  2. 静态能力 — 执行器类型一致且设备能力包含工序要求;
  3. 场景绑定 — 设备有绑定, 工序资源与物料区域都在绑定覆盖内;
  4. 设备占用 — 活动执行/资源 owner 只读视图; 有替代空闲设备时优先选它,
     全部忙时仍选择排序第一的可用候选, 由协调器排队等待;
  5. 工序/物料前提 — 输入 UID 在期望区域、类型匹配、目标区域容量允许;
  6. 物理可行性 — 非基线设备只有在 ``verified_pairs`` 中登记的
     (device, operation) 组合才可被选择; 未验收组合显式拒绝.

资源 owner 冲突、AGV 停靠状态属于执行前条件, 记录在 ``conditions`` 中但不
阻断选择: 任务必须进入队列, 现有 ``_poll_agv`` 才会据此调度车辆, 否则
"B/D 装载任务需要 AGV 先到位" 会死锁. 真正拿锁仍由协调器
``ResourceTable.acquire_all()`` 全有或全无地完成.

排序: 业务/配套优先级 (当前为 operation priority) → 等待时间降序 →
ready 时间 → 稳定 operation instance ID. 候选设备按 recipe 声明顺序遍历,
不依赖 dict/set 遍历顺序.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional

from ..spec import SCHEDULER_POLICY_VERSION


class SchedulingError(RuntimeError):
    code = "V1_SCHEDULING"


# 本阶段没有权威数据源、因而**不参与筛选**的维度. 每条 decision 显式列出,
# 防止把“未建模”静默当作“已通过”. 设备健康/禁用状态、工具载荷和姿态包络
# 只有未来接入权威目录后才可进入候选判断.
NOT_MODELED_DIMENSIONS = (
    "device_health", "tool_payload", "pose_envelope",
)


# ═══════════════════════════════════════════════════════════════
# 输入快照 (纯数据, 不含句柄)
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class DeviceView:
    """目录 + 静态绑定投影; ``bound=False`` 表示没有有效场景绑定."""

    device_id: str
    kind: str
    capabilities: tuple[str, ...] = ()
    resources: tuple[str, ...] = ()
    zones: tuple[str, ...] = ()
    default_line_id: str = ""
    bound: bool = True

    def as_dict(self) -> dict:
        return {
            "device_id": self.device_id,
            "kind": self.kind,
            "capabilities": list(self.capabilities),
            "resources": list(self.resources),
            "zones": list(self.zones),
            "default_line_id": self.default_line_id,
            "bound": bool(self.bound),
        }


@dataclass(frozen=True)
class MaterialView:
    """工序输入 UID 的只读物料状态; 只保留筛选需要的字段."""

    uid: int
    item_type: str
    zone: str

    def as_dict(self) -> dict:
        return {"uid": int(self.uid), "item_type": self.item_type,
                "zone": self.zone}


@dataclass(frozen=True)
class OperationView:
    """一个 ready 工序的调度输入."""

    instance_id: str
    template_id: str
    kind: str
    uid: int
    executor_kind: str
    capability: str
    candidate_device_ids: tuple[str, ...]
    task_id: str = ""
    run_id: str = ""
    entry_id: str = ""
    priority: int = 1
    ready_sim_time: float = 0.0
    input_zones: tuple[str, ...] = ()
    input_item_types: tuple[str, ...] = ()
    destination_zone: str = ""
    source_kind: str = ""
    sink_kind: str = ""
    dock: str = ""
    route_origin_dock: str = ""
    route_destination_dock: str = ""
    required_resources: tuple[str, ...] = ()
    selected_route: str = ""

    @property
    def baseline_device_id(self) -> str:
        """recipe 声明的首个候选: 阶段 2/3 已验收的固定设备分配."""
        return self.candidate_device_ids[0] if self.candidate_device_ids else ""

    @property
    def binding_zones(self) -> tuple[str, ...]:
        zones = list(self.input_zones)
        if self.destination_zone:
            zones.append(self.destination_zone)
        return tuple(dict.fromkeys(zone for zone in zones if zone))

    def as_dict(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "task_id": self.task_id,
            "template_id": self.template_id,
            "kind": self.kind,
            "uid": int(self.uid),
            "executor_kind": self.executor_kind,
            "capability": self.capability,
            "candidate_device_ids": list(self.candidate_device_ids),
            "baseline_device_id": self.baseline_device_id,
            "run_id": self.run_id,
            "entry_id": self.entry_id,
            "priority": int(self.priority),
            "ready_sim_time": round(float(self.ready_sim_time), 4),
            "input_zones": list(self.input_zones),
            "input_item_types": list(self.input_item_types),
            "destination_zone": self.destination_zone,
            "source_kind": self.source_kind,
            "sink_kind": self.sink_kind,
            "dock": self.dock,
            "route_origin_dock": self.route_origin_dock,
            "route_destination_dock": self.route_destination_dock,
            "required_resources": list(self.required_resources),
            "selected_route": self.selected_route,
        }


@dataclass(frozen=True)
class SchedulingSnapshot:
    """一次调度请求的只读输入.

    ``resource_owners``/``busy_devices`` 是当前活动执行与资源表的只读投影;
    策略不写回. ``free_zones`` 只包含本次 ready 工序关心的目标区域.
    """

    sim_time: float
    operations: tuple[OperationView, ...]
    devices: tuple[DeviceView, ...]
    materials: tuple[MaterialView, ...] = ()
    free_zones: Mapping[str, bool] = field(default_factory=dict)
    resource_owners: Mapping[str, str] = field(default_factory=dict)
    busy_devices: Mapping[str, str] = field(default_factory=dict)
    verified_pairs: frozenset[tuple[str, str]] = frozenset()
    agv_at_dock: str = ""
    agv_dock_settled: bool = True

    def material(self, uid: int) -> Optional[MaterialView]:
        for row in self.materials:
            if int(row.uid) == int(uid):
                return row
        return None

    def as_dict(self) -> dict:
        return {
            "sim_time": round(float(self.sim_time), 4),
            "operations": [row.as_dict() for row in self.operations],
            "devices": [row.as_dict() for row in self.devices],
            "materials": [row.as_dict() for row in self.materials],
            "free_zones": {zone: bool(value) for zone, value
                           in sorted(self.free_zones.items())},
            "resource_owners": {name: owner for name, owner
                                in sorted(self.resource_owners.items())},
            "busy_devices": {name: owner for name, owner
                             in sorted(self.busy_devices.items())},
            "verified_pairs": [list(pair) for pair in
                               sorted(self.verified_pairs)],
            "agv_at_dock": self.agv_at_dock,
            "agv_dock_settled": bool(self.agv_dock_settled),
        }


# ═══════════════════════════════════════════════════════════════
# 输出决策
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class CandidateDecision:
    """一个候选设备的判断结果.

    ``reasons`` 是阻断性拒绝原因 (该设备当前不可用); ``conditions`` 是已
    记录但不阻断选择的执行前条件 (设备忙、资源 owner、AGV 停靠等).
    """

    device_id: str
    accepted: bool
    reasons: tuple[str, ...] = ()
    conditions: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "device_id": self.device_id,
            "accepted": bool(self.accepted),
            "reasons": list(self.reasons),
            "conditions": list(self.conditions),
        }


@dataclass(frozen=True)
class DispatchDecision:
    """一个 ready 工序的完整派工决策 (可 JSON 序列化)."""

    operation_instance_id: str
    task_id: str
    template_id: str
    kind: str
    uid: int
    candidates: tuple[CandidateDecision, ...]
    selected_device_id: str = ""
    defer_reason: str = ""
    run_id: str = ""
    entry_id: str = ""
    priority: int = 1
    ready_sim_time: float = 0.0
    sim_time: float = 0.0
    policy_version: str = SCHEDULER_POLICY_VERSION

    @property
    def dispatched(self) -> bool:
        return bool(self.selected_device_id)

    def as_dict(self) -> dict:
        return {
            "operation_instance_id": self.operation_instance_id,
            "task_id": self.task_id,
            "template_id": self.template_id,
            "kind": self.kind,
            "uid": int(self.uid),
            "run_id": self.run_id,
            "entry_id": self.entry_id,
            "priority": int(self.priority),
            "ready_sim_time": round(float(self.ready_sim_time), 4),
            "sim_time": round(float(self.sim_time), 4),
            "candidates": [row.as_dict() for row in self.candidates],
            "selected_device_id": self.selected_device_id,
            "defer_reason": self.defer_reason,
            "policy_version": self.policy_version,
            "not_modeled": list(NOT_MODELED_DIMENSIONS),
        }


@dataclass(frozen=True)
class SchedulingResult:
    """一次 ready 集合的调度结果; decisions 保持策略排序."""

    sim_time: float
    decisions: tuple[DispatchDecision, ...]
    policy_version: str = SCHEDULER_POLICY_VERSION

    def as_dict(self) -> dict:
        return {
            "sim_time": round(float(self.sim_time), 4),
            "policy_version": self.policy_version,
            "decisions": [row.as_dict() for row in self.decisions],
        }


# ═══════════════════════════════════════════════════════════════
# 纯策略
# ═══════════════════════════════════════════════════════════════

def _dedupe_reasons(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _evaluate_candidate(
        operation: OperationView, device: Optional[DeviceView],
        snapshot: SchedulingSnapshot,
        booked_devices: Mapping[str, str],
        booked_resources: Mapping[str, str]) -> CandidateDecision:
    """按固定顺序检查一个候选设备; 不修改任何输入."""
    if device is None:
        return CandidateDecision(
            device_id="", accepted=False,
            reasons=("UNKNOWN_DEVICE",))

    reasons: list[str] = []
    conditions: list[str] = []

    # 1. recipe allowlist (候选列表由调用方按 recipe 声明构造; 仍显式核对).
    if device.device_id not in operation.candidate_device_ids:
        reasons.append("NOT_IN_RECIPE_ALLOWLIST")

    # 2. 静态执行器类型与能力.
    if device.kind != operation.executor_kind:
        reasons.append(
            f"EXECUTOR_KIND_MISMATCH: {device.kind} != "
            f"{operation.executor_kind}")
    if operation.capability not in device.capabilities:
        reasons.append(f"CAPABILITY_MISMATCH: {operation.capability}")

    # 3. 场景绑定: 设备必须已绑定, 工序资源/区域在覆盖范围内.
    if not device.bound:
        reasons.append("DEVICE_NOT_BOUND")
    for resource in operation.required_resources:
        if resource not in device.resources:
            reasons.append(f"RESOURCE_NOT_BOUND: {resource}")
    for zone in operation.binding_zones:
        if zone not in device.zones:
            reasons.append(f"ZONE_NOT_BOUND: {zone}")

    # 4. 设备占用与资源 owner: 只读记录; 有替代空闲设备时由选择阶段优先.
    device_owner = (booked_devices.get(device.device_id)
                    or snapshot.busy_devices.get(device.device_id))
    if device_owner:
        conditions.append(f"DEVICE_BUSY: {device_owner}")
    for resource in operation.required_resources:
        holder = (booked_resources.get(resource)
                  or snapshot.resource_owners.get(resource))
        if holder:
            conditions.append(f"RESOURCE_BUSY: {resource} held by {holder}")

    # 5. 工序/物料前提.
    material = snapshot.material(operation.uid)
    if material is None:
        reasons.append("MATERIAL_MISSING")
    else:
        if (operation.input_item_types
                and material.item_type not in operation.input_item_types):
            reasons.append(
                f"ITEM_TYPE_MISMATCH: {material.item_type} not in "
                f"{list(operation.input_item_types)}")
        if (operation.input_zones
                and material.zone != operation.input_zones[0]):
            reasons.append(
                f"ZONE_MISMATCH: uid={operation.uid} at {material.zone} "
                f"expected {operation.input_zones[0]}")
        destination = operation.destination_zone
        # 原地/同一区域输入输出 (AGV 运输) 不重复占位, 不做容量判断.
        if (destination and destination != material.zone
                and not snapshot.free_zones.get(destination, True)):
            reasons.append(f"DEST_ZONE_OCCUPIED: {destination}")

    # 6. 物理可行性: 只有基线设备或已验收的 (设备, 工序) 组合可选.
    pair = (device.device_id, operation.template_id)
    if (device.device_id != operation.baseline_device_id
            and pair not in snapshot.verified_pairs):
        reasons.append("DYNAMIC_GEOMETRY_NOT_VERIFIED")

    # AGV 停靠属于执行前条件: 入队后由协调器等待并驱动空载调机.
    if operation.executor_kind == "agv":
        expected = operation.route_origin_dock
        if not expected:
            conditions.append("AGV_ROUTE_MISSING_ORIGIN")
        elif snapshot.agv_at_dock != expected:
            conditions.append(
                f"AGV_NOT_AT_DOCK: expected {expected} "
                f"actual {snapshot.agv_at_dock}")
        elif not snapshot.agv_dock_settled:
            conditions.append("AGV_DOCK_NOT_SETTLED")
    elif operation.source_kind == "agv" or operation.sink_kind == "agv":
        expected = operation.dock
        if expected and snapshot.agv_at_dock != expected:
            conditions.append(
                f"AGV_NOT_AT_DOCK: expected {expected} "
                f"actual {snapshot.agv_at_dock}")
        elif expected and not snapshot.agv_dock_settled:
            conditions.append("AGV_DOCK_NOT_SETTLED")

    return CandidateDecision(
        device_id=device.device_id, accepted=not reasons,
        reasons=_dedupe_reasons(reasons),
        conditions=_dedupe_reasons(conditions))


def _order_key(operation: OperationView, sim_time: float) -> tuple:
    """业务优先级 → 等待时间降序 → ready 时间 → 稳定实例 ID."""
    waited = max(0.0, float(sim_time) - float(operation.ready_sim_time))
    return (int(operation.priority), -round(waited, 6),
            round(float(operation.ready_sim_time), 6),
            operation.instance_id)


def _select_candidate(
        candidates: tuple[CandidateDecision, ...]
) -> Optional[CandidateDecision]:
    feasible = [row for row in candidates if row.accepted]
    if not feasible:
        return None
    idle = [
        row for row in feasible
        if not any(reason.startswith("DEVICE_BUSY")
                   for reason in row.conditions)]
    return (idle or feasible)[0]


def plan_ready(snapshot: SchedulingSnapshot) -> SchedulingResult:
    """对快照中的 ready 工序给出确定性派工决策.

    同一批次内先选中的设备/资源会作为预留写入本次计算的本地视图, 使后续
    工序优先选择仍空闲的替代设备; 但 ``DEVICE_BUSY`` 不阻断最终选择, 工序
    仍会带着等待条件交给协调器排队.
    """
    if len({row.device_id for row in snapshot.devices}) != len(snapshot.devices):
        raise SchedulingError("调度快照包含重复设备 ID")
    if len({row.instance_id for row in snapshot.operations}) != \
            len(snapshot.operations):
        raise SchedulingError("调度快照包含重复工序实例 ID")

    devices = {row.device_id: row for row in snapshot.devices}
    booked_devices = dict(snapshot.busy_devices)
    booked_resources = dict(snapshot.resource_owners)
    decisions: list[DispatchDecision] = []

    for operation in sorted(
            snapshot.operations,
            key=lambda row: _order_key(row, snapshot.sim_time)):
        candidates: list[CandidateDecision] = []
        if not operation.candidate_device_ids:
            candidates.append(CandidateDecision(
                device_id="", accepted=False,
                reasons=("NO_CANDIDATE_DEVICE",)))
        else:
            for device_id in operation.candidate_device_ids:
                candidates.append(_evaluate_candidate(
                    operation, devices.get(device_id), snapshot,
                    booked_devices, booked_resources))
        selected = _select_candidate(tuple(candidates))
        if selected is not None:
            booked_devices.setdefault(
                selected.device_id,
                f"scheduler/{operation.instance_id}")
            for resource in operation.required_resources:
                booked_resources.setdefault(
                    resource, f"scheduler/{operation.instance_id}")
        decisions.append(DispatchDecision(
            operation_instance_id=operation.instance_id,
            task_id=operation.task_id,
            template_id=operation.template_id,
            kind=operation.kind,
            uid=int(operation.uid),
            candidates=tuple(candidates),
            selected_device_id=("" if selected is None
                                else selected.device_id),
            defer_reason=("" if selected is not None
                          else "NO_FEASIBLE_CANDIDATE"),
            run_id=operation.run_id,
            entry_id=operation.entry_id,
            priority=int(operation.priority),
            ready_sim_time=float(operation.ready_sim_time),
            sim_time=float(snapshot.sim_time)))

    return SchedulingResult(
        sim_time=float(snapshot.sim_time), decisions=tuple(decisions))


__all__ = [
    "CandidateDecision",
    "DeviceView",
    "DispatchDecision",
    "MaterialView",
    "NOT_MODELED_DIMENSIONS",
    "OperationView",
    "SCHEDULER_POLICY_VERSION",
    "SchedulingError",
    "SchedulingResult",
    "SchedulingSnapshot",
    "plan_ready",
]
