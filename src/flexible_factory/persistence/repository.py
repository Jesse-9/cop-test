"""persistence.repository — 已发布 release 到领域对象的只读仓储.

ORM 行只在本模块内出现; 返回值是 :class:`ProcessCatalog` 与
:class:`CatalogSeed` 领域数据. 读取时重新执行领域校验、来源摘要校验和
派生关系校验, 任何不一致都抛 :class:`CatalogIntegrityError`, 不返回
"看似可用"的空目录.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain.materials import ItemType
from ..domain.process_model import (CapabilitySpec, DeviceSpec, MaterialPort,
                                    OperationTemplate, OrderEntry,
                                    ProcessCatalog, ProcessModelError,
                                    RecipeSpec, RouteVariant, WorkStepTemplateRef,
                                    ZoneSpec)
from .errors import (CatalogIntegrityError, NoPublishedReleaseError,
                     ReleaseNotFoundError)
from .models import (Capability, CatalogRelease, Device, DeviceCapability,
                     DeviceResource, MaterialType, OperationCandidateDevice,
                     OperationDependency, OperationInput, OperationOutput,
                     OperationResourceRequest,
                     OperationTemplate as OperationRow, OperationWorkStep,
                     OrderEntry as OrderEntryRow, Recipe, Resource,
                     RouteOperation, RouteVariant as RouteVariantRow,
                     SceneBinding, Zone, ZoneMaterialType)
from .records import (CatalogSeed, MaterialTypeRecord, ResourceRecord,
                      SceneBindingRecord, seed_digest)


def _text(value: Optional[str]) -> str:
    return "" if value is None else str(value)


class CatalogRepository:
    """按 release 读取目录; ``release_id=None`` 表示当前已发布版本."""

    def __init__(self, session: Session, release_id: Optional[str] = None):
        self.session = session
        if release_id is None:
            release = session.scalars(select(CatalogRelease).where(
                CatalogRelease.status == "published")).one_or_none()
            if release is None:
                raise NoPublishedReleaseError(
                    "数据库中没有已发布目录; 先导入并发布 release")
            release_id = release.release_id
        self.release_id = str(release_id)
        self._release = session.get(CatalogRelease, self.release_id)
        if self._release is None:
            raise ReleaseNotFoundError(
                f"release {self.release_id!r} 不存在")

    @property
    def release(self) -> CatalogRelease:
        assert self._release is not None
        return self._release

    def _rows(self, model, *order_by) -> list:
        statement = select(model).where(
            model.release_id == self.release_id)
        if order_by:
            statement = statement.order_by(*order_by)
        return list(self.session.scalars(statement))

    # ── seed 重建 ───────────────────────────────────────────

    def load_seed(self) -> CatalogSeed:
        self.release  # 触发存在性检查
        material_types = tuple(
            MaterialTypeRecord(
                material_type_id=row.material_type_id,
                description=_text(row.description),
                color=None if row.color_json is None else tuple(
                    float(value) for value in row.color_json))
            for row in self._rows(MaterialType, MaterialType.ordinal))
        unknown_types = sorted(
            row.material_type_id for row in material_types
            if row.material_type_id not in {item.value for item in ItemType})
        if unknown_types:
            raise CatalogIntegrityError(
                f"数据库包含领域未登记的物料类型: {unknown_types}")
        resources = tuple(
            ResourceRecord(
                resource_id=row.resource_id, kind=row.kind, role=row.role,
                capacity=int(row.capacity),
                acquire_order=int(row.acquire_order), policy=row.policy)
            for row in self._rows(Resource, Resource.acquire_order))
        scene_bindings = tuple(
            SceneBindingRecord(
                binding_id=row.binding_id, device_id=row.device_id,
                scene_id=row.scene_id,
                contract_version=row.contract_version,
                role=row.role, alias=row.alias)
            for row in self._rows(SceneBinding, SceneBinding.ordinal))

        catalog = self._build_catalog(resources)
        self._verify_zone_material_types(catalog)
        seed = CatalogSeed(catalog=catalog,
                           material_types=material_types,
                           resources=resources,
                           scene_bindings=scene_bindings)
        actual = seed_digest(seed)
        if actual != self.release.source_digest:
            raise CatalogIntegrityError(
                f"release {self.release_id!r} 往返摘要不一致: "
                f"source={self.release.source_digest} actual={actual}")
        return seed

    def load_catalog(self) -> ProcessCatalog:
        return self.load_seed().catalog

    # ── 目录构造 ────────────────────────────────────────────

    def _build_catalog(self, resources: Sequence[ResourceRecord]) -> \
            ProcessCatalog:
        capabilities = tuple(
            CapabilitySpec(id=row.capability_id,
                           description=_text(row.description))
            for row in self._rows(Capability, Capability.ordinal))

        capability_rows: dict[str, list[str]] = defaultdict(list)
        for row in self._rows(DeviceCapability,
                              DeviceCapability.device_id,
                              DeviceCapability.ordinal):
            capability_rows[row.device_id].append(row.capability_id)
        resource_rows: dict[str, list[str]] = defaultdict(list)
        for row in self._rows(DeviceResource, DeviceResource.device_id,
                              DeviceResource.ordinal):
            resource_rows[row.device_id].append(row.resource_id)
        devices = tuple(
            DeviceSpec(id=row.device_id, kind=row.kind,
                       capabilities=tuple(capability_rows[row.device_id]),
                       resources=tuple(resource_rows[row.device_id]),
                       default_line_id=_text(row.default_line_id),
                       physical=bool(row.physical))
            for row in self._rows(Device, Device.ordinal))
        zones = tuple(
            ZoneSpec(id=row.zone_id, capacity=int(row.capacity),
                     description=_text(row.description))
            for row in self._rows(Zone, Zone.ordinal))

        operation_rows = self._rows(OperationRow,
                                    OperationRow.recipe_id,
                                    OperationRow.ordinal)
        operations_by_recipe: dict[str, list[OperationRow]] = defaultdict(list)
        for row in operation_rows:
            operations_by_recipe[row.recipe_id].append(row)

        input_rows: dict[str, list[OperationInput]] = defaultdict(list)
        for row in self._rows(OperationInput, OperationInput.operation_id,
                              OperationInput.ordinal):
            input_rows[row.operation_id].append(row)
        output_rows = {row.operation_id: row
                       for row in self._rows(OperationOutput)}
        candidate_rows: dict[str, list[str]] = defaultdict(list)
        for row in self._rows(OperationCandidateDevice,
                              OperationCandidateDevice.operation_id,
                              OperationCandidateDevice.ordinal):
            candidate_rows[row.operation_id].append(row.device_id)
        request_rows: dict[str, list[str]] = defaultdict(list)
        for row in self._rows(OperationResourceRequest,
                              OperationResourceRequest.operation_id,
                              OperationResourceRequest.ordinal):
            if int(row.quantity) != 1:
                raise CatalogIntegrityError(
                    f"operation_resource_request.quantity 目前只支持 1: "
                    f"{row.operation_id}/{row.resource_id}={row.quantity}")
            request_rows[row.operation_id].append(row.resource_id)
        step_rows: dict[str, list[str]] = defaultdict(list)
        for row in self._rows(OperationWorkStep,
                              OperationWorkStep.operation_id,
                              OperationWorkStep.ordinal):
            step_rows[row.operation_id].append(row.template_id)
        dependency_rows = self._rows(
            OperationDependency, OperationDependency.operation_id,
            OperationDependency.ordinal)
        depends_on_rows: dict[str, list[str]] = defaultdict(list)
        successor_rows: dict[str, list[OperationDependency]] = defaultdict(list)
        for row in dependency_rows:
            depends_on_rows[row.operation_id].append(
                row.predecessor_operation_id)
            successor_rows[row.predecessor_operation_id].append(row)
        for rows in successor_rows.values():
            rows.sort(key=lambda row: int(row.successor_ordinal))

        operations_by_id: dict[str, OperationTemplate] = {}
        for row in operation_rows:
            output = output_rows.get(row.operation_id)
            try:
                operation = OperationTemplate(
                    id=row.operation_id, kind=row.kind,
                    semantics=row.semantics,
                    executor_kind=row.executor_kind,
                    capability=row.capability_id,
                    candidate_devices=tuple(
                        candidate_rows[row.operation_id]),
                    inputs=tuple(
                        MaterialPort(zone=port.zone_id,
                                    item_type=ItemType(port.material_type_id))
                        for port in input_rows[row.operation_id]),
                    output=None if output is None else MaterialPort(
                        zone=output.zone_id,
                        item_type=ItemType(output.material_type_id)),
                    source_kind=row.source_kind,
                    sink_kind=row.sink_kind,
                    resources=tuple(request_rows[row.operation_id]),
                    resource_policy=row.resource_policy,
                    work_steps=tuple(
                        WorkStepTemplateRef(template_id)
                        for template_id in step_rows[row.operation_id]),
                    completion_verifier=row.completion_verifier,
                    priority=int(row.priority),
                    depends_on=tuple(depends_on_rows[row.operation_id]),
                    predecessor_policy=row.predecessor_policy,
                    successors=tuple(
                        item.operation_id
                        for item in successor_rows[row.operation_id]),
                    dock=_text(row.dock),
                    route_origin_dock=_text(row.route_origin_dock),
                    route_destination_dock=_text(
                        row.route_destination_dock))
            except ValueError as exc:
                raise CatalogIntegrityError(
                    f"工序 {row.operation_id!r} 物料类型非法: {exc}") from exc
            operations_by_id[row.operation_id] = operation

        route_rows: dict[str, list[RouteVariantRow]] = defaultdict(list)
        for row in self._rows(RouteVariantRow, RouteVariantRow.recipe_id,
                              RouteVariantRow.ordinal):
            route_rows[row.recipe_id].append(row)
        route_operation_rows: dict[tuple[str, str], list[str]] = defaultdict(
            list)
        for row in self._rows(RouteOperation, RouteOperation.recipe_id,
                              RouteOperation.route_id,
                              RouteOperation.ordinal):
            route_operation_rows[(row.recipe_id, row.route_id)].append(
                row.operation_id)
        entry_rows: dict[str, list[OrderEntryRow]] = defaultdict(list)
        for row in self._rows(OrderEntryRow, OrderEntryRow.recipe_id,
                              OrderEntryRow.ordinal):
            entry_rows[row.recipe_id].append(row)

        recipes: list[RecipeSpec] = []
        for row in self._rows(Recipe, Recipe.ordinal):
            recipe_operations = tuple(
                operations_by_id[operation.operation_id]
                for operation in operations_by_recipe[row.recipe_id])
            route_variants = tuple(
                RouteVariant(
                    id=route.route_id, group=route.route_group,
                    semantics=route.semantics,
                    operation_ids=tuple(route_operation_rows[
                        (row.recipe_id, route.route_id)]),
                    entry_zone=route.entry_zone_id,
                    exit_zone=route.exit_zone_id)
                for route in route_rows[row.recipe_id])
            entries = tuple(
                OrderEntry(
                    id=entry.entry_id, operation_id=entry.operation_id,
                    line=entry.line_id,
                    item_type=ItemType(entry.material_type_id),
                    route_group=_text(entry.route_group),
                    route_variant_id=_text(entry.route_id))
                for entry in entry_rows[row.recipe_id])
            recipes.append(RecipeSpec(
                id=row.recipe_id, version=row.version,
                product_type=row.product_type,
                operations=recipe_operations,
                route_variants=route_variants,
                entries=entries,
                join_operation_id=_text(row.join_operation_id)))

        catalog = ProcessCatalog(
            recipes=tuple(recipes), capabilities=capabilities,
            devices=devices, zones=zones,
            known_resources=tuple(row.resource_id for row in resources),
            version=self.release.catalog_version)
        try:
            catalog.validate()
        except ProcessModelError as exc:
            raise CatalogIntegrityError(
                f"release {self.release_id!r} 领域校验失败: {exc}") from exc
        return catalog

    def _verify_zone_material_types(self, catalog: ProcessCatalog) -> None:
        expected: set[tuple[str, str]] = set()
        for recipe in catalog.recipes:
            for operation in recipe.operations:
                for port in operation.inputs:
                    expected.add((port.zone, port.item_type.value))
                if operation.output is not None:
                    expected.add((operation.output.zone,
                                  operation.output.item_type.value))
        stored = {(row.zone_id, row.material_type_id)
                  for row in self._rows(ZoneMaterialType)}
        if stored != expected:
            raise CatalogIntegrityError(
                "zone_material_type 与工序端口不一致: "
                f"missing={sorted(expected - stored)} "
                f"unexpected={sorted(stored - expected)}")

    # ── 统计 ────────────────────────────────────────────────

    def counts(self) -> dict:
        models = {
            "material_types": MaterialType,
            "resources": Resource,
            "zones": Zone,
            "zone_material_types": ZoneMaterialType,
            "capabilities": Capability,
            "devices": Device,
            "device_capabilities": DeviceCapability,
            "device_resources": DeviceResource,
            "scene_bindings": SceneBinding,
            "recipes": Recipe,
            "operations": OperationRow,
            "operation_dependencies": OperationDependency,
            "operation_inputs": OperationInput,
            "operation_outputs": OperationOutput,
            "operation_candidate_devices": OperationCandidateDevice,
            "operation_resource_requests": OperationResourceRequest,
            "operation_work_steps": OperationWorkStep,
            "route_variants": RouteVariantRow,
            "route_operations": RouteOperation,
            "order_entries": OrderEntryRow,
        }
        counts = {"release": 1}
        for label, model in models.items():
            counts[label] = int(self.session.scalar(
                select(func.count()).select_from(model).where(
                    model.release_id == self.release_id)) or 0)
        return counts


def list_releases(session: Session) -> list[dict]:
    """按创建时间列出 release; 不读取子表."""
    rows = session.scalars(select(CatalogRelease).order_by(
        CatalogRelease.created_at, CatalogRelease.release_id)).all()
    return [{
        "release_id": row.release_id,
        "catalog_version": row.catalog_version,
        "status": row.status,
        "source_digest": row.source_digest,
        "created_at": row.created_at,
        "published_at": row.published_at,
    } for row in rows]
