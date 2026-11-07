"""order_validation.py — 订单 DAG 的纯结构校验 (阶段 1).

校验只回答“这张声明式图是否合法、是否可编译”, 不运行 CoppeliaSim、不申请
资源、不创建运行时实例. 目录级规则 (设备/能力/区域/资源/工步登记) 继续由
:class:`~flexible_factory.domain.process_model.ProcessCatalog` 负责; 本模块
只检查订单层的 ID、边、端口供给、多前驱语义、环、入口与目标可达性.

诊断使用稳定 ``code`` 和 node/edge/port/input 定位, 调用方可直接用于 API
错误响应或 CLI 输出.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .. import spec
from .orders import (ORDER_CONTRACT_VERSION, OrderDiagnostic, OrderEdge,
                     OrderInput, OrderSpec)
from .process_model import (COMPLETION_VERIFIERS, EXECUTOR_KINDS,
                            JOIN_HANDLERS, RUNTIME_JOIN_HANDLERS,
                            WORK_STEP_TEMPLATES, OperationTemplate,
                            ProcessCatalog, ProcessModelError)


@dataclass(frozen=True)
class PortSupplier:
    """一个节点输入端口的唯一供给者."""

    kind: str                         # "input" | "node"
    input_id: str = ""
    node_id: str = ""
    from_port: str = ""

    def as_dict(self) -> dict:
        return {"kind": self.kind, "input_id": self.input_id,
                "node_id": self.node_id, "from_port": self.from_port}


@dataclass
class OrderValidationResult:
    """校验结果与编译器所需的结构索引."""

    order: OrderSpec
    diagnostics: list[OrderDiagnostic] = field(default_factory=list)
    templates: dict[str, OperationTemplate] = field(default_factory=dict)
    suppliers: dict[tuple[str, str], PortSupplier] = field(
        default_factory=dict)
    predecessor_nodes: dict[str, tuple[str, ...]] = field(
        default_factory=dict)
    topological_order: tuple[str, ...] = ()
    entry_node_ids: tuple[str, ...] = ()
    target_node_ids: tuple[str, ...] = ()
    terminal_target_node_ids: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.diagnostics

    def error_codes(self) -> list[str]:
        return [item.code for item in self.diagnostics]

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "diagnostics": [item.as_dict() for item in self.diagnostics],
            "topological_order": list(self.topological_order),
            "entry_node_ids": list(self.entry_node_ids),
            "target_node_ids": list(self.target_node_ids),
            "terminal_target_node_ids": list(
                self.terminal_target_node_ids),
        }


class OrderValidator:
    """订单结构校验器; 无状态, 可复用同一已校验目录."""

    def __init__(self, catalog: ProcessCatalog) -> None:
        self.catalog = catalog

    # ── 入口 ────────────────────────────────────────────────

    def validate(self, order: OrderSpec) -> tuple[OrderDiagnostic, ...]:
        return tuple(self.validate_result(order).diagnostics)

    def validate_result(self, order: OrderSpec) -> OrderValidationResult:
        result = OrderValidationResult(order=order)
        self._check_identity(order, result)
        self._resolve_templates(order, result)
        self._check_edges(order, result)
        self._check_inputs(order, result)
        self._check_ports_closed(order, result)
        self._check_predecessors(order, result)
        self._check_cycles(order, result)
        self._check_reachability(order, result)
        return result

    # ── 基础身份 ────────────────────────────────────────────

    def _check_identity(self, order: OrderSpec,
                        result: OrderValidationResult) -> None:
        if order.contract_version != ORDER_CONTRACT_VERSION:
            result.diagnostics.append(OrderDiagnostic(
                "ORDER_CONTRACT_VERSION",
                f"订单契约版本 {order.contract_version!r} 不受支持; "
                f"当前 {ORDER_CONTRACT_VERSION!r}"))
        if order.scene_id != spec.LAYOUT_NAME:
            result.diagnostics.append(OrderDiagnostic(
                "ORDER_SCENE_UNKNOWN",
                f"订单场景 {order.scene_id!r} 不是当前仿真场景 "
                f"{spec.LAYOUT_NAME!r}"))

        seen_nodes: set[str] = set()
        for node in sorted(order.nodes, key=lambda row: row.node_id):
            if node.node_id in seen_nodes:
                result.diagnostics.append(OrderDiagnostic(
                    "ORDER_NODE_DUPLICATE",
                    f"重复 node_id: {node.node_id!r}",
                    node_id=node.node_id))
            seen_nodes.add(node.node_id)
        seen_inputs: set[str] = set()
        for row in sorted(order.inputs, key=lambda item: item.input_id):
            if row.input_id in seen_inputs:
                result.diagnostics.append(OrderDiagnostic(
                    "ORDER_INPUT_DUPLICATE",
                    f"重复 input_id: {row.input_id!r}",
                    input_id=row.input_id))
            seen_inputs.add(row.input_id)
        if not order.nodes:
            result.diagnostics.append(OrderDiagnostic(
                "ORDER_NODES_EMPTY", "订单至少需要一个节点"))

    # ── 模板 allowlist ──────────────────────────────────────

    def _template(self, template_id: str
                  ) -> Optional[OperationTemplate]:
        try:
            return self.catalog.operation(template_id)
        except ProcessModelError:
            return None

    def _resolve_templates(self, order: OrderSpec,
                           result: OrderValidationResult) -> None:
        for node in sorted(order.nodes, key=lambda row: row.node_id):
            template = self._template(node.template_id)
            if template is None:
                result.diagnostics.append(OrderDiagnostic(
                    "ORDER_TEMPLATE_UNKNOWN",
                    f"节点 {node.node_id!r} 引用未知模板 "
                    f"{node.template_id!r}", node_id=node.node_id))
                continue
            result.templates[node.node_id] = template
            if not template.orchestratable:
                result.diagnostics.append(OrderDiagnostic(
                    "ORDER_TEMPLATE_NOT_ORCHESTRATABLE",
                    f"模板 {template.id!r} 未列入订单编排 allowlist",
                    node_id=node.node_id))
            if template.scene_id and template.scene_id != order.scene_id:
                result.diagnostics.append(OrderDiagnostic(
                    "ORDER_TEMPLATE_SCENE_MISMATCH",
                    f"模板 {template.id!r} 场景范围 "
                    f"{template.scene_id!r} 与订单 {order.scene_id!r} 不一致",
                    node_id=node.node_id))
            if template.executor_kind not in EXECUTOR_KINDS:
                result.diagnostics.append(OrderDiagnostic(
                    "ORDER_EXECUTOR_NOT_ALLOWED",
                    f"模板 {template.id!r} 执行器 "
                    f"{template.executor_kind!r} 未登记",
                    node_id=node.node_id))
            if template.completion_verifier not in COMPLETION_VERIFIERS:
                result.diagnostics.append(OrderDiagnostic(
                    "ORDER_VERIFIER_NOT_ALLOWED",
                    f"模板 {template.id!r} 完成验证器 "
                    f"{template.completion_verifier!r} 未登记",
                    node_id=node.node_id))
            for step in template.work_steps:
                if step.template_id not in WORK_STEP_TEMPLATES:
                    result.diagnostics.append(OrderDiagnostic(
                        "ORDER_WORK_STEP_NOT_ALLOWED",
                        f"模板 {template.id!r} 工步 "
                        f"{step.template_id!r} 未登记",
                        node_id=node.node_id))
            if template.executor_kind == "logic":
                if template.join_handler_id not in JOIN_HANDLERS:
                    result.diagnostics.append(OrderDiagnostic(
                        "ORDER_JOIN_HANDLER_UNKNOWN",
                        f"模板 {template.id!r} join handler "
                        f"{template.join_handler_id!r} 未登记",
                        node_id=node.node_id))
                elif template.join_handler_id not in RUNTIME_JOIN_HANDLERS:
                    result.diagnostics.append(OrderDiagnostic(
                        "ORDER_JOIN_HANDLER_NOT_EXECUTABLE",
                        f"模板 {template.id!r} join handler "
                        f"{template.join_handler_id!r} 没有运行时实现",
                        node_id=node.node_id))
                if len(template.inputs) < 2:
                    result.diagnostics.append(OrderDiagnostic(
                        "ORDER_JOIN_ARITY",
                        f"逻辑合流 {template.id!r} 至少需要两个输入端口",
                        node_id=node.node_id))
            elif len(template.inputs) != 1:
                result.diagnostics.append(OrderDiagnostic(
                    "ORDER_MULTI_INPUT_UNSUPPORTED",
                    f"非逻辑工序 {template.id!r} 只支持一个输入端口; "
                    f"实际 {len(template.inputs)}",
                    node_id=node.node_id))

    # ── 边 ──────────────────────────────────────────────────

    def _resolved_edge_port(self, edge: OrderEdge,
                            target: Optional[OperationTemplate]
                            ) -> tuple[str, str]:
        """返回 (port_id, 诊断 code); 空 port_id 表示无法解析."""
        if edge.to_port:
            return edge.to_port, ""
        if target is None:
            return "", "ORDER_EDGE_UNKNOWN_NODE"
        ports = target.input_port_ids
        if len(ports) == 1:
            return ports[0], ""
        return "", "ORDER_EDGE_PORT_REQUIRED"

    def _check_edges(self, order: OrderSpec,
                     result: OrderValidationResult) -> None:
        seen_edges: set[tuple[str, str, str, str]] = set()
        seen_sources: set[tuple[str, str]] = set()
        predecessor_sets: dict[str, list[str]] = {}
        for index, edge in enumerate(order.edges):
            key = (edge.from_node, edge.to_node, edge.to_port,
                   edge.from_port)
            if key in seen_edges:
                result.diagnostics.append(OrderDiagnostic(
                    "ORDER_EDGE_DUPLICATE",
                    f"重复边 {edge.from_node}->{edge.to_node}"
                    f"({edge.to_port or 'auto'})",
                    node_id=edge.to_node, edge_index=index,
                    port_id=edge.to_port))
                continue
            seen_edges.add(key)
            if edge.from_node == edge.to_node:
                result.diagnostics.append(OrderDiagnostic(
                    "ORDER_EDGE_SELF",
                    f"边不能自环: {edge.from_node!r}",
                    node_id=edge.from_node, edge_index=index))
                continue
            source = result.templates.get(edge.from_node)
            target = result.templates.get(edge.to_node)
            if edge.from_node not in {row.node_id for row in order.nodes}:
                result.diagnostics.append(OrderDiagnostic(
                    "ORDER_EDGE_UNKNOWN_NODE",
                    f"边起点 {edge.from_node!r} 不存在",
                    node_id=edge.from_node, edge_index=index))
                continue
            if edge.to_node not in {row.node_id for row in order.nodes}:
                result.diagnostics.append(OrderDiagnostic(
                    "ORDER_EDGE_UNKNOWN_NODE",
                    f"边终点 {edge.to_node!r} 不存在",
                    node_id=edge.to_node, edge_index=index))
                continue
            if source is None or target is None:
                continue
            if edge.from_port and \
                    edge.from_port != source.resolved_output_port_id:
                result.diagnostics.append(OrderDiagnostic(
                    "ORDER_EDGE_OUTPUT_PORT",
                    f"边起点 {source.id!r} 没有输出端口 "
                    f"{edge.from_port!r}",
                    node_id=edge.from_node, edge_index=index,
                    port_id=edge.from_port))
                continue
            port_id, code = self._resolved_edge_port(edge, target)
            if code:
                result.diagnostics.append(OrderDiagnostic(
                    code,
                    (f"节点 {edge.to_node!r} 有多个输入端口, 边必须声明 "
                     f"to_port" if code == "ORDER_EDGE_PORT_REQUIRED"
                     else f"边终点 {edge.to_node!r} 不存在"),
                    node_id=edge.to_node, edge_index=index))
                continue
            port = target.input_port(port_id)
            if port is None:
                result.diagnostics.append(OrderDiagnostic(
                    "ORDER_EDGE_INPUT_PORT",
                    f"节点 {edge.to_node!r} 没有输入端口 {port_id!r}",
                    node_id=edge.to_node, edge_index=index, port_id=port_id))
                continue
            output = source.output
            if output is None or output.zone != port.zone \
                    or output.item_type != port.item_type:
                actual = ("无输出" if output is None
                          else f"{output.zone}/{output.item_type.value}")
                result.diagnostics.append(OrderDiagnostic(
                    "ORDER_EDGE_MATERIAL_MISMATCH",
                    f"边 {edge.from_node}->{edge.to_node}[{port_id}] "
                    f"物料不连续: 上游输出 {actual}, 下游输入 "
                    f"{port.zone}/{port.item_type.value}",
                    node_id=edge.to_node, edge_index=index, port_id=port_id))
                continue
            supplier = (edge.from_node, port_id)
            if supplier in seen_sources:
                result.diagnostics.append(OrderDiagnostic(
                    "ORDER_EDGE_DUPLICATE_SOURCE",
                    f"上游 {edge.from_node!r} 重复连接到同一节点 "
                    f"{edge.to_node!r} 的多个端口",
                    node_id=edge.to_node, edge_index=index))
                continue
            seen_sources.add(supplier)
            existing = result.suppliers.get((edge.to_node, port_id))
            if existing is not None:
                result.diagnostics.append(OrderDiagnostic(
                    "ORDER_PORT_MULTIPLE_SUPPLIERS",
                    f"节点 {edge.to_node!r} 端口 {port_id!r} 已有供给者",
                    node_id=edge.to_node, edge_index=index, port_id=port_id))
                continue
            result.suppliers[(edge.to_node, port_id)] = PortSupplier(
                kind="node", node_id=edge.from_node,
                from_port=edge.from_port
                or source.resolved_output_port_id)
            predecessor_sets.setdefault(edge.to_node, [])
            if edge.from_node not in predecessor_sets[edge.to_node]:
                predecessor_sets[edge.to_node].append(edge.from_node)
        result.predecessor_nodes = {
            node_id: tuple(sorted(nodes))
            for node_id, nodes in predecessor_sets.items()}

    # ── 订单输入 ────────────────────────────────────────────

    def _check_inputs(self, order: OrderSpec,
                      result: OrderValidationResult) -> None:
        entry_nodes: list[str] = []
        for row in sorted(order.inputs, key=lambda item: item.input_id):
            zone_spec = None
            try:
                zone_spec = self.catalog.zone(row.zone)
            except ProcessModelError:
                result.diagnostics.append(OrderDiagnostic(
                    "ORDER_INPUT_ZONE_UNKNOWN",
                    f"投料 {row.input_id!r} 引用未知区域 {row.zone!r}",
                    input_id=row.input_id))
                continue
            if not zone_spec.spawnable:
                result.diagnostics.append(OrderDiagnostic(
                    "ORDER_INPUT_ZONE_NOT_SPAWNABLE",
                    f"投料 {row.input_id!r} 区域 {row.zone!r} "
                    f"不能在订单开始时生成来料",
                    input_id=row.input_id))
            binding = self._resolve_input_binding(order, result, row)
            if binding is None:
                continue
            node_id, port_id = binding
            target = result.templates[node_id]
            port = target.input_port(port_id)
            if port is None:
                result.diagnostics.append(OrderDiagnostic(
                    "ORDER_INPUT_PORT_UNKNOWN",
                    f"投料 {row.input_id!r} 绑定到节点 {node_id!r} 的未知"
                    f"端口 {port_id!r}", input_id=row.input_id,
                    node_id=node_id, port_id=port_id))
                continue
            if port.item_type != row.item_type:
                result.diagnostics.append(OrderDiagnostic(
                    "ORDER_INPUT_TYPE_MISMATCH",
                    f"投料 {row.input_id!r} 类型 {row.item_type.value} 与"
                    f"端口 {port_id!r} 的 {port.item_type.value} 不一致",
                    input_id=row.input_id, node_id=node_id,
                    port_id=port_id))
                continue
            if port.zone != row.zone:
                result.diagnostics.append(OrderDiagnostic(
                    "ORDER_INPUT_ZONE_MISMATCH",
                    f"投料 {row.input_id!r} 区域 {row.zone!r} 与端口 "
                    f"{port_id!r} 的 {port.zone!r} 不一致",
                    input_id=row.input_id, node_id=node_id,
                    port_id=port_id))
                continue
            if target.executor_kind == "logic":
                result.diagnostics.append(OrderDiagnostic(
                    "ORDER_JOIN_PORT_INPUT",
                    f"逻辑合流节点 {node_id!r} 的端口 {port_id!r} "
                    f"必须由前置工序供给, 不能直接投料",
                    input_id=row.input_id, node_id=node_id,
                    port_id=port_id))
                continue
            existing = result.suppliers.get((node_id, port_id))
            if existing is not None:
                result.diagnostics.append(OrderDiagnostic(
                    "ORDER_PORT_MULTIPLE_SUPPLIERS",
                    f"节点 {node_id!r} 端口 {port_id!r} 已有供给者",
                    input_id=row.input_id, node_id=node_id,
                    port_id=port_id))
                continue
            result.suppliers[(node_id, port_id)] = PortSupplier(
                kind="input", input_id=row.input_id)
            if node_id not in entry_nodes:
                entry_nodes.append(node_id)
        result.entry_node_ids = tuple(sorted(entry_nodes))
        if not order.inputs:
            result.diagnostics.append(OrderDiagnostic(
                "ORDER_INPUTS_EMPTY", "订单至少需要一笔投料"))
        elif not result.entry_node_ids:
            result.diagnostics.append(OrderDiagnostic(
                "ORDER_NO_ENTRY", "订单没有任何可解析的入口投料"))

    def _resolve_input_binding(
            self, order: OrderSpec, result: OrderValidationResult,
            row: OrderInput) -> Optional[tuple[str, str]]:
        if row.node_id:
            if row.node_id not in result.templates:
                result.diagnostics.append(OrderDiagnostic(
                    "ORDER_INPUT_NODE_UNKNOWN",
                    f"投料 {row.input_id!r} 引用未知节点 "
                    f"{row.node_id!r}", input_id=row.input_id,
                    node_id=row.node_id))
                return None
            if not row.port_id:
                result.diagnostics.append(OrderDiagnostic(
                    "ORDER_INPUT_PORT_REQUIRED",
                    f"投料 {row.input_id!r} 指定了 node_id 但缺少 port_id",
                    input_id=row.input_id, node_id=row.node_id))
                return None
            return row.node_id, row.port_id
        candidates: list[tuple[str, str]] = []
        for node_id, target in sorted(result.templates.items()):
            if target.executor_kind == "logic":
                continue
            for port_id in target.input_port_ids:
                if (node_id, port_id) in result.suppliers:
                    continue
                port = target.input_port(port_id)
                if port is not None and port.zone == row.zone \
                        and port.item_type == row.item_type:
                    candidates.append((node_id, port_id))
        if not candidates:
            result.diagnostics.append(OrderDiagnostic(
                "ORDER_INPUT_NO_PORT",
                f"投料 {row.input_id!r} ({row.zone}/"
                f"{row.item_type.value}) 没有未供给的匹配端口",
                input_id=row.input_id))
            return None
        if len(candidates) > 1:
            result.diagnostics.append(OrderDiagnostic(
                "ORDER_INPUT_AMBIGUOUS",
                f"投料 {row.input_id!r} 可匹配多个端口 {candidates}; "
                f"请显式声明 node_id/port_id", input_id=row.input_id))
            return None
        return candidates[0]

    # ── 端口闭合与多前驱 ────────────────────────────────────

    def _check_ports_closed(self, order: OrderSpec,
                            result: OrderValidationResult) -> None:
        counts: dict[tuple[str, str], int] = {}
        for key in result.suppliers:
            counts[key] = counts.get(key, 0) + 1
        for node in sorted(order.nodes, key=lambda row: row.node_id):
            target = result.templates.get(node.node_id)
            if target is None:
                continue
            for port_id in target.input_port_ids:
                count = counts.get((node.node_id, port_id), 0)
                if count == 0:
                    result.diagnostics.append(OrderDiagnostic(
                        "ORDER_PORT_UNSUPPLIED",
                        f"节点 {node.node_id!r} 输入端口 {port_id!r} "
                        f"没有供给者", node_id=node.node_id,
                        port_id=port_id))
                elif count > 1:
                    result.diagnostics.append(OrderDiagnostic(
                        "ORDER_PORT_MULTIPLE_SUPPLIERS",
                        f"节点 {node.node_id!r} 输入端口 {port_id!r} "
                        f"有多个供给者", node_id=node.node_id,
                        port_id=port_id))

    def _check_predecessors(self, order: OrderSpec,
                            result: OrderValidationResult) -> None:
        for node in sorted(order.nodes, key=lambda row: row.node_id):
            target = result.templates.get(node.node_id)
            if target is None:
                continue
            predecessors = result.predecessor_nodes.get(node.node_id, ())
            if len(predecessors) > 1:
                if target.predecessor_policy != "all":
                    result.diagnostics.append(OrderDiagnostic(
                        "ORDER_PREDECESSOR_POLICY",
                        f"多前驱节点 {node.node_id!r} 必须声明 AND "
                        f"(predecessor_policy='all')",
                        node_id=node.node_id))
                if target.executor_kind != "logic":
                    result.diagnostics.append(OrderDiagnostic(
                        "ORDER_MULTI_INPUT_UNSUPPORTED",
                        f"多前驱节点 {node.node_id!r} 只有已登记逻辑合流"
                        f"处理器才能执行", node_id=node.node_id))
            if target.executor_kind == "logic" \
                    and len(predecessors) != len(target.inputs):
                result.diagnostics.append(OrderDiagnostic(
                    "ORDER_JOIN_ARITY",
                    f"逻辑合流 {node.node_id!r} 需要 "
                    f"{len(target.inputs)} 个前驱, 实际 "
                    f"{len(predecessors)}", node_id=node.node_id))

    # ── 环与可达性 ──────────────────────────────────────────

    def _topological(self, order: OrderSpec,
                     result: OrderValidationResult) -> tuple[str, ...]:
        adjacency: dict[str, set[str]] = {row.node_id: set()
                                          for row in order.nodes}
        indegree: dict[str, int] = {row.node_id: 0 for row in order.nodes}
        for edge in order.edges:
            if edge.from_node in adjacency and edge.to_node in indegree:
                if edge.to_node not in adjacency[edge.from_node]:
                    adjacency[edge.from_node].add(edge.to_node)
                    indegree[edge.to_node] += 1
        ready = sorted(node_id for node_id, degree in indegree.items()
                       if degree == 0)
        ordered: list[str] = []
        while ready:
            node_id = ready.pop(0)
            ordered.append(node_id)
            for successor in sorted(adjacency[node_id]):
                indegree[successor] -= 1
                if indegree[successor] == 0:
                    ready.append(successor)
                    ready.sort()
        return tuple(ordered)

    def _check_cycles(self, order: OrderSpec,
                      result: OrderValidationResult) -> None:
        ordered = self._topological(order, result)
        result.topological_order = ordered
        if len(ordered) != len(order.nodes):
            remaining = sorted({row.node_id for row in order.nodes}
                               - set(ordered))
            result.diagnostics.append(OrderDiagnostic(
                "ORDER_CYCLE",
                f"订单 DAG 存在循环依赖; 未排序节点 {remaining}",
                node_id=remaining[0] if remaining else ""))

    def _check_reachability(self, order: OrderSpec,
                            result: OrderValidationResult) -> None:
        node_ids = {row.node_id for row in order.nodes}
        adjacency: dict[str, set[str]] = {node_id: set()
                                          for node_id in node_ids}
        reverse: dict[str, set[str]] = {node_id: set()
                                        for node_id in node_ids}
        for edge in order.edges:
            if edge.from_node in adjacency and edge.to_node in reverse:
                adjacency[edge.from_node].add(edge.to_node)
                reverse[edge.to_node].add(edge.from_node)

        reached: set[str] = set()
        frontier = list(result.entry_node_ids)
        while frontier:
            node_id = frontier.pop()
            if node_id in reached:
                continue
            reached.add(node_id)
            frontier.extend(sorted(adjacency.get(node_id, ())))
        unreachable = sorted(node_ids - reached)
        for node_id in unreachable:
            result.diagnostics.append(OrderDiagnostic(
                "ORDER_NODE_UNREACHABLE",
                f"节点 {node_id!r} 不能从订单投料到达",
                node_id=node_id))

        target_nodes = sorted(
            node_id for node_id, template in result.templates.items()
            if template.output is not None
            and template.output.item_type == order.target.item_type)
        result.target_node_ids = tuple(target_nodes)
        if not target_nodes:
            result.diagnostics.append(OrderDiagnostic(
                "ORDER_TARGET_UNREACHABLE",
                f"没有任何节点产出目标物料 "
                f"{order.target.item_type.value}"))
            return
        terminal_targets = sorted(
            node_id for node_id in target_nodes
            if not adjacency.get(node_id))
        result.terminal_target_node_ids = tuple(terminal_targets)
        if not terminal_targets:
            result.diagnostics.append(OrderDiagnostic(
                "ORDER_TARGET_TERMINAL",
                f"目标物料 {order.target.item_type.value} 只出现在"
                f"中间节点, 没有终端产物节点"))

        target_reachable: set[str] = set()
        frontier = list(target_nodes)
        while frontier:
            node_id = frontier.pop()
            if node_id in target_reachable:
                continue
            target_reachable.add(node_id)
            frontier.extend(sorted(reverse.get(node_id, ())))
        dangling = sorted(node_ids - target_reachable)
        for node_id in dangling:
            result.diagnostics.append(OrderDiagnostic(
                "ORDER_NODE_DANGLING",
                f"节点 {node_id!r} 不在通往目标 "
                f"{order.target.item_type.value} 的路径上",
                node_id=node_id))


__all__ = ["OrderValidationResult", "OrderValidator", "PortSupplier"]
