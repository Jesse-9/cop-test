---
title: 本地数据库查看指南
type: reference
status: review
created: 2026-11-06
updated: 2026-11-06
scope: var/catalog/catalog.db 的 CLI、仪表盘和原始 SQLite 查看方式
related:
  - docs/wiki/98-ProjectUsage/USAGE.md
  - docs/implementation/reports/data-platform/v1-catalog-persistence.md
  - docs/implementation/reports/data-platform/v1-knowledge-graph-import.md
  - docs/implementation/reports/data-platform/v1-run-record-persistence.md
---

# 本地数据库查看指南

本页汇总“怎么查看本地 SQLite 数据库内容”。数据库是本地运行数据，
默认路径 `var/catalog/catalog.db`，已被 `.gitignore` 忽略；它不是受保护的
场景工件，也不是正式验收证据本身。

## 1. 三组数据

| 数据组 | 主要表 | 权威说明 |
|---|---|---|
| 静态工艺目录 | `catalog_release`、`recipe`、`operation_template`、`route_variant`、`resource`、`device`、`zone`、`order_entry` 等 | [静态目录持久化报告](../../reports/data-platform/v1-catalog-persistence.md) |
| 知识图谱 | `source_document`、`extraction_run`、`knowledge_entity`、`knowledge_relation`、`entity_source`、`relation_source` | [知识图谱导入报告](../../reports/data-platform/v1-knowledge-graph-import.md) |
| 运行记录 | `production_run`、`operation_instance`、`material_instance`、`material_genealogy`、`resource_event`、`run_event` | [运行记录报告](../../reports/data-platform/v1-run-record-persistence.md) |

迁移版本由 `alembic_version` 记录；当前 head 为 `0003_run_records`。

## 2. 最快方式：HTML 仪表盘

```powershell
uv run python .\tools\db_dashboard.py `
  --db .\var\catalog\catalog.db `
  --out .\docs\implementation\runs\db_dashboard.html `
  --open
```

仪表盘包含：

- 知识图谱（按类型分组、搜索、节点/关系表格）；
- 工艺目录：工序依赖 DAG、点击工序详情、路线卡片、资源申请顺序、
  设备/能力、区域、物料、订单入口、场景绑定；
- 运行记录：Q1 汇总、Q2 谱系、Q3 资源等待、Q4 工序时间线；
- 目录 release、知识来源与抽取状态。

页面是只读快照；数据变化后重新执行生成命令。工艺目录为空时，先执行
`catalog import` 和 `catalog publish`。

## 3. CLI 查询

```powershell
# 目录 release、结构/往返校验、代码/数据库影子对比
uv run flexible-factory catalog status
uv run flexible-factory catalog verify
uv run flexible-factory catalog diff

# 运行记录
uv run flexible-factory report runs
uv run flexible-factory report run --run-id <run_id>
uv run flexible-factory report genealogy --run-id <run_id> --uid <final_uid>
uv run flexible-factory report waits --run-id <run_id>
uv run flexible-factory report timeline --run-id <run_id>

# 知识图谱抽取
uv run flexible-factory knowledge status
uv run flexible-factory knowledge verify --extraction-id <extraction_id>
```

不传 `--run-id` 时 `report` 使用最近一次运行；`--db` 可覆盖数据库路径。

## 4. 原始 SQLite 查询

数据库是普通 SQLite；可用 DB Browser for SQLite，或用仓库虚拟环境中的
Python：

```powershell
.\.venv\Scripts\python.exe -I - <<'PY'
import sqlite3
con = sqlite3.connect("var/catalog/catalog.db")
print(con.execute(
    "select name from sqlite_master where type='table' order by name"
).fetchall())
for table in ("catalog_release", "operation_template", "knowledge_entity",
              "production_run"):
    print(table, con.execute(f"select count(*) from {table}").fetchone()[0])
PY
```

常用查询：

```sql
-- 已发布工艺目录
select release_id, catalog_version, status, published_at
from catalog_release order by created_at;

-- 工序与执行器
select operation_id, kind, executor_kind, priority, resource_policy
from operation_template order by recipe_id, ordinal;

-- 资源申请顺序
select resource_id, kind, capacity, role
from resource order by acquire_order;

-- 运行记录
select run_id, scenario, status, all_ok, sim_started_s, sim_finished_s
from production_run order by started_at desc;

-- 知识实体类型
select entity_type, count(*) from knowledge_entity group by entity_type;
```

SQLite 默认不启用外键；`flexible_factory.persistence.db` 的应用连接会启用
并核验 `PRAGMA foreign_keys=ON`。直接用外部工具查看时只读即可。

## 5. Neo4j Browser（仅知识图谱）

本地 Neo4j 容器运行时可打开 `http://localhost:7474`，用项目本地密码登录：

```cypher
MATCH p=()-[]->() RETURN p LIMIT 100;
MATCH (i:Instance)-[:achieve_goal]->(g:Goal) RETURN i, g LIMIT 50;
```

Neo4j 是读取原始 dump 和人工查询的工具；SQLite 知识表才是导入后的权威
结果。载入/导出步骤见 [Neo4j 工具说明](../../../../tools/neo4j/README.md)。

## 6. 注意

- 数据库路径默认 `var/catalog/catalog.db`，可用 `--db` 指向其他位置；
  不要写入 `docs/scene_artifacts/`。
- 默认 `run` 使用代码内目录，只有 `--catalog-source db` 才读取数据库目录。
- 运行记录在运行开始/结束批量写入，不在 `sim.step()` 热路径写库。
- 不要手工修改已发布 release 来“修复”数据；重新导入/发布并保留旧版本。
