---
title: 资源目录数据库化与知识图谱实施方案
type: plan
status: partially-implemented
created: 2026-11-05
updated: 2026-11-05
scope: flexible_factory resources, process catalog, material lineage, fmpmm.dump assessment
related:
  - docs/implementation/reports/v1-catalog-persistence.md
---

# 资源目录数据库化与知识图谱实施方案

## 1. 目标与决策

本文记录资源数据数据库化的架构决策、剩余融合工作和验收边界。数据库化及 Neo4j dump 导入的已完成实现以对应实施报告为准；本方案不重复列出已完成实现步骤。知识图谱用于表达实体关系、来源和查询路径，不取代运行时领域模型。

### 1.1 首选方案

- 首期使用 SQLAlchemy 2.x ORM/Core 管理 SQLite 映射、查询和事务，并使用 Alembic 管理 schema 迁移；不增加服务端关系数据库依赖。Neo4j 仍只作为后续可选图谱投影。
- SQLite 是当前单机仿真产品的本地权威数据存储。静态目录在应用启动时载入并转换成现有领域对象；运行时调度仍由内存中的 `ResourceTable`、`MaterialRegistry`、`OperationRuntime` 管理。
- 第一批持久化范围为物料类型、设备、能力、场景绑定、互斥资源、物料区域、配方/工序/路线及它们的显式关系。运行记录与知识抽取分后续阶段进入。
- 关系表是可查询业务数据和关系事实的权威表示。JSON 只存非核心扩展属性、来源原文/片段和交换快照；不得用 JSON 数组替代需要约束或查询的外键关系。
- 暂不部署 Neo4j。只有明确的多跳查询、规模或图算法需求通过关系查询基准后仍无法满足时，才评估从关系表导出到 Neo4j。
- `fmpmm.dump` 已由官方 Neo4j 工具载入并完成只读导出及 SQLite staging 导入；来源、处理过程和验证证据见[知识图谱导入报告](../reports/v1-knowledge-graph-import.md)。这里的 `fmpmm` 是文件/来源项目标识，不作为通用领域标准名称使用。

### 1.2 当前项目边界

当前 `domain/process_model.py` 已定义静态配方、工序、路线、订单入口、设备与能力目录；`domain/resources.py` 是固定顺序、全有或全无的运行时互斥锁；`domain/materials.py` 管理 UID、物料谱系和区域；`artifacts/store.py` 管理版本化场景工件。数据库实现放在新建的 `src/flexible_factory/persistence/` 边界内，不能让 `domain/` 直接导入 SQLAlchemy、SQLite、文件系统或 CLI。Repository 将 ORM 实体转换为现有领域数据类；ORM 对象不得越过 persistence/application 边界。

句柄、连接对象、仿真时刻中的临时 owner/waiting 队列和控制器对象均为运行时状态，不进入静态资源目录。持久化场景绑定时使用稳定设备 ID、场景/契约版本与 alias，不保存 CoppeliaSim handle。

## 2. 数据模型

所有稳定业务 ID 使用受约束的字符串主键（例如 `arm_A`、`dock_A`、`mixed_line_v1`）。表使用 `snake_case` 命名，时间戳统一 UTC ISO-8601，仿真时间使用以秒为单位的 REAL。删除策略默认 `RESTRICT`；目录发布采用新版本记录，不原地改写已发布版本。

### 2.1 首期静态目录表

| 表 | 核心字段 | 约束/说明 |
|---|---|---|
| `alembic_version` | Alembic 管理的当前 revision | 由 Alembic 创建和维护；schema 变更追加 revision |
| `catalog_release` | `release_id`, `catalog_version`, `status`, `source_digest`, `created_at`, `published_at` | 导入先为 `staging`；校验通过后原子发布 |
| `material_type` | `material_type_id`, `release_id`, `description`, `color_json` | `(release_id, material_type_id)` 唯一 |
| `device` | `device_id`, `release_id`, `kind`, `physical`, `description` | 物理设备和逻辑处理器均可表达；逻辑设备不伪装为场景对象 |
| `capability` | `capability_id`, `release_id`, `description` | 能力词表按发布版本管理 |
| `device_capability` | `device_id`, `capability_id` | 复合主键；两端外键 |
| `scene_binding` | `binding_id`, `device_id`, `scene_id`, `contract_version`, `role`, `alias` | alias 在同一场景/角色范围按项目契约唯一；不存 handle |
| `resource` | `resource_id`, `release_id`, `kind`, `role`, `capacity`, `acquire_order`, `policy` | 资源锁定义，与 `device`、`zone` 分开；capacity > 0 |
| `device_resource` | `device_id`, `resource_id`, `relation_kind` | 表达设备与其占用/关联的资源，不按同名 ID 推断 |
| `zone` | `zone_id`, `release_id`, `capacity`, `description` | 物料位置/容量，不等同互斥资源 |
| `zone_material_type` | `zone_id`, `material_type_id` | 区域允许的物料类型关联 |
| `recipe` | `recipe_id`, `release_id`, `version`, `product_type`, `join_operation_id` | `(release_id, recipe_id, version)` 唯一 |
| `operation_template` | `operation_id`, `recipe_id`, `kind`, `executor_kind`, `capability_id`, `priority`, `resource_policy`, `predecessor_policy`, `completion_verifier` | 受控枚举字段；引用已登记执行路径 ID，不存可执行代码 |
| `operation_dependency` | `operation_id`, `predecessor_operation_id` | 复合主键，禁止自环；导入校验检测更长环路 |
| `operation_input` | `operation_id`, `ordinal`, `zone_id`, `material_type_id` | `(operation_id, ordinal)` 唯一，顺序明确 |
| `operation_output` | `operation_id`, `zone_id`, `material_type_id` | 每个工序 0..1 个输出；无输出用无记录表达 |
| `operation_candidate_device` | `operation_id`, `device_id`, `ordinal` | 候选设备顺序显式保存 |
| `operation_resource_request` | `operation_id`, `resource_id`, `quantity` | `quantity > 0`；固定/路线动态策略由策略字段区分 |
| `operation_work_step` | `operation_id`, `ordinal`, `template_id` | 只引用已登记工步执行路径 ID |
| `route_variant` | `recipe_id`, `route_id`, `route_group`, `entry_zone_id`, `exit_zone_id` | `(recipe_id, route_id)` 唯一；同组互斥关系由目录校验 |
| `route_operation` | `recipe_id`, `route_id`, `ordinal`, `operation_id` | 按 ordinal 保留有序路线；组合外键保证同配方 |
| `order_entry` | `recipe_id`, `entry_id`, `operation_id`, `line_id`, `material_type_id`, `route_group`, `route_id` | 静态入口定义，不代表当前已有生产订单实体 |

数据库采用 SQLite `STRICT` 表（运行 SQLite 版本满足要求时）或等价的显式 `CHECK` 约束。SQLAlchemy Engine 的 connect hook 统一启用 `PRAGMA foreign_keys = ON`，并检查实际值；SQLite 默认不启用外键，不能只依赖 ORM relationship。建表时明确 `NOT NULL`、唯一性、外键和范围约束；Python 领域校验继续负责跨行不变量和拓扑无环验证。SQLAlchemy 提供 ORM 映射及 Core 表达能力；批量 seed/import 可用 Core 批量语句，不为追求“全 ORM”牺牲清晰性。

### 2.2 后续运行记录表（不进入首期）

只有运行审计、跨进程查询或结果再分析成为明确需求后，才增加以下表：

| 表 | 主要用途 |
|---|---|
| `production_run` | 一次仿真批次、目录发布版本、场景工件 ID 和运行结果 |
| `operation_instance` | 工序实例 ID、运行/配方/模板关联、选定设备、状态与生命周期时间 |
| `material_instance` | 物料 UID、类型、状态、当前位置及所属运行 |
| `material_genealogy` | 父 UID → 子 UID 的物料谱系边及产生事件 |
| `resource_event` | 批量记录 ACQUIRE/WAIT/RELEASE 等资源事件 |
| `run_event` | 可重算指标的领域事件及验证证据引用 |

不要在仿真 `step()` 的热路径逐步写库。运行中保持现有内存状态；在明确的提交边界按批写入事件，或在运行结束后写入汇总。若新增批写入，必须规定批次大小、失败处理和是否允许丢弃；首期选择不丢事件，批次写入失败则使运行记录明确标记失败，不能静默当作成功。

### 2.3 知识图谱表（抽取阶段再加）

| 表 | 字段建议 |
|---|---|
| `source_document` | `source_id`, `uri_or_path`, `media_type`, `sha256`, `source_version`, `registered_at` |
| `extraction_run` | `extraction_id`, `source_id`, `extractor_id`, `extractor_version`, `status`, `started_at`, `finished_at`, `report_json` |
| `knowledge_entity` | `entity_id`, `entity_type`, `canonical_id`, `label`, `attributes_json`, `status` |
| `knowledge_relation` | `relation_id`, `subject_id`, `predicate`, `object_id`, `attributes_json`, `status` |
| `entity_source` | `entity_id`, `source_id`, `source_locator`, `raw_fragment`, `confidence`, `extraction_id` |
| `relation_source` | `relation_id`, `source_id`, `source_locator`, `raw_fragment`, `confidence`, `extraction_id` |

实体/关系需要稳定 ID、受控类型、来源定位和导入批次。建议的关系词表从当前领域需要开始：`PROVIDES_CAPABILITY`、`USES_RESOURCE`、`HAS_INPUT`、`PRODUCES`、`PRECEDES`、`ROUTE_CONTAINS`、`BINDS_TO_SCENE_OBJECT`、`DERIVED_FROM`。只有在来源明确支持该关系时才创建；推断关系要单独标记状态和置信度，不与已确认事实混淆。

## 3. 现有数据映射

| 当前来源 | 数据库目标 | 迁移规则 |
|---|---|---|
| `domain/rules.py: RESOURCE_ORDER`、`RESOURCE_ROLES` | `resource` | 顺序存入 `acquire_order`；保持唯一且连续/稳定的原顺序；不要从资源字符串推断设备或区域类型 |
| `domain/rules.py: ZONE_CAPACITY` | `zone` | 每个区域独立记录容量；验证 `output_e=4` 与其它容量为 1 的现状 |
| `domain/rules.py: ITEM_TYPES`、颜色、转换规则 | `material_type`，必要时另设 `material_transition` | 转换关系显式表述工位/输入类型/输出类型；不要拼入物料实例状态 |
| `domain/default_process.py` / `ProcessCatalog` | recipe、operation、route、entry 和关联表 | 以 `as_dict()` 为初始交换模型；关系字段规范化为关系表；导入后调用现有目录校验 |
| `simulation/device_binding.py`、配置中的 aliases | `scene_binding` | 保持稳定目录设备 ID 与场景 alias 分离；alias 与场景契约版本一起记录 |
| `ResourceTable` 中 owners/waiting/events | 首期不持久化；后续映射 `resource_event` | 不把一次仿真的 owner、等待队列写进静态资源定义 |
| `MaterialRegistry` 中 UID、parents、history | 后续 `material_instance`、`material_genealogy`、`run_event` | 保留 UID 事实和父子边；模拟句柄仍不持久化 |
| `docs/scene_artifacts/**/manifest.json` | `production_run` 来源或导入 provenance | 场景工件仍由 ArtifactStore 管理；数据库只引用 artifact_id 和摘要，不复制/取代工件仓库 |

首期目录发布必须能从当前 `default_catalog()` 和当前规则数据导入，再由数据库仓储读取并重建等价的 `ProcessCatalog`。以稳定序列化结果比较（忽略无语义顺序的映射键排序，但保留 routes、inputs、work steps、resources 和 candidates 的业务顺序）。

## 4. 来源知识图谱导入状态

`fmpmm.dump` 是 Neo4j 数据库 dump；它已通过官方 Neo4j 工具载入、只读导出为 JSON，并导入 SQLite staging。完整命令、实际实体/关系统计、发布状态和手工复核方式见[知识图谱导入报告](../reports/v1-knowledge-graph-import.md)。本方案只保留导入边界：二进制 dump 不由项目自行解析；源 ID、原标签/谓词和来源摘要用于追溯；Neo4j 负责读取原始 dump 或按需查询，不成为仿真运行时依赖。

仍待完成的是将 staging 中已审核的来源知识语义映射为本项目可执行 catalog 候选，详见阶段 5.1。知识抽取已发布不等于可执行 recipe 已发布；两者须分别审核和发布。

## 5. Neo4j 运行方式与定位

### 5.1 是否使用 Docker

对于本项目的开发、手工验证和知识图谱试验，Docker 更方便：它隔离 Neo4j 版本和运行环境，启动/停止/删除由一组命令完成，数据可用 named volume 持久化，Windows、macOS 和 Linux 使用方式基本一致。Neo4j 官方提供 Community/Enterprise Docker 镜像，并支持发布 Browser 的 `7474` 端口和 Bolt 的 `7687` 端口；挂载 `/data` 才能避免容器删除或重建时丢失数据库内容。参见 [Neo4j Docker 入门文档](https://neo4j.com/docs/operations-manual/current/docker/introduction/)。

本项目只把 Neo4j 作为关系库导出的派生图谱和人工查询工具。资源目录、工艺目录和运行事实的权威来源仍是关系型数据；不要让仿真主路径依赖 Neo4j 可用性。暂不部署集群、插件、Enterprise 功能或公网暴露。

### 5.2 Windows 本地快速启动

前置条件：Docker Desktop 已启动，PowerShell 中 `docker version` 能正常返回客户端和服务端信息。

```powershell
# 只需执行一次；命名卷用于保存 Neo4j 数据
docker volume create cop-test-neo4j-data

# 使用明确版本，密码替换为至少 8 个字符的实际值
docker run --detach --name cop-test-neo4j `
  --publish 7474:7474 --publish 7687:7687 `
  --env NEO4J_AUTH=neo4j/ReplaceWithYourPassword `
  --volume cop-test-neo4j-data:/data `
  neo4j:2026.10.1

docker ps --filter name=cop-test-neo4j
docker logs --follow cop-test-neo4j
```

浏览器打开 `http://localhost:7474`，使用用户 `neo4j` 和启动时设置的密码登录；应用或脚本通过 `bolt://localhost:7687` 连接。官方文档的 Docker 示例使用 `NEO4J_AUTH` 设置初始密码；如果 `/data` 已有数据库，重新设置该环境变量不会覆盖已有认证信息。

停止、启动和删除容器：

```powershell
docker stop cop-test-neo4j
docker start cop-test-neo4j
docker logs --tail 100 cop-test-neo4j

# 删除容器但保留数据卷
docker rm cop-test-neo4j

# 只有确认不再需要本地图谱时才删除数据；这是破坏性操作
docker volume rm cop-test-neo4j-data
```

### 5.3 推荐的 Compose 方式

如果需要反复启动，建议在项目外或明确的本地工具目录保存 `docker-compose.yml`。密码不要提交到 Git；使用未跟踪的 `.env` 或 Docker secret。Neo4j 官方 Docker 文档也支持按相同原则配置持久化目录、认证和端口。

```yaml
services:
  neo4j:
    image: neo4j:2026.10.1
    container_name: cop-test-neo4j
    restart: unless-stopped
    ports:
      - "7474:7474"
      - "7687:7687"
    environment:
      NEO4J_AUTH: ${NEO4J_AUTH}
    volumes:
      - cop-test-neo4j-data:/data
      - ./neo4j-import:/import:ro
      - ./neo4j-logs:/logs

volumes:
  cop-test-neo4j-data:
```

`.env` 示例（不要提交）：

```text
NEO4J_AUTH=neo4j/ReplaceWithYourPassword
```

PowerShell 操作：

```powershell
docker compose up -d
docker compose ps
docker compose logs -f neo4j
docker compose stop
docker compose start
docker compose down
```

`docker compose down` 默认保留 named volume；只有明确需要清空数据时才使用 `docker compose down --volumes`。

### 5.4 导入和查询顺序

1. 先用 Browser 或 `cypher-shell` 执行 `RETURN 1`，确认认证、HTTP 和 Bolt 都可用。
2. 在本地 `neo4j-import/` 准备经过审核的 CSV 或 Cypher 文件，并以只读方式挂载到容器的 `/import`。
3. 小规模试验优先使用 `cypher-shell` 执行幂等的 `MERGE` 脚本：

   ```powershell
   docker exec --interactive cop-test-neo4j `
     cypher-shell -u neo4j -p ReplaceWithYourPassword `
     --file /import/smoke.cypher
   ```

4. 大批量 CSV 导入才使用 `neo4j-admin database import full`；该工具需要符合 Neo4j CSV header 规范，并应在独立的 staging 数据目录执行。官方 Docker 操作文档说明可通过 `docker exec` 在容器内运行 `neo4j-admin` 和导入工具，参见 [Docker-specific operations](https://neo4j.com/docs/operations-manual/current/docker/operations/)。
5. 导入后手工检查节点/关系数量、关键关系方向、孤立节点和来源字段；用 Cypher 保存可复现的查询样例。
6. 对已确认的原始 Neo4j dump，只使用其实施报告记录的官方载入/迁移流程；不要对 dump 做项目自定义二进制解析。其他来源文件仍须先确认格式和官方恢复方式。

### 5.5 与本项目的连接边界

- Python 仓储先完成 SQLite 目录读取和校验，再由单独的导出器生成 Neo4j 节点/边数据。
- Neo4j 不参与 `ResourceTable.acquire_all()`、物料原子合流、工序 ready 判定或 CoppeliaSim stepping。
- 图谱导入失败时，SQLite 目录和仿真运行不受影响；图谱只保留上一次已发布版本。
- 每次导出记录源数据库 release、导出器版本、输入摘要、节点/关系计数和导入时间。

## 6. 详细执行步骤与验收门槛

以下步骤按顺序实施。每阶段在前一阶段验收通过后再开始依赖它的工作；变更通过代码评审和用户手工验证，不改变已保护场景工件。

### 阶段 0：盘点与数据契约（已完成）

1. 盘点 `rules.py`、`default_process.py`、`process_model.py`、`device_binding.py`、`layout.py`、`materials.py`、`resources.py`、manifest 和相关测试中的持久字段。
2. 为每个字段记录：稳定 ID、来源模块、静态/运行时属性、消费者、版本策略、可空性、顺序语义、是否持久化。
3. 对 `device`/`resource`/`zone`/`material_type`/`operation` 形成术语对照，列出同名字符串代表不同语义的情况。
4. 记录来源知识图谱格式、标签/谓词和已导入状态；静态目录字段盘点与术语对照见[字段盘点](../reference/static-catalog-field-inventory.md)，dump 导入细节见[知识图谱导入报告](../reports/v1-knowledge-graph-import.md)。

交付：字段目录、词汇表、dump 来源状态记录。验收：没有把句柄、运行时 owner、waiting 队列或物理仿真瞬时状态列入静态目录。

### 阶段 1：schema 与迁移框架（已实施）

1. 在 `pyproject.toml` 中将 SQLAlchemy 2.x、Alembic 加入依赖，并按项目既有工具更新 `uv.lock`；不增加额外 ORM 插件。
2. 新建 `src/flexible_factory/persistence/`，划分 SQLAlchemy `Base`/ORM 映射、Engine/Session 工厂、repository 和领域转换器；领域模块只接收/返回领域数据类，不依赖 ORM。
3. 初始化 Alembic 并将 ORM metadata 接入 `env.py`；创建首个 revision，实现本方案 2.1 的最小核心表与约束。运行记录/图谱表留到对应阶段新增 revision。
4. Alembic autogenerate 只生成候选迁移；每个候选都必须人工检查约束、索引、SQLite DDL 与 upgrade/downgrade 行为后才能提交。已发布 revision 不修改，schema 变更追加新 revision。
5. 在 Engine connect hook 中设置并验证 `PRAGMA foreign_keys = ON`；显式配置 SQLAlchemy Session/事务边界。SQLite 事务和 DDL 行为按项目支持的 Python/sqlite 版本确认。
6. 数据库路径通过现有应用配置/CLI 注入，手工验证使用临时目录；不得默认将数据库写入已受保护的场景工件目录。

验收：空库可由 `alembic upgrade head` 从零迁移；重复 upgrade 无副作用；非法外键/容量/重复 ID 被拒绝；失败迁移保留明确错误且 `alembic_version` 不误报成功。

### 阶段 2：目录导入与只读仓储（已实施）

1. 实现 `ProcessCatalog` → 数据库 staging release 的显式转换器；先导入 material/device/capability/resource/zone，再导入 recipe/operation/route/entry 和关联表。
2. 使用 SQLAlchemy Core 批量 insert 或 ORM unit of work，在单一 Session/事务内完成一个 staging release；任何校验失败都回滚该批次。
3. 查询仓储从已发布 release 查询 ORM 映射，并构造现有不可变领域数据类；验证外键完整性和全部领域校验后才返回目录。
4. 对比数据库往返前后的稳定 `as_dict()` 输出，并校验资源顺序、输入/输出顺序、工步顺序、路线顺序、候选设备顺序及前置策略。
5. 提供显式的初始化/导入入口；尚未迁移 CLI 前，生产仿真继续使用原默认目录，不做静默 fallback。

验收：当前默认目录可导入、读取并重建；非法引用、重复 order、循环依赖及未登记执行器 ID 均在发布前失败；读取 staging release 不会改变当前发布指针。

### 阶段 3：影子读取与切换（已实施）

1. 在测试与诊断命令中并行构建“代码目录”和“数据库目录”，对规范化序列化值做 diff；运行时先不切换。
2. 清理所有 diff：区分无语义字段顺序、明确的展示字段和真实数据差异；不能用放宽比较掩盖顺序语义差异。
3. 增加只读数据库目录选择配置；默认仍为现有行为，显式选库才进入数据库目录路径。
4. 运行纯逻辑测试及相关流程测试；验证场景配置摘要、process model 版本和已验收 full_flow 行为不变。
5. 达到发布门槛后，才将新部署默认目录来源改为数据库；保留代码内默认目录作为可重复导入的 seed 和迁移对照，直到稳定迁移策略确定。

验收：等价性 diff 为空；数据库不可用时错误清晰且不会悄悄使用过期空目录；full_flow 结果契约和工件内容未被目录读取切换意外改变。

### 阶段 4：运行记录与已确认查询契约（已实施）

阶段 4 已按实际生产排查需要确定并实现 Q1–Q4。它们分别回答“这次运行是否可信”“最终物料从哪里来”“运行被什么资源卡住”“具体哪道工序何时、由谁执行、在哪里失败”。设计时不以图查询或报表数量作为目标，而以能否复核一次 `full_flow` 的结果和异常为准。

| 查询 | 现场问题与触发场景 | 权威数据边界 | 当前支持 |
|---|---|---|---|
| Q1 运行索引与验收汇总 | 最近运行是哪次？是否 DONE、验收是否通过？产出几个 FINAL？失败/中止原因是什么？不同场景或目录来源是否可辨？ | 以 `production_run` 为权威；计数来自同 run 的工序、物料、资源和事件记录 | 已有运行列表和单次运行报告。跨运行聚合筛选 UI 暂不支持；按场景/版本/时间筛选有实际需求后再扩展。 |
| Q2 单件物料谱系与过程追溯 | 某个 FINAL/异常物料由哪些输入 UID 合流？输入物料是什么类型、从哪个入口来、在哪个工序/仿真时刻形成？相关验证事件是什么？ | `material_instance` 与有向 `material_genealogy(parent_uid → child_uid)`，关联产生工序和 UID 事件，限定同一 run | 已有按 UID 查询目标物料、祖先树、谱系边和相关事件。当前 E 岛 `FINAL` 是逻辑配对输出，不代表真实机械装配；当前沿父方向查祖先，反向后代影响分析暂缓。 |
| Q3 资源等待、持有与竞争 | 哪个共享资源造成等待？谁在等、当时 holder 是谁、等待是否解决、等待/持有多久？是否出现拒绝释放或资源顺序违规？ | `resource_event` 的 WAIT、ACQUIRE、RELEASE、RELEASE_DENIED、ORDER_VIOLATION；按 run/resource/owner/task 关联 | 已有等待报告，可按资源筛选并查看等待/持有统计、未解决等待和争用明细。WAIT 是状态变化记录，不是逐仿真 tick 采样。 |
| Q4 工序时间线与失败取证 | 指定 run/工序实例中，工序何时可派、何时启动/提交/结束？实际用了哪个 route/device、输入输出 UID、在哪个阶段/事件失败？等待资源多久？ | `operation_instance` 生命周期、输入/输出 UID、相关资源事件和 run events | 已有工序时间线和失败汇总。最终快照及事件不支持连续控制器状态或物理轨迹回放。 |

以上是阶段 4 的查询需求摘要，具体返回字段、CLI 参数、手工检查命令和验证证据以[运行记录实施报告](../reports/v1-run-record-persistence.md)为准；这里不重复维护命令清单。

### 阶段 4.1：有证据后再扩展的查询

以下不是首期承诺，也不应为“可能有用”而加表或加事件：

| 候选查询 | 只有出现以下实际问题时才考虑 | 需要新增的事实 |
|---|---|---|
| Q5 跨运行对比/趋势 | 用户需要比较目录版本、场景、route 或设备策略对通过率、周期、产量的影响 | 统一 acceptance/metrics schema、运行筛选器和可比较的指标版本；目前 `result_json` 为紧凑快照，不能假定所有历史运行字段口径一致 |
| Q6 调度决策解释/设备利用率 | 用户需要回答“某道工序为什么选了/拒绝了某设备”或“设备实际忙了多久” | 将 `dispatch_decisions` 及候选拒绝原因持久化；定义设备占用的起止事件和利用率分母。只存最终 selected device 不足以重建候选比较过程 |
| Q7 故障恢复分析 | 未来确实实现重试、换设备或恢复策略，且需要比较恢复效果 | attempt 级事件、故障类别、原设备/替代设备、策略版本及恢复结果；当前 `TASK_MAX_ATTEMPTS=1`，不得预造恢复流水线 |
| Q8 全物理过程回放 | 发生需要重建控制器逐步状态的诊断/监管要求 | 单独定义采样率、存储量、时钟与轨迹 schema；当前只存业务状态变化与验证证据，不应把逐 tick 日志塞入运行记录库 |

### 阶段 4.2：写入边界和事实口径（已实施）

仿真期间不做同步数据库写入；运行事实在指定生命周期边界批量持久化，并明确中止、失败、记录失败和强制终止的处理。字段、事务边界、失败口径及验证证据见[运行记录实施报告](../reports/v1-run-record-persistence.md)。

### 阶段 5：知识图谱导入（已实施）

dump 识别、Neo4j 官方工具载入、只读导出、来源追溯、SQLite staging 和审核发布流程均已实现。实际实现与验证情况见[知识图谱导入报告](../reports/v1-knowledge-graph-import.md)；阶段 5.1 是尚待实施的语义融合，不重复安排导入器工作。

### 阶段 5.1：把来源项目知识接入可执行流程

此处的 `FMPMM` 沿用 dump/来源项目的标识。该缩写在其他领域也有不同释义，不能据此认定它是知识图谱方法或行业标准名称。该来源图谱包含产品/零件结构、工艺目标及 `Goal → Operation → Action → Parameter` 等关系，可据此形成有序工艺候选并匹配设备能力。当前 `flexible_factory` 已有 `RecipeSpec`、`OperationTemplate`、工序依赖 DAG、设备能力、静态资源、WorkStep 引用、完成验证器和 CoppeliaSim alias 绑定；待做的是把已导入的来源知识经过项目自身校验后映射为可执行目录，不把来源图节点直接交给仿真器执行。

| 来源图谱概念 | 当前项目对应 | 融合规则 |
|---|---|---|
| `Instance` + `include` | 产品/零件结构候选；物料类型/产品定义 | `include` 只在确认其表示设计组成/BOM 时映射为产品结构。不得映射成运行时物料父子谱系；后者来自真实 UID 转换/合流事件。通用 Instance 不能一律当作物料或设备，须用 labels、`pro_code`/code、来源及人工映射确定实体角色。 |
| `Goal` + `achieve_goal` | 工艺目标、工序输出/完成条件 | 目标映射为可校验的目标/工艺候选；当前 `completion_verifier` 是已登记代码路径 ID，不能把自然语言 Goal 直接当成验证器或成功条件。 |
| `Operation` + `after` + Goal 的 `operation` | `OperationTemplate` + `operation_dependency` / route DAG | 将 after 关系方向和拓扑顺序显式核验，再构建 recipe 操作图。必须确定 AND/OR、路线互斥、合流输入及循环规则；源图顺序不明确时禁止直接发布。 |
| `Action` | 已登记 `WORK_STEP_TEMPLATES` 中的工步引用或未来新增的受控工步类型 | 只允许映射至当前已实现的执行路径 ID（如 `arm_pick_place`、`agv_route`、`logical_join`）。未知动作仅作为知识记录/待映射项；不从图谱加载或调用任意代码。 |
| `Parameter` | 受版本控制的工步参数（当前工步引用尚无通用参数 schema） | 首期只保存/审查来源参数。只有把参数名、类型、单位、范围、默认值和消费代码逐项登记后，才可进入可执行配置；不把任意 Neo4j property 字典透传给 controller。 |
| `Capability` + `has_capability` | `capability`、`device_capability`、工序所需 capability | 形成设备候选集合，继续经当前项目的 scene binding、资源可用性、物料位置和调度策略筛选；有 capability 不等于几何可达或安全可执行。 |
| `installed_on` | 设备/夹具装配关系或场景 binding 的来源证据 | 区分“夹具物理安装在机器人上”和“设备与 CoppeliaSim alias 对应”；不能以安装关系代替运行时 handle 解析、alias 契约校验或设备能力验证。 |

已有导入链路到 SQLite staging 的部分已经实现；融合阶段从已审核的 staging 记录开始，后续流程为：

```text
已实现：Neo4j dump → 官方工具只读导出 JSON → SQLite knowledge staging
待实施：已审核 staging
  → 人工语义映射（产品结构、工艺目标、操作、工步、参数、能力、设备/夹具）
  → 生成或更新静态 catalog staging release
  → ProcessCatalog.validate + 场景绑定/设备/资源/工步 allowlist 校验
  → 发布 catalog release（知识抽取发布和可执行目录发布是两个独立动作）
  → 应用启动选定 release，构建 RecipeSpec/OperationTemplate
  → Coordinator 按订单入口/route 实例化 OperationGraph，调度并调用现有执行器
  → 完成 verifier 通过后提交 MaterialRegistry 变换/合流
  → RunRecorder 保存 run、工序快照、UID 谱系、资源等待和证据
  → Q1–Q4 复核运行，必要时回到知识映射或目录修订
```

具体落地步骤：

1. **建立语义映射清单**：以已导入图谱中的节点标签、关系谓词和业务 ID 为输入建立字段映射。每条映射注明 `source_type/source_predicate`、目标对象、是否自动、人工责任人和缺失处理。保持原始 Neo4j ID/原谓词/来源摘要以便回溯，不把 Neo4j `elementId` 当成项目长期业务主键。实际标签/谓词清单见导入报告。
2. **定义产品结构与运行谱系边界**：若 `include` 表示产品设计组成，建立/填充设计时 BOM/产品结构对象；若来源不能证明该语义，只保留知识边。每批物料 UID 在运行中生成的谱系只由 `MaterialRegistry` 的已验证提交事实产生，两者分库表/关系类型并明确标识设计关系与执行关系。
3. **生成静态工艺候选**：按具体 product/Goal 查相关 Operation、`after` 顺序和 Action/Parameter，生成可审查的 recipe 候选 JSON；这是离线导入/维护流程，不在仿真每步查询 Neo4j，也不对导入图谱自动做模糊语义推断。
4. **做执行可行性映射**：每个 operation 明确 executor kind、所需 capability、输入输出 material ports、route/前置策略、resource policy、work-step ID 和 completion verifier。每个 action/parameter 必须指向登记过的适配器和参数 schema；未知项进入 unresolved 报告，阻止 catalog 发布。
5. **设备匹配与场景契约检查**：来源图谱的 capability/install 关系只生成候选。再用当前 `ProcessCatalog.validate()`、候选设备 capability、`device_binding`、required aliases、布局/IK/碰撞和资源规则逐层筛选。候选设备选择仍由当前 scheduler 记录；物理可达性和安全验证仍由仿真执行器负责。
6. **双重发布**：先发布经过来源/冲突验证的 knowledge extraction；再独立导入 catalog staging，跑目录结构与仿真绑定校验，通过后发布 catalog release。知识事实有效不代表它是完整可执行 recipe；任何一步失败都保留旧 current release。
7. **运行时钉住版本**：每个 run 记录 recipe/catalog version、route、artifact ID 和 contract version。运行中目录不可变更；`OperationGraph` 使用该版本实例化，所有实际输入/输出 UID 与资源事件归入同一 run。
8. **用已实现的运行查询反馈修订**：Q1 确认批次/验收结果，Q2 检查产物和设计输入差异，Q3 检查 route/resource 竞争，Q4 检查实际工序、设备、输入输出和 verifier 事件。发现错误后修订源映射或新 catalog release，不回写已发布运行事实。
9. **按需导出图查询视图**：只有出现具体的多跳推荐/反向影响分析需求时，才从已发布知识和 catalog 导出 Neo4j projection；运行记录默认留在关系库。

第一批融合范围限定为：产品/部件语义、工艺目标、Operation 顺序、Action 到现有 work-step 的映射、Capability 到设备候选。参数驱动控制、动态夹具更换、在线工艺推荐、自动生成可执行 Python、跨运行优化均不纳入首批。

### 阶段 6：维护与运维约定

1. 每个 schema 变更追加迁移文件和迁移测试；不重写已经发布的 migration。
2. 目录数据变更生成新 release，校验通过后发布；保留上一有效 release 的只读数据以便回退。
3. 数据库备份/恢复流程按实际部署位置定义并演练；场景工件仍由 ArtifactStore 的摘要、生命周期和只读策略管理。
4. 清楚记录 SQLite 数据库位置、初始化命令、目录导入命令、当前 release 查询方式、备份和恢复步骤。
5. 若未来切换 PostgreSQL，保持仓储接口和版本化领域 schema；单独制定数据库迁移，不把 SQLite SQL 兼容性假设冒充跨数据库能力。

验收：新环境能从 schema migrations 和受版本控制的 seed 重建同一目录；备份恢复后目录校验和来源摘要一致。

## 7. 当前阶段的手动验证指导

完整自动回归测试暂不作为本阶段交付条件，也不在本次工作中执行。当前先提供手动运行指导，由使用者在本机 Docker、数据库和 CoppeliaSim 环境中验证；后续若需要自动化，再把已确认的手工步骤固化为测试。

### 7.1 Neo4j 容器检查

按 5.2 或 5.3 启动后依次执行：

```powershell
docker ps --filter name=cop-test-neo4j
docker logs --tail 100 cop-test-neo4j
docker exec cop-test-neo4j cypher-shell -u neo4j -p ReplaceWithYourPassword "RETURN 1 AS ok"
```

预期：容器为 `Up`，日志没有启动失败，Cypher 返回 `ok = 1`。在 Browser 中执行同一查询也应成功。

### 7.2 图谱最小烟雾验证

在 `/import/smoke.cypher` 放入小型、可重复执行的样例，例如：

```cypher
MERGE (d:Device {id: 'arm_A'})
MERGE (c:Capability {id: 'robotic_pick_place'})
MERGE (d)-[:PROVIDES_CAPABILITY]->(c)
RETURN d.id AS device, c.id AS capability;
```

执行后手动检查：

```cypher
MATCH (d:Device)-[:PROVIDES_CAPABILITY]->(c:Capability)
RETURN d.id, c.id;
```

重复执行脚本后，节点和关系数量不应重复增长；这只验证容器、认证、Cypher 和幂等样例。来源 dump 的实际导入状态见[知识图谱导入报告](../reports/v1-knowledge-graph-import.md)。

### 7.3 SQLite 目录手工验证

数据库实现完成后，按以下顺序手工检查：

1. 初始化空数据库并执行全部 schema migrations。
2. 导入当前默认 catalog，确认得到一个 `staging` release。
3. 执行目录校验；确认非法外键、重复 ID、无效资源顺序、循环工序依赖会被拒绝。
4. 发布通过的 release，查询设备、能力、资源、区域、配方、工序和路线数量。
5. 重新启动应用，从已发布 release 读取目录；确认构建出的目录与代码 seed 的稳定序列化结果一致。
6. 人工确认数据库中没有 CoppeliaSim handle、owner/waiting 临时队列或逐 tick 运行状态。

### 7.4 运行与回退手工检查

1. 使用数据库目录执行一次受控的非仿真目录加载/校验。
2. 如果已接入仿真，再执行一次用户指定的 `full_flow` 或最小手动场景；记录实际命令、环境、工件 ID 和结果。
3. 暂停 Neo4j 或断开 Neo4j 后再次运行 SQLite 目录校验；结果应不受影响。
4. 停止并重启 Neo4j 容器，确认 named volume 中的数据仍可查询。
5. 发现目录往返不等价、数据库发布失败或图谱导入错误时，停止切换默认读取，保留旧 release/current，并记录诊断信息。

### 7.5 暂缓项目

- 不执行完整 `python -m pytest -q` 回归。
- 不执行需要 CoppeliaSim 的 focused/full_flow 自动验收。
- 不把“手动启动 Neo4j 成功”描述为资源数据库迁移完成。
- 不重复实施或描述已完成的 dump 识别、导入和运行记录查询；后续工作聚焦 5.1 的语义映射与可执行 catalog 发布。

## 8. 后续自动化测试范围

- migration 检查：空库迁移、连续升级、迁移失败、重复升级；Alembic revision 与数据库当前版本一致。
- 约束测试：外键、唯一键、容量范围、受控枚举、路线 ordinal、禁止自环和非法关联。
- 转换测试：默认 catalog 往返等价、顺序语义保持、缺失引用给出具体诊断。
- 发布测试：staging 失败不动 current release；成功发布在一个事务边界内切换。
- 边界检查：`domain` 不导入 SQLAlchemy/SQLite/persistence；仿真热路径不执行同步数据库写入；数据库目录无句柄和临时运行状态。
- 本阶段不要求实现或执行以上自动化测试，按第 7 节手工验证。若后续转为自动化，按上述范围单独实施。
- 任何数据库实现阶段都不得修改或覆盖受保护的 `docs/scene_artifacts/v1/` 场景工件；若目录版本变化要求新工件，使用现有工件派生/验证流程另行生成。

## 9. 风险、回退与停止条件

- **目录往返不等价**：不切换默认读取；修正映射/顺序/约束后重新验收。
- **迁移损坏或发布失败**：事务回滚，当前 release 指针不变；修复迁移前不人工改库冒充成功。
- **数据库不可用**：显式数据库模式报错，保留现有代码目录模式用于当前运行；不自动将空数据库解释为有效空目录。
- **dump 格式无法确认**：暂停该 dump 的解析和图谱导入；继续独立推进资源目录 schema。
- **图查询未产生具体收益**：保持关系型查询，不部署图数据库。
- **仿真延迟或行为回归**：撤回运行时目录切换配置，目录导入和数据库读写不进入仿真步进路径。

阶段结束以对应验收证据为准。计划中的步骤不视为已实现功能；各阶段报告需区分实现、测试通过、未执行和待确认事项。

## 10. 参考资料

- SQLite Foreign Key Support: <https://www.sqlite.org/foreignkeys.html>（每个连接需启用外键约束。）
- SQLite FTS5: <https://www.sqlite.org/fts5.html>（可用于后续全文检索评估；中文分词效果必须以实际数据验证。）
- PostgreSQL JSON Types: <https://www.postgresql.org/docs/current/datatype-json.html>（JSON/JSONB 适用边界参考；首期并不依赖 PostgreSQL。）
- SQLAlchemy ORM: <https://docs.sqlalchemy.org/en/20/orm/>（映射、查询和 Session/unit-of-work。）
- SQLAlchemy SQLite dialect: <https://docs.sqlalchemy.org/en/20/dialects/sqlite.html>（SQLite 外键需对每个连接启用 `PRAGMA foreign_keys=ON`，并说明 SQLite 事务行为。）
- Alembic autogenerate: <https://alembic.sqlalchemy.org/en/latest/autogenerate.html>（生成结果是候选迁移，需人工复核。）
- Neo4j CSV Import: <https://neo4j.com/docs/getting-started/data-import/csv-import/>（关系数据导出后按需构造图导入。）
- Neo4j relational-to-graph import: <https://neo4j.com/docs/getting-started/data-import/relational-to-graph-import/>（关系模型到图模型的派生导入参考。）

## 11. 实施记录入口

本方案维护架构决策、范围和验收门槛，不维护阶段完成快照或实测数字。实际改动、
测试结果与未完成项分别记录在[目录持久化报告](../reports/v1-catalog-persistence.md)、
[运行记录报告](../reports/v1-run-record-persistence.md)和
[知识图谱导入报告](../reports/v1-knowledge-graph-import.md)；字段来源见
[静态目录字段盘点](../reference/static-catalog-field-inventory.md)。

