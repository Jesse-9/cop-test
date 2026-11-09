"""persistence.knowledge_models — 知识图谱抽取结果的 ORM 映射.

本模块对应 ``docs/implementation/plans/data-platform/resource-catalog-database-and-knowledge-graph.md`` §2.3 的六张表。权威事实仍来自
关系表: 实体/关系行是经过来源登记和抽取运行校验后的导入结果;
``entity_source`` / ``relation_source`` 保存来源定位、原始片段和置信度.
Neo4j 只是读取原始 dump 和人工查询的工具, 不是本 schema 的权威来源.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import (CheckConstraint, Float, ForeignKeyConstraint, Index,
                        JSON, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, _in, _non_empty

EXTRACTION_STATUSES = ("staging", "published", "failed")
KNOWLEDGE_STATUSES = ("staging", "published", "conflict", "rejected")


class SourceDocument(Base):
    """一个可追溯的原始资料 (例如 Neo4j dump 或导出 JSON)."""

    __tablename__ = "source_document"
    __table_args__ = (
        _non_empty("source_id"),
        _non_empty("uri_or_path"),
        _non_empty("media_type"),
        _non_empty("registered_at"),
        CheckConstraint("length(sha256) = 64", name="sha256_digest"),
        UniqueConstraint("sha256", name="uq_source_document_sha256"),
    )

    source_id: Mapped[str] = mapped_column(Text, primary_key=True)
    uri_or_path: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    source_version: Mapped[Optional[str]] = mapped_column(Text)
    registered_at: Mapped[str] = mapped_column(Text, nullable=False)


class ExtractionRun(Base):
    """一次抽取运行; 同一来源/抽取器版本只能登记一次 (幂等键)."""

    __tablename__ = "extraction_run"
    __table_args__ = (
        _non_empty("extraction_id"),
        _non_empty("extractor_id"),
        _non_empty("extractor_version"),
        _non_empty("started_at"),
        _in("status", EXTRACTION_STATUSES),
        UniqueConstraint("source_id", "extractor_id", "extractor_version",
                         name="uq_extraction_run_source_extractor"),
        ForeignKeyConstraint(["source_id"], ["source_document.source_id"],
                             ondelete="RESTRICT",
                             name="fk_extraction_run_source"),
        Index("ix_extraction_run_status", "status"),
    )

    extraction_id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    extractor_id: Mapped[str] = mapped_column(Text, nullable=False)
    extractor_version: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[str] = mapped_column(Text, nullable=False)
    finished_at: Mapped[Optional[str]] = mapped_column(Text)
    report_json: Mapped[Optional[dict]] = mapped_column(JSON)


class KnowledgeEntity(Base):
    """受来源支持的实体; 相同 canonical ID 冲突时保留首条并标记 conflict."""

    __tablename__ = "knowledge_entity"
    __table_args__ = (
        _non_empty("entity_id"),
        _non_empty("entity_type"),
        _non_empty("canonical_id"),
        _non_empty("label"),
        _in("status", KNOWLEDGE_STATUSES),
        UniqueConstraint("entity_type", "canonical_id",
                         name="uq_knowledge_entity_type_canonical"),
        Index("ix_knowledge_entity_type", "entity_type"),
        Index("ix_knowledge_entity_status", "status"),
    )

    entity_id: Mapped[str] = mapped_column(Text, primary_key=True)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_id: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    attributes_json: Mapped[Optional[dict]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(Text, nullable=False)


class KnowledgeRelation(Base):
    """受来源支持的关系; ``source_predicate`` 保留原始谓词."""

    __tablename__ = "knowledge_relation"
    __table_args__ = (
        _non_empty("relation_id"),
        _non_empty("predicate"),
        _non_empty("source_predicate"),
        _in("status", KNOWLEDGE_STATUSES),
        UniqueConstraint("subject_id", "predicate", "object_id",
                         name="uq_knowledge_relation_triple"),
        ForeignKeyConstraint(
            ["subject_id"], ["knowledge_entity.entity_id"],
            ondelete="RESTRICT", name="fk_knowledge_relation_subject"),
        ForeignKeyConstraint(
            ["object_id"], ["knowledge_entity.entity_id"],
            ondelete="RESTRICT", name="fk_knowledge_relation_object"),
        Index("ix_knowledge_relation_predicate", "predicate"),
        Index("ix_knowledge_relation_status", "status"),
    )

    relation_id: Mapped[str] = mapped_column(Text, primary_key=True)
    subject_id: Mapped[str] = mapped_column(Text, nullable=False)
    predicate: Mapped[str] = mapped_column(Text, nullable=False)
    object_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_predicate: Mapped[str] = mapped_column(Text, nullable=False)
    attributes_json: Mapped[Optional[dict]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(Text, nullable=False)


class EntitySource(Base):
    """实体到来源的追溯记录; 同一实体可有多条来源片段."""

    __tablename__ = "entity_source"
    __table_args__ = (
        _non_empty("source_locator"),
        _non_empty("raw_fragment"),
        CheckConstraint("confidence >= 0 AND confidence <= 1",
                        name="confidence_range"),
        ForeignKeyConstraint(["entity_id"], ["knowledge_entity.entity_id"],
                             ondelete="RESTRICT",
                             name="fk_entity_source_entity"),
        ForeignKeyConstraint(["source_id"], ["source_document.source_id"],
                             ondelete="RESTRICT",
                             name="fk_entity_source_source"),
        ForeignKeyConstraint(
            ["extraction_id"], ["extraction_run.extraction_id"],
            ondelete="RESTRICT", name="fk_entity_source_extraction"),
        Index("ix_entity_source_extraction", "extraction_id"),
    )

    entity_id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_id: Mapped[str] = mapped_column(Text, primary_key=True)
    extraction_id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_locator: Mapped[str] = mapped_column(Text, primary_key=True)
    raw_fragment: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)


class RelationSource(Base):
    """关系到来源的追溯记录."""

    __tablename__ = "relation_source"
    __table_args__ = (
        _non_empty("source_locator"),
        _non_empty("raw_fragment"),
        CheckConstraint("confidence >= 0 AND confidence <= 1",
                        name="confidence_range"),
        ForeignKeyConstraint(["relation_id"],
                             ["knowledge_relation.relation_id"],
                             ondelete="RESTRICT",
                             name="fk_relation_source_relation"),
        ForeignKeyConstraint(["source_id"], ["source_document.source_id"],
                             ondelete="RESTRICT",
                             name="fk_relation_source_source"),
        ForeignKeyConstraint(
            ["extraction_id"], ["extraction_run.extraction_id"],
            ondelete="RESTRICT", name="fk_relation_source_extraction"),
        Index("ix_relation_source_extraction", "extraction_id"),
    )

    relation_id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_id: Mapped[str] = mapped_column(Text, primary_key=True)
    extraction_id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_locator: Mapped[str] = mapped_column(Text, primary_key=True)
    raw_fragment: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
