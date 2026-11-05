"""persistence.run_models — 仿真运行记录表 (方案 §2.2, 阶段 4).

运行记录由 :class:`flexible_factory.persistence.runs.RunRecorder` 在运行
开始/结束时批量写入, 不在 ``sim.step()`` 热路径逐 tick 写库. 记录是
自包含事实: 资源/区域 ID 保存为文本, 通过 ``catalog_version`` 关联到当时
使用的目录版本, 不建到某个 catalog release 的外键.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import (Boolean, CheckConstraint, Float, ForeignKeyConstraint,
                        Index, Integer, JSON, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, _in, _non_empty

RUN_STATUSES = ("RUNNING", "DONE", "FAILED", "ABORTED")
TASK_STATES = ("PENDING", "WAITING", "RUNNING", "BLOCKED", "DONE", "FAILED",
               "REJECTED")
MATERIAL_STATES = ("CREATED", "IN_TRANSIT", "READY", "HELD", "DELIVERED",
                   "CONSUMED")
RESOURCE_ACTIONS = ("ACQUIRE", "WAIT", "RELEASE", "RELEASE_DENIED",
                    "ORDER_VIOLATION")
CATALOG_SOURCES = ("code", "database")


class ProductionRun(Base):
    """一次仿真批次; 开始写 RUNNING, 结束更新为终态."""

    __tablename__ = "production_run"
    __table_args__ = (
        _non_empty("run_id"),
        _non_empty("record_version"),
        _non_empty("scenario"),
        _non_empty("started_at"),
        _in("status", RUN_STATUSES),
        _in("catalog_source", CATALOG_SOURCES),
        CheckConstraint(
            "(status = 'RUNNING' AND finished_at IS NULL) OR "
            "(status IN ('DONE', 'FAILED', 'ABORTED') "
            "AND finished_at IS NOT NULL)",
            name="finished_at_consistent"),
        CheckConstraint("sim_started_s IS NULL OR sim_started_s >= 0",
                        name="sim_started_nonnegative"),
        CheckConstraint("sim_finished_s IS NULL OR sim_finished_s >= 0",
                        name="sim_finished_nonnegative"),
        CheckConstraint("wall_duration_s IS NULL OR wall_duration_s >= 0",
                        name="wall_duration_nonnegative"),
        Index("ix_production_run_status", "status"),
        Index("ix_production_run_started", "started_at"),
    )

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    record_version: Mapped[str] = mapped_column(Text, nullable=False)
    scenario: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_id: Mapped[Optional[str]] = mapped_column(Text)
    catalog_version: Mapped[Optional[str]] = mapped_column(Text)
    catalog_source: Mapped[str] = mapped_column(Text, nullable=False)
    scene_contract_version: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[str] = mapped_column(Text, nullable=False)
    finished_at: Mapped[Optional[str]] = mapped_column(Text)
    sim_started_s: Mapped[Optional[float]] = mapped_column(Float)
    sim_finished_s: Mapped[Optional[float]] = mapped_column(Float)
    wall_duration_s: Mapped[Optional[float]] = mapped_column(Float)
    all_ok: Mapped[Optional[bool]] = mapped_column(Boolean)
    failure_reason: Mapped[Optional[str]] = mapped_column(Text)
    result_json: Mapped[Optional[dict]] = mapped_column(JSON)


class OperationInstanceRecord(Base):
    """工序实例的最终快照; 生命周期字段来自 OperationInstance."""

    __tablename__ = "operation_instance"
    __table_args__ = (
        _non_empty("instance_id"),
        _non_empty("template_id"),
        _non_empty("kind"),
        _in("state", TASK_STATES),
        CheckConstraint("uid >= 0", name="uid_nonnegative"),
        CheckConstraint("priority >= 0", name="priority_nonnegative"),
        CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
        CheckConstraint("sequence >= 0", name="sequence_nonnegative"),
        ForeignKeyConstraint(["run_id"], ["production_run.run_id"],
                             ondelete="RESTRICT",
                             name="fk_operation_instance_run"),
        UniqueConstraint("run_id", "task_id",
                         name="uq_operation_instance_run_task"),
        Index("ix_operation_instance_state", "run_id", "state"),
        Index("ix_operation_instance_template", "run_id", "template_id"),
    )

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    instance_id: Mapped[str] = mapped_column(Text, primary_key=True)
    template_id: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    recipe_id: Mapped[str] = mapped_column(Text, nullable=False)
    recipe_version: Mapped[str] = mapped_column(Text, nullable=False)
    recipe_run_id: Mapped[str] = mapped_column(Text, nullable=False)
    entry_id: Mapped[str] = mapped_column(Text, nullable=False)
    route_id: Mapped[str] = mapped_column(Text, nullable=False)
    task_id: Mapped[str] = mapped_column(Text, nullable=False)
    uid: Mapped[int] = mapped_column(Integer, nullable=False)
    selected_device_id: Mapped[Optional[str]] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    phase: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    transport: Mapped[bool] = mapped_column(Boolean, nullable=False)
    upgraded: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_sim_time: Mapped[float] = mapped_column(Float, nullable=False)
    started_sim_time: Mapped[Optional[float]] = mapped_column(Float)
    finished_sim_time: Mapped[Optional[float]] = mapped_column(Float)
    committed_sim_time: Mapped[Optional[float]] = mapped_column(Float)
    waiting_since: Mapped[Optional[float]] = mapped_column(Float)
    blocked_since: Mapped[Optional[float]] = mapped_column(Float)
    failure_reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[Optional[dict]] = mapped_column(JSON)


class MaterialInstanceRecord(Base):
    """物料实例的最终快照; 句柄不持久化, 父子关系在 genealogy 表."""

    __tablename__ = "material_instance"
    __table_args__ = (
        _non_empty("alias"),
        _non_empty("item_type"),
        _in("status", MATERIAL_STATES),
        CheckConstraint("uid >= 0", name="uid_nonnegative"),
        ForeignKeyConstraint(["run_id"], ["production_run.run_id"],
                             ondelete="RESTRICT",
                             name="fk_material_instance_run"),
        Index("ix_material_instance_type", "run_id", "item_type"),
        Index("ix_material_instance_state", "run_id", "status"),
    )

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    uid: Mapped[int] = mapped_column(Integer, primary_key=True)
    alias: Mapped[str] = mapped_column(Text, nullable=False)
    item_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    zone: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    destination: Mapped[str] = mapped_column(Text, nullable=False)
    process_stage: Mapped[str] = mapped_column(Text, nullable=False)
    created_sim_time: Mapped[Optional[float]] = mapped_column(Float)
    last_event_sim_time: Mapped[Optional[float]] = mapped_column(Float)


class OperationInstanceInput(Base):
    __tablename__ = "operation_instance_input"
    __table_args__ = (
        CheckConstraint("ordinal >= 0", name="ordinal_nonnegative"),
        CheckConstraint("uid >= 0", name="uid_nonnegative"),
        ForeignKeyConstraint(["run_id", "instance_id"],
                             ["operation_instance.run_id",
                              "operation_instance.instance_id"],
                             ondelete="RESTRICT",
                             name="fk_operation_instance_input_operation"),
        ForeignKeyConstraint(["run_id", "uid"],
                             ["material_instance.run_id",
                              "material_instance.uid"],
                             ondelete="RESTRICT",
                             name="fk_operation_instance_input_material"),
    )

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    instance_id: Mapped[str] = mapped_column(Text, primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    uid: Mapped[int] = mapped_column(Integer, nullable=False)


class OperationInstanceOutput(Base):
    __tablename__ = "operation_instance_output"
    __table_args__ = (
        CheckConstraint("ordinal >= 0", name="ordinal_nonnegative"),
        CheckConstraint("uid >= 0", name="uid_nonnegative"),
        ForeignKeyConstraint(["run_id", "instance_id"],
                             ["operation_instance.run_id",
                              "operation_instance.instance_id"],
                             ondelete="RESTRICT",
                             name="fk_operation_instance_output_operation"),
        ForeignKeyConstraint(["run_id", "uid"],
                             ["material_instance.run_id",
                              "material_instance.uid"],
                             ondelete="RESTRICT",
                             name="fk_operation_instance_output_material"),
    )

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    instance_id: Mapped[str] = mapped_column(Text, primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    uid: Mapped[int] = mapped_column(Integer, nullable=False)


class MaterialGenealogy(Base):
    """父 UID → 子 UID 的谱系边; 当前由 E 岛原子配对产生."""

    __tablename__ = "material_genealogy"
    __table_args__ = (
        _non_empty("event_kind"),
        CheckConstraint("child_uid <> parent_uid", name="no_self_parent"),
        CheckConstraint("child_uid >= 0 AND parent_uid >= 0",
                        name="uids_nonnegative"),
        ForeignKeyConstraint(["run_id", "child_uid"],
                             ["material_instance.run_id",
                              "material_instance.uid"],
                             ondelete="RESTRICT",
                             name="fk_material_genealogy_child"),
        ForeignKeyConstraint(["run_id", "parent_uid"],
                             ["material_instance.run_id",
                              "material_instance.uid"],
                             ondelete="RESTRICT",
                             name="fk_material_genealogy_parent"),
        ForeignKeyConstraint(["run_id", "operation_instance_id"],
                             ["operation_instance.run_id",
                              "operation_instance.instance_id"],
                             ondelete="RESTRICT",
                             name="fk_material_genealogy_operation"),
        Index("ix_material_genealogy_child", "run_id", "child_uid"),
    )

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    child_uid: Mapped[int] = mapped_column(Integer, primary_key=True)
    parent_uid: Mapped[int] = mapped_column(Integer, primary_key=True)
    operation_instance_id: Mapped[Optional[str]] = mapped_column(Text)
    event_kind: Mapped[str] = mapped_column(Text, nullable=False)
    pair_key: Mapped[Optional[str]] = mapped_column(Text)
    sim_time: Mapped[float] = mapped_column(Float, nullable=False)


class ResourceEventRecord(Base):
    __tablename__ = "resource_event"
    __table_args__ = (
        _non_empty("resource_id"),
        _non_empty("owner"),
        _in("action", RESOURCE_ACTIONS),
        ForeignKeyConstraint(["run_id"], ["production_run.run_id"],
                             ondelete="RESTRICT",
                             name="fk_resource_event_run"),
        ForeignKeyConstraint(["run_id", "instance_id"],
                             ["operation_instance.run_id",
                              "operation_instance.instance_id"],
                             ondelete="RESTRICT",
                             name="fk_resource_event_operation"),
        Index("ix_resource_event_resource", "run_id", "resource_id"),
        Index("ix_resource_event_instance", "run_id", "instance_id"),
        Index("ix_resource_event_time", "run_id", "sim_time"),
    )

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True,
                                          autoincrement=True)
    run_id: Mapped[str] = mapped_column(Text, nullable=False)
    sim_time: Mapped[float] = mapped_column(Float, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    resource_id: Mapped[str] = mapped_column(Text, nullable=False)
    owner: Mapped[str] = mapped_column(Text, nullable=False)
    task_id: Mapped[str] = mapped_column(Text, nullable=False)
    holder: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    instance_id: Mapped[Optional[str]] = mapped_column(Text)


class RunEventRecord(Base):
    __tablename__ = "run_event"
    __table_args__ = (
        _non_empty("kind"),
        ForeignKeyConstraint(["run_id"], ["production_run.run_id"],
                             ondelete="RESTRICT", name="fk_run_event_run"),
        ForeignKeyConstraint(["run_id", "instance_id"],
                             ["operation_instance.run_id",
                              "operation_instance.instance_id"],
                             ondelete="RESTRICT",
                             name="fk_run_event_operation"),
        Index("ix_run_event_time", "run_id", "sim_time"),
        Index("ix_run_event_kind", "run_id", "kind"),
        Index("ix_run_event_uid", "run_id", "uid"),
        Index("ix_run_event_instance", "run_id", "instance_id"),
    )

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True,
                                          autoincrement=True)
    run_id: Mapped[str] = mapped_column(Text, nullable=False)
    sim_time: Mapped[float] = mapped_column(Float, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    task_id: Mapped[str] = mapped_column(Text, nullable=False)
    uid: Mapped[Optional[int]] = mapped_column(Integer)
    item_type: Mapped[str] = mapped_column(Text, nullable=False)
    zone: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    destination: Mapped[str] = mapped_column(Text, nullable=False)
    device: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_id: Mapped[str] = mapped_column(Text, nullable=False)
    instance_id: Mapped[Optional[str]] = mapped_column(Text)
    detail_json: Mapped[Optional[dict]] = mapped_column(JSON)
