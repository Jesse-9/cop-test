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
    empty = {"available": True, "releases": [], "published": None,
             "counts": {}, "recipes": [], "materials": [], "resources": [],
             "zones": [], "capabilities": [], "devices": [],
             "scene_bindings": [], "operations": [], "routes": [],
             "entries": [], "dependencies": []}
    if "catalog_release" not in tables:
        empty["available"] = False
        return empty
    releases = [dict(row) for row in connection.execute(
        "SELECT release_id, catalog_version, status, created_at, "
        "published_at FROM catalog_release ORDER BY created_at, release_id")]
    published = next((row for row in releases
                      if row["status"] == "published"), None)
    if published is None:
        return {**empty, "releases": releases}
    release_id = published["release_id"]

    def rows(sql: str) -> list[dict]:
        return [dict(row) for row in connection.execute(sql, (release_id,))]

    count_tables = (
        "material_type", "resource", "zone", "zone_material_type",
        "capability", "device", "device_capability", "device_resource",
        "scene_binding", "recipe", "operation_template",
        "operation_dependency", "operation_input", "operation_output",
        "operation_candidate_device", "operation_resource_request",
        "operation_work_step", "route_variant", "route_operation",
        "order_entry",
    )
    counts = {}
    for table in count_tables:
        if table in tables:
            counts[table] = int(connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE release_id = ?",
                (release_id,)).fetchone()[0])

    materials = rows(
        "SELECT material_type_id, ordinal, description, color_json "
        "FROM material_type WHERE release_id = ? ORDER BY ordinal")
    for material in materials:
        color = material.get("color_json")
        if isinstance(color, str):
            try:
                color = json.loads(color)
            except json.JSONDecodeError:
                color = None
        material["color_json"] = color
    resources = rows(
        "SELECT resource_id, kind, role, capacity, acquire_order, policy "
        "FROM resource WHERE release_id = ? ORDER BY acquire_order")
    zones = rows(
        "SELECT zone_id, ordinal, capacity, description FROM zone "
        "WHERE release_id = ? ORDER BY ordinal")
    capabilities = rows(
        "SELECT capability_id, ordinal, description FROM capability "
        "WHERE release_id = ? ORDER BY ordinal")
    device_caps: dict[str, list[str]] = {}
    for row in rows(
            "SELECT device_id, capability_id FROM device_capability "
            "WHERE release_id = ? ORDER BY device_id, ordinal"):
        device_caps.setdefault(row["device_id"], []).append(
            row["capability_id"])
    device_resources: dict[str, list[str]] = {}
    for row in rows(
            "SELECT device_id, resource_id FROM device_resource "
            "WHERE release_id = ? ORDER BY device_id, ordinal"):
        device_resources.setdefault(row["device_id"], []).append(
            row["resource_id"])
    devices = []
    for row in rows(
            "SELECT device_id, kind, physical, default_line_id, ordinal "
            "FROM device WHERE release_id = ? ORDER BY ordinal"):
        item = dict(row)
        item["physical"] = bool(item["physical"])
        item["capabilities"] = device_caps.get(item["device_id"], [])
        item["resources"] = device_resources.get(item["device_id"], [])
        devices.append(item)
    scene_bindings = rows(
        "SELECT binding_id, device_id, scene_id, contract_version, role, alias "
        "FROM scene_binding WHERE release_id = ? ORDER BY ordinal")
    recipes = rows(
        "SELECT recipe_id, version, product_type, join_operation_id, ordinal "
        "FROM recipe WHERE release_id = ? ORDER BY ordinal")

    route_rows = rows(
        "SELECT recipe_id, route_id, ordinal, route_group, semantics, "
        "entry_zone_id, exit_zone_id FROM route_variant "
        "WHERE release_id = ? ORDER BY recipe_id, ordinal")
    route_operations: dict[tuple[str, str], list[str]] = {}
    for row in rows(
            "SELECT recipe_id, route_id, ordinal, operation_id "
            "FROM route_operation WHERE release_id = ? "
            "ORDER BY recipe_id, route_id, ordinal"):
        route_operations.setdefault(
            (row["recipe_id"], row["route_id"]), []).append(
            row["operation_id"])
    routes = []
    for row in route_rows:
        item = dict(row)
        item["operation_ids"] = route_operations.get(
            (item["recipe_id"], item["route_id"]), [])
        routes.append(item)

    operation_rows = rows(
        "SELECT operation_id, recipe_id, ordinal, kind, semantics, "
        "executor_kind, capability_id, priority, resource_policy, "
        "predecessor_policy, completion_verifier, source_kind, sink_kind, "
        "dock, route_origin_dock, route_destination_dock "
        "FROM operation_template WHERE release_id = ? "
        "ORDER BY recipe_id, ordinal")
    inputs: dict[str, list[dict]] = {}
    for row in rows(
            "SELECT operation_id, ordinal, zone_id, material_type_id "
            "FROM operation_input WHERE release_id = ? "
            "ORDER BY operation_id, ordinal"):
        inputs.setdefault(row["operation_id"], []).append({
            "ordinal": row["ordinal"], "zone": row["zone_id"],
            "item_type": row["material_type_id"]})
    outputs = {}
    for row in rows(
            "SELECT operation_id, zone_id, material_type_id "
            "FROM operation_output WHERE release_id = ?"):
        outputs[row["operation_id"]] = {
            "zone": row["zone_id"], "item_type": row["material_type_id"]}
    candidates: dict[str, list[dict]] = {}
    for row in rows(
            "SELECT operation_id, ordinal, device_id "
            "FROM operation_candidate_device WHERE release_id = ? "
            "ORDER BY operation_id, ordinal"):
        candidates.setdefault(row["operation_id"], []).append({
            "ordinal": row["ordinal"], "device_id": row["device_id"]})
    requests: dict[str, list[dict]] = {}
    for row in rows(
            "SELECT operation_id, ordinal, resource_id, quantity "
            "FROM operation_resource_request WHERE release_id = ? "
            "ORDER BY operation_id, ordinal"):
        requests.setdefault(row["operation_id"], []).append({
            "ordinal": row["ordinal"], "resource_id": row["resource_id"],
            "quantity": row["quantity"]})
    work_steps: dict[str, list[dict]] = {}
    for row in rows(
            "SELECT operation_id, ordinal, template_id "
            "FROM operation_work_step WHERE release_id = ? "
            "ORDER BY operation_id, ordinal"):
        work_steps.setdefault(row["operation_id"], []).append({
            "ordinal": row["ordinal"], "template_id": row["template_id"]})
    dependencies = rows(
        "SELECT operation_id, predecessor_operation_id, ordinal, "
        "successor_ordinal FROM operation_dependency WHERE release_id = ? "
        "ORDER BY operation_id, ordinal")
    predecessors: dict[str, list[dict]] = {}
    successors: dict[str, list[dict]] = {}
    for row in dependencies:
        predecessors.setdefault(row["operation_id"], []).append({
            "ordinal": row["ordinal"],
            "operation_id": row["predecessor_operation_id"]})
        successors.setdefault(
            row["predecessor_operation_id"], []).append({
                "ordinal": row["successor_ordinal"],
                "operation_id": row["operation_id"]})
    route_membership: dict[str, list[dict]] = {}
    for row in rows(
            "SELECT recipe_id, route_id, ordinal, operation_id "
            "FROM route_operation WHERE release_id = ? "
            "ORDER BY recipe_id, route_id, ordinal"):
        route_membership.setdefault(row["operation_id"], []).append({
            "recipe_id": row["recipe_id"], "route_id": row["route_id"],
            "ordinal": row["ordinal"]})
    entries = rows(
        "SELECT recipe_id, entry_id, ordinal, operation_id, line_id, "
        "material_type_id, route_group, route_id FROM order_entry "
        "WHERE release_id = ? ORDER BY recipe_id, ordinal")
    entries_by_operation: dict[str, list[dict]] = {}
    for entry in entries:
        entries_by_operation.setdefault(entry["operation_id"], []).append(
            entry)
    operations = []
    for row in operation_rows:
        item = dict(row)
        item["inputs"] = inputs.get(item["operation_id"], [])
        item["output"] = outputs.get(item["operation_id"])
        item["candidates"] = candidates.get(item["operation_id"], [])
        item["resources"] = requests.get(item["operation_id"], [])
        item["work_steps"] = work_steps.get(item["operation_id"], [])
        item["predecessors"] = predecessors.get(item["operation_id"], [])
        item["successors"] = successors.get(item["operation_id"], [])
        item["routes"] = route_membership.get(item["operation_id"], [])
        item["entries"] = entries_by_operation.get(item["operation_id"], [])
        operations.append(item)
    return {
        "available": True,
        "releases": releases,
        "published": dict(published),
        "counts": counts,
        "recipes": recipes,
        "materials": materials,
        "resources": resources,
        "zones": zones,
        "capabilities": capabilities,
        "devices": devices,
        "scene_bindings": scene_bindings,
        "operations": operations,
        "routes": routes,
        "entries": entries,
        "dependencies": dependencies,
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
