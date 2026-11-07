"""order_compiler.py — 声明式订单 DAG → 运行时工序实例图 (阶段 1).

编译器是纯 Python: 不导入 CoppeliaSim、application 或 persistence, 不申请
资源, 不改生命周期. 它按订单输入数量分配确定性物料 UID, 把节点/边解析为
:class:`~flexible_factory.domain.operation_runtime.OperationInstance`, 并
建立真实前驱索引.

配对规则 (首期明确且可解释):
  - 单输入节点按输入物料的 ordinal 一对一实例化;
  - 另一类 join 节点的每个输入端口各自有一组前驱实例, 按稳定 ordinal
    配对; 各端口实例数必须相等, 否则编译报 ``ORDER_CARDINALITY_MISMATCH``;
  - 逻辑 join 的输出分配新 UID, instance ID 使用输入 UID 元组;
  - 同一模板可在订单中重复使用, instance/task ID 都包含 ``node_id``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from .operation_runtime import OperationInstance, legacy_task_fields
from .order_validation import OrderValidationResult, OrderValidator
from .orders import (ORDER_CONTRACT_VERSION, OrderCompileError,
                     OrderDiagnostic, OrderRunContext, OrderSpec)
from .process_model import OperationTemplate, ProcessCatalog


# ═══════════════════════════════════════════════════════════════
# 编译产物
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class MaterialPlan:
    """编译器为一次订单运行分配的物料 UID; 运行期必须按此生成."""

    uid: int
    item_type: str
    zone: str
    kind: str                          # input | join
    input_id: str = ""
    node_id: str = ""
    port_id: str = ""
    producer_instance_id: str = ""

    def as_dict(self) -> dict:
        return {
            "uid": int(self.uid), "item_type": self.item_type,
            "zone": self.zone, "kind": self.kind,
            "input_id": self.input_id, "node_id": self.node_id,
            "port_id": self.port_id,
            "producer_instance_id": self.producer_instance_id,
        }


@dataclass(frozen=True)
class CompiledOrder:
    """已校验并实例化的订单运行图; 不含句柄或资源所有权."""

    order: OrderSpec
    run_id: str
    instances: tuple[OperationInstance, ...]
    material_plans: tuple[MaterialPlan, ...]
    validation: OrderValidationResult
    context: OrderRunContext
    topological_order: tuple[str, ...]
    instance_counts: dict[str, int]

    @property
    def digest(self) -> str:
        return self.order.dag_digest()

    @property
    def target_instance_ids(self) -> tuple[str, ...]:
        return self.context.target_instance_ids

    def as_dict(self) -> dict:
        return {
            "order": self.order.as_dict(),
            "run_id": self.run_id,
            "digest": self.digest,
            "context": self.context.as_dict(),
            "topological_order": list(self.topological_order),
            "instance_counts": dict(self.instance_counts),
            "instances": [row.as_dict() for row in self.instances],
            "material_plans": [row.as_dict()
                               for row in self.material_plans],
            "target_instance_ids": list(self.target_instance_ids),
        }


@dataclass(frozen=True)
class CompilePreview:
    """只读编译预览; 不启动仿真、不申请资源、不改变生命周期状态."""

    order_id: str
    version: int
    scene_id: str
    digest: str
    ok: bool
    diagnostics: tuple[OrderDiagnostic, ...]
    topological_order: tuple[str, ...]
    entry_node_ids: tuple[str, ...]
    terminal_target_node_ids: tuple[str, ...]
    nodes: tuple[dict, ...]
    instance_counts: dict[str, int]
    material_plans: tuple[MaterialPlan, ...]
    target_instance_ids: tuple[str, ...]
    expected_outputs: tuple[dict, ...]
    simulation_started: bool = False

    def as_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "version": int(self.version),
            "scene_id": self.scene_id,
            "contract_version": ORDER_CONTRACT_VERSION,
            "digest": self.digest,
            "ok": self.ok,
            "simulation_started": bool(self.simulation_started),
            "diagnostics": [item.as_dict()
                            for item in self.diagnostics],
            "topological_order": list(self.topological_order),
            "entry_node_ids": list(self.entry_node_ids),
            "terminal_target_node_ids": list(
                self.terminal_target_node_ids),
            "nodes": list(self.nodes),
            "instance_counts": dict(self.instance_counts),
            "material_plans": [row.as_dict()
                               for row in self.material_plans],
            "target_instance_ids": list(self.target_instance_ids),
            "expected_outputs": list(self.expected_outputs),
        }


class OrderDagCompiler:
    """订单 DAG 编译器; 同一目录/订单/run_id 产生确定性结果."""

    def __init__(self, catalog: ProcessCatalog) -> None:
        self.catalog = catalog
        self.validator = OrderValidator(catalog)

    # ── 编译 ────────────────────────────────────────────────

    def compile(self, order: OrderSpec, *, run_id: str,
                start_uid: int = 1) -> CompiledOrder:
        run_id = str(run_id or "").strip()
        if not run_id:
            raise OrderCompileError([OrderDiagnostic(
                "ORDER_RUN_ID_MISSING", "编译需要非空 run_id")])
        start_uid = int(start_uid)
        if start_uid < 0:
            raise OrderCompileError([OrderDiagnostic(
                "ORDER_UID_RANGE", "start_uid 不能为负")])
        result = self.validator.validate_result(order)
        if not result.ok:
            raise OrderCompileError(result.diagnostics)

        plans: list[MaterialPlan] = []
        input_plans: dict[tuple[str, str], list[MaterialPlan]] = \
            defaultdict(list)
        next_uid = start_uid
        for order_input in sorted(order.inputs, key=lambda row: row.input_id):
            bindings = [
                key for key, supplier in result.suppliers.items()
                if supplier.kind == "input"
                and supplier.input_id == order_input.input_id]
            if len(bindings) != 1:
                raise OrderCompileError([OrderDiagnostic(
                    "ORDER_INPUT_BINDING",
                    f"投料 {order_input.input_id!r} 没有唯一入口端口",
                    input_id=order_input.input_id)])
            node_id, port_id = bindings[0]
            for _ordinal in range(int(order_input.quantity)):
                plan = MaterialPlan(
                    uid=next_uid, item_type=order_input.item_type.value,
                    zone=order_input.zone, kind="input",
                    input_id=order_input.input_id, node_id=node_id,
                    port_id=port_id)
                next_uid += 1
                plans.append(plan)
                input_plans[(node_id, port_id)].append(plan)

        instances_by_node: dict[str, list[OperationInstance]] = \
            defaultdict(list)
        output_context: dict[str, tuple[int, ...]] = {}
        for node_id in result.topological_order:
            template = result.templates[node_id]
            port_contexts: list[list[tuple[int, ...]]] = []
            port_predecessors: list[list[OperationInstance]] = []
            for port_id in template.input_port_ids:
                supplier = result.suppliers.get((node_id, port_id))
                if supplier is None:
                    raise OrderCompileError([OrderDiagnostic(
                        "ORDER_PORT_UNSUPPLIED",
                        f"节点 {node_id!r} 端口 {port_id!r} 没有供给者",
                        node_id=node_id, port_id=port_id)])
                if supplier.kind == "input":
                    rows = sorted(input_plans[(node_id, port_id)],
                                  key=lambda row: row.uid)
                    port_contexts.append([(row.uid,) for row in rows])
                    port_predecessors.append([])
                else:
                    predecessors = instances_by_node.get(
                        supplier.node_id, [])
                    port_contexts.append([
                        output_context[row.instance_id]
                        for row in predecessors])
                    port_predecessors.append(list(predecessors))
            counts = {len(rows) for rows in port_contexts}
            if len(counts) != 1:
                raise OrderCompileError([OrderDiagnostic(
                    "ORDER_CARDINALITY_MISMATCH",
                    f"节点 {node_id!r} 各输入端口实例数不一致: "
                    f"{[len(rows) for rows in port_contexts]}",
                    node_id=node_id)])
            count = counts.pop() if counts else 0
            if count == 0:
                raise OrderCompileError([OrderDiagnostic(
                    "ORDER_NODE_NO_INSTANCE",
                    f"节点 {node_id!r} 没有可实例化的输入物料",
                    node_id=node_id)])
            for ordinal in range(count):
                input_context = tuple(
                    uid
                    for contexts in port_contexts
                    for uid in contexts[ordinal])
                predecessors = tuple(
                    rows[ordinal]
                    for rows in port_predecessors if rows)
                instance, plan = self._build_instance(
                    order=order, run_id=run_id, node_id=node_id,
                    template=template, input_context=input_context,
                    predecessors=predecessors, ordinal=ordinal,
                    next_uid=next_uid, result=result)
                if plan is not None:
                    next_uid += 1
                    plans.append(plan)
                instances_by_node[node_id].append(instance)
                output_context[instance.instance_id] = (int(instance.uid),)

        instances = tuple(
            instance
            for node_id in result.topological_order
            for instance in instances_by_node[node_id])
        terminal_instances = [
            instance
            for node_id in result.terminal_target_node_ids
            for instance in instances_by_node[node_id]]
        actual = len(terminal_instances)
        expected = int(order.target.quantity)
        if actual != expected:
            raise OrderCompileError([OrderDiagnostic(
                "ORDER_TARGET_QUANTITY_MISMATCH",
                f"目标 {order.target.item_type.value} 需要 {expected} 件, "
                f"编译得到 {actual} 个终端实例")])

        target_ids = tuple(row.instance_id for row in terminal_instances)
        context = OrderRunContext(
            order_id=order.order_id, order_version=int(order.version),
            dag_digest=order.dag_digest(), scene_id=order.scene_id,
            node_ids=tuple(result.topological_order),
            target_item_type=order.target.item_type.value,
            target_quantity=int(order.target.quantity),
            target_instance_ids=target_ids)
        counts = {node_id: len(rows)
                  for node_id, rows in instances_by_node.items()}
        return CompiledOrder(
            order=order, run_id=run_id, instances=instances,
            material_plans=tuple(sorted(plans, key=lambda row: row.uid)),
            validation=result, context=context,
            topological_order=result.topological_order,
            instance_counts=counts)

    def _build_instance(
            self, *, order: OrderSpec, run_id: str, node_id: str,
            template: OperationTemplate, input_context: tuple[int, ...],
            predecessors: tuple[OperationInstance, ...], ordinal: int,
            next_uid: int, result: OrderValidationResult
            ) -> tuple[OperationInstance, Optional[MaterialPlan]]:
        if template.output is None:
            raise OrderCompileError([OrderDiagnostic(
                "ORDER_OUTPUT_MISSING",
                f"模板 {template.id!r} 缺少输出物料端口",
                node_id=node_id)])
        fields = legacy_task_fields(template)
        supplier_inputs = sorted(
            supplier.input_id
            for (candidate_node, _port), supplier
            in result.suppliers.items()
            if candidate_node == node_id and supplier.kind == "input")
        entry_id = "+".join(dict.fromkeys(supplier_inputs))
        if template.executor_kind == "logic":
            output_uid = int(next_uid)
            instance_id = (f"{run_id}:{node_id}:"
                           f"{'+'.join(str(uid) for uid in input_context)}")
            plan: Optional[MaterialPlan] = MaterialPlan(
                uid=output_uid,
                item_type=(template.output.item_type.value
                           if template.output else ""),
                zone=(template.output.zone if template.output else ""),
                kind="join", node_id=node_id,
                producer_instance_id=instance_id)
        else:
            output_uid = int(input_context[0])
            instance_id = f"{run_id}:{node_id}:{output_uid}"
            plan = None
        predecessor_ids = tuple(
            row.instance_id for row in predecessors)
        predecessor_ports = {
            row.instance_id: port_id
            for row, port_id in zip(predecessors,
                                    self._predecessor_port_ids(
                                        node_id, result))
            if row is not None}
        instance = OperationInstance(
            instance_id=instance_id,
            task_id=f"{template.kind}_uid{output_uid}_{node_id}",
            template_id=template.id, kind=template.kind,
            uid=output_uid, source=fields["source"],
            destination=fields["destination"], dock=fields["dock"],
            device=fields["device"], transport=bool(fields["transport"]),
            run_id=run_id, recipe_id="", recipe_version="",
            entry_id=entry_id, selected_route="",
            predecessor_instance_ids=list(predecessor_ids),
            predecessor_ports=predecessor_ports,
            input_uids=list(input_context),
            node_id=node_id, order_id=order.order_id,
            order_version=int(order.version),
            capability=template.capability,
            candidate_device_ids=list(template.candidate_devices),
            current_work_step=(template.work_steps[0].template_id
                               if template.work_steps else ""),
            priority=int(template.priority),
            created_sim_time=0.0)
        return instance, plan

    def _predecessor_port_ids(
            self, node_id: str,
            result: OrderValidationResult) -> list[str]:
        template = result.templates[node_id]
        return [
            port_id for port_id in template.input_port_ids
            if result.suppliers.get((node_id, port_id)) is not None
            and result.suppliers[(node_id, port_id)].kind == "node"]

    # ── 预览 ────────────────────────────────────────────────

    def preview(self, order: OrderSpec, *, run_id: str = "preview",
                start_uid: int = 1) -> CompilePreview:
        result = self.validator.validate_result(order)
        nodes = tuple(self._node_summary(node_id, result)
                      for node_id in result.topological_order)
        if not result.ok:
            return CompilePreview(
                order_id=order.order_id, version=order.version,
                scene_id=order.scene_id, digest=order.dag_digest(),
                ok=False, diagnostics=tuple(result.diagnostics),
                topological_order=result.topological_order,
                entry_node_ids=result.entry_node_ids,
                terminal_target_node_ids=result.terminal_target_node_ids,
                nodes=nodes, instance_counts={}, material_plans=(),
                target_instance_ids=(), expected_outputs=())
        try:
            compiled = self.compile(order, run_id=run_id,
                                    start_uid=start_uid)
        except OrderCompileError as exc:
            return CompilePreview(
                order_id=order.order_id, version=order.version,
                scene_id=order.scene_id, digest=order.dag_digest(),
                ok=False, diagnostics=tuple(exc.diagnostics),
                topological_order=result.topological_order,
                entry_node_ids=result.entry_node_ids,
                terminal_target_node_ids=result.terminal_target_node_ids,
                nodes=nodes, instance_counts={}, material_plans=(),
                target_instance_ids=(), expected_outputs=())
        by_id = {row.instance_id: row for row in compiled.instances}
        expected = []
        for instance_id in compiled.target_instance_ids:
            instance = by_id[instance_id]
            expected.append({
                "instance_id": instance.instance_id,
                "node_id": instance.node_id,
                "uid": int(instance.uid),
                "item_type": (compiled.order.target.item_type.value),
                "zone": instance.destination,
                "path": self._path(instance, by_id),
            })
        return CompilePreview(
            order_id=order.order_id, version=order.version,
            scene_id=order.scene_id, digest=compiled.digest, ok=True,
            diagnostics=(), topological_order=compiled.topological_order,
            entry_node_ids=result.entry_node_ids,
            terminal_target_node_ids=result.terminal_target_node_ids,
            nodes=nodes, instance_counts=dict(compiled.instance_counts),
            material_plans=compiled.material_plans,
            target_instance_ids=compiled.target_instance_ids,
            expected_outputs=tuple(expected))

    def _node_summary(self, node_id: str,
                      result: OrderValidationResult) -> dict:
        template = result.templates.get(node_id)
        if template is None:
            return {"node_id": node_id, "template_id": "",
                    "available": False}
        return {
            "node_id": node_id,
            "template_id": template.id,
            "available": True,
            "semantics": template.semantics,
            "executor_kind": template.executor_kind,
            "capability": template.capability,
            "candidate_devices": list(template.candidate_devices),
            "resources": list(template.resources),
            "inputs": [
                {"port_id": port_id, "zone": port.zone,
                 "item_type": port.item_type.value}
                for port_id, port in zip(template.input_port_ids,
                                         template.inputs)],
            "output": (None if template.output is None else {
                "port_id": template.resolved_output_port_id,
                "zone": template.output.zone,
                "item_type": template.output.item_type.value}),
            "source_kind": template.source_kind,
            "sink_kind": template.sink_kind,
            "completion_verifier": template.completion_verifier,
            "work_steps": [step.template_id
                           for step in template.work_steps],
            "join_handler_id": template.join_handler_id,
            "predecessor_nodes": list(
                result.predecessor_nodes.get(node_id, ())),
        }

    @staticmethod
    def _path(instance: OperationInstance,
              by_id: dict[str, OperationInstance]) -> list[str]:
        path: list[str] = []
        seen: set[str] = set()

        def visit(row: OperationInstance) -> None:
            if row.instance_id in seen:
                return
            seen.add(row.instance_id)
            for predecessor_id in row.predecessor_instance_ids:
                predecessor = by_id.get(predecessor_id)
                if predecessor is not None:
                    visit(predecessor)
            label = row.node_id or row.template_id
            if label not in path:
                path.append(label)

        visit(instance)
        return path


__all__ = ["CompilePreview", "CompiledOrder", "MaterialPlan",
           "OrderDagCompiler"]
