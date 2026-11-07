"""persistence.importer — ``CatalogSeed`` → staging release 的显式转换器.

一次导入在单一 Session/事务内完成: 先写 release 行, 再写目录与关系表, 最后
从同一事务中重建 seed 并比较来源摘要. 任一领域/约束/往返校验失败都会回滚,
不会留下半成品 release, 也不会更新已发布指针.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..domain.materials import ItemType
from ..domain.process_model import (EXECUTOR_KINDS, PREDECESSOR_POLICIES,
                                    RESOURCE_POLICIES,
                                    WORK_STEP_TEMPLATES, OperationTemplate,
                                    ProcessModelError)
from .clock import utc_iso_now
from .db import CatalogDatabase
from .errors import (CatalogIntegrityError, ReleaseExistsError)
from .models import (Capability, CatalogRelease, Device, DeviceCapability,
                     DeviceResource, MaterialType, OperationCandidateDevice,
                     OperationDependency, OperationInput, OperationOutput,
                     OperationResourceRequest, OperationTemplate as OperationRow,
                     OperationWorkStep, OrderEntry, Recipe, Resource,
                     RouteOperation, RouteVariant, SceneBinding, Zone,
                     ZoneMaterialType)
from .records import (CatalogSeed, MaterialTypeRecord, ResourceRecord,
                      seed_digest)

_RELEASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")


@dataclass(frozen=True)
class ImportResult:
    release_id: str
    status: str
    catalog_version: str
    source_digest: str
    created_at: str
    counts: dict

    def as_dict(self) -> dict:
        return {
            "release_id": self.release_id,
            "status": self.status,
            "catalog_version": self.catalog_version,
            "source_digest": self.source_digest,
            "created_at": self.created_at,
            "counts": self.counts,
        }


def _fail(message: str) -> None:
    raise CatalogIntegrityError(message)


def _unique(values: Sequence[str], label: str) -> None:
    seen: dict[str, int] = {}
    for value in values:
        seen[value] = seen.get(value, 0) + 1
    duplicated = sorted(key for key, count in seen.items() if count > 1)
    if duplicated:
        _fail(f"{label} 重复: {duplicated}")


def _check_text(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        _fail(f"{label} 必须是非空字符串: {value!r}")
    if value != value.strip():
        _fail(f"{label} 不能含首尾空白: {value!r}")


def _check_color(row: MaterialTypeRecord) -> None:
    if row.color is None:
        return
    try:
        color = tuple(float(value) for value in row.color)
    except (TypeError, ValueError) as exc:
        raise CatalogIntegrityError(
            f"物料类型 {row.material_type_id!r} 颜色不可解析: "
            f"{row.color!r}") from exc
    if len(color) != 3:
        _fail(f"物料类型 {row.material_type_id!r} 颜色必须是 3 个分量")
    for value in color:
        if not 0.0 <= value <= 1.0:
            _fail(f"物料类型 {row.material_type_id!r} 颜色分量越界: {value}")


def _used_material_types(operations: Iterable[OperationTemplate]) -> set[str]:
    used: set[str] = set()
    for operation in operations:
        for port in operation.inputs:
            used.add(port.item_type.value)
        if operation.output is not None:
            used.add(operation.output.item_type.value)
    return used


def validate_seed(seed: CatalogSeed, *,
                  catalog_version: str = "") -> str:
    """在写库前校验 seed; 返回最终 catalog version.

    这里只做持久化边界能独立判断的检查; 配方拓扑/设备能力等跨行语义由
    ``ProcessCatalog.validate()`` 负责. 两类错误都以
    :class:`CatalogIntegrityError` 返回, 便于 CLI 给出定位.
    """
    catalog = seed.catalog
    try:
        catalog.validate()
    except ProcessModelError as exc:
        raise CatalogIntegrityError(str(exc)) from exc
    if catalog_version and catalog_version != catalog.version:
        _fail(f"catalog_version={catalog_version!r} 与目录版本 "
              f"{catalog.version!r} 不一致")
    version = catalog.version or catalog_version
    _check_text(version, "catalog version")

    material_ids = [row.material_type_id for row in seed.material_types]
    for row in seed.material_types:
        _check_text(row.material_type_id, "material_type_id")
        _check_color(row)
    _unique(material_ids, "material_type_id")
    used_types = _used_material_types(
        operation for recipe in catalog.recipes
        for operation in recipe.operations)
    known_item_types = {item.value for item in ItemType}
    unknown_types = sorted(used_types - known_item_types)
    if unknown_types:
        _fail(f"目录使用了未登记的物料枚举: {unknown_types}")
    missing_types = sorted(used_types - set(material_ids))
    if missing_types:
        _fail(f"物料类型目录缺少工序端口类型: {missing_types}")

    resource_ids = [row.resource_id for row in seed.resources]
    for row in seed.resources:
        _check_text(row.resource_id, "resource_id")
        _check_text(row.role, "resource role")
        if int(row.capacity) <= 0:
            _fail(f"资源 {row.resource_id!r} capacity 必须 > 0")
    _unique(resource_ids, "resource_id")
    orders = [int(row.acquire_order) for row in seed.resources]
    if sorted(orders) != list(range(len(orders))):
        _fail(f"资源 acquire_order 必须唯一且从 0 连续: {sorted(orders)}")
    known_resources = set(resource_ids)
    if not set(catalog.known_resources) <= known_resources:
        _fail(f"目录 known_resources 缺少资源记录: "
              f"{sorted(set(catalog.known_resources) - known_resources)}")
    for device in catalog.devices:
        if device.default_line_id and \
                device.default_line_id not in known_resources:
            _fail(f"设备 {device.id!r} 默认线 "
                  f"{device.default_line_id!r} 不在资源目录中")
        missing = [name for name in device.resources
                   if name not in known_resources]
        if missing:
            _fail(f"设备 {device.id!r} 引用未知资源: {missing}")
    for recipe in catalog.recipes:
        for operation in recipe.operations:
            missing = [name for name in operation.resources
                       if name not in known_resources]
            if missing:
                _fail(f"工序 {operation.id!r} 引用未知资源: {missing}")
            for step in operation.work_steps:
                if step.template_id not in WORK_STEP_TEMPLATES:
                    _fail(f"工序 {operation.id!r} 引用未知工步模板: "
                          f"{step.template_id!r}")
            if operation.executor_kind not in EXECUTOR_KINDS:
                _fail(f"工序 {operation.id!r} 执行器类型非法: "
                      f"{operation.executor_kind!r}")
            if operation.resource_policy not in RESOURCE_POLICIES:
                _fail(f"工序 {operation.id!r} 资源策略非法: "
                      f"{operation.resource_policy!r}")
            if operation.predecessor_policy not in PREDECESSOR_POLICIES:
                _fail(f"工序 {operation.id!r} 前置策略非法: "
                      f"{operation.predecessor_policy!r}")

    zone_ids = [zone.id for zone in catalog.zones]
    _unique(zone_ids, "zone_id")
    for zone in catalog.zones:
        _check_text(zone.id, "zone_id")
        if int(zone.capacity) <= 0:
            _fail(f"区域 {zone.id!r} capacity 必须 > 0")

    binding_ids = [row.binding_id for row in seed.scene_bindings]
    _unique(binding_ids, "binding_id")
    device_by_id = {device.id: device for device in catalog.devices}
    role_keys: set[tuple[str, str, str, str]] = set()
    alias_keys: set[tuple[str, str, str, str]] = set()
    bound_devices: set[str] = set()
    for row in seed.scene_bindings:
        for value, label in (
                (row.binding_id, "binding_id"),
                (row.device_id, "scene_binding.device_id"),
                (row.scene_id, "scene_binding.scene_id"),
                (row.contract_version, "scene_binding.contract_version"),
                (row.role, "scene_binding.role"),
                (row.alias, "scene_binding.alias")):
            _check_text(value, label)
        if "/" in row.alias:
            _fail(f"场景别名必须为扁平下划线: {row.alias!r}")
        device = device_by_id.get(row.device_id)
        if device is None:
            _fail(f"场景绑定 {row.binding_id!r} 引用未知设备 "
                  f"{row.device_id!r}")
        if not device.physical:
            _fail(f"逻辑设备 {row.device_id!r} 不应有场景对象绑定")
        bound_devices.add(row.device_id)
        role_key = (row.device_id, row.scene_id, row.contract_version,
                    row.role)
        if role_key in role_keys:
            _fail(f"设备 {row.device_id!r} 在同一场景/契约/角色重复绑定: "
                  f"{row.role!r}")
        role_keys.add(role_key)
        alias_key = (row.scene_id, row.contract_version, row.role, row.alias)
        if alias_key in alias_keys:
            _fail(f"别名 {row.alias!r} 在场景/契约/角色内重复")
        alias_keys.add(alias_key)
    unbound = sorted(device.id for device in catalog.devices
                     if device.physical and device.id not in bound_devices)
    if unbound:
        _fail(f"物理设备缺少场景绑定: {unbound}")
    return version


def _add_material_types(session: Session, release_id: str,
                        rows: Sequence[MaterialTypeRecord]) -> None:
    for ordinal, row in enumerate(rows):
        session.add(MaterialType(
            release_id=release_id, material_type_id=row.material_type_id,
            ordinal=ordinal,
            description=row.description or None,
            color_json=None if row.color is None else list(row.color)))


def _add_resources(session: Session, release_id: str,
                   rows: Sequence[ResourceRecord]) -> None:
    for row in sorted(rows, key=lambda item: int(item.acquire_order)):
        session.add(Resource(
            release_id=release_id, resource_id=row.resource_id,
            kind=row.kind, role=row.role, capacity=int(row.capacity),
            acquire_order=int(row.acquire_order), policy=row.policy))


def _add_catalog(session: Session, release_id: str,
                 seed: CatalogSeed) -> None:
    catalog = seed.catalog

    # 分层 flush: 无 ORM relationship 时, 单次 flush 不保证跨 mapper 的外键
    # 插入顺序; 每层先落库再写引用方, 仍处于同一事务.
    for ordinal, zone in enumerate(catalog.zones):
        session.add(Zone(
            release_id=release_id, zone_id=zone.id, ordinal=ordinal,
            capacity=int(zone.capacity),
            description=zone.description or None,
            spawnable=bool(zone.spawnable)))
    session.flush()

    port_types: set[tuple[str, str]] = set()
    for recipe in catalog.recipes:
        for operation in recipe.operations:
            for port in operation.inputs:
                port_types.add((port.zone, port.item_type.value))
            if operation.output is not None:
                port_types.add((operation.output.zone,
                                operation.output.item_type.value))
    for zone_id, material_type_id in sorted(port_types):
        session.add(ZoneMaterialType(
            release_id=release_id, zone_id=zone_id,
            material_type_id=material_type_id))

    for ordinal, capability in enumerate(catalog.capabilities):
        session.add(Capability(
            release_id=release_id, capability_id=capability.id,
            ordinal=ordinal, description=capability.description or None))
    session.flush()

    for ordinal, device in enumerate(catalog.devices):
        session.add(Device(
            release_id=release_id, device_id=device.id, ordinal=ordinal,
            kind=device.kind, physical=bool(device.physical),
            default_line_id=device.default_line_id or None))
    session.flush()

    for device in catalog.devices:
        for cap_ordinal, capability_id in enumerate(device.capabilities):
            session.add(DeviceCapability(
                release_id=release_id, device_id=device.id,
                capability_id=capability_id, ordinal=cap_ordinal))
        for res_ordinal, resource_id in enumerate(device.resources):
            session.add(DeviceResource(
                release_id=release_id, device_id=device.id,
                resource_id=resource_id, relation_kind="occupies",
                ordinal=res_ordinal))
    for ordinal, binding in enumerate(seed.scene_bindings):
        session.add(SceneBinding(
            release_id=release_id, binding_id=binding.binding_id,
            ordinal=ordinal, device_id=binding.device_id,
            scene_id=binding.scene_id,
            contract_version=binding.contract_version,
            role=binding.role, alias=binding.alias))
    session.flush()

    for recipe_ordinal, recipe in enumerate(catalog.recipes):
        session.add(Recipe(
            release_id=release_id, recipe_id=recipe.id,
            ordinal=recipe_ordinal, version=recipe.version,
            product_type=recipe.product_type,
            join_operation_id=recipe.join_operation_id or None))
    session.flush()

    for recipe in catalog.recipes:
        operation_index = {operation.id: operation
                           for operation in recipe.operations}
        for operation_ordinal, operation in enumerate(recipe.operations):
            session.add(OperationRow(
                release_id=release_id, operation_id=operation.id,
                recipe_id=recipe.id, ordinal=operation_ordinal,
                kind=operation.kind, semantics=operation.semantics,
                executor_kind=operation.executor_kind,
                capability_id=operation.capability,
                priority=int(operation.priority),
                resource_policy=operation.resource_policy,
                predecessor_policy=operation.predecessor_policy,
                completion_verifier=operation.completion_verifier,
                source_kind=operation.source_kind,
                sink_kind=operation.sink_kind,
                dock=operation.dock or None,
                route_origin_dock=operation.route_origin_dock or None,
                route_destination_dock=operation.route_destination_dock
                or None,
                orchestratable=bool(operation.orchestratable),
                scene_id=operation.scene_id or "",
                output_port_id=operation.output_port_id or "",
                join_handler_id=operation.join_handler_id or ""))
        session.flush()

        for operation in recipe.operations:
            for ordinal, port in enumerate(operation.inputs):
                session.add(OperationInput(
                    release_id=release_id, operation_id=operation.id,
                    ordinal=ordinal, zone_id=port.zone,
                    material_type_id=port.item_type.value,
                    port_id=port.port_id or ""))
            if operation.output is not None:
                session.add(OperationOutput(
                    release_id=release_id, operation_id=operation.id,
                    zone_id=operation.output.zone,
                    material_type_id=operation.output.item_type.value))
            for ordinal, device_id in enumerate(
                    operation.candidate_devices):
                session.add(OperationCandidateDevice(
                    release_id=release_id, operation_id=operation.id,
                    ordinal=ordinal, device_id=device_id))
            for ordinal, resource_id in enumerate(operation.resources):
                session.add(OperationResourceRequest(
                    release_id=release_id, operation_id=operation.id,
                    resource_id=resource_id, quantity=1, ordinal=ordinal))
            for ordinal, step in enumerate(operation.work_steps):
                session.add(OperationWorkStep(
                    release_id=release_id, operation_id=operation.id,
                    ordinal=ordinal, template_id=step.template_id))
            for ordinal, predecessor_id in enumerate(operation.depends_on):
                predecessor = operation_index.get(predecessor_id)
                if predecessor is None:
                    _fail(f"工序 {operation.id!r} 引用未知前置 "
                          f"{predecessor_id!r}")
                try:
                    successor_ordinal = predecessor.successors.index(
                        operation.id)
                except ValueError as exc:
                    raise CatalogIntegrityError(
                        f"工序 {operation.id!r} 不在前置 {predecessor_id!r} "
                        f"的 successors 中") from exc
                session.add(OperationDependency(
                    release_id=release_id, operation_id=operation.id,
                    predecessor_operation_id=predecessor_id,
                    ordinal=ordinal,
                    successor_ordinal=successor_ordinal))
        session.flush()

    for recipe in catalog.recipes:
        for route_ordinal, route in enumerate(recipe.route_variants):
            session.add(RouteVariant(
                release_id=release_id, recipe_id=recipe.id,
                route_id=route.id, ordinal=route_ordinal,
                route_group=route.group, semantics=route.semantics,
                entry_zone_id=route.entry_zone,
                exit_zone_id=route.exit_zone))
    session.flush()

    for recipe in catalog.recipes:
        for route in recipe.route_variants:
            for ordinal, operation_id in enumerate(route.operation_ids):
                session.add(RouteOperation(
                    release_id=release_id, recipe_id=recipe.id,
                    route_id=route.id, ordinal=ordinal,
                    operation_id=operation_id))
        for entry_ordinal, entry in enumerate(recipe.entries):
            session.add(OrderEntry(
                release_id=release_id, recipe_id=recipe.id,
                entry_id=entry.id, ordinal=entry_ordinal,
                operation_id=entry.operation_id, line_id=entry.line,
                material_type_id=entry.item_type.value,
                route_group=entry.route_group or None,
                route_id=entry.route_variant_id or None))
    session.flush()


class CatalogImporter:
    """把已校验的 seed 写入 staging release; 不做发布."""

    @staticmethod
    def stage(database: CatalogDatabase, seed: CatalogSeed, *,
              release_id: str, catalog_version: str = "") -> ImportResult:
        _check_text(release_id, "release_id")
        if not _RELEASE_ID.match(release_id):
            raise CatalogIntegrityError(
                f"release_id={release_id!r} 只允许字母/数字/_.:-")
        version = validate_seed(seed, catalog_version=catalog_version)
        digest = seed_digest(seed)
        created_at = utc_iso_now()

        with database.session() as session:
            try:
                with session.begin():
                    if session.get(CatalogRelease, release_id) is not None:
                        raise ReleaseExistsError(
                            f"release {release_id!r} 已存在; 目录发布不原地"
                            f"改写, 请使用新的 release_id")
                    session.add(CatalogRelease(
                        release_id=release_id, catalog_version=version,
                        status="staging", source_digest=digest,
                        created_at=created_at, published_at=None))
                    session.flush()
                    _add_material_types(session, release_id,
                                        seed.material_types)
                    _add_resources(session, release_id, seed.resources)
                    _add_catalog(session, release_id, seed)
                    session.flush()

                    # 同一事务内重建 seed: 转换器有损/约束缺失都会在这里暴露.
                    from .repository import CatalogRepository
                    rebuilt = CatalogRepository(
                        session, release_id=release_id).load_seed()
                    if seed_digest(rebuilt) != digest:
                        raise CatalogIntegrityError(
                            "目录往返摘要不一致: 数据库内容与导入 seed 不等价")
                    counts = CatalogRepository(
                        session, release_id=release_id).counts()
            except IntegrityError as exc:
                raise CatalogIntegrityError(
                    f"数据库约束拒绝导入 release {release_id!r}: "
                    f"{exc.orig}") from exc
        return ImportResult(release_id=release_id, status="staging",
                            catalog_version=version, source_digest=digest,
                            created_at=created_at, counts=counts)
