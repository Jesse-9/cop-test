"""v1 知识图谱导入: Neo4j 导出 → staging → 审核发布与来源追溯."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from flexible_factory.cli import main
from flexible_factory.persistence.db import (CatalogDatabase,
                                              initialize_database)
from flexible_factory.persistence.errors import (KnowledgeImportError,
                                                  KnowledgeStateError)
from flexible_factory.persistence.knowledge import (EXPORT_FORMAT,
                                                    EXTRACTOR_ID,
                                                    EXTRACTOR_VERSION,
                                                    MEDIA_TYPE,
                                                    knowledge_status,
                                                    knowledge_verify,
                                                    load_export,
                                                    publish_extraction,
                                                    stage_knowledge_export,
                                                    validate_export)
from flexible_factory.util import sha256_file


@pytest.fixture()
def database(tmp_path) -> CatalogDatabase:
    db = CatalogDatabase(tmp_path / "knowledge.db")
    initialize_database(db)
    return db


def _source_file(tmp_path, name: str = "fmpmm.dump",
                 data: bytes = b"DZV1-test-dump") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _node(node_id: str, label: str, **properties) -> dict:
    return {"id": node_id, "labels": [label],
            "properties": properties}


def _relationship(relation_id: str, source_type: str,
                  start: str, end: str, **properties) -> dict:
    return {"id": relation_id, "type": source_type, "start": start,
            "end": end, "properties": properties}


def _export(source: Path, nodes: list[dict],
            relationships: list[dict]) -> dict:
    return {
        "format": EXPORT_FORMAT,
        "source": {
            "path": str(source),
            "sha256": sha256_file(source),
            "media_type": MEDIA_TYPE,
            "source_version": "neo4j-5.13",
        },
        "extractor": {"id": EXTRACTOR_ID, "version": EXTRACTOR_VERSION},
        "nodes": nodes,
        "relationships": relationships,
    }


def _clean_graph(source: Path) -> dict:
    return _export(source, [
        _node("1", "Instance", pro_code="TESTBOX", name="零件1"),
        _node("2", "Instance", pro_code="TESTCOVER", name="零件2"),
        _node("3", "Instance", pro_code="AUR0001", name="机械臂1"),
        _node("4", "Instance", pro_code="GDH0001", name="夹具1"),
        _node("5", "Goal", name="工艺目标90"),
        _node("6", "Instance", pro_code="FEATURE_X", name="装配特征"),
    ], [
        _relationship("r1", "achieve_goal", "1", "5"),
        _relationship("r2", "achieve_goal", "2", "5"),
        _relationship("r3", "has_capability", "1", "6"),
        _relationship("r4", "installed_on", "4", "3"),
    ])


def _count(database: CatalogDatabase, table: str) -> int:
    with database.connect() as connection:
        return int(connection.execute(
            text(f"SELECT COUNT(*) FROM {table}")).scalar())


# ═══════════════════════════════════════════════════════════════
# 导出格式校验
# ═══════════════════════════════════════════════════════════════

def test_validate_export_rejects_bad_format_and_dangling_endpoint(tmp_path):
    source = _source_file(tmp_path)
    payload = _clean_graph(source)
    payload["format"] = "unknown/1"
    with pytest.raises(KnowledgeImportError):
        validate_export(payload)

    payload = _clean_graph(source)
    payload["relationships"][0]["end"] = "missing"
    with pytest.raises(KnowledgeImportError):
        validate_export(payload)

    payload = _clean_graph(source)
    payload["nodes"].append(dict(payload["nodes"][0]))
    with pytest.raises(KnowledgeImportError):
        validate_export(payload)


def test_load_export_reads_json(tmp_path):
    source = _source_file(tmp_path)
    export_path = tmp_path / "graph.json"
    export_path.write_text(json.dumps(_clean_graph(source)),
                           encoding="utf-8")
    payload = load_export(export_path)
    assert payload["format"] == EXPORT_FORMAT
    assert len(payload["nodes"]) == 6


# ═══════════════════════════════════════════════════════════════
# staging / 幂等 / 冲突
# ═══════════════════════════════════════════════════════════════

def test_stage_export_records_provenance_and_counts(database, tmp_path):
    source = _source_file(tmp_path)
    result = stage_knowledge_export(
        database, _clean_graph(source), source_dump=source)
    assert result["status"] == "staging"
    assert result["changed"] is True
    assert result["counts"]["nodes"] == 6
    assert result["counts"]["relationships"] == 4
    assert result["counts"]["entities_inserted"] == 6
    assert result["counts"]["relations_inserted"] == 4
    assert result["unmapped_entity_types"] == []
    assert result["unmapped_predicates"] == []

    assert _count(database, "source_document") == 1
    assert _count(database, "extraction_run") == 1
    assert _count(database, "knowledge_entity") == 6
    assert _count(database, "knowledge_relation") == 4
    assert _count(database, "entity_source") == 6
    assert _count(database, "relation_source") == 4

    status = knowledge_status(database, result["extraction_id"])
    assert status["selected"]["entity_count"] == 6
    assert status["selected"]["relation_count"] == 4
    assert knowledge_verify(database, result["extraction_id"])["ok"] is True


def test_same_export_is_idempotent(database, tmp_path):
    source = _source_file(tmp_path)
    export = _clean_graph(source)
    first = stage_knowledge_export(database, export, source_dump=source)
    second = stage_knowledge_export(database, export, source_dump=source)
    assert first["changed"] is True
    assert second["changed"] is False
    assert second["extraction_id"] == first["extraction_id"]
    assert _count(database, "source_document") == 1
    assert _count(database, "extraction_run") == 1
    assert _count(database, "knowledge_entity") == 6
    assert _count(database, "entity_source") == 6


def test_changed_export_requires_new_extractor_version(database, tmp_path):
    source = _source_file(tmp_path)
    stage_knowledge_export(
        database, _clean_graph(source), source_dump=source)
    changed = _clean_graph(source)
    changed["nodes"][0]["properties"]["name"] = "零件1改"
    with pytest.raises(KnowledgeImportError):
        stage_knowledge_export(database, changed, source_dump=source)
    assert _count(database, "extraction_run") == 1


def test_entity_conflict_blocks_publish(database, tmp_path):
    first_source = _source_file(tmp_path, "first.dump", b"first-dump")
    stage_knowledge_export(
        database, _clean_graph(first_source), source_dump=first_source)

    second_source = _source_file(tmp_path, "second.dump", b"second-dump")
    conflict = _clean_graph(second_source)
    conflict["nodes"][0]["properties"]["name"] = "冲突名"
    result = stage_knowledge_export(
        database, conflict, source_dump=second_source)
    assert result["counts"]["entity_conflicts"] == 1
    with pytest.raises(KnowledgeStateError):
        publish_extraction(database, result["extraction_id"])
    with database.connect() as connection:
        status = connection.execute(text(
            "SELECT status FROM knowledge_entity "
            "WHERE entity_id = 'Instance:TESTBOX'")).scalar()
    assert status == "conflict"


def test_unmapped_values_block_publish(database, tmp_path):
    source = _source_file(tmp_path)
    export = _export(source, [
        _node("1", "Instance", pro_code="TESTBOX", name="零件1"),
        _node("2", "Instance", pro_code="TESTCOVER", name="零件2"),
        _node("3", "NewLabel", pro_code="NEW001", name="新类型"),
    ], [
        _relationship("r1", "custom_relation", "1", "2"),
    ])
    result = stage_knowledge_export(database, export, source_dump=source)
    assert result["unmapped_entity_types"] == ["NewLabel"]
    assert result["unmapped_predicates"] == ["custom_relation"]
    with pytest.raises(KnowledgeStateError):
        publish_extraction(database, result["extraction_id"])


def test_clean_export_publishes_all_facts(database, tmp_path):
    source = _source_file(tmp_path)
    result = stage_knowledge_export(
        database, _clean_graph(source), source_dump=source)
    published = publish_extraction(database, result["extraction_id"])
    assert published["status"] == "published"
    assert published["changed"] is True
    assert published["entities"] == 6
    assert published["relations"] == 4
    with database.connect() as connection:
        assert connection.execute(text(
            "SELECT COUNT(*) FROM knowledge_entity "
            "WHERE status = 'published'")).scalar() == 6
        assert connection.execute(text(
            "SELECT COUNT(*) FROM knowledge_relation "
            "WHERE status = 'published'")).scalar() == 4
    assert knowledge_verify(database, result["extraction_id"])["ok"] is True


def test_low_confidence_and_missing_source_are_rejected(database, tmp_path):
    source = _source_file(tmp_path)
    result = stage_knowledge_export(
        database, _clean_graph(source), source_dump=source)
    with database.connect() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(text(
                "UPDATE entity_source SET confidence = 2.0 "
                "WHERE extraction_id = :id"),
                {"id": result["extraction_id"]})
            connection.commit()
        connection.rollback()
    with pytest.raises(KnowledgeImportError):
        stage_knowledge_export(
            database, _clean_graph(source),
            source_dump=tmp_path / "missing.dump")


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def test_cli_knowledge_import_status_verify_publish(tmp_path, capsys):
    source = _source_file(tmp_path)
    export_path = tmp_path / "graph.json"
    export_path.write_text(json.dumps(_clean_graph(source)),
                           encoding="utf-8")
    db_path = tmp_path / "catalog.db"
    assert main(["knowledge", "import", "--db", str(db_path),
                 "--export", str(export_path),
                 "--source-dump", str(source)]) == 0
    imported = json.loads(capsys.readouterr().out)
    extraction_id = imported["extraction_id"]
    assert imported["status"] == "staging"

    assert main(["knowledge", "status", "--db", str(db_path),
                 "--extraction-id", extraction_id]) == 0
    assert main(["knowledge", "verify", "--db", str(db_path),
                 "--extraction-id", extraction_id]) == 0
    assert main(["knowledge", "publish", "--db", str(db_path),
                 "--extraction-id", extraction_id]) == 0
    assert main(["knowledge", "status", "--db", str(db_path)]) == 0
    output = capsys.readouterr().out
    assert "published" in output
