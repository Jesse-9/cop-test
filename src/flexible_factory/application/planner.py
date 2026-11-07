"""planner.py — 有界、确定性的订单 DAG 自主编排器 (阶段 4).

planner 只读静态目录, 不申请资源、不操作 CoppeliaSim、不写数据库:
输入目标物料/数量、可用投料和允许范围; 输出 `OrderSpec` 草稿、编译预览和
候选解释. 搜索空间只包含:

  - 目录中已登记的 `RouteVariant` 线性路线 (route macro);
  - 已登记 `join_handler_id` 的逻辑合流 (join macro);
  - 允许的 template/device 过滤.

planner 不把单道工序的端口兼容性当作可自由拼接的边: route macro 保留静态
目录中已验证的工序链, 避免跳过 AGV 运输等中间工序. 生成结果仍调用阶段 1
validator/compiler; 编译失败作为 planner 缺陷返回, 不静默修补.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from itertools import product
from typing import Iterable, Optional

from .. import spec
from ..domain.materials import ItemType
from ..domain.order_compiler import CompilePreview, OrderDagCompiler
from ..domain.orders import (ORDER_CONTRACT_VERSION, OrderEdge, OrderInput,
                             OrderNode, OrderSpec, OrderSpecError, OrderTarget)
from ..domain.process_model import (JOIN_HANDLERS, RUNTIME_JOIN_HANDLERS,
                                    OperationTemplate, ProcessCatalog)

PLANNER_POLICY_VERSION = spec.ORDER_PLANNER_POLICY_VERSION
DEFAULT_MAX_CANDIDATES = 64
DEFAULT_MAX_DEPTH = 12
DEFAULT_MAX_NODES = 64

_UNMODELED_DIMENSIONS = [
    "设备健康/故障率/节拍",
    "实时资源占用与 AGV 排队",
    "几何可达性、碰撞与运动规划可行性",
    "局部失败风险和恢复代价",
    "多订单并行与跨订单优化",
]

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

_CATEGORY_ORDER = {
    "policy": 0,
    "scene": 1,
    "target": 2,
    "template": 3,
    "device": 4,
    "join": 5,
    "input": 6,
    "search_limit": 7,
    "compile": 8,
    "preference": 9,
}


class PlannerError(RuntimeError):
    code = "PLANNER"


class PlannerInputError(PlannerError):
    code = "PLANNER_INPUT"


# ═══════════════════════════════════════════════════════════════
# 输入契约
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class PlannerSupply:
    """一种可用投料; zone 为空表示该物料类型可用于其任意入口区域."""

    item_type: ItemType
    quantity: int
    zone: str = ""

    def as_dict(self) -> dict:
        return {"item_type": self.item_type.value,
                "quantity": int(self.quantity), "zone": self.zone}


@dataclass(frozen=True)
class PlannerInput:
    target_item_type: ItemType
    target_quantity: int
    inputs: tuple[PlannerSupply, ...]
    scene_id: str = spec.LAYOUT_NAME
    allowed_template_ids: tuple[str, ...] = ()
    allowed_device_ids: tuple[str, ...] = ()
    preferred_route_ids: tuple[str, ...] = ()
    allow_high_risk: bool = False
    max_candidates: int = DEFAULT_MAX_CANDIDATES
    max_depth: int = DEFAULT_MAX_DEPTH
    max_nodes: int = DEFAULT_MAX_NODES
    order_id: str = "planner-draft"
    order_version: int = 1
    policy_version: str = PLANNER_POLICY_VERSION

    def as_dict(self) -> dict:
        return {
            "policy_version": self.policy_version,
            "target_item_type": self.target_item_type.value,
            "target_quantity": int(self.target_quantity),
            "inputs": [row.as_dict() for row in sorted(
                self.inputs,
                key=lambda item: (item.item_type.value, item.zone,
                                  int(item.quantity)))],
            "scene_id": self.scene_id,
            "allowed_template_ids": list(self.allowed_template_ids),
            "allowed_device_ids": list(self.allowed_device_ids),
            "preferred_route_ids": list(self.preferred_route_ids),
            "allow_high_risk": bool(self.allow_high_risk),
            "max_candidates": int(self.max_candidates),
            "max_depth": int(self.max_depth),
            "max_nodes": int(self.max_nodes),
            "order_id": self.order_id,
            "order_version": int(self.order_version),
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "PlannerInput":
        if not isinstance(payload, dict):
            raise PlannerInputError("planner 输入必须是 JSON 对象")
        allowed = {
            "policy_version", "target_item_type", "target_quantity",
            "inputs", "scene_id", "allowed_template_ids",
            "allowed_device_ids", "preferred_route_ids", "allow_high_risk",
            "max_candidates", "max_depth", "max_nodes", "order_id",
            "order_version",
        }
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise PlannerInputError(f"planner 输入包含未知字段: {unknown}")
        raw_inputs = payload.get("inputs", [])
        if not isinstance(raw_inputs, list):
            raise PlannerInputError("inputs 必须是数组")
        supplies = []
        for index, row in enumerate(raw_inputs):
            if not isinstance(row, dict):
                raise PlannerInputError(f"inputs[{index}] 必须是对象")
            extra = sorted(set(row) - {"item_type", "quantity", "zone"})
            if extra:
                raise PlannerInputError(
                    f"inputs[{index}] 包含未知字段: {extra}")
            item_type = _item_type(row.get("item_type"),
                                   path=f"inputs[{index}].item_type")
            quantity = _positive_int(row.get("quantity", 1),
                                     path=f"inputs[{index}].quantity")
            zone = _text(row.get("zone"), path=f"inputs[{index}].zone",
                         default="")
            supplies.append(PlannerSupply(item_type, quantity, zone))
        return cls(
            target_item_type=_item_type(payload.get("target_item_type"),
                                        path="target_item_type"),
            target_quantity=_positive_int(
                payload.get("target_quantity", 1),
                path="target_quantity"),
            inputs=tuple(supplies),
            scene_id=_text(payload.get("scene_id"), path="scene_id",
                           default=spec.LAYOUT_NAME),
            allowed_template_ids=_text_tuple(
                payload.get("allowed_template_ids", ()),
                path="allowed_template_ids"),
            allowed_device_ids=_text_tuple(
                payload.get("allowed_device_ids", ()),
                path="allowed_device_ids"),
            preferred_route_ids=_text_tuple(
                payload.get("preferred_route_ids", ()),
                path="preferred_route_ids"),
            allow_high_risk=_boolean(payload.get("allow_high_risk"),
                                     path="allow_high_risk"),
            max_candidates=_positive_int(
                payload.get("max_candidates", DEFAULT_MAX_CANDIDATES),
                path="max_candidates"),
            max_depth=_non_negative_int(
                payload.get("max_depth", DEFAULT_MAX_DEPTH),
                path="max_depth"),
            max_nodes=_positive_int(
                payload.get("max_nodes", DEFAULT_MAX_NODES),
                path="max_nodes"),
            order_id=_text(payload.get("order_id"), path="order_id",
                           default="planner-draft", pattern=_ID_PATTERN),
            order_version=_positive_int(
                payload.get("order_version", 1), path="order_version"),
            policy_version=_text(
                payload.get("policy_version"), path="policy_version",
                default=PLANNER_POLICY_VERSION),
        )


def _item_type(value, *, path: str) -> ItemType:
    text = _text(value, path=path)
    try:
        return ItemType(text)
    except ValueError as exc:
        raise PlannerInputError(f"{path}: 未知物料类型 {text!r}") from exc


def _text(value, *, path: str, default: Optional[str] = None,
          pattern: Optional[re.Pattern] = None) -> str:
    if default is not None and (value is None or value == ""):
        return default
    if not isinstance(value, str) or not value.strip():
        raise PlannerInputError(f"{path}: 必须是非空字符串")
    text = value.strip()
    if pattern is not None and not pattern.fullmatch(text):
        raise PlannerInputError(f"{path}: 标识格式非法 {text!r}")
    return text


def _text_tuple(value, *, path: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise PlannerInputError(f"{path}: 必须是字符串数组")
    rows = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise PlannerInputError(f"{path}[{index}]: 必须是非空字符串")
        rows.append(item.strip())
    return tuple(rows)


def _positive_int(value, *, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise PlannerInputError(f"{path}: 必须是正整数")
    return int(value)


def _non_negative_int(value, *, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise PlannerInputError(f"{path}: 必须是非负整数")
    return int(value)


def _boolean(value, *, path: str) -> bool:
    if value is None:
        return False
    if not isinstance(value, bool):
        raise PlannerInputError(f"{path}: 必须是布尔值")
    return bool(value)


# ═══════════════════════════════════════════════════════════════
# 诊断与候选
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class PlannerDiagnostic:
    code: str
    message: str
    category: str
    severity: str = "error"
    item_type: str = ""
    route_id: str = ""
    template_id: str = ""
    port_id: str = ""

    def as_dict(self) -> dict:
        return {
            "code": self.code, "message": self.message,
            "category": self.category, "severity": self.severity,
            "item_type": self.item_type, "route_id": self.route_id,
            "template_id": self.template_id, "port_id": self.port_id,
        }


@dataclass(frozen=True)
class _RouteMacro:
    route_id: str
    group: str
    entry_zone: str
    entry_item_type: ItemType
    exit_zone: str
    exit_item_type: ItemType
    operation_ids: tuple[str, ...]
    template_ids: tuple[str, ...]
    operation_count: int
    signature: tuple

    def as_dict(self) -> dict:
        return {
            "route_id": self.route_id, "group": self.group,
            "entry_zone": self.entry_zone,
            "entry_item_type": self.entry_item_type.value,
            "exit_zone": self.exit_zone,
            "exit_item_type": self.exit_item_type.value,
            "operation_ids": list(self.operation_ids),
            "operation_count": int(self.operation_count),
        }


@dataclass(frozen=True)
class _JoinMacro:
    template_id: str
    input_ports: tuple[tuple[str, str, ItemType], ...]
    output_zone: str
    output_item_type: ItemType
    join_handler_id: str
    signature: tuple

    def as_dict(self) -> dict:
        return {
            "template_id": self.template_id,
            "inputs": [
                {"port_id": port_id, "zone": zone,
                 "item_type": item_type.value}
                for port_id, zone, item_type in self.input_ports],
            "output_zone": self.output_zone,
            "output_item_type": self.output_item_type.value,
            "join_handler_id": self.join_handler_id,
        }


@dataclass(frozen=True)
class _Branch:
    kind: str                          # route | join
    label: str
    route_ids: tuple[str, ...]
    template_ids: tuple[str, ...]
    input_requirements: tuple[tuple[str, str, int], ...]
    output_item_type: ItemType
    output_zone: str
    operation_count: int
    signature: tuple
    children: tuple["_Branch", ...] = ()
    join_ports: tuple[tuple[str, int], ...] = ()
    join_template_id: str = ""

    @property
    def node_count(self) -> int:
        return len(self.template_ids)

    def as_dict(self) -> dict:
        return {
            "kind": self.kind, "label": self.label,
            "route_ids": list(self.route_ids),
            "template_ids": list(self.template_ids),
            "input_requirements": [
                {"item_type": item, "zone": zone, "quantity": qty}
                for item, zone, qty in self.input_requirements],
            "output_item_type": self.output_item_type.value,
            "output_zone": self.output_zone,
            "operation_count": int(self.operation_count),
        }


@dataclass(frozen=True)
class _Candidate:
    candidate_id: str
    branch: _Branch
    feasible: bool
    rejection_code: str = ""
    rejection_reason: str = ""
    order: Optional[OrderSpec] = None
    digest: str = ""
    preview: Optional[CompilePreview] = None
    input_usage: tuple[dict, ...] = ()
    score: tuple = ()

    def as_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "branch": self.branch.as_dict(),
            "feasible": bool(self.feasible),
            "rejection_code": self.rejection_code,
            "rejection_reason": self.rejection_reason,
            "operation_count": int(self.branch.operation_count),
            "route_ids": list(self.branch.route_ids),
            "template_ids": list(self.branch.template_ids),
            "digest": self.digest,
            "score": list(self.score),
            "input_usage": list(self.input_usage),
        }


@dataclass(frozen=True)
class PlannerResult:
    input: PlannerInput
    ok: bool
    diagnostics: tuple[PlannerDiagnostic, ...]
    selected: Optional[_Candidate]
    candidates: tuple[_Candidate, ...]
    rejected: tuple[_Candidate, ...]
    order: Optional[OrderSpec]
    digest: str
    preview: Optional[CompilePreview]
    explanation: dict
    search: dict
    policy_version: str = PLANNER_POLICY_VERSION

    @property
    def first_blocking_category(self) -> str:
        return _blocking_category(self.diagnostics)

    def as_dict(self) -> dict:
        return {
            "ok": bool(self.ok),
            "policy_version": self.policy_version,
            "input": self.input.as_dict(),
            "first_blocking_category": self.first_blocking_category,
            "diagnostics": [row.as_dict() for row in self.diagnostics],
            "selected": None if self.selected is None
            else self.selected.as_dict(),
            "selected_order": None if self.order is None
            else self.order.as_dict(),
            "digest": self.digest,
            "preview": None if self.preview is None
            else self.preview.as_dict(),
            "candidates": [row.as_dict() for row in self.candidates],
            "rejected": [row.as_dict() for row in self.rejected],
            "explanation": self.explanation,
            "search": self.search,
        }


# ═══════════════════════════════════════════════════════════════
# Planner
# ═══════════════════════════════════════════════════════════════

class OrderPlanner:
    """有界确定性 planner; 只读目录并调用 validator/compiler."""

    def __init__(self, catalog: ProcessCatalog) -> None:
        self.catalog = catalog
        self.compiler = OrderDagCompiler(catalog)

    def plan(self, request: PlannerInput) -> PlannerResult:
        diagnostics: list[PlannerDiagnostic] = []
        if request.policy_version != PLANNER_POLICY_VERSION:
            diagnostics.append(PlannerDiagnostic(
                "PLANNER_POLICY_VERSION",
                f"planner 策略版本 {request.policy_version!r} 与当前 "
                f"{PLANNER_POLICY_VERSION!r} 不一致", "policy"))
        if request.scene_id != spec.LAYOUT_NAME:
            diagnostics.append(PlannerDiagnostic(
                "PLANNER_SCENE_UNKNOWN",
                f"场景 {request.scene_id!r} 不是 {spec.LAYOUT_NAME!r}",
                "scene"))
        self._search = {
            "expansions": 0,
            "branches": 0,
            "truncated": False,
            "limit_reasons": [],
            "limits": {
                "max_candidates": int(request.max_candidates),
                "max_depth": int(request.max_depth),
                "max_nodes": int(request.max_nodes),
            },
        }
        routes, route_diagnostics = self._route_macros(request)
        joins, join_diagnostics = self._join_macros(request)
        self._route_cache = (request, (routes, route_diagnostics))
        self._join_cache = (request, (joins, join_diagnostics))
        diagnostics.extend(route_diagnostics)
        diagnostics.extend(join_diagnostics)
        if not routes and not joins:
            diagnostics.append(PlannerDiagnostic(
                "PLANNER_NO_ROUTE",
                "目录中没有可用的 route/join 候选", "template"))
        branches = self._producer_options(
            request.target_item_type, "", 0, frozenset(), request)
        if not branches and not any(row.category in ("target", "join")
                                    for row in diagnostics) \
                and not self._search["truncated"] \
                and not self._search.get("join_failures"):
            diagnostics.append(PlannerDiagnostic(
                "PLANNER_TARGET_UNREACHABLE",
                f"没有可生产 {request.target_item_type.value} 的路线",
                "target", item_type=request.target_item_type.value))

        candidates: list[_Candidate] = []
        blocking_diagnostics: list[PlannerDiagnostic] = []
        preference_rank = {
            route_id: index
            for index, route_id in enumerate(request.preferred_route_ids)}
        for branch in branches:
            total_nodes = int(branch.operation_count) * int(
                request.target_quantity)
            if total_nodes > request.max_nodes:
                self._mark_limit("max_nodes")
                candidates.append(_Candidate(
                    candidate_id="", branch=branch, feasible=False,
                    rejection_code="PLANNER_NODE_LIMIT",
                    rejection_reason=(
                        f"目标数量 {request.target_quantity} 需要 "
                        f"{total_nodes} 个节点, 超过 max_nodes "
                        f"{request.max_nodes}"),
                    score=(0, int(branch.operation_count),
                           "|".join(str(item) for item in branch.signature))))
                blocking_diagnostics.append(PlannerDiagnostic(
                    "PLANNER_NODE_LIMIT",
                    f"目标数量 {request.target_quantity} 需要 "
                    f"{total_nodes} 个节点, 超过 max_nodes "
                    f"{request.max_nodes}", "search_limit"))
                continue
            usage, missing = self._check_supply(branch, request)
            score = (
                min((preference_rank.get(route_id,
                                         len(preference_rank))
                     for route_id in branch.route_ids),
                    default=len(preference_rank)),
                int(branch.operation_count),
                "|".join(str(item) for item in branch.signature),
            )
            if missing:
                candidates.append(_Candidate(
                    candidate_id="", branch=branch, feasible=False,
                    rejection_code="PLANNER_INPUT_INSUFFICIENT",
                    rejection_reason="; ".join(
                        f"{row['item_type']}@{row['zone']} 缺 "
                        f"{row['missing']}" for row in missing),
                    input_usage=tuple(usage), score=score))
                blocking_diagnostics.append(PlannerDiagnostic(
                    "PLANNER_INPUT_INSUFFICIENT",
                    "投料不足: " + ", ".join(
                        f"{row['item_type']}@{row['zone']} "
                        f"需要 {row['required']} 可用 {row['available']}"
                        for row in missing),
                    "input", item_type=missing[0]["item_type"],
                    route_id=branch.route_ids[0] if branch.route_ids else ""))
                continue
            try:
                order = self._materialize(branch, request)
            except (OrderSpecError, ValueError) as exc:
                candidates.append(_Candidate(
                    candidate_id="", branch=branch, feasible=False,
                    rejection_code="PLANNER_MATERIALIZE_FAILED",
                    rejection_reason=str(exc), input_usage=tuple(usage),
                    score=score))
                blocking_diagnostics.append(PlannerDiagnostic(
                    "PLANNER_MATERIALIZE_FAILED", str(exc), "compile",
                    template_id=branch.label))
                continue
            preview = self.compiler.preview(order)
            if not preview.ok:
                candidates.append(_Candidate(
                    candidate_id="", branch=branch, feasible=False,
                    rejection_code="PLANNER_COMPILE_FAILED",
                    rejection_reason="; ".join(
                        row.message for row in preview.diagnostics[:3]),
                    order=order, preview=preview, input_usage=tuple(usage),
                    score=score))
                blocking_diagnostics.append(PlannerDiagnostic(
                    "PLANNER_COMPILE_FAILED",
                    "候选订单未通过 validator/compiler: " + "; ".join(
                        row.code for row in preview.diagnostics[:3]),
                    "compile", template_id=branch.label))
                continue
            candidates.append(_Candidate(
                candidate_id="", branch=branch, feasible=True,
                order=order, digest=order.dag_digest(), preview=preview,
                input_usage=tuple(usage), score=score))

        candidates.sort(key=lambda row: (not row.feasible, row.score))
        for index, candidate in enumerate(candidates):
            candidates[index] = _replace_candidate(
                candidate, candidate_id=f"cand-{index:03d}")
        feasible = [row for row in candidates if row.feasible]
        rejected = [row for row in candidates if not row.feasible]
        selected = feasible[0] if feasible else None
        fatal = any(row.code in ("PLANNER_POLICY_VERSION",
                                 "PLANNER_SCENE_UNKNOWN")
                    for row in diagnostics)
        if fatal:
            selected = None
        if selected is None and self._search["truncated"]:
            reasons = ", ".join(self._search.get("limit_reasons") or [])
            diagnostics.append(PlannerDiagnostic(
                "PLANNER_SEARCH_LIMIT",
                f"搜索达到上限 {reasons}; 未生成完整候选", "search_limit"))
        if selected is None:
            for row in self._search.get("join_failures") or []:
                diagnostics.append(PlannerDiagnostic(
                    "PLANNER_JOIN_INPUT_UNREACHABLE",
                    f"合流 {row['template_id']!r} 端口 {row['port_id']!r} "
                    f"没有可生产 {row['item_type']}@{row['zone']} 的路线",
                    "join", template_id=row["template_id"],
                    port_id=row["port_id"], item_type=row["item_type"]))
            diagnostics.extend(blocking_diagnostics)
        if selected is None and not any(
                row.severity == "error" for row in diagnostics):
            diagnostics.append(PlannerDiagnostic(
                "PLANNER_NO_CANDIDATE", "没有可行候选", "target"))
        ok = selected is not None
        if selected is None:
            first = self._first_blocking(diagnostics)
            if first is not None and first.code == "PLANNER_NO_CANDIDATE":
                diagnostics.append(PlannerDiagnostic(
                    "PLANNER_NO_SOLUTION", "没有满足约束的候选",
                    first.category))
        search = dict(self._search)
        search["candidates"] = len(candidates)
        search["feasible"] = len(feasible)
        search["rejected"] = len(rejected)
        explanation = self._explain(
            request, selected, candidates, search, diagnostics)
        return PlannerResult(
            input=request, ok=ok, diagnostics=tuple(diagnostics),
            selected=selected, candidates=tuple(feasible),
            rejected=tuple(rejected),
            order=None if selected is None else selected.order,
            digest="" if selected is None else selected.digest,
            preview=None if selected is None else selected.preview,
            explanation=explanation, search=search)

    # ── 搜索空间 ────────────────────────────────────────────

    def _route_macros(self, request: PlannerInput
                      ) -> tuple[list[_RouteMacro], list[PlannerDiagnostic]]:
        diagnostics: list[PlannerDiagnostic] = []
        allowed_templates = set(request.allowed_template_ids)
        allowed_devices = set(request.allowed_device_ids)
        routes: list[_RouteMacro] = []
        for recipe in self.catalog.recipes:
            for route in recipe.route_variants:
                if not route.operation_ids:
                    continue
                try:
                    templates = [self.catalog.operation(
                        operation_id, recipe.id)
                        for operation_id in route.operation_ids]
                except Exception:
                    continue
                if any(not template.orchestratable for template in templates):
                    continue
                if any(template.scene_id
                       and template.scene_id != request.scene_id
                       for template in templates):
                    continue
                if allowed_templates and any(
                        template.id not in allowed_templates
                        for template in templates):
                    diagnostics.append(PlannerDiagnostic(
                        "PLANNER_TEMPLATE_EXCLUDED",
                        f"路线 {route.id!r} 使用被排除模板",
                        "template", severity="warning",
                        route_id=route.id))
                    continue
                if allowed_devices and any(
                        not set(template.candidate_devices) & allowed_devices
                        for template in templates):
                    diagnostics.append(PlannerDiagnostic(
                        "PLANNER_DEVICE_EXCLUDED",
                        f"路线 {route.id!r} 没有允许的候选设备",
                        "device", severity="warning", route_id=route.id))
                    continue
                entry_ports = [
                    port for port in templates[0].inputs
                    if port.zone == route.entry_zone]
                if len(entry_ports) != 1:
                    diagnostics.append(PlannerDiagnostic(
                        "PLANNER_ROUTE_UNSUPPORTED",
                        f"路线 {route.id!r} 入口区域 "
                        f"{route.entry_zone!r} 不是唯一输入端口",
                        "template", severity="warning",
                        route_id=route.id))
                    continue
                last = templates[-1]
                if last.output is None or \
                        last.output.zone != route.exit_zone:
                    diagnostics.append(PlannerDiagnostic(
                        "PLANNER_ROUTE_UNSUPPORTED",
                        f"路线 {route.id!r} 出口与末工序输出不一致",
                        "template", severity="warning",
                        route_id=route.id))
                    continue
                if not self._route_continuous(templates):
                    diagnostics.append(PlannerDiagnostic(
                        "PLANNER_ROUTE_UNSUPPORTED",
                        f"路线 {route.id!r} 工序端口不连续",
                        "template", severity="warning",
                        route_id=route.id))
                    continue
                routes.append(_RouteMacro(
                    route_id=route.id, group=route.group,
                    entry_zone=route.entry_zone,
                    entry_item_type=entry_ports[0].item_type,
                    exit_zone=route.exit_zone,
                    exit_item_type=last.output.item_type,
                    operation_ids=tuple(route.operation_ids),
                    template_ids=tuple(row.id for row in templates),
                    operation_count=len(templates),
                    signature=("route", route.id,
                               tuple(route.operation_ids))))
        routes.sort(key=lambda row: (
            int(row.operation_count), row.route_id, row.signature))
        return routes, diagnostics

    @staticmethod
    def _route_continuous(templates: list[OperationTemplate]) -> bool:
        for previous, current in zip(templates, templates[1:]):
            output = previous.output
            if output is None:
                return False
            if not any(port.zone == output.zone
                       and port.item_type == output.item_type
                       for port in current.inputs):
                return False
        return True

    def _join_macros(self, request: PlannerInput
                     ) -> tuple[list[_JoinMacro], list[PlannerDiagnostic]]:
        diagnostics: list[PlannerDiagnostic] = []
        allowed_templates = set(request.allowed_template_ids)
        allowed_devices = set(request.allowed_device_ids)
        joins: list[_JoinMacro] = []
        for recipe in self.catalog.recipes:
            for template in recipe.operations:
                if template.executor_kind != "logic" \
                        or not template.orchestratable:
                    continue
                if template.join_handler_id not in JOIN_HANDLERS:
                    continue
                if template.join_handler_id not in RUNTIME_JOIN_HANDLERS:
                    diagnostics.append(PlannerDiagnostic(
                        "PLANNER_JOIN_HANDLER_UNSUPPORTED",
                        f"合流 {template.id!r} handler "
                        f"{template.join_handler_id!r} 没有运行时实现",
                        "join", severity="warning",
                        template_id=template.id))
                    continue
                if template.output is None or len(template.inputs) < 2:
                    continue
                if template.scene_id and \
                        template.scene_id != request.scene_id:
                    continue
                if allowed_templates and template.id not in allowed_templates:
                    diagnostics.append(PlannerDiagnostic(
                        "PLANNER_TEMPLATE_EXCLUDED",
                        f"合流 {template.id!r} 被允许模板范围排除",
                        "template", severity="warning",
                        template_id=template.id))
                    continue
                if allowed_devices and not (
                        set(template.candidate_devices) & allowed_devices):
                    diagnostics.append(PlannerDiagnostic(
                        "PLANNER_DEVICE_EXCLUDED",
                        f"合流 {template.id!r} 没有允许的设备",
                        "device", severity="warning",
                        template_id=template.id))
                    continue
                ports = tuple(
                    (port_id, port.zone, port.item_type)
                    for port_id, port in zip(template.input_port_ids,
                                             template.inputs))
                joins.append(_JoinMacro(
                    template_id=template.id, input_ports=ports,
                    output_zone=template.output.zone,
                    output_item_type=template.output.item_type,
                    join_handler_id=template.join_handler_id,
                    signature=("join", template.id, ports)))
        joins.sort(key=lambda row: row.template_id)
        return joins, diagnostics

    # ── 反向生产候选 ────────────────────────────────────────

    def _producer_options(self, item_type: ItemType, zone: str, depth: int,
                          visiting: frozenset[str],
                          request: PlannerInput) -> list[_Branch]:
        self._search["expansions"] += 1
        if depth > request.max_depth:
            self._mark_limit("max_depth")
            return []
        if item_type.value in visiting:
            return []
        next_visiting = frozenset(set(visiting) | {item_type.value})
        routes, _ = self._route_macros_cached(request)
        joins, _ = self._join_macros_cached(request)
        branches: list[_Branch] = []
        for route in routes:
            if route.exit_item_type != item_type:
                continue
            if zone and route.exit_zone != zone:
                continue
            if route.operation_count > request.max_nodes:
                self._mark_limit("max_nodes")
                continue
            branches.append(_Branch(
                kind="route", label=route.route_id,
                route_ids=(route.route_id,),
                template_ids=route.template_ids,
                input_requirements=((route.entry_item_type.value,
                                     route.entry_zone, 1),),
                output_item_type=route.exit_item_type,
                output_zone=route.exit_zone,
                operation_count=route.operation_count,
                signature=route.signature))
        for join in joins:
            if join.output_item_type != item_type:
                continue
            if zone and join.output_zone != zone:
                continue
            option_lists = []
            failed = False
            for port_id, port_zone, port_type in join.input_ports:
                options = self._producer_options(
                    port_type, port_zone, depth + 1, next_visiting, request)
                if not options:
                    failures = self._search.setdefault("join_failures", [])
                    row = {
                        "template_id": join.template_id,
                        "port_id": port_id, "zone": port_zone,
                        "item_type": port_type.value,
                    }
                    if row not in failures:
                        failures.append(row)
                    failed = True
                    break
                option_lists.append(options)
            if failed:
                continue
            combinations = 1
            for options in option_lists:
                combinations *= len(options)
            if combinations > request.max_candidates:
                self._mark_limit("max_candidates")
            for combination in _bounded_product(
                    option_lists, request.max_candidates):
                children = tuple(combination)
                node_count = 1 + sum(child.node_count for child in children)
                if node_count > request.max_nodes:
                    self._mark_limit("max_nodes")
                    continue
                requirements = _merge_requirements(
                    requirement
                    for child in children
                    for requirement in child.input_requirements)
                route_ids = tuple(
                    route_id for child in children
                    for route_id in child.route_ids)
                template_ids = (join.template_id,) + tuple(
                    template_id for child in children
                    for template_id in child.template_ids)
                branches.append(_Branch(
                    kind="join", label=join.template_id,
                    route_ids=route_ids,
                    template_ids=template_ids,
                    input_requirements=requirements,
                    output_item_type=join.output_item_type,
                    output_zone=join.output_zone,
                    operation_count=1 + sum(
                        child.operation_count for child in children),
                    signature=("join", join.template_id,
                               tuple(child.signature for child in children)),
                    children=children,
                    join_ports=tuple(
                        (port_id, index)
                        for index, (port_id, _zone, _type)
                        in enumerate(join.input_ports)),
                    join_template_id=join.template_id))
        unique: dict[tuple, _Branch] = {}
        for branch in branches:
            unique.setdefault(branch.signature, branch)
        ordered = sorted(unique.values(), key=lambda row: (
            int(row.operation_count), row.signature))
        self._search["branches"] += len(ordered)
        if len(ordered) > request.max_candidates:
            self._mark_limit("max_candidates")
            ordered = ordered[:request.max_candidates]
        return ordered

    def _mark_limit(self, reason: str) -> None:
        self._search["truncated"] = True
        reasons = self._search.setdefault("limit_reasons", [])
        if reason not in reasons:
            reasons.append(reason)

    def _route_macros_cached(self, request: PlannerInput):
        cached = getattr(self, "_route_cache", None)
        if cached is None or cached[0] is not request:
            cached = (request, self._route_macros(request))
            self._route_cache = cached
        return cached[1]

    def _join_macros_cached(self, request: PlannerInput):
        cached = getattr(self, "_join_cache", None)
        if cached is None or cached[0] is not request:
            cached = (request, self._join_macros(request))
            self._join_cache = cached
        return cached[1]

    # ── 投料与物化 ──────────────────────────────────────────

    def _check_supply(self, branch: _Branch, request: PlannerInput
                      ) -> tuple[list[dict], list[dict]]:
        supplies = [
            [row.item_type.value, row.zone, int(row.quantity)]
            for row in sorted(
                request.inputs,
                key=lambda item: (item.item_type.value, item.zone,
                                  int(item.quantity)))]
        usage: list[dict] = []
        missing: list[dict] = []
        for item_type, zone, quantity in sorted(
                branch.input_requirements,
                key=lambda row: (row[0], row[1])):
            required = int(quantity) * int(request.target_quantity)
            remaining = required
            available = 0
            for supply in supplies:
                if supply[0] != item_type:
                    continue
                if supply[1] and supply[1] != zone:
                    continue
                available += max(0, int(supply[2]))
                take = min(remaining, max(0, int(supply[2])))
                supply[2] = int(supply[2]) - take
                remaining -= take
                if remaining <= 0:
                    break
            usage.append({
                "item_type": item_type, "zone": zone,
                "required": required, "available": available,
            })
            if remaining > 0:
                missing.append({
                    "item_type": item_type, "zone": zone,
                    "required": required, "available": required - remaining,
                    "missing": remaining,
                })
        return usage, missing

    def _materialize(self, branch: _Branch,
                     request: PlannerInput) -> OrderSpec:
        nodes: list[OrderNode] = []
        edges: list[OrderEdge] = []
        inputs: list[OrderInput] = []
        serial = 0
        input_serial = 0

        def next_node(template_id: str) -> str:
            nonlocal serial
            node_id = f"{template_id}-{serial:02d}"
            if len(node_id) > 64:
                node_id = f"n{serial:03d}"
            serial += 1
            nodes.append(OrderNode(node_id, template_id))
            return node_id

        def next_input(item_type: ItemType, zone: str, node_id: str,
                       port_id: str) -> None:
            nonlocal input_serial
            inputs.append(OrderInput(
                input_id=f"in-{input_serial:03d}", item_type=item_type,
                quantity=1, zone=zone, node_id=node_id, port_id=port_id))
            input_serial += 1

        def visit(current: _Branch) -> str:
            if current.kind == "route":
                template_ids = current.template_ids
                previous_node = ""
                previous_template: Optional[OperationTemplate] = None
                entry_type, entry_zone, _entry_qty = \
                    current.input_requirements[0]
                for index, template_id in enumerate(template_ids):
                    node_id = next_node(template_id)
                    template = self.catalog.operation(template_id)
                    if index == 0:
                        port_id = _matching_entry_port_id(
                            template, current.input_requirements)
                        next_input(ItemType(entry_type), entry_zone,
                                   node_id, port_id)
                    else:
                        assert previous_template is not None
                        port_id = _matching_port_id(previous_template,
                                                    template)
                        edges.append(OrderEdge(
                            previous_node, node_id, port_id))
                    previous_node = node_id
                    previous_template = template
                return previous_node
            # join
            child_terminals = []
            for child in current.children:
                child_terminals.append(visit(child))
            node_id = next_node(current.join_template_id)
            for index, (port_id, _child_index) in enumerate(
                    current.join_ports):
                edges.append(OrderEdge(
                    child_terminals[index], node_id, port_id))
            return node_id

        for _unit in range(int(request.target_quantity)):
            visit(branch)
        return OrderSpec(
            order_id=request.order_id, version=int(request.order_version),
            scene_id=request.scene_id,
            target=OrderTarget(request.target_item_type,
                               int(request.target_quantity)),
            inputs=tuple(inputs), nodes=tuple(nodes), edges=tuple(edges),
            source="planner")

    # ── 解释 ────────────────────────────────────────────────

    def _explain(self, request: PlannerInput, selected: Optional[_Candidate],
                 candidates: Iterable[_Candidate], search: dict,
                 diagnostics: list[PlannerDiagnostic]) -> dict:
        join_bindings = []
        if selected is not None:
            join_bindings = _join_bindings(selected.branch)
        return {
            "policy_version": PLANNER_POLICY_VERSION,
            "input_summary": request.as_dict(),
            "selected_candidate_id": None if selected is None
            else selected.candidate_id,
            "selected_branch": None if selected is None
            else selected.branch.as_dict(),
            "selected_order": None if selected is None
            or selected.order is None else selected.order.as_dict(),
            "digest": "" if selected is None else selected.digest,
            "target_paths": [] if selected is None or selected.preview is None
            else [row for row in selected.preview.as_dict()[
                "expected_outputs"]],
            "join_bindings": join_bindings,
            "candidates": [row.as_dict() for row in candidates],
            "rejected": [row.as_dict() for row in candidates
                         if not row.feasible],
            "search": search,
            "unmodeled_dimensions": list(_UNMODELED_DIMENSIONS),
            "first_blocking_category": _blocking_category(diagnostics),
        }

    @staticmethod
    def _first_blocking(diagnostics: list[PlannerDiagnostic]
                        ) -> Optional[PlannerDiagnostic]:
        errors = [row for row in diagnostics if row.severity == "error"]
        if not errors:
            return None
        return min(errors, key=lambda row: _CATEGORY_ORDER.get(
            row.category, 99))


def _blocking_category(diagnostics: Iterable[PlannerDiagnostic]) -> str:
    errors = [row for row in diagnostics if row.severity == "error"]
    if not errors:
        return ""
    if any(row.code == "PLANNER_SEARCH_LIMIT" for row in errors):
        return "search_limit"
    return min(errors, key=lambda row: _CATEGORY_ORDER.get(
        row.category, 99)).category


def _merge_requirements(rows: Iterable[tuple[str, str, int]]
                        ) -> tuple[tuple[str, str, int], ...]:
    totals: dict[tuple[str, str], int] = {}
    for item_type, zone, quantity in rows:
        totals[(item_type, zone)] = totals.get((item_type, zone), 0) + int(
            quantity)
    return tuple((item, zone, quantity)
                 for (item, zone), quantity in sorted(totals.items()))


def _bounded_product(options: list[list[_Branch]], limit: int):
    count = 0
    for combination in product(*options):
        yield combination
        count += 1
        if count >= limit:
            return


def _replace_candidate(candidate: _Candidate,
                       **changes) -> _Candidate:
    return replace(candidate, **changes)


def _matching_entry_port_id(template: OperationTemplate,
                            requirements: tuple[tuple[str, str, int], ...]
                            ) -> str:
    """路线首工序中与 entry zone/item type 匹配的端口 ID."""
    for port_id, port in zip(template.input_port_ids, template.inputs):
        if port.zone == requirements[0][1] \
                and port.item_type.value == requirements[0][0]:
            return port_id
    return template.input_port_ids[0]


def _matching_port_id(previous: OperationTemplate,
                      current: OperationTemplate) -> str:
    output = previous.output
    if output is not None:
        for port_id, port in zip(current.input_port_ids, current.inputs):
            if port.zone == output.zone \
                    and port.item_type == output.item_type:
                return port_id
    return current.input_port_ids[0]


def _join_bindings(branch: _Branch) -> list[dict]:
    bindings: list[dict] = []
    if branch.kind == "join":
        ports = []
        for index, (port_id, child_index) in enumerate(branch.join_ports):
            child = branch.children[child_index]
            ports.append({
                "port_id": port_id,
                "item_type": child.output_item_type.value,
                "route_ids": list(child.route_ids),
                "template_ids": list(child.template_ids),
            })
        bindings.append({
            "join_template_id": branch.join_template_id,
            "inputs": ports,
        })
        for child in branch.children:
            bindings.extend(_join_bindings(child))
    return bindings


__all__ = [
    "OrderPlanner", "PLANNER_POLICY_VERSION", "PlannerDiagnostic",
    "PlannerError", "PlannerInput", "PlannerInputError", "PlannerResult",
    "PlannerSupply",
]
