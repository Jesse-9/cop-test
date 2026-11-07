"""阶段 3: 订单版本不可变性、digest 校验与运行关联 (纯持久化)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from flexible_factory.domain.operation_runtime import (OperationInstance,
                                                       TaskStatus)
from flexible_factory.persistence.db import (CatalogDatabase,
                                              initialize_database)
from flexible_factory.persistence.errors import (OrderIntegrityError,
                                                  OrderVersionConflictError)
from flexible_factory.persistence.orders import OrderRepository
from flexible_factory.persistence.runs import (RunRecorder, operation_timeline,
                                                run_summary)
from order_fixtures import full_flow_order


@pytest.fixture()
def repository(tmp_path) -> OrderRepository:
    database = CatalogDatabase(tmp_path / "orders.db")
    initialize_database(database)
    return OrderRepository(database)


def test_order_and_versions_are_append_only(repository):
    order = full_flow_order()
    created = repository.create_order(order)
    assert created["current_version"] == 1
    assert created["versions"][0]["dag_digest"] == order.dag_digest()

    second = full_flow_order(version=2, raw_a=2, raw_d=2,
                             target_quantity=2)
    updated = repository.create_version(second, expected_version=1)
    assert updated["current_version"] == 2
    assert [row["version"] for row in updated["versions"]] == [1, 2]
    assert repository.get_version(order.order_id, 1)["dag_digest"] == \
        order.dag_digest()
    assert repository.get_version(order.order_id, 2)["dag_digest"] == \
        second.dag_digest()

    with pytest.raises(OrderVersionConflictError):
        repository.create_order(order)
    with pytest.raises(OrderVersionConflictError):
        repository.create_version(full_flow_order(version=4))


def test_order_version_update_is_rejected_by_database_trigger(repository):
    repository.create_order(full_flow_order())
    with repository.database.connect() as connection:
        with pytest.raises(IntegrityError, match="immutable"):
            connection.execute(text(
                "UPDATE order_version SET spec_json = '{}' "
                "WHERE order_id = 'order-full-flow-001' AND version = 1"))


def test_stored_spec_digest_is_verified_on_read(repository):
    repository.create_order(full_flow_order())
    # 直接插入一条 digest 错误的版本行; 读取必须拒绝而不是返回可用订单.
    with repository.database.session() as session:
        session.execute(text(
            "INSERT INTO order_version (order_id, version, contract_version, "
            "scene_id, source, dag_digest, spec_json, catalog_version, "
            "parent_version, created_at, note) VALUES "
            "('order-full-flow-001', 3, 'v1_order_20261106', 'L5_UFLOW', "
            "'fixture', :digest, :spec, NULL, 2, '2026-11-06', NULL)"),
            {"digest": "0" * 64,
             "spec": json.dumps(full_flow_order(version=3).as_dict())})
        session.commit()
    with pytest.raises(OrderIntegrityError, match="digest"):
        repository.get_version("order-full-flow-001", 3)


def test_order_run_linkage_and_node_snapshots(repository):
    order = full_flow_order()
    repository.create_order(order)
    digest = order.dag_digest()
    run = repository.create_run(
        run_id="run_order_1", order_id=order.order_id, version=1,
        dag_digest=digest, artifact_id="v1/L5_UFLOW/v001")
    assert run["status"] == "STARTING"
    repository.update_run("run_order_1", status="RUNNING")
    repository.record_nodes("run_order_1", [{
        "instance_id": "run_order_1:housing-start:1",
        "node_id": "housing-start", "template_id": "housing_direct_A",
        "uid": 1, "state": "FAILED",
        "failure_scope": "NODE", "failure_code": "NODE_PRECHECK_REJECTED",
        "failure_reason": "not ready", "blocked_by": [],
        "input_uids": [1], "output_uids": [], "evidence": {"a": 1},
    }])
    finished = repository.update_run(
        "run_order_1", status="FAILED", failure_scope="NODE",
        stop_reason="not ready")
    assert finished["finished_at"]
    assert finished["node_count"] == 1
    assert finished["failed_count"] == 1
    assert finished["nodes"][0]["failure_code"] == "NODE_PRECHECK_REJECTED"
    assert repository.list_runs(order_id=order.order_id)[0]["run_id"] == \
        "run_order_1"


def test_run_creation_requires_matching_digest(repository):
    order = full_flow_order()
    repository.create_order(order)
    with pytest.raises(OrderIntegrityError):
        repository.create_run(
            run_id="run_bad", order_id=order.order_id, version=1,
            dag_digest="f" * 64, artifact_id="artifact")


def test_run_record_maps_cancelled_state_without_schema_break(repository):
    """旧 operation_instance CHECK 不含 CANCELLED; 写库映射为 BLOCKED."""
    instance = OperationInstance(
        instance_id="run_cancel:kit-join:1+2",
        task_id="kit_join_uid3_kit-join",
        template_id="kit_join", kind="logical_join", uid=3,
        source="buffer_e_c", destination="output_e", run_id="run_cancel",
        node_id="kit-join", order_id="order-full-flow-001",
        order_version=1, state=TaskStatus.CANCELLED,
        phase="CANCELLED: 上游失败", selected_route="")
    coordinator = SimpleNamespace(
        op_runtime=SimpleNamespace(instances=lambda: [instance]),
        flow=SimpleNamespace(all=lambda: [], events=[], join_history=[]),
        resources=SimpleNamespace(events=[]),
        events=[],
        catalog=SimpleNamespace(version="v1_process_20261106a"),
        recipe=SimpleNamespace(join_operation_id="kit_join"))
    recorder = RunRecorder(repository.database, run_id="run_cancel",
                           scenario="order")
    assert recorder.start(sim_time=0.0)["ok"]
    assert recorder.finish(
        coordinator, {"acceptance": {"ok": False}, "sim_time": 1.0},
        status="FAILED")["ok"]
    summary = run_summary(repository.database, "run_cancel")
    assert summary["counts"]["operation_states"] == {"BLOCKED": 1}
    timeline = operation_timeline(repository.database, run_id="run_cancel")
    evidence = timeline["instances"][0]["evidence"]
    assert evidence["cancelled_state"] == "CANCELLED"
    assert timeline["instances"][0]["state"] == "BLOCKED"
