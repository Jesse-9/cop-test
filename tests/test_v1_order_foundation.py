"""阶段 1: 订单契约、端口元数据、结构校验与 DAG 编译 (纯 Python)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from flexible_factory.domain.default_process import (DEFAULT_RECIPE_ID,
                                                     default_catalog)
from flexible_factory.domain.operation_runtime import OperationRuntime
from flexible_factory.domain.order_compiler import OrderDagCompiler
from flexible_factory.domain.order_validation import OrderValidator
from flexible_factory.domain.orders import (OrderCompileError, OrderEdge,
                                            OrderInput, OrderNode, OrderSpec,
                                            OrderSpecError, OrderTarget)
from flexible_factory.domain.process_model import ProcessCatalog
from flexible_factory.domain.materials import ItemType
from order_fixtures import full_flow_order

CATALOG = default_catalog()
RECIPE = CATALOG.recipe(DEFAULT_RECIPE_ID)
COMPILER = OrderDagCompiler(CATALOG)


def _errors(order: OrderSpec) -> list[str]:
    return [item.code for item in OrderValidator(CATALOG).validate(order)]


# ═══════════════════════════════════════════════════════════════
# 目录元数据
# ═══════════════════════════════════════════════════════════════

def test_catalog_exposes_order_metadata_and_ports():
    catalog = default_catalog()
    join = catalog.operation("kit_join")
    assert join.orchestratable is True
    assert join.scene_id == "L5_UFLOW"
    assert join.join_handler_id == "e_material_pair"
    assert join.input_port_ids == ("housing-input", "bracket-input")
    assert join.resolved_output_port_id == "output-0"
    assert catalog.zone("line_s1a").spawnable is True
    assert catalog.zone("handoff_ac").spawnable is False
    assert catalog.operation("housing_direct_A").input_port_ids == \
        ("input-0",)
    assert any(row.id == "kit_join"
               for row in catalog.orchestratable_operations())


# ═══════════════════════════════════════════════════════════════
# 声明式契约与摘要
# ═══════════════════════════════════════════════════════════════

def test_order_round_trips_and_digest_is_container_order_independent():
    order = full_flow_order()
    payload = order.as_dict()
    restored = OrderSpec.from_dict(payload)
    assert restored == order
    assert restored.dag_digest() == order.dag_digest()

    reversed_order = replace(
        order, inputs=tuple(reversed(order.inputs)),
        nodes=tuple(reversed(order.nodes)),
        edges=tuple(reversed(order.edges)))
    assert reversed_order.dag_digest() == order.dag_digest()
    assert reversed_order.canonical_json() == order.canonical_json()


def test_order_contract_is_strict_and_rejects_code_or_handles():
    payload = full_flow_order().as_dict()
    payload["python"] = "print('no')"
    with pytest.raises(OrderSpecError, match="未知字段"):
        OrderSpec.from_dict(payload)

    payload = full_flow_order().as_dict()
    payload["nodes"][0]["handle"] = 42
    with pytest.raises(OrderSpecError, match="未知字段"):
        OrderSpec.from_dict(payload)

    payload = full_flow_order().as_dict()
    payload["order_id"] = "../escape"
    with pytest.raises(OrderSpecError, match="标识格式"):
        OrderSpec.from_dict(payload)


def test_order_rejects_unknown_source_and_contract_version():
    with pytest.raises(OrderSpecError, match="未知编排来源"):
        OrderSpec.from_dict({**full_flow_order().as_dict(),
                             "source": "llm"})
    with pytest.raises(OrderSpecError, match="未知物料类型"):
        OrderSpec.from_dict({
            **full_flow_order().as_dict(),
            "target": {"item_type": "UNOBTAINIUM", "quantity": 1}})


# ═══════════════════════════════════════════════════════════════
# 校验正例 / 编译正例
# ═══════════════════════════════════════════════════════════════

def test_full_flow_order_validates_and_compiles_equivalent_dependencies():
    order = full_flow_order()
    result = OrderValidator(CATALOG).validate_result(order)
    assert result.ok, [row.as_dict() for row in result.diagnostics]
    assert result.entry_node_ids == ("bracket-start", "housing-start")
    assert result.terminal_target_node_ids == ("kit-join",)
    assert result.topological_order == (
        "bracket-start", "bracket-transport", "bracket-buffer",
        "housing-start", "housing-finish", "housing-buffer", "kit-join")

    compiled = COMPILER.compile(order, run_id="run0001")
    assert compiled.digest == order.dag_digest()
    assert compiled.validation.ok
    assert compiled.instance_counts == {
        "bracket-start": 1, "bracket-transport": 1,
        "bracket-buffer": 1, "housing-start": 1, "housing-finish": 1,
        "housing-buffer": 1, "kit-join": 1}
    # 订单输入按 input_id 稳定分配 UID; A=1, D=2.
    assert [(row.uid, row.item_type, row.zone)
            for row in compiled.material_plans] == [
        (1, "RAW_A", "line_s1a"), (2, "RAW_D", "line_s2d"),
        (3, "FINAL", "output_e")]
    by_node = {row.node_id: row for row in compiled.instances}
    assert by_node["housing-start"].uid == 1
    assert by_node["bracket-start"].uid == 2
    join = by_node["kit-join"]
    assert join.uid == 3
    assert join.input_uids == [1, 2]
    assert join.instance_id == "run0001:kit-join:1+2"
    assert join.predecessor_instance_ids == [
        "run0001:housing-buffer:1", "run0001:bracket-buffer:2"]
    assert join.predecessor_ports == {
        "run0001:housing-buffer:1": "housing-input",
        "run0001:bracket-buffer:2": "bracket-input"}
    assert compiled.target_instance_ids == ("run0001:kit-join:1+2",)
    # 同一模板可以重复使用; task key 含 node_id, 不与旧任务键冲突.
    assert by_node["housing-start"].task_id == \
        "a_conveyor_uid1_housing-start"
    assert all(row.selected_route == "" for row in compiled.instances)
    assert all(row.order_id == order.order_id for row in compiled.instances)


def test_multi_uid_order_compiles_one_chain_per_material_and_pairs_joins():
    order = full_flow_order(raw_a=2, raw_d=2, target_quantity=2)
    compiled = COMPILER.compile(order, run_id="run0002")
    assert compiled.instance_counts["housing-start"] == 2
    assert compiled.instance_counts["kit-join"] == 2
    joins = [row for row in compiled.instances
             if row.node_id == "kit-join"]
    assert [(row.uid, row.input_uids) for row in joins] == [
        (5, [1, 3]), (6, [2, 4])]
    assert [row.instance_id for row in joins] == [
        "run0002:kit-join:1+3", "run0002:kit-join:2+4"]
    assert len(compiled.target_instance_ids) == 2
    assert len({row.uid for row in compiled.instances}) == 6
    assert len({row.instance_id for row in compiled.instances}) == 14


def test_compile_is_deterministic_across_input_container_order():
    first = full_flow_order()
    second = replace(
        first, inputs=tuple(reversed(first.inputs)),
        edges=tuple(reversed(first.edges)))
    a = COMPILER.compile(first, run_id="run0003")
    b = COMPILER.compile(second, run_id="run0003")
    assert a.digest == b.digest
    assert [row.as_dict() for row in a.instances] == \
        [row.as_dict() for row in b.instances]
    assert [row.as_dict() for row in a.material_plans] == \
        [row.as_dict() for row in b.material_plans]


def test_preview_reports_topology_targets_and_does_not_start_simulation():
    preview = COMPILER.preview(full_flow_order())
    assert preview.ok
    assert preview.simulation_started is False
    payload = preview.as_dict()
    assert payload["topological_order"][-1] == "kit-join"
    assert payload["entry_node_ids"] == ["bracket-start", "housing-start"]
    assert payload["target_instance_ids"] == ["preview:kit-join:1+2"]
    assert payload["expected_outputs"][0]["path"] == [
        "housing-start", "housing-finish", "housing-buffer",
        "bracket-start", "bracket-transport", "bracket-buffer",
        "kit-join"]
    assert payload["instance_counts"]["kit-join"] == 1
    assert all(row["inputs"] for row in payload["nodes"])


def test_operation_runtime_order_mode_uses_port_bindings_not_routes():
    order = full_flow_order()
    compiled = COMPILER.compile(order, run_id="run0004")
    runtime = OperationRuntime(CATALOG, RECIPE, order=compiled.context)
    runtime.load_order_instances(compiled.instances)
    assert runtime.order_mode
    assert runtime.validate() == []
    assert runtime.graph.mode == "order"
    assert runtime.progress_state() == "RUNNING"
    ready = runtime.ready()
    assert [row.node_id for row in ready] == ["bracket-start",
                                              "housing-start"]
    assert all(row.selected_route == "" for row in ready)


# ═══════════════════════════════════════════════════════════════
# 参数化结构负例
# ═══════════════════════════════════════════════════════════════

def _catalog_with(operation_id: str, **changes) -> ProcessCatalog:
    recipe = CATALOG.recipe(DEFAULT_RECIPE_ID)
    operations = tuple(
        replace(row, **changes) if row.id == operation_id else row
        for row in recipe.operations)
    return ProcessCatalog(
        recipes=(replace(recipe, operations=operations),),
        capabilities=CATALOG.capabilities, devices=CATALOG.devices,
        zones=CATALOG.zones, known_resources=CATALOG.known_resources,
        version=CATALOG.version)


def _order_with(**changes) -> OrderSpec:
    return replace(full_flow_order(), **changes)


def test_negative_unknown_template_and_duplicate_node_id():
    order = _order_with(nodes=full_flow_order().nodes + (
        OrderNode("housing-start", "housing_direct_A"),))
    assert "ORDER_NODE_DUPLICATE" in _errors(order)

    nodes = tuple(
        replace(row, template_id="no_such_template")
        if row.node_id == "housing-finish" else row
        for row in full_flow_order().nodes)
    assert "ORDER_TEMPLATE_UNKNOWN" in _errors(_order_with(nodes=nodes))


def test_negative_unknown_edge_endpoint_and_self_edge():
    edges = full_flow_order().edges + (
        OrderEdge("housing-finish", "missing-node", "input-0"),)
    assert "ORDER_EDGE_UNKNOWN_NODE" in _errors(_order_with(edges=edges))

    edges = full_flow_order().edges + (
        OrderEdge("kit-join", "kit-join", "housing-input"),)
    assert "ORDER_EDGE_SELF" in _errors(_order_with(edges=edges))


def test_negative_cycle_is_located():
    edges = full_flow_order().edges + (
        OrderEdge("kit-join", "housing-start", "input-0"),)
    codes = _errors(_order_with(edges=edges))
    assert "ORDER_CYCLE" in codes


def test_negative_material_and_zone_mismatch():
    edges = tuple(
        replace(row, from_node="bracket-buffer")
        if row.from_node == "housing-buffer"
        and row.to_node == "kit-join" else row
        for row in full_flow_order().edges)
    assert "ORDER_EDGE_MATERIAL_MISMATCH" in _errors(_order_with(edges=edges))

    inputs = tuple(
        replace(row, zone="line_s2d")
        if row.input_id == "raw-a-1" else row
        for row in full_flow_order().inputs)
    assert "ORDER_INPUT_ZONE_MISMATCH" in _errors(_order_with(inputs=inputs))


def test_negative_unsupplied_port_and_missing_input():
    edges = tuple(row for row in full_flow_order().edges
                  if not (row.to_node == "housing-finish"))
    assert "ORDER_PORT_UNSUPPLIED" in _errors(_order_with(edges=edges))

    inputs = tuple(row for row in full_flow_order().inputs
                   if row.input_id != "raw-d-1")
    assert "ORDER_PORT_UNSUPPLIED" in _errors(_order_with(inputs=inputs))


def test_negative_target_unreachable_and_dangling_node():
    target = OrderTarget(ItemType.SEMI_CE, 1)
    assert "ORDER_TARGET_TERMINAL" in _errors(_order_with(target=target))

    target = OrderTarget(ItemType.RAW_B, 1)
    assert "ORDER_TARGET_UNREACHABLE" in _errors(_order_with(target=target))

    nodes = full_flow_order().nodes + (
        OrderNode("extra-housing", "housing_direct_A"),)
    inputs = full_flow_order().inputs + (
        OrderInput("raw-a-2", ItemType.RAW_A, 1, "line_s1a",
                   "extra-housing", "input-0"),)
    codes = _errors(_order_with(nodes=nodes, inputs=inputs))
    assert "ORDER_NODE_DANGLING" in codes


def test_negative_template_allowlist_executor_and_join_handler():
    catalog = _catalog_with("housing_direct_A", orchestratable=False)
    codes = [row.code for row in
             OrderValidator(catalog).validate(full_flow_order())]
    assert "ORDER_TEMPLATE_NOT_ORCHESTRATABLE" in codes

    catalog = _catalog_with("housing_direct_A", executor_kind="teleport")
    codes = [row.code for row in
             OrderValidator(catalog).validate(full_flow_order())]
    assert "ORDER_EXECUTOR_NOT_ALLOWED" in codes

    catalog = _catalog_with("kit_join", join_handler_id="unknown_handler")
    codes = [row.code for row in
             OrderValidator(catalog).validate(full_flow_order())]
    assert "ORDER_JOIN_HANDLER_UNKNOWN" in codes


def test_negative_multiple_predecessors_require_and_semantics():
    """多输入模板必须是已登记逻辑 join; OR 语义不被订单模式接受."""
    catalog = _catalog_with(
        "kit_join", executor_kind="arm", predecessor_policy="any",
        join_handler_id="")
    codes = [row.code for row in
             OrderValidator(catalog).validate(full_flow_order())]
    assert "ORDER_PREDECESSOR_POLICY" in codes
    assert "ORDER_MULTI_INPUT_UNSUPPORTED" in codes


def test_negative_target_quantity_is_checked_at_compile_time():
    order = replace(full_flow_order(),
                    target=OrderTarget(ItemType.FINAL, 2))
    with pytest.raises(OrderCompileError) as excinfo:
        COMPILER.compile(order, run_id="run0005")
    assert "ORDER_TARGET_QUANTITY_MISMATCH" in str(excinfo.value)
