"""order_variants.py — 内置订单方案目录与确定性物化 (阶段 10 / 订单方案目录).

本模块把已登记路线、路线前缀、FINAL 合流和 MVP 直接来料配方包装成有限的
:class:`OrderVariant`, 供订单工作台展示并一键生成普通订单. 它只做**静态、
显式、可测试**的目录派生, 不实现任意 recipe/DAG 搜索:

  - 完整路线变体: 已登记 ``RouteVariant`` 的完整链;
  - 工序前缀变体: 同一 route 的非空前缀, 让“一个工序不同”成为独立方案;
  - FINAL 组合: 完整壳体路线 + 完整支架路线 + 已登记 join;
  - MVP 变体: ``RecipeSpec.direct_input`` 的直接来料 join.

物化器只支持线性分支 + 可选 join + 直接来料 join, 端口在定义中显式绑定;
生成结果仍交给 :class:`OrderValidator`/:class:`OrderDagCompiler` 校验.
本模块不导入 CoppeliaSim、SQLAlchemy 或 application, 不创建运行实例/UID.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Iterable, Optional

from .. import spec
from . import assembly_mvp as mvp
from .materials import ItemType
from .order_compiler import OrderDagCompiler
from .orders import (OrderEdge, OrderInput, OrderNode, OrderSpec,
                     OrderSpecError, OrderTarget)
from .process_model import (OperationTemplate, ProcessCatalog, RecipeSpec,
                            RouteVariant)
from .scene_profile import SceneCapabilities

VARIANT_VERSION = spec.ORDER_VARIANT_VERSION

SCOPE_PRODUCTION = "production"
SCOPE_INTERMEDIATE = "intermediate"
SCOPE_TRANSIT = "transit"
SCOPES = (SCOPE_PRODUCTION, SCOPE_INTERMEDIATE, SCOPE_TRANSIT)
_SCOPE_ORDER = {SCOPE_PRODUCTION: 0, SCOPE_INTERMEDIATE: 1,
                SCOPE_TRANSIT: 2}

VERIFICATION_REAL_RUN = "real_run"
VERIFICATION_PLANNER_TEST = "planner_test"
VERIFICATION_COMPILER_ONLY = "compiler_only"
VERIFICATIONS = (VERIFICATION_REAL_RUN, VERIFICATION_PLANNER_TEST,
                 VERIFICATION_COMPILER_ONLY)

MAX_VARIANT_NODES = 64
_PROFILE_FULL_FLOW = mvp.SCENE_PROFILE_FULL_FLOW
_PROFILE_ASSEMBLY_MVP = mvp.PROFILE
_PRODUCT_TYPES = frozenset({
    ItemType.FINAL.value, mvp.ITEM_TYPE_COMPRESSOR_ASSY,
    mvp.ITEM_TYPE_FLANGE_ASSY,
})


class OrderVariantError(RuntimeError):
    """订单方案目录/物化错误; API 映射为 422."""

    code = "ORDER_VARIANT"


class OrderVariantNotFoundError(OrderVariantError):
    code = "ORDER_VARIANT_UNKNOWN"


class OrderVariantUnavailableError(OrderVariantError):
    code = "ORDER_VARIANT_UNAVAILABLE"


class OrderVariantCompileError(OrderVariantError):
    code = "ORDER_VARIANT_COMPILE"


class OrderVariantNodeLimitError(OrderVariantError):
    code = "ORDER_VARIANT_NODE_LIMIT"


# ═══════════════════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class VariantStep:
    """线性分支中的一道工序; ``to_port`` 是显式入口端口 identity."""

    template_id: str
    to_port: str


@dataclass(frozen=True)
class VariantBranch:
    """一条从订单投料出发的线性工序链."""

    branch_id: str
    entry_item_type: ItemType
    entry_zone: str
    steps: tuple[VariantStep, ...]
    output_zone: str = ""
    output_item_type: Optional[ItemType] = None

    @property
    def template_ids(self) -> tuple[str, ...]:
        return tuple(step.template_id for step in self.steps)

    @property
    def terminal_template_id(self) -> str:
        return self.steps[-1].template_id if self.steps else ""

    def as_dict(self) -> dict:
        return {
            "branch_id": self.branch_id,
            "entry_item_type": self.entry_item_type.value,
            "entry_zone": self.entry_zone,
            "steps": [
                {"template_id": step.template_id,
                 "to_port": step.to_port}
                for step in self.steps],
            "template_ids": list(self.template_ids),
            "output_zone": self.output_zone,
            "output_item_type": (
                None if self.output_item_type is None
                else self.output_item_type.value),
        }


@dataclass(frozen=True)
class VariantDirectInput:
    """直接来料 join 的端口投料."""

    port_id: str
    item_type: ItemType
    zone: str

    def as_dict(self) -> dict:
        return {"port_id": self.port_id,
                "item_type": self.item_type.value, "zone": self.zone}


@dataclass(frozen=True)
class OrderVariant:
    """一个命名订单方案; 只引用已登记工序/端口/区域."""

    variant_id: str
    display_name: str
    description: str
    scene_profile: str
    scope: str
    verification: str
    target_item_type: ItemType
    target_quantity: int
    branches: tuple[VariantBranch, ...]
    join_template_id: str = ""
    join_ports: tuple[tuple[str, str], ...] = ()
    direct_inputs: tuple[VariantDirectInput, ...] = ()
    route_ids: tuple[str, ...] = ()
    output_zone: str = ""
    used_arms: tuple[str, ...] = ()
    used_transports: tuple[str, ...] = ()
    difference_from: str = ""
    difference_summary: str = ""
    recommended_transport_fleet: str = "single"
    route_complete: bool = False

    @property
    def template_ids(self) -> tuple[str, ...]:
        rows: list[str] = []
        for branch in self.branches:
            rows.extend(branch.template_ids)
        if self.join_template_id:
            rows.append(self.join_template_id)
        return tuple(dict.fromkeys(rows))

    @property
    def input_rows(self) -> tuple[tuple[ItemType, str], ...]:
        rows: list[tuple[ItemType, str]] = []
        for branch in self.branches:
            rows.append((branch.entry_item_type, branch.entry_zone))
        for row in self.direct_inputs:
            rows.append((row.item_type, row.zone))
        return tuple(rows)

    @property
    def signature(self) -> tuple:
        return (
            self.target_item_type.value,
            self.join_template_id,
            tuple((branch.branch_id, branch.template_ids)
                  for branch in self.branches),
            tuple((row.port_id, row.item_type.value, row.zone)
                  for row in self.direct_inputs),
        )

    def as_dict(self, *, available: bool = True,
                unavailable_reason: str = "") -> dict:
        return {
            "variant_id": self.variant_id,
            "display_name": self.display_name,
            "description": self.description,
            "scene_profile": self.scene_profile,
            "scope": self.scope,
            "verification": self.verification,
            "target": {"item_type": self.target_item_type.value,
                       "quantity": int(self.target_quantity)},
            "inputs": [
                {"item_type": item_type.value, "zone": zone,
                 "quantity": 1}
                for item_type, zone in self.input_rows],
            "branches": [branch.as_dict() for branch in self.branches],
            "direct_inputs": [row.as_dict() for row in self.direct_inputs],
            "template_ids": list(self.template_ids),
            "route_ids": list(self.route_ids),
            "join_template_id": self.join_template_id,
            "join_ports": [
                {"branch_id": branch_id, "port_id": port_id}
                for branch_id, port_id in self.join_ports],
            "output_zone": self.output_zone,
            "used_arms": list(self.used_arms),
            "used_transports": list(self.used_transports),
            "difference_from": self.difference_from,
            "difference_summary": self.difference_summary,
            "recommended_transport_fleet":
                self.recommended_transport_fleet,
            "route_complete": bool(self.route_complete),
            "available": bool(available),
            "unavailable_reason": str(unavailable_reason or ""),
        }


# ═══════════════════════════════════════════════════════════════
# 目录元数据
# ═══════════════════════════════════════════════════════════════

_ROUTE_VARIANT_IDS = {
    # (route_id, prefix_length) -> 稳定方案 ID
    ("housing_direct", 1): "semi_ac_direct_a",
    ("housing_direct", 2): "semi_ce_direct_ac",
    ("housing_direct", 3): "semi_ce_direct",
    ("housing_alt", 1): "semi_ab_alt_b",
    ("housing_alt", 2): "semi_ab_alt_ba",
    ("housing_alt", 3): "semi_ac_alt_a",
    ("housing_alt", 4): "semi_ce_alt_ac",
    ("housing_alt", 5): "semi_ce_alt",
    ("bracket_route", 1): "part_de_d",
    ("bracket_route", 2): "part_de_de",
    ("bracket_route", 3): "part_de_bracket",
}

_FINAL_VARIANT_IDS = {
    ("housing_direct", "bracket_route"): "final_direct_kit",
    ("housing_alt", "bracket_route"): "final_alt_kit",
}

_MVP_VARIANT_IDS = {
    mvp.TEMPLATE_COMPRESSOR: "mvp_compressor_pair",
    mvp.TEMPLATE_FLANGE: "mvp_flange_pair",
}

_VARIANT_META = {
    "final_direct_kit": {
        "display_name": "壳体直达 + 支架齐套 → FINAL",
        "description": "RAW_A 直达 + RAW_D 支架路线，在 E 岛 kit_join 合流",
        "difference_summary": "",
        "recommended_transport_fleet": "single",
    },
    "final_alt_kit": {
        "display_name": "壳体绕行 + 支架齐套 → FINAL",
        "description": "RAW_B 绕行 + RAW_D 支架，在 E 岛 kit_join 合流",
        "difference_from": "final_direct_kit",
        "difference_summary": "壳体改为 B 臂上料 + B→A 运输，多使用 B 臂",
        "recommended_transport_fleet": "dual",
    },
    "semi_ce_direct": {
        "display_name": "壳体直达完整 → SEMI_CE",
        "description": "A 臂上料、C 臂汇入传送带、E 臂 buffer 搬运；不含支架与合流",
        "difference_summary": "",
        "recommended_transport_fleet": "single",
    },
    "semi_ac_direct_a": {
        "display_name": "A 臂上料 → SEMI_AC",
        "description": "仅 A 臂从 S1 来料搬运到 handoff_ac",
        "difference_from": "semi_ce_direct",
        "difference_summary": "比完整直达少 housing_finish、housing_to_E_buffer",
        "recommended_transport_fleet": "single",
    },
    "semi_ce_direct_ac": {
        "display_name": "A+C 汇入传送带 → SEMI_CE",
        "description": "A 臂上料 + C 臂搬运；不含 E 臂 buffer 搬运",
        "difference_from": "semi_ce_direct",
        "difference_summary": "比完整直达少 housing_to_E_buffer（E 臂 buffer）",
        "recommended_transport_fleet": "single",
    },
    "semi_ce_alt": {
        "display_name": "壳体绕行完整 → SEMI_CE",
        "description": "B 臂上料、B→A 运输、A 臂卸料、C 臂汇入、E 臂 buffer",
        "difference_from": "semi_ce_direct",
        "difference_summary": "壳体改为 B 臂上料 + B→A 运输，多使用 B 臂",
        "recommended_transport_fleet": "single",
    },
    "semi_ab_alt_b": {
        "display_name": "B 臂上料 → SEMI_AB（AGV 在载）",
        "description": "仅 B 臂从 S1 来料搬运到 AGV 载台",
        "difference_from": "semi_ce_alt",
        "difference_summary": "比完整绕行少 B→A、A 臂卸料、C 臂、E 臂",
        "recommended_transport_fleet": "single",
    },
    "semi_ab_alt_ba": {
        "display_name": "B 臂上料 + B→A → SEMI_AB（AGV 在载）",
        "description": "B 臂上料并完成 B→A 运输；不含后续 A/C/E 工序",
        "difference_from": "semi_ce_alt",
        "difference_summary": "比完整绕行少 A 臂卸料、C 臂、E 臂",
        "recommended_transport_fleet": "single",
    },
    "semi_ac_alt_a": {
        "display_name": "绕行 A 臂卸料 → SEMI_AC",
        "description": "B 臂上料 + B→A 运输 + A 臂卸料到 handoff_ac",
        "difference_from": "semi_ce_alt",
        "difference_summary": "比完整绕行少 C 臂、E 臂",
        "recommended_transport_fleet": "single",
    },
    "semi_ce_alt_ac": {
        "display_name": "绕行 A+C → SEMI_CE",
        "description": "B 臂上料 + B→A 运输 + A 臂卸料 + C 臂汇入",
        "difference_from": "semi_ce_alt",
        "difference_summary": "比完整绕行少 housing_to_E_buffer（E 臂 buffer）",
        "recommended_transport_fleet": "single",
    },
    "part_de_d": {
        "display_name": "D 臂上料 → PART_DE（AGV 在载）",
        "description": "仅 D 臂从 S2 来料搬运到 AGV 载台",
        "difference_from": "part_de_bracket",
        "difference_summary": "比完整支架少 D→E 运输、E 臂卸料",
        "recommended_transport_fleet": "single",
    },
    "part_de_de": {
        "display_name": "D 臂上料 + D→E → PART_DE（AGV 在载）",
        "description": "D 臂上料并完成 D→E 运输；不含 E 臂卸料",
        "difference_from": "part_de_bracket",
        "difference_summary": "比完整支架少 bracket_to_E（E 臂卸料）",
        "recommended_transport_fleet": "single",
    },
    "part_de_bracket": {
        "display_name": "支架完整 → PART_DE",
        "description": "D 臂上料、D→E 运输、E 臂卸料到 buffer_e_d",
        "difference_summary": "",
        "recommended_transport_fleet": "single",
    },
    "mvp_compressor_pair": {
        "display_name": "压缩机 + 底板 → 压缩机装配体",
        "description": "MVP 独立岛直接来料原子配对（逻辑装配）",
        "difference_summary": "",
        "recommended_transport_fleet": "single",
    },
    "mvp_flange_pair": {
        "display_name": "前法兰 + 后法兰 → 法兰装配体",
        "description": "MVP 独立岛直接来料原子配对（逻辑装配）",
        "difference_from": "mvp_compressor_pair",
        "difference_summary": "不同产品与来料对；均为直接来料逻辑装配",
        "recommended_transport_fleet": "single",
    },
}

_REAL_RUN_IDS = frozenset({
    "final_direct_kit", "final_alt_kit",
    "mvp_compressor_pair", "mvp_flange_pair",
})
_PLANNER_TEST_IDS = frozenset({"semi_ce_direct"})


# ═══════════════════════════════════════════════════════════════
# 目录派生
# ═══════════════════════════════════════════════════════════════

def _port_for(
        template: OperationTemplate, zone: str,
        item_type: Optional[ItemType] = None
        ) -> Optional[tuple[str, str]]:
    """返回唯一匹配的 ``(port_id, item_type.value)``; 歧义返回 None."""
    matches = []
    for port_id, port in zip(template.input_port_ids, template.inputs):
        if port.zone != str(zone):
            continue
        if item_type is not None and port.item_type != item_type:
            continue
        matches.append((port_id, port.item_type.value))
    if len(matches) != 1:
        return None
    return matches[0]


def _resolve_route_templates(
        recipe: RecipeSpec, route: RouteVariant, catalog: ProcessCatalog
        ) -> Optional[tuple[OperationTemplate, ...]]:
    try:
        return tuple(catalog.operation(operation_id, recipe.id)
                     for operation_id in route.operation_ids)
    except Exception:
        return None


def _branch_from_route(
        recipe: RecipeSpec, route: RouteVariant, catalog: ProcessCatalog,
        *, length: int = 0, branch_id: str = ""
        ) -> Optional[VariantBranch]:
    templates = _resolve_route_templates(recipe, route, catalog)
    if not templates:
        return None
    if length:
        templates = templates[:int(length)]
    if not templates:
        return None
    first_port = _port_for(templates[0], route.entry_zone)
    if first_port is None:
        return None
    steps = [VariantStep(templates[0].id, first_port[0])]
    for previous, current in zip(templates, templates[1:]):
        output = previous.output
        if output is None:
            return None
        port = _port_for(current, output.zone, output.item_type)
        if port is None:
            return None
        steps.append(VariantStep(current.id, port[0]))
    output = templates[-1].output
    if output is None:
        return None
    return VariantBranch(
        branch_id=branch_id or route.id,
        entry_item_type=ItemType(first_port[1]),
        entry_zone=route.entry_zone,
        steps=tuple(steps),
        output_zone=output.zone,
        output_item_type=output.item_type)


def _scope_for(target_item_type: ItemType, output_zone: str) -> str:
    if target_item_type.value in _PRODUCT_TYPES:
        return SCOPE_PRODUCTION
    if str(output_zone) == "agv":
        return SCOPE_TRANSIT
    return SCOPE_INTERMEDIATE


def _verification_for(variant_id: str) -> str:
    if variant_id in _REAL_RUN_IDS:
        return VERIFICATION_REAL_RUN
    if variant_id in _PLANNER_TEST_IDS:
        return VERIFICATION_PLANNER_TEST
    return VERIFICATION_COMPILER_ONLY


def _used_arms(templates: Iterable[OperationTemplate]) -> tuple[str, ...]:
    used = set()
    for template in templates:
        for device_id in template.candidate_devices:
            text = str(device_id)
            if text.startswith("arm_"):
                used.add(text[len("arm_"):])
    return tuple(sorted(used))


def _used_transports(
        templates: Iterable[OperationTemplate]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        template.id for template in templates
        if template.executor_kind == "agv"))


def _meta(variant_id: str, **defaults) -> dict:
    row = dict(defaults)
    row.update(_VARIANT_META.get(variant_id) or {})
    return row


def _route_prefix_variants(
        recipe: RecipeSpec, route: RouteVariant,
        catalog: ProcessCatalog) -> list[OrderVariant]:
    templates = _resolve_route_templates(recipe, route, catalog)
    if not templates:
        return []
    full_templates = templates
    rows: list[OrderVariant] = []
    for length in range(1, len(templates) + 1):
        branch = _branch_from_route(
            recipe, route, catalog, length=length)
        if branch is None or branch.output_item_type is None:
            continue
        variant_id = _ROUTE_VARIANT_IDS.get(
            (route.id, length),
            f"{route.id}_p{length}")
        meta = _meta(variant_id)
        missing = [template.id for template in full_templates
                   if template.id not in branch.template_ids]
        difference_summary = meta.get("difference_summary", "")
        if not difference_summary and missing:
            difference_summary = "比完整路线少 " + "、".join(missing)
        completion = length == len(templates)
        rows.append(OrderVariant(
            variant_id=variant_id,
            display_name=str(meta.get("display_name") or
                             f"{route.id} / {branch.output_item_type.value}"),
            description=str(meta.get("description") or route.semantics),
            scene_profile=_PROFILE_FULL_FLOW,
            scope=_scope_for(branch.output_item_type, branch.output_zone),
            verification=_verification_for(variant_id),
            target_item_type=branch.output_item_type,
            target_quantity=1,
            branches=(branch,),
            route_ids=(route.id,),
            output_zone=branch.output_zone,
            used_arms=_used_arms(templates[:length]),
            used_transports=_used_transports(templates[:length]),
            difference_from=str(
                meta.get("difference_from")
                or ("" if completion else _ROUTE_VARIANT_IDS.get(
                    (route.id, len(templates)), ""))),
            difference_summary=str(difference_summary),
            recommended_transport_fleet=str(
                meta.get("recommended_transport_fleet") or "single"),
            route_complete=completion))
    return rows


def _final_variants(
        recipe: RecipeSpec, catalog: ProcessCatalog) -> list[OrderVariant]:
    if not recipe.join_operation_id:
        return []
    try:
        join = catalog.operation(recipe.join_operation_id, recipe.id)
    except Exception:
        return []
    if join.output is None or len(join.inputs) < 2:
        return []
    route_options: list[list[RouteVariant]] = []
    for port in join.inputs:
        matches = []
        for route in recipe.route_variants:
            branch = _branch_from_route(recipe, route, catalog)
            if branch is None:
                continue
            if branch.output_zone == port.zone \
                    and branch.output_item_type == port.item_type:
                matches.append(route)
        if not matches:
            return []
        route_options.append(matches)
    rows: list[OrderVariant] = []
    for combination in product(*route_options):
        route_ids = tuple(route.id for route in combination)
        if len(set(route_ids)) != len(route_ids):
            continue
        branches = []
        join_ports = []
        ok = True
        for route, port_id in zip(combination, join.input_port_ids):
            branch = _branch_from_route(recipe, route, catalog)
            if branch is None:
                ok = False
                break
            branches.append(branch)
            join_ports.append((branch.branch_id, port_id))
        if not ok:
            continue
        variant_id = _FINAL_VARIANT_IDS.get(
            route_ids, "final_" + "_".join(route_ids))
        meta = _meta(variant_id)
        templates = [
            catalog.operation(template_id, recipe.id)
            for branch in branches for template_id in branch.template_ids]
        templates.append(join)
        rows.append(OrderVariant(
            variant_id=variant_id,
            display_name=str(meta.get("display_name") or
                             " + ".join(route_ids)),
            description=str(meta.get("description") or join.semantics),
            scene_profile=_PROFILE_FULL_FLOW,
            scope=_scope_for(join.output.item_type, join.output.zone),
            verification=_verification_for(variant_id),
            target_item_type=join.output.item_type,
            target_quantity=1,
            branches=tuple(branches),
            join_template_id=join.id,
            join_ports=tuple(join_ports),
            route_ids=route_ids,
            output_zone=join.output.zone,
            used_arms=_used_arms(templates),
            used_transports=_used_transports(templates),
            difference_from=str(meta.get("difference_from") or ""),
            difference_summary=str(meta.get("difference_summary") or ""),
            recommended_transport_fleet=str(
                meta.get("recommended_transport_fleet") or "single"),
            route_complete=True))
    return rows


def _mvp_variants(
        recipe: RecipeSpec, catalog: ProcessCatalog) -> list[OrderVariant]:
    if not recipe.direct_input or len(recipe.operations) != 1:
        return []
    template = recipe.operations[0]
    if template.output is None or len(template.inputs) < 2:
        return []
    direct_inputs = tuple(
        VariantDirectInput(port_id, port.item_type, port.zone)
        for port_id, port in zip(template.input_port_ids, template.inputs))
    variant_id = _MVP_VARIANT_IDS.get(
        template.id, f"variant_{template.id}")
    meta = _meta(variant_id)
    return [OrderVariant(
        variant_id=variant_id,
        display_name=str(meta.get("display_name") or template.semantics),
        description=str(meta.get("description") or template.semantics),
        scene_profile=_PROFILE_ASSEMBLY_MVP,
        scope=_scope_for(template.output.item_type, template.output.zone),
        verification=_verification_for(variant_id),
        target_item_type=template.output.item_type,
        target_quantity=1,
        branches=(),
        join_template_id=template.id,
        direct_inputs=direct_inputs,
        output_zone=template.output.zone,
        used_arms=(),
        used_transports=(),
        difference_from=str(meta.get("difference_from") or ""),
        difference_summary=str(meta.get("difference_summary") or ""),
        recommended_transport_fleet=str(
            meta.get("recommended_transport_fleet") or "single"),
        route_complete=True)]


def build_order_variants(
        catalog: ProcessCatalog) -> tuple[OrderVariant, ...]:
    """从已校验目录派生全部内置方案; 纯函数, 结果稳定排序."""
    rows: list[OrderVariant] = []
    for recipe in catalog.recipes:
        for route in recipe.route_variants:
            rows.extend(_route_prefix_variants(recipe, route, catalog))
        rows.extend(_final_variants(recipe, catalog))
        rows.extend(_mvp_variants(recipe, catalog))
    unique: dict[tuple, OrderVariant] = {}
    for row in rows:
        unique.setdefault(row.signature, row)
    return tuple(sorted(
        unique.values(),
        key=lambda row: (not row.route_complete,
                         _SCOPE_ORDER.get(row.scope, 99),
                         row.variant_id)))


def find_order_variant(
        catalog: ProcessCatalog, variant_id: str) -> OrderVariant:
    target = str(variant_id or "").strip()
    for row in build_order_variants(catalog):
        if row.variant_id == target:
            return row
    raise OrderVariantNotFoundError(f"未知订单方案 {target!r}")


# ═══════════════════════════════════════════════════════════════
# 可用性与物化
# ═══════════════════════════════════════════════════════════════

def variant_profile_allowed(
        variant: OrderVariant, capabilities: SceneCapabilities) -> bool:
    """profile 过滤: full_flow 只允许 full_flow; MVP 工件允许全部."""
    if not capabilities.bound:
        return True
    if capabilities.scene_profile == _PROFILE_ASSEMBLY_MVP:
        return True
    return variant.scene_profile == capabilities.scene_profile


def variant_availability(
        variant: OrderVariant, catalog: ProcessCatalog,
        capabilities: SceneCapabilities) -> tuple[bool, str]:
    """静态可用性 + compiler preview; 返回 (available, reason)."""
    if not variant_profile_allowed(variant, capabilities):
        return False, (f"方案 profile {variant.scene_profile!r} 与当前工件 "
                       f"profile {capabilities.scene_profile!r} 不匹配")
    allowed = capabilities.allowed_template_ids
    for template_id in variant.template_ids:
        if template_id not in allowed:
            return False, f"模板 {template_id!r} 不属于当前工件 profile"
    if not capabilities.allows_target(variant.target_item_type):
        return False, (f"目标 {variant.target_item_type.value} 不在当前 "
                       f"profile 可生产类型中")
    for item_type, zone in variant.input_rows:
        if not capabilities.allows_input(item_type):
            return False, f"投料 {item_type.value} 不在当前 profile 入口类型中"
        capability_zone = capabilities.zone(zone)
        if capability_zone is None or not capability_zone.spawnable:
            return False, f"区域 {zone!r} 不是当前 profile 的可生成来料区域"
    try:
        order = materialize_variant(
            variant, catalog, order_id="variant-check", version=1,
            quantity=1)
        preview = OrderDagCompiler(catalog).preview(order)
    except OrderVariantError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if not preview.ok:
        first = preview.diagnostics[0] if preview.diagnostics else None
        return False, (f"{first.code}: {first.message}" if first
                       else "编译预览未通过")
    return True, ""


def materialize_variant(
        variant: OrderVariant, catalog: ProcessCatalog, *,
        order_id: str, version: int = 1, quantity: int = 1) -> OrderSpec:
    """把内置方案物化为普通 OrderSpec; 不分配 UID, 不申请资源."""
    quantity = int(quantity)
    if quantity <= 0:
        raise OrderVariantError("目标数量必须是正整数")
    per_unit = sum(len(branch.steps) for branch in variant.branches)
    per_unit += 1 if variant.join_template_id else 0
    if per_unit <= 0 or per_unit * quantity > MAX_VARIANT_NODES:
        raise OrderVariantNodeLimitError(
            f"方案需要 {per_unit * quantity} 个节点, 超过上限 "
            f"{MAX_VARIANT_NODES}")
    if not variant.branches and not variant.direct_inputs:
        raise OrderVariantError("方案没有工序或直接投料")
    if not variant.join_template_id and len(variant.branches) != 1:
        raise OrderVariantError("无 join 方案必须只有一条线性分支")

    nodes: list[OrderNode] = []
    edges: list[OrderEdge] = []
    inputs: list[OrderInput] = []

    def next_input(item_type: ItemType, zone: str, node_id: str,
                   port_id: str) -> None:
        inputs.append(OrderInput(
            input_id=f"in-{len(inputs):03d}", item_type=item_type,
            quantity=1, zone=zone, node_id=node_id, port_id=port_id))

    for unit in range(quantity):
        terminals: dict[str, str] = {}
        for branch_index, branch in enumerate(variant.branches):
            previous = ""
            for step_index, step in enumerate(branch.steps):
                node_id = (f"{variant.variant_id[:24]}-u{unit:02d}"
                           f"-b{branch_index:02d}-s{step_index:02d}")
                nodes.append(OrderNode(node_id, step.template_id))
                if step_index == 0:
                    next_input(branch.entry_item_type, branch.entry_zone,
                               node_id, step.to_port)
                else:
                    edges.append(OrderEdge(previous, node_id,
                                           step.to_port))
                previous = node_id
            terminals[branch.branch_id] = previous
        if not variant.join_template_id:
            continue
        join_id = f"{variant.variant_id[:24]}-u{unit:02d}-j"
        nodes.append(OrderNode(join_id, variant.join_template_id))
        if variant.direct_inputs:
            for row in variant.direct_inputs:
                next_input(row.item_type, row.zone, join_id, row.port_id)
        else:
            if not variant.join_ports:
                raise OrderVariantError("join 方案缺少端口绑定")
            for branch_id, port_id in variant.join_ports:
                terminal = terminals.get(branch_id)
                if not terminal:
                    raise OrderVariantError(
                        f"join 端口引用未知分支 {branch_id!r}")
                edges.append(OrderEdge(terminal, join_id, port_id))
    try:
        return OrderSpec.from_dict({
            "order_id": order_id,
            "version": int(version),
            "scene_id": spec.LAYOUT_NAME,
            "source": "manual",
            "target": {"item_type": variant.target_item_type.value,
                       "quantity": quantity},
            "inputs": [row.as_dict() for row in inputs],
            "nodes": [row.as_dict() for row in nodes],
            "edges": [row.as_dict() for row in edges],
        })
    except OrderSpecError:
        raise


__all__ = [
    "MAX_VARIANT_NODES", "OrderVariant", "OrderVariantCompileError",
    "OrderVariantError", "OrderVariantNodeLimitError",
    "OrderVariantNotFoundError", "OrderVariantUnavailableError",
    "SCOPES", "SCOPE_INTERMEDIATE", "SCOPE_PRODUCTION", "SCOPE_TRANSIT",
    "VERIFICATIONS", "VERIFICATION_COMPILER_ONLY",
    "VERIFICATION_PLANNER_TEST", "VERIFICATION_REAL_RUN", "VARIANT_VERSION",
    "VariantBranch", "VariantDirectInput", "VariantStep",
    "build_order_variants", "find_order_variant", "materialize_variant",
    "variant_availability", "variant_profile_allowed",
]