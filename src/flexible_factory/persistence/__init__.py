"""persistence — v1 静态资源/工艺目录、知识图谱与运行记录的持久化边界.

本包只负责关系数据映射与事务, 不导入 ``simulation`` 或 ``application``:

  - :mod:`models`      SQLAlchemy 2.x ORM 映射 (schema 见 Alembic 迁移);
  - :mod:`knowledge_models`  知识实体/关系与来源追溯表;
  - :mod:`db`          Engine/Session 工厂、外键 PRAGMA、迁移入口;
  - :mod:`records`     非工艺目录静态数据的纯数据记录;
  - :mod:`importer`    ``CatalogSeed`` → staging release (单事务);
  - :mod:`repository`  已发布 release → 领域 ``ProcessCatalog``;
  - :mod:`knowledge`   Neo4j 导出 JSON → staging knowledge extraction;
  - :mod:`runs`        运行记录批量写入与 Q1–Q4 查询;
  - :mod:`service`     初始化/导入/发布/读取的应用无关服务函数;
  - :mod:`diff`        代码目录与数据库目录的稳定序列化差异.

``domain`` 不导入本包; ORM 对象不越过本包边界.
"""

from __future__ import annotations

from .db import CatalogDatabase, initialize_database
from .errors import (
    CatalogIntegrityError,
    CatalogPersistenceError,
    KnowledgeError,
    KnowledgeImportError,
    KnowledgeStateError,
    MigrationError,
    NoPublishedReleaseError,
    ReleaseExistsError,
    ReleaseNotFoundError,
    ReleaseStateError,
    RunNotFoundError,
    RunRecordError,
)
from .importer import CatalogImporter, ImportResult
from .knowledge import (EXPORT_FORMAT, EXTRACTOR_ID, EXTRACTOR_VERSION,
                        knowledge_status, knowledge_verify, load_export,
                        publish_extraction, stage_knowledge_export,
                        validate_export)
from .records import (CatalogSeed, MaterialTypeRecord, ResourceRecord,
                      SceneBindingRecord, seed_digest)
from .repository import CatalogRepository
from .runs import (RUN_RECORD_VERSION, RunRecorder, list_runs,
                   material_genealogy, operation_timeline, resource_waits,
                   run_summary)
from .service import (load_catalog, publish_release, release_status,
                      stage_catalog)

__all__ = [
    "CatalogDatabase", "initialize_database",
    "CatalogImporter", "ImportResult", "CatalogRepository",
    "CatalogSeed", "MaterialTypeRecord", "ResourceRecord",
    "SceneBindingRecord", "seed_digest",
    "load_catalog", "publish_release", "release_status", "stage_catalog",
    "EXPORT_FORMAT", "EXTRACTOR_ID", "EXTRACTOR_VERSION",
    "load_export", "validate_export", "stage_knowledge_export",
    "publish_extraction", "knowledge_status", "knowledge_verify",
    "RUN_RECORD_VERSION", "RunRecorder", "list_runs", "run_summary",
    "material_genealogy", "resource_waits", "operation_timeline",
    "CatalogPersistenceError", "CatalogIntegrityError", "MigrationError",
    "KnowledgeError", "KnowledgeImportError", "KnowledgeStateError",
    "RunRecordError", "RunNotFoundError",
    "NoPublishedReleaseError", "ReleaseExistsError", "ReleaseNotFoundError",
    "ReleaseStateError",
]
