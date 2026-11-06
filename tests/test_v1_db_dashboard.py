"""本地数据库仪表盘生成器的轻量回归测试."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import db_dashboard  # noqa: E402
from flexible_factory.application.catalog import (  # noqa: E402
    stage_default_catalog)
from flexible_factory.persistence.db import (CatalogDatabase,  # noqa: E402
                                              initialize_database)
from flexible_factory.persistence.runs import RunRecorder  # noqa: E402
from flexible_factory.persistence.service import publish_release  # noqa: E402


def _empty_payload() -> dict:
    return {
        "generated_at": "2026-11-05T00:00:00+00:00",
        "db_path": "test.db",
        "revision": "0003_run_records",
        "knowledge": {
            "available": True, "entities": 0, "relations": 0,
            "entity_types": [], "predicates": [], "statuses": [],
            "nodes": [], "edges": [], "sources": [], "extractions": [],
        },
        "catalog": {"available": True, "releases": [], "published": None,
                    "counts": {}, "operations": [], "routes": []},
        "runs": [],
        "counts": {"entities": 0, "relations": 0, "entity_types": 0,
                   "predicates": 0, "runs": 0},
    }


def test_render_html_embeds_payload_and_escapes_script_close():
    payload = _empty_payload()
    payload["knowledge"]["nodes"] = [{
        "id": "x", "type": "Instance", "canonical_id": "x",
        "label": "</script><b>unsafe</b>", "status": "published",
    }]
    html = db_dashboard.render_html(payload)
    assert "__PAYLOAD__" not in html
    assert "<\\/script>" in html
    assert "</script><b>unsafe</b>" not in html
    assert "工厂数据仪表盘" in html


def test_build_payload_reads_revision_run_and_process_catalog(tmp_path):
    database = CatalogDatabase(tmp_path / "dashboard.db")
    initialize_database(database)
    stage_default_catalog(database, release_id="dash_catalog")
    publish_release(database, "dash_catalog")
    recorder = RunRecorder(database, run_id="dash_run",
                           scenario="full_flow")
    assert recorder.start(sim_time=0.0)["ok"]
    assert recorder.finish(
        None, {"acceptance": {"ok": True}}, status="DONE")["ok"]
    payload = db_dashboard.build_payload(database.path, runs_limit=5)
    assert payload["revision"] == "0003_run_records"
    assert payload["counts"]["runs"] == 1
    assert payload["runs"][0]["run_id"] == "dash_run"
    assert payload["runs"][0]["status"] == "DONE"
    assert payload["knowledge"]["available"] is True
    catalog = payload["catalog"]
    assert catalog["published"]["release_id"] == "dash_catalog"
    assert len(catalog["operations"]) == 10
    assert len(catalog["routes"]) == 3
    assert len(catalog["resources"]) == 21
    assert len(catalog["devices"]) == 7
    assert len(catalog["zones"]) == 9
    assert len(catalog["entries"]) == 3
    ops = {row["operation_id"]: row for row in catalog["operations"]}
    assert ops["kit_join"]["inputs"][0]["zone"] == "buffer_e_c"
    assert ops["housing_direct_A"]["successors"][0]["operation_id"] == \
        "housing_finish"
    routes = {row["route_id"]: row for row in catalog["routes"]}
    assert routes["housing_direct"]["operation_ids"][0] == \
        "housing_direct_A"
    html = db_dashboard.render_html(payload)
    assert '"dash_run"' in html
    assert '"housing_direct_A"' in html
