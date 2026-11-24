"""阶段 10: 内置订单方案目录、物化与 API (纯逻辑)."""

from __future__ import annotations

import json
import threading

import pytest

from flexible_factory.api.server import OrderApiServer
from flexible_factory.application.orders import OrderService
from flexible_factory.domain import assembly_mvp as mvp
from flexible_factory.domain import rules as cfg
from flexible_factory.domain.default_process import default_catalog
from flexible_factory.domain.order_compiler import OrderDagCompiler
from flexible_factory.domain.order_variants import (MAX_VARIANT_NODES,
                                                   OrderVariantError,
                                                   OrderVariantNodeLimitError,
                                                   OrderVariantNotFoundError,
                                                   OrderVariantUnavailableError,
                                                   SCOPE_INTERMEDIATE,
                                                   SCOPE_PRODUCTION,
                                                   SCOPE_TRANSIT,
                                                   VARIANT_VERSION,
                                                   VERIFICATION_COMPILER_ONLY,
                                                   VERIFICATION_PLANNER_TEST,
                                                   VERIFICATION_REAL_RUN,
                                                   build_order_variants,
                                                   materialize_variant,
                                                   variant_availability,
                                                   variant_profile_allowed)
from flexible_factory.domain.scene_profile import capabilities_for_profile
from flexible_factory.persistence.db import (CatalogDatabase,
                                              initialize_database)
from test_v1_order_service import _request

CATALOG = default_catalog()
VARIANTS = build_order_variants(CATALOG)
BY_ID = {row.variant_id: row for row in VARIANTS}


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


def _resolver(reference: str) -> str:
    if reference in ("current", "full", "v1/L5_UFLOW/full"):
        return "v1/L5_UFLOW/full"
    if reference in ("mvp", "experiments/assembly-mvp/v1/L5_UFLOW/mvp"):
        return "experiments/assembly-mvp/v1/L5_UFLOW/mvp"
    raise OrderVariantError(f"unknown artifact {reference!r}")


def _loader(artifact_id: str) -> dict:
    return {
        "v1/L5_UFLOW/full": _full_manifest(),
        "experiments/assembly-mvp/v1/L5_UFLOW/mvp": _mvp_manifest(),
    }[artifact_id]


@pytest.fixture()
def database(tmp_path) -> CatalogDatabase:
    db = CatalogDatabase(tmp_path / "variants.db")
    initialize_database(db)
    return db


def _bound_service(database) -> OrderService:
    return OrderService(
        database, artifact_id="current", artifact_resolver=_resolver,
        artifact_manifest_loader=_loader)


# ═══════════════════════════════════════════════════════════════
# 目录与物化
# ═══════════════════════════════════════════════════════════════

def test_catalog_has_15_unique_variants():
    assert len(VARIANTS) == 15
    assert len(BY_ID) == 15
    assert {row.variant_id for row in VARIANTS} == {
        "final_direct_kit", "final_alt_kit",
        "semi_ce_direct", "semi_ac_direct_a", "semi_ce_direct_ac",
        "semi_ce_alt", "semi_ab_alt_b", "semi_ab_alt_ba",
        "semi_ac_alt_a", "semi_ce_alt_ac",
        "part_de_d", "part_de_de", "part_de_bracket",
        "mvp_compressor_pair", "mvp_flange_pair",
    }


def test_all_variants_compile_and_targets_match():
    compiler = OrderDagCompiler(CATALOG)
    for variant in VARIANTS:
        order = materialize_variant(
            variant, CATALOG,
            order_id=f"check-{variant.variant_id}", quantity=1)
        assert order.target.item_type is variant.target_item_type
        assert order.target.quantity == 1
        assert order.source == "manual"
        preview = compiler.preview(order)
        assert preview.ok, (variant.variant_id,
                            [(row.code, row.message)
                             for row in preview.diagnostics])
        assert preview.terminal_target_node_ids


def test_variant_scope_and_verification_counts():
    scopes = {row.scope for row in VARIANTS}
    assert scopes == {SCOPE_PRODUCTION, SCOPE_INTERMEDIATE, SCOPE_TRANSIT}
    assert sum(row.scope == SCOPE_PRODUCTION for row in VARIANTS) == 4
    assert sum(row.scope == SCOPE_INTERMEDIATE for row in VARIANTS) == 7
    assert sum(row.scope == SCOPE_TRANSIT for row in VARIANTS) == 4
    assert sum(row.verification == VERIFICATION_REAL_RUN
               for row in VARIANTS) == 4
    assert sum(row.verification == VERIFICATION_PLANNER_TEST
               for row in VARIANTS) == 1
    assert sum(row.verification == VERIFICATION_COMPILER_ONLY
               for row in VARIANTS) == 10
    assert sum(row.route_complete for row in VARIANTS) == 7


def test_arm_and_transport_metadata():
    alt = BY_ID["final_alt_kit"]
    assert alt.used_arms == ("A", "B", "C", "D", "E")
    assert alt.used_transports == ("transport_BA", "transport_DE")
    assert alt.recommended_transport_fleet == "dual"

    direct = BY_ID["final_direct_kit"]
    assert direct.used_arms == ("A", "C", "D", "E")
    assert direct.used_transports == ("transport_DE",)
    assert direct.recommended_transport_fleet == "single"

    assert BY_ID["semi_ab_alt_b"].scope == SCOPE_TRANSIT
    assert BY_ID["semi_ab_alt_b"].used_arms == ("B",)
    assert BY_ID["part_de_de"].used_arms == ("D",)
    assert BY_ID["part_de_de"].used_transports == ("transport_DE",)
    assert BY_ID["mvp_compressor_pair"].used_arms == ()
    assert BY_ID["mvp_compressor_pair"].used_transports == ()


def test_prefix_variants_differ_by_one_operation():
    assert BY_ID["semi_ac_direct_a"].difference_from == "semi_ce_direct"
    assert "housing_finish" in BY_ID["semi_ac_direct_a"].difference_summary
    assert BY_ID["semi_ce_direct_ac"].difference_from == "semi_ce_direct"
    assert "housing_to_E_buffer" in \
        BY_ID["semi_ce_direct_ac"].difference_summary
    assert BY_ID["semi_ce_alt_ac"].difference_from == "semi_ce_alt"
    assert "housing_to_E_buffer" in \
        BY_ID["semi_ce_alt_ac"].difference_summary
    assert BY_ID["part_de_de"].difference_from == "part_de_bracket"
    assert "bracket_to_E" in BY_ID["part_de_de"].difference_summary


def test_profile_filtering_and_availability():
    full = capabilities_for_profile("full_flow", CATALOG)
    mvp_caps = capabilities_for_profile("assembly_mvp", CATALOG)
    assert sum(variant_profile_allowed(row, full) for row in VARIANTS) == 13
    assert sum(variant_profile_allowed(row, mvp_caps) for row in VARIANTS) \
        == 15
    assert variant_profile_allowed(BY_ID["final_direct_kit"], full)
    assert not variant_profile_allowed(BY_ID["mvp_compressor_pair"], full)
    assert variant_availability(
        BY_ID["final_direct_kit"], CATALOG, full)[0] is True
    assert variant_availability(
        BY_ID["mvp_compressor_pair"], CATALOG, full)[0] is False


def test_quantity_and_node_limit():
    order = materialize_variant(
        BY_ID["final_direct_kit"], CATALOG,
        order_id="variant-qty", quantity=2)
    assert order.target.quantity == 2
    assert len(order.nodes) == 14
    assert len(order.inputs) == 4
    preview = OrderDagCompiler(CATALOG).preview(order)
    assert preview.ok
    assert len(preview.target_instance_ids) == 2

    with pytest.raises(OrderVariantNodeLimitError):
        materialize_variant(
            BY_ID["final_direct_kit"], CATALOG,
            order_id="variant-too-many",
            quantity=MAX_VARIANT_NODES)


# ═══════════════════════════════════════════════════════════════
# 服务与 API
# ═══════════════════════════════════════════════════════════════

def test_service_lists_variants_by_profile(database):
    unbound = OrderService(database)
    assert len(unbound.order_variants()["variants"]) == 15
    assert unbound.order_variants()["scene_profile"] == ""

    service = _bound_service(database)
    full = service.order_variants()
    assert full["scene_profile"] == "full_flow"
    assert len(full["variants"]) == 13
    assert "mvp_compressor_pair" not in {
        row["variant_id"] for row in full["variants"]}
    assert full["variant_version"] == VARIANT_VERSION

    mvp_catalog = service.order_variants("mvp")
    assert mvp_catalog["scene_profile"] == "assembly_mvp"
    assert len(mvp_catalog["variants"]) == 15
    assert "mvp_compressor_pair" in {
        row["variant_id"] for row in mvp_catalog["variants"]}

    production = service.order_variants("mvp", scope="production")
    assert {row["variant_id"] for row in production["variants"]} == {
        "final_direct_kit", "final_alt_kit",
        "mvp_compressor_pair", "mvp_flange_pair"}
    with pytest.raises(OrderVariantError, match="方案层级"):
        service.order_variants(scope="unknown")


def test_service_creates_ordinary_variant_order(database):
    service = _bound_service(database)
    result = service.create_order_from_variant(
        "final_direct_kit",
        {"order_id": "variant-direct", "quantity": 1, "note": "smoke"})
    assert result["ok"]
    assert result["saved_order"]["version"] == 1
    assert result["preview"]["ok"] is True
    version = service.get_version("variant-direct", 1)
    assert version["source"] == "manual"
    assert version["note"].startswith("variant:final_direct_kit")
    assert version["spec"]["target"] == {
        "item_type": "FINAL", "quantity": 1}
    assert [node["template_id"] for node in version["spec"]["nodes"]] == [
        "housing_direct_A", "housing_finish", "housing_to_E_buffer",
        "bracket_process", "transport_DE", "bracket_to_E", "kit_join"]

    second = service.create_order_from_variant(
        "final_direct_kit", {"order_id": "variant-direct", "quantity": 2})
    assert second["saved_order"]["version"] == 2
    assert service.get_order("variant-direct")["current_version"] == 2
    assert len(service.get_version(
        "variant-direct", 2)["spec"]["inputs"]) == 4


def test_service_creates_prefix_and_mvp_variants(database):
    service = _bound_service(database)
    prefix = service.create_order_from_variant(
        "semi_ce_direct_ac", {"order_id": "variant-prefix"})
    assert prefix["preview"]["ok"] is True
    version = service.get_version("variant-prefix", 1)
    assert version["spec"]["target"] == {
        "item_type": "SEMI_CE", "quantity": 1}
    assert [node["template_id"] for node in version["spec"]["nodes"]] == [
        "housing_direct_A", "housing_finish"]

    mvp = service.create_order_from_variant(
        "mvp_compressor_pair", {"order_id": "variant-mvp"},
        artifact_id="mvp")
    assert mvp["preview"]["ok"] is True
    mvp_version = service.get_version("variant-mvp", 1)
    assert mvp_version["spec"]["target"] == {
        "item_type": "COMPRESSOR_ASSY", "quantity": 1}
    assert [node["template_id"] for node in mvp_version["spec"]["nodes"]] \
        == ["mvp_compressor_assembly"]
    assert [row["port_id"] for row in mvp_version["spec"]["inputs"]] == [
        "compressor-in", "baseplate-in"]


def test_service_rejects_unknown_and_cross_profile(database):
    service = _bound_service(database)
    with pytest.raises(OrderVariantNotFoundError):
        service.create_order_from_variant(
            "unknown-variant", {"order_id": "variant-unknown"})
    with pytest.raises(OrderVariantUnavailableError):
        service.create_order_from_variant(
            "mvp_compressor_pair", {"order_id": "variant-cross"})
    assert service.list_orders() == []


def test_variant_http_api(database):
    service = _bound_service(database)
    server = OrderApiServer(service, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, payload = _request(
            server.base_url, "GET", "/api/order-variants")
        assert status == 200, payload
        assert payload["scene_profile"] == "full_flow"
        assert len(payload["variants"]) == 13
        assert payload["variant_version"] == VARIANT_VERSION

        status, payload = _request(
            server.base_url, "GET",
            "/api/order-variants?artifact_id=mvp&scope=production")
        assert status == 200, payload
        assert {row["variant_id"] for row in payload["variants"]} == {
            "final_direct_kit", "final_alt_kit",
            "mvp_compressor_pair", "mvp_flange_pair"}

        status, payload = _request(
            server.base_url, "POST",
            "/api/order-variants/semi_ce_direct/orders",
            {"order_id": "api-variant", "quantity": 1})
        assert status == 200, payload
        assert payload["saved_order"]["version"] == 1
        assert payload["preview"]["ok"] is True

        status, payload = _request(
            server.base_url, "POST",
            "/api/order-variants/mvp_compressor_pair/orders",
            {"order_id": "api-cross"})
        assert status == 422
        assert payload["error"]["code"] == "ORDER_VARIANT_UNAVAILABLE"

        status, payload = _request(
            server.base_url, "POST",
            "/api/order-variants/unknown/orders",
            {"order_id": "api-unknown"})
        assert status == 404
        assert payload["error"]["code"] == "ORDER_VARIANT_UNKNOWN"

        status, payload = _request(
            server.base_url, "GET", "/api/health")
        assert status == 200
        assert payload["order_variant_version"] == VARIANT_VERSION
    finally:
        server.shutdown()