"""persistence.records — 目录 seed 的纯数据记录与稳定摘要.

``ProcessCatalog`` 覆盖配方/工序/路线/设备/能力/区域; 物料类型、互斥资源与
场景绑定属于其他领域/场景来源, 由 application 层装配成这里的记录. 记录只含
稳定业务 ID 与静态属性, 不含句柄、owner、等待队列或运行时刻.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..domain.process_model import ProcessCatalog
from ..util import stable_digest


@dataclass(frozen=True)
class MaterialTypeRecord:
    """一种物料类型; ``color`` 是 0..1 的三元 RGB, 可为空."""

    material_type_id: str
    description: str = ""
    color: Optional[tuple[float, float, float]] = None

    def as_dict(self) -> dict:
        return {
            "material_type_id": self.material_type_id,
            "description": self.description,
            "color": None if self.color is None else list(self.color),
        }


@dataclass(frozen=True)
class ResourceRecord:
    """固定顺序互斥资源; ``acquire_order`` 是业务顺序, 不是数组下标推断."""

    resource_id: str
    kind: str
    role: str
    capacity: int = 1
    acquire_order: int = 0
    policy: str = "exclusive"

    def as_dict(self) -> dict:
        return {
            "resource_id": self.resource_id,
            "kind": self.kind,
            "role": self.role,
            "capacity": int(self.capacity),
            "acquire_order": int(self.acquire_order),
            "policy": self.policy,
        }


@dataclass(frozen=True)
class SceneBindingRecord:
    """稳定设备 ID 到场景别名的绑定; 不保存 CoppeliaSim handle."""

    binding_id: str
    device_id: str
    scene_id: str
    contract_version: str
    role: str
    alias: str

    def as_dict(self) -> dict:
        return {
            "binding_id": self.binding_id,
            "device_id": self.device_id,
            "scene_id": self.scene_id,
            "contract_version": self.contract_version,
            "role": self.role,
            "alias": self.alias,
        }


@dataclass(frozen=True)
class CatalogSeed:
    """一次静态目录发布的完整来源; 导入按此对象单事务写入."""

    catalog: ProcessCatalog
    material_types: tuple[MaterialTypeRecord, ...]
    resources: tuple[ResourceRecord, ...]
    scene_bindings: tuple[SceneBindingRecord, ...]

    def as_dict(self) -> dict:
        return {
            "catalog": self.catalog.as_dict(),
            "material_types": [row.as_dict()
                               for row in self.material_types],
            "resources": [row.as_dict() for row in self.resources],
            "scene_bindings": [row.as_dict()
                               for row in self.scene_bindings],
        }


def seed_digest(seed: CatalogSeed) -> str:
    """规范化来源摘要; 语义顺序保留, 映射键排序由 stable_digest 处理."""
    return stable_digest(seed.as_dict())
