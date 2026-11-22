"""阶段 4: 有界确定性订单 planner、草稿保存与 API (纯逻辑)."""

from __future__ import annotations

import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from flexible_factory.api.server import OrderApiServer
from flexible_factory.application.orders import OrderService
from flexible_factory.application.planner import (PLANNER_POLICY_VERSION,
                                                  OrderPlanner, PlannerInput,
                                                  PlannerInputError,
                                                  PlannerSupply)
from flexible_factory.domain.default_process import default_catalog
from flexible_factory.domain.materials import ItemType
from flexible_factory.domain.order_compiler import OrderDagCompiler
from flexible_factory.domain.order_validation import OrderValidator
from flexible_factory.persistence.db import (CatalogDatabase,
                                              initialize_database)

CATALOG = default_catalog()
PLANNER = OrderPlanner(CATALOG)


def _payload(*, target: str = "FINAL", quantity: int = 1,
             supplies=(("RAW_A", 1), ("RAW_D", 1)), order_id: str = "planner",
             **extra) -> dict:
    payload = {
        "target_item_type": target,
        "target_quantity": quantity,
        "inputs": [{"item_type": item, "quantity": qty}
                   for item, qty in supplies],
        "order_id": order_id,
        "order_version": 1,
    }
    payload.update(extra)
    return payload


def _plan(payload: dict):
    return PLANNER.plan(PlannerInput.from_dict(payload))


# ═══════════════════════════════════════════════════════════════
# 可行目标 golden cases
# ═══════════════════════════════════════════════════════════════

def test_golden_full_flow_target_plans_and_compiles():
    result = _plan(_payload())
    assert result.ok
    assert result.first_blocking_category == ""
    assert result.selected is not None
    assert result.selected.branch.route_ids == (
        "housing_direct", "bracket_route")
    assert result.order is not None
    assert result.order.target.item_type is ItemType.FINAL
    assert result.order.target.quantity == 1
    assert OrderValidator(CATALOG).validate(result.order) == ()
    compiled = OrderDagCompiler(CATALOG).compile(result.order,
                                                 run_id="planner")
    assert compiled.target_instance_ids
    assert result.preview is not None and result.preview.ok
    assert result.explanation["join_bindings"][0]["join_template_id"] == \
        "kit_join"
    assert result.explanation["input_summary"]["target_quantity"] == 1
    assert result.digest == result.order.dag_digest()


def test_raw_b_plan_uses_housing_alt_route():
    result = _plan(_payload(supplies=(("RAW_B", 1), ("RAW_D", 1))))
    assert result.ok
    assert result.selected is not None
    assert result.selected.branch.route_ids == (
        "housing_alt", "bracket_route")
    assert [row.input_requirements for row in
            result.selected.branch.children] == [
        (("RAW_B", "line_s1b", 1),), (("RAW_D", "line_s2d", 1),)]
    compiled = OrderDagCompiler(CATALOG).compile(result.order,
                                                 run_id="planner-alt")
    templates = {row.template_id for row in compiled.instances}
    assert {"housing_alt_B", "transport_BA", "housing_alt_A",
            "kit_join"} <= templates


def test_multi_quantity_plan_compiles_expected_instances():
    result = _plan(_payload(quantity=2,
                            supplies=(("RAW_A", 2), ("RAW_D", 2))))
    assert result.ok
    compiled = OrderDagCompiler(CATALOG).compile(result.order,
                                                 run_id="planner")
    assert len(compiled.target_instance_ids) == 2
    by_template = {}
    for row in compiled.instances:
        by_template[row.template_id] = by_template.get(row.template_id, 0) + 1
    assert by_template["housing_direct_A"] == 2
    assert by_template["kit_join"] == 2
    assert len([row for row in result.order.inputs]) == 4


def test_route_only_target_does_not_require_join():
    result = _plan(_payload(target="SEMI_CE",
                            supplies=(("RAW_A", 1),)))
    assert result.ok
    assert result.selected is not None
    assert result.selected.branch.kind == "route"
    assert result.selected.branch.route_ids == ("housing_direct",)
    assert result.explanation["join_bindings"] == []


def test_planner_is_deterministic_across_supply_container_order():
    first = _plan(_payload(supplies=(("RAW_A", 1), ("RAW_D", 1))))
    second = _plan(_payload(supplies=(("RAW_D", 1), ("RAW_A", 1))))
    assert first.ok and second.ok
    assert first.digest == second.digest
    assert first.order.canonical_json() == second.order.canonical_json()
    assert first.explanation == second.explanation


# ═══════════════════════════════════════════════════════════════
# 等价候选与稳定 tie-break
# ═══════════════════════════════════════════════════════════════

def test_equivalent_candidates_use_fewest_operations_and_stable_ids():
    payload = _payload(supplies=(("RAW_A", 1), ("RAW_B", 1),
                                 ("RAW_D", 1)))
    first = _plan(payload)
    second = _plan(payload)
    assert first.ok and second.ok
    assert first.digest == second.digest
    assert first.as_dict() == second.as_dict()
    feasible = {row.branch.route_ids for row in first.candidates}
    assert feasible == {
        ("housing_direct", "bracket_route"),
        ("housing_alt", "bracket_route"),
    }
    assert first.selected.branch.route_ids == (
        "housing_direct", "bracket_route")
    assert [row.candidate_id for row in first.candidates] == [
        "cand-000", "cand-001"]


def test_route_preference_overrides_operation_count():
    result = _plan(_payload(
        supplies=(("RAW_A", 1), ("RAW_B", 1), ("RAW_D", 1)),
        preferred_route_ids=("housing_alt", "bracket_route")))
    assert result.ok
    assert result.selected.branch.route_ids == (
        "housing_alt", "bracket_route")


# ═══════════════════════════════════════════════════════════════
# 参数化无解/约束 failures
# ═══════════════════════════════════════════════════════════════

@pytest.mark.parametrize(("payload", "category", "codes"), [
    (_payload(supplies=(("RAW_A", 1),)), "input",
     {"PLANNER_INPUT_INSUFFICIENT"}),
    (_payload(supplies=(("RAW_D", 1),)), "input",
     {"PLANNER_INPUT_INSUFFICIENT"}),
    (_payload(target="RAW_B", supplies=()), "target",
     {"PLANNER_TARGET_UNREACHABLE"}),
    (_payload(max_depth=0), "search_limit", {"PLANNER_SEARCH_LIMIT"}),
    (_payload(max_nodes=1), "search_limit", {"PLANNER_SEARCH_LIMIT"}),
    (_payload(policy_version="v0"), "policy",
     {"PLANNER_POLICY_VERSION"}),
    (_payload(scene_id="L4_COLUMN"), "scene", {"PLANNER_SCENE_UNKNOWN"}),
])
def test_no_solution_reports_stable_blocking_category(payload, category,
                                                       codes):
    result = _plan(payload)
    assert not result.ok
    assert result.order is None
    assert result.first_blocking_category == category
    assert codes & {row.code for row in result.diagnostics}


def test_total_target_nodes_are_bounded():
    result = _plan(_payload(quantity=4,
                            supplies=(("RAW_A", 4), ("RAW_D", 4)),
                            max_nodes=10))
    assert not result.ok
    assert result.first_blocking_category == "search_limit"
    assert any(row.code == "PLANNER_NODE_LIMIT"
               for row in result.diagnostics)


def test_template_exclusion_reports_join_constraint():
    result = _plan(_payload(
        allowed_template_ids=("housing_direct_A", "housing_finish",
                              "housing_to_E_buffer", "kit_join")))
    assert not result.ok
    assert result.first_blocking_category == "join"
    assert any(row.code == "PLANNER_JOIN_INPUT_UNREACHABLE"
               for row in result.diagnostics)
    assert any(row.code == "PLANNER_TEMPLATE_EXCLUDED"
               for row in result.diagnostics)


def test_device_exclusion_is_explained():
    result = _plan(_payload(allowed_device_ids=("arm_A",)))
    assert not result.ok
    assert any(row.code == "PLANNER_DEVICE_EXCLUDED"
               for row in result.diagnostics)


def test_planner_input_is_strict():
    with pytest.raises(PlannerInputError, match="未知字段"):
        PlannerInput.from_dict({**_payload(), "python": "no"})
    with pytest.raises(PlannerInputError, match="正整数"):
        PlannerInput.from_dict(_payload(quantity=0))
    with pytest.raises(PlannerInputError, match="未知物料类型"):
        PlannerInput.from_dict(_payload(target="UNOBTAINIUM"))


# ═══════════════════════════════════════════════════════════════
# 车队假设 (阶段 8)
# ═══════════════════════════════════════════════════════════════

def test_default_fleet_context_is_single_and_does_not_change_dag():
    default = _plan(_payload())
    explicit = _plan(_payload(transport_fleet="single"))
    assert default.ok and explicit.ok
    assert default.digest == explicit.digest
    context = default.explanation["fleet_context"]
    assert context["mode"] == "single"
    assert context["assignment"] == {}
    assert context["available_vehicle_ids"] == ["agv_1"]
    assert any(row["template_id"] == "transport_DE"
               and row["vehicle_id"] == "agv_1"
               for row in context["route_vehicles"])


def test_dual_fleet_assumption_records_assignment_without_dag_change():
    single = _plan(_payload())
    dual = _plan(_payload(transport_fleet="dual"))
    assert single.ok and dual.ok
    assert dual.digest == single.digest
    context = dual.explanation["fleet_context"]
    assert context["mode"] == "dual"
    assert context["assignment"]["transport_de"] == "cart_2"
    assert any(row["template_id"] == "transport_DE"
               and row["vehicle_id"] == "cart_2"
               for row in context["route_vehicles"])
    assert dual.selected.fleet["mode"] == "dual"


def test_static_unavailable_vehicle_penalizes_then_strict_rejects():
    payload = _payload(
        transport_fleet="dual",
        fleet_assumptions={"available_vehicle_ids": ["agv_1"]})
    relaxed = _plan(payload)
    assert relaxed.ok
    assert relaxed.selected.fleet["penalized"] is True
    assert any(row.code == "PLANNER_FLEET_ASSIGNMENT_UNAVAILABLE"
               for row in relaxed.diagnostics)
    assert any(row.code == "PLANNER_FLEET_ROUTE_PENALIZED"
               for row in relaxed.diagnostics)

    strict = _plan({**payload, "strict_fleet": True})
    assert not strict.ok
    assert any(row.code == "PLANNER_FLEET_ASSIGNMENT_UNAVAILABLE"
               and row.severity == "error" for row in strict.diagnostics)


def test_invalid_fleet_mode_is_rejected_at_input_boundary():
    with pytest.raises(PlannerInputError, match="车队模式"):
        PlannerInput.from_dict(_payload(transport_fleet="triple"))
    request = PlannerInput(
        target_item_type=ItemType.FINAL, target_quantity=1,
        inputs=(PlannerSupply(ItemType.RAW_A, 1),
                PlannerSupply(ItemType.RAW_D, 1)),
        transport_fleet="triple")
    result = PLANNER.plan(request)
    assert not result.ok
    assert any(row.code == "PLANNER_FLEET_MODE_INVALID"
               for row in result.diagnostics)


def test_fleet_eta_ranking_adds_static_evidence_only():
    baseline = _plan(_payload(transport_fleet="dual"))
    ranked = _plan(_payload(transport_fleet="dual",
                            fleet_eta_ranking=True))
    assert ranked.ok
    assert ranked.digest == baseline.digest
    eta = ranked.explanation["fleet_context"]["eta"]
    assert eta["assumption"] == "discrete_event_not_physics"
    assert eta["policy"] == "fixed"
    assert eta["makespan_s"] > 0
    assert eta["utilization"]["cart_2"] > 0
    assert eta["utilization"]["agv_1"] == 0


def test_fleet_eta_marks_unserved_jobs_unavailable():
    result = _plan(_payload(
        transport_fleet="dual",
        fleet_assumptions={"available_vehicle_ids": ["agv_1"]},
        fleet_eta_ranking=True))
    assert result.ok
    eta = result.explanation["fleet_context"]["eta"]
    assert eta["available"] is False
    assert eta["pending"] >= 1
    assert eta["completed"] == 0


def test_plan_and_save_records_fleet_assumption(database):
    service = OrderService(database)
    saved = service.plan_and_save(
        _payload(order_id="planner-fleet", transport_fleet="dual"))
    assert saved["ok"]
    version = service.get_version("planner-fleet", 1)
    assert version["planner"]["input"]["transport_fleet"] == "dual"
    assert version["planner"]["explain"]["fleet_context"]["mode"] == "dual"


# ═══════════════════════════════════════════════════════════════
# 服务/API 草稿保存
# ═══════════════════════════════════════════════════════════════

@pytest.fixture()
def database(tmp_path) -> CatalogDatabase:
    db = CatalogDatabase(tmp_path / "planner.db")
    initialize_database(db)
    return db


def test_service_plan_and_save_creates_ordinary_versions(database):
    service = OrderService(database)
    payload = _payload(order_id="planner-draft")

    saved = service.plan_and_save(payload, note="planner golden")
    assert saved["ok"]
    assert saved["order"]["current_version"] == 1
    assert saved["order"]["versions"][0]["source"] == "planner"
    version = service.get_version("planner-draft", 1)
    assert version["source"] == "planner"
    assert version["planner_policy_version"] == PLANNER_POLICY_VERSION
    assert version["planner"]["policy_version"] == PLANNER_POLICY_VERSION
    assert version["planner"]["candidate_count"] >= 1
    assert version["dag_digest"] == saved["saved_order"]["dag_digest"]
    assert version["spec"]["source"] == "planner"

    second = service.plan_and_save(payload, note="planner golden 2")
    assert second["ok"]
    assert second["order"]["current_version"] == 2
    assert second["saved_order"]["version"] == 2
    assert service.get_version("planner-draft", 2)["planner_policy_version"] \
        == PLANNER_POLICY_VERSION
    assert service.get_version("planner-draft", 1)["planner_policy_version"] \
        == PLANNER_POLICY_VERSION


def test_service_plan_and_save_does_not_save_no_solution(database):
    service = OrderService(database)
    result = service.plan_and_save(_payload(
        order_id="planner-no-solution", supplies=(("RAW_A", 1),)))
    assert result["ok"] is False
    assert result["order"] is None
    assert result["plan"]["ok"] is False
    assert result["plan"]["first_blocking_category"] == "input"
    assert service.list_orders() == []


def test_planner_draft_is_an_ordinary_order_and_compiles(database):
    service = OrderService(database)
    saved = service.plan_and_save(_payload(order_id="planner-ordinary"))
    assert saved["ok"]
    stored = service.repository.get_spec("planner-ordinary", 1)
    assert stored.source == "planner"
    assert stored.order_id == "planner-ordinary"
    preview = service.preview_spec(stored)
    assert preview.ok
    assert preview.digest == stored.dag_digest()


# ═══════════════════════════════════════════════════════════════
# 场景 profile 与直接来料 join (阶段 9)
# ═══════════════════════════════════════════════════════════════

def _mvp_payload(*, target: str = "COMPRESSOR_ASSY", supplies=(
        ("COMPRESSOR", 1), ("BASEPLATE", 1)), **extra) -> dict:
    payload = _payload(target=target, supplies=supplies,
                       scene_profile="assembly_mvp", **extra)
    return payload


@pytest.mark.parametrize(("target", "template_id", "inputs"), [
    ("COMPRESSOR_ASSY", "mvp_compressor_assembly",
     (("COMPRESSOR", "mvp_source_compressor", "compressor-in"),
      ("BASEPLATE", "mvp_source_baseplate", "baseplate-in"))),
    ("FLANGE_ASSY", "mvp_flange_assembly",
     (("FRONT_FLANGE", "mvp_source_front_flange", "front-in"),
      ("REAR_FLANGE", "mvp_source_rear_flange", "rear-in"))),
])
def test_mvp_direct_input_plans_compile(target, template_id, inputs):
    result = _plan(_mvp_payload(target=target, supplies=tuple(
        (item_type, 1) for item_type, _zone, _port in inputs)))
    assert result.ok
    assert result.first_blocking_category == ""
    branch = result.selected.branch
    assert branch.kind == "join"
    assert branch.route_ids == ()
    assert branch.template_ids == (template_id,)
    assert branch.direct_input_ports == tuple(
        (port_id, zone, ItemType(item_type))
        for item_type, zone, port_id in inputs)
    assert result.order is not None
    assert result.order.edges == ()
    assert len(result.order.nodes) == 1
    assert {
        (row.item_type.value, row.zone, row.node_id, row.port_id)
        for row in result.order.inputs
    } == {(item_type, zone, result.order.nodes[0].node_id, port_id)
          for item_type, zone, port_id in inputs}
    assert OrderValidator(CATALOG).validate(result.order) == ()
    compiled = OrderDagCompiler(CATALOG).compile(
        result.order, run_id="planner-mvp")
    assert len(compiled.target_instance_ids) == 1
    assert [row.template_id for row in compiled.instances] == [template_id]
    assert result.preview is not None and result.preview.ok
    binding = result.explanation["join_bindings"][0]
    assert binding["join_template_id"] == template_id
    assert [row["source"] for row in binding["inputs"]] == ["input", "input"]
    assert [row["port_id"] for row in binding["inputs"]] == [
        port_id for _item, _zone, port_id in inputs]


def test_mvp_plan_quantity_creates_one_join_and_two_inputs_per_unit():
    result = _plan(_mvp_payload(
        target_quantity=2,
        supplies=(("COMPRESSOR", 2), ("BASEPLATE", 2))))
    assert result.ok
    assert len(result.order.nodes) == 2
    assert len(result.order.inputs) == 4
    assert len({row.node_id for row in result.order.nodes}) == 2
    compiled = OrderDagCompiler(CATALOG).compile(
        result.order, run_id="planner-mvp-qty")
    assert len(compiled.target_instance_ids) == 2


@pytest.mark.parametrize(("supplies", "missing_type"), [
    ((("COMPRESSOR", 1),), "BASEPLATE"),
    ((("BASEPLATE", 1),), "COMPRESSOR"),
    ((("COMPRESSOR", 1), ("FRONT_FLANGE", 1)), "BASEPLATE"),
])
def test_mvp_missing_or_wrong_pair_reports_input(supplies, missing_type):
    result = _plan(_mvp_payload(supplies=supplies))
    assert not result.ok
    assert result.first_blocking_category == "input"
    assert any(row.code == "PLANNER_INPUT_INSUFFICIENT"
               and missing_type in row.message
               for row in result.diagnostics)


def test_mvp_target_requires_assembly_profile():
    full_flow = _plan(_payload(
        target="COMPRESSOR_ASSY",
        supplies=(("COMPRESSOR", 1), ("BASEPLATE", 1))))
    assert not full_flow.ok
    assert full_flow.first_blocking_category == "target"
    codes = {row.code for row in full_flow.diagnostics}
    assert "PLANNER_TARGET_PROFILE_MISMATCH" in codes
    assert "PLANNER_TEMPLATE_PROFILE_EXCLUDED" in codes


def test_scene_profile_is_rejected_at_input_boundary():
    with pytest.raises(PlannerInputError, match="scene_profile"):
        PlannerInput.from_dict(_payload(scene_profile="unknown"))
    request = PlannerInput(
        target_item_type=ItemType.COMPRESSOR_ASSY, target_quantity=1,
        inputs=(PlannerSupply(ItemType.COMPRESSOR, 1),
                PlannerSupply(ItemType.BASEPLATE, 1)),
        scene_profile="unknown")
    result = PLANNER.plan(request)
    assert not result.ok
    assert any(row.code == "PLANNER_SCENE_PROFILE_UNKNOWN"
               for row in result.diagnostics)


def test_full_flow_plan_with_explicit_profile_is_unchanged():
    implicit = _plan(_payload())
    explicit = _plan(_payload(scene_profile="full_flow"))
    assert implicit.ok and explicit.ok
    assert implicit.digest == explicit.digest
    assert implicit.selected.branch.route_ids == (
        "housing_direct", "bracket_route")


def test_mvp_plan_and_save_creates_ordinary_order(database):
    service = OrderService(database)
    saved = service.plan_and_save(
        _mvp_payload(order_id="planner-mvp"))
    assert saved["ok"]
    version = service.get_version("planner-mvp", 1)
    assert version["source"] == "planner"
    assert version["planner"]["input"]["scene_profile"] == "assembly_mvp"
    assert version["planner"]["explain"]["scene_profile"] == "assembly_mvp"
    stored = service.repository.get_spec("planner-mvp", 1)
    assert service.preview_spec(stored).ok
    assert stored.nodes[0].template_id == "mvp_compressor_assembly"


# ═══════════════════════════════════════════════════════════════
# HTTP API
# ═══════════════════════════════════════════════════════════════

def _request(base: str, method: str, path: str,
             payload=None) -> tuple[int, dict]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(base + path, data=data, method=method,
                      headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=5) as response:
            return int(response.status), json.loads(
                response.read().decode("utf-8"))
    except HTTPError as exc:
        return int(exc.code), json.loads(exc.read().decode("utf-8"))


def test_planner_http_api_plan_and_draft(database):
    service = OrderService(database)
    server = OrderApiServer(service, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, payload = _request(server.base_url, "GET", "/api/health")
        assert status == 200
        assert payload["planner_policy_version"] == PLANNER_POLICY_VERSION

        status, payload = _request(
            server.base_url, "POST", "/api/planner/plan",
            _payload(order_id="api-planner"))
        assert status == 200, payload
        assert payload["plan"]["ok"] is True
        assert payload["plan"]["selected"]["branch"]["route_ids"] == [
            "housing_direct", "bracket_route"]

        status, payload = _request(
            server.base_url, "POST", "/api/planner/plan",
            _payload(order_id="api-planner-dual", transport_fleet="dual"))
        assert status == 200, payload
        context = payload["plan"]["explanation"]["fleet_context"]
        assert context["mode"] == "dual"
        assert context["assignment"]["transport_de"] == "cart_2"

        status, payload = _request(
            server.base_url, "POST", "/api/planner/plan",
            _payload(transport_fleet="triple"))
        assert status == 422
        assert payload["error"]["code"] == "PLANNER_INPUT"

        status, payload = _request(
            server.base_url, "POST", "/api/planner/drafts",
            _payload(order_id="api-planner"))
        assert status == 200, payload
        assert payload["draft"]["saved_order"]["version"] == 1
        status, payload = _request(
            server.base_url, "GET",
            "/api/orders/api-planner/versions/1")
        assert status == 200
        assert payload["version"]["source"] == "planner"
        assert payload["version"]["planner_policy_version"] == \
            PLANNER_POLICY_VERSION

        status, payload = _request(
            server.base_url, "POST", "/api/planner/drafts",
            _payload(order_id="api-no-solution",
                     supplies=(("RAW_A", 1),)))
        assert status == 422
        assert payload["error"]["code"] == "PLANNER_NO_SOLUTION"
    finally:
        server.shutdown()
