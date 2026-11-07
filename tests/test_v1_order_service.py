"""阶段 3: 订单服务、worker 生命周期与本地 HTTP API (纯逻辑)."""

from __future__ import annotations

import json
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from flexible_factory.api.server import OrderApiServer
from flexible_factory.application.orders import OrderService
from flexible_factory.domain.default_process import default_catalog
from flexible_factory.domain.orders import OrderCompileError
from flexible_factory.domain.process_model import ProcessCatalog
from flexible_factory.persistence.db import (CatalogDatabase,
                                              initialize_database)
from flexible_factory.persistence.errors import (OrderRunConflictError,
                                                  OrderStateError)
from order_fixtures import full_flow_order


def _success_summary(command) -> dict:
    instances = []
    for row in command.compiled.instances:
        item = row.as_dict()
        item["state"] = "DONE"
        item["committed_sim_time"] = 1.0
        item["output_uids"] = [int(row.uid)]
        instances.append(item)
    return {
        "run_id": command.run_id,
        "order": command.compiled.context.as_dict(),
        "order_status": "SUCCEEDED",
        "acceptance": {"ok": True, "order_status": "SUCCEEDED",
                       "order": command.compiled.context.as_dict()},
        "operations": {"graph": {"instances": instances}},
        "events": [{"kind": "RUN_START", "sim_time": 0.0},
                   {"kind": "RUN_END", "sim_time": 2.0}],
        "failures": [],
        "failure_summary": {"failed": [], "blocked": [], "continued": []},
        "stop_reason": "",
        "sim_time": 2.0,
    }


def _failing_summary(command) -> dict:
    summary = _success_summary(command)
    summary["order_status"] = "FAILED"
    summary["acceptance"] = {"ok": False, "order_status": "FAILED",
                             "order": command.compiled.context.as_dict()}
    first = summary["operations"]["graph"]["instances"][0]
    first["state"] = "FAILED"
    first["failure_code"] = "NODE_PRECHECK_REJECTED"
    first["failure_reason"] = "not ready"
    summary["failures"] = [{
        "failure_code": "NODE_PRECHECK_REJECTED", "scope": "NODE",
        "instance_id": first["instance_id"],
        "node_id": first["node_id"], "message": "not ready",
    }]
    return summary


@pytest.fixture()
def database(tmp_path) -> CatalogDatabase:
    db = CatalogDatabase(tmp_path / "orders.db")
    initialize_database(db)
    return db


def _service(database, runner) -> OrderService:
    return OrderService(database, artifact_id="artifact-test", runner=runner)


def test_service_runs_order_and_persists_versions_and_nodes(database):
    service = _service(database, lambda command, stop: _success_summary(command))
    created = service.create_order(full_flow_order().as_dict())
    assert created["current_version"] == 1

    started = service.start("order-full-flow-001", 1,
                            artifact_id="artifact-test")
    run_id = started["run_id"]
    service.worker.wait(run_id, timeout=5)

    status = service.status(run_id)
    assert status["status"] == "SUCCEEDED"
    assert status["order_id"] == "order-full-flow-001"
    assert status["order_version"] == 1
    assert status["artifact_id"] == "artifact-test"
    assert status["node_count"] == len(status["nodes"])
    assert status["dag_digest"] == full_flow_order().dag_digest()
    nodes = service.nodes(run_id)
    assert {row["node_id"] for row in nodes} == {
        "housing-start", "housing-finish", "housing-buffer",
        "bracket-start", "bracket-transport", "bracket-buffer", "kit-join"}
    assert all(row["state"] == "DONE" for row in nodes)
    assert service.list_runs(order_id="order-full-flow-001")[0]["run_id"] == \
        run_id


def test_service_rejects_invalid_order_before_start(database):
    service = _service(database, lambda command, stop: _success_summary(command))
    payload = full_flow_order().as_dict()
    payload["nodes"][0]["template_id"] = "no_such_template"
    service.create_order(payload)
    with pytest.raises(OrderCompileError):
        service.start("order-full-flow-001", 1,
                      artifact_id="artifact-test")
    assert service.list_runs(order_id="order-full-flow-001") == []


def test_service_rejects_catalog_version_mismatch(database):
    base = default_catalog()
    other_catalog = ProcessCatalog(
        recipes=base.recipes, capabilities=base.capabilities,
        devices=base.devices, zones=base.zones,
        known_resources=base.known_resources, version="v1_other_catalog")
    writer = OrderService(
        database, catalog=other_catalog, artifact_id="artifact-test",
        runner=lambda command, stop: _success_summary(command))
    writer.create_order(full_flow_order().as_dict())
    reader = _service(
        database, lambda command, stop: _success_summary(command))
    with pytest.raises(OrderStateError, match="目录"):
        reader.start("order-full-flow-001", 1,
                     artifact_id="artifact-test")


def test_service_conflict_and_stop_lifecycle(database):
    release = threading.Event()

    def blocking(command, stop_event):
        if command.progress is not None:
            command.progress({
                "status": "RUNNING", "order_status": "RUNNING",
                "sim_time": 0.0,
                "nodes": [row.as_dict()
                          for row in command.compiled.instances],
                "events": [{"kind": "TASK_START", "sim_time": 0.0}],
            })
        stop_event.wait(5)
        summary = _success_summary(command)
        if stop_event.is_set():
            summary["order_status"] = "ABORTED"
            summary["aborted"] = True
            summary["acceptance"] = {
                "ok": False, "order_status": "ABORTED",
                "order": command.compiled.context.as_dict()}
        release.set()
        return summary

    service = _service(database, blocking)
    service.create_order(full_flow_order().as_dict())
    first = service.start("order-full-flow-001", 1,
                          artifact_id="artifact-test")
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        live = service.status(first["run_id"])
        if live.get("live") and live["live"].get("nodes"):
            break
        time.sleep(0.01)
    assert live["live"]["status"] == "RUNNING"
    assert live["live"]["nodes"]
    assert service.events(first["run_id"])
    with pytest.raises(OrderRunConflictError):
        service.start("order-full-flow-001", 1,
                      artifact_id="artifact-test")
    stopped = service.stop(first["run_id"])
    assert stopped["stop_requested"] is True
    service.worker.wait(first["run_id"], timeout=5)
    status = service.status(first["run_id"])
    assert status["status"] == "ABORTED"
    with pytest.raises(OrderStateError):
        service.stop(first["run_id"])


def test_service_persists_node_scoped_failure(database):
    service = _service(database, lambda command, stop: _failing_summary(command))
    service.create_order(full_flow_order().as_dict())
    run = service.start("order-full-flow-001", 1,
                        artifact_id="artifact-test")
    service.worker.wait(run["run_id"], timeout=5)
    status = service.status(run["run_id"])
    assert status["status"] == "FAILED"
    assert status["failure_scope"] == "NODE"
    failed = [row for row in service.nodes(run["run_id"])
              if row["state"] == "FAILED"]
    assert failed and failed[0]["failure_scope"] == "NODE"
    assert failed[0]["failure_code"] == "NODE_PRECHECK_REJECTED"


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


def _wait_status(base: str, run_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, payload = _request(base, "GET", f"/api/runs/{run_id}")
        assert status == 200, payload
        if payload["run"]["status"] in (
                "SUCCEEDED", "PARTIAL_FAILED", "FAILED", "ABORTED"):
            return payload["run"]
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} 未在 {timeout}s 内收敛")


def test_http_api_contract_and_run_query(database):
    service = _service(database, lambda command, stop: _success_summary(command))
    server = OrderApiServer(service, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, payload = _request(server.base_url, "GET", "/api/health")
        assert status == 200 and payload["ok"] is True
        assert payload["contract_version"] == "v1_order_20261106"

        status, payload = _request(server.base_url, "GET", "/api/templates")
        assert status == 200
        assert any(row["template_id"] == "kit_join"
                   for row in payload["templates"])

        status, payload = _request(
            server.base_url, "POST", "/api/orders/preview",
            full_flow_order().as_dict())
        assert status == 200 and payload["preview"]["ok"] is True

        status, payload = _request(
            server.base_url, "POST", "/api/orders",
            full_flow_order().as_dict())
        assert status == 200, payload
        assert payload["order"]["current_version"] == 1

        status, payload = _request(
            server.base_url, "POST",
            "/api/orders/order-full-flow-001/versions/1/preview")
        assert status == 200 and payload["preview"]["ok"] is True
        assert payload["preview"]["digest"] == full_flow_order().dag_digest()

        status, payload = _request(
            server.base_url, "POST",
            "/api/orders/order-full-flow-001/versions/1/start",
            {"artifact_id": "artifact-test"})
        assert status == 200, payload
        run_id = payload["run"]["run_id"]
        run = _wait_status(server.base_url, run_id)
        assert run["status"] == "SUCCEEDED"

        status, payload = _request(
            server.base_url, "GET", f"/api/runs/{run_id}/nodes")
        assert status == 200 and payload["nodes"]
        status, payload = _request(
            server.base_url, "GET", f"/api/runs/{run_id}/events")
        assert status == 200

        bad = full_flow_order(order_id="bad-order").as_dict()
        bad["nodes"][0]["template_id"] = "no_such_template"
        status, payload = _request(server.base_url, "POST",
                                   "/api/orders", bad)
        assert status == 200  # 创建只做格式校验, 编译在预览/启动时
        status, payload = _request(
            server.base_url, "POST",
            "/api/orders/order-full-flow-001/versions/2/preview")
        assert status == 404

        status, payload = _request(
            server.base_url, "GET", "/api/orders/no-such-order")
        assert status == 404 and payload["error"]["code"] == \
            "ORDER_NOT_FOUND"

        status, payload = _request(
            server.base_url, "POST", "/api/orders",
            {"order_id": "bad", "nodes": []})
        assert status == 422
    finally:
        server.shutdown()
        if service.worker is not None:
            service.worker.shutdown()
