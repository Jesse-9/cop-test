#!/usr/bin/env python
"""Generate a self-contained HTML dashboard from the local SQLite database.

The page has no external dependencies: CSS, JavaScript and data are embedded.
It shows the knowledge graph (SQLite copy), catalog release summary, and Q1-Q4
run-record data when present.

Usage (PowerShell, repo root):

    uv run python tools/db_dashboard.py `
      --db var/catalog/catalog.db `
      --out docs/implementation/runs/db_dashboard.html

Open the output file directly in a browser. The database is read-only.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from flexible_factory.persistence.db import CatalogDatabase
from flexible_factory.persistence.runs import (list_runs, material_genealogy,
                                               operation_timeline,
                                               resource_waits, run_summary)

TEMPLATE = Path(__file__).with_name("db_dashboard_template.html")


def _ro_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}


def _revision(connection: sqlite3.Connection) -> str:
    try:
        row = connection.execute(
            "SELECT version_num FROM alembic_version").fetchone()
        return "" if row is None else str(row[0])
    except sqlite3.Error:
        return ""


def _knowledge(connection: sqlite3.Connection,
               tables: set[str]) -> dict:
    if "knowledge_entity" not in tables:
        return {"available": False, "entities": 0, "relations": 0,
                "entity_types": [], "predicates": [], "statuses": [],
                "nodes": [], "edges": [], "sources": [], "extractions": []}
    entity_types = [dict(row) for row in connection.execute(
        "SELECT entity_type, COUNT(*) AS count FROM knowledge_entity "
        "GROUP BY entity_type ORDER BY count DESC, entity_type")]
    statuses = [dict(row) for row in connection.execute(
        "SELECT status, COUNT(*) AS count FROM knowledge_entity "
        "GROUP BY status ORDER BY status")]
    predicates = [dict(row) for row in connection.execute(
        "SELECT predicate, COUNT(*) AS count FROM knowledge_relation "
        "GROUP BY predicate ORDER BY count DESC, predicate")]
    nodes = [dict(row) for row in connection.execute(
        "SELECT entity_id AS id, entity_type AS type, canonical_id, "
        "label, status FROM knowledge_entity ORDER BY entity_type, label")]
    edges = [dict(row) for row in connection.execute(
        "SELECT subject_id AS source, predicate, object_id AS target, "
        "status FROM knowledge_relation "
        "ORDER BY predicate, subject_id, object_id")]
    sources = [dict(row) for row in connection.execute(
        "SELECT source_id, uri_or_path, media_type, sha256, source_version, "
        "registered_at FROM source_document ORDER BY registered_at")]
    extractions = []
    for row in connection.execute(
            "SELECT extraction_id, source_id, extractor_id, extractor_version, "
            "status, started_at, finished_at, report_json "
            "FROM extraction_run ORDER BY started_at DESC"):
        item = dict(row)
        report = item.pop("report_json", None)
        try:
            report = json.loads(report) if report else {}
        except json.JSONDecodeError:
            report = {}
        item["counts"] = report.get("counts", {})
        item["unmapped_entity_types"] = report.get(
            "unmapped_entity_types", [])
        item["unmapped_predicates"] = report.get("unmapped_predicates", [])
        extractions.append(item)
    return {
        "available": True,
        "entities": len(nodes),
        "relations": len(edges),
        "entity_types": entity_types,
        "predicates": predicates,
        "statuses": statuses,
        "nodes": nodes,
        "edges": edges,
        "sources": sources,
        "extractions": extractions,
    }


def _catalog(connection: sqlite3.Connection,
             tables: set[str]) -> dict:
    if "catalog_release" not in tables:
        return {"available": False, "releases": [], "published": None,
                "counts": {}, "operations": [], "routes": []}
    releases = [dict(row) for row in connection.execute(
        "SELECT release_id, catalog_version, status, created_at, "
        "published_at FROM catalog_release ORDER BY created_at, release_id")]
    published = next((row for row in releases
                      if row["status"] == "published"), None)
    if published is None:
        return {"available": True, "releases": releases, "published": None,
                "counts": {}, "operations": [], "routes": []}
    release_id = published["release_id"]
    count_tables = (
        "material_type", "resource", "zone", "capability", "device",
        "recipe", "operation_template", "route_variant", "order_entry",
        "scene_binding",
    )
    counts = {}
    for table in count_tables:
        if table in tables:
            counts[table] = int(connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE release_id = ?",
                (release_id,)).fetchone()[0])
    operations = [dict(row) for row in connection.execute(
        "SELECT operation_id, kind, executor_kind, priority, "
        "resource_policy, semantics FROM operation_template "
        "WHERE release_id = ? ORDER BY recipe_id, ordinal", (release_id,))]
    routes = [dict(row) for row in connection.execute(
        "SELECT route_id, route_group, entry_zone_id, exit_zone_id "
        "FROM route_variant WHERE release_id = ? "
        "ORDER BY recipe_id, ordinal", (release_id,))]
    return {
        "available": True,
        "releases": releases,
        "published": dict(published),
        "counts": counts,
        "operations": operations,
        "routes": routes,
    }


def _runs(database: CatalogDatabase, *, limit: int) -> list[dict]:
    rows = list_runs(database, limit=limit)
    payload: list[dict] = []
    for row in rows:
        run_id = row["run_id"]
        item = dict(row)
        try:
            summary = run_summary(database, run_id)
            item["counts"] = summary.get("counts", {})
            item["final_uids"] = summary.get("final_uids", [])
            item["result"] = summary.get("result")
        except Exception as exc:
            item["counts"] = {}
            item["final_uids"] = []
            item["result"] = None
            item["error"] = f"{type(exc).__name__}: {exc}"
        try:
            timeline = operation_timeline(database, run_id=run_id)
            item["operations"] = [{
                key: value for key, value in instance.items()
                if key in ("instance_id", "template_id", "kind", "task_id",
                           "uid", "state", "phase", "selected_device_id",
                           "created_sim_time", "started_sim_time",
                           "committed_sim_time", "duration_s",
                           "ready_wait_s", "failure_reason",
                           "input_uids", "output_uids")
            } for instance in timeline.get("instances", [])]
            item["failures"] = timeline.get("failures", [])
        except Exception as exc:
            item["operations"] = []
            item["failures"] = []
            item.setdefault("error", f"{type(exc).__name__}: {exc}")
        try:
            waits = resource_waits(database, run_id=run_id)
            item["resources"] = waits.get("resources", [])
            item["waits"] = waits.get("waits", [])[:200]
            item["waits_total"] = len(waits.get("waits", []))
        except Exception as exc:
            item["resources"] = []
            item["waits"] = []
            item["waits_total"] = 0
            item.setdefault("error", f"{type(exc).__name__}: {exc}")
        item["genealogy"] = None
        final_uids = item.get("final_uids") or []
        if final_uids:
            try:
                item["genealogy"] = material_genealogy(
                    database, uid=int(final_uids[0]), run_id=run_id)
            except Exception as exc:
                item.setdefault("error", f"{type(exc).__name__}: {exc}")
        payload.append(item)
    return payload


def build_payload(db_path: Path, *, runs_limit: int = 10) -> dict:
    path = Path(db_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"数据库不存在: {path}")
    connection = _ro_connection(path)
    try:
        tables = _tables(connection)
        revision = _revision(connection)
        knowledge = _knowledge(connection, tables)
        catalog = _catalog(connection, tables)
    finally:
        connection.close()
    database = CatalogDatabase(path)
    runs = _runs(database, limit=runs_limit) if {
        "production_run", "operation_instance", "material_instance",
        "resource_event",
    } <= tables else []
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
        "db_path": str(path),
        "revision": revision,
        "knowledge": knowledge,
        "catalog": catalog,
        "runs": runs,
        "counts": {
            "entities": knowledge["entities"],
            "relations": knowledge["relations"],
            "entity_types": len(knowledge["entity_types"]),
            "predicates": len(knowledge["predicates"]),
            "runs": len(runs),
        },
    }


def render_html(payload: dict, *, template: Path = TEMPLATE) -> str:
    template_text = template.read_text(encoding="utf-8")
    payload_json = json.dumps(payload, ensure_ascii=False)
    payload_json = payload_json.replace("</", "<\\/")
    if "__PAYLOAD__" not in template_text:
        raise ValueError(f"模板缺少 __PAYLOAD__: {template}")
    return template_text.replace("__PAYLOAD__", payload_json)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="从本地 SQLite 生成自包含 HTML 数据库仪表盘")
    parser.add_argument("--db", default="var/catalog/catalog.db")
    parser.add_argument(
        "--out", default="docs/implementation/runs/db_dashboard.html")
    parser.add_argument("--runs-limit", type=int, default=10)
    parser.add_argument("--open", action="store_true",
                        help="生成后用默认浏览器打开")
    args = parser.parse_args(argv)
    try:
        payload = build_payload(Path(args.db),
                                runs_limit=max(1, args.runs_limit))
        html = render_html(payload)
    except Exception as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "out": str(out.resolve()),
        "db": payload["db_path"],
        "revision": payload["revision"],
        "entities": payload["counts"]["entities"],
        "relations": payload["counts"]["relations"],
        "runs": payload["counts"]["runs"],
    }, ensure_ascii=False, indent=2))
    if args.open:
        import webbrowser

        webbrowser.open(out.resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
