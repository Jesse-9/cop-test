"""阶段 3: 配方实例化、工序 DAG、ready 集合与提交边界 (不需要 CoppeliaSim).

覆盖 06-21 §实施顺序 1-3 的纯 Python 部分: 入口固定选路、共享尾段唯一实例、
依赖解锁、稳定顺序、重复提交拒绝、失败不解锁、图级结构校验和 E 合流绑定.
"""

from __future__ import annotations

import pytest

from flexible_factory.domain.default_process import (DEFAULT_RECIPE_ID,
                                                     default_catalog)
from flexible_factory.domain.operation_runtime import (OperationGraph,
                                                       OperationInstance,
                                                       OperationRuntime,
                                                       OperationRuntimeError,
                                                       RecipeRunContext,
                                                       TaskStatus)

CATALOG = default_catalog()
RECIPE = CATALOG.recipe(DEFAULT_RECIPE_ID)
JOIN = CATALOG.operation("kit_join")


def _runtime() -> OperationRuntime:
    return OperationRuntime(CATALOG, RECIPE)


def _finish(runtime: OperationRuntime, instance: OperationInstance,
            sim_time: float = 0.0) -> OperationInstance:
    runtime.mark_started(instance.instance_id, sim_time)
    return runtime.commit_success(
        instance.instance_id, output_uids=[instance.uid],
        sim_time=sim_time + 0.05)


def _run_route(runtime: OperationRuntime, entry_id: str, uid: int) -> None:
    """实例化并把该入口的整条链逐步走到 DONE."""
    instances = runtime.instantiate_entry(entry_id, uid, sim_time=0.0)
    for index, instance in enumerate(instances):
        _finish(runtime, instance, sim_time=index * 10.0)


def _instance(instance_id: str, template_id: str, *, run_id: str = "run0001",
              uid: int = 1, predecessors=(), route: str = "housing_direct",
              task_id: str = "") -> OperationInstance:
    template = CATALOG.operation(template_id)
    return OperationInstance(
        instance_id=instance_id, task_id=task_id or f"{template.kind}_uid{uid}",
        template_id=template_id, kind=template.kind, uid=uid,
        source=template.inputs[0].zone,
        destination=template.output.zone if template.output else "",
        run_id=run_id, selected_route=route,
        predecessor_instance_ids=list(predecessors),
        priority=int(template.priority))


# ═══════════════════════════════════════════════════════════════
# 实例化: 只生成选中路线
# ═══════════════════════════════════════════════════════════════

def test_entry_instantiation_follows_selected_route_only():
    runtime = _runtime()
    direct = runtime.instantiate_entry("s1a", 1, sim_time=1.0)
    alt = runtime.instantiate_entry("s1b", 2, sim_time=1.1)
    bracket = runtime.instantiate_entry("s2d", 3, sim_time=1.2)

    direct_ops = [row.template_id for row in direct]
    alt_ops = [row.template_id for row in alt]
    bracket_ops = [row.template_id for row in bracket]

    assert direct_ops == ["housing_direct_A", "housing_finish",
                          "housing_to_E_buffer"]
    assert alt_ops == ["housing_alt_B", "transport_BA", "housing_alt_A",
                       "housing_finish", "housing_to_E_buffer"]
    assert bracket_ops == ["bracket_process", "transport_DE", "bracket_to_E"]

    # 未选路线不生成可执行实例.
    assert "housing_alt_B" not in direct_ops
    assert "transport_BA" not in direct_ops
    assert "housing_direct_A" not in alt_ops
    assert {row.selected_route for row in direct} == {"housing_direct"}
    assert {row.selected_route for row in alt} == {"housing_alt"}
    assert {row.selected_route for row in bracket} == {"bracket_route"}


def test_instance_ids_are_deterministic_and_keep_legacy_task_keys():
    runtime = _runtime()
    first = runtime.instantiate_entry("s1a", 7, sim_time=0.0)
    second = runtime.instantiate_entry("s1a", 8, sim_time=0.0)

    assert [row.instance_id for row in first] == [
        f"{first[0].run_id}:7:housing_direct_A",
        f"{first[0].run_id}:7:housing_finish",
        f"{first[0].run_id}:7:housing_to_E_buffer"]
    # 不同 UID 的同一模板不冲突, 任务键仍是旧格式.
    assert first[0].instance_id != second[0].instance_id
    assert [row.task_id for row in first] == [
        "a_conveyor_uid7", "c_handoff_uid7", "e_conveyor_uid7"]
    assert first[0].run_id != second[0].run_id
    # 任务队列键 → 运行时实例的映射是唯一的 (协调器按实例派工).
    assert runtime.for_task_id("a_conveyor_uid7") is first[0]
    assert runtime.for_task_id("a_conveyor_uid8") is second[0]

    payload = first[0].as_dict()
    for key in ("instance_id", "template_id", "kind", "uid", "source",
                "destination", "dock", "device", "state", "phase",
                "priority", "attempts", "created_sim_time",
                "started_sim_time", "finished_sim_time", "failure_reason",
                "resources", "transport"):
        assert key in payload
    assert payload["run_id"] == first[0].run_id
    assert payload["predecessor_instance_ids"] == []


def test_shared_tail_binds_only_the_selected_route_terminal():
    runtime = _runtime()
    alt = runtime.instantiate_entry("s1b", 2)
    by_template = {row.template_id: row for row in alt}

    finish = by_template["housing_finish"]
    to_buffer = by_template["housing_to_E_buffer"]
    # 共享尾段只实例化一次, 前驱是该 UID 实际走的分支, 不是两条路线.
    assert len([row for row in alt
                if row.template_id == "housing_finish"]) == 1
    assert finish.predecessor_instance_ids == [
        by_template["housing_alt_A"].instance_id]
    assert "housing_direct_A" not in finish.predecessor_instance_ids
    assert to_buffer.predecessor_instance_ids == [finish.instance_id]


def test_duplicate_task_key_is_rejected_before_mutating_graph():
    runtime = _runtime()
    runtime.instantiate_entry("s1a", 1)
    before = len(runtime.graph)
    with pytest.raises(OperationRuntimeError, match="任务键"):
        runtime.instantiate_entry("s1a", 1)
    assert len(runtime.graph) == before


def test_unknown_entry_and_route_are_rejected():
    runtime = _runtime()
    with pytest.raises(OperationRuntimeError, match="未知订单入口"):
        runtime.instantiate_entry("nope", 1)


# ═══════════════════════════════════════════════════════════════
# ready 集合与提交
# ═══════════════════════════════════════════════════════════════

def test_ready_starts_with_entry_then_unlocks_one_step_at_a_time():
    runtime = _runtime()
    instances = runtime.instantiate_entry("s1a", 1)

    assert [row.template_id for row in runtime.ready()] == [
        "housing_direct_A"]
    first = runtime.claim_ready()[0]
    assert first.template_id == "housing_direct_A"
    # 已派发但未提交: 不重复 ready, 也不解锁后继.
    assert runtime.ready() == []

    _finish(runtime, first)
    assert [row.template_id for row in runtime.ready()] == ["housing_finish"]
    second = runtime.claim_ready()[0]
    _finish(runtime, second, sim_time=10.0)
    assert [row.template_id for row in runtime.ready()] == [
        "housing_to_E_buffer"]
    assert len(instances) == 3


def test_unlock_requires_committed_output_not_only_done_state():
    graph = OperationGraph(RecipeRunContext(
        run_id="run0001", recipe_id=RECIPE.id, recipe_version=RECIPE.version,
        entry_id="s1a", entry_uid=1, entry_item_type="RAW_A",
        entry_zone="line_s1a", route_variant_id="housing_direct"))
    first = graph.add(_instance("run0001:1:housing_direct_A",
                                "housing_direct_A"))
    second = graph.add(_instance("run0001:1:housing_finish",
                                 "housing_finish",
                                 predecessors=[first.instance_id]))
    first.state = TaskStatus.DONE          # 状态 DONE 但输出未提交
    assert graph.ready() == []
    first.committed_sim_time = 1.0
    assert [row.instance_id for row in graph.ready()] == [second.instance_id]


def test_commit_rejects_duplicate_and_unlocked_instance():
    runtime = _runtime()
    instance = runtime.instantiate_entry("s1a", 1)[0]
    runtime.mark_started(instance.instance_id, 0.0)
    runtime.commit_success(instance.instance_id, output_uids=[1],
                           sim_time=0.5)
    with pytest.raises(OperationRuntimeError, match="重复提交"):
        runtime.commit_success(instance.instance_id, output_uids=[1],
                               sim_time=0.6)

    runtime = _runtime()
    chain = runtime.instantiate_entry("s1a", 1)
    with pytest.raises(OperationRuntimeError, match="前驱未全部提交"):
        runtime.commit_success(chain[1].instance_id, output_uids=[1],
                               sim_time=1.0)


def test_failed_predecessor_does_not_unlock_successor():
    runtime = _runtime()
    chain = runtime.instantiate_entry("s1a", 1)
    runtime.mark_started(chain[0].instance_id, 0.0)
    runtime.mark_failed(chain[0].instance_id, "PICK_FAILED", 0.5)

    assert runtime.ready() == []
    assert chain[1].state is TaskStatus.PENDING
    assert not runtime.all_done()
    # mark_failed 不覆盖终态, 也不允许把 FAILED 提交成 DONE.
    runtime.mark_failed(chain[0].instance_id, "SECOND", 0.6)
    assert chain[0].failure_reason == "PICK_FAILED"
    with pytest.raises(OperationRuntimeError, match="已 FAILED"):
        runtime.commit_success(chain[0].instance_id, output_uids=[1],
                               sim_time=0.7)


def test_ready_order_is_stable_by_priority_then_creation():
    graph = OperationGraph()
    low = graph.add(_instance("run:1:c_handoff", "housing_finish",
                              uid=1, task_id="c_handoff_uid1"))
    high = graph.add(_instance("run:2:a_conveyor", "housing_direct_A",
                               uid=2, task_id="a_conveyor_uid2"))
    low.priority = 1
    high.priority = 0

    assert [row.instance_id for row in graph.ready()] == [
        high.instance_id, low.instance_id]

    same = _runtime()
    first = same.instantiate_entry("s1a", 1)[0]
    second = same.instantiate_entry("s2d", 2)[0]
    assert first.priority == second.priority
    assert [row.instance_id for row in same.ready()] == [
        first.instance_id, second.instance_id]       # 创建序号决定


def test_all_done_requires_every_instantiated_node():
    runtime = _runtime()
    instances = runtime.instantiate_entry("s1a", 1)
    for index, instance in enumerate(instances[:-1]):
        _finish(runtime, instance, sim_time=index * 10.0)
    assert not runtime.all_done()
    _finish(runtime, instances[-1], sim_time=30.0)
    assert runtime.all_done()


# ═══════════════════════════════════════════════════════════════
# 图级校验
# ═══════════════════════════════════════════════════════════════

def _issue_text(graph: OperationGraph) -> str:
    issues = graph.validate(CATALOG, RECIPE)
    assert issues
    return "\n".join(issues)


def test_default_instantiated_graph_validates_with_run_context():
    runtime = _runtime()
    _run_route(runtime, "s1a", 1)
    _run_route(runtime, "s1b", 2)
    _run_route(runtime, "s2d", 3)

    assert runtime.validate() == []
    report = runtime.as_dict()
    assert report["validation"]["ok"]
    assert [row["entry_id"] for row in report["graph"]["instances"]
            if row["entry_id"]] == ["s1a", "s1a", "s1a",
                                     "s1b", "s1b", "s1b", "s1b", "s1b",
                                     "s2d", "s2d", "s2d"]
    assert report["graph"]["all_done"]


def test_validate_reports_cycle_with_instance_locators():
    graph = OperationGraph(RecipeRunContext(
        run_id="run0001", recipe_id=RECIPE.id, recipe_version=RECIPE.version,
        entry_id="s1a", entry_uid=1, entry_item_type="RAW_A",
        entry_zone="line_s1a", route_variant_id="housing_direct"))
    first = graph.add(_instance("run0001:1:housing_direct_A",
                                "housing_direct_A"))
    second = graph.add(_instance("run0001:1:housing_finish", "housing_finish",
                                 predecessors=[first.instance_id]))
    graph.link(second.instance_id, first.instance_id)

    text = _issue_text(graph)
    assert "循环依赖" in text
    assert "run=run0001" in text and "recipe=mixed_line_v1" in text
    assert "instance=" in text


def test_validate_reports_dangling_predecessor_and_mismatched_material():
    graph = OperationGraph()
    graph.add(_instance("run0001:1:housing_finish", "housing_finish",
                        predecessors=["run0001:1:missing"]))
    text = _issue_text(graph)
    assert "前驱实例不在图内" in text

    runtime = _runtime()
    housing = runtime.instantiate_entry("s1a", 1)
    bracket = runtime.instantiate_entry("s2d", 3)
    runtime.graph.link(bracket[0].instance_id, housing[1].instance_id)
    text = "\n".join(runtime.validate())
    assert "不能接入本工序输入" in text
    assert "operation=housing_finish" in text


def test_validate_reports_unselected_route_instances():
    runtime = _runtime()
    instances = runtime.instantiate_entry("s1a", 1)
    instances[0].selected_route = "housing_alt"

    text = "\n".join(runtime.validate())
    assert "不属于" in text
    assert "缺少路线工序" in text


def test_validate_reports_missing_route_terminal():
    graph = OperationGraph(RecipeRunContext(
        run_id="run0001", recipe_id=RECIPE.id, recipe_version=RECIPE.version,
        entry_id="s1a", entry_uid=1, entry_item_type="RAW_A",
        entry_zone="line_s1a", route_variant_id="housing_direct"))
    first = graph.add(_instance("run0001:1:housing_direct_A",
                                "housing_direct_A"))
    graph.add(_instance("run0001:1:housing_finish", "housing_finish",
                        predecessors=[first.instance_id]))

    text = _issue_text(graph)
    assert "缺少路线工序" in text
    assert "缺少终点" in text


# ═══════════════════════════════════════════════════════════════
# E 合流
# ═══════════════════════════════════════════════════════════════

def _both_inputs_ready(runtime: OperationRuntime):
    _run_route(runtime, "s1a", 1)
    _run_route(runtime, "s2d", 3)
    semi = runtime.graph.get("run0001:1:housing_to_E_buffer")
    part = runtime.graph.get("run0002:3:bracket_to_E")
    assert semi is not None and part is not None
    return semi, part


def test_join_node_binds_two_parent_instances_and_commits_once():
    runtime = _runtime()
    semi, part = _both_inputs_ready(runtime)

    instance = runtime.add_join(parents=[semi, part],
                                input_uids=[1, 3], uid=4, sim_time=40.0)
    assert instance.template_id == "kit_join"
    assert instance.selected_route == ""
    assert instance.predecessor_instance_ids == [semi.instance_id,
                                                 part.instance_id]
    assert runtime.join_blocked("kit_join")
    assert not runtime.all_done()

    runtime.commit_success(instance.instance_id, output_uids=[4],
                           sim_time=41.0)
    assert instance.state is TaskStatus.DONE
    assert instance.output_uids == [4]
    assert runtime.all_done()
    # 合流节点接入后, 两条输入路线的终点仍判断正确 (不得误报缺终点).
    assert runtime.validate() == []
    with pytest.raises(OperationRuntimeError, match="重复提交"):
        runtime.commit_success(instance.instance_id, output_uids=[4],
                               sim_time=42.0)


def test_join_requires_committed_parents():
    runtime = _runtime()
    runtime.instantiate_entry("s1a", 1)
    runtime.instantiate_entry("s2d", 3)
    semi = runtime.graph.get("run0001:1:housing_to_E_buffer")
    part = runtime.graph.get("run0002:3:bracket_to_E")

    with pytest.raises(OperationRuntimeError, match="未提交"):
        runtime.add_join(parents=[semi, part], input_uids=[1, 3], uid=4)


def test_join_rejects_parents_that_do_not_fill_both_ports():
    runtime = _runtime()
    _run_route(runtime, "s2d", 3)
    _run_route(runtime, "s2d", 5)
    first = runtime.graph.get("run0001:3:bracket_to_E")
    second = runtime.graph.get("run0002:5:bracket_to_E")

    with pytest.raises(OperationRuntimeError, match="合流"):
        runtime.add_join(parents=[first, second], input_uids=[3, 5], uid=6)


def test_completed_output_resolves_join_inputs_by_port():
    runtime = _runtime()
    semi, part = _both_inputs_ready(runtime)

    assert runtime.completed_output(1, JOIN.inputs[0]) is semi
    assert runtime.completed_output(3, JOIN.inputs[1]) is part
    assert runtime.completed_output(1, JOIN.inputs[1]) is None
    assert runtime.completed_output(999, JOIN.inputs[0]) is None
