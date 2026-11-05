"""persistence.models — 静态资源/工艺目录的 SQLAlchemy 2.x ORM 映射.

Schema 由 Alembic revision 管理; 本模块只描述表和约束, 不执行 DDL. 所有
release 子表的复合主键都以 ``release_id`` 开头, 使外键天然限制在同一发布
版本内. 约束分两层:

  - 数据库层: NOT NULL、主键/唯一键、外键、CHECK 范围与受控枚举;
  - 领域层: 跨行语义 (路线互斥、工序无环、物料连续性、候选设备能力等),
    由 :class:`~flexible_factory.domain.process_model.ProcessCatalog` 校验.

``recipe.join_operation_id`` 故意不建外键: ``recipe`` 与
``operation_template`` 互相引用会形成建表环; 合流引用由领域校验和导入器
在发布前核对, schema 变更时不得把它当作已约束字段.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import (JSON, Boolean, CheckConstraint, ForeignKeyConstraint,
                        Index, Integer, MetaData, Text, UniqueConstraint, text)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

_NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# 受控枚举 (schema 与领域常量的一致性由导入/读取校验保证).
RELEASE_STATUSES = ("staging", "published", "superseded")
DEVICE_KINDS = ("arm", "agv", "logic")
RESOURCE_KINDS = ("line", "handoff", "buffer", "lane", "dock", "interlock",
                  "arm_envelope")
RESOURCE_POLICIES = ("exclusive",)
RELATION_KINDS = ("occupies", "associates")
EXECUTOR_KINDS = ("arm", "agv", "logic")
MATERIAL_FLOW_KINDS = ("conveyor", "agv", "station", "none")
RESOURCE_POLICIES_OPERATION = ("fixed", "agv_route", "logical_join")
PREDECESSOR_POLICIES = ("all", "any")


def _non_empty(column: str) -> CheckConstraint:
    return CheckConstraint(f"length(trim({column})) > 0",
                           name=f"{column}_nonempty")


def _in(column: str, values: tuple[str, ...]) -> CheckConstraint:
    quoted = ", ".join(f"'{value}'" for value in values)
    return CheckConstraint(f"{column} IN ({quoted})",
                           name=f"{column}_enum")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=_NAMING)


# ═══════════════════════════════════════════════════════════════
# 发布
# ═══════════════════════════════════════════════════════════════

class CatalogRelease(Base):
    """一次目录导入/发布; ``staging`` 通过校验后才能原子发布."""

    __tablename__ = "catalog_release"
    __table_args__ = (
        _non_empty("release_id"),
        _non_empty("catalog_version"),
        _non_empty("created_at"),
        _in("status", RELEASE_STATUSES),
        CheckConstraint("length(source_digest) = 64",
                        name="source_digest_sha256"),
        CheckConstraint(
            "(status = 'staging' AND published_at IS NULL) OR "
            "(status IN ('published', 'superseded') "
            "AND published_at IS NOT NULL)",
            name="published_at_consistent"),
        # 同值唯一索引: 数据库最多允许一个 published 行.
        Index("ux_catalog_release_single_published", "status", unique=True,
              sqlite_where=text("status = 'published'")),
    )

    release_id: Mapped[str] = mapped_column(Text, primary_key=True)
    catalog_version: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    source_digest: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[Optional[str]] = mapped_column(Text)


# ═══════════════════════════════════════════════════════════════
# 物料类型 / 资源 / 区域 / 设备
# ═══════════════════════════════════════════════════════════════

class MaterialType(Base):
    __tablename__ = "material_type"
    __table_args__ = (
        _non_empty("material_type_id"),
        CheckConstraint("ordinal >= 0", name="ordinal_nonnegative"),
        UniqueConstraint("release_id", "ordinal",
                         name="uq_material_type_release_ordinal"),
        ForeignKeyConstraint(["release_id"], ["catalog_release.release_id"],
                             ondelete="RESTRICT",
                             name="fk_material_type_release"),
    )

    release_id: Mapped[str] = mapped_column(Text, primary_key=True)
    material_type_id: Mapped[str] = mapped_column(Text, primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    color_json: Mapped[Optional[list]] = mapped_column(JSON)


class Resource(Base):
    __tablename__ = "resource"
    __table_args__ = (
        _non_empty("resource_id"),
        _non_empty("role"),
        _in("kind", RESOURCE_KINDS),
        _in("policy", RESOURCE_POLICIES),
        CheckConstraint("capacity > 0", name="capacity_positive"),
        CheckConstraint("acquire_order >= 0", name="acquire_order_nonnegative"),
        UniqueConstraint("release_id", "acquire_order",
                         name="uq_resource_release_acquire_order"),
        ForeignKeyConstraint(["release_id"], ["catalog_release.release_id"],
                             ondelete="RESTRICT", name="fk_resource_release"),
    )

    release_id: Mapped[str] = mapped_column(Text, primary_key=True)
    resource_id: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    acquire_order: Mapped[int] = mapped_column(Integer, nullable=False)
    policy: Mapped[str] = mapped_column(Text, nullable=False)


class Zone(Base):
    __tablename__ = "zone"
    __table_args__ = (
        _non_empty("zone_id"),
        CheckConstraint("capacity > 0", name="capacity_positive"),
        CheckConstraint("ordinal >= 0", name="ordinal_nonnegative"),
        UniqueConstraint("release_id", "ordinal",
                         name="uq_zone_release_ordinal"),
        ForeignKeyConstraint(["release_id"], ["catalog_release.release_id"],
                             ondelete="RESTRICT", name="fk_zone_release"),
    )

    release_id: Mapped[str] = mapped_column(Text, primary_key=True)
    zone_id: Mapped[str] = mapped_column(Text, primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)


class ZoneMaterialType(Base):
    __tablename__ = "zone_material_type"
    __table_args__ = (
        ForeignKeyConstraint(["release_id", "zone_id"],
                             ["zone.release_id", "zone.zone_id"],
                             ondelete="RESTRICT",
                             name="fk_zone_material_type_zone"),
        ForeignKeyConstraint(
            ["release_id", "material_type_id"],
            ["material_type.release_id", "material_type.material_type_id"],
            ondelete="RESTRICT", name="fk_zone_material_type_material"),
    )

    release_id: Mapped[str] = mapped_column(Text, primary_key=True)
    zone_id: Mapped[str] = mapped_column(Text, primary_key=True)
    material_type_id: Mapped[str] = mapped_column(Text, primary_key=True)


class Capability(Base):
    __tablename__ = "capability"
    __table_args__ = (
        _non_empty("capability_id"),
        CheckConstraint("ordinal >= 0", name="ordinal_nonnegative"),
        UniqueConstraint("release_id", "ordinal",
                         name="uq_capability_release_ordinal"),
        ForeignKeyConstraint(["release_id"], ["catalog_release.release_id"],
                             ondelete="RESTRICT",
                             name="fk_capability_release"),
    )

    release_id: Mapped[str] = mapped_column(Text, primary_key=True)
    capability_id: Mapped[str] = mapped_column(Text, primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)


class Device(Base):
    __tablename__ = "device"
    __table_args__ = (
        _non_empty("device_id"),
        _in("kind", DEVICE_KINDS),
        CheckConstraint("physical IN (0, 1)", name="physical_boolean"),
        CheckConstraint("ordinal >= 0", name="ordinal_nonnegative"),
        UniqueConstraint("release_id", "ordinal",
                         name="uq_device_release_ordinal"),
        ForeignKeyConstraint(["release_id"], ["catalog_release.release_id"],
                             ondelete="RESTRICT", name="fk_device_release"),
        ForeignKeyConstraint(
            ["release_id", "default_line_id"],
            ["resource.release_id", "resource.resource_id"],
            ondelete="RESTRICT", name="fk_device_default_line"),
    )

    release_id: Mapped[str] = mapped_column(Text, primary_key=True)
    device_id: Mapped[str] = mapped_column(Text, primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    physical: Mapped[bool] = mapped_column(Boolean, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    default_line_id: Mapped[Optional[str]] = mapped_column(Text)


class DeviceCapability(Base):
    __tablename__ = "device_capability"
    __table_args__ = (
        CheckConstraint("ordinal >= 0", name="ordinal_nonnegative"),
        UniqueConstraint("release_id", "device_id", "ordinal",
                         name="uq_device_capability_device_ordinal"),
        ForeignKeyConstraint(["release_id", "device_id"],
                             ["device.release_id", "device.device_id"],
                             ondelete="RESTRICT",
                             name="fk_device_capability_device"),
        ForeignKeyConstraint(
            ["release_id", "capability_id"],
            ["capability.release_id", "capability.capability_id"],
            ondelete="RESTRICT", name="fk_device_capability_capability"),
    )

    release_id: Mapped[str] = mapped_column(Text, primary_key=True)
    device_id: Mapped[str] = mapped_column(Text, primary_key=True)
    capability_id: Mapped[str] = mapped_column(Text, primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)


class DeviceResource(Base):
    __tablename__ = "device_resource"
    __table_args__ = (
        _in("relation_kind", RELATION_KINDS),
        CheckConstraint("ordinal >= 0", name="ordinal_nonnegative"),
        UniqueConstraint("release_id", "device_id", "ordinal",
                         name="uq_device_resource_device_ordinal"),
        ForeignKeyConstraint(["release_id", "device_id"],
                             ["device.release_id", "device.device_id"],
                             ondelete="RESTRICT",
                             name="fk_device_resource_device"),
        ForeignKeyConstraint(["release_id", "resource_id"],
                             ["resource.release_id", "resource.resource_id"],
                             ondelete="RESTRICT",
                             name="fk_device_resource_resource"),
    )

    release_id: Mapped[str] = mapped_column(Text, primary_key=True)
    device_id: Mapped[str] = mapped_column(Text, primary_key=True)
    resource_id: Mapped[str] = mapped_column(Text, primary_key=True)
    relation_kind: Mapped[str] = mapped_column(Text, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)


class SceneBinding(Base):
    """设备到场景对象的稳定绑定; alias 在同一场景/契约/角色内唯一."""

    __tablename__ = "scene_binding"
    __table_args__ = (
        _non_empty("binding_id"),
        _non_empty("scene_id"),
        _non_empty("contract_version"),
        _non_empty("role"),
        _non_empty("alias"),
        CheckConstraint("instr(alias, '/') = 0", name="alias_flat"),
        CheckConstraint("ordinal >= 0", name="ordinal_nonnegative"),
        UniqueConstraint("release_id", "ordinal",
                         name="uq_scene_binding_release_ordinal"),
        UniqueConstraint("release_id", "device_id", "scene_id",
                         "contract_version", "role",
                         name="uq_scene_binding_device_role"),
        UniqueConstraint("release_id", "scene_id", "contract_version", "role",
                         "alias", name="uq_scene_binding_scene_role_alias"),
        ForeignKeyConstraint(["release_id", "device_id"],
                             ["device.release_id", "device.device_id"],
                             ondelete="RESTRICT",
                             name="fk_scene_binding_device"),
    )

    release_id: Mapped[str] = mapped_column(Text, primary_key=True)
    binding_id: Mapped[str] = mapped_column(Text, primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    device_id: Mapped[str] = mapped_column(Text, nullable=False)
    scene_id: Mapped[str] = mapped_column(Text, nullable=False)
    contract_version: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    alias: Mapped[str] = mapped_column(Text, nullable=False)


# ═══════════════════════════════════════════════════════════════
# 配方 / 工序 / 路线 / 入口
# ═══════════════════════════════════════════════════════════════

class Recipe(Base):
    __tablename__ = "recipe"
    __table_args__ = (
        _non_empty("recipe_id"),
        _non_empty("version"),
        _non_empty("product_type"),
        CheckConstraint("ordinal >= 0", name="ordinal_nonnegative"),
        UniqueConstraint("release_id", "recipe_id", "version",
                         name="uq_recipe_release_id_version"),
        UniqueConstraint("release_id", "ordinal",
                         name="uq_recipe_release_ordinal"),
        ForeignKeyConstraint(["release_id"], ["catalog_release.release_id"],
                             ondelete="RESTRICT", name="fk_recipe_release"),
    )

    release_id: Mapped[str] = mapped_column(Text, primary_key=True)
    recipe_id: Mapped[str] = mapped_column(Text, primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    product_type: Mapped[str] = mapped_column(Text, nullable=False)
    join_operation_id: Mapped[Optional[str]] = mapped_column(Text)


class OperationTemplate(Base):
    __tablename__ = "operation_template"
    __table_args__ = (
        _non_empty("operation_id"),
        _non_empty("kind"),
        _non_empty("semantics"),
        _non_empty("completion_verifier"),
        _in("executor_kind", EXECUTOR_KINDS),
        _in("source_kind", MATERIAL_FLOW_KINDS),
        _in("sink_kind", MATERIAL_FLOW_KINDS),
        _in("resource_policy", RESOURCE_POLICIES_OPERATION),
        _in("predecessor_policy", PREDECESSOR_POLICIES),
        CheckConstraint("priority >= 0", name="priority_nonnegative"),
        CheckConstraint("ordinal >= 0", name="ordinal_nonnegative"),
        UniqueConstraint("release_id", "recipe_id", "operation_id",
                         name="uq_operation_template_recipe_operation"),
        UniqueConstraint("release_id", "recipe_id", "ordinal",
                         name="uq_operation_template_recipe_ordinal"),
        ForeignKeyConstraint(["release_id", "recipe_id"],
                             ["recipe.release_id", "recipe.recipe_id"],
                             ondelete="RESTRICT",
                             name="fk_operation_template_recipe"),
        ForeignKeyConstraint(
            ["release_id", "capability_id"],
            ["capability.release_id", "capability.capability_id"],
            ondelete="RESTRICT",
            name="fk_operation_template_capability"),
    )

    release_id: Mapped[str] = mapped_column(Text, primary_key=True)
    operation_id: Mapped[str] = mapped_column(Text, primary_key=True)
    recipe_id: Mapped[str] = mapped_column(Text, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    semantics: Mapped[str] = mapped_column(Text, nullable=False)
    executor_kind: Mapped[str] = mapped_column(Text, nullable=False)
    capability_id: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    resource_policy: Mapped[str] = mapped_column(Text, nullable=False)
    predecessor_policy: Mapped[str] = mapped_column(Text, nullable=False)
    completion_verifier: Mapped[str] = mapped_column(Text, nullable=False)
    source_kind: Mapped[str] = mapped_column(Text, nullable=False)
    sink_kind: Mapped[str] = mapped_column(Text, nullable=False)
    dock: Mapped[Optional[str]] = mapped_column(Text)
    route_origin_dock: Mapped[Optional[str]] = mapped_column(Text)
    route_destination_dock: Mapped[Optional[str]] = mapped_column(Text)


class OperationDependency(Base):
    """一条前置边; 同时保留两端列表中的业务顺序.

    ``ordinal`` 是该前置在 operation.depends_on 中的位置;
    ``successor_ordinal`` 是该工序在 predecessor.successors 中的位置.
    """

    __tablename__ = "operation_dependency"
    __table_args__ = (
        CheckConstraint("operation_id <> predecessor_operation_id",
                        name="no_self_loop"),
        CheckConstraint("ordinal >= 0", name="ordinal_nonnegative"),
        CheckConstraint("successor_ordinal >= 0",
                        name="successor_ordinal_nonnegative"),
        UniqueConstraint("release_id", "operation_id", "ordinal",
                         name="uq_operation_dependency_operation_ordinal"),
        UniqueConstraint("release_id", "predecessor_operation_id",
                         "successor_ordinal",
                         name="uq_operation_dependency_successor_ordinal"),
        ForeignKeyConstraint(
            ["release_id", "operation_id"],
            ["operation_template.release_id",
             "operation_template.operation_id"],
            ondelete="RESTRICT", name="fk_operation_dependency_operation"),
        ForeignKeyConstraint(
            ["release_id", "predecessor_operation_id"],
            ["operation_template.release_id",
             "operation_template.operation_id"],
            ondelete="RESTRICT",
            name="fk_operation_dependency_predecessor"),
    )

    release_id: Mapped[str] = mapped_column(Text, primary_key=True)
    operation_id: Mapped[str] = mapped_column(Text, primary_key=True)
    predecessor_operation_id: Mapped[str] = mapped_column(Text,
                                                          primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    successor_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)


class OperationInput(Base):
    __tablename__ = "operation_input"
    __table_args__ = (
        CheckConstraint("ordinal >= 0", name="ordinal_nonnegative"),
        ForeignKeyConstraint(
            ["release_id", "operation_id"],
            ["operation_template.release_id",
             "operation_template.operation_id"],
            ondelete="RESTRICT", name="fk_operation_input_operation"),
        ForeignKeyConstraint(["release_id", "zone_id"],
                             ["zone.release_id", "zone.zone_id"],
                             ondelete="RESTRICT",
                             name="fk_operation_input_zone"),
        ForeignKeyConstraint(
            ["release_id", "material_type_id"],
            ["material_type.release_id", "material_type.material_type_id"],
            ondelete="RESTRICT", name="fk_operation_input_material"),
    )

    release_id: Mapped[str] = mapped_column(Text, primary_key=True)
    operation_id: Mapped[str] = mapped_column(Text, primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    zone_id: Mapped[str] = mapped_column(Text, nullable=False)
    material_type_id: Mapped[str] = mapped_column(Text, nullable=False)


class OperationOutput(Base):
    __tablename__ = "operation_output"
    __table_args__ = (
        ForeignKeyConstraint(
            ["release_id", "operation_id"],
            ["operation_template.release_id",
             "operation_template.operation_id"],
            ondelete="RESTRICT", name="fk_operation_output_operation"),
        ForeignKeyConstraint(["release_id", "zone_id"],
                             ["zone.release_id", "zone.zone_id"],
                             ondelete="RESTRICT",
                             name="fk_operation_output_zone"),
        ForeignKeyConstraint(
            ["release_id", "material_type_id"],
            ["material_type.release_id", "material_type.material_type_id"],
            ondelete="RESTRICT", name="fk_operation_output_material"),
    )

    release_id: Mapped[str] = mapped_column(Text, primary_key=True)
    operation_id: Mapped[str] = mapped_column(Text, primary_key=True)
    zone_id: Mapped[str] = mapped_column(Text, nullable=False)
    material_type_id: Mapped[str] = mapped_column(Text, nullable=False)


class OperationCandidateDevice(Base):
    __tablename__ = "operation_candidate_device"
    __table_args__ = (
        CheckConstraint("ordinal >= 0", name="ordinal_nonnegative"),
        ForeignKeyConstraint(
            ["release_id", "operation_id"],
            ["operation_template.release_id",
             "operation_template.operation_id"],
            ondelete="RESTRICT",
            name="fk_operation_candidate_device_operation"),
        ForeignKeyConstraint(["release_id", "device_id"],
                             ["device.release_id", "device.device_id"],
                             ondelete="RESTRICT",
                             name="fk_operation_candidate_device_device"),
    )

    release_id: Mapped[str] = mapped_column(Text, primary_key=True)
    operation_id: Mapped[str] = mapped_column(Text, primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[str] = mapped_column(Text, nullable=False)


class OperationResourceRequest(Base):
    __tablename__ = "operation_resource_request"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("ordinal >= 0", name="ordinal_nonnegative"),
        UniqueConstraint("release_id", "operation_id", "ordinal",
                         name="uq_operation_resource_request_ordinal"),
        ForeignKeyConstraint(
            ["release_id", "operation_id"],
            ["operation_template.release_id",
             "operation_template.operation_id"],
            ondelete="RESTRICT",
            name="fk_operation_resource_request_operation"),
        ForeignKeyConstraint(["release_id", "resource_id"],
                             ["resource.release_id", "resource.resource_id"],
                             ondelete="RESTRICT",
                             name="fk_operation_resource_request_resource"),
    )

    release_id: Mapped[str] = mapped_column(Text, primary_key=True)
    operation_id: Mapped[str] = mapped_column(Text, primary_key=True)
    resource_id: Mapped[str] = mapped_column(Text, primary_key=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)


class OperationWorkStep(Base):
    __tablename__ = "operation_work_step"
    __table_args__ = (
        _non_empty("template_id"),
        CheckConstraint("ordinal >= 0", name="ordinal_nonnegative"),
        ForeignKeyConstraint(
            ["release_id", "operation_id"],
            ["operation_template.release_id",
             "operation_template.operation_id"],
            ondelete="RESTRICT", name="fk_operation_work_step_operation"),
    )

    release_id: Mapped[str] = mapped_column(Text, primary_key=True)
    operation_id: Mapped[str] = mapped_column(Text, primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    template_id: Mapped[str] = mapped_column(Text, nullable=False)


class RouteVariant(Base):
    __tablename__ = "route_variant"
    __table_args__ = (
        _non_empty("route_id"),
        _non_empty("route_group"),
        _non_empty("semantics"),
        CheckConstraint("ordinal >= 0", name="ordinal_nonnegative"),
        UniqueConstraint("release_id", "recipe_id", "ordinal",
                         name="uq_route_variant_recipe_ordinal"),
        ForeignKeyConstraint(["release_id", "recipe_id"],
                             ["recipe.release_id", "recipe.recipe_id"],
                             ondelete="RESTRICT",
                             name="fk_route_variant_recipe"),
        ForeignKeyConstraint(
            ["release_id", "entry_zone_id"],
            ["zone.release_id", "zone.zone_id"],
            ondelete="RESTRICT", name="fk_route_variant_entry_zone"),
        ForeignKeyConstraint(
            ["release_id", "exit_zone_id"],
            ["zone.release_id", "zone.zone_id"],
            ondelete="RESTRICT", name="fk_route_variant_exit_zone"),
    )

    release_id: Mapped[str] = mapped_column(Text, primary_key=True)
    recipe_id: Mapped[str] = mapped_column(Text, primary_key=True)
    route_id: Mapped[str] = mapped_column(Text, primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    route_group: Mapped[str] = mapped_column(Text, nullable=False)
    semantics: Mapped[str] = mapped_column(Text, nullable=False)
    entry_zone_id: Mapped[str] = mapped_column(Text, nullable=False)
    exit_zone_id: Mapped[str] = mapped_column(Text, nullable=False)


class RouteOperation(Base):
    __tablename__ = "route_operation"
    __table_args__ = (
        CheckConstraint("ordinal >= 0", name="ordinal_nonnegative"),
        ForeignKeyConstraint(
            ["release_id", "recipe_id", "route_id"],
            ["route_variant.release_id", "route_variant.recipe_id",
             "route_variant.route_id"],
            ondelete="RESTRICT", name="fk_route_operation_route"),
        ForeignKeyConstraint(
            ["release_id", "recipe_id", "operation_id"],
            ["operation_template.release_id",
             "operation_template.recipe_id",
             "operation_template.operation_id"],
            ondelete="RESTRICT", name="fk_route_operation_operation"),
    )

    release_id: Mapped[str] = mapped_column(Text, primary_key=True)
    recipe_id: Mapped[str] = mapped_column(Text, primary_key=True)
    route_id: Mapped[str] = mapped_column(Text, primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    operation_id: Mapped[str] = mapped_column(Text, nullable=False)


class OrderEntry(Base):
    __tablename__ = "order_entry"
    __table_args__ = (
        _non_empty("entry_id"),
        CheckConstraint("ordinal >= 0", name="ordinal_nonnegative"),
        UniqueConstraint("release_id", "recipe_id", "ordinal",
                         name="uq_order_entry_recipe_ordinal"),
        ForeignKeyConstraint(["release_id", "recipe_id"],
                             ["recipe.release_id", "recipe.recipe_id"],
                             ondelete="RESTRICT",
                             name="fk_order_entry_recipe"),
        ForeignKeyConstraint(
            ["release_id", "recipe_id", "operation_id"],
            ["operation_template.release_id",
             "operation_template.recipe_id",
             "operation_template.operation_id"],
            ondelete="RESTRICT", name="fk_order_entry_operation"),
        ForeignKeyConstraint(["release_id", "line_id"],
                             ["zone.release_id", "zone.zone_id"],
                             ondelete="RESTRICT",
                             name="fk_order_entry_line"),
        ForeignKeyConstraint(
            ["release_id", "material_type_id"],
            ["material_type.release_id", "material_type.material_type_id"],
            ondelete="RESTRICT", name="fk_order_entry_material"),
        ForeignKeyConstraint(
            ["release_id", "recipe_id", "route_id"],
            ["route_variant.release_id", "route_variant.recipe_id",
             "route_variant.route_id"],
            ondelete="RESTRICT", name="fk_order_entry_route"),
    )

    release_id: Mapped[str] = mapped_column(Text, primary_key=True)
    recipe_id: Mapped[str] = mapped_column(Text, primary_key=True)
    entry_id: Mapped[str] = mapped_column(Text, primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    operation_id: Mapped[str] = mapped_column(Text, nullable=False)
    line_id: Mapped[str] = mapped_column(Text, nullable=False)
    material_type_id: Mapped[str] = mapped_column(Text, nullable=False)
    route_group: Mapped[Optional[str]] = mapped_column(Text)
    route_id: Mapped[Optional[str]] = mapped_column(Text)


# 知识图谱表 (方案 §2.3) 在同一 metadata 中注册; 单独文件保持静态目录
# 与抽取结果 schema 的分区. 必须放在 Base/辅助函数定义之后.
from . import knowledge_models  # noqa: E402,F401
