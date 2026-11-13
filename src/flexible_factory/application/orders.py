"""orders.py — 订单服务、运行 worker 与状态查询 (阶段 3).

服务边界:

  - 手工订单和 planner 订单共用 :class:`OrderSpec`、validator、compiler;
  - 订单版本在启动前持久化为不可变版本; 运行只绑定显式版本 + 工件;
  - HTTP/CLI 入口只调用本服务, 不持有 sim/scene/controller/handle;
  - 仿真 worker 独占 stepping; API 通过命令队列启动/停止并读取状态快照;
  - 终态写入在 worker 完成时批量执行, 不在 ``sim.step()`` 热路径写库.

本模块不导入 CoppeliaSim; 真实 runner 由
:mod:`flexible_factory.application.order_runner` 延迟装配.
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Callable, Optional

from .. import spec
from ..domain.default_process import default_catalog
from ..domain.failures import NODE_FAILURE_ALLOWLIST
from ..domain.order_compiler import CompiledOrder, CompilePreview, \
    OrderDagCompiler
from ..domain.orders import (ORDER_CONTRACT_VERSION, OrderSpec,
                             OrderSpecError)
from ..domain.process_model import ProcessCatalog
from ..persistence.db import CatalogDatabase
from ..persistence.errors import (OrderNotFoundError, OrderRunConflictError,
                                  OrderRunNotFoundError, OrderStateError)
from ..persistence.orders import OrderRepository

LOGGER = logging.getLogger("flexible_factory.orders")

TERMINAL_ORDER_STATUSES = ("SUCCEEDED", "PARTIAL_FAILED", "FAILED",
                           "ABORTED")


@dataclass(frozen=True)
class OrderRunCommand:
    """投递给仿真 worker 的不可变启动命令."""

    order: OrderSpec
    compiled: CompiledOrder
    run_id: str
    artifact_id: str
    max_sim_time: float = 1200.0
    progress: Optional[Callable[[dict], None]] = field(
        default=None, compare=False, repr=False)

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "order_id": self.order.order_id,
            "order_version": int(self.order.version),
            "dag_digest": self.compiled.digest,
            "artifact_id": self.artifact_id,
            "max_sim_time": float(self.max_sim_time),
        }


@dataclass
class _WorkerState:
    run_id: str
    command: OrderRunCommand
    status: str = "STARTING"
    started_at: str = ""
    finished_at: str = ""
    error: str = ""
    summary: Optional[dict] = None
    progress_data: dict = field(default_factory=dict)
    stop_event: threading.Event = field(default_factory=threading.Event)
    finished_event: threading.Event = field(default_factory=threading.Event)
    thread: Optional[threading.Thread] = None


Runner = Callable[[OrderRunCommand, threading.Event], dict]


class OrderSimulationWorker:
    """单实例仿真 worker; 同一时刻只接受一个订单运行."""

    def __init__(self, runner: Runner, *,
                 on_finish: Optional[Callable[[str, Optional[dict], str],
                                              None]] = None,
                 logger: Optional[logging.Logger] = None) -> None:
        if not callable(runner):
            raise TypeError("runner 必须可调用")
        self.runner = runner
        self.on_finish = on_finish
        self.logger = logger or LOGGER
        self._lock = threading.RLock()
        self._state: Optional[_WorkerState] = None

    @property
    def active_run_id(self) -> str:
        with self._lock:
            if self._state is None or self._state.finished_event.is_set():
                return ""
            return self._state.run_id

    def start(self, command: OrderRunCommand) -> dict:
        with self._lock:
            if self._state is not None \
                    and not self._state.finished_event.is_set():
                raise OrderRunConflictError(
                    f"worker 已有活动订单运行 {self._state.run_id!r}")
            state = _WorkerState(
                run_id=command.run_id, command=command,
                status="STARTING", started_at=_utc_now())

            def _progress(payload: dict) -> None:
                with self._lock:
                    state.progress_data = dict(payload)

            state.command = replace(command, progress=_progress)
            state.thread = threading.Thread(
                target=self._loop, args=(state,),
                name=f"order-worker-{command.run_id}", daemon=True)
            self._state = state
            state.thread.start()
            return self._snapshot(state)

    def _loop(self, state: _WorkerState) -> None:
        summary: Optional[dict] = None
        error = ""
        with self._lock:
            state.status = "RUNNING"
        try:
            summary = self.runner(state.command, state.stop_event)
            status = _status_from_summary(summary, "")
        except Exception as exc:      # runner 必须把失败收敛为终态
            error = f"{type(exc).__name__}: {exc}"
            status = "FAILED"
            self.logger.exception("[ORDER] run %s 异常: %s",
                                  state.run_id, error)
        finally:
            with self._lock:
                state.summary = summary
                state.error = error
                state.status = _status_from_summary(summary, error)
                state.finished_at = _utc_now()
                state.finished_event.set()
            if self.on_finish is not None:
                try:
                    self.on_finish(state.run_id, summary, error)
                except Exception:
                    self.logger.exception(
                        "[ORDER] run %s 终态持久化失败", state.run_id)

    def stop(self, run_id: str) -> dict:
        with self._lock:
            state = self._require_live(run_id)
            state.stop_event.set()
            state.status = "RUNNING"
            return {"run_id": run_id, "stop_requested": True}

    def status(self, run_id: str) -> Optional[dict]:
        with self._lock:
            if self._state is None or self._state.run_id != str(run_id):
                return None
            return self._snapshot(self._state)

    def wait(self, run_id: str, timeout: Optional[float] = None) -> dict:
        with self._lock:
            state = self._state
            if state is None or state.run_id != str(run_id):
                raise OrderRunNotFoundError(
                    f"worker 没有运行 {run_id!r}")
        state.finished_event.wait(timeout)
        with self._lock:
            return self._snapshot(state)

    def shutdown(self, timeout: float = 5.0) -> None:
        with self._lock:
            state = self._state
        if state is None:
            return
        if not state.finished_event.is_set():
            state.stop_event.set()
            if not state.finished_event.wait(timeout):
                with self._lock:
                    if not state.finished_event.is_set():
                        state.status = "ABORTED"
                        state.error = "WORKER_SHUTDOWN_TIMEOUT"
                        state.finished_at = _utc_now()
                        state.finished_event.set()
                if self.on_finish is not None:
                    try:
                        self.on_finish(state.run_id, None,
                                       "WORKER_SHUTDOWN_TIMEOUT")
                    except Exception:
                        self.logger.exception(
                            "[ORDER] run %s 关闭终态写入失败", state.run_id)

    def _require_live(self, run_id: str) -> _WorkerState:
        state = self._state
        if state is None or state.run_id != str(run_id):
            raise OrderRunNotFoundError(
                f"worker 没有运行 {run_id!r}")
        if state.finished_event.is_set():
            raise OrderStateError(
                f"订单运行 {run_id!r} 已结束, 不能停止")
        return state

    def _snapshot(self, state: _WorkerState) -> dict:
        raw_nodes = state.progress_data.get("nodes")
        if raw_nodes:
            nodes = _summary_nodes({
                "operations": {"graph": {"instances": raw_nodes}},
                "failures": list(state.progress_data.get("failures") or []),
            })
        else:
            nodes = _summary_nodes(state.summary)
        events = state.progress_data.get("events") or _summary_events(
            state.summary)
        finished = state.finished_event.is_set()
        return {
            "run_id": state.run_id,
            "status": (state.status if finished else
                       state.progress_data.get("status") or state.status),
            "order_status": ("" if finished else
                             state.progress_data.get("order_status", "")),
            "sim_time": state.progress_data.get("sim_time"),
            "failures": list(state.progress_data.get("failures") or []),
            "started_at": state.started_at,
            "finished_at": state.finished_at,
            "error": state.error,
            "stop_requested": state.stop_event.is_set(),
            "summary": state.summary,
            "nodes": nodes,
            "events": events,
        }


# ═══════════════════════════════════════════════════════════════
# 应用服务
# ═══════════════════════════════════════════════════════════════

class OrderService:
    """订单定义、预览、版本化与运行生命周期的唯一应用服务."""

    def __init__(self, database: CatalogDatabase, *,
                 catalog: Optional[ProcessCatalog] = None,
                 artifact_id: str = "",
                 worker: Optional[OrderSimulationWorker] = None,
                 runner: Optional[Runner] = None,
                 artifact_resolver: Optional[Callable[[str], str]] = None,
                 logger: Optional[logging.Logger] = None) -> None:
        self.database = database
        self.catalog = catalog if catalog is not None else default_catalog()
        self.artifact_id = str(artifact_id or "")
        self.artifact_resolver = artifact_resolver
        self.repository = OrderRepository(database)
        self.compiler = OrderDagCompiler(self.catalog)
        # planner 延迟装配, 避免 order service 的导入路径依赖阶段 4 模块.
        self._planner = None
        self.logger = logger or LOGGER
        if worker is None and runner is not None:
            worker = OrderSimulationWorker(
                runner, on_finish=self._persist_finish,
                logger=self.logger)
        if worker is not None and isinstance(worker, OrderSimulationWorker) \
                and worker.on_finish is None:
            worker.on_finish = self._persist_finish
        self.worker = worker

    # ── 目录 ────────────────────────────────────────────────

    def templates(self) -> dict:
        rows = []
        for template in self.catalog.orchestratable_operations():
            rows.append({
                "template_id": template.id,
                "kind": template.kind,
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
                "work_steps": [step.template_id
                               for step in template.work_steps],
                "completion_verifier": template.completion_verifier,
                "join_handler_id": template.join_handler_id,
                "predecessor_policy": template.predecessor_policy,
                "scene_id": template.scene_id,
            })
        return {
            "catalog_version": self.catalog.version,
            "contract_version": ORDER_CONTRACT_VERSION,
            "scene_id": spec.LAYOUT_NAME,
            "templates": rows,
            "routes": [
                {
                    "route_id": route.id,
                    "group": route.group,
                    "semantics": route.semantics,
                    "operation_ids": list(route.operation_ids),
                    "entry_zone": route.entry_zone,
                    "exit_zone": route.exit_zone,
                }
                for recipe in self.catalog.recipes
                for route in recipe.route_variants
            ],
        }

    # ── 校验/预览 ───────────────────────────────────────────

    def parse(self, payload: dict) -> OrderSpec:
        return OrderSpec.from_dict(payload)

    def preview(self, payload: dict) -> CompilePreview:
        return self.compiler.preview(self.parse(payload))

    def preview_spec(self, order: OrderSpec) -> CompilePreview:
        return self.compiler.preview(order)

    # ── 自主编排 (阶段 4) ───────────────────────────────────

    def plan(self, payload: dict):
        from .planner import OrderPlanner, PlannerInput
        if self._planner is None:
            self._planner = OrderPlanner(self.catalog)
        return self._planner.plan(PlannerInput.from_dict(payload))

    def plan_and_save(self, payload: dict, *, note: str = "") -> dict:
        """生成 planner 草稿并保存为普通不可变订单版本."""
        result = self.plan(payload)
        if not result.ok or result.order is None:
            return {"ok": False, "plan": result.as_dict(), "order": None}
        planner_meta = {
            "planner_policy_version": result.policy_version,
            "planner_input": result.input.as_dict(),
            "planner_explain": result.explanation,
            "planner_candidate_count": (len(result.candidates)
                                        + len(result.rejected)),
        }
        order = result.order
        try:
            existing = self.repository.get_order(order.order_id)
        except OrderNotFoundError:
            existing = None
        if existing is None:
            order = replace(order, version=1)
            saved = self.repository.create_order(
                order, source="planner", note=note,
                catalog_version=self.catalog.version, **planner_meta)
        else:
            current = int(existing["current_version"])
            order = replace(order, version=current + 1)
            saved = self.repository.create_version(
                order, expected_version=current, note=note,
                catalog_version=self.catalog.version, **planner_meta)
        return {
            "ok": True,
            "plan": result.as_dict(),
            "order": saved,
            "saved_order": {
                "order_id": order.order_id,
                "version": int(order.version),
                "dag_digest": order.dag_digest(),
                "plan_digest": result.digest,
            },
        }

    # ── 订单版本 ────────────────────────────────────────────

    def create_order(self, payload: dict, *, source: str = "manual",
                     note: str = "") -> dict:
        order = self.parse(payload)
        if order.version != 1:
            order = replace(order, version=1)
        return self.repository.create_order(
            order, source=source, note=note,
            catalog_version=self.catalog.version)

    def create_version(self, order_id: str, payload: dict, *,
                       note: str = "") -> dict:
        current = self.repository.get_order(order_id)["current_version"]
        order = self.parse(payload)
        if order.order_id != order_id:
            raise OrderSpecError(
                f"请求路径 order_id={order_id!r} 与 spec "
                f"{order.order_id!r} 不一致", path="order_id")
        if order.version != int(current) + 1:
            order = replace(order, version=int(current) + 1)
        return self.repository.create_version(
            order, expected_version=int(current), note=note,
            catalog_version=self.catalog.version)

    def get_order(self, order_id: str) -> dict:
        return self.repository.get_order(order_id)

    def list_orders(self, *, limit: int = 50) -> list[dict]:
        return self.repository.list_orders(limit=limit)

    def get_version(self, order_id: str, version: int) -> dict:
        return self.repository.get_version(order_id, version)

    def list_versions(self, order_id: str) -> list[dict]:
        return self.repository.list_versions(order_id)

    # ── 运行 ────────────────────────────────────────────────

    def _resolve_artifact(self, artifact_id: str) -> str:
        reference = str(artifact_id or self.artifact_id or "").strip()
        if not reference:
            raise OrderStateError(
                "启动订单运行必须绑定显式 artifact_id")
        if self.artifact_resolver is not None:
            try:
                resolved = str(self.artifact_resolver(reference) or "").strip()
            except Exception as exc:
                raise OrderStateError(
                    f"工件引用 {reference!r} 无法解析: "
                    f"{type(exc).__name__}: {exc}") from exc
            if not resolved:
                raise OrderStateError(
                    f"工件引用 {reference!r} 无法解析为具体 artifact")
            return resolved
        if reference == "current":
            raise OrderStateError(
                "没有工件解析器时不能使用可变 'current' 引用; "
                "请传入具体 artifact_id")
        return reference

    def _load_version_for_run(self, order_id: str, version: int) -> OrderSpec:
        meta = self.repository.get_version(order_id, version)
        stored = str(meta.get("catalog_version") or "")
        if stored and stored != self.catalog.version:
            raise OrderStateError(
                f"订单版本 {order_id}@{version} 使用目录 {stored!r}, "
                f"当前目录 {self.catalog.version!r}; 拒绝用不同目录启动")
        return self.repository.get_spec(order_id, version)

    def start(self, order_id: str, version: int, *,
              artifact_id: str = "", max_sim_time: float = 1200.0) -> dict:
        if self.worker is None:
            raise OrderStateError("订单 worker 未配置")
        artifact_id = self._resolve_artifact(artifact_id)
        order = self._load_version_for_run(order_id, version)
        run_id = _new_run_id(order_id)
        compiled = self.compiler.compile(order, run_id=run_id)
        self.repository.create_run(
            run_id=run_id, order_id=order.order_id,
            version=int(order.version), dag_digest=compiled.digest,
            artifact_id=artifact_id, status="STARTING")
        command = OrderRunCommand(
            order=order, compiled=compiled, run_id=run_id,
            artifact_id=artifact_id, max_sim_time=float(max_sim_time))
        try:
            self.worker.start(command)
        except Exception as exc:
            self.repository.update_run(
                run_id, status="ABORTED",
                stop_reason=f"WORKER_REJECTED: {exc}")
            raise
        try:
            self.repository.update_run(
                run_id, status="RUNNING", stop_reason="")
        except OrderRunConflictError:
            # worker 可能在 start 返回前已完成并写入终态.
            pass
        return self.repository.get_run(run_id)

    def run_sync(self, order_id: str, version: int, runner: Runner, *,
                 artifact_id: str = "",
                 max_sim_time: float = 1200.0) -> dict:
        """CLI/维护入口: 在当前进程同步执行一次订单运行."""
        artifact_id = self._resolve_artifact(artifact_id)
        order = self._load_version_for_run(order_id, version)
        run_id = _new_run_id(order_id)
        compiled = self.compiler.compile(order, run_id=run_id)
        self.repository.create_run(
            run_id=run_id, order_id=order.order_id,
            version=int(order.version), dag_digest=compiled.digest,
            artifact_id=artifact_id, status="STARTING")
        command = OrderRunCommand(
            order=order, compiled=compiled, run_id=run_id,
            artifact_id=artifact_id, max_sim_time=float(max_sim_time))
        summary: Optional[dict] = None
        error = ""
        try:
            summary = runner(command, threading.Event())
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        self._persist_finish(run_id, summary, error)
        return self.repository.get_run(run_id)

    def status(self, run_id: str) -> dict:
        persisted = self.repository.get_run(run_id)
        live = self.worker.status(run_id) if self.worker is not None else None
        if live is not None and not live.get("finished_at"):
            persisted = dict(persisted)
            persisted["status"] = live.get("status") or persisted["status"]
            persisted["live"] = live
            if live.get("nodes"):
                persisted["nodes"] = live["nodes"]
                persisted["node_count"] = len(live["nodes"])
            if live.get("events"):
                persisted["events"] = live["events"]
        else:
            persisted = dict(persisted)
            persisted["live"] = live
            if live is not None and live.get("finished_at"):
                if persisted["status"] not in TERMINAL_ORDER_STATUSES \
                        and live.get("status") in TERMINAL_ORDER_STATUSES:
                    persisted["status"] = live["status"]
                if live.get("nodes"):
                    persisted["nodes"] = live["nodes"]
                    persisted["node_count"] = len(live["nodes"])
        return persisted

    def nodes(self, run_id: str) -> list[dict]:
        live = self.worker.status(run_id) if self.worker is not None else None
        if live is not None and live.get("nodes"):
            return live["nodes"]
        return self.repository.run_nodes(run_id)

    def events(self, run_id: str, *, limit: int = 2000) -> list[dict]:
        live = self.worker.status(run_id) if self.worker is not None else None
        if live is not None and live.get("events") \
                and not live.get("finished_at"):
            return list(live["events"])[:max(1, int(limit))]
        return self.repository.run_events(run_id, limit=limit)

    def list_runs(self, *, order_id: str = "", limit: int = 50) -> list[dict]:
        return self.repository.list_runs(order_id=order_id, limit=limit)

    def stop(self, run_id: str) -> dict:
        live = self.worker.status(run_id) if self.worker is not None else None
        if live is not None and not live.get("finished_at"):
            return self.worker.stop(run_id)
        persisted = self.repository.get_run(run_id)
        effective = (live.get("status")
                     if live is not None and live.get("finished_at")
                     else persisted["status"])
        if effective in TERMINAL_ORDER_STATUSES:
            raise OrderStateError(
                f"订单运行 {run_id!r} 已终态 {effective}")
        self.repository.update_run(
            run_id, status="ABORTED",
            stop_reason="NO_ACTIVE_WORKER")
        return self.repository.get_run(run_id)

    # ── 终态持久化 ──────────────────────────────────────────

    def _persist_finish(self, run_id: str, summary: Optional[dict],
                        error: str) -> None:
        status = _status_from_summary(summary, error)
        failure_scope = _failure_scope(summary)
        stop_reason = _stop_reason(summary, error)
        nodes = _summary_nodes(summary)
        try:
            self.repository.update_run(
                run_id, status=status, failure_scope=failure_scope,
                stop_reason=stop_reason,
                result_json=_compact_result(summary, status, error))
            if nodes:
                self.repository.record_nodes(run_id, nodes)
        except Exception:
            self.logger.exception(
                "[ORDER] run %s 终态写入失败", run_id)


# ═══════════════════════════════════════════════════════════════
# 摘要投影
# ═══════════════════════════════════════════════════════════════

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_run_id(order_id: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_"
                   for ch in str(order_id))[:32]
    return f"order_{safe}_{stamp}_{uuid.uuid4().hex[:6]}"


def _status_from_summary(summary: Optional[dict], error: str) -> str:
    if error:
        return "FAILED"
    if not summary:
        return "FAILED"
    if summary.get("aborted"):
        return "ABORTED"
    acceptance = summary.get("acceptance") or {}
    order_status = str(acceptance.get("order_status")
                       or summary.get("order_status") or "")
    if order_status in TERMINAL_ORDER_STATUSES:
        return order_status
    if order_status == "RUNNING":
        return "FAILED"
    if acceptance.get("ok"):
        return "SUCCEEDED"
    return "FAILED"


def _failure_scope(summary: Optional[dict]) -> str:
    if not summary:
        return ""
    scopes = {str(row.get("scope") or "")
              for row in summary.get("failures") or []}
    if "GLOBAL" in scopes:
        return "GLOBAL"
    if "NODE" in scopes:
        return "NODE"
    return ""


def _stop_reason(summary: Optional[dict], error: str) -> str:
    if error:
        return error
    if not summary:
        return "NO_SUMMARY"
    return str(summary.get("stop_reason") or "")


def _compact_result(summary: Optional[dict], status: str,
                    error: str) -> Optional[dict]:
    if not summary:
        return {"status": status, "error": error} if error else None
    keys = ("scenario", "fault", "sim_time", "aborted", "frozen",
            "stop_reason", "order_status", "batch", "metrics",
            "active_unit_ids", "runtime_destroy", "failure_summary",
            "acceptance")
    compact = {key: summary[key] for key in keys if key in summary}
    compact["status"] = status
    if error:
        compact["error"] = error
    compact["failures"] = list(summary.get("failures") or [])
    order_plan = summary.get("order_plan") or {}
    if order_plan:
        compact["order_run_id"] = order_plan.get("run_id")
        compact["dag_digest"] = order_plan.get("digest")
    return compact


def _summary_nodes(summary: Optional[dict]) -> list[dict]:
    if not summary:
        return []
    rows = summary.get("order_nodes")
    if rows is None:
        rows = ((summary.get("operations") or {}).get("graph") or {}).get(
            "instances", [])
    failure_scope: dict[str, str] = {}
    for row in summary.get("failures") or []:
        instance_id = str(row.get("instance_id") or "")
        if instance_id:
            failure_scope[instance_id] = str(row.get("scope") or "")
    nodes = []
    for row in rows:
        if not row.get("node_id"):
            continue
        instance_id = str(row.get("instance_id") or "")
        state = str(row.get("state") or "")
        scope = failure_scope.get(instance_id, "")
        if not scope and state in ("BLOCKED", "CANCELLED") \
                and row.get("blocked_by"):
            scope = "NODE"
        if not scope and state == "FAILED" \
                and row.get("failure_code") in NODE_FAILURE_ALLOWLIST:
            scope = "NODE"
        nodes.append({
            "instance_id": instance_id,
            "node_id": str(row.get("node_id") or ""),
            "template_id": str(row.get("template_id") or ""),
            "uid": int(row.get("uid") or 0),
            "state": state,
            "failure_scope": scope,
            "failure_code": str(row.get("failure_code") or ""),
            "failure_reason": str(row.get("failure_reason") or ""),
            "blocked_by": list(row.get("blocked_by") or []),
            "input_uids": [int(value)
                           for value in row.get("input_uids") or []],
            "output_uids": [int(value)
                            for value in row.get("output_uids") or []],
            "evidence": row.get("evidence") or {},
        })
    return nodes


def _summary_events(summary: Optional[dict]) -> list[dict]:
    if not summary:
        return []
    return list(summary.get("events") or [])


__all__ = [
    "OrderRunCommand", "OrderService", "OrderSimulationWorker", "Runner",
    "TERMINAL_ORDER_STATUSES",
]
