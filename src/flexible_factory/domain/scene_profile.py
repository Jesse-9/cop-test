"""scene_profile.py — 场景 profile 能力目录 (阶段 9).

本模块把**已加载/将用于启动的工件 manifest** 与静态工艺目录组合成订单
工作台可消费的能力清单:

  - 允许的目标物料、入口投料类型与可生成来料的区域;
  - 允许编排的工序模板与 route variant;
  - 区域/端口元数据, 供前端选择器使用.

边界 (阶段 9 §9A):

  - profile 的权威来源是工件 manifest 的 ``extra.v1``; 旧 full_flow 工件
    没有记录 ``scene_profile`` 时按 manifest 的 ``assembly_mvp`` 扩展块和
    ``material_model.extension_types`` 判定, 最后回落到 ``full_flow``.
  - ``assembly_mvp`` 工件在 v1 五岛底图上追加扩展岛, 因此同时包含
    full_flow 与 MVP 直接来料工序; ``full_flow`` 工件不包含 MVP 扩展.
  - 未绑定工件时返回 ``unbound`` 能力 (全部目录模板), 这是只读目录查询的
    兼容模式, 不代表任何具体工件可运行. 订单启动仍以 manifest 为准.
  - 本模块只做静态判断, 不读取 CoppeliaSim, 不申请资源, 不是物理可行性
    保证, 也不实现通用 recipe 图编译.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from .. import spec
from . import assembly_mvp as mvp
from .materials import ItemType
from .orders import OrderDiagnostic, OrderSpec
from .process_model import (OperationTemplate, ProcessCatalog,
                            RouteVariant)

PROFILE_FULL_FLOW = mvp.SCENE_PROFILE_FULL_FLOW
PROFILE_ASSEMBLY_MVP = mvp.PROFILE
SCENE_PROFILES = (PROFILE_FULL_FLOW, PROFILE_ASSEMBLY_MVP)
DEFAULT_SCENE_PROFILE = PROFILE_FULL_FLOW
SCENE_PROFILE_VERSION = spec.SCENE_PROFILE_VERSION

PROFILE_SOURCE_MANIFEST = "manifest"
PROFILE_SOURCE_DEFAULT = "default"
PROFILE_SOURCE_UNBOUND = "unbound"
PROFILE_SOURCES = (PROFILE_SOURCE_MANIFEST, PROFILE_SOURCE_DEFAULT,
                   PROFILE_SOURCE_UNBOUND)


class SceneProfileError(RuntimeError):
    """manifest profile/能力声明非法; 调用方应作为配置错误返回."""

    code = "SCENE_PROFILE"


# ═══════════════════════════════════════════════════════════════
# 能力模型
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class SceneZoneCapability:
    """订单工作台可用的区域元数据."""

    id: str
    capacity: int
    description: str
    spawnable: bool
    item_types: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "capacity": int(self.capacity),
            "description": self.description,
            "spawnable": bool(self.spawnable),
            "item_types": list(self.item_types),
        }


@dataclass(frozen=True)
class SceneCapabilities:
    """一个 profile (或未绑定目录) 的静态能力清单."""

    scene_profile: str
    source: str
    profiles: tuple[str, ...]
    templates: tuple[OperationTemplate, ...]
    routes: tuple[RouteVariant, ...]
    target_item_types: tuple[ItemType, ...]
    input_item_types: tuple[ItemType, ...]
    zones: tuple[SceneZoneCapability, ...]
    artifact_id: str = ""
    profile_version: str = SCENE_PROFILE_VERSION

    @property
    def bound(self) -> bool:
        return self.source != PROFILE_SOURCE_UNBOUND

    @property
    def allowed_template_ids(self) -> frozenset[str]:
        return frozenset(row.id for row in self.templates)

    @property
    def allowed_route_ids(self) -> frozenset[str]:
        return frozenset(row.id for row in self.routes)

    @property
    def zone_ids(self) -> frozenset[str]:
        return frozenset(row.id for row in self.zones)

    @property
    def item_types(self) -> tuple[ItemType, ...]:
        merged = set(self.target_item_types) | set(self.input_item_types)
        return tuple(sorted(merged, key=lambda row: row.value))

    def allows_template(self, template_id: str) -> bool:
        return str(template_id) in self.allowed_template_ids

    def allows_target(self, item_type: ItemType | str) -> bool:
        value = item_type.value if isinstance(item_type, ItemType) \
            else str(item_type)
        return value in {row.value for row in self.target_item_types}

    def allows_input(self, item_type: ItemType | str) -> bool:
        value = item_type.value if isinstance(item_type, ItemType) \
            else str(item_type)
        return value in {row.value for row in self.input_item_types}

    def zone(self, zone_id: str) -> Optional[SceneZoneCapability]:
        for row in self.zones:
            if row.id == str(zone_id):
                return row
        return None

    def as_dict(self) -> dict:
        return {
            "profile_version": self.profile_version,
            "scene_profile": self.scene_profile,
            "profile_source": self.source,
            "profiles": list(self.profiles),
            "artifact_id": self.artifact_id,
            "item_types": {
                "targets": [row.value for row in self.target_item_types],
                "inputs": [row.value for row in self.input_item_types],
            },
            "templates": [row.id for row in self.templates],
            "routes": [row.id for row in self.routes],
            "zones": [row.as_dict() for row in self.zones],
        }


# ═══════════════════════════════════════════════════════════════
# profile 判定
# ═══════════════════════════════════════════════════════════════

def normalize_scene_profile(value: object, *,
                            default: str = DEFAULT_SCENE_PROFILE) -> str:
    """校验 profile 名称; 空值返回 ``default``, 未知值抛 ValueError."""
    text = str(value or "").strip()
    if not text:
        return default
    if text not in SCENE_PROFILES:
        raise ValueError(
            f"未知场景 profile {text!r}; 允许值 {list(SCENE_PROFILES)}")
    return text


def _manifest_extra(manifest: dict) -> dict:
    if not isinstance(manifest, dict):
        raise SceneProfileError("manifest 顶层必须是对象")
    extra = manifest.get("extra")
    if extra is None:
        return {}
    if not isinstance(extra, dict):
        raise SceneProfileError("manifest.extra 必须是对象")
    v1 = extra.get("v1")
    if v1 is None:
        return {}
    if not isinstance(v1, dict):
        raise SceneProfileError("manifest.extra.v1 必须是对象")
    return v1


def _parse_item_type(raw: object, *, path: str) -> ItemType:
    text = str(raw or "").strip()
    try:
        return ItemType(text)
    except ValueError as exc:
        raise SceneProfileError(
            f"{path}: 未知物料类型 {text!r}") from exc


def _manifest_item_types(extra: dict) -> Optional[tuple[ItemType, ...]]:
    material = extra.get("material_model")
    if material is None:
        return None
    if not isinstance(material, dict):
        raise SceneProfileError("manifest.extra.v1.material_model 必须是对象")
    raw_types = material.get("types")
    if raw_types is None:
        return None
    if not isinstance(raw_types, list):
        raise SceneProfileError(
            "manifest.extra.v1.material_model.types 必须是数组")
    rows = [_parse_item_type(raw, path=f"material_model.types[{index}]")
            for index, raw in enumerate(raw_types)]
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.value] = counts.get(row.value, 0) + 1
    duplicated = sorted(value for value, count in counts.items()
                        if count > 1)
    if duplicated:
        raise SceneProfileError(f"material_model.types 重复: {duplicated}")
    return tuple(rows)


def profile_from_manifest(manifest: dict) -> tuple[str, str]:
    """返回 ``(scene_profile, source)``; 旧工件回落到 full_flow."""
    extra = _manifest_extra(manifest)
    declared = str(extra.get("scene_profile") or "").strip()
    if declared:
        if declared not in SCENE_PROFILES:
            raise SceneProfileError(
                f"manifest 声明未知 scene_profile {declared!r}; "
                f"允许值 {list(SCENE_PROFILES)}")
        return declared, PROFILE_SOURCE_MANIFEST

    extension = extra.get("assembly_mvp")
    if extension is not None:
        if not isinstance(extension, dict):
            raise SceneProfileError(
                "manifest.extra.v1.assembly_mvp 必须是对象或 null")
        declared = str(extension.get("profile") or PROFILE_ASSEMBLY_MVP)
        if declared != PROFILE_ASSEMBLY_MVP:
            raise SceneProfileError(
                f"assembly_mvp 扩展声明 profile {declared!r}, "
                f"与工件扩展块不一致")
        return PROFILE_ASSEMBLY_MVP, PROFILE_SOURCE_MANIFEST

    material = extra.get("material_model") or {}
    extension_types = material.get("extension_types") or []
    if not isinstance(extension_types, list):
        raise SceneProfileError(
            "manifest.extra.v1.material_model.extension_types 必须是数组")
    if extension_types:
        for index, raw in enumerate(extension_types):
            _parse_item_type(raw, path=f"material_model.extension_types"
                                      f"[{index}]")
        return PROFILE_ASSEMBLY_MVP, PROFILE_SOURCE_MANIFEST
    return PROFILE_FULL_FLOW, PROFILE_SOURCE_DEFAULT


# ═══════════════════════════════════════════════════════════════
# 能力计算
# ═══════════════════════════════════════════════════════════════

def _direct_input_template_ids(catalog: ProcessCatalog) -> frozenset[str]:
    """直接来料逻辑合流模板: 只来自 ``RecipeSpec.direct_input`` 配方."""
    ids: set[str] = set()
    for recipe in catalog.recipes:
        if not recipe.direct_input:
            continue
        for operation in recipe.operations:
            ids.add(operation.id)
    return frozenset(ids)


def profile_allows_template(profile: str, template_id: str,
                            direct_input_ids: Iterable[str]) -> bool:
    """profile 的模板允许规则; 不做任意 recipe 推断."""
    if profile == PROFILE_ASSEMBLY_MVP:
        return True
    if profile == PROFILE_FULL_FLOW:
        return str(template_id) not in set(direct_input_ids)
    return False


def _zone_capabilities(catalog: ProcessCatalog,
                       templates: Iterable[OperationTemplate],
                       allowed_zones: set[str]) -> tuple[
                           SceneZoneCapability, ...]:
    item_types: dict[str, set[str]] = {
        zone.id: set() for zone in catalog.zones if zone.id in allowed_zones}
    for template in templates:
        for port in template.inputs:
            if port.zone in item_types:
                item_types[port.zone].add(port.item_type.value)
    # MVP 来料槽的物料类型来自有限常量表; 与订单入口/场景生成位置一致.
    for zone_id, item_type in mvp.SOURCE_ITEM_TYPE.items():
        if zone_id in item_types:
            item_types[zone_id].add(item_type)
    rows = []
    for zone in catalog.zones:
        if zone.id not in allowed_zones:
            continue
        rows.append(SceneZoneCapability(
            id=zone.id, capacity=int(zone.capacity),
            description=zone.description, spawnable=bool(zone.spawnable),
            item_types=tuple(sorted(item_types.get(zone.id, ())))))
    rows.sort(key=lambda row: row.id)
    return tuple(rows)


def capabilities_for_profile(
        profile: str, catalog: ProcessCatalog, *,
        source: str = PROFILE_SOURCE_DEFAULT, artifact_id: str = "",
        manifest_item_types: Optional[Iterable[ItemType]] = None
        ) -> SceneCapabilities:
    """按 profile 过滤静态目录; 只返回该 profile 可运行的能力."""
    if profile not in SCENE_PROFILES:
        raise SceneProfileError(f"未知场景 profile {profile!r}")
    direct_ids = _direct_input_template_ids(catalog)
    templates = tuple(
        template for template in catalog.orchestratable_operations()
        if profile_allows_template(profile, template.id, direct_ids))
    allowed_templates = {row.id for row in templates}

    routes = tuple(
        route for recipe in catalog.recipes
        for route in recipe.route_variants
        if route.operation_ids
        and all(operation_id in allowed_templates
                for operation_id in route.operation_ids))

    if profile == PROFILE_FULL_FLOW:
        excluded_zones = set(mvp.ZONES)
    else:
        excluded_zones = set()
    zone_ids = {zone.id for zone in catalog.zones
                if zone.id not in excluded_zones}
    zones = _zone_capabilities(catalog, templates, zone_ids)

    targets = {template.output.item_type for template in templates
               if template.output is not None}
    inputs = {item_type for zone in zones if zone.spawnable
              for item_type in ItemType
              if item_type.value in zone.item_types}

    declared_types = None
    if manifest_item_types is not None:
        declared_types = tuple(manifest_item_types)
        declared_values = {row.value for row in declared_types}
        missing = sorted(row.value for row in targets | inputs
                         if row.value not in declared_values)
        if missing:
            raise SceneProfileError(
                f"工艺目录物料类型未在 manifest material_model.types "
                f"中声明: {missing}")
        if profile == PROFILE_FULL_FLOW:
            extension = {row.value for row in declared_types} \
                & set(mvp.ITEM_TYPES)
            if extension:
                raise SceneProfileError(
                    f"full_flow 工件不能声明 assembly_mvp 扩展物料 "
                    f"{sorted(extension)}")

    return SceneCapabilities(
        scene_profile=profile, source=source,
        profiles=tuple(SCENE_PROFILES), templates=templates, routes=routes,
        target_item_types=tuple(sorted(targets,
                                       key=lambda row: row.value)),
        input_item_types=tuple(sorted(inputs,
                                      key=lambda row: row.value)),
        zones=zones, artifact_id=str(artifact_id or ""))


def capabilities_from_manifest(manifest: dict, catalog: ProcessCatalog, *,
                               artifact_id: str = "") -> SceneCapabilities:
    """从工件 manifest 解析 profile 与声明物料类型并计算能力."""
    extra = _manifest_extra(manifest)
    profile, source = profile_from_manifest(manifest)
    declared = _manifest_item_types(extra)
    return capabilities_for_profile(
        profile, catalog, source=source, artifact_id=artifact_id,
        manifest_item_types=declared)


def unbound_capabilities(catalog: ProcessCatalog) -> SceneCapabilities:
    """未绑定工件时的只读目录: 全部模板/区域, 不声明任何工件可运行."""
    templates = catalog.orchestratable_operations()
    allowed = {row.id for row in templates}
    routes = tuple(
        route for recipe in catalog.recipes
        for route in recipe.route_variants
        if route.operation_ids
        and all(operation_id in allowed
                for operation_id in route.operation_ids))
    zones = _zone_capabilities(catalog, templates,
                               {zone.id for zone in catalog.zones})
    targets = {template.output.item_type for template in templates
               if template.output is not None}
    inputs = {item_type for zone in zones if zone.spawnable
              for item_type in ItemType
              if item_type.value in zone.item_types}
    return SceneCapabilities(
        scene_profile="", source=PROFILE_SOURCE_UNBOUND,
        profiles=tuple(SCENE_PROFILES), templates=tuple(templates),
        routes=routes,
        target_item_types=tuple(sorted(targets,
                                       key=lambda row: row.value)),
        input_item_types=tuple(sorted(inputs,
                                      key=lambda row: row.value)),
        zones=zones)


def order_required_profile(order: OrderSpec,
                           catalog: ProcessCatalog) -> str:
    """订单模板集合要求的最低场景 profile (MVP 模板 -> assembly_mvp)."""
    direct_ids = _direct_input_template_ids(catalog)
    if any(node.template_id in direct_ids for node in order.nodes):
        return PROFILE_ASSEMBLY_MVP
    return PROFILE_FULL_FLOW


def order_profile_diagnostics(
        order: OrderSpec, capabilities: SceneCapabilities
        ) -> tuple[OrderDiagnostic, ...]:
    """订单与已绑定 profile 的兼容性诊断; 未绑定目录不做判断."""
    if not capabilities.bound:
        return ()
    issues: list[OrderDiagnostic] = []
    allowed_templates = capabilities.allowed_template_ids
    for node in sorted(order.nodes, key=lambda row: row.node_id):
        if node.template_id not in allowed_templates:
            issues.append(OrderDiagnostic(
                "ORDER_TEMPLATE_PROFILE_MISMATCH",
                f"节点 {node.node_id!r} 模板 {node.template_id!r} 不属于"
                f"场景 profile {capabilities.scene_profile!r}",
                node_id=node.node_id))
    if not capabilities.allows_target(order.target.item_type):
        issues.append(OrderDiagnostic(
            "ORDER_TARGET_PROFILE_MISMATCH",
            f"目标物料 {order.target.item_type.value} 不在场景 profile "
            f"{capabilities.scene_profile!r} 的可生产类型中"))
    zone_ids = capabilities.zone_ids
    input_types = {row.value for row in capabilities.input_item_types}
    for row in sorted(order.inputs, key=lambda item: item.input_id):
        if row.item_type.value not in input_types:
            issues.append(OrderDiagnostic(
                "ORDER_INPUT_PROFILE_MISMATCH",
                f"投料 {row.input_id!r} 物料 {row.item_type.value} 不在场景"
                f" profile {capabilities.scene_profile!r} 的入口类型中",
                input_id=row.input_id))
        if row.zone not in zone_ids:
            issues.append(OrderDiagnostic(
                "ORDER_INPUT_ZONE_PROFILE_MISMATCH",
                f"投料 {row.input_id!r} 区域 {row.zone!r} 不属于场景 "
                f"profile {capabilities.scene_profile!r}",
                input_id=row.input_id))
    return tuple(issues)


__all__ = [
    "DEFAULT_SCENE_PROFILE", "PROFILE_ASSEMBLY_MVP", "PROFILE_FULL_FLOW",
    "PROFILE_SOURCE_DEFAULT", "PROFILE_SOURCE_MANIFEST",
    "PROFILE_SOURCE_UNBOUND", "SCENE_PROFILES", "SCENE_PROFILE_VERSION",
    "SceneCapabilities", "SceneProfileError", "SceneZoneCapability",
    "capabilities_for_profile", "capabilities_from_manifest",
    "normalize_scene_profile", "order_profile_diagnostics",
    "order_required_profile", "profile_allows_template",
    "profile_from_manifest", "unbound_capabilities",
]