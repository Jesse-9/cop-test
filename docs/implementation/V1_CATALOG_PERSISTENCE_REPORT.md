---
title: v1 资源目录数据库化实施报告 — 静态目录持久化
type: report
status: review
created: 2026-11-05
updated: 2026-11-05
scope: flexible_factory persistence / SQLAlchemy + Alembic / 静态目录
related:
  - RESOURCE_DATABASE_PLAN.md
  - ../../PROJECT_ENGINEERING_REFERENCE.md
---

# v1 资源目录数据库化实施报告：静态目录持久化

本报告记录
[资源目录数据库化与知识图谱实施方案](RESOURCE_DATABASE_PLAN.md)
阶段 0–3 在 v1 代码上的实施结果。阶段 0 的字段盘点与术语对照见
[静态目录字段盘点](V1_CATALOG_FIELD_INVENTORY.md)。本阶段只把
**静态资源与工艺目录**持久化到 SQLite，并增加显式选库能力；运行记录和
知识图谱导入已在后续阶段实施，分别见
[运行记录实施报告](V1_RUN_RECORDS_REPORT.md) 与
[知识图谱导入实施报告](V1_KNOWLEDGE_GRAPH_IMPORT_REPORT.md)。

结论：

- 阶段 1（schema/迁移）、阶段 2（目录导入/只读仓储）、阶段 3（影子对比与
  显式选库）已实现。
- 阶段 4（运行记录）已按确认的 Q1–Q4 口径另行实施，见
  [运行记录实施报告](V1_RUN_RECORDS_REPORT.md)；阶段 5 的知识图谱表与导入
  管线已实施，真实 dump 222 节点/384 关系已导入并发布；阶段 6 仅覆盖
  迁移/文档部分。
- 新增 23 个持久化测试通过；相关既有纯逻辑回归 87 个通过；完整
  `python -m pytest -q` 按方案 §7.5 未执行。
- 默认 `full_flow` 运行时仍使用代码内默认目录；
  `--catalog-source db` 只有在数据库目录与当前代码目录**逐字段等价**时才
  允许切换，否则拒绝启动。

## 1. 代码落点

| 模块 | 内容 |
|---|---|
| `persistence/models.py`（新增） | SQLAlchemy 2.x ORM 映射；全部 release 子表以 `release_id` 为复合主键首列，外键限制在同一发布版本内 |
| `persistence/db.py`（新增） | 文件型 SQLite Engine/Session 工厂、每连接 `PRAGMA foreign_keys = ON` 核验、程序化 Alembic 配置 |
| `persistence/records.py`（新增） | `CatalogSeed`、物料类型/资源/场景绑定记录及 `seed_digest()` 稳定摘要 |
| `persistence/importer.py`（新增） | `CatalogSeed` → `staging` release；单事务分层写入，写后重建 seed 并比较摘要 |
| `persistence/repository.py`（新增） | 已发布/指定 release → `ProcessCatalog`；读取时重跑领域校验、派生关系校验和摘要校验 |
| `persistence/service.py`（新增） | 初始化、导入、发布、状态、结构与往返校验 |
| `persistence/diff.py`（新增） | 代码目录与数据库目录的逐路径稳定差异 |
| `persistence/migrations/`（新增） | Alembic `env.py`、首个人工复核 revision `0001_catalog_core` |
| `application/catalog.py`（新增） | seed 装配：物料来自 `domain.rules`，目录来自 `default_process`，场景绑定来自 `device_binding`；显式选库只允许等价目录 |
| `simulation/device_binding.py`（修改） | `DeviceBinding` 增加与 `aliases` 一一对应的 `alias_roles`，用于 `scene_binding.role`；不存句柄 |
| `application/run.py`、`cli.py`（修改） | 可选 catalog 注入；新增 `catalog` 命令组和 `run --catalog-source/--catalog-db` |
| `pyproject.toml`、`uv.lock` | 增加 `sqlalchemy>=2.0.36,<3`、`alembic>=1.14,<2` |
| `alembic.ini`（新增） | 开发者手动执行 Alembic 的入口；应用不依赖它 |
| `tests/test_v1_catalog_persistence.py`（新增） | 迁移、约束、导入/发布、往返、CLI 与故障回滚 |
| `tests/test_v1_import_boundary.py`（扩展） | `domain` 不导入 SQLAlchemy/SQLite/persistence；persistence 不导入 simulation/application |
| [V1_CATALOG_FIELD_INVENTORY.md](V1_CATALOG_FIELD_INVENTORY.md)（阶段 0） | 字段来源、静态/运行时边界、顺序语义、术语对照与 dump 状态 |

## 2. Schema 与关键决策

首期表覆盖方案 §2.1：`catalog_release`、`material_type`、`resource`、
`zone`、`zone_material_type`、`capability`、`device`、`device_capability`、
`device_resource`、`scene_binding`、`recipe`、`operation_template`、
`operation_dependency`、`operation_input`、`operation_output`、
`operation_candidate_device`、`operation_resource_request`、
`operation_work_step`、`route_variant`、`route_operation`、`order_entry`。

- 所有稳定业务 ID 使用 `TEXT` 主键并带非空 `CHECK`；受控枚举/范围用
  `CHECK` 约束；时间戳保存 UTC ISO-8601 文本。
- 每个 release 至多一个 `published` 行：`catalog_release(status)` 上的
  部分唯一索引 `sqlite_where = "status = 'published'"` 提供数据库级保证。
- 业务顺序显式保存：`ordinal`/`acquire_order` 覆盖 recipes、operations、
  inputs、work steps、resources、candidates、routes、entries 与 scene
  bindings；不依赖查询返回顺序。`operation_dependency` 同时保存
  `ordinal`（在 `depends_on` 中的位置）和 `successor_ordinal`（在
  `successors` 中的位置），因此读写往返保留两端顺序。
- `device.default_line_id` 通过复合外键指向 `resource`；`route_operation`
  与 `order_entry` 通过 `(release_id, recipe_id, ...)` 组合外键保证工序
  属于同一配方。
- `recipe.join_operation_id` 不建数据库外键：`recipe` 与
  `operation_template` 互相引用会形成建表环；该引用由
  `ProcessCatalog.validate()` 和导入器在发布前核对。此例外已写入迁移
  文件头部，schema 变更时不得把它当作已约束字段。
- 未启用 SQLite `STRICT` 表：它要求列类型为 `TEXT`/`INTEGER`/`REAL`/
  `BLOB`/`ANY`，与 SQLAlchemy 通用类型映射及 Alembic 跨方言迁移不便；
  方案允许的“等价显式 CHECK 约束”已落实。
- 数据库不保存 CoppeliaSim handle、owner/waiting 队列或仿真时刻状态；
  测试显式检查表列名中不存在 `handle`/`owner`/`waiting`/`sim_time`。

## 3. 导入、发布与读取语义

1. `catalog import` 把 `default_catalog()`、`rules` 中的物料/资源/区域和
   `device_binding` 组装为 `CatalogSeed`，先做 seed 校验，再在单一事务中
   写入 `staging` release。
2. 写入后在同一事务内从数据库重建 seed，比较 `source_digest`；不一致即
   回滚。事务中途异常同样回滚（测试用注入故障验证无半成品 release）。
3. `catalog publish` 在一个事务内重新执行完整性/领域校验，把旧的
   `published` 改为 `superseded`，再发布目标 release；再次发布同一版本
   返回 `changed=false`，不会重复改写。
4. `catalog diff` 比较代码目录与指定/当前发布目录；`catalog verify` 执行
   `PRAGMA integrity_check`、`PRAGMA foreign_key_check` 和目录往返校验。
5. `run --catalog-source db` 只接受与当前代码目录 `as_dict()` 完全等价的
   已发布目录；否则在连接 CoppeliaSim 前抛出 `CATALOG_INTEGRITY`。

## 4. 运行方式

```powershell
# 一次性初始化/升级
flexible-factory catalog init

# 导入为 staging，再显式发布
flexible-factory catalog import
flexible-factory catalog publish --release-id <release_id>

# 影子对比、状态与完整性检查
flexible-factory catalog diff
flexible-factory catalog status
flexible-factory catalog verify

# 显式使用数据库目录运行（默认仍是 code）
flexible-factory run --scenario full_flow --catalog-source db
```

默认数据库路径为 `var/catalog/catalog.db`（已加入 `.gitignore`），不会写入
受保护的 `docs/scene_artifacts/`。开发者也可直接使用 Alembic：

```powershell
alembic -c alembic.ini -x db_url=sqlite:///E:/path/catalog.db upgrade head
alembic -c alembic.ini -x db_url=sqlite:///E:/path/catalog.db check
```

## 5. 验证记录

| 项目 | 结果 |
|---|---|
| `alembic upgrade head` 空库迁移 | 通过；`alembic_version = 0001_catalog_core` |
| 重复 `upgrade head` | 通过；版本行数仍为 1 |
| `downgrade base` → `upgrade head` | 通过；表集合恢复 |
| `alembic check` | `No new upgrade operations detected.` |
| 新增持久化测试 | 23 passed |
| 相关既有回归（process model/scheduling/runtime/coordinator/DAG/artifact/contract） | 87 passed |
| 导入→发布→读取往返 | `as_dict()` 与代码目录完全一致；`seed_digest` 一致 |
| 约束 | 外键、容量、重复 ID、单发布索引均由 SQLite 拒绝 |
| 故障回滚 | 事务中途注入异常后无 release 残留 |
| CLI | `catalog init/import/publish/status/diff/verify` 全部通过 |

未执行：完整 `python -m pytest -q`（方案 §7.5 暂缓）；需要 CoppeliaSim 的
focused/full_flow 验收；`run --catalog-source db` 的实机运行。因此本报告
只声明持久化与纯逻辑层面已验证，不声明实机目录切换已验证。

## 6. `fmpmm.dump` 状态

本报告只记录目录阶段的只读识别结果；后续由用户提供的 `ref/scripts/`
确认它是 **Neo4j 数据库 dump**，官方读取路径为 `neo4j-admin database
load` / `migrate`。知识图谱表、导出器和 staging 导入已在阶段 5 单独实施，
见 [知识图谱导入实施报告](V1_KNOWLEDGE_GRAPH_IMPORT_REPORT.md)。

| 属性 | 值 |
|---|---|
| 文件 | `ref/fmpmm.dump`（与本报告记录的工作区文件同一 SHA-256） |
| 大小 | 273,272 字节 |
| SHA-256 | `9413d53dd5b6206e5f6082bf134757a3aa8f28e367f2fb95bea094cfdc75e344` |
| 开头字节 | ASCII `DZV1`，其后为 `28 b5 2f fd` 字节序列 |

二进制 dump 仍不由项目自行解析；真实载入和 Bolt 导出已在阶段 5 执行
（222 节点/384 关系，导入并发布成功），细节见对应报告。

## 7. 未实施与限制

- 运行记录表已在 revision `0003_run_records` 创建，见
  [运行记录实施报告](V1_RUN_RECORDS_REPORT.md)；真实 `full_flow` 与运行
  记录的联合验收未执行。
- 知识图谱表已在 revision `0002_knowledge_graph` 创建；真实 dump 的 Docker
  载入、Bolt 导出、SQLite 导入和发布已完成（222 实体/384 关系），见
  [知识图谱导入实施报告](V1_KNOWLEDGE_GRAPH_IMPORT_REPORT.md)。
- 物料转换 `PROCESS_TRANSITIONS` 未单独建表；当前由领域规则维护，未进入
  首期数据库 schema。
- `scene_binding` 只覆盖设备别名；传送带/交接台/缓冲/输出台等 support
  绑定仍由 `device_binding.support_bindings()` 在代码中校验，尚未持久化。
- 运行时目录切换只做“完全等价”准入；没有实现数据库目录热切换、版本选择
  或部分目录发布。

## 8. 回退

- 删除 `var/catalog/catalog.db` 即可回到纯代码目录；默认运行路径不受影响。
- 已发布 release 不原地改写；`run --catalog-source db` 只读取当前
  `published` release。查看/校验历史 release 使用
  `catalog status|diff|verify --release-id <id>`，但 `run` 不接受 release
  参数；实际回退方式是停止使用数据库目录。
- 迁移失败或 `catalog verify` 失败时不切换运行时；`docs/scene_artifacts/v1/`
  下的受保护工件未被本次实现读写。
