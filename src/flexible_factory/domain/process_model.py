"""process_model.py — v1 UFLOW 静态工艺模型与目录校验 (06-19 §1A).

本模块只保存**静态工艺意图**: 配方、工序模板、路线变体、工步模板引用、
设备/能力/区域目录和完整性校验. 不导入 CoppeliaSim, 不创建任务, 不持有
运行时句柄或单次 UID.

术语边界 (06-18 §1):
  - 配方 Recipe: 某产品可复用的工序模板、路线选择规则和订单入口;
  - 工序 Operation: 可调度、可验收的工作模块;
  - 工步 Work Step: 工序内部由设备执行端推进的步骤;
  - 设备 Device: 提供能力的资源; 逻辑配对处理器不是物理设备.

阶段 0-1 只建立静态目录, 不实现工序实例化/依赖推进 (阶段三) 和设备
执行适配器 (阶段二). `default_process.py` 提供当前唯一默认配方;
`coordinator.py` 从该目录读取任务字段, 执行仍调用已验收控制器.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .materials import ItemType


class ProcessModelError(RuntimeError):
    code = "STAGE4_PROCESS_MODEL"


# ═══════════════════════════════════════════════════════════════
# 工步模板与完成验证器登记
# ═══════════════════════════════════════════════════════════════
# 配置只能引用**已存在的执行路径标识**, 不允许携带任意 Python 代码.
# 阶段 0-1 不新增执行器; 这些 ID 用于把工序模板绑定到已验收实现.

WORK_STEP_TEMPLATES = {
    "conveyor_pickup": "从传送带停止区按 UID 取料 "
                       "(Stage4UnitController source=conveyor)",
    "arm_pick_place": "机械臂吸附/转运/放置 "
                      "(Stage4UnitController 状态机与 IK/碰撞检查)",
    "agv_route": "AGV 轮速路线 (AGVRouteController4)",
    "logical_join": "E 岛逻辑配对 (MaterialRegistry.commit_join)",
}

COMPLETION_VERIFIERS = {
    "unit_handoff_verified": "协调器校验 UID/类型/位姿/物理属性后提交 "
                             "(Stage4Coordinator._verify_unit_task)",
    "agv_route_verified": "协调器校验停靠/航向/载荷滑移 "
                          "(Stage4Coordinator._verify_route)",
    "join_pair_committed": "MaterialRegistry 原子消费两条父 UID 并生成 "
                           "FINAL (commit_join)",
}

EXECUTOR_KINDS = ("arm", "agv", "logic")
SOURCE_KINDS = {
    "arm": ("conveyor", "agv", "station"),
    "agv": ("agv",),
    "logic": ("none",),
}
SINK_KINDS = {
    "arm": ("station", "agv", "conveyor"),
    "agv": ("agv",),
    "logic": ("none",),
}
RESOURCE_POLICIES = ("fixed", "agv_route", "logical_join")
PREDECESSOR_POLICIES = ("all", "any")


# ═══════════════════════════════════════════════════════════════
# 不可变数据模型
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class MaterialPort:
    """工序的输入/输出物料端口: 稳定区域 ID + 物料类型."""

    zone: str
    item_type: ItemType

    def as_dict(self) -> dict:
        return {"zone": self.zone, "item_type": self.item_type.value}


@dataclass(frozen=True)
class WorkStepTemplateRef:
    """对已存在工步执行路径的引用; 不携带可执行代码."""

    template_id: str

    def as_dict(self) -> dict:
        return {"template_id": self.template_id}


@dataclass(frozen=True)
class OperationTemplate:
    """一个可调度工序模板.

    ``kind`` 是阶段四旧 task kind 的兼容标识, 只用于报告/证据映射;
    调度与执行读取本模板, 不再读取 coordinator 中的固定映射表.
    """

    id: str
    kind: str
    semantics: str
    executor_kind: str                 # arm | agv | logic
    capability: str
    candidate_devices: tuple[str, ...]
    inputs: tuple[MaterialPort, ...]
    output: Optional[MaterialPort]
    source_kind: str                   # conveyor | agv | station | none
    sink_kind: str                     # station | agv | conveyor | none
    resources: tuple[str, ...]
    resource_policy: str
    work_steps: tuple[WorkStepTemplateRef, ...]
    completion_verifier: str
    priority: int = 1
    depends_on: tuple[str, ...] = ()
    predecessor_policy: str = "all"    # all = AND, any = OR
    successors: tuple[str, ...] = ()
    dock: str = ""
    route_origin_dock: str = ""
    route_destination_dock: str = ""

    @property
    def input_types(self) -> tuple[ItemType, ...]:
        return tuple(port.item_type for port in self.inputs)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "semantics": self.semantics,
            "executor_kind": self.executor_kind,
            "capability": self.capability,
            "candidate_devices": list(self.candidate_devices),
            "inputs": [port.as_dict() for port in self.inputs],
            "output": None if self.output is None else self.output.as_dict(),
            "source_kind": self.source_kind,
            "sink_kind": self.sink_kind,
            "resources": list(self.resources),
            "resource_policy": self.resource_policy,
            "work_steps": [step.as_dict() for step in self.work_steps],
            "completion_verifier": self.completion_verifier,
            "priority": int(self.priority),
            "depends_on": list(self.depends_on),
            "predecessor_policy": self.predecessor_policy,
            "successors": list(self.successors),
            "dock": self.dock,
            "route_origin_dock": self.route_origin_dock,
            "route_destination_dock": self.route_destination_dock,
        }


@dataclass(frozen=True)
class RouteVariant:
    """命名路线的工序选择; 同组路线互斥, 不是并行分支."""

    id: str
    group: str
    semantics: str
    operation_ids: tuple[str, ...]
    entry_zone: str
    exit_zone: str

    @property
    def terminal_operation_id(self) -> str:
        return self.operation_ids[-1] if self.operation_ids else ""

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "group": self.group,
            "semantics": self.semantics,
            "operation_ids": list(self.operation_ids),
            "entry_zone": self.entry_zone,
            "exit_zone": self.exit_zone,
            "terminal_operation_id": self.terminal_operation_id,
        }


@dataclass(frozen=True)
class OrderEntry:
    """配方订单入口: 来源线、来料类型、初始工序与固定路线选择.

    ``route_variant_id`` 是有限批次按入口固定选择的路线的唯一标识; 留空表示
    由运行时在唯一同组路线中推断 (v1 默认配方显式填写). 它不是动态选路,
    只把"这个入口走哪条互斥路线"固化到静态目录, 供实例图校验使用.
    """

    id: str
    operation_id: str
    line: str
    item_type: ItemType
    route_group: str
    route_variant_id: str = ""

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "operation_id": self.operation_id,
            "line": self.line,
            "item_type": self.item_type.value,
            "route_group": self.route_group,
            "route_variant_id": self.route_variant_id,
        }


@dataclass(frozen=True)
class RecipeSpec:
    """一个产品的静态工艺定义."""

    id: str
    version: str
    product_type: str
    operations: tuple[OperationTemplate, ...]
    route_variants: tuple[RouteVariant, ...]
    entries: tuple[OrderEntry, ...]
    join_operation_id: str = ""

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "version": self.version,
            "product_type": self.product_type,
            "operations": [op.as_dict() for op in self.operations],
            "route_variants": [route.as_dict()
                               for route in self.route_variants],
            "entries": [entry.as_dict() for entry in self.entries],
            "join_operation_id": self.join_operation_id,
        }


@dataclass(frozen=True)
class CapabilitySpec:
    id: str
    description: str = ""

    def as_dict(self) -> dict:
        return {"id": self.id, "description": self.description}


@dataclass(frozen=True)
class DeviceSpec:
    """设备目录条目; 逻辑工序处理器不是物理设备."""

    id: str
    kind: str                          # arm | agv | logic
    capabilities: tuple[str, ...]
    resources: tuple[str, ...] = ()
    default_line_id: str = ""
    physical: bool = True

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "capabilities": list(self.capabilities),
            "resources": list(self.resources),
            "default_line_id": self.default_line_id,
            "physical": bool(self.physical),
        }


@dataclass(frozen=True)
class ZoneSpec:
    """区域目录条目; 容量权威仍在配置/物料登记表中."""

    id: str
    capacity: int = 1
    description: str = ""

    def as_dict(self) -> dict:
        return {"id": self.id, "capacity": int(self.capacity),
                "description": self.description}


# ═══════════════════════════════════════════════════════════════
# 目录: 唯一索引与完整性校验
# ═══════════════════════════════════════════════════════════════

class ProcessCatalog:
    """配方/能力/设备/区域/资源的唯一索引与校验入口."""

    def __init__(self, *, recipes, capabilities, devices, zones,
                 known_resources=(), version: str = "") -> None:
        self.recipes = tuple(recipes)
        self.capabilities = tuple(capabilities)
        self.devices = tuple(devices)
        self.zones = tuple(zones)
        self.known_resources = tuple(known_resources)
        self.version = str(version)

        self._recipe_rows: dict[str, list[RecipeSpec]] = {}
        self._operation_rows: dict[str, list[tuple[str, OperationTemplate]]] = {}
        self._kind_rows: dict[str, list[tuple[str, OperationTemplate]]] = {}
        for recipe in self.recipes:
            self._recipe_rows.setdefault(recipe.id, []).append(recipe)
            for op in recipe.operations:
                self._operation_rows.setdefault(op.id, []).append(
                    (recipe.id, op))
                self._kind_rows.setdefault(op.kind, []).append(
                    (recipe.id, op))
        self._capability_rows: dict[str, list[CapabilitySpec]] = {}
        for cap in self.capabilities:
            self._capability_rows.setdefault(cap.id, []).append(cap)
        self._device_rows: dict[str, list[DeviceSpec]] = {}
        for device in self.devices:
            self._device_rows.setdefault(device.id, []).append(device)
        self._zone_rows: dict[str, list[ZoneSpec]] = {}
        for zone in self.zones:
            self._zone_rows.setdefault(zone.id, []).append(zone)

    # ── 查询 ────────────────────────────────────────────────

    def recipe(self, recipe_id: str) -> RecipeSpec:
        rows = self._recipe_rows.get(recipe_id, [])
        if not rows:
            raise ProcessModelError(f"未知 recipe: {recipe_id!r}")
        if len(rows) > 1:
            raise ProcessModelError(f"重复 recipe id: {recipe_id!r}")
        return rows[0]

    def operation(self, operation_id: str,
                  recipe_id: Optional[str] = None) -> OperationTemplate:
        rows = self._operation_rows.get(operation_id, [])
        if recipe_id is not None:
            rows = [row for row in rows if row[0] == recipe_id]
        if not rows:
            where = f" (recipe={recipe_id})" if recipe_id else ""
            raise ProcessModelError(
                f"未知 operation: {operation_id!r}{where}")
        if len(rows) > 1:
            raise ProcessModelError(
                f"重复 operation id: {operation_id!r}")
        return rows[0][1]

    def operation_for_kind(self, kind: str) -> OperationTemplate:
        rows = self._kind_rows.get(kind, [])
        if not rows:
            raise ProcessModelError(f"未知 task kind: {kind!r}")
        if len(rows) > 1:
            raise ProcessModelError(f"重复 task kind: {kind!r}")
        return rows[0][1]

    def operations_for_device(
            self, device_id: str) -> tuple[OperationTemplate, ...]:
        rows: dict[str, OperationTemplate] = {}
        for recipe in self.recipes:
            for op in recipe.operations:
                if device_id in op.candidate_devices:
                    rows.setdefault(op.id, op)
        return tuple(rows.values())

    def device(self, device_id: str) -> DeviceSpec:
        rows = self._device_rows.get(device_id, [])
        if not rows:
            raise ProcessModelError(f"未知设备: {device_id!r}")
        if len(rows) > 1:
            raise ProcessModelError(f"重复 device id: {device_id!r}")
        return rows[0]

    def capability(self, capability_id: str) -> CapabilitySpec:
        rows = self._capability_rows.get(capability_id, [])
        if not rows:
            raise ProcessModelError(f"未知能力: {capability_id!r}")
        if len(rows) > 1:
            raise ProcessModelError(f"重复 capability id: {capability_id!r}")
        return rows[0]

    def zone(self, zone_id: str) -> ZoneSpec:
        rows = self._zone_rows.get(zone_id, [])
        if not rows:
            raise ProcessModelError(f"未知 zone: {zone_id!r}")
        if len(rows) > 1:
            raise ProcessModelError(f"重复 zone id: {zone_id!r}")
        return rows[0]

    def route_lines(self) -> list[str]:
        """报告用路线摘要; 不是调度输入."""
        lines: list[str] = []
        for recipe in self.recipes:
            for route in recipe.route_variants:
                lines.append(f"{route.group}/{route.id}: "
                             f"{' -> '.join(route.operation_ids)}")
            if recipe.join_operation_id:
                join = self.operation(recipe.join_operation_id, recipe.id)
                inputs = " + ".join(
                    f"{port.zone}:{port.item_type.value}"
                    for port in join.inputs)
                output = ("" if join.output is None
                          else f" -> {join.output.zone}:"
                               f"{join.output.item_type.value}")
                lines.append(f"{recipe.id}/join: {inputs}{output}")
        return lines

    # ── 校验 ────────────────────────────────────────────────

    def validate(self) -> dict:
        """校验目录; 有错误时抛出 ProcessModelError.

        所有错误信息带 recipe/operation 定位. 返回的报告用于证据输出.
        """
        report = self.validate_report()
        if not report["ok"]:
            raise ProcessModelError(
                "工艺目录校验失败:\n" + "\n".join(report["errors"]))
        return report

    def validate_report(self) -> dict:
        errors: list[str] = []
        checks: list[dict] = []

        def check(name: str, ok: bool, detail: str = "") -> None:
            checks.append({"name": name, "ok": bool(ok), "detail": detail})

        def err(message: str, recipe_id: str = "",
                operation_id: str = "") -> None:
            prefix = []
            if recipe_id:
                prefix.append(f"recipe={recipe_id}")
            if operation_id:
                prefix.append(f"operation={operation_id}")
            errors.append(
                ((" ".join(prefix) + ": ") if prefix else "") + message)

        self._check_unique_ids(err, check)

        for recipe in self.recipes:
            self._check_recipe(recipe, err, check)

        return {"ok": not errors, "errors": errors, "checks": checks,
                "version": self.version}

    def _check_unique_ids(self, err, check) -> None:
        for label, rows in (
                ("recipe", self.recipes),
                ("capability", self.capabilities),
                ("device", self.devices),
                ("zone", self.zones)):
            seen: dict[str, int] = {}
            for row in rows:
                seen[row.id] = seen.get(row.id, 0) + 1
            duplicated = sorted(key for key, count in seen.items()
                                if count > 1)
            check(f"unique_{label}_ids", not duplicated,
                  f"duplicated={duplicated}")
            for key in duplicated:
                err(f"重复 {label} id: {key!r}")

        operation_counts: dict[str, int] = {}
        kind_counts: dict[str, int] = {}
        for recipe in self.recipes:
            for operation in recipe.operations:
                operation_counts[operation.id] = \
                    operation_counts.get(operation.id, 0) + 1
                kind_counts[operation.kind] = \
                    kind_counts.get(operation.kind, 0) + 1
        duplicated_operations = sorted(
            key for key, count in operation_counts.items() if count > 1)
        duplicated_kinds = sorted(
            key for key, count in kind_counts.items() if count > 1)
        check("unique_operation_ids", not duplicated_operations,
              f"duplicated={duplicated_operations}")
        check("unique_task_kinds", not duplicated_kinds,
              f"duplicated={duplicated_kinds}")
        for key in duplicated_operations:
            err(f"重复 operation id: {key!r} (catalog 级)")
        for key in duplicated_kinds:
            err(f"重复 task kind: {key!r} (catalog 级)")

        if self.known_resources:
            for device in self.devices:
                if (device.default_line_id
                        and device.default_line_id
                        not in self.known_resources):
                    err(f"设备 {device.id!r} 默认线 "
                        f"{device.default_line_id!r} 不在资源表中")

    def _check_recipe(self, recipe: RecipeSpec, err, check) -> None:
        rid = recipe.id
        check(f"recipe:{rid}:identity",
              bool(recipe.id and recipe.version and recipe.product_type),
              f"version={recipe.version!r} "
              f"product_type={recipe.product_type!r}")
        if not recipe.id:
            err("缺少 recipe id")
        if not recipe.version:
            err("缺少 recipe version", rid)
        if not recipe.product_type:
            err("缺少 product_type", rid)
        if not recipe.operations:
            err("recipe 缺少 operation", rid)

        operation_ids: list[str] = [op.id for op in recipe.operations]
        duplicated_ops = sorted({op_id for op_id in operation_ids
                                 if operation_ids.count(op_id) > 1})
        check(f"recipe:{rid}:unique_operation_ids", not duplicated_ops,
              f"duplicated={duplicated_ops}")
        for op_id in duplicated_ops:
            err(f"重复 operation id: {op_id!r}", rid, op_id)
        kind_rows: dict[str, list[str]] = {}
        for op in recipe.operations:
            kind_rows.setdefault(op.kind, []).append(op.id)
        duplicated_kinds = sorted(kind for kind, rows in kind_rows.items()
                                  if len(rows) > 1)
        check(f"recipe:{rid}:unique_kinds", not duplicated_kinds,
              f"duplicated={duplicated_kinds}")
        for kind in duplicated_kinds:
            err(f"重复 operation kind: {kind!r} -> {kind_rows[kind]}", rid)

        operation_index = {op.id: op for op in recipe.operations}
        for op in recipe.operations:
            self._check_operation(recipe, op, operation_index, err, check)

        self._check_dependency_graph(recipe, operation_index, err, check)
        self._check_routes(recipe, operation_index, err, check)
        self._check_entries(recipe, operation_index, err, check)
        self._check_join(recipe, operation_index, err, check)

    def _check_operation(self, recipe: RecipeSpec, op: OperationTemplate,
                         operation_index: dict, err, check) -> None:
        rid, oid = recipe.id, op.id
        check(f"operation:{oid}:identity",
              bool(op.id and op.kind and op.semantics),
              f"kind={op.kind!r} semantics={op.semantics!r}")
        if not op.id:
            err("缺少 operation id", rid)
        if not op.kind:
            err("缺少 task kind", rid, oid)
        if not op.semantics:
            err("缺少工序语义", rid, oid)
        if op.executor_kind not in EXECUTOR_KINDS:
            err(f"未知执行器类型: {op.executor_kind!r}", rid, oid)
        else:
            if op.source_kind not in SOURCE_KINDS[op.executor_kind]:
                err(f"执行器 {op.executor_kind} 不支持 source_kind="
                    f"{op.source_kind!r}", rid, oid)
            if op.sink_kind not in SINK_KINDS[op.executor_kind]:
                err(f"执行器 {op.executor_kind} 不支持 sink_kind="
                    f"{op.sink_kind!r}", rid, oid)

        if not op.candidate_devices:
            err("缺少候选设备", rid, oid)
        for device_id in op.candidate_devices:
            device_rows = self._device_rows.get(device_id, [])
            if not device_rows:
                err(f"未知设备: {device_id!r}", rid, oid)
                continue
            if len(device_rows) > 1:
                err(f"重复 device id: {device_id!r}", rid, oid)
                continue
            device = device_rows[0]
            if device.kind != op.executor_kind:
                err(f"设备 {device_id!r} 类型 {device.kind!r} 与工序执行器 "
                    f"{op.executor_kind!r} 不匹配", rid, oid)
            if device.kind == "arm" and not device.default_line_id:
                err(f"机械臂设备 {device_id!r} 缺少 default_line_id",
                    rid, oid)
            if op.capability not in device.capabilities:
                err(f"设备 {device_id!r} 缺少能力 {op.capability!r}",
                    rid, oid)

        capability_rows = self._capability_rows.get(op.capability, [])
        if not capability_rows:
            err(f"未知能力: {op.capability!r}", rid, oid)
        elif len(capability_rows) > 1:
            err(f"重复 capability id: {op.capability!r}", rid, oid)

        if not op.inputs:
            err("缺少输入物料端口", rid, oid)
        if op.output is None:
            err("缺少输出物料端口", rid, oid)
        for port in (*op.inputs, *((op.output,) if op.output else ())):
            if port.zone not in self._zone_rows:
                err(f"未知 zone: {port.zone!r}", rid, oid)
            elif len(self._zone_rows[port.zone]) != 1:
                err(f"重复 zone id: {port.zone!r}", rid, oid)

        if op.executor_kind == "agv":
            if not op.route_origin_dock or not op.route_destination_dock:
                err("AGV 运输工序缺少 route_origin_dock/"
                    "route_destination_dock", rid, oid)
            else:
                for dock in (op.route_origin_dock,
                             op.route_destination_dock):
                    resource = f"dock_{dock}"
                    if (self.known_resources
                            and resource not in self.known_resources):
                        err(f"未知 dock 资源: {resource!r}", rid, oid)
            if len(op.inputs) != 1 or op.output is None \
                    or op.inputs[0].zone != op.output.zone:
                err("AGV 运输工序输入/输出区域必须一致", rid, oid)
        if op.executor_kind == "logic" and len(op.inputs) < 2:
            err("逻辑合流工序至少需要两个输入端口", rid, oid)

        if op.resource_policy not in RESOURCE_POLICIES:
            err(f"未知资源策略: {op.resource_policy!r}", rid, oid)
        if op.resource_policy == "fixed" and not op.resources:
            err("固定资源策略缺少资源请求", rid, oid)
        if self.known_resources:
            for resource in op.resources:
                if resource not in self.known_resources:
                    err(f"未知资源: {resource!r}", rid, oid)
        if op.resource_policy == "agv_route" and op.resources:
            err("AGV 路线资源必须由 route spec 动态申请, 不能在模板中写死",
                rid, oid)

        if not op.work_steps:
            err("缺少工步模板引用", rid, oid)
        for step in op.work_steps:
            if step.template_id not in WORK_STEP_TEMPLATES:
                err(f"未知工步模板: {step.template_id!r}", rid, oid)
        if op.completion_verifier not in COMPLETION_VERIFIERS:
            err(f"未知完成验证器: {op.completion_verifier!r}", rid, oid)
        if int(op.priority) < 0:
            err(f"priority 不能为负: {op.priority}", rid, oid)
        if op.predecessor_policy not in PREDECESSOR_POLICIES:
            err(f"未知前置策略: {op.predecessor_policy!r}", rid, oid)
        for predecessor in op.depends_on:
            if predecessor not in operation_index:
                err(f"无效前置: {predecessor!r}", rid, oid)
        for successor in op.successors:
            if successor not in operation_index:
                err(f"无效后继: {successor!r}", rid, oid)

    def _check_dependency_graph(self, recipe: RecipeSpec,
                                operation_index: dict, err, check) -> None:
        rid = recipe.id
        inconsistent: list[str] = []
        for op in recipe.operations:
            for successor_id in op.successors:
                successor = operation_index.get(successor_id)
                if successor is not None and op.id not in successor.depends_on:
                    inconsistent.append(f"{op.id}->{successor_id} 缺少反向依赖")
            for predecessor_id in op.depends_on:
                predecessor = operation_index.get(predecessor_id)
                if predecessor is not None \
                        and op.id not in predecessor.successors:
                    inconsistent.append(
                        f"{predecessor_id}->{op.id} 缺少后继声明")
        check(f"recipe:{rid}:dependency_inverse", not inconsistent,
              f"inconsistent={inconsistent[:6]}")
        for row in inconsistent:
            err(f"依赖关系不一致: {row}", rid)

        state: dict[str, int] = {}
        cycle_paths: list[list[str]] = []

        def visit(node: str, stack: list[str]) -> None:
            mark = state.get(node, 0)
            if mark == 2:
                return
            if mark == 1:
                start = stack.index(node) if node in stack else 0
                cycle_paths.append(stack[start:] + [node])
                return
            state[node] = 1
            stack.append(node)
            for successor_id in operation_index[node].successors:
                if successor_id in operation_index:
                    visit(successor_id, stack)
            stack.pop()
            state[node] = 2

        for op in recipe.operations:
            visit(op.id, [])
        check(f"recipe:{rid}:acyclic", not cycle_paths,
              f"cycles={cycle_paths[:4]}")
        for path in cycle_paths:
            err("循环依赖: " + " -> ".join(path), rid, path[0])

        mismatched: list[str] = []
        for op in recipe.operations:
            input_types = {port.item_type for port in op.inputs}
            input_zones = {port.zone for port in op.inputs}
            for predecessor_id in op.depends_on:
                predecessor = operation_index.get(predecessor_id)
                if predecessor is None or predecessor.output is None:
                    continue
                output = predecessor.output
                if output.item_type not in input_types:
                    mismatched.append(
                        f"{predecessor_id} 输出 {output.item_type.value} "
                        f"不在 {op.id} 输入 "
                        f"{sorted(t.value for t in input_types)}")
                if output.zone not in input_zones:
                    mismatched.append(
                        f"{predecessor_id} 输出区域 {output.zone} "
                        f"不在 {op.id} 输入区域 {sorted(input_zones)}")
        check(f"recipe:{rid}:material_continuity", not mismatched,
              f"mismatched={mismatched[:6]}")
        for row in mismatched:
            err(f"物料类型不匹配: {row}", rid)

    def _check_routes(self, recipe: RecipeSpec, operation_index: dict,
                      err, check) -> None:
        rid = recipe.id
        route_ids = [route.id for route in recipe.route_variants]
        duplicated = sorted({route_id for route_id in route_ids
                             if route_ids.count(route_id) > 1})
        check(f"recipe:{rid}:unique_route_ids", not duplicated,
              f"duplicated={duplicated}")
        for route_id in duplicated:
            err(f"重复 route id: {route_id!r}", rid)
        if not recipe.route_variants:
            err("recipe 缺少 route variant", rid)

        covered: set[str] = set()
        for route in recipe.route_variants:
            context = f"route={route.id}"
            if not route.group:
                err(f"{context} 缺少 group", rid)
            if not route.operation_ids:
                err(f"{context} 缺少 operation", rid)
                continue
            missing = [op_id for op_id in route.operation_ids
                       if op_id not in operation_index]
            if missing:
                err(f"{context} 引用未知 operation: {missing}", rid)
                continue
            covered.update(route.operation_ids)
            operations = [operation_index[op_id]
                          for op_id in route.operation_ids]
            first, last = operations[0], operations[-1]
            if not any(port.zone == route.entry_zone
                       for port in first.inputs):
                err(f"{context} entry_zone={route.entry_zone!r} 不是首工序 "
                    f"{first.id} 的输入区域", rid, first.id)
            if last.output is None or last.output.zone != route.exit_zone:
                err(f"{context} exit_zone={route.exit_zone!r} 不是末工序 "
                    f"{last.id} 的输出区域", rid, last.id)
            for previous, current in zip(operations, operations[1:]):
                output = previous.output
                match = output is not None and any(
                    port.zone == output.zone
                    and port.item_type == output.item_type
                    for port in current.inputs)
                if not match:
                    err(f"{context} 工序链不连续: {previous.id} -> "
                        f"{current.id} 物料类型/区域不匹配", rid, current.id)

        join_id = recipe.join_operation_id
        if join_id and join_id in operation_index:
            join = operation_index[join_id]
            covered.add(join.id)
            terminals = [route.terminal_operation_id
                         for route in recipe.route_variants]
            for terminal in terminals:
                if terminal and terminal not in join.depends_on:
                    err(f"route 终点 {terminal!r} 未接入合流 {join_id!r}",
                        rid, terminal)
        orphan = sorted(set(operation_index) - covered)
        check(f"recipe:{rid}:operations_covered", not orphan,
              f"orphan={orphan}")
        for op_id in orphan:
            err(f"工序未被任何路线或合流覆盖: {op_id!r}", rid, op_id)

    def _check_entries(self, recipe: RecipeSpec, operation_index: dict,
                       err, check) -> None:
        rid = recipe.id
        entry_ids = [entry.id for entry in recipe.entries]
        duplicated = sorted({entry_id for entry_id in entry_ids
                             if entry_ids.count(entry_id) > 1})
        check(f"recipe:{rid}:unique_entry_ids", not duplicated,
              f"duplicated={duplicated}")
        for entry_id in duplicated:
            err(f"重复 entry id: {entry_id!r}", rid)
        if not recipe.entries:
            err("recipe 缺少订单入口", rid)
        route_groups = {route.group for route in recipe.route_variants}
        routes_by_id = {route.id: route for route in recipe.route_variants}
        first_ops = {route.operation_ids[0] for route in recipe.route_variants
                     if route.operation_ids}
        for entry in recipe.entries:
            if entry.operation_id not in operation_index:
                err(f"订单入口 {entry.id!r} 引用未知 operation "
                    f"{entry.operation_id!r}", rid)
                continue
            op = operation_index[entry.operation_id]
            if not op.inputs:
                continue
            if op.inputs[0].zone != entry.line:
                err(f"订单入口 {entry.id!r} line={entry.line!r} 与工序 "
                    f"{op.id} 输入区域 {op.inputs[0].zone!r} 不一致",
                    rid, op.id)
            if op.inputs[0].item_type != entry.item_type:
                err(f"订单入口 {entry.id!r} 来料类型 "
                    f"{entry.item_type.value} 与工序 {op.id} 输入 "
                    f"{op.inputs[0].item_type.value} 不匹配", rid, op.id)
            if entry.route_group and entry.route_group not in route_groups:
                err(f"订单入口 {entry.id!r} 引用未知 route group "
                    f"{entry.route_group!r}", rid)
            if entry.operation_id not in first_ops:
                err(f"订单入口 {entry.id!r} 的工序 {op.id} 不是任何路线的"
                    f"首工序", rid, op.id)
            self._check_entry_route(recipe, entry, op, routes_by_id,
                                    err, check)

    def _check_entry_route(self, recipe: RecipeSpec, entry: OrderEntry,
                           op: OperationTemplate, routes_by_id: dict,
                           err, check) -> None:
        """入口到 route variant 的固定对应必须唯一且自洽."""
        rid = recipe.id
        if not entry.route_variant_id:
            candidates = [
                route for route in recipe.route_variants
                if route.operation_ids
                and route.operation_ids[0] == entry.operation_id
                and (not entry.route_group
                     or route.group == entry.route_group)]
            ok = len(candidates) == 1
            check(f"entry:{entry.id}:route", ok,
                  f"candidates={[route.id for route in candidates]}")
            if not ok:
                err(f"订单入口 {entry.id!r} 无法唯一确定 route variant: "
                    f"{[route.id for route in candidates]}", rid,
                    entry.operation_id)
            return
        route = routes_by_id.get(entry.route_variant_id)
        ok = (route is not None
              and bool(route.operation_ids)
              and route.operation_ids[0] == entry.operation_id
              and (not entry.route_group or route.group == entry.route_group)
              and route.entry_zone == entry.line)
        check(f"entry:{entry.id}:route", ok,
              f"route={entry.route_variant_id!r}")
        if route is None:
            err(f"订单入口 {entry.id!r} 引用未知 route variant "
                f"{entry.route_variant_id!r}", rid, entry.operation_id)
            return
        first = route.operation_ids[0] if route.operation_ids else ""
        if first != entry.operation_id:
            err(f"订单入口 {entry.id!r} 的 route variant "
                f"{route.id!r} 首工序是 {first!r}, 不是 "
                f"{entry.operation_id!r}", rid, entry.operation_id)
        if entry.route_group and route.group != entry.route_group:
            err(f"订单入口 {entry.id!r} 的 route variant {route.id!r} 属于 "
                f"group {route.group!r}, 与声明的 {entry.route_group!r} "
                f"不一致", rid, entry.operation_id)
        if route.entry_zone != entry.line:
            err(f"订单入口 {entry.id!r} 的 route variant {route.id!r} "
                f"entry_zone={route.entry_zone!r} 与来源线 "
                f"{entry.line!r} 不一致", rid, entry.operation_id)

    def _check_join(self, recipe: RecipeSpec, operation_index: dict,
                    err, check) -> None:
        rid = recipe.id
        join_id = recipe.join_operation_id
        if not join_id:
            check(f"recipe:{rid}:join", True, "no join")
            return
        join = operation_index.get(join_id)
        if join is None:
            err(f"未知 join operation: {join_id!r}", rid)
            return
        ok = (len(join.inputs) >= 2
              and join.output is not None
              and join.completion_verifier == "join_pair_committed"
              and not join.successors)
        check(f"recipe:{rid}:join", ok,
              f"inputs={len(join.inputs)} "
              f"verifier={join.completion_verifier!r} "
              f"successors={list(join.successors)}")
        if len(join.inputs) < 2:
            err("合流工序至少需要两个输入端口", rid, join_id)
        if join.output is None:
            err("合流工序缺少输出端口", rid, join_id)
        if join.completion_verifier != "join_pair_committed":
            err(f"合流工序完成验证器应为 join_pair_committed, 实际 "
                f"{join.completion_verifier!r}", rid, join_id)
        if join.successors:
            err(f"合流工序不应有后继: {list(join.successors)}", rid, join_id)

    # ── 报告 ────────────────────────────────────────────────

    def as_dict(self) -> dict:
        return {
            "version": self.version,
            "known_resources": list(self.known_resources),
            "capabilities": [cap.as_dict() for cap in self.capabilities],
            "devices": [device.as_dict() for device in self.devices],
            "zones": [zone.as_dict() for zone in self.zones],
            "recipes": [recipe.as_dict() for recipe in self.recipes],
        }
