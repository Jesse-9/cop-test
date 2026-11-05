---
title: v1 知识图谱导入实施报告 — Neo4j dump 导出与 staging
type: report
status: review
created: 2026-11-05
updated: 2026-11-05
scope: 资源目录数据库化方案阶段 5 / Neo4j 导出 / 知识表
related:
  - docs/implementation/plans/resource-catalog-database-and-knowledge-graph.md
  - docs/implementation/reports/v1-catalog-persistence.md
  - ../../tools/neo4j/README.md
---

# v1 知识图谱导入实施报告：Neo4j dump 导出与 staging

本报告记录
[资源目录数据库化与知识图谱实施方案](../plans/resource-catalog-database-and-knowledge-graph.md)
阶段 5 的实施结果。来源门槛已由用户提供的
`ref/scripts/` 和 `ref/fmpmm.dump` 满足：

- `fmpmm.dump` 是 **Neo4j 数据库 dump**，不是通用压缩包；
- 官方读取路径是 `neo4j-admin database load` + `neo4j-admin database
  migrate`，随后通过 Bolt/Cypher 只读查询；
- 真实图包含 7 类节点标签（`Instance`、`Abstraction`、`Goal`、
  `Operation`、`Parameter`、`Action`、`Capability`）和 9 类关系
  （`include`、`has_instance`、`achieve_goal`、`has_capability`、
  `operation`、`has_action`、`has_parameter`、`after`、`installed_on`）。

阶段 5 已从“待识别”转为**已实现并完成真实 dump 端到端导入**；二进制 dump
仍不自行解析。

## 1. 实施范围

| 模块 | 内容 |
|---|---|
| `persistence/knowledge_models.py`（新增） | 方案 §2.3 的 `source_document`、`extraction_run`、`knowledge_entity`、`knowledge_relation`、`entity_source`、`relation_source` |
| `persistence/migrations/versions/0002_knowledge_graph.py`（新增） | 六张表、来源/抽取幂等键、状态枚举、置信度范围、索引和复合来源主键 |
| `persistence/knowledge.py`（新增） | 导出 JSON 校验、来源登记、staging extraction、canonical ID/谓词映射、冲突/悬空/未映射报告、发布门槛 |
| `application/knowledge.py`（新增） | CLI 用例封装，不导入仿真 |
| `tools/neo4j_export.py`（新增） | 通过官方 Neo4j Python 驱动只读导出节点/关系 JSON，并记录 dump SHA-256 |
| `tools/neo4j/docker-compose.yml`、`load-dump.ps1`、`README.md`（新增） | 参考 `ref/scripts/` 的官方载入/迁移路径；密码仅从环境变量读取 |
| `cli.py`（扩展） | `knowledge import/status/verify/publish` |
| `tests/test_v1_knowledge_graph.py`（新增） | 格式校验、来源追溯、幂等、冲突、发布门槛、CLI |

## 2. 数据流

```text
ref/fmpmm.dump
  └─ neo4j-admin database load / migrate (Docker, 官方工具)
       └─ Neo4j 5.13 (只读查询)
            └─ tools/neo4j_export.py (MATCH ... RETURN)
                 └─ JSON: format/source.sha256/nodes/relationships
                      └─ flexible-factory knowledge import
                           └─ SQLite source_document + extraction_run(staging)
                                + knowledge_entity/relation
                                + entity_source/relation_source
                                     └─ knowledge publish (审核通过后)
```

SQLite 是知识事实的权威导入结果；Neo4j 是读取原始 dump 和人工查询的
工具，不参与仿真主路径。

## 3. 受控词表与冲突策略

- 已知实体类型（来自真实 dump 审核）：`Abstraction`、`Action`、
  `Capability`、`Goal`、`Instance`、`Operation`、`Parameter`；标签集合按
  排序后用 `|` 连接，多标签不会被丢弃。
- 已知谓词：
  `include → INCLUDES`、`has_instance → HAS_INSTANCE`、
  `achieve_goal → ACHIEVES_GOAL`、`has_capability → HAS_CAPABILITY`、
  `operation → HAS_OPERATION`、`has_action → HAS_ACTION`、
  `has_parameter → HAS_PARAMETER`、`after → AFTER`、
  `installed_on → INSTALLED_ON`；`source_predicate` 保留原始谓词。
- 未知标签/谓词不会静默丢弃：写入 `unmapped_entity_types` /
  `unmapped_predicates`，并阻止发布，直到审核并扩展映射。
- 实体 canonical ID 优先使用 `pro_code`，其次 `code`/`goal_id`/`uid`/
  `id`/`name`，最后回退到 `neo4j:<node_id>`。同名但无业务编码的节点
  （真实图中 44 个）用 `#<elementId>` 确定性消歧，不误判为冲突。
- 同一 canonical ID 的属性/标签不一致：保留首条，状态置为 `conflict`，
  写入冲突报告，不采用“最后写入覆盖”。
- 关系端点缺失、同一三元组属性不一致同样进入报告并阻止发布。
- 复跑幂等：来源 SHA-256 + 抽取器 ID/版本确定 `extraction_id`；同一输入
  重跑返回 `changed=false`，不重复写实体/关系/来源行。相同版本产生不同
  导出内容时拒绝导入，要求递增 `extractor_version`。

## 4. 运行命令

```powershell
# 官方载入（会覆盖本地命名卷中的图谱；需 Docker Desktop）
$env:NEO4J_AUTH = 'neo4j/<password>'
$env:NEO4J_PASSWORD = '<password>'
.\tools\neo4j\load-dump.ps1 -DumpPath .\ref\fmpmm.dump

# 只读导出
uv run python .\tools\neo4j_export.py `
  --source-dump .\ref\fmpmm.dump `
  --out .\docs\implementation\runs\fmpmm_graph.json

# SQLite staging → 审核 → 发布
flexible-factory knowledge import `
  --export .\docs\implementation\runs\fmpmm_graph.json `
  --source-dump .\ref\fmpmm.dump
flexible-factory knowledge status
flexible-factory knowledge verify --extraction-id <extraction_id>
flexible-factory knowledge publish --extraction-id <extraction_id>
```

`knowledge import` 会自动幂等升级 schema（等同 `catalog init`）。默认数据库
仍是 `var/catalog/catalog.db`。

## 5. 验证记录

| 项目 | 结果 |
|---|---|
| 迁移 `0001` → `0002` | 通过 |
| `alembic check` | `No new upgrade operations detected.` |
| 空库 → head → base → head | 通过 |
| 新增知识图谱测试 | 10 passed |
| 目录/知识/边界组合测试 | 40 passed |
| 相关聚焦回归（含运行记录） | 157 passed |
| 合成导出 staging | 6 实体/4 关系/来源行完整 |
| 幂等复跑 | 第二次 `changed=false`，行数不增长 |
| 实体冲突 | 状态 `conflict`，发布被拒绝 |
| 未映射标签/谓词 | 进入报告，发布被拒绝 |
| 干净导出发布 | 实体/关系全部 `published` |
| CLI | `knowledge import/status/verify/publish` 全部通过 |

**真实 dump 端到端已执行（2026-11-05）**：

- Docker Neo4j 5.13 载入 `ref/fmpmm.dump`
  （SHA-256 `9413d53d…75e344`），`neo4j-admin database migrate` 通过；
- 图规模：**222 节点 / 384 关系**；
- 节点标签：`Instance` 155、`Abstraction` 18、`Goal` 17、`Operation` 12、
  `Parameter` 9、`Action` 7、`Capability` 4；
- 关系类型：`include` 131、`has_instance` 68、`achieve_goal` 48、
  `has_capability` 42、`operation` 24、`has_action` 23、
  `has_parameter` 22、`after` 21、`installed_on` 5；
- `knowledge import`：222 实体、384 关系、44 个歧义 canonical ID 确定性
  消歧；无未映射类型/谓词、无冲突、无悬空关系；
- `knowledge verify`：`ok=true`；`knowledge publish`：成功，222/384 全部
  `published`，`entity_source`/`relation_source` 各 222/384 行；
- 抽查 `TESTBOX`、`TESTCOVER`、`AUR0001`、`AUR0002`、`GDH0001`、`GDH0002`
  均以 `Instance:<pro_code>` 发布；`TESTBOX -[:achieve_goal]-> Goal` 在
  Neo4j 与 SQLite 中可对应查询（含 `id(g)=90` 的目标）。

证据摘要：[evidence/v1_knowledge_graph_import_20261105.json](../evidence/v1_knowledge_graph_import_20261105.json)。

**仍未执行**：真实 `full_flow` 与运行记录的联合验收；Docker 环境外的新机器
需要重新执行载入/导出。

## 6. 与 ref 脚本的差异

- 不硬编码 Neo4j 密码；只从 `NEO4J_AUTH`/`NEO4J_PASSWORD` 读取。
- 项目命名：`cop-test-neo4j` / `cop-test-neo4j-data`，避免误操作其他项目的
  容器和卷。
- 载入脚本增加输入确认（`-Force` 可跳过），因为 `--overwrite-destination`
  会覆盖已有本地图谱。
- 导出由 Python 驱动完成，输出结构化 JSON，而不是仅靠手工 `cypher-shell`
  查看；这满足方案 §4/§5 的来源、摘要、原始 ID 和可重复导入要求。
- `ref/` 目录保持只读参考，不修改；它是用户本地提供的资料，不随仓库
  版本控制发布。新环境没有 `ref/fmpmm.dump` 时只能使用已有的导出 JSON，
  不能伪造真实 dump 导入结果。

## 7. 阶段 4 与阶段 6 状态

- **阶段 4 已实施**：按用户确认的 Q1–Q4 口径新增 `0003_run_records`、
  `RunRecorder` 和 `report` 查询，见
  [运行记录实施报告](v1-run-record-persistence.md)。真实 `full_flow` 与运行
  记录的联合验收未执行。
- **阶段 6 部分覆盖**：迁移测试、来源摘要、发布门槛和工具文档已落地；
  备份/恢复演练和运行记录保留策略未实施（当前全量保留、无删除命令）。

## 8. 限制与风险

- 真实 dump 的标签/谓词已按实际图审核并映射；未来 dump 若出现新标签/
  谓词，未映射项会阻止发布，这是预期的审核门槛，不是静默成功。
- Neo4j `elementId()` 用于来源定位和歧义消解；canonical ID 优先使用业务
  属性（`pro_code` 等），只有同名且无业务编码的节点才附加来源 ID。
- 导出 JSON 全量载入内存；当前 dump 约 267 KB（222 节点/384 关系），规模
  可接受；更大图需改为流式导出/分批导入。
- `Abstraction` 等节点使用 `name` 作为 canonical key；重名节点用来源 ID
  消歧，跨不同 dump 的 canonical ID 不保证一致。
- 未实现 Neo4j 作为运行时查询后端；SQLite 知识表仍是权威导入结果。
