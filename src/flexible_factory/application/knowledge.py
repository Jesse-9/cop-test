"""application.knowledge — 知识图谱导出导入用例.

CLI 只通过本模块调用 persistence; 二进制 Neo4j dump 的载入与 Bolt 导出由
``tools/neo4j_export.py`` 完成, 本模块只消费其 JSON 结果.
"""

from __future__ import annotations

from pathlib import Path

from ..persistence.db import CatalogDatabase
from ..persistence.knowledge import (EXPORT_FORMAT, EXTRACTOR_ID,
                                     EXTRACTOR_VERSION, knowledge_status,
                                     knowledge_verify, load_export,
                                     publish_extraction,
                                     stage_knowledge_export)

__all__ = [
    "EXPORT_FORMAT", "EXTRACTOR_ID", "EXTRACTOR_VERSION",
    "import_knowledge_export", "publish_knowledge", "knowledge_status",
    "knowledge_verify",
]


def import_knowledge_export(
        database: CatalogDatabase, *, export_path: Path,
        source_dump: Path,
        extractor_version: str = EXTRACTOR_VERSION) -> dict:
    payload = load_export(Path(export_path))
    return stage_knowledge_export(
        database, payload, source_dump=Path(source_dump),
        extractor_id=EXTRACTOR_ID, extractor_version=extractor_version)


def publish_knowledge(database: CatalogDatabase,
                      extraction_id: str) -> dict:
    return publish_extraction(database, extraction_id)
