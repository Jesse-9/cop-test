"""persistence.orders — 版本化订单仓储 (阶段 3).

写入边界:

  - 订单版本只追加; ``order_version`` 的 UPDATE 由数据库触发器拒绝;
  - 订单运行关联在启动前写入, 终态在一个事务内更新;
  - 节点快照每次终态写入时整体替换, 不在仿真 step 热路径调用.

读取时重新解析规范化 spec 并核对 digest/身份; 任何不一致抛
:class:`OrderIntegrityError`, 不返回“看似可用”的订单.
"""

from __future__ import annotations

import json
from typing import Iterable, Optional

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from ..domain.orders import OrderSpec, OrderSpecError
from .clock import utc_iso_now
from .db import CatalogDatabase, initialize_database
from .errors import (OrderIntegrityError, OrderNotFoundError,
                     OrderRunConflictError, OrderRunNotFoundError,
                     OrderStateError, OrderVersionConflictError,
                     OrderVersionNotFoundError)
from .order_models import (ORDER_STATUSES, OrderHeader, OrderNodeRun,
                           OrderRun, OrderVersion)
from .run_models import RunEventRecord


class OrderRepository:
    """订单定义与运行关联的唯一读写入口; 不导入 application/domain 运行逻辑."""

    def __init__(self, database: CatalogDatabase) -> None:
        self.database = database

    def initialize(self) -> dict:
        return initialize_database(self.database)

    # ── 订单与版本 ──────────────────────────────────────────

    def create_order(self, order: OrderSpec, *, source: str = "",
                     note: str = "", catalog_version: str = "",
                     planner_policy_version: str = "",
                     planner_input: Optional[dict] = None,
                     planner_explain: Optional[dict] = None,
                     planner_candidate_count: Optional[int] = None) -> dict:
        source = str(source or order.source or "manual")
        if order.version != 1:
            raise OrderVersionConflictError(
                f"新订单 {order.order_id!r} 的首版本必须是 1, 实际 "
                f"{order.version}")
        now = utc_iso_now()
        with self.database.session() as session:
            with session.begin():
                if session.get(OrderHeader, order.order_id) is not None:
                    raise OrderVersionConflictError(
                        f"订单 {order.order_id!r} 已存在")
                session.add(OrderHeader(
                    order_id=order.order_id, current_version=1,
                    source=source, created_at=now, updated_at=now,
                    note=note or None))
                session.add(self._version_row(
                    order, source=source, parent_version=None, note=note,
                    created_at=now, catalog_version=catalog_version,
                    planner_policy_version=planner_policy_version,
                    planner_input=planner_input,
                    planner_explain=planner_explain,
                    planner_candidate_count=planner_candidate_count))
            return self.get_order(order.order_id, session=session)

    def create_version(self, order: OrderSpec, *, expected_version: int = 0,
                       note: str = "", catalog_version: str = "",
                       planner_policy_version: str = "",
                       planner_input: Optional[dict] = None,
                       planner_explain: Optional[dict] = None,
                       planner_candidate_count: Optional[int] = None) -> dict:
        with self.database.session() as session:
            with session.begin():
                header = session.get(OrderHeader, order.order_id)
                if header is None:
                    raise OrderNotFoundError(
                        f"订单 {order.order_id!r} 不存在")
                current = int(header.current_version)
                if expected_version and int(expected_version) != current:
                    raise OrderVersionConflictError(
                        f"订单 {order.order_id!r} 当前版本 {current}, "
                        f"期望 {expected_version}")
                next_version = current + 1
                if int(order.version) != next_version:
                    raise OrderVersionConflictError(
                        f"新版本号必须是 {next_version}, 实际 "
                        f"{order.version}")
                now = utc_iso_now()
                session.add(self._version_row(
                    order, source=order.source,
                    parent_version=current, note=note, created_at=now,
                    catalog_version=catalog_version,
                    planner_policy_version=planner_policy_version,
                    planner_input=planner_input,
                    planner_explain=planner_explain,
                    planner_candidate_count=planner_candidate_count))
                header.current_version = next_version
                header.updated_at = now
            return self.get_order(order.order_id, session=session)

    def _version_row(self, order: OrderSpec, *, source: str,
                     parent_version: Optional[int], note: str,
                     created_at: str, catalog_version: str = "",
                     planner_policy_version: str = "",
                     planner_input: Optional[dict] = None,
                     planner_explain: Optional[dict] = None,
                     planner_candidate_count: Optional[int] = None
                     ) -> OrderVersion:
        return OrderVersion(
            order_id=order.order_id, version=int(order.version),
            contract_version=order.contract_version,
            scene_id=order.scene_id, source=str(source or order.source),
            dag_digest=order.dag_digest(),
            spec_json=order.canonical_json(),
            catalog_version=str(catalog_version or "") or None,
            parent_version=parent_version,
            planner_policy_version=(str(planner_policy_version or "")
                                    or None),
            planner_input_json=planner_input,
            planner_explain_json=planner_explain,
            planner_candidate_count=(
                None if planner_candidate_count is None
                else int(planner_candidate_count)),
            created_at=created_at, note=note or None)

    def get_order(self, order_id: str, *,
                  session: Optional[Session] = None) -> dict:
        if session is None:
            with self.database.session() as own:
                return self.get_order(order_id, session=own)
        header = session.get(OrderHeader, str(order_id))
        if header is None:
            raise OrderNotFoundError(f"订单 {order_id!r} 不存在")
        versions = list(session.scalars(select(OrderVersion).where(
            OrderVersion.order_id == header.order_id).order_by(
            OrderVersion.version)))
        latest_run = session.scalars(select(OrderRun).where(
            OrderRun.order_id == header.order_id).order_by(
            OrderRun.started_at.desc(), OrderRun.run_id.desc()).limit(1)
        ).one_or_none()
        return {
            "order_id": header.order_id,
            "current_version": int(header.current_version),
            "source": header.source,
            "created_at": header.created_at,
            "updated_at": header.updated_at,
            "note": header.note or "",
            "versions": [_version_meta(row) for row in versions],
            "latest_run": None if latest_run is None else _run_meta(
                latest_run),
        }

    def list_orders(self, *, limit: int = 50) -> list[dict]:
        with self.database.session() as session:
            rows = list(session.scalars(select(OrderHeader).order_by(
                OrderHeader.updated_at.desc(),
                OrderHeader.order_id).limit(max(1, int(limit)))))
            results = []
            for header in rows:
                run = session.scalars(select(OrderRun).where(
                    OrderRun.order_id == header.order_id).order_by(
                    OrderRun.started_at.desc(),
                    OrderRun.run_id.desc()).limit(1)).one_or_none()
                results.append({
                    "order_id": header.order_id,
                    "current_version": int(header.current_version),
                    "source": header.source,
                    "created_at": header.created_at,
                    "updated_at": header.updated_at,
                    "latest_run": None if run is None else _run_meta(run),
                })
            return results

    def get_version(self, order_id: str, version: int) -> dict:
        with self.database.session() as session:
            row = session.get(OrderVersion, (str(order_id), int(version)))
            if row is None:
                raise OrderVersionNotFoundError(
                    f"订单 {order_id!r} 没有版本 {version}")
            spec = self._spec_from_row(row)
            planner = None
            if row.planner_policy_version:
                planner = {
                    "policy_version": row.planner_policy_version,
                    "input": row.planner_input_json,
                    "explain": row.planner_explain_json,
                    "candidate_count": row.planner_candidate_count,
                }
            return {**_version_meta(row), "spec": spec.as_dict(),
                    "planner": planner}

    def get_spec(self, order_id: str, version: int) -> OrderSpec:
        with self.database.session() as session:
            row = session.get(OrderVersion, (str(order_id), int(version)))
            if row is None:
                raise OrderVersionNotFoundError(
                    f"订单 {order_id!r} 没有版本 {version}")
            return self._spec_from_row(row)

    def list_versions(self, order_id: str) -> list[dict]:
        with self.database.session() as session:
            if session.get(OrderHeader, str(order_id)) is None:
                raise OrderNotFoundError(f"订单 {order_id!r} 不存在")
            rows = session.scalars(select(OrderVersion).where(
                OrderVersion.order_id == str(order_id)).order_by(
                OrderVersion.version))
            return [_version_meta(row) for row in rows]

    def _spec_from_row(self, row: OrderVersion) -> OrderSpec:
        try:
            spec = OrderSpec.from_dict(json.loads(row.spec_json))
        except (ValueError, TypeError, OrderSpecError) as exc:
            raise OrderIntegrityError(
                f"订单 {row.order_id!r}@{row.version} spec 解析失败: "
                f"{type(exc).__name__}: {exc}") from exc
        if spec.order_id != row.order_id or int(spec.version) != int(row.version):
            raise OrderIntegrityError(
                f"订单版本身份不一致: row={row.order_id}@{row.version} "
                f"spec={spec.order_id}@{spec.version}")
        if spec.dag_digest() != row.dag_digest:
            raise OrderIntegrityError(
                f"订单 {row.order_id!r}@{row.version} digest 不一致: "
                f"stored={row.dag_digest} computed={spec.dag_digest()}")
        if spec.contract_version != row.contract_version:
            raise OrderIntegrityError(
                f"订单 {row.order_id!r}@{row.version} 契约版本不一致")
        return spec

    # ── 运行关联 ────────────────────────────────────────────

    def create_run(self, *, run_id: str, order_id: str, version: int,
                   dag_digest: str, artifact_id: str = "",
                   status: str = "STARTING") -> dict:
        with self.database.session() as session:
            with session.begin():
                if session.get(OrderRun, str(run_id)) is not None:
                    raise OrderRunConflictError(
                        f"run_id {run_id!r} 已存在")
                version_row = session.get(
                    OrderVersion, (str(order_id), int(version)))
                if version_row is None:
                    raise OrderVersionNotFoundError(
                        f"订单 {order_id!r} 没有版本 {version}")
                if version_row.dag_digest != str(dag_digest):
                    raise OrderIntegrityError(
                        f"运行 digest {dag_digest!r} 与订单版本 "
                        f"{version_row.dag_digest!r} 不一致")
                session.add(OrderRun(
                    run_id=str(run_id), order_id=str(order_id),
                    order_version=int(version), dag_digest=str(dag_digest),
                    artifact_id=str(artifact_id or ""), status=str(status),
                    failure_scope=None, stop_reason=None,
                    started_at=utc_iso_now(), finished_at=None,
                    result_json=None))
            return self.get_run(run_id)

    def update_run(self, run_id: str, *, status: str,
                   failure_scope: Optional[str] = None,
                   stop_reason: str = "",
                   result_json: Optional[dict] = None,
                   finished_at: Optional[str] = None) -> dict:
        if status not in ORDER_STATUSES:
            raise OrderStateError(f"未知订单运行状态: {status!r}")
        terminal_statuses = ("SUCCEEDED", "PARTIAL_FAILED", "FAILED",
                             "ABORTED")
        terminal = status in terminal_statuses
        now = finished_at or utc_iso_now()
        with self.database.session() as session:
            with session.begin():
                statement = update(OrderRun).where(
                    OrderRun.run_id == str(run_id))
                if terminal:
                    # 相同终态幂等; 已结束且状态不同则拒绝.
                    statement = statement.where(or_(
                        OrderRun.status.notin_(terminal_statuses),
                        OrderRun.status == str(status)))
                else:
                    # STARTING/RUNNING 只允许更新未终态行; 避免与 worker
                    # 终态写入竞争时把 finished_at 留在 RUNNING 行.
                    statement = statement.where(
                        OrderRun.status.notin_(terminal_statuses))
                result = session.execute(statement.values(
                    status=str(status),
                    failure_scope=str(failure_scope or "") or None,
                    stop_reason=str(stop_reason or "") or None,
                    result_json=result_json,
                    finished_at=now if terminal else None))
                if result.rowcount == 0:
                    row = session.get(OrderRun, str(run_id))
                    if row is None:
                        raise OrderRunNotFoundError(
                            f"订单运行 {run_id!r} 不存在")
                    raise OrderRunConflictError(
                        f"订单运行 {run_id!r} 已终态 {row.status}, 不能改为 "
                        f"{status}")
            return self.get_run(run_id)

    def record_nodes(self, run_id: str, nodes: Iterable[dict]) -> int:
        rows = list(nodes)
        now = utc_iso_now()
        with self.database.session() as session:
            with session.begin():
                run = session.get(OrderRun, str(run_id))
                if run is None:
                    raise OrderRunNotFoundError(
                        f"订单运行 {run_id!r} 不存在")
                session.execute(delete(OrderNodeRun).where(
                    OrderNodeRun.run_id == str(run_id)))
                for row in rows:
                    session.add(OrderNodeRun(
                        run_id=str(run_id),
                        instance_id=str(row.get("instance_id") or ""),
                        node_id=str(row.get("node_id") or ""),
                        template_id=str(row.get("template_id") or ""),
                        uid=int(row.get("uid") or 0),
                        state=str(row.get("state") or ""),
                        failure_scope=(str(row.get("failure_scope") or "")
                                       or None),
                        failure_code=(str(row.get("failure_code") or "")
                                      or None),
                        failure_reason=(str(row.get("failure_reason") or "")
                                        or None),
                        blocked_by_json=list(row.get("blocked_by") or []),
                        input_uids_json=[int(value)
                                         for value in row.get("input_uids")
                                         or []],
                        output_uids_json=[int(value)
                                          for value in row.get("output_uids")
                                          or []],
                        evidence_json=row.get("evidence") or None,
                        updated_at=now))
        return len(rows)

    def get_run(self, run_id: str) -> dict:
        with self.database.session() as session:
            row = session.get(OrderRun, str(run_id))
            if row is None:
                raise OrderRunNotFoundError(
                    f"订单运行 {run_id!r} 不存在")
            nodes = list(session.scalars(select(OrderNodeRun).where(
                OrderNodeRun.run_id == row.run_id).order_by(
                OrderNodeRun.node_id, OrderNodeRun.instance_id)))
            return {
                **_run_meta(row),
                "node_count": len(nodes),
                "failed_count": sum(
                    1 for node in nodes if node.state == "FAILED"),
                "cancelled_count": sum(
                    1 for node in nodes if node.state == "CANCELLED"),
                "nodes": [_node_meta(node) for node in nodes],
            }

    def list_runs(self, *, order_id: str = "",
                  limit: int = 50) -> list[dict]:
        with self.database.session() as session:
            statement = select(OrderRun)
            if order_id:
                statement = statement.where(
                    OrderRun.order_id == str(order_id))
            rows = session.scalars(statement.order_by(
                OrderRun.started_at.desc(), OrderRun.run_id.desc()).limit(
                max(1, int(limit))))
            return [_run_meta(row) for row in rows]

    def run_nodes(self, run_id: str) -> list[dict]:
        with self.database.session() as session:
            if session.get(OrderRun, str(run_id)) is None:
                raise OrderRunNotFoundError(
                    f"订单运行 {run_id!r} 不存在")
            rows = session.scalars(select(OrderNodeRun).where(
                OrderNodeRun.run_id == str(run_id)).order_by(
                OrderNodeRun.node_id, OrderNodeRun.instance_id))
            return [_node_meta(row) for row in rows]

    def run_events(self, run_id: str, *, limit: int = 2000) -> list[dict]:
        with self.database.session() as session:
            if session.get(OrderRun, str(run_id)) is None:
                raise OrderRunNotFoundError(
                    f"订单运行 {run_id!r} 不存在")
            rows = session.scalars(select(RunEventRecord).where(
                RunEventRecord.run_id == str(run_id)).order_by(
                RunEventRecord.sim_time, RunEventRecord.event_id).limit(
                max(1, int(limit))))
            return [{
                "event_id": row.event_id,
                "sim_time": row.sim_time,
                "kind": row.kind,
                "task_id": row.task_id,
                "uid": row.uid,
                "item_type": row.item_type,
                "zone": row.zone,
                "source": row.source,
                "destination": row.destination,
                "device": row.device,
                "evidence_id": row.evidence_id,
                "instance_id": row.instance_id,
                "detail": row.detail_json or {},
            } for row in rows]


def _version_meta(row: OrderVersion) -> dict:
    return {
        "order_id": row.order_id,
        "version": int(row.version),
        "contract_version": row.contract_version,
        "scene_id": row.scene_id,
        "source": row.source,
        "dag_digest": row.dag_digest,
        "catalog_version": row.catalog_version or "",
        "parent_version": row.parent_version,
        "planner_policy_version": row.planner_policy_version or "",
        "planner_candidate_count": row.planner_candidate_count,
        "created_at": row.created_at,
        "note": row.note or "",
    }


def _run_meta(row: OrderRun) -> dict:
    return {
        "run_id": row.run_id,
        "order_id": row.order_id,
        "order_version": int(row.order_version),
        "dag_digest": row.dag_digest,
        "artifact_id": row.artifact_id or "",
        "status": row.status,
        "failure_scope": row.failure_scope or "",
        "stop_reason": row.stop_reason or "",
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "result": row.result_json,
    }


def _node_meta(row: OrderNodeRun) -> dict:
    return {
        "instance_id": row.instance_id,
        "node_id": row.node_id,
        "template_id": row.template_id,
        "uid": int(row.uid),
        "state": row.state,
        "failure_scope": row.failure_scope or "",
        "failure_code": row.failure_code or "",
        "failure_reason": row.failure_reason or "",
        "blocked_by": list(row.blocked_by_json or []),
        "input_uids": list(row.input_uids_json or []),
        "output_uids": list(row.output_uids_json or []),
        "evidence": row.evidence_json or {},
        "updated_at": row.updated_at,
    }


__all__ = ["OrderRepository"]
