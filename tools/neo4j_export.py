#!/usr/bin/env python
"""Export a loaded Neo4j database to the knowledge-import JSON format.

Prerequisites:
  * the official ``fmpmm.dump`` has been loaded with ``neo4j-admin database
    load`` and Neo4j is running (see tools/neo4j/README.md);
  * the ``neo4j`` Python driver is installed (``uv sync --group dev``).

The script only runs read-only ``MATCH ... RETURN`` queries; it never writes
to Neo4j and never parses the binary dump.

Example (PowerShell):

    $env:NEO4J_PASSWORD = "<password>"
    uv run python tools/neo4j_export.py `
      --source-dump ref/fmpmm.dump `
      --out docs/implementation/runs/fmpmm_graph.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any

try:
    from neo4j import GraphDatabase
except ImportError:  # pragma: no cover - 依赖缺失时的操作提示
    print("[ERROR] 缺少 neo4j 驱动; 先执行 uv sync --group dev",
          file=sys.stderr)
    raise SystemExit(2)

from flexible_factory.persistence.knowledge import (EXPORT_FORMAT,
                                                    EXTRACTOR_ID,
                                                    EXTRACTOR_VERSION,
                                                    MEDIA_TYPE)
from flexible_factory.util import write_json_atomic

NODES_QUERY = """
MATCH (n)
RETURN elementId(n) AS id, labels(n) AS labels, properties(n) AS properties
ORDER BY elementId(n)
"""

RELATIONSHIPS_QUERY = """
MATCH (a)-[r]->(b)
RETURN elementId(r) AS id, type(r) AS type, elementId(a) AS start,
       elementId(b) AS end, properties(r) AS properties
ORDER BY elementId(r)
"""


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        try:
            return isoformat()
        except Exception:
            pass
    return str(value)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="只读导出 Neo4j 图为知识导入 JSON")
    parser.add_argument("--uri", default=os.environ.get(
        "NEO4J_URI", "bolt://localhost:7687"))
    parser.add_argument("--user", default=os.environ.get(
        "NEO4J_USER", "neo4j"))
    parser.add_argument("--password", default=os.environ.get(
        "NEO4J_PASSWORD", ""))
    parser.add_argument("--database", default=os.environ.get(
        "NEO4J_DATABASE", "neo4j"))
    parser.add_argument("--source-dump", required=True,
                        help="原始 dump 路径 (只计算 SHA-256)")
    parser.add_argument("--source-version", default=os.environ.get(
        "NEO4J_SOURCE_VERSION", "neo4j"))
    parser.add_argument("--out", required=True,
                        help="导出 JSON 路径")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    source = Path(args.source_dump)
    if not source.is_file():
        print(f"[ERROR] dump 不存在: {source}", file=sys.stderr)
        return 2
    if not args.password:
        print("[ERROR] 未提供 Neo4j 密码; 设置 NEO4J_PASSWORD 或 --password",
              file=sys.stderr)
        return 2

    digest = _sha256(source)
    driver = GraphDatabase.driver(
        args.uri, auth=(args.user, args.password))
    try:
        driver.verify_connectivity()
        with driver.session(database=args.database) as session:
            nodes = [{
                "id": str(record["id"]),
                "labels": list(record["labels"]),
                "properties": _json_safe(record["properties"] or {}),
            } for record in session.run(NODES_QUERY)]
            relationships = [{
                "id": str(record["id"]),
                "type": record["type"],
                "start": str(record["start"]),
                "end": str(record["end"]),
                "properties": _json_safe(record["properties"] or {}),
            } for record in session.run(RELATIONSHIPS_QUERY)]
    except Exception as exc:
        print(f"[ERROR] Neo4j 导出失败: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1
    finally:
        driver.close()

    payload = {
        "format": EXPORT_FORMAT,
        "source": {
            "path": str(source),
            "sha256": digest,
            "media_type": MEDIA_TYPE,
            "source_version": args.source_version,
        },
        "extractor": {
            "id": EXTRACTOR_ID,
            "version": EXTRACTOR_VERSION,
        },
        "exported_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
        "nodes": nodes,
        "relationships": relationships,
    }
    write_json_atomic(Path(args.out), payload)
    print(json.dumps({
        "ok": True,
        "out": str(args.out),
        "source_sha256": digest,
        "nodes": len(nodes),
        "relationships": len(relationships),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
