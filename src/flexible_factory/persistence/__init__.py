"""persistence — v1 静态资源/工艺目录的 SQLite 持久化边界.

本包只负责关系数据映射与事务, 不导入 ``simulation`` 或 ``application``:

  - :mod:`models`      SQLAlchemy 2.x ORM 映射 (schema 见 Alembic 迁移);
  - :mod:`db`          Engine/Session 工厂、外键 PRAGMA、迁移入口;
  - :mod:`records`     非工艺目录静态数据的纯数据记录;
  - :mod:`importer`    ``CatalogSeed`` → staging release (单事务);
  - :mod:`repository`  已发布 release → 领域 ``ProcessCatalog``;
  - :mod:`service`     初始化/导入/发布/读取的应用无关服务函数;
  - :mod:`diff`        代码目录与数据库目录的稳定序列化差异.

``domain`` 不导入本包; ORM 对象不越过本包边界.
"""

from __future__ import annotations

from .db import CatalogDatabase, initialize_database
from .errors import (
    CatalogIntegrityError,
    CatalogPersistenceError,
    MigrationError,
    NoPublishedReleaseError,
    ReleaseExistsError,
    ReleaseNotFoundError,
    ReleaseStateError,
)
from .importer import CatalogImporter, ImportResult
from .records import (CatalogSeed, MaterialTypeRecord, ResourceRecord,
                      SceneBindingRecord, seed_digest)
from .repository import CatalogRepository
from .service import (load_catalog, publish_release, release_status,
                      stage_catalog)

__all__ = [
    "CatalogDatabase", "initialize_database",
    "CatalogImporter", "ImportResult", "CatalogRepository",
    "CatalogSeed", "MaterialTypeRecord", "ResourceRecord",
    "SceneBindingRecord", "seed_digest",
    "load_catalog", "publish_release", "release_status", "stage_catalog",
    "CatalogPersistenceError", "CatalogIntegrityError", "MigrationError",
    "NoPublishedReleaseError", "ReleaseExistsError", "ReleaseNotFoundError",
    "ReleaseStateError",
]
