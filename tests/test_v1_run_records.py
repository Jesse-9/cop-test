"""v1 运行记录: 批量写入、Q1–Q4 查询、失败语义与 CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from flexible_factory.application.report import (report_genealogy,
                                                 report_run, report_timeline,
                                                 report_waits)
from flexible_factory.cli import build_parser, main
from flexible_factory.domain.default_process import (DEFAULT_RECIPE_ID,
                                                     default_catalog)
from flexible_factory.domain.materials import ItemType, MaterialRegistry
from flexible_factory.domain.operation_runtime import (OperationInstance,
                                                       OperationRuntime,
                                                       TaskStatus)
from flexible_factory.domain.resources import ResourceTable
from flexible_factory.persistence.db import CatalogDatabase
from flexible_factory.persistence.runs import RunRecorder


class _FakeOpRuntime:
    def __init__(self, instances):
        self._instances = list(instances)

    def instances(self):
        return list(self._instances)


class _FakeCoordinator:
    def __init__(self, instances, registry, resources, events):
        self.catalog = default_catalog()
        self.recipe = self.catalog.recipe(DEFAULT_RECIPE_ID)
        self.op_runtime = _FakeOpRuntime(instances)
        self.flow = registry
        self.resources = resources
        self.events = list(events)


def _summary(*, ok: bool = True, aborted: bool = False,
             stop_reason: str = "") -> dict:
    return {
        "scenario": "full_flow",
        "fault": "none",
        "sim_time": 10.0,
        "aborted": aborted,
        "frozen": not ok,
        "stop_reason": stop_reason,
        "batch": {"1": 1},
        "source_counts": {"s1a": 1, "s2d": 1},
        "metrics": {"throughput_per_h": 360.0},
        "acceptance": {"ok": ok, "checks": []},
        "process_model": {"version": "v1_process_20261103b"},
    }


def _instance(catalog, *, instance_id: str, template_id: str, uid: int,
              task_id: str, created: float, started: float,
              committed: float, inputs: tuple[int, ...],
              outputs: tuple[int, ...],
              device: str = "arm_A") -> OperationInstance:
    template = catalog.operation(template_id)
    return OperationInstance(
        instance_id=instance_id, template_id=template_id, kind=template.kind,
        uid=uid, source="", destination="", device=device,
        task_id=task_id, run_id="recipe_run_1", recipe_id=DEFAULT_RECIPE_ID,
        recipe_version=catalog.recipe(DEFAULT_RECIPE_ID).version,
        entry_id="s1a", selected_route="housing_direct",
        input_uids=list(inputs), output_uids=list(outputs),
        capability=template.capability, selected_device_id=device,
        state=TaskStatus.DONE, phase="DONE", priority=1, attempts=1,
        sequence=1, created_sim_time=created, started_sim_time=started,
        finished_sim_time=committed, committed_sim_time=committed)


def _fixture_data() -> _FakeCoordinator:
    catalog = default_catalog()
    registry = MaterialRegistry()
    registry.register(1, ItemType.RAW_A, "mat_1", sim_time=0.0,
                      source="s1a", destination="handoff_ac")
    registry.process(1, "A", 0.5)
    registry.occupy(1, "handoff_ac", 1.0, state=None)
    registry.process(1, "C", 2.0)
    registry.occupy(1, "buffer_e_c", 3.0, state=None)
    registry.register(2, ItemType.RAW_D, "mat_2", sim_time=0.0,
                      source="s2d", destination="buffer_e_d")
    registry.process(2, "D", 1.0)
    registry.occupy(2, "buffer_e_d", 2.0, state=None)
    registry.commit_join(final_uid=3, sim_time=10.0, final_alias="mat_3",
                         final_zone="output_e")

    resources = ResourceTable()
    resources.acquire("arm_A", "ownerB", "taskB", 0.5)
    assert not resources.acquire(
        "arm_A", "ownerA", "a_conveyor_uid1", 0.6)
    resources.release("arm_A", "ownerB", 1.5)
    assert resources.acquire(
        "arm_A", "ownerA", "a_conveyor_uid1", 1.6)
    resources.release("arm_A", "ownerA", 3.0)

    instances = [
        _instance(catalog,
                  instance_id="recipe_run_1:1:housing_direct_A",
                  template_id="housing_direct_A", uid=1,
                  task_id="a_conveyor_uid1", created=0.0, started=0.1,
                  committed=2.0, inputs=(1,), outputs=(1,)),
        _instance(catalog,
                  instance_id="recipe_run_1:3:kit_join",
                  template_id="kit_join", uid=3,
                  task_id="logical_join_uid3", created=9.0, started=9.0,
                  committed=10.0, inputs=(1, 2), outputs=(3,),
                  device="logic_E"),
    ]
    events = [
        {"sim_time": 0.0, "kind": "COORDINATOR_START", "task_id": "",
         "uid": None, "item_type": "", "zone": "", "source": "",
         "destination": "", "device": "", "evidence_id": "ev1",
         "detail": {}},
        {"sim_time": 2.0, "kind": "TASK_DONE",
         "task_id": "a_conveyor_uid1", "uid": 1, "item_type": "SEMI_AC",
         "zone": "handoff_ac", "source": "", "destination": "",
         "device": "arm_A", "evidence_id": "ev2", "detail": {}},
        {"sim_time": 10.0, "kind": "TASK_DONE",
         "task_id": "logical_join_uid3", "uid": 3, "item_type": "FINAL",
         "zone": "output_e", "source": "", "destination": "",
         "device": "logic_E", "evidence_id": "ev3", "detail": {}},
    ]
    return _FakeCoordinator(instances, registry, resources, events)


def _record_run(database: CatalogDatabase, run_id: str = "run_test_1",
                summary: dict | None = None,
                coordinator=None) -> tuple[RunRecorder, dict]:
    coordinator = coordinator or _fixture_data()
    recorder = RunRecorder(
        database, run_id=run_id, scenario="full_flow",
        artifact_id="v1/L5_UFLOW/v001", catalog_source="code")
    assert recorder.start(sim_time=0.0)["ok"]
    result = recorder.finish(coordinator, summary or _summary())
    return recorder, result


@pytest.fixture()
def database(tmp_path) -> CatalogDatabase:
    from flexible_factory.persistence.db import initialize_database
    db = CatalogDatabase(tmp_path / "runs.db")
    initialize_database(db)
    return db


# ═══════════════════════════════════════════════════════════════
# 写入
# ═══════════════════════════════════════════════════════════════

def test_recorder_writes_run_operations_materials_and_events(database):
    _recorder, result = _record_run(database)
    assert result["ok"] is True
    assert result["status"] == "DONE"
    assert result["counts"]["operation_instances"] == 2
    assert result["counts"]["materials"] == 3
    assert result["counts"]["genealogy_edges"] == 2
    assert result["counts"]["resource_events"] >= 3
    assert result["counts"]["run_events"] >= 4


def test_q1_run_summary_and_verification(database):
    _record_run(database)
    report = report_run(database, "run_test_1")
    assert report["status"] == "DONE"
    assert report["all_ok"] is True
    assert report["final_uids"] == [3]
    assert report["counts"]["operation_states"] == {"DONE": 2}
    assert report["counts"]["material_types"] == {
        "SEMI_CE": 1, "PART_DE": 1, "FINAL": 1}
    assert report["counts"]["material_states"] == {
        "CONSUMED": 2, "DELIVERED": 1}
    assert report["counts"]["resource_actions"]["WAIT"] == 1
    assert "RUN_START" in report["counts"]["event_kinds"]
    assert "RUN_END" in report["counts"]["event_kinds"]


def test_q2_material_genealogy_tree(database):
    _record_run(database)
    report = report_genealogy(database, uid=3, run_id="run_test_1")
    assert report["tree"]["material"]["uid"] == 3
    parents = sorted(row["material"]["uid"]
                     for row in report["tree"]["parents"])
    assert parents == [1, 2]
    assert sorted(row["uid"] for row in report["nodes"]) == [1, 2, 3]
    assert len(report["edges"]) == 2
    assert all(row["event_kind"] == "JOIN" for row in report["edges"])
    assert any(row["kind"] == "JOIN_COMMIT" for row in report["events"])


def test_q3_resource_wait_durations(database):
    _record_run(database)
    report = report_waits(database, run_id="run_test_1")
    arm = next(row for row in report["resources"]
               if row["resource_id"] == "arm_A")
    assert arm["acquire_count"] == 2
    assert arm["wait_count"] == 1
    assert arm["total_wait_s"] == pytest.approx(1.0)
    wait = report["waits"][0]
    assert wait["resource_id"] == "arm_A"
    assert wait["owner"] == "ownerA"
    assert wait["holder"] == "ownerB"
    assert wait["wait_s"] == pytest.approx(1.0)
    assert wait["resolved"] is True
    assert wait["instance_id"] == "recipe_run_1:1:housing_direct_A"


def test_q4_operation_timeline_and_failure_forensics(database):
    _record_run(database)
    report = report_timeline(database, run_id="run_test_1")
    rows = {row["instance_id"]: row for row in report["instances"]}
    a_row = rows["recipe_run_1:1:housing_direct_A"]
    assert a_row["state"] == "DONE"
    assert a_row["selected_device_id"] == "arm_A"
    assert a_row["duration_s"] == pytest.approx(1.9)
    assert a_row["ready_wait_s"] == pytest.approx(0.1)
    assert a_row["input_uids"] == [1]
    assert a_row["output_uids"] == [1]
    assert a_row["resource_waits"]
    join = rows["recipe_run_1:3:kit_join"]
    assert join["input_uids"] == [1, 2]
    assert join["output_uids"] == [3]
    assert report["failures"] == []


def test_failed_and_aborted_runs_are_recorded(database):
    _record_run(database, "run_aborted",
                summary=_summary(ok=False, aborted=True,
                                 stop_reason="STOP_REQUESTED"))
    report = report_run(database, "run_aborted")
    assert report["status"] == "ABORTED"
    assert report["all_ok"] is False
    assert report["failure_reason"] == "STOP_REQUESTED"
    assert "RUN_END" in report["counts"]["event_kinds"]


def test_final_status_failed_when_acceptance_fails(database):
    _record_run(database, "run_failed",
                summary=_summary(ok=False, stop_reason="SAFETY_LATCH"))
    report = report_run(database, "run_failed")
    assert report["status"] == "FAILED"
    assert report["failure_reason"] == "SAFETY_LATCH"


def test_recorder_uses_real_operation_runtime_instances(database):
    catalog = default_catalog()
    recipe = catalog.recipe(DEFAULT_RECIPE_ID)
    runtime = OperationRuntime(catalog, recipe)
    created = runtime.instantiate_entry(
        "s1a", 1, item_type="RAW_A", sim_time=0.0)
    registry = MaterialRegistry()
    registry.register(1, ItemType.RAW_A, "mat_1", sim_time=0.0)
    resources = ResourceTable()

    class _Coordinator:
        pass

    coordinator = _Coordinator()
    coordinator.catalog = catalog
    coordinator.recipe = recipe
    coordinator.op_runtime = runtime
    coordinator.flow = registry
    coordinator.resources = resources
    coordinator.events = []

    recorder = RunRecorder(
        database, run_id="run_real_runtime", scenario="full_flow")
    assert recorder.start(sim_time=0.0)["ok"]
    result = recorder.finish(coordinator, _summary())
    assert result["ok"] is True, result["error"]
    assert result["counts"]["operation_instances"] == len(created)
    report = report_run(database, "run_real_runtime")
    assert report["counts"]["operation_states"] == {
        "PENDING": len(created)}


def test_recorder_finish_is_idempotent(database):
    recorder, result = _record_run(database)
    again = recorder.finish(_fixture_data(), _summary())
    assert again == result
    assert report_run(database, "run_test_1")["counts"][
        "operation_states"] == {"DONE": 2}
    assert recorder.start(sim_time=0.0)["changed"] is False


def test_persistence_failure_marks_run_failed_without_raising(
        database, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated write failure")

    monkeypatch.setattr(RunRecorder, "_write_children", boom)
    recorder, result = _record_run(database, "run_broken")
    assert result["ok"] is False
    assert "simulated write failure" in result["error"]
    report = report_run(database, "run_broken")
    assert report["status"] == "FAILED"
    assert report["finished_at"] is not None
    assert "运行记录写入失败" in report["failure_reason"]


def test_start_failure_does_not_create_run_and_finish_is_noop(tmp_path):
    blocker = tmp_path / "not_a_db"
    blocker.mkdir()
    recorder = RunRecorder(
        CatalogDatabase(blocker), run_id="run_no_db",
        scenario="full_flow")
    started = recorder.start(sim_time=0.0)
    assert started["ok"] is False
    result = recorder.finish(None, None, error="before start")
    assert result["ok"] is False
    assert result["status"] == "FAILED"


# ═══════════════════════════════════════════════════════════════
# 查询边界与 CLI
# ═══════════════════════════════════════════════════════════════

def test_unknown_run_or_uid_is_reported(database):
    from flexible_factory.persistence.errors import RunNotFoundError
    _record_run(database)
    with pytest.raises(RunNotFoundError):
        report_run(database, "missing")
    with pytest.raises(RunNotFoundError):
        report_genealogy(database, uid=99, run_id="run_test_1")


def test_cli_report_commands(database, capsys):
    _record_run(database)
    db = str(database.path)
    assert main(["report", "runs", "--db", db]) == 0
    assert main(["report", "run", "--db", db,
                 "--run-id", "run_test_1"]) == 0
    assert main(["report", "genealogy", "--db", db,
                 "--run-id", "run_test_1", "--uid", "3"]) == 0
    assert main(["report", "waits", "--db", db,
                 "--run-id", "run_test_1", "--resource", "arm_A"]) == 0
    assert main(["report", "timeline", "--db", db,
                 "--run-id", "run_test_1"]) == 0
    output = capsys.readouterr().out
    assert "run_test_1" in output


def test_run_parser_accepts_run_record_options():
    ns = build_parser().parse_args([
        "run", "--run-records", "off",
        "--catalog-db", "var/catalog/catalog.db"])
    assert ns.run_records == "off"
    assert ns.catalog_db == "var/catalog/catalog.db"
