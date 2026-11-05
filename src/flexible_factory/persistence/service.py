"""persistence.service — 初始化、导入、发布与只读查询的应用无关服务.

所有写操作都显式开启事务; 发布只切换 release 状态, 不改写子表数据. 读取
失败/摘要不一致时抛 :class:`CatalogIntegrityError`, 不回退到代码内目录.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select, text

from .clock import utc_iso_now
from .db import CatalogDatabase, current_revision
from .errors import (NoPublishedReleaseError, ReleaseNotFoundError,
                     ReleaseStateError)
from .importer import CatalogImporter, ImportResult
from .models import CatalogRelease
from .records import CatalogSeed
from .repository import CatalogRepository, list_releases


def stage_catalog(database: CatalogDatabase, seed: CatalogSeed, *,
                  release_id: str, catalog_version: str = "") -> ImportResult:
    return CatalogImporter.stage(
        database, seed, release_id=release_id,
        catalog_version=catalog_version)


def publish_release(database: CatalogDatabase, release_id: str) -> dict:
    """在一个事务内校验 staging 内容并切换发布指针."""
    with database.session() as session:
        with session.begin():
            release = session.get(CatalogRelease, release_id)
            if release is None:
                raise ReleaseNotFoundError(
                    f"release {release_id!r} 不存在")
            if release.status == "published":
                counts = CatalogRepository(session, release_id).counts()
                return {"ok": True, "release_id": release_id,
                        "status": release.status,
                        "published_at": release.published_at,
                        "counts": counts,
                        "changed": False}
            if release.status != "staging":
                raise ReleaseStateError(
                    f"release {release_id!r} 状态为 {release.status!r}, "
                    f"只能发布 staging")
            # 发布前再次完整性检查 (摘要/领域校验/派生关系).
            CatalogRepository(session, release_id).load_seed()
            previous = list(session.scalars(select(CatalogRelease).where(
                CatalogRelease.status == "published")))
            for row in previous:
                row.status = "superseded"
            if previous:
                session.flush()
            release.status = "published"
            release.published_at = utc_iso_now()
            session.flush()
            counts = CatalogRepository(session, release_id).counts()
            return {"ok": True, "release_id": release_id,
                    "status": release.status,
                    "published_at": release.published_at,
                    "superseded": [row.release_id for row in previous],
                    "counts": counts, "changed": True}


def load_catalog(database: CatalogDatabase,
                 release_id: Optional[str] = None):
    with database.session() as session:
        return CatalogRepository(session, release_id).load_catalog()


def load_seed(database: CatalogDatabase,
              release_id: Optional[str] = None) -> CatalogSeed:
    with database.session() as session:
        return CatalogRepository(session, release_id).load_seed()


def release_status(database: CatalogDatabase,
                   release_id: Optional[str] = None) -> dict:
    with database.session() as session:
        releases = list_releases(session)
        published = session.scalars(select(CatalogRelease).where(
            CatalogRelease.status == "published")).one_or_none()
        target_id = release_id
        if target_id is None and published is not None:
            target_id = published.release_id
        if target_id is None:
            return {"revision": current_revision(database),
                    "releases": releases, "published": None,
                    "selected": None, "counts": None}
        repository = CatalogRepository(session, target_id)
        return {"revision": current_revision(database),
                "releases": releases,
                "published": None if published is None
                else published.release_id,
                "selected": {
                    "release_id": repository.release.release_id,
                    "catalog_version":
                        repository.release.catalog_version,
                    "status": repository.release.status,
                    "source_digest": repository.release.source_digest,
                    "created_at": repository.release.created_at,
                    "published_at": repository.release.published_at},
                "counts": repository.counts()}


def verify_database(database: CatalogDatabase,
                    release_id: Optional[str] = None) -> dict:
    """结构、外键、事务内容和领域往返校验; 不修改数据库."""
    errors: list[str] = []
    revision = current_revision(database)
    with database.connect() as connection:
        integrity = [str(row[0]) for row in connection.execute(
            text("PRAGMA integrity_check")).fetchall()]
        if integrity != ["ok"]:
            errors.append(f"SQLite integrity_check: {integrity}")
        fk_rows = connection.execute(
            text("PRAGMA foreign_key_check")).fetchall()
        if fk_rows:
            errors.append(
                f"外键违规 {len(fk_rows)} 条: "
                f"{[tuple(row) for row in fk_rows[:5]]}")

    selected: Optional[dict] = None
    counts: Optional[dict] = None
    try:
        with database.session() as session:
            if release_id is None:
                published = session.scalars(select(CatalogRelease).where(
                    CatalogRelease.status == "published")).one_or_none()
                if published is None:
                    raise NoPublishedReleaseError(
                        "数据库中没有已发布目录")
                target_id = published.release_id
            else:
                target_id = release_id
            repository = CatalogRepository(session, target_id)
            catalog = repository.load_catalog()
            counts = repository.counts()
            selected = {
                "release_id": repository.release.release_id,
                "catalog_version": repository.release.catalog_version,
                "status": repository.release.status,
                "source_digest": repository.release.source_digest,
                "recipes": len(catalog.recipes),
            }
    except Exception as exc:      # 完整性错误统一进入报告
        errors.append(f"{getattr(exc, 'code', type(exc).__name__)}: {exc}")

    return {"ok": not errors, "errors": errors, "revision": revision,
            "selected": selected, "counts": counts}
