"""阶段 6: 自建订单 DAG 的 CoppeliaSim 集成验收 (需要真实仿真).

    FLEXIBLE_FACTORY_SIM=1 python -m pytest -q \
        tests/test_v1_order_sim.py -s

未启用时 skip, 报告中记"未执行", 不得按通过报告. 本文件只覆盖阶段 6
需要真实运动/物理/生命周期补证的部分:

  - 手工订单 service → runner → coordinator → executor 全链路;
  - planner 订单复用同一 compiler/service/runner;
  - 物理动作前 NODE_PRECHECK_REJECTED 只阻断依赖后继, 独立支路继续;
  - 真实安全锁存进入 GLOBAL freeze/ABORT, 保留锁存并释放普通资源.

NODE 用例只对 ``NODE_PRECHECK_REJECTED`` 的受控前置拒绝准入; 被替换的
arm_A 执行器不会启动真实运动. 真实 Arm/AGV 运动错误仍为 GLOBAL.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from flexible_factory import spec
from flexible_factory.application.orders import OrderService
from flexible_factory.application.order_runner import (OrderRunnerConfig,
                                                       make_order_runner)
from flexible_factory.application.coordinator import Stage4Coordinator
from flexible_factory.domain.default_process import default_catalog
from flexible_factory.domain.materials import ItemType
from flexible_factory.domain.order_compiler import OrderDagCompiler
from flexible_factory.domain.orders import (OrderEdge, OrderInput, OrderNode,
                                            OrderSpec, OrderTarget)
from flexible_factory.persistence.db import (CatalogDatabase,
                                              initialize_database)
from flexible_factory.simulation.execution import (ExecutionReport,
                                                   ExecutionState)
from stage2_harness import (drive, open_context, require_sim, write_evidence)

ARTIFACT_ID = ""


def _artifact_id() -> str:
    global ARTIFACT_ID
    if not ARTIFACT_ID:
        current = json.loads(
            (Path(spec.ARTIFACT_ROOT) / "v1" / "current.json").read_text(
                encoding="utf-8"))
        ARTIFACT_ID = str(current["artifact_id"])
    return ARTIFACT_ID


def _revision() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True,
            encoding="utf-8").strip()
    except Exception:
        return ""


def _database(tmp_path: Path) -> CatalogDatabase:
    database = CatalogDatabase(tmp_path / "order_acceptance.db")
    initialize_database(database)
    return database


def _runner_config(database: CatalogDatabase, tmp_path: Path
                   ) -> OrderRunnerConfig:
    return OrderRunnerConfig(
        host="localhost", port=23000,
        artifact_root=None,
        record_db=database.path,
        run_records=True,
        log=str(tmp_path / "order_sim.log"),
        out_dir=tmp_path / "reports")


def _write_evidence(name: str, *, order_id: str, version: int,
                    run: dict, extra: dict | None = None) -> str:
    payload = {
        "case": name,
        "source_revision": _revision(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "coppelia": {"version": "4.10", "zmq": "127.0.0.1:23000"},
        "artifact_id": _artifact_id(),
        "contract_version": "v1_order_20261106",
        "catalog_version": default_catalog().version,
        "planner_policy_version": spec.ORDER_PLANNER_POLICY_VERSION,
        "order_id": order_id,
        "order_version": int(version),
        "run": run,
        "extra": extra or {},
    }
    path = write_evidence(name, payload, prefix="v1_order_")
    print(f"[stage6] {name} 证据: {path}")
    return path


def _assert_success(run: dict, *, target_quantity: int = 1) -> None:
    assert run.get("status") == "SUCCEEDED", run
    result = run.get("result") or {}
    acceptance = result.get("acceptance") or {}
    assert acceptance.get("ok") is True, acceptance
    assert acceptance.get("order_status") == "SUCCEEDED", acceptance
    assert acceptance.get("order_target_count") == target_quantity, acceptance
    assert acceptance.get("final_count") == target_quantity, acceptance
    assert len(acceptance.get("final_parents") or []) == target_quantity
    assert acceptance.get("resources_released") is True, acceptance
    assert acceptance.get("any_safety_latch") is False, acceptance
    nodes = run.get("nodes") or []
    assert nodes, "运行记录缺少订单节点快照"
    assert all(node["state"] == "DONE" for node in nodes), [
        (node["node_id"], node["state"]) for node in nodes]
    assert any(node["node_id"].endswith("kit-join") for node in nodes)
    assert run.get("failure_scope") in ("", None)


def test_manual_order_service_run_succeeds(tmp_path):
    require_sim()
    database = _database(tmp_path)
    service = OrderService(database)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    order = _full_flow_order(order_id=f"accept-manual-{stamp}")
    service.create_order(order.as_dict(), source="manual")
    preview = service.preview_spec(service.repository.get_spec(
        order.order_id, 1))
    assert preview.ok, preview.diagnostics
    run = service.run_sync(
        order.order_id, 1,
        make_order_runner(_runner_config(database, tmp_path),
                          catalog=service.catalog),
        artifact_id=_artifact_id(), max_sim_time=1200)
    _write_evidence("manual_order", order_id=order.order_id, version=1,
                    run=run, extra={"preview_digest": preview.digest,
                                    "database": str(database.path)})
    _assert_success(run)
    stored = service.get_version(order.order_id, 1)
    assert stored["source"] == "manual"
    assert stored["dag_digest"] == preview.digest


def test_planner_order_service_run_succeeds(tmp_path):
    require_sim()
    database = _database(tmp_path)
    service = OrderService(database)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    payload = {
        "target_item_type": "FINAL",
        "target_quantity": 1,
        "inputs": [
            {"item_type": "RAW_A", "quantity": 1},
            {"item_type": "RAW_D", "quantity": 1},
        ],
        "order_id": f"accept-planner-{stamp}",
        "order_version": 1,
    }
    saved = service.plan_and_save(payload)
    assert saved["ok"], saved
    order_id = saved["saved_order"]["order_id"]
    version = saved["saved_order"]["version"]
    assert saved["saved_order"]["plan_digest"] == \
        saved["saved_order"]["dag_digest"]
    run = service.run_sync(
        order_id, version,
        make_order_runner(_runner_config(database, tmp_path),
                          catalog=service.catalog),
        artifact_id=_artifact_id(), max_sim_time=1200)
    _write_evidence("planner_order", order_id=order_id, version=version,
                    run=run, extra={
                        "planner_policy_version":
                            saved["plan"]["policy_version"],
                        "plan_digest": saved["saved_order"]["plan_digest"],
                        "database": str(database.path),
                    })
    _assert_success(run)
    stored = service.get_version(order_id, version)
    assert stored["source"] == "planner"
    assert stored["planner_policy_version"] == \
        spec.ORDER_PLANNER_POLICY_VERSION
    assert stored["dag_digest"] == saved["saved_order"]["dag_digest"]


class _PrecheckFailExecutor:
    """只在 arm_A 物理动作前拒绝; 不创建控制器, 不产生运动."""

    kind = "arm"
    device_id = "arm_A"

    def __init__(self) -> None:
        self.uid: int | None = None
        self._controller = SimpleNamespace(
            state=SimpleNamespace(name="FAILED"),
            source="conveyor", sink="station", dock_id="",
            summary=lambda: {"injected": "NODE_PRECHECK_REJECTED"})

    @property
    def controller(self):
        return self._controller

    def start(self, request) -> str:
        self.uid = request.uid
        return "arm_A#precheck"

    def pre_tick(self, sim_time: float) -> None:
        pass

    def post_tick(self, sim_time: float) -> None:
        pass

    def poll(self, execution_id: str) -> ExecutionReport:
        return ExecutionReport(
            execution_id=execution_id, device_id=self.device_id,
            operation_id="", state=ExecutionState.FAILED, finished=True,
            failure_reason="NODE_PRECHECK_REJECTED: acceptance injection",
            failure_code="NODE_PRECHECK_REJECTED",
            detail={"phase": "FAILED",
                    "physical_action_started": False},
            evidence={"injected": "NODE_PRECHECK_REJECTED"})

    def release(self, reason: str = "") -> None:
        pass


def test_node_precheck_rejects_only_dependent_branch():
    require_sim()
    catalog = default_catalog()
    order = _full_flow_order(order_id="accept-node-precheck")
    compiled = OrderDagCompiler(catalog).compile(
        order, run_id="sim-node-precheck")
    with open_context() as ctx:
        coordinator = Stage4Coordinator(
            ctx.sim, ctx.scene, ctx.comp, scenario="full_flow",
            logger=ctx.logger, order_plan=compiled)
        coordinator.arm_executors["arm_A"] = _PrecheckFailExecutor()
        coordinator.start(ctx.sim.getSimulationTime())
        result = drive(coordinator, max_wall_s=1800.0)
        summary = coordinator.summary()
        _write_evidence(
            "node_precheck", order_id=order.order_id, version=1,
            run={"summary": summary, "drive": result},
            extra={
                "failures": coordinator.failures,
                "order_status": coordinator.order_status(),
                "instances": [
                    {"node_id": row.node_id, "state": row.state.value,
                     "blocked_by": list(row.blocked_by)}
                    for row in coordinator.op_runtime.instances()],
            })

        assert result["finished"], result
        assert not coordinator.frozen
        assert not coordinator.aborted
        assert coordinator.failures
        assert coordinator.failures[0]["scope"] == "NODE"
        assert coordinator.failures[0]["node_id"] == "housing-start"
        safe = coordinator.failures[0]["safe_state"]
        assert safe["physical_action_started"] is False
        assert safe["material_zone"] == "line_s1a"
        assert safe["ledger_unchanged"] is True
        assert safe["resources_released"] is True
        material = coordinator.flow.by_uid(1)
        assert material is not None
        assert material.zone == "line_s1a"
        assert material.item_type is ItemType.RAW_A
        assert material.state.value in ("CREATED", "READY")
        instances = {row.node_id: row
                     for row in coordinator.op_runtime.instances()}
        assert instances["housing-start"].state.value == "FAILED"
        assert instances["housing-finish"].state.value in (
            "BLOCKED", "CANCELLED")
        assert instances["housing-buffer"].state.value in (
            "BLOCKED", "CANCELLED")
        assert instances["kit-join"].state.value in (
            "BLOCKED", "CANCELLED")
        assert instances["bracket-start"].state.value == "DONE"
        assert instances["bracket-transport"].state.value == "DONE"
        assert instances["bracket-buffer"].state.value == "DONE"
        assert coordinator.order_status() == "FAILED"
        assert all(owner is None
                   for owner in coordinator.resources.owners.values())
        assert not coordinator.controllers


def test_global_safety_latch_aborts_order():
    require_sim()
    catalog = default_catalog()
    order = _short_order(order_id="accept-global-safety")
    compiled = OrderDagCompiler(catalog).compile(
        order, run_id="sim-global-safety")
    with open_context() as ctx:
        coordinator = Stage4Coordinator(
            ctx.sim, ctx.scene, ctx.comp, scenario="full_flow",
            logger=ctx.logger, order_plan=compiled)
        coordinator.start(ctx.sim.getSimulationTime())
        injected = False
        steps = 0
        while not coordinator.finished:
            if not injected and "A" in coordinator.controllers and steps >= 2:
                coordinator.controllers["A"].controller._safety_fail(
                    "ACCEPTANCE_INJECTED_SAFETY")
                injected = True
            coordinator.step()
            steps += 1
            if steps > 5000:
                break
        summary = coordinator.summary()
        latch = ctx.comp["runtime"].arms["A"].safety.latch.as_dict()
        _write_evidence(
            "global_safety", order_id=order.order_id, version=1,
            run={"summary": summary, "steps": steps},
            extra={"safety_latch": latch,
                   "order_status": coordinator.order_status(),
                   "injected": injected,
                   "instances": [
                       {"node_id": row.node_id, "state": row.state.value}
                       for row in coordinator.op_runtime.instances()]})

        assert injected
        assert coordinator.aborted and coordinator.frozen
        assert coordinator.order_status() == "ABORTED"
        assert coordinator.acceptance()["order_status"] == "ABORTED"
        assert coordinator.acceptance()["any_safety_latch"] is True
        assert coordinator.acceptance()["resources_released"] is True
        assert latch["engaged"] is True
        assert coordinator.failures
        assert any(row["scope"] == "GLOBAL" for row in coordinator.failures)
        assert all(owner is None
                   for owner in coordinator.resources.owners.values())
        assert not coordinator.controllers
        assert all(row.state.value != "DONE"
                   for row in coordinator.op_runtime.instances())


def _full_flow_order(order_id: str) -> OrderSpec:
    return OrderSpec(
        order_id=order_id, version=1, scene_id="L5_UFLOW",
        target=OrderTarget(ItemType.FINAL, 1),
        inputs=(
            OrderInput("raw-a-1", ItemType.RAW_A, 1, "line_s1a",
                       "housing-start", "input-0"),
            OrderInput("raw-d-1", ItemType.RAW_D, 1, "line_s2d",
                       "bracket-start", "input-0"),
        ),
        nodes=(
            OrderNode("housing-start", "housing_direct_A"),
            OrderNode("housing-finish", "housing_finish"),
            OrderNode("housing-buffer", "housing_to_E_buffer"),
            OrderNode("bracket-start", "bracket_process"),
            OrderNode("bracket-transport", "transport_DE"),
            OrderNode("bracket-buffer", "bracket_to_E"),
            OrderNode("kit-join", "kit_join"),
        ),
        edges=(
            OrderEdge("housing-start", "housing-finish", "input-0"),
            OrderEdge("housing-finish", "housing-buffer", "input-0"),
            OrderEdge("bracket-start", "bracket-transport", "input-0"),
            OrderEdge("bracket-transport", "bracket-buffer", "input-0"),
            OrderEdge("housing-buffer", "kit-join", "housing-input"),
            OrderEdge("bracket-buffer", "kit-join", "bracket-input"),
        ),
        source="manual")


def _short_order(order_id: str) -> OrderSpec:
    return OrderSpec(
        order_id=order_id, version=1, scene_id="L5_UFLOW",
        target=OrderTarget(ItemType.SEMI_CE, 1),
        inputs=(OrderInput("raw-a-1", ItemType.RAW_A, 1, "line_s1a",
                           "housing-start", "input-0"),),
        nodes=(
            OrderNode("housing-start", "housing_direct_A"),
            OrderNode("housing-finish", "housing_finish"),
            OrderNode("housing-buffer", "housing_to_E_buffer"),
        ),
        edges=(
            OrderEdge("housing-start", "housing-finish", "input-0"),
            OrderEdge("housing-finish", "housing-buffer", "input-0"),
        ),
        source="manual")
