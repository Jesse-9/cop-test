"""阶段四静态工艺目录、路线与运行时状态映射测试 (06-19)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from flexible_factory.domain import rules as cfg
from flexible_factory.domain.default_process import (DEFAULT_RECIPE_ID, default_catalog,
                                    validate_default_against_config)
from flexible_factory.domain.materials import ItemType
from flexible_factory.domain.process_model import (MaterialPort, ProcessCatalog,
                                  ProcessModelError)
from flexible_factory.domain.tasks import (TaskError, TaskQueue, TaskStatus,
                          make_task_from_operation)


# ═══════════════════════════════════════════════════════════════
# 默认目录正向契约
# ═══════════════════════════════════════════════════════════════

def test_default_catalog_validates_and_matches_frozen_config():
    catalog = default_catalog()
    report = catalog.validate()
    assert report["ok"] and not report["errors"]
    assert catalog.version == "v1_process_20261103"
    assert validate_default_against_config(catalog)["ok"]

    recipe = catalog.recipe(DEFAULT_RECIPE_ID)
    assert recipe.product_type == "assembled_module"
    assert len(recipe.operations) == 10
    assert [route.id for route in recipe.route_variants] == [
        "housing_direct", "housing_alt", "bracket_route"]
    assert recipe.join_operation_id == "kit_join"

    for kind, priority in cfg.TASK_PRIORITY.items():
        if kind == cfg.TASK_KIND_REPOSITION:
            continue
        assert catalog.operation_for_kind(kind).priority == priority

    join = catalog.operation("kit_join")
    assert tuple(port.item_type.value for port in join.inputs) == \
        tuple(cfg.JOIN_INPUTS)
    assert join.output.item_type.value == cfg.JOIN_OUTPUT
    assert {port.item_type.value: port.zone for port in join.inputs} == \
        dict(cfg.JOIN_BUFFER_OF)
    assert join.predecessor_policy == "all"

    derived_transitions = {}
    for operation in recipe.operations:
        if operation.executor_kind != "arm" or operation.output is None:
            continue
        unit_id = catalog.device(
            operation.candidate_devices[0]).id.removeprefix("arm_")
        for port in operation.inputs:
            if port.item_type != operation.output.item_type:
                derived_transitions[(unit_id, port.item_type.value)] = \
                    operation.output.item_type.value
    assert derived_transitions == dict(cfg.PROCESS_TRANSITIONS)

    for entry in recipe.entries:
        source = cfg.SOURCE_SPECS[entry.id]
        assert entry.line == source["line"]
        assert entry.item_type.value == source["item_type"]
        assert catalog.operation(entry.operation_id).inputs[0].zone == \
            entry.line

    assert [op.kind for op in catalog.operations_for_device("arm_A")] == [
        cfg.TASK_KIND_A_CONVEYOR, cfg.TASK_KIND_A_AGV]
    assert [op.kind for op in catalog.operations_for_device("arm_E")] == [
        cfg.TASK_KIND_E_CONVEYOR, cfg.TASK_KIND_E_AGV]
    assert catalog.operations_for_device("logic_E")[0].id == "kit_join"


def test_source_specs_match_scene_config():
    """domain 目录的来源线/来料类型必须与场景配置一致."""
    from flexible_factory.simulation import config as scene_cfg
    for key, spec in cfg.SOURCE_SPECS.items():
        source = scene_cfg.SOURCE_ITEMS[key]
        assert spec["line"] == source["line"], key
        assert spec["item_type"] == source["item_type"], key


def test_default_resources_and_devices_are_complete():
    catalog = default_catalog()
    expected = {
        "housing_direct_A": ("line_s1a", "handoff_ac", "zone_ab",
                             "zone_ac", "arm_A"),
        "housing_alt_A": ("handoff_ac", "dock_A", "zone_ab", "zone_ac",
                          "arm_A"),
        "housing_alt_B": ("line_s1b", "dock_B", "zone_ab", "arm_B"),
        "housing_finish": ("line_ce", "handoff_ac", "zone_ac", "arm_C"),
        "bracket_process": ("line_s2d", "dock_D", "arm_D"),
        "housing_to_E_buffer": ("line_ce", "buffer_e_c", "arm_E"),
        "bracket_to_E": ("buffer_e_d", "dock_E", "arm_E"),
    }
    for operation_id, resources in expected.items():
        assert catalog.operation(operation_id).resources == resources
    for unit_id in cfg.UNIT_IDS:
        device = catalog.device(f"arm_{unit_id}")
        assert device.kind == "arm" and device.physical
        assert "robotic_pick_place" in device.capabilities
    assert catalog.device("agv_1").kind == "agv"
    assert catalog.device("logic_E").physical is False


def test_catalog_operations_match_frozen_task_flow_lines():
    """默认目录的工序语义必须覆盖 config.TASK_FLOW 的冻结描述."""
    catalog = default_catalog()
    by_id = {operation.id: operation
             for operation in catalog.recipe(DEFAULT_RECIPE_ID).operations}
    expected = {
        "housing_direct_A": cfg.TASK_FLOW[0],
        "housing_alt_B": cfg.TASK_FLOW[1],
        "transport_BA": cfg.TASK_FLOW[2],
        "housing_finish": cfg.TASK_FLOW[3],
        "housing_to_E_buffer": cfg.TASK_FLOW[4],
        "bracket_process": cfg.TASK_FLOW[5],
        "transport_DE": cfg.TASK_FLOW[6],
    }
    for operation_id, line in expected.items():
        assert by_id[operation_id].semantics == line, operation_id
    assert by_id["kit_join"].semantics.startswith(
        "E join: buffer_e_c + buffer_e_d")


def test_catalog_reproduces_removed_coordinator_mappings():
    """迁移等价: 目录覆盖原 coordinator 的任务类型/源汇/区域/dock/后继."""
    catalog = default_catalog()
    recipe = catalog.recipe(DEFAULT_RECIPE_ID)
    by_kind = {operation.kind: operation
               for operation in recipe.operations}
    expected = {
        # kind: (source_zone, dest_zone, source_kind, sink_kind, dock,
        #        input_type, output_type, successors)
        cfg.TASK_KIND_A_CONVEYOR: (
            "line_s1a", "handoff_ac", "conveyor", "station", "A",
            ItemType.RAW_A, ItemType.SEMI_AC, ("housing_finish",)),
        cfg.TASK_KIND_A_AGV: (
            "agv", "handoff_ac", "agv", "station", "A",
            ItemType.SEMI_AB, ItemType.SEMI_AC, ("housing_finish",)),
        cfg.TASK_KIND_B: (
            "line_s1b", "agv", "conveyor", "agv", "B",
            ItemType.RAW_B, ItemType.SEMI_AB, ("transport_BA",)),
        cfg.TASK_KIND_C: (
            "handoff_ac", "line_ce", "station", "conveyor", "",
            ItemType.SEMI_AC, ItemType.SEMI_CE,
            ("housing_to_E_buffer",)),
        cfg.TASK_KIND_D: (
            "line_s2d", "agv", "conveyor", "agv", "D",
            ItemType.RAW_D, ItemType.PART_DE, ("transport_DE",)),
        cfg.TASK_KIND_E_CONVEYOR: (
            "line_ce", "buffer_e_c", "conveyor", "station", "E",
            ItemType.SEMI_CE, ItemType.SEMI_CE, ("kit_join",)),
        cfg.TASK_KIND_E_AGV: (
            "agv", "buffer_e_d", "agv", "station", "E",
            ItemType.PART_DE, ItemType.PART_DE, ("kit_join",)),
        cfg.TASK_KIND_TRANSPORT_BA: (
            "agv", "agv", "agv", "agv", "A",
            ItemType.SEMI_AB, ItemType.SEMI_AB, ("housing_alt_A",)),
        cfg.TASK_KIND_TRANSPORT_DE: (
            "agv", "agv", "agv", "agv", "E",
            ItemType.PART_DE, ItemType.PART_DE, ("bracket_to_E",)),
    }
    for kind, row in expected.items():
        operation = by_kind[kind]
        source_zone, dest_zone, source_kind, sink_kind, dock, \
            input_type, output_type, successors = row
        assert operation.inputs[0].zone == source_zone, kind
        assert operation.output.zone == dest_zone, kind
        assert operation.source_kind == source_kind, kind
        assert operation.sink_kind == sink_kind, kind
        assert operation.dock == dock, kind
        assert operation.inputs[0].item_type is input_type, kind
        assert operation.output.item_type is output_type, kind
        assert operation.successors == successors, kind
    assert by_kind[cfg.TASK_KIND_TRANSPORT_BA].route_origin_dock == "B"
    assert by_kind[cfg.TASK_KIND_TRANSPORT_BA].route_destination_dock == "A"
    assert by_kind[cfg.TASK_KIND_TRANSPORT_DE].route_origin_dock == "D"
    assert by_kind[cfg.TASK_KIND_TRANSPORT_DE].route_destination_dock == "E"


def test_housing_routes_are_exclusive_and_merge_before_c():
    catalog = default_catalog()
    recipe = catalog.recipe(DEFAULT_RECIPE_ID)
    routes = {route.id: route for route in recipe.route_variants}

    direct = routes["housing_direct"]
    alternate = routes["housing_alt"]
    assert direct.group == alternate.group == "housing_supply"
    assert set(direct.operation_ids) & set(alternate.operation_ids) == {
        "housing_finish", "housing_to_E_buffer"}
    assert "transport_BA" not in direct.operation_ids
    assert alternate.operation_ids[:3] == (
        "housing_alt_B", "transport_BA", "housing_alt_A")
    # 两条互斥路线在 C 前汇合, 不是同时执行的两个并行分支.
    assert direct.terminal_operation_id == \
        alternate.terminal_operation_id == "housing_to_E_buffer"

    join = catalog.operation("kit_join")
    assert set(join.depends_on) == {"housing_to_E_buffer", "bracket_to_E"}
    bracket = set(routes["bracket_route"].operation_ids)
    assert bracket.isdisjoint(set(direct.operation_ids) |
                              set(alternate.operation_ids))
    assert routes["bracket_route"].exit_zone == "buffer_e_d"


# ═══════════════════════════════════════════════════════════════
# 运行时状态: 单一 operation instance
# ═══════════════════════════════════════════════════════════════

def test_task_fields_are_read_only_projection_of_operation_instance():
    catalog = default_catalog()
    template = catalog.operation("housing_direct_A")
    task = make_task_from_operation(template, 1, sim_time=2.0)

    assert task.operation_template_id == template.id
    assert task.operation.template_id == template.id
    assert task.operation.instance_id == task.task_id
    assert task.kind == template.kind
    assert task.uid == 1
    assert task.source == "line_s1a"
    assert task.destination == "handoff_ac"
    # 稳定字段只能通过 operation 读取, 不能另写一份.
    with pytest.raises(AttributeError):
        task.kind = "other"
    with pytest.raises(AttributeError):
        task.uid = 99

    queue = TaskQueue(catalog=catalog)
    assert queue.submit(task)[0]
    queue.wait(task.task_id, 3.0, "handoff busy")
    assert task.operation.state is TaskStatus.WAITING
    queue.start(task.task_id, 4.0)
    assert task.status is TaskStatus.RUNNING
    assert task.operation.state is TaskStatus.RUNNING
    task.phase = "PICKING"
    assert task.operation.phase == "PICKING"
    assert task.as_dict()["status"] == "RUNNING"
    assert task.as_dict()["operation_template_id"] == template.id
    assert task.as_dict()["operation_instance_id"] == task.task_id


def test_transport_task_fields_follow_route_metadata():
    catalog = default_catalog()
    template = catalog.operation("transport_BA")
    task = make_task_from_operation(template, 2, sim_time=1.0)
    assert task.is_transport()
    assert task.source == "dock_B"
    assert task.destination == "dock_A"
    assert task.dock == "A"
    assert task.device == "agv"
    assert task.operation_template_id == "transport_BA"


def test_logical_join_does_not_create_stage4_task():
    catalog = default_catalog()
    with pytest.raises(TaskError, match="逻辑合流工序"):
        make_task_from_operation(catalog.operation("kit_join"), 9)


def test_queue_report_exposes_catalog_routes_and_legacy_flow():
    queue = TaskQueue(catalog=default_catalog())
    report = queue.as_dict()
    assert report["process_model_version"] == "v1_process_20261103"
    assert any("housing_direct" in line
               and "housing_finish" in line
               for line in report["process_routes"])
    # 旧字段保留, 供既有证据读取.
    assert report["flow"] == list(cfg.TASK_FLOW)
    assert report["flow_version"] == cfg.TASK_FLOW_VERSION


# ═══════════════════════════════════════════════════════════════
# 负向校验
# ═══════════════════════════════════════════════════════════════

def _replace_operations(catalog, replacements: dict) -> ProcessCatalog:
    recipe = catalog.recipe(DEFAULT_RECIPE_ID)
    operations = tuple(
        replacements.get(operation.id, operation)
        for operation in recipe.operations)
    return ProcessCatalog(
        recipes=(replace(recipe, operations=operations),),
        capabilities=catalog.capabilities, devices=catalog.devices,
        zones=catalog.zones, known_resources=catalog.known_resources,
        version=catalog.version)


def _catalog_with(operation_id: str, **changes) -> ProcessCatalog:
    catalog = default_catalog()
    operation = catalog.operation(operation_id)
    return _replace_operations(
        catalog, {operation_id: replace(operation, **changes)})


def _error_text(catalog: ProcessCatalog) -> str:
    report = catalog.validate_report()
    assert not report["ok"]
    return "\n".join(report["errors"])


def test_negative_duplicate_operation_id():
    catalog = default_catalog()
    recipe = catalog.recipe(DEFAULT_RECIPE_ID)
    duplicate = replace(
        catalog.operation("housing_direct_A"), kind="duplicate_kind",
        depends_on=(), successors=())
    operations = recipe.operations + (duplicate,)
    broken = ProcessCatalog(
        recipes=(replace(recipe, operations=operations),),
        capabilities=catalog.capabilities, devices=catalog.devices,
        zones=catalog.zones, known_resources=catalog.known_resources)
    assert "重复 operation id" in _error_text(broken)
    assert "recipe=mixed_line_v1" in _error_text(broken)


def test_negative_unknown_device_capability_zone_and_resource():
    assert "未知设备" in _error_text(_catalog_with(
        "housing_direct_A", candidate_devices=("arm_Z",)))
    assert "未知能力" in _error_text(_catalog_with(
        "housing_direct_A", capability="teleport"))
    assert "未知 zone" in _error_text(_catalog_with(
        "housing_direct_A",
        inputs=(MaterialPort("nowhere", ItemType.RAW_A),)))
    assert "未知资源" in _error_text(_catalog_with(
        "housing_direct_A",
        resources=("line_s1a", "handoff_ac", "zone_ab", "zone_ac",
                   "arm_A", "zone_zz")))


def test_negative_missing_input_output_and_work_step():
    assert "缺少输入" in _error_text(_catalog_with(
        "housing_direct_A", inputs=()))
    assert "缺少输出" in _error_text(_catalog_with(
        "housing_direct_A", output=None))
    assert "缺少工步模板引用" in _error_text(_catalog_with(
        "housing_direct_A", work_steps=()))


def test_negative_invalid_successor_and_cycle():
    assert "无效后继" in _error_text(_catalog_with(
        "housing_direct_A", successors=("no_such_operation",)))

    catalog = default_catalog()
    finish = replace(
        catalog.operation("housing_finish"),
        depends_on=("housing_finish", "housing_direct_A", "housing_alt_A"),
        successors=("housing_finish", "housing_to_E_buffer"))
    broken = _replace_operations(catalog, {"housing_finish": finish})
    text = _error_text(broken)
    assert "循环依赖" in text
    assert "housing_finish" in text


def test_negative_material_type_mismatch():
    text = _error_text(_catalog_with(
        "housing_finish",
        inputs=(MaterialPort("handoff_ac", ItemType.RAW_D),)))
    assert "物料类型不匹配" in text
    assert "housing_finish" in text


def test_negative_route_chain_discontinuity():
    catalog = default_catalog()
    recipe = catalog.recipe(DEFAULT_RECIPE_ID)
    routes = tuple(
        replace(route, operation_ids=("housing_direct_A", "housing_alt_B"))
        if route.id == "housing_direct" else route
        for route in recipe.route_variants)
    broken = ProcessCatalog(
        recipes=(replace(recipe, route_variants=routes),),
        capabilities=catalog.capabilities, devices=catalog.devices,
        zones=catalog.zones, known_resources=catalog.known_resources)
    assert "工序链不连续" in _error_text(broken)


def test_negative_duplicate_device_id():
    catalog = default_catalog()
    recipe = catalog.recipe(DEFAULT_RECIPE_ID)
    broken = ProcessCatalog(
        recipes=(recipe,), capabilities=catalog.capabilities,
        devices=catalog.devices + (catalog.device("arm_A"),),
        zones=catalog.zones, known_resources=catalog.known_resources)
    assert "重复 device id" in _error_text(broken)


def test_negative_duplicate_capability_and_zone_id():
    catalog = default_catalog()
    recipe = catalog.recipe(DEFAULT_RECIPE_ID)
    duplicated_capability = ProcessCatalog(
        recipes=(recipe,),
        capabilities=catalog.capabilities + (
            catalog.capability("robotic_pick_place"),),
        devices=catalog.devices, zones=catalog.zones,
        known_resources=catalog.known_resources)
    assert "重复 capability id" in _error_text(duplicated_capability)

    duplicated_zone = ProcessCatalog(
        recipes=(recipe,), capabilities=catalog.capabilities,
        devices=catalog.devices,
        zones=catalog.zones + (catalog.zone("handoff_ac"),),
        known_resources=catalog.known_resources)
    assert "重复 zone id" in _error_text(duplicated_zone)


def test_validate_raises_with_recipe_and_operation_context():
    broken = _catalog_with("housing_direct_A", capability="teleport")
    with pytest.raises(ProcessModelError) as excinfo:
        broken.validate()
    message = str(excinfo.value)
    assert "recipe=mixed_line_v1" in message
    assert "operation=housing_direct_A" in message
