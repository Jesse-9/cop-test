"""阶段 9: 场景 profile 能力目录与跨 profile 订单拒绝 (纯逻辑/API)."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from flexible_factory.api.server import OrderApiServer
from flexible_factory.application.orders import OrderService
from flexible_factory.artifacts.store import ArtifactStore
from flexible_factory.domain import assembly_mvp as mvp
from flexible_factory.domain import rules as cfg
from flexible_factory.domain import scene_profile
from flexible_factory.domain.default_process import default_catalog
from flexible_factory.domain.orders import OrderSpec
from flexible_factory.persistence.db import (CatalogDatabase,
                                              initialize_database)
from flexible_factory.persistence.errors import (OrderRunConfigError,
                                                  OrderStateError)
from test_v1_order_service import _request, _success_summary

CATALOG = default_catalog()
REPO_ROOT = Path(__file__).resolve().parent.parent


def _mvp_payload(name: str) -> dict:
    path = (REPO_ROOT / "docs" / "implementation" / "reference"
            / "production-flow" / "assembly-mvp" / f"{name}.json")
    return json.loads(path.read_text(encoding="utf-8"))


def _full_manifest() -> dict:
    return {"extra": {"v1": {
        "scene_profile": "full_flow",
        "material_model": {"types": list(cfg.ITEM_TYPES)},
        "assembly_mvp": None,
    }}}


def _mvp_manifest() -> dict:
    return {"extra": {"v1": {
        "scene_profile": "assembly_mvp",
        "material_model": {
            "types": [*cfg.ITEM_TYPES, *mvp.ITEM_TYPES],
            "extension_types": list(mvp.ITEM_TYPES),
        },
        "assembly_mvp": {"profile": "assembly_mvp"},
    }}}


def _legacy_full_manifest() -> dict:
    return {"extra": {"v1": {
        "material_model": {"types": list(cfg.ITEM_TYPES)},
    }}}


# ═══════════════════════════════════════════════════════════════
# manifest profile 判定与能力过滤
# ═══════════════════════════════════════════════════════════════

def test_profile_from_manifest_prefers_declared_profile():
    assert scene_profile.profile_from_manifest(_full_manifest()) == (
        "full_flow", "manifest")
    assert scene_profile.profile_from_manifest(_mvp_manifest()) == (
        "assembly_mvp", "manifest")
    # 旧 v1 工件没有 scene_profile: 回落 full_flow/default.
    assert scene_profile.profile_from_manifest(_legacy_full_manifest()) == (
        "full_flow", "default")
    legacy_mvp = {"extra": {"v1": {
        "material_model": {"extension_types": ["COMPRESSOR"]}}}}
    assert scene_profile.profile_from_manifest(legacy_mvp) == (
        "assembly_mvp", "manifest")


def test_repository_artifacts_declare_expected_profiles():
    full = ArtifactStore(REPO_ROOT / "docs" / "scene_artifacts") \
        .read_current("v1")
    assert scene_profile.profile_from_manifest(full.manifest) == (
        "full_flow", "default")
    full_caps = scene_profile.capabilities_from_manifest(
        full.manifest, CATALOG, artifact_id=full.artifact_id)
    assert mvp.TEMPLATE_COMPRESSOR not in full_caps.allowed_template_ids

    mvp_root = (REPO_ROOT / "docs" / "scene_artifacts" / "experiments"
                / "assembly-mvp")
    experiment = ArtifactStore(mvp_root).read_current("v1")
    assert scene_profile.profile_from_manifest(experiment.manifest) == (
        "assembly_mvp", "manifest")
    mvp_caps = scene_profile.capabilities_from_manifest(
        experiment.manifest, CATALOG, artifact_id=experiment.artifact_id)
    assert {mvp.TEMPLATE_COMPRESSOR, mvp.TEMPLATE_FLANGE,
            "kit_join"} <= mvp_caps.allowed_template_ids


def test_unknown_declared_profile_is_rejected():
    manifest = {"extra": {"v1": {"scene_profile": "unknown"}}}
    with pytest.raises(scene_profile.SceneProfileError, match="未知"):
        scene_profile.profile_from_manifest(manifest)


def test_full_flow_capabilities_exclude_mvp_extension():
    capabilities = scene_profile.capabilities_for_profile(
        "full_flow", CATALOG)
    assert capabilities.scene_profile == "full_flow"
    assert mvp.TEMPLATE_COMPRESSOR not in capabilities.allowed_template_ids
    assert mvp.TEMPLATE_FLANGE not in capabilities.allowed_template_ids
    assert "kit_join" in capabilities.allowed_template_ids
    targets = {row.value for row in capabilities.target_item_types}
    inputs = {row.value for row in capabilities.input_item_types}
    assert "FINAL" in targets
    assert not targets & set(mvp.PRODUCT_ITEM_TYPES)
    assert {"RAW_A", "RAW_B", "RAW_D"} <= inputs
    assert not inputs & set(mvp.ITEM_TYPES)
    zone_ids = capabilities.zone_ids
    assert "line_s1a" in zone_ids
    assert not zone_ids & set(mvp.ZONES)


def test_assembly_mvp_capabilities_include_base_and_extension():
    capabilities = scene_profile.capabilities_for_profile(
        "assembly_mvp", CATALOG)
    assert capabilities.scene_profile == "assembly_mvp"
    assert {mvp.TEMPLATE_COMPRESSOR, mvp.TEMPLATE_FLANGE,
            "kit_join"} <= capabilities.allowed_template_ids
    targets = {row.value for row in capabilities.target_item_types}
    inputs = {row.value for row in capabilities.input_item_types}
    assert {"FINAL", *mvp.PRODUCT_ITEM_TYPES} <= targets
    assert {"RAW_A", "RAW_B", "RAW_D", "COMPRESSOR", "BASEPLATE",
            "FRONT_FLANGE", "REAR_FLANGE"} <= inputs
    assert set(mvp.SOURCE_ZONES) <= capabilities.zone_ids


def test_manifest_item_types_are_validated():
    unknown = {"extra": {"v1": {
        "scene_profile": "full_flow",
        "material_model": {"types": ["UNOBTAINIUM"]}}}}
    with pytest.raises(scene_profile.SceneProfileError, match="未知物料类型"):
        scene_profile.capabilities_from_manifest(unknown, CATALOG)
    bad_full = {"extra": {"v1": {
        "scene_profile": "full_flow",
        "material_model": {"types": [*cfg.ITEM_TYPES, "COMPRESSOR"]}}}}
    with pytest.raises(scene_profile.SceneProfileError, match="扩展物料"):
        scene_profile.capabilities_from_manifest(bad_full, CATALOG)
    incomplete = {"extra": {"v1": {
        "scene_profile": "full_flow",
        "material_model": {"types": ["RAW_A"]}}}}
    with pytest.raises(scene_profile.SceneProfileError, match="未在"):
        scene_profile.capabilities_from_manifest(incomplete, CATALOG)


def test_order_profile_diagnostics_flag_cross_profile_order():
    order = OrderSpec.from_dict(_mvp_payload("order_compressor"))
    full = scene_profile.capabilities_for_profile("full_flow", CATALOG)
    codes = {row.code for row in
             scene_profile.order_profile_diagnostics(order, full)}
    assert {
        "ORDER_TEMPLATE_PROFILE_MISMATCH",
        "ORDER_TARGET_PROFILE_MISMATCH",
        "ORDER_INPUT_PROFILE_MISMATCH",
        "ORDER_INPUT_ZONE_PROFILE_MISMATCH",
    } <= codes
    mvp_capabilities = scene_profile.capabilities_for_profile(
        "assembly_mvp", CATALOG)
    assert scene_profile.order_profile_diagnostics(
        order, mvp_capabilities) == ()
    assert scene_profile.order_required_profile(order, CATALOG) == \
        "assembly_mvp"


# ═══════════════════════════════════════════════════════════════
# 服务与 HTTP API
# ═══════════════════════════════════════════════════════════════

MANIFESTS = {
    "v1/L5_UFLOW/full": _full_manifest(),
    "experiments/assembly-mvp/v1/L5_UFLOW/mvp": _mvp_manifest(),
}


def _resolver(reference: str) -> str:
    if reference in ("current", "full", "v1/L5_UFLOW/full"):
        return "v1/L5_UFLOW/full"
    if reference in ("mvp", "experiments/assembly-mvp/v1/L5_UFLOW/mvp"):
        return "experiments/assembly-mvp/v1/L5_UFLOW/mvp"
    raise OrderStateError(f"unknown artifact {reference!r}")


def _loader(artifact_id: str) -> dict:
    return MANIFESTS[artifact_id]


@pytest.fixture()
def database(tmp_path) -> CatalogDatabase:
    db = CatalogDatabase(tmp_path / "phase9.db")
    initialize_database(db)
    return db


def _service(database, **extra) -> OrderService:
    return OrderService(
        database, artifact_id="current", artifact_resolver=_resolver,
        artifact_manifest_loader=_loader, **extra)


def test_service_templates_filter_by_manifest_profile(database):
    service = _service(database)
    full = service.templates()
    assert full["scene_profile"] == "full_flow"
    assert full["profile_source"] == "manifest"
    full_templates = {row["template_id"] for row in full["templates"]}
    assert "kit_join" in full_templates
    assert mvp.TEMPLATE_COMPRESSOR not in full_templates
    assert full["item_types"]["targets"] == [
        "FINAL", "PART_DE", "SEMI_AB", "SEMI_AC", "SEMI_CE"]
    assert full["item_types"]["inputs"] == ["RAW_A", "RAW_B", "RAW_D"]
    spawnable = {zone["id"] for zone in full["zones"] if zone["spawnable"]}
    assert spawnable == {"line_s1a", "line_s1b", "line_s2d"}

    mvp_catalog = service.templates("mvp")
    assert mvp_catalog["scene_profile"] == "assembly_mvp"
    mvp_templates = {row["template_id"] for row in mvp_catalog["templates"]}
    assert {mvp.TEMPLATE_COMPRESSOR, mvp.TEMPLATE_FLANGE} <= mvp_templates
    assert "kit_join" in mvp_templates
    assert "COMPRESSOR" in mvp_catalog["item_types"]["inputs"]
    assert mvp_catalog["artifact_id"] == \
        "experiments/assembly-mvp/v1/L5_UFLOW/mvp"


def test_unbound_service_catalog_is_explicit(database):
    service = OrderService(database)
    catalog = service.templates()
    assert catalog["scene_profile"] == ""
    assert catalog["profile_source"] == "unbound"
    templates = {row["template_id"] for row in catalog["templates"]}
    assert {mvp.TEMPLATE_COMPRESSOR, "kit_join"} <= templates


def test_preview_rejects_cross_profile_order(database):
    service = _service(database)
    payload = _mvp_payload("order_compressor")
    preview = service.preview(payload, artifact_id="full")
    assert preview.ok is False
    codes = {row.code for row in preview.diagnostics}
    assert "ORDER_TEMPLATE_PROFILE_MISMATCH" in codes
    assert service.preview(payload, artifact_id="mvp").ok is True


def test_start_rejects_cross_profile_order_before_run(database):
    service = _service(
        database, runner=lambda command, stop: _success_summary(command))
    service.create_order(_mvp_payload("order_compressor"))
    with pytest.raises(OrderRunConfigError, match="profile"):
        service.start("mvp-compressor-001", 1, artifact_id="full")
    assert service.list_runs() == []


def test_http_version_preview_uses_requested_artifact(database):
    service = _service(database)
    service.create_order(_mvp_payload("order_compressor"))
    server = OrderApiServer(service, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, payload = _request(
            server.base_url, "POST",
            "/api/orders/mvp-compressor-001/versions/1/preview"
            "?artifact_id=full")
        assert status == 200, payload
        assert payload["preview"]["ok"] is False
        assert any(
            row["code"] == "ORDER_TEMPLATE_PROFILE_MISMATCH"
            for row in payload["preview"]["diagnostics"])

        status, payload = _request(
            server.base_url, "POST",
            "/api/orders/mvp-compressor-001/versions/1/preview"
            "?artifact_id=mvp")
        assert status == 200, payload
        assert payload["preview"]["ok"] is True
    finally:
        server.shutdown()


def test_http_templates_reports_profile_and_filters(database):
    service = _service(database)
    server = OrderApiServer(service, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, payload = _request(
            server.base_url, "GET", "/api/templates?artifact_id=full")
        assert status == 200, payload
        assert payload["scene_profile"] == "full_flow"
        assert payload["profile_source"] == "manifest"
        assert mvp.TEMPLATE_COMPRESSOR not in {
            row["template_id"] for row in payload["templates"]}

        status, payload = _request(
            server.base_url, "GET", "/api/templates?artifact_id=mvp")
        assert status == 200, payload
        assert payload["scene_profile"] == "assembly_mvp"
        assert mvp.TEMPLATE_COMPRESSOR in {
            row["template_id"] for row in payload["templates"]}

        status, payload = _request(
            server.base_url, "GET", "/api/templates?artifact_id=missing")
        assert status == 422
        assert payload["error"]["code"] == "ORDER_RUN_CONFIG"

        status, payload = _request(server.base_url, "GET", "/api/health")
        assert status == 200
        assert payload["scene_profile"] == "full_flow"
        assert payload["scene_profile_version"] == \
            scene_profile.SCENE_PROFILE_VERSION
    finally:
        server.shutdown()