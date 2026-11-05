"""application.report — 运行记录只读查询用例.

CLI 通过本模块查询 Q1–Q4; 查询只读, 不写库, 不连接 CoppeliaSim.
"""

from __future__ import annotations

from typing import Optional

from ..persistence.db import CatalogDatabase
from ..persistence.runs import list_runs, material_genealogy
from ..persistence.runs import operation_timeline, resource_waits, run_summary

__all__ = [
    "report_runs", "report_run", "report_genealogy", "report_waits",
    "report_timeline",
]


def report_runs(database: CatalogDatabase, *, limit: int = 50) -> list[dict]:
    return list_runs(database, limit=limit)


def report_run(database: CatalogDatabase,
               run_id: Optional[str] = None) -> dict:
    return run_summary(database, run_id)


def report_genealogy(database: CatalogDatabase, *, uid: int,
                     run_id: Optional[str] = None) -> dict:
    return material_genealogy(database, uid=uid, run_id=run_id)


def report_waits(database: CatalogDatabase, *,
                 run_id: Optional[str] = None,
                 resource_id: str = "") -> dict:
    return resource_waits(database, run_id=run_id, resource_id=resource_id)


def report_timeline(database: CatalogDatabase, *,
                    run_id: Optional[str] = None,
                    instance_id: str = "") -> dict:
    return operation_timeline(database, run_id=run_id,
                              instance_id=instance_id)
