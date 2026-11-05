"""application.catalog — 静态目录 seed 装配与持久化用例.

本模块是 persistence 与领域/场景来源之间的唯一适配层:

  - 物料类型来自 ``domain.rules``;
  - 配方/设备/能力/区域来自 ``domain.default_process``;
  - 资源分类来自本模块的显式表 (不从资源字符串前缀推断);
  - 场景绑定来自 ``simulation.device_binding`` 的稳定 role/alias 表;
  - 时间戳、摘要和事务由 ``persistence`` 处理.

``persistence`` 不导入 ``simulation``; ``domain`` 不导入本模块或 SQLAlchemy.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .. import spec
from ..domain import rules
from ..domain.default_process import default_catalog
from ..domain.process_model import ProcessCatalog
from ..persistence.db import CatalogDatabase
from ..persistence.diff import catalog_diff, shadow_report
from ..persistence.errors import CatalogIntegrityError
from ..persistence.records import (CatalogSeed, MaterialTypeRecord,
                                   ResourceRecord, SceneBindingRecord)
from ..persistence.service import load_catalog
from ..persistence.service import publish_release as _publish_release
from ..persistence.service import release_status, stage_catalog
from ..persistence.service import verify_database
from ..simulation.device_binding import device_bindings

# 资源语义分类: 显式维护, 不按 ID 前缀推断. RESOURCE_ORDER/ROLES 变化时
# 同步更新; 缺失或多余条目会在装配 seed 时拒绝.
_RESOURCE_KINDS = {
    "line_s1a": "line",
    "line_s1b": "line",
    "line_s2d": "line",
    "line_ce": "line",
    "handoff_ac": "handoff",
    "buffer_e_c": "buffer",
    "buffer_e_d": "buffer",
    "agv_lane_west": "lane",
    "agv_lane_south": "lane",
    "agv_lane_east": "lane",
    "dock_A": "dock",
    "dock_B": "dock",
    "dock_D": "dock",
    "dock_E": "dock",
    "zone_ab": "interlock",
    "zone_ac": "interlock",
    "arm_A": "arm_envelope",
    "arm_B": "arm_envelope",
    "arm_C": "arm_envelope",
    "arm_D": "arm_envelope",
    "arm_E": "arm_envelope",
}

_RESOURCE_CAPACITY = 1
_RESOURCE_POLICY = "exclusive"


def material_type_records() -> tuple[MaterialTypeRecord, ...]:
    rows = []
    for item_type in rules.ITEM_TYPES:
        color = rules.ITEM_COLORS.get(item_type)
        rows.append(MaterialTypeRecord(
            material_type_id=item_type,
            description="",
            color=None if color is None else tuple(
                float(value) for value in color)))
    return tuple(rows)


def resource_records() -> tuple[ResourceRecord, ...]:
    ordered = tuple(rules.RESOURCE_ORDER)
    missing = sorted(set(ordered) - set(_RESOURCE_KINDS))
    unexpected = sorted(set(_RESOURCE_KINDS) - set(ordered))
    if missing or unexpected:
        raise CatalogIntegrityError(
            "资源分类表与 RESOURCE_ORDER 不一致: "
            f"missing={missing} unexpected={unexpected}")
    return tuple(
        ResourceRecord(
            resource_id=resource_id,
            kind=_RESOURCE_KINDS[resource_id],
            role=rules.RESOURCE_ROLES.get(resource_id, ""),
            capacity=_RESOURCE_CAPACITY,
            acquire_order=index,
            policy=_RESOURCE_POLICY)
        for index, resource_id in enumerate(ordered))


def scene_binding_records(
        catalog: Optional[ProcessCatalog] = None, *,
        scene_id: str = spec.LAYOUT_NAME,
        contract_version: str = spec.SCENE_CONTRACT_VERSION) -> \
        tuple[SceneBindingRecord, ...]:
    catalog = catalog or default_catalog()
    device_ids = {device.id for device in catalog.devices}
    rows: list[SceneBindingRecord] = []
    for binding in device_bindings():
        if binding.device_id not in device_ids:
            raise CatalogIntegrityError(
                f"场景绑定 {binding.device_id!r} 不在设备目录中")
        if len(binding.alias_roles) != len(binding.aliases):
            raise CatalogIntegrityError(
                f"设备 {binding.device_id!r} 的 alias_roles/aliases 数量"
                f"不一致")
        for role, alias in zip(binding.alias_roles, binding.aliases):
            rows.append(SceneBindingRecord(
                binding_id=f"{binding.device_id}:{role}",
                device_id=binding.device_id,
                scene_id=scene_id,
                contract_version=contract_version,
                role=role,
                alias=alias))
    return tuple(rows)


def default_seed(catalog: Optional[ProcessCatalog] = None) -> CatalogSeed:
    catalog = catalog or default_catalog()
    return CatalogSeed(
        catalog=catalog,
        material_types=material_type_records(),
        resources=resource_records(),
        scene_bindings=scene_binding_records(catalog))


def default_release_id(catalog: Optional[ProcessCatalog] = None) -> str:
    catalog = catalog or default_catalog()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"rel_{catalog.version}_{stamp}"


def stage_default_catalog(database: CatalogDatabase, *,
                          release_id: str = "",
                          catalog_version: str = "") -> dict:
    seed = default_seed()
    release_id = release_id or default_release_id(seed.catalog)
    return stage_catalog(database, seed, release_id=release_id,
                         catalog_version=catalog_version).as_dict()


def shadow_compare(database: CatalogDatabase,
                   release_id: Optional[str] = None) -> dict:
    """并行构建代码目录与数据库目录, 返回逐路径差异 (不切换运行时)."""
    code_catalog = default_catalog()
    try:
        database_catalog = load_catalog(database, release_id)
    except Exception as exc:
        return shadow_report(
            code_catalog, None,
            error=f"{getattr(exc, 'code', type(exc).__name__)}: {exc}")
    return shadow_report(code_catalog, database_catalog)


def runtime_catalog(database: CatalogDatabase,
                    release_id: Optional[str] = None) -> ProcessCatalog:
    """显式选库时的运行时目录; 必须与当前代码目录完全等价."""
    code_catalog = default_catalog()
    database_catalog = load_catalog(database, release_id)
    if database_catalog.version != code_catalog.version:
        raise CatalogIntegrityError(
            f"数据库目录版本 {database_catalog.version!r} 与当前代码目录 "
            f"{code_catalog.version!r} 不一致")
    differences = catalog_diff(code_catalog, database_catalog)
    if differences:
        raise CatalogIntegrityError(
            "数据库目录与当前代码目录不等价, 拒绝切换运行时: "
            f"{differences[:5]}")
    return database_catalog


def catalog_status(database: CatalogDatabase,
                   release_id: Optional[str] = None) -> dict:
    return release_status(database, release_id)


def publish_release(database: CatalogDatabase, release_id: str) -> dict:
    return _publish_release(database, release_id)


def catalog_verify(database: CatalogDatabase,
                   release_id: Optional[str] = None) -> dict:
    return verify_database(database, release_id)


__all__ = [
    "material_type_records", "resource_records", "scene_binding_records",
    "default_seed", "default_release_id", "stage_default_catalog",
    "shadow_compare", "runtime_catalog", "catalog_status", "catalog_verify",
    "publish_release",
]
