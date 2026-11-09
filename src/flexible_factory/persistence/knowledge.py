"""persistence.knowledge — Neo4j 导出的知识图谱事实导入与发布.

流程遵循 ``docs/implementation/plans/data-platform/resource-catalog-database-and-knowledge-graph.md`` §4/§5:

  1. 原始 ``fmpmm.dump`` 由官方 ``neo4j-admin`` 载入 Neo4j;
  2. ``tools/neo4j_export.py`` 通过官方 Bolt 驱动只读导出节点/关系 JSON;
  3. 本模块把导出文件登记为 ``source_document``, 创建 staging
     ``extraction_run``, 写入实体/关系及来源定位;
  4. 人工审核报告后显式发布.

本模块不解析二进制 dump, 不连接 Neo4j, 也不依赖仿真代码. 未映射的标签/
谓词、端点缺失和 canonical ID 冲突都会进入报告并阻止发布.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..util import sha256_file, stable_digest
from .clock import utc_iso_now
from .db import CatalogDatabase, current_revision
from .errors import KnowledgeImportError, KnowledgeStateError
from .knowledge_models import (EntitySource, ExtractionRun, KnowledgeEntity,
                               KnowledgeRelation, RelationSource,
                               SourceDocument)

EXPORT_FORMAT = "cop-test-neo4j-export/1"
EXTRACTOR_ID = "flexible_factory.neo4j_export"
EXTRACTOR_VERSION = "1"
MEDIA_TYPE = "application/x-neo4j-dump"

# 受控词表: 来源标签/关系必须能映射到这些值; 未知值进入报告, 不静默丢弃.
ENTITY_TYPE_MAP = {
    "Abstraction": "Abstraction",
    "Action": "Action",
    "Capability": "Capability",
    "Goal": "Goal",
    "Instance": "Instance",
    "Operation": "Operation",
    "Parameter": "Parameter",
}
PREDICATE_MAP = {
    "include": "INCLUDES",
    "has_instance": "HAS_INSTANCE",
    "achieve_goal": "ACHIEVES_GOAL",
    "has_capability": "HAS_CAPABILITY",
    "operation": "HAS_OPERATION",
    "has_action": "HAS_ACTION",
    "has_parameter": "HAS_PARAMETER",
    "after": "AFTER",
    "installed_on": "INSTALLED_ON",
}

_REPORT_LIMIT = 200


def load_export(path: Path) -> dict:
    """读取并校验导出 JSON; 只接受显式格式, 不做猜测性解析."""
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except OSError as exc:
        raise KnowledgeImportError(f"无法读取导出文件 {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise KnowledgeImportError(
            f"导出文件不是合法 JSON ({path}): {exc}") from exc
    return validate_export(payload)


def validate_export(payload: dict) -> dict:
    """校验导出载荷; 返回原对象, 便于 load_export 与测试复用."""
    if not isinstance(payload, dict):
        raise KnowledgeImportError("导出文件顶层必须是 JSON 对象")
    if payload.get("format") != EXPORT_FORMAT:
        raise KnowledgeImportError(
            f"不支持的导出格式: {payload.get('format')!r}; "
            f"期望 {EXPORT_FORMAT!r}")
    nodes = payload.get("nodes")
    relationships = payload.get("relationships")
    if not isinstance(nodes, list) or not isinstance(relationships, list):
        raise KnowledgeImportError("导出文件必须包含 nodes/relationships 数组")
    extractor = payload.get("extractor")
    if extractor is not None and not isinstance(extractor, dict):
        raise KnowledgeImportError("extractor 必须是对象")
    source = payload.get("source")
    if source is not None and not isinstance(source, dict):
        raise KnowledgeImportError("source 必须是对象")

    node_ids: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            raise KnowledgeImportError("nodes 元素必须是对象")
        node_id = str(node.get("id", "")).strip()
        if not node_id:
            raise KnowledgeImportError("节点缺少非空 id")
        if node_id in node_ids:
            raise KnowledgeImportError(f"重复节点 id: {node_id!r}")
        node_ids.add(node_id)
        labels = node.get("labels")
        if not isinstance(labels, list) or \
                not all(isinstance(label, str) and label.strip()
                        for label in labels):
            raise KnowledgeImportError(
                f"节点 {node_id!r} 的 labels 必须是非空字符串数组")
        if not isinstance(node.get("properties"), dict):
            raise KnowledgeImportError(
                f"节点 {node_id!r} 的 properties 必须是对象")

    relation_ids: set[str] = set()
    for relation in relationships:
        if not isinstance(relation, dict):
            raise KnowledgeImportError("relationships 元素必须是对象")
        relation_id = str(relation.get("id", "")).strip()
        if not relation_id:
            raise KnowledgeImportError("关系缺少非空 id")
        if relation_id in relation_ids:
            raise KnowledgeImportError(f"重复关系 id: {relation_id!r}")
        relation_ids.add(relation_id)
        source_type = relation.get("type")
        if not isinstance(source_type, str) or not source_type.strip():
            raise KnowledgeImportError(
                f"关系 {relation_id!r} 缺少非空 type")
        for key in ("start", "end"):
            endpoint = str(relation.get(key, "")).strip()
            if not endpoint:
                raise KnowledgeImportError(
                    f"关系 {relation_id!r} 缺少 {key} 端点")
            if endpoint not in node_ids:
                raise KnowledgeImportError(
                    f"关系 {relation_id!r} 的 {key}={endpoint!r} "
                    f"不在 nodes 中")
        if not isinstance(relation.get("properties", {}), dict):
            raise KnowledgeImportError(
                f"关系 {relation_id!r} 的 properties 必须是对象")
    return payload


def _entity_type(labels: list[str]) -> str:
    joined = "|".join(sorted(labels)) or "Node"
    return ENTITY_TYPE_MAP.get(joined, joined)


def _canonical_key(properties: dict, node_id: str) -> str:
    for key in ("pro_code", "code", "goal_id", "uid", "id", "name"):
        value = properties.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
    return f"neo4j:{node_id}"


def _label(properties: dict, fallback: str) -> str:
    for key in ("name", "pro_code"):
        value = properties.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def _same_json(left: Any, right: Any) -> bool:
    return stable_digest(left) == stable_digest(right)


def _fragment(node_id: str, labels: list[str],
              properties: dict) -> str:
    return json.dumps({"id": node_id, "labels": labels,
                       "properties": properties}, ensure_ascii=False,
                      sort_keys=True, separators=(",", ":"))


def _relation_fragment(relation: dict) -> str:
    return json.dumps(relation, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _limited(rows: list) -> tuple[list, bool]:
    return rows[:_REPORT_LIMIT], len(rows) > _REPORT_LIMIT


def _export_digest(export: dict) -> str:
    return stable_digest({"nodes": export["nodes"],
                          "relationships": export["relationships"]})


def _extraction_id(source_id: str, extractor_id: str,
                   extractor_version: str) -> str:
    seed = f"{source_id}|{extractor_id}|{extractor_version}"
    return "ext_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:20]


def _relation_id(subject_id: str, predicate: str, object_id: str) -> str:
    seed = f"{subject_id}|{predicate}|{object_id}"
    return "rel_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:20]


def _summary(run: ExtractionRun) -> dict:
    report = run.report_json or {}
    return {
        "extraction_id": run.extraction_id,
        "source_id": run.source_id,
        "extractor_id": run.extractor_id,
        "extractor_version": run.extractor_version,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "counts": report.get("counts", {}),
        "unmapped_entity_types": report.get("unmapped_entity_types", []),
        "unmapped_predicates": report.get("unmapped_predicates", []),
    }


def _entity_for(session: Session, seen: dict[str, KnowledgeEntity],
                entity_id: str) -> Optional[KnowledgeEntity]:
    if entity_id in seen:
        return seen[entity_id]
    row = session.get(KnowledgeEntity, entity_id)
    if row is not None:
        seen[entity_id] = row
    return row


def stage_knowledge_export(
        database: CatalogDatabase, export: dict, *,
        source_dump: Path,
        extractor_id: str = EXTRACTOR_ID,
        extractor_version: str = EXTRACTOR_VERSION) -> dict:
    """把官方导出 JSON 写为 staging extraction; 不发布."""
    export = validate_export(export)
    source_path = Path(source_dump)
    if not source_path.is_file():
        raise KnowledgeImportError(
            f"来源 dump 不存在: {source_path}")
    if not extractor_id.strip() or not extractor_version.strip():
        raise KnowledgeImportError("extractor_id/version 不能为空")
    declared_extractor = export.get("extractor") or {}
    declared_id = str(declared_extractor.get("id") or "")
    declared_version = str(declared_extractor.get("version") or "")
    if declared_id and declared_id != extractor_id:
        raise KnowledgeImportError(
            f"导出文件抽取器 {declared_id!r} 与当前导入器 "
            f"{extractor_id!r} 不一致")
    if declared_version and declared_version != extractor_version:
        raise KnowledgeImportError(
            f"导出文件抽取器版本 {declared_version!r} 与导入参数 "
            f"{extractor_version!r} 不一致")
    digest = sha256_file(source_path)
    declared = str((export.get("source") or {}).get("sha256") or "")
    if declared and declared.lower() != digest:
        raise KnowledgeImportError(
            f"导出文件声明的来源摘要 {declared} 与实际 dump {digest} 不一致")
    export_digest = _export_digest(export)
    source_meta = export.get("source") or {}
    media_type = str(source_meta.get("media_type") or MEDIA_TYPE)
    source_version = str(source_meta.get("source_version") or "") or None
    started_at = utc_iso_now()

    with database.session() as session:
        try:
            with session.begin():
                source = session.scalars(select(SourceDocument).where(
                    SourceDocument.sha256 == digest)).one_or_none()
                if source is None:
                    source = SourceDocument(
                        source_id=f"neo4j_{digest[:16]}",
                        uri_or_path=str(source_path),
                        media_type=media_type, sha256=digest,
                        source_version=source_version,
                        registered_at=started_at)
                    session.add(source)
                    session.flush()
                extraction_id = _extraction_id(
                    source.source_id, extractor_id, extractor_version)
                existing = session.get(ExtractionRun, extraction_id)
                if existing is not None:
                    previous_digest = (existing.report_json or {}).get(
                        "export_digest")
                    if previous_digest and previous_digest != export_digest:
                        raise KnowledgeImportError(
                            "同一来源/抽取器版本产生了不同的导出内容; "
                            "请递增 extractor_version 后重试")
                    summary = _summary(existing)
                    summary["changed"] = False
                    return summary

                run = ExtractionRun(
                    extraction_id=extraction_id,
                    source_id=source.source_id,
                    extractor_id=extractor_id,
                    extractor_version=extractor_version,
                    status="staging", started_at=started_at,
                    finished_at=None, report_json=None)
                session.add(run)
                session.flush()

                counts = Counter()
                entity_types: Counter = Counter()
                predicates: Counter = Counter()
                unmapped_entity_types: set[str] = set()
                unmapped_predicates: set[str] = set()
                conflicts: list[dict] = []
                unresolved: list[dict] = []
                seen_entities: dict[str, KnowledgeEntity] = {}
                node_entity: dict[str, str] = {}
                entity_sources: list[dict] = []
                key_counts: Counter = Counter()
                for node in export["nodes"]:
                    labels = list(node["labels"])
                    key_counts[(_entity_type(labels), _canonical_key(
                        dict(node["properties"]), str(node["id"])))] += 1

                for node in export["nodes"]:
                    node_id = str(node["id"])
                    labels = list(node["labels"])
                    properties = dict(node["properties"])
                    raw_type = "|".join(sorted(labels)) or "Node"
                    if raw_type not in ENTITY_TYPE_MAP:
                        unmapped_entity_types.add(raw_type)
                    entity_type = _entity_type(labels)
                    key = _canonical_key(properties, node_id)
                    if key_counts[(entity_type, key)] > 1:
                        # 同名但无业务编码的节点 (如多个“凸台”) 用来源节点
                        # ID 确定性消歧, 避免把真实节点误判为冲突.
                        key = f"{key}#{node_id}"
                        counts["ambiguous_canonical_ids"] += 1
                    canonical_id = f"{entity_type}:{key}"
                    entity_id = canonical_id
                    node_entity[node_id] = entity_id
                    label = _label(properties, key)
                    existing_entity = _entity_for(
                        session, seen_entities, entity_id)
                    if existing_entity is None:
                        existing_entity = KnowledgeEntity(
                            entity_id=entity_id, entity_type=entity_type,
                            canonical_id=canonical_id, label=label,
                            attributes_json=properties,
                            status="staging")
                        session.add(existing_entity)
                        seen_entities[entity_id] = existing_entity
                        counts["entities_inserted"] += 1
                    else:
                        if (existing_entity.entity_type == entity_type
                                and existing_entity.label == label
                                and _same_json(
                                    existing_entity.attributes_json,
                                    properties)):
                            counts["entities_reused"] += 1
                        else:
                            if existing_entity.status != "conflict":
                                existing_entity.status = "conflict"
                            counts["entity_conflicts"] += 1
                            conflicts.append({
                                "kind": "entity", "entity_id": entity_id,
                                "reason": "同一 canonical ID 的属性/标签不一致",
                                "source_locator": f"neo4j:node:{node_id}"})
                    entity_sources.append({
                        "entity_id": entity_id,
                        "source_id": source.source_id,
                        "extraction_id": extraction_id,
                        "source_locator": f"neo4j:node:{node_id}",
                        "raw_fragment": _fragment(
                            node_id, labels, properties),
                        "confidence": 1.0})
                    entity_types[entity_type] += 1
                    counts["nodes"] += 1

                # 先落实体, 再写引用实体的来源行; 无 ORM relationship 时
                # 单次 flush 不保证跨 mapper 的插入顺序.
                session.flush()
                for row in entity_sources:
                    session.add(EntitySource(**row))
                session.flush()
                seen_relations: dict[str, KnowledgeRelation] = {}
                relation_sources: list[dict] = []

                for relation in export["relationships"]:
                    relation_key = str(relation["id"])
                    source_predicate = str(relation["type"])
                    subject_id = node_entity.get(str(relation["start"]))
                    object_id = node_entity.get(str(relation["end"]))
                    if subject_id is None or object_id is None:
                        unresolved.append({
                            "relation_id": relation_key,
                            "type": source_predicate,
                            "start": str(relation["start"]),
                            "end": str(relation["end"]),
                            "reason": "端点未映射到实体"})
                        counts["unresolved_relationships"] += 1
                        continue
                    if source_predicate not in PREDICATE_MAP:
                        unmapped_predicates.add(source_predicate)
                    predicate = PREDICATE_MAP.get(
                        source_predicate, source_predicate)
                    relation_id = _relation_id(
                        subject_id, predicate, object_id)
                    attributes = dict(relation.get("properties") or {})
                    existing_relation = seen_relations.get(relation_id)
                    if existing_relation is None:
                        existing_relation = session.get(
                            KnowledgeRelation, relation_id)
                    if existing_relation is None:
                        existing_relation = KnowledgeRelation(
                            relation_id=relation_id,
                            subject_id=subject_id, predicate=predicate,
                            object_id=object_id,
                            source_predicate=source_predicate,
                            attributes_json=attributes,
                            status="staging")
                        session.add(existing_relation)
                        seen_relations[relation_id] = existing_relation
                        counts["relations_inserted"] += 1
                    else:
                        seen_relations[relation_id] = existing_relation
                        if (existing_relation.source_predicate
                                == source_predicate
                                and _same_json(
                                    existing_relation.attributes_json,
                                    attributes)):
                            counts["relations_reused"] += 1
                        else:
                            if existing_relation.status != "conflict":
                                existing_relation.status = "conflict"
                            counts["relation_conflicts"] += 1
                            conflicts.append({
                                "kind": "relation",
                                "relation_id": relation_id,
                                "reason": "同一三元组的谓词/属性不一致",
                                "source_locator":
                                    f"neo4j:relationship:{relation_key}"})
                    relation_sources.append({
                        "relation_id": relation_id,
                        "source_id": source.source_id,
                        "extraction_id": extraction_id,
                        "source_locator":
                            f"neo4j:relationship:{relation_key}",
                        "raw_fragment": _relation_fragment(relation),
                        "confidence": 1.0})
                    predicates[predicate] += 1
                    counts["relationships"] += 1

                # 先落关系, 再写引用关系的来源行.
                session.flush()
                for row in relation_sources:
                    session.add(RelationSource(**row))
                session.flush()

                finished_at = utc_iso_now()
                conflict_sample, conflict_truncated = _limited(conflicts)
                unresolved_sample, unresolved_truncated = _limited(unresolved)
                report = {
                    "format": EXPORT_FORMAT,
                    "export_digest": export_digest,
                    "source": {
                        "source_id": source.source_id,
                        "path": str(source_path),
                        "sha256": digest,
                        "media_type": media_type,
                        "source_version": source_version,
                    },
                    "extractor": {
                        "id": extractor_id,
                        "version": extractor_version,
                    },
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "counts": dict(counts),
                    "entity_types": dict(sorted(entity_types.items())),
                    "predicates": dict(sorted(predicates.items())),
                    "unmapped_entity_types":
                        sorted(unmapped_entity_types),
                    "unmapped_predicates": sorted(unmapped_predicates),
                    "conflicts": conflict_sample,
                    "conflicts_truncated": conflict_truncated,
                    "unresolved_relationships": unresolved_sample,
                    "unresolved_truncated": unresolved_truncated,
                }
                run.finished_at = finished_at
                run.report_json = report
                session.flush()
                summary = _summary(run)
                summary["changed"] = True
                return summary
        except IntegrityError as exc:
            raise KnowledgeImportError(
                f"数据库约束拒绝知识导入: {exc.orig}") from exc


def _blockers(report: dict) -> list[str]:
    blockers: list[str] = []
    if report.get("unmapped_entity_types"):
        blockers.append(
            f"未映射实体类型: {report['unmapped_entity_types']}")
    if report.get("unmapped_predicates"):
        blockers.append(
            f"未映射关系谓词: {report['unmapped_predicates']}")
    counts = report.get("counts") or {}
    if counts.get("entity_conflicts"):
        blockers.append(
            f"实体冲突 {counts['entity_conflicts']} 条")
    if counts.get("relation_conflicts"):
        blockers.append(
            f"关系冲突 {counts['relation_conflicts']} 条")
    if counts.get("unresolved_relationships"):
        blockers.append(
            f"未解析关系 {counts['unresolved_relationships']} 条")
    return blockers


def publish_extraction(database: CatalogDatabase, extraction_id: str) -> dict:
    """审核通过后发布抽取结果; 有未映射/冲突/悬空关系则拒绝."""
    with database.session() as session:
        with session.begin():
            run = session.get(ExtractionRun, extraction_id)
            if run is None:
                raise KnowledgeStateError(
                    f"extraction {extraction_id!r} 不存在")
            if run.status == "published":
                summary = _summary(run)
                summary["changed"] = False
                return summary
            if run.status != "staging":
                raise KnowledgeStateError(
                    f"extraction {extraction_id!r} 状态为 {run.status!r}, "
                    f"只能发布 staging")
            report = run.report_json or {}
            blockers = _blockers(report)
            if blockers:
                raise KnowledgeStateError(
                    "知识抽取存在未审核项, 拒绝发布: " + "; ".join(blockers))

            entity_ids = list(session.scalars(select(
                EntitySource.entity_id).where(
                EntitySource.extraction_id == extraction_id).distinct()))
            relation_ids = list(session.scalars(select(
                RelationSource.relation_id).where(
                RelationSource.extraction_id == extraction_id).distinct()))
            for entity in session.scalars(select(KnowledgeEntity).where(
                    KnowledgeEntity.entity_id.in_(entity_ids))):
                if entity.status == "staging":
                    entity.status = "published"
            for relation in session.scalars(select(KnowledgeRelation).where(
                    KnowledgeRelation.relation_id.in_(relation_ids))):
                if relation.status == "staging":
                    relation.status = "published"
            run.status = "published"
            session.flush()
            summary = _summary(run)
            summary["changed"] = True
            summary["entities"] = len(entity_ids)
            summary["relations"] = len(relation_ids)
            return summary


def _selected_run(session: Session,
                  extraction_id: Optional[str]) -> ExtractionRun:
    if extraction_id:
        run = session.get(ExtractionRun, extraction_id)
    else:
        run = session.scalars(select(ExtractionRun).order_by(
            ExtractionRun.started_at.desc(),
            ExtractionRun.extraction_id.desc()).limit(1)).one_or_none()
    if run is None:
        raise KnowledgeStateError("没有可用的知识抽取运行")
    return run


def knowledge_status(database: CatalogDatabase,
                     extraction_id: Optional[str] = None) -> dict:
    with database.session() as session:
        runs = list(session.scalars(select(ExtractionRun).order_by(
            ExtractionRun.started_at, ExtractionRun.extraction_id)))
        selected = _selected_run(session, extraction_id)
        entity_count = int(session.scalar(select(func.count(func.distinct(
            EntitySource.entity_id))).where(
            EntitySource.extraction_id == selected.extraction_id)) or 0)
        relation_count = int(session.scalar(select(func.count(func.distinct(
            RelationSource.relation_id))).where(
            RelationSource.extraction_id == selected.extraction_id)) or 0)
        summary = _summary(selected)
        summary["entity_count"] = entity_count
        summary["relation_count"] = relation_count
        return {
            "revision": current_revision(database),
            "extractions": [_summary(run) for run in runs],
            "selected": summary,
        }


def knowledge_verify(database: CatalogDatabase,
                     extraction_id: Optional[str] = None) -> dict:
    errors: list[str] = []
    revision = current_revision(database)
    with database.connect() as connection:
        integrity = [str(row[0]) for row in connection.execute(
            text("PRAGMA integrity_check")).fetchall()]
        if integrity != ["ok"]:
            errors.append(f"SQLite integrity_check: {integrity}")
        fk_rows = connection.execute(
            text("PRAGMA foreign_key_check")).fetchall()
        if fk_rows:
            errors.append(
                f"外键违规 {len(fk_rows)} 条: "
                f"{[tuple(row) for row in fk_rows[:5]]}")
    selected: Optional[dict] = None
    try:
        with database.session() as session:
            run = _selected_run(session, extraction_id)
            entity_count = int(session.scalar(select(func.count(
                func.distinct(EntitySource.entity_id))).where(
                EntitySource.extraction_id == run.extraction_id)) or 0)
            relation_count = int(session.scalar(select(func.count(
                func.distinct(RelationSource.relation_id))).where(
                RelationSource.extraction_id == run.extraction_id)) or 0)
            selected = _summary(run)
            selected["entity_count"] = entity_count
            selected["relation_count"] = relation_count
            selected["report"] = run.report_json
            blockers = _blockers(run.report_json or {})
            if blockers:
                errors.extend(blockers)
    except Exception as exc:      # 完整性错误统一进入报告
        errors.append(f"{getattr(exc, 'code', type(exc).__name__)}: {exc}")
    return {"ok": not errors, "errors": errors, "revision": revision,
            "selected": selected}
