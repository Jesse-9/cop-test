"""persistence.runs — 仿真运行记录写入与查询 (方案 §2.2, 阶段 4).

写入只发生在两个明确边界:

  - ``RunRecorder.start``: 运行开始写一行 ``production_run(RUNNING)``;
  - ``RunRecorder.finish``: 运行结束在一个事务内批量写工序/物料/谱系/
    资源事件/运行事件, 并更新运行终态.

``sim.step()`` 循环内不写库. 写入失败只标记运行报告中的持久化失败, 不改变
仿真结果, 不自动重放. 查询函数只读, 覆盖已确认的 Q1–Q4.
"""

from __future__ import annotations

import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import spec
from .clock import utc_iso_now
from .db import CatalogDatabase, initialize_database
from .errors import RunNotFoundError, RunRecordError
from .run_models import (MaterialGenealogy, MaterialInstanceRecord,
                         OperationInstanceInput, OperationInstanceOutput,
                         OperationInstanceRecord, ProductionRun,
                         ResourceEventRecord, RunEventRecord)

RUN_RECORD_VERSION = "v1_run_records_20261105"


# ═══════════════════════════════════════════════════════════════
# 写入
# ═══════════════════════════════════════════════════════════════

class RunRecorder:
    """在一个仿真批次开始/结束时写运行记录; 失败不影响仿真."""

    def __init__(self, database: CatalogDatabase, *, run_id: str,
                 scenario: str, artifact_id: str = "",
                 catalog_source: str = "code",
                 catalog_version: str = "",
                 scene_contract_version: str = "",
                 record_version: str = RUN_RECORD_VERSION) -> None:
        self.database = database
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.run_id = str(run_id or "").strip() or (
            f"run_{stamp}_{uuid.uuid4().hex[:6]}")
        self.scenario = str(scenario)
        self.artifact_id = str(artifact_id or "")
        self.catalog_source = str(catalog_source or "code")
        self.catalog_version = str(catalog_version or "")
        self.scene_contract_version = str(
            scene_contract_version or spec.SCENE_CONTRACT_VERSION)
        self.record_version = str(record_version)
        self.error = ""
        self._started = False
        self._finished = False
        self._result: Optional[dict] = None
        self._wall_start = time.monotonic()
        self._sim_started_s: Optional[float] = None

    @property
    def result(self) -> Optional[dict]:
        return self._result

    def start(self, *, sim_time: Optional[float] = None) -> dict:
        """初始化 schema 并写 RUNNING 行; 失败时返回 ok=False."""
        if self._started:
            return {"ok": True, "run_id": self.run_id, "status": "RUNNING",
                    "database": str(self.database.path), "changed": False}
        try:
            initialize_database(self.database)
            with self.database.session() as session:
                with session.begin():
                    if session.get(ProductionRun, self.run_id) is not None:
                        raise RunRecordError(
                            f"run_id {self.run_id!r} 已存在; 运行记录不覆盖")
                    session.add(ProductionRun(
                        run_id=self.run_id,
                        record_version=self.record_version,
                        scenario=self.scenario,
                        artifact_id=self.artifact_id or None,
                        catalog_version=self.catalog_version or None,
                        catalog_source=self.catalog_source,
                        scene_contract_version=(
                            self.scene_contract_version or None),
                        status="RUNNING",
                        started_at=utc_iso_now(),
                        finished_at=None,
                        sim_started_s=(None if sim_time is None
                                       else float(sim_time)),
                        sim_finished_s=None, wall_duration_s=None,
                        all_ok=None, failure_reason=None,
                        result_json=None))
            self._started = True
            self._sim_started_s = (None if sim_time is None
                                   else float(sim_time))
            self._wall_start = time.monotonic()
            return {"ok": True, "run_id": self.run_id,
                    "status": "RUNNING",
                    "database": str(self.database.path), "changed": True}
        except Exception as exc:
            self.error = (f"{getattr(exc, 'code', type(exc).__name__)}: "
                          f"{exc}")
            return {"ok": False, "run_id": self.run_id,
                    "status": "FAILED", "error": self.error,
                    "database": str(self.database.path)}

    def finish(self, coordinator=None, summary: Optional[dict] = None, *,
               error: str = "", status: str = "") -> dict:
        """批量写运行结果; 重复调用返回第一次结果."""
        if self._finished:
            assert self._result is not None
            return self._result
        if not self._started:
            result = {"ok": False, "run_id": self.run_id,
                      "status": "FAILED",
                      "error": self.error or "运行记录未启动",
                      "counts": {}}
            self._finished = True
            self._result = result
            return result

        summary = summary or {}
        final_status = status or self._status(summary, error)
        failure_reason = error or (
            "" if final_status == "DONE"
            else str(summary.get("stop_reason") or ""))
        try:
            with self.database.session() as session:
                with session.begin():
                    run = session.get(ProductionRun, self.run_id)
                    if run is None:
                        raise RunRecordError(
                            f"production_run {self.run_id!r} 不存在")
                    counts = self._write_children(
                        session, coordinator, summary,
                        final_status, failure_reason)
                    run.status = final_status
                    run.finished_at = utc_iso_now()
                    run.sim_finished_s = (
                        float(summary["sim_time"])
                        if summary.get("sim_time") is not None else None)
                    run.wall_duration_s = max(
                        0.0, time.monotonic() - self._wall_start)
                    run.all_ok = bool(
                        (summary.get("acceptance") or {}).get("ok"))
                    run.failure_reason = failure_reason or None
                    run.catalog_version = (
                        coordinator.catalog.version if coordinator is not None
                        else (run.catalog_version or None))
                    run.result_json = _compact_summary(summary)
                    session.flush()
            result = {"ok": True, "run_id": self.run_id,
                      "status": final_status, "error": "",
                      "counts": counts,
                      "database": str(self.database.path),
                      "changed": True}
        except Exception as exc:
            self.error = (f"{getattr(exc, 'code', type(exc).__name__)}: "
                          f"{exc}")
            self._mark_failed(self.error)
            result = {"ok": False, "run_id": self.run_id,
                      "status": "FAILED", "error": self.error,
                      "counts": {},
                      "database": str(self.database.path)}
        self._finished = True
        self._result = result
        return result

    def _status(self, summary: dict, error: str) -> str:
        if error:
            return "FAILED"
        if summary.get("aborted"):
            return "ABORTED"
        if (summary.get("acceptance") or {}).get("ok"):
            return "DONE"
        return "FAILED"

    def _mark_failed(self, error: str) -> None:
        """尽力把 RUNNING 行标为 FAILED; 失败不再抛出."""
        try:
            with self.database.session() as session:
                with session.begin():
                    run = session.get(ProductionRun, self.run_id)
                    if run is None or run.status != "RUNNING":
                        return
                    run.status = "FAILED"
                    run.finished_at = utc_iso_now()
                    run.failure_reason = f"运行记录写入失败: {error}"[:4000]
                    run.wall_duration_s = max(
                        0.0, time.monotonic() - self._wall_start)
        except Exception:
            pass

    # ── 批量子表 ────────────────────────────────────────────

    def _write_children(self, session: Session, coordinator,
                        summary: dict, final_status: str,
                        failure_reason: str) -> dict:
        counts = {
            "materials": 0, "operation_instances": 0,
            "operation_inputs": 0, "operation_outputs": 0,
            "genealogy_edges": 0, "resource_events": 0, "run_events": 0,
        }
        if coordinator is None:
            return counts

        instances = list(coordinator.op_runtime.instances())
        materials = list(coordinator.flow.all())
        material_by_uid = {int(row.uid): row for row in materials}
        task_to_instance = {row.task_id: row.instance_id
                            for row in instances if row.task_id}

        for material in materials:
            history = list(material.history)
            session.add(MaterialInstanceRecord(
                run_id=self.run_id, uid=int(material.uid),
                alias=material.alias,
                item_type=material.item_type.value,
                status=material.state.value,
                zone=material.zone or "",
                source=material.source or "",
                destination=material.destination or "",
                process_stage=material.process_stage or "",
                created_sim_time=(float(history[0].sim_time)
                                  if history else None),
                last_event_sim_time=(float(history[-1].sim_time)
                                     if history else None)))
            counts["materials"] += 1
        session.flush()

        for instance in instances:
            session.add(OperationInstanceRecord(
                run_id=self.run_id,
                instance_id=instance.instance_id,
                template_id=instance.template_id,
                kind=instance.kind,
                recipe_id=instance.recipe_id,
                recipe_version=instance.recipe_version,
                recipe_run_id=instance.run_id,
                entry_id=instance.entry_id,
                route_id=instance.selected_route,
                task_id=instance.task_id,
                uid=int(instance.uid),
                selected_device_id=(
                    instance.selected_device_id or instance.device or None),
                state=instance.state.value,
                phase=instance.phase or "",
                priority=int(instance.priority),
                attempts=int(instance.attempts),
                sequence=int(instance.sequence),
                transport=bool(instance.transport),
                upgraded=bool(instance.upgraded),
                created_sim_time=float(instance.created_sim_time),
                started_sim_time=(None if instance.started_sim_time is None
                                  else float(instance.started_sim_time)),
                finished_sim_time=(None if instance.finished_sim_time is None
                                   else float(instance.finished_sim_time)),
                committed_sim_time=(None if instance.committed_sim_time is None
                                    else float(instance.committed_sim_time)),
                waiting_since=(None if instance.waiting_since is None
                               else float(instance.waiting_since)),
                blocked_since=(None if instance.blocked_since is None
                               else float(instance.blocked_since)),
                failure_reason=instance.failure_reason or "",
                evidence_json=instance.evidence or None))
            counts["operation_instances"] += 1
        session.flush()

        for instance in instances:
            for ordinal, uid in enumerate(instance.input_uids):
                uid = int(uid)
                if uid not in material_by_uid:
                    raise RunRecordError(
                        f"工序 {instance.instance_id} 输入 UID {uid} "
                        f"没有物料记录")
                session.add(OperationInstanceInput(
                    run_id=self.run_id, instance_id=instance.instance_id,
                    ordinal=ordinal, uid=uid))
                counts["operation_inputs"] += 1
            for ordinal, uid in enumerate(instance.output_uids):
                uid = int(uid)
                if uid not in material_by_uid:
                    raise RunRecordError(
                        f"工序 {instance.instance_id} 输出 UID {uid} "
                        f"没有物料记录")
                session.add(OperationInstanceOutput(
                    run_id=self.run_id, instance_id=instance.instance_id,
                    ordinal=ordinal, uid=uid))
                counts["operation_outputs"] += 1
        session.flush()

        join_template = ""
        try:
            join_template = coordinator.recipe.join_operation_id or ""
        except Exception:
            pass
        by_output_uid: dict[int, str] = {}
        for instance in instances:
            for uid in instance.output_uids:
                by_output_uid.setdefault(int(uid), instance.instance_id)
        for row in coordinator.flow.join_history:
            child = int(row["final_uid"])
            parents = [int(row["semi_uid"]), int(row["part_uid"])]
            if child not in material_by_uid:
                raise RunRecordError(
                    f"谱系子 UID {child} 没有物料记录")
            operation_instance_id = ""
            for instance in instances:
                if child in [int(uid) for uid in instance.output_uids] and \
                        (not join_template
                         or instance.template_id == join_template):
                    operation_instance_id = instance.instance_id
                    break
            if not operation_instance_id:
                operation_instance_id = by_output_uid.get(child, "")
            for parent in parents:
                if parent not in material_by_uid:
                    raise RunRecordError(
                        f"谱系父 UID {parent} 没有物料记录")
                session.add(MaterialGenealogy(
                    run_id=self.run_id, child_uid=child,
                    parent_uid=parent,
                    operation_instance_id=operation_instance_id or None,
                    event_kind="JOIN",
                    pair_key=str(row.get("pair_key") or "") or None,
                    sim_time=float(row.get("sim_time") or 0.0)))
                counts["genealogy_edges"] += 1
        session.flush()

        for event in coordinator.resources.events:
            session.add(ResourceEventRecord(
                run_id=self.run_id,
                sim_time=float(event.sim_time),
                action=event.action,
                resource_id=event.resource,
                owner=event.owner,
                task_id=event.task_id or "",
                holder=event.holder or "",
                reason=event.reason or "",
                instance_id=task_to_instance.get(event.task_id)))
            counts["resource_events"] += 1
        session.flush()

        events: list[dict] = []
        for event in coordinator.events:
            events.append(dict(event))
        for event in coordinator.flow.events:
            events.append(event.as_dict())
        sim_end = summary.get("sim_time")
        events.append({
            "sim_time": (self._sim_started_s
                         if self._sim_started_s is not None else 0.0),
            "kind": "RUN_START", "task_id": "", "uid": None,
            "item_type": "", "zone": "", "source": "", "destination": "",
            "device": "", "evidence_id": "run_start",
            "detail": {"scenario": self.scenario,
                       "artifact_id": self.artifact_id,
                       "catalog_source": self.catalog_source},
        })
        events.append({
            "sim_time": float(sim_end) if sim_end is not None else 0.0,
            "kind": "RUN_END", "task_id": "", "uid": None,
            "item_type": "", "zone": "", "source": "", "destination": "",
            "device": "", "evidence_id": "run_end",
            "detail": {"status": final_status,
                       "all_ok": bool(
                           (summary.get("acceptance") or {}).get("ok")),
                       "failure_reason": failure_reason},
        })
        indexed = list(enumerate(events))
        indexed.sort(key=lambda item: (float(item[1].get("sim_time") or 0.0),
                                       item[0]))
        for _index, event in indexed:
            kind = str(event.get("kind") or "")
            if not kind:
                continue
            task_id = str(event.get("task_id") or "")
            session.add(RunEventRecord(
                run_id=self.run_id,
                sim_time=float(event.get("sim_time") or 0.0),
                kind=kind,
                task_id=task_id,
                uid=(None if event.get("uid") is None
                     else int(event["uid"])),
                item_type=str(event.get("item_type") or ""),
                zone=str(event.get("zone") or ""),
                source=str(event.get("source") or ""),
                destination=str(event.get("destination") or ""),
                device=str(event.get("device") or ""),
                evidence_id=str(event.get("evidence_id") or ""),
                instance_id=task_to_instance.get(task_id),
                detail_json=event.get("detail") or {}))
            counts["run_events"] += 1
        session.flush()
        return counts


def _compact_summary(summary: dict) -> Optional[dict]:
    if not summary:
        return None
    keys = ("scenario", "fault", "sim_time", "aborted", "frozen",
            "stop_reason", "batch", "source_counts", "agv_at_dock",
            "metrics", "acceptance")
    compact = {key: summary[key] for key in keys if key in summary}
    process_model = summary.get("process_model") or {}
    if process_model:
        compact["process_model_version"] = process_model.get("version")
    return compact


# ═══════════════════════════════════════════════════════════════
# 查询
# ═══════════════════════════════════════════════════════════════

def _require_run(session: Session,
                 run_id: Optional[str]) -> ProductionRun:
    if run_id:
        run = session.get(ProductionRun, str(run_id))
        if run is None:
            raise RunNotFoundError(f"run_id {run_id!r} 不存在")
        return run
    run = session.scalars(select(ProductionRun).order_by(
        ProductionRun.started_at.desc(),
        ProductionRun.run_id.desc()).limit(1)).one_or_none()
    if run is None:
        raise RunNotFoundError("没有运行记录")
    return run


def list_runs(database: CatalogDatabase, *, limit: int = 50) -> list[dict]:
    with database.session() as session:
        rows = session.scalars(select(ProductionRun).order_by(
            ProductionRun.started_at.desc(),
            ProductionRun.run_id.desc()).limit(max(1, int(limit))))
        return [{
            "run_id": row.run_id,
            "scenario": row.scenario,
            "status": row.status,
            "artifact_id": row.artifact_id,
            "catalog_version": row.catalog_version,
            "catalog_source": row.catalog_source,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "sim_duration_s": (
                None if row.sim_started_s is None
                or row.sim_finished_s is None
                else round(row.sim_finished_s - row.sim_started_s, 4)),
            "wall_duration_s": row.wall_duration_s,
            "all_ok": row.all_ok,
            "failure_reason": row.failure_reason,
        } for row in rows]


def run_summary(database: CatalogDatabase,
                run_id: Optional[str] = None) -> dict:
    with database.session() as session:
        run = _require_run(session, run_id)

        def grouped(model, *columns) -> dict:
            rows = session.execute(
                select(*columns, func.count()).where(
                    model.run_id == run.run_id).group_by(*columns)).all()
            return {"/".join(str(value) for value in row[:-1]):
                    int(row[-1]) for row in rows}

        operation_states = grouped(
            OperationInstanceRecord, OperationInstanceRecord.state)
        material_types = grouped(
            MaterialInstanceRecord, MaterialInstanceRecord.item_type)
        material_states = grouped(
            MaterialInstanceRecord, MaterialInstanceRecord.status)
        resource_actions = grouped(
            ResourceEventRecord, ResourceEventRecord.action)
        event_kinds = grouped(RunEventRecord, RunEventRecord.kind)
        final_uids = [int(row) for row in session.scalars(select(
            MaterialInstanceRecord.uid).where(
            MaterialInstanceRecord.run_id == run.run_id,
            MaterialInstanceRecord.item_type == "FINAL").order_by(
            MaterialInstanceRecord.uid))]
        return {
            "run_id": run.run_id,
            "record_version": run.record_version,
            "scenario": run.scenario,
            "artifact_id": run.artifact_id,
            "catalog_version": run.catalog_version,
            "catalog_source": run.catalog_source,
            "scene_contract_version": run.scene_contract_version,
            "status": run.status,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "sim_started_s": run.sim_started_s,
            "sim_finished_s": run.sim_finished_s,
            "sim_duration_s": (
                None if run.sim_started_s is None
                or run.sim_finished_s is None
                else round(run.sim_finished_s - run.sim_started_s, 4)),
            "wall_duration_s": run.wall_duration_s,
            "all_ok": run.all_ok,
            "failure_reason": run.failure_reason,
            "final_uids": final_uids,
            "counts": {
                "operation_states": operation_states,
                "material_types": material_types,
                "material_states": material_states,
                "resource_actions": resource_actions,
                "event_kinds": event_kinds,
            },
            "result": run.result_json,
        }


def _material_dict(row: MaterialInstanceRecord) -> dict:
    return {
        "uid": int(row.uid),
        "alias": row.alias,
        "item_type": row.item_type,
        "status": row.status,
        "zone": row.zone,
        "source": row.source,
        "destination": row.destination,
        "process_stage": row.process_stage,
        "created_sim_time": row.created_sim_time,
        "last_event_sim_time": row.last_event_sim_time,
    }


def material_genealogy(database: CatalogDatabase, *, uid: int,
                       run_id: Optional[str] = None) -> dict:
    with database.session() as session:
        run = _require_run(session, run_id)
        materials = {int(row.uid): row for row in session.scalars(select(
            MaterialInstanceRecord).where(
            MaterialInstanceRecord.run_id == run.run_id))}
        if int(uid) not in materials:
            raise RunNotFoundError(
                f"run {run.run_id!r} 没有 UID {uid}")
        edges = list(session.scalars(select(MaterialGenealogy).where(
            MaterialGenealogy.run_id == run.run_id).order_by(
            MaterialGenealogy.child_uid, MaterialGenealogy.parent_uid)))
        parents: dict[int, list[MaterialGenealogy]] = defaultdict(list)
        for edge in edges:
            parents[int(edge.child_uid)].append(edge)

        visited: set[int] = set()

        def build(node: int) -> dict:
            if node in visited:
                return {"uid": node, "cycle": True}
            visited.add(node)
            return {
                "material": _material_dict(materials[node]),
                "parents": [build(int(edge.parent_uid))
                            for edge in parents.get(node, [])],
            }

        tree = build(int(uid))
        node_uids = sorted(visited)
        events = list(session.scalars(select(RunEventRecord).where(
            RunEventRecord.run_id == run.run_id,
            RunEventRecord.uid.in_(node_uids)).order_by(
            RunEventRecord.sim_time, RunEventRecord.event_id)))
        return {
            "run_id": run.run_id,
            "uid": int(uid),
            "tree": tree,
            "nodes": [_material_dict(materials[node]) for node in node_uids],
            "edges": [{
                "child_uid": int(edge.child_uid),
                "parent_uid": int(edge.parent_uid),
                "operation_instance_id": edge.operation_instance_id,
                "event_kind": edge.event_kind,
                "pair_key": edge.pair_key,
                "sim_time": edge.sim_time,
            } for edge in edges if int(edge.child_uid) in visited],
            "events": [{
                "event_id": event.event_id,
                "sim_time": event.sim_time,
                "kind": event.kind,
                "uid": event.uid,
                "item_type": event.item_type,
                "zone": event.zone,
                "task_id": event.task_id,
                "instance_id": event.instance_id,
                "detail": event.detail_json,
            } for event in events],
        }


def resource_waits(database: CatalogDatabase, *,
                   run_id: Optional[str] = None,
                   resource_id: str = "") -> dict:
    with database.session() as session:
        run = _require_run(session, run_id)
        statement = select(ResourceEventRecord).where(
            ResourceEventRecord.run_id == run.run_id)
        if resource_id:
            statement = statement.where(
                ResourceEventRecord.resource_id == resource_id)
        events = list(session.scalars(statement.order_by(
            ResourceEventRecord.event_id)))
        sim_end = (float(run.sim_finished_s)
                   if run.sim_finished_s is not None
                   else max([float(event.sim_time) for event in events] or [0.0]))
        stats: dict[str, dict] = {}
        waits: list[dict] = []
        open_waits: dict[tuple[str, str, str], dict] = {}
        holdings: dict[tuple[str, str], dict] = {}

        def bucket(resource: str) -> dict:
            return stats.setdefault(resource, {
                "resource_id": resource,
                "acquire_count": 0, "release_count": 0,
                "wait_count": 0, "total_wait_s": 0.0, "max_wait_s": 0.0,
                "unresolved_wait_s": 0.0,
                "total_hold_s": 0.0, "max_hold_s": 0.0,
                "release_denied": 0, "order_violations": 0,
            })

        for event in events:
            resource = event.resource_id
            row = bucket(resource)
            key = (resource, event.owner, event.task_id)
            if event.action == "WAIT":
                if key not in open_waits:
                    open_waits[key] = {
                        "resource_id": resource,
                        "owner": event.owner,
                        "task_id": event.task_id,
                        "instance_id": event.instance_id,
                        "holder": event.holder,
                        "wait_start_s": event.sim_time,
                        "reason": event.reason,
                    }
            elif event.action == "ACQUIRE":
                row["acquire_count"] += 1
                if key in open_waits:
                    wait = open_waits.pop(key)
                    wait_s = max(0.0, event.sim_time - wait["wait_start_s"])
                    wait.update({"acquired_s": event.sim_time,
                                 "wait_s": round(wait_s, 4),
                                 "resolved": True})
                    waits.append(wait)
                    row["wait_count"] += 1
                    row["total_wait_s"] += wait_s
                    row["max_wait_s"] = max(row["max_wait_s"], wait_s)
                holdings[(resource, event.owner)] = {
                    "start_s": event.sim_time,
                    "owner": event.owner,
                    "task_id": event.task_id,
                    "instance_id": event.instance_id,
                }
            elif event.action == "RELEASE":
                row["release_count"] += 1
                held = holdings.pop((resource, event.owner), None)
                if held is not None:
                    hold_s = max(0.0, event.sim_time - held["start_s"])
                    row["total_hold_s"] += hold_s
                    row["max_hold_s"] = max(row["max_hold_s"], hold_s)
            elif event.action == "RELEASE_DENIED":
                row["release_denied"] += 1
            elif event.action == "ORDER_VIOLATION":
                row["order_violations"] += 1

        for key, wait in open_waits.items():
            wait_s = max(0.0, sim_end - wait["wait_start_s"])
            wait.update({"acquired_s": None, "wait_s": round(wait_s, 4),
                         "resolved": False})
            waits.append(wait)
            row = stats[wait["resource_id"]]
            row["wait_count"] += 1
            row["total_wait_s"] += wait_s
            row["unresolved_wait_s"] += wait_s
            row["max_wait_s"] = max(row["max_wait_s"], wait_s)
        for (resource, _owner), held in holdings.items():
            hold_s = max(0.0, sim_end - held["start_s"])
            row = stats[resource]
            row["total_hold_s"] += hold_s
            row["max_hold_s"] = max(row["max_hold_s"], hold_s)

        for row in stats.values():
            row["total_wait_s"] = round(row["total_wait_s"], 4)
            row["max_wait_s"] = round(row["max_wait_s"], 4)
            row["unresolved_wait_s"] = round(row["unresolved_wait_s"], 4)
            row["total_hold_s"] = round(row["total_hold_s"], 4)
            row["max_hold_s"] = round(row["max_hold_s"], 4)
        resources = sorted(stats.values(),
                           key=lambda row: (-row["total_wait_s"],
                                            row["resource_id"]))
        return {
            "run_id": run.run_id,
            "sim_end_s": sim_end,
            "resources": resources,
            "top_contention": resources[:10],
            "waits": sorted(
                waits, key=lambda row: (-row["wait_s"], row["resource_id"],
                                        row["owner"], row["task_id"])),
        }


def operation_timeline(database: CatalogDatabase, *,
                       run_id: Optional[str] = None,
                       instance_id: str = "") -> dict:
    with database.session() as session:
        run = _require_run(session, run_id)
        statement = select(OperationInstanceRecord).where(
            OperationInstanceRecord.run_id == run.run_id)
        if instance_id:
            statement = statement.where(
                OperationInstanceRecord.instance_id == instance_id)
        rows = list(session.scalars(statement.order_by(
            OperationInstanceRecord.created_sim_time,
            OperationInstanceRecord.sequence,
            OperationInstanceRecord.instance_id)))
        if instance_id and not rows:
            raise RunNotFoundError(
                f"run {run.run_id!r} 没有工序实例 {instance_id!r}")
        inputs: dict[str, list[int]] = defaultdict(list)
        for row in session.scalars(select(OperationInstanceInput).where(
                OperationInstanceInput.run_id == run.run_id).order_by(
                OperationInstanceInput.instance_id,
                OperationInstanceInput.ordinal)):
            inputs[row.instance_id].append(int(row.uid))
        outputs: dict[str, list[int]] = defaultdict(list)
        for row in session.scalars(select(OperationInstanceOutput).where(
                OperationInstanceOutput.run_id == run.run_id).order_by(
                OperationInstanceOutput.instance_id,
                OperationInstanceOutput.ordinal)):
            outputs[row.instance_id].append(int(row.uid))
        wait_report = resource_waits(database, run_id=run.run_id)
        waits_by_instance: dict[str, list[dict]] = defaultdict(list)
        for wait in wait_report["waits"]:
            if wait.get("instance_id"):
                waits_by_instance[wait["instance_id"]].append(wait)
        events_by_instance: dict[str, list[dict]] = defaultdict(list)
        for event in session.scalars(select(RunEventRecord).where(
                RunEventRecord.run_id == run.run_id,
                RunEventRecord.instance_id.is_not(None)).order_by(
                RunEventRecord.sim_time, RunEventRecord.event_id)):
            events_by_instance[event.instance_id].append({
                "event_id": event.event_id,
                "sim_time": event.sim_time,
                "kind": event.kind,
                "task_id": event.task_id,
                "detail": event.detail_json,
            })
        instances = []
        for row in rows:
            end = row.committed_sim_time or row.finished_sim_time
            duration = (None if row.started_sim_time is None or end is None
                        else round(end - row.started_sim_time, 4))
            ready_wait = (None if row.started_sim_time is None
                          else round(max(0.0, row.started_sim_time -
                                         row.created_sim_time), 4))
            instances.append({
                "instance_id": row.instance_id,
                "template_id": row.template_id,
                "kind": row.kind,
                "task_id": row.task_id,
                "recipe_id": row.recipe_id,
                "recipe_version": row.recipe_version,
                "recipe_run_id": row.recipe_run_id,
                "entry_id": row.entry_id,
                "route_id": row.route_id,
                "uid": int(row.uid),
                "state": row.state,
                "phase": row.phase,
                "priority": int(row.priority),
                "attempts": int(row.attempts),
                "selected_device_id": row.selected_device_id,
                "input_uids": inputs.get(row.instance_id, []),
                "output_uids": outputs.get(row.instance_id, []),
                "created_sim_time": row.created_sim_time,
                "started_sim_time": row.started_sim_time,
                "finished_sim_time": row.finished_sim_time,
                "committed_sim_time": row.committed_sim_time,
                "waiting_since": row.waiting_since,
                "blocked_since": row.blocked_since,
                "ready_wait_s": ready_wait,
                "duration_s": duration,
                "failure_reason": row.failure_reason,
                "evidence": row.evidence_json,
                "resource_waits": waits_by_instance.get(row.instance_id, []),
                "events": events_by_instance.get(row.instance_id, []),
            })
        failures = [row for row in instances
                    if row["state"] in ("FAILED", "REJECTED")
                    or row["failure_reason"]]
        return {
            "run": {
                "run_id": run.run_id,
                "scenario": run.scenario,
                "status": run.status,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "sim_duration_s": (
                    None if run.sim_started_s is None
                    or run.sim_finished_s is None
                    else round(run.sim_finished_s - run.sim_started_s, 4)),
                "all_ok": run.all_ok,
                "failure_reason": run.failure_reason,
                "result": run.result_json,
            },
            "instances": instances,
            "failures": failures,
        }
