"""persistence 错误类型.

错误码用于 CLI/报告定位; 所有异常都表示持久化边界内的明确失败, 不表示
仿真运行状态. 失败迁移不得把 release 标记为已发布.
"""

from __future__ import annotations


class CatalogPersistenceError(RuntimeError):
    """持久化边界的基础异常."""

    code = "CATALOG_PERSISTENCE"


class MigrationError(CatalogPersistenceError):
    """schema 迁移失败或数据库版本不可识别."""

    code = "CATALOG_MIGRATION"


class ReleaseNotFoundError(CatalogPersistenceError):
    """指定 release 不存在."""

    code = "CATALOG_RELEASE_NOT_FOUND"


class NoPublishedReleaseError(CatalogPersistenceError):
    """数据库中没有已发布 release."""

    code = "CATALOG_NO_PUBLISHED_RELEASE"


class ReleaseExistsError(CatalogPersistenceError):
    """release_id 已被占用; 已发布数据不原地改写."""

    code = "CATALOG_RELEASE_EXISTS"


class ReleaseStateError(CatalogPersistenceError):
    """release 状态不允许当前操作 (例如发布已 superseded 的版本)."""

    code = "CATALOG_RELEASE_STATE"


class CatalogIntegrityError(CatalogPersistenceError):
    """数据库内容与源目录/来源摘要不一致, 或领域校验失败."""

    code = "CATALOG_INTEGRITY"


class KnowledgeError(CatalogPersistenceError):
    """知识图谱导入/发布的基础异常."""

    code = "KNOWLEDGE"


class KnowledgeImportError(KnowledgeError):
    """导出文件无来源、结构非法或与来源摘要不一致."""

    code = "KNOWLEDGE_IMPORT"


class KnowledgeStateError(KnowledgeError):
    """抽取运行状态不允许当前操作, 或存在未审核的冲突."""

    code = "KNOWLEDGE_STATE"


class RunRecordError(CatalogPersistenceError):
    """运行记录写入或查询失败."""

    code = "RUN_RECORD"


class RunNotFoundError(RunRecordError):
    """指定 run_id 不存在, 或与记录内容不一致."""

    code = "RUN_NOT_FOUND"


class OrderError(CatalogPersistenceError):
    """订单持久化边界的基础异常."""

    code = "ORDER_PERSISTENCE"


class OrderNotFoundError(OrderError):
    code = "ORDER_NOT_FOUND"


class OrderVersionNotFoundError(OrderError):
    code = "ORDER_VERSION_NOT_FOUND"


class OrderVersionConflictError(OrderError):
    """订单版本已存在或当前版本指针与请求不一致."""

    code = "ORDER_VERSION_CONFLICT"


class OrderIntegrityError(OrderError):
    """存储的订单 spec 与 digest/身份不一致."""

    code = "ORDER_INTEGRITY"


class OrderRunNotFoundError(OrderError):
    code = "ORDER_RUN_NOT_FOUND"


class OrderRunConflictError(OrderError):
    """实例已有活动订单运行, 或运行状态不允许当前操作."""

    code = "ORDER_RUN_CONFLICT"


class OrderStateError(OrderError):
    code = "ORDER_STATE"
