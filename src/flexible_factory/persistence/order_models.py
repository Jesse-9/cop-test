"""persistence.order_models — 版本化订单定义与订单运行关联.

订单定义与静态工艺目录分表/分责:

  - ``order_header``: 订单头与当前版本指针;
  - ``order_version``: 不可变订单版本 (规范化 spec + digest);
  - ``order_run``: 一次订单运行与订单版本/工件/终态的关联;
  - ``order_node_run``: 订单节点运行快照, 含失败范围与阻断原因.

``order_version`` 行由数据库触发器禁止 UPDATE; 修改编排只能创建新版本.
该 schema 不导入 ``domain`` 或 ``application``; spec 以规范化 JSON 文本保存,
读取时重新校验 digest.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import (CheckConstraint, ForeignKeyConstraint, Index, Integer,
                        JSON, Text)
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, _in, _non_empty

ORDER_SOURCES = ("manual", "planner", "fixture")
ORDER_STATUSES = ("STARTING", "RUNNING", "SUCCEEDED", "PARTIAL_FAILED",
                  "FAILED", "ABORTED")
ORDER_FAILURE_SCOPES = ("NODE", "GLOBAL")


class OrderHeader(Base):
    """订单头; ``current_version`` 是创建新版本时的乐观并发指针."""

    __tablename__ = "order_header"
    __table_args__ = (
        _non_empty("order_id"),
        _in("source", ORDER_SOURCES),
        CheckConstraint("current_version > 0",
                        name="current_version_positive"),
        Index("ix_order_header_updated", "updated_at"),
    )

    order_id: Mapped[str] = mapped_column(Text, primary_key=True)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text)


class OrderVersion(Base):
    """不可变订单版本; ``spec_json`` 是规范化订单声明."""

    __tablename__ = "order_version"
    __table_args__ = (
        _non_empty("order_id"),
        _non_empty("contract_version"),
        _non_empty("scene_id"),
        _in("source", ORDER_SOURCES),
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint("length(dag_digest) = 64",
                        name="dag_digest_sha256"),
        ForeignKeyConstraint(["order_id"], ["order_header.order_id"],
                             ondelete="RESTRICT",
                             name="fk_order_version_header"),
        Index("ix_order_version_order", "order_id", "version"),
    )

    order_id: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    contract_version: Mapped[str] = mapped_column(Text, nullable=False)
    scene_id: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    dag_digest: Mapped[str] = mapped_column(Text, nullable=False)
    spec_json: Mapped[str] = mapped_column(Text, nullable=False)
    catalog_version: Mapped[Optional[str]] = mapped_column(Text)
    parent_version: Mapped[Optional[int]] = mapped_column(Integer)
    planner_policy_version: Mapped[Optional[str]] = mapped_column(Text)
    planner_input_json: Mapped[Optional[dict]] = mapped_column(JSON)
    planner_explain_json: Mapped[Optional[dict]] = mapped_column(JSON)
    planner_candidate_count: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text)


class OrderRun(Base):
    """一次订单运行; 与 ``production_run`` 以 run_id 关联但不强制同时写入."""

    __tablename__ = "order_run"
    __table_args__ = (
        _non_empty("run_id"),
        _non_empty("dag_digest"),
        _in("status", ORDER_STATUSES),
        _in("failure_scope", ORDER_FAILURE_SCOPES),
        CheckConstraint("length(dag_digest) = 64",
                        name="dag_digest_sha256"),
        CheckConstraint(
            "(status = 'STARTING' AND finished_at IS NULL) OR "
            "(status = 'RUNNING' AND finished_at IS NULL) OR "
            "(status IN ('SUCCEEDED', 'PARTIAL_FAILED', 'FAILED', "
            "'ABORTED') AND finished_at IS NOT NULL)",
            name="finished_at_consistent"),
        ForeignKeyConstraint(["order_id", "order_version"],
                             ["order_version.order_id",
                              "order_version.version"],
                             ondelete="RESTRICT",
                             name="fk_order_run_version"),
        Index("ix_order_run_order", "order_id", "order_version"),
        Index("ix_order_run_status", "status"),
    )

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    order_id: Mapped[str] = mapped_column(Text, nullable=False)
    order_version: Mapped[int] = mapped_column(Integer, nullable=False)
    dag_digest: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_id: Mapped[str] = mapped_column(Text, nullable=False,
                                             default="")
    status: Mapped[str] = mapped_column(Text, nullable=False)
    failure_scope: Mapped[Optional[str]] = mapped_column(Text)
    stop_reason: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[str] = mapped_column(Text, nullable=False)
    finished_at: Mapped[Optional[str]] = mapped_column(Text)
    result_json: Mapped[Optional[dict]] = mapped_column(JSON)


class OrderNodeRun(Base):
    """订单节点运行快照; 用于重建局部失败/阻断/继续分支."""

    __tablename__ = "order_node_run"
    __table_args__ = (
        _non_empty("node_id"),
        _non_empty("template_id"),
        CheckConstraint("uid >= 0", name="uid_nonnegative"),
        ForeignKeyConstraint(["run_id"], ["order_run.run_id"],
                             ondelete="RESTRICT",
                             name="fk_order_node_run_run"),
        Index("ix_order_node_run_node", "run_id", "node_id"),
        Index("ix_order_node_run_state", "run_id", "state"),
    )

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    instance_id: Mapped[str] = mapped_column(Text, primary_key=True)
    node_id: Mapped[str] = mapped_column(Text, nullable=False)
    template_id: Mapped[str] = mapped_column(Text, nullable=False)
    uid: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    failure_scope: Mapped[Optional[str]] = mapped_column(Text)
    failure_code: Mapped[Optional[str]] = mapped_column(Text)
    failure_reason: Mapped[Optional[str]] = mapped_column(Text)
    blocked_by_json: Mapped[Optional[list]] = mapped_column(JSON)
    input_uids_json: Mapped[Optional[list]] = mapped_column(JSON)
    output_uids_json: Mapped[Optional[list]] = mapped_column(JSON)
    evidence_json: Mapped[Optional[dict]] = mapped_column(JSON)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


__all__ = ["ORDER_FAILURE_SCOPES", "ORDER_SOURCES", "ORDER_STATUSES",
           "OrderHeader", "OrderNodeRun", "OrderRun", "OrderVersion"]
