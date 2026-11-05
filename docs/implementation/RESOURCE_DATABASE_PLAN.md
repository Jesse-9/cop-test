---
title: 资源目录数据库化与知识图谱实施方案
type: plan
status: proposed
created: 2026-11-05
updated: 2026-11-05
scope: flexible_factory resources, process catalog, material lineage, fmpmm.dump assessment
---

# 资源目录数据库化与知识图谱实施方案

## 1. 目标与决策

本文规划把当前散布在 Python 常量、静态工艺目录、场景绑定和运行证据中的资源数据整理为版本化、可校验、可查询的数据格式，并以数据库持久化。知识图谱用于表达实体关系、来源和查询路径，不取代运行时领域模型。

### 1.1 首选方案

- 首期使用 Python 标准库 `sqlite3` 和 SQL 迁移文件，不增加 ORM、服务端数据库或图数据库依赖。
- SQLite 是当前单机仿真产品的本地权威数据存储。静态目录在应用启动时载入并转换成现有领域对象；运行时调度仍由内存中的 `ResourceTable`、`MaterialRegistry`、`OperationRuntime` 管理。
- 第一批持久化范围为物料类型、设备、能力、场景绑定、互斥资源、物料区域、配方/工序/路线及它们的显式关系。运行记录与知识抽取分后续阶段进入。
- 关系表是可查询业务数据和关系事实的权威表示。JSON 只存非核心扩展属性、来源原文/片段和交换快照；不得用 JSON 数组替代需要约束或查询的外键关系。
- 暂不部署 Neo4j。只有明确的多跳查询、规模或图算法需求通过关系查询基准后仍无法满足时，才评估从关系表导出到 Neo4j。
- `fmpmm.dump` 作为未经识别的输入工件处理。识别真实生成软件、版本和官方导出/恢复格式后才设计解析；在此之前不猜测文件内容、不执行导入、不把它视为已知图数据库备份。

### 1.2 当前项目边界

当前 `domain/process_model.py` 已定义静态配方、工序、路线、订单入口、设备与能力目录；`domain/resources.py` 是固定顺序、全有或全无的运行时互斥锁；`domain/materials.py` 管理 UID、物料谱系和区域；`artifacts/store.py` 管理版本化场景工件。数据库实现放在新建的 `src/flexible_factory/catalog/` 或 `src/flexible_factory/persistence/` 边界内，不能让 `domain/` 直接导入 SQLite、文件系统或 CLI。

句柄、连接对象、仿真时刻中的临时 owner/waiting 队列和控制器对象均为运行时状态，不进入静态资源目录。持久化场景绑定时使用稳定设备 ID、场景/契约版本与 alias，不保存 CoppeliaSim handle。

## 2. 数据模型

所有稳定业务 ID 使用受约束的字符串主键（例如 `arm_A`、`dock_A`、`mixed_line_v1`）。表使用 `snake_case` 命名，时间戳统一 UTC ISO-8601，仿真时间使用以秒为单位的 REAL。删除策略默认 `RESTRICT`；目录发布采用新版本记录，不原地改写已发布版本。

### 2.1 首期静态目录表

| 表 | 核心字段 | 约束/说明 |
|---|---|---|
| `schema_migration` | `version`, `applied_at`, `checksum` | 迁移版本唯一；已应用迁移不可编辑 |
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

数据库采用 SQLite `STRICT` 表（运行 SQLite 版本满足要求时）或等价的显式 `CHECK` 约束。每个连接创建时启用 `PRAGMA foreign_keys = ON`；应用启动检查其实际值，未启用即失败。建表时明确 `NOT NULL`、唯一性、外键和范围约束；Python 领域校验继续负责跨行不变量和拓扑无环验证。

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

## 4. `fmpmm.dump` 识别与知识图谱导入流程

当前工作区检查结果：文件名为 `fmpmm.dump`，大小约 273 KB，开头字节为 ASCII `DZV1`；zstd 检测未识别为 zstd 压缩数据。仅凭这些信息不能确认生成工具、容器格式、内部版本或其是否包含知识图谱。

按以下门槛执行：

1. 从文件提供者/生成系统取得导出应用名称、版本、导出命令或界面选项、对应恢复/导入说明、是否加密及脱敏要求。
2. 优先使用生成应用官方的只读 inspect/list 或官方格式转换功能，将 dump 导出为受支持的中间格式；不对原文件执行可能修改内容的恢复操作。
3. 对原始文件计算 SHA-256，记录文件字节数、提供者、工具版本、时间和转换命令；转换产物另算摘要。
4. 先在独立 staging 数据库导入小样本。识别节点/边/属性/来源字段、ID 规则、关系方向和编码；将统计及无法映射项写入导入报告。
5. 制作映射表，将来源标签/关系映射到 `knowledge_entity.entity_type` 和受控谓词；保留来源原始 ID 和原始片段，不以模型生成内容覆盖事实字段。
6. 验证实体/关系数量、唯一 ID、引用闭合、关系端点存在、孤立率、重复率、无来源比例及抽样追溯。定义具体阈值后由数据负责人确认；在确认阈值前，报告所有异常并禁止发布。
7. 重复运行同一来源摘要和同一转换器版本应幂等；相同 canonical ID 的冲突写入冲突报告/待审核状态，不采用“最后写入覆盖”。
8. 仅在 staging 校验通过后，于事务中发布对应 extraction/catalog release；发布前后的数据库备份和恢复流程经过演练。
9. 如果官方转换可导出 CSV/JSON 等，优先基于该输出导入关系库；若后续确实需要 Neo4j，从关系数据生成节点/边文件并单独验证图导入。不要让未知 dump 解析逻辑进入仿真主路径。

若无法得到格式来源或官方解析途径，`fmpmm.dump` 保持为待识别资料，资源目录数据库化仍可独立按本方案推进；不得把未读取的文件描述为已完成图谱导入。

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
6. `fmpmm.dump` 在来源和格式未确认前禁止直接传给 `neo4j-admin database load` 或任何猜测性的恢复命令。它必须先经过第 4 节的只读识别和转换。

### 5.5 与本项目的连接边界

- Python 仓储先完成 SQLite 目录读取和校验，再由单独的导出器生成 Neo4j 节点/边数据。
- Neo4j 不参与 `ResourceTable.acquire_all()`、物料原子合流、工序 ready 判定或 CoppeliaSim stepping。
- 图谱导入失败时，SQLite 目录和仿真运行不受影响；图谱只保留上一次已发布版本。
- 每次导出记录源数据库 release、导出器版本、输入摘要、节点/关系计数和导入时间。

## 6. 详细执行步骤与验收门槛

以下步骤按顺序实施。每阶段在前一阶段验收通过后再开始依赖它的工作；变更通过正常代码评审和项目测试，不改变已保护场景工件。

### 阶段 0：盘点与数据契约

1. 盘点 `rules.py`、`default_process.py`、`process_model.py`、`device_binding.py`、`layout.py`、`materials.py`、`resources.py`、manifest 和相关测试中的持久字段。
2. 为每个字段记录：稳定 ID、来源模块、静态/运行时属性、消费者、版本策略、可空性、顺序语义、是否持久化。
3. 对 `device`/`resource`/`zone`/`material_type`/`operation` 形成术语对照，列出同名字符串代表不同语义的情况。
4. 对 `fmpmm.dump` 执行格式来源调查；该项不阻塞静态目录 schema，但阻塞 dump 导入。

交付：字段目录、词汇表、dump 来源状态记录。验收：没有把句柄、运行时 owner、waiting 队列或物理仿真瞬时状态列入静态目录。

### 阶段 1：schema 与迁移框架

1. 新建 persistence/catalog 包，明确连接工厂、迁移 runner、仓储接口与领域转换器；领域模块只接收数据对象，不依赖 SQLite。
2. 新建有序 SQL 迁移文件 `0001_catalog_core.sql`，实现本方案 2.1 的最小核心表与约束；大表/运行记录/图谱表留到各自阶段。
3. 实现迁移账本：按版本顺序执行；同一迁移重复执行不重复应用；应用后校验文件 checksum，禁止静默修改已应用迁移。
4. 每个连接设置并验证外键开启、忙等待策略和事务模式；不配置 WAL/线程池等未经需要证明的机制。
5. 数据库路径通过现有应用配置/CLI 注入，测试使用临时目录；不得默认将数据库写入已受保护的场景工件目录。

验收：空库可从零迁移；重复初始化无重复副作用；非法外键/容量/重复 ID 被拒绝；失败迁移保留明确错误且不将版本标记成功。

### 阶段 2：目录导入与只读仓储

1. 实现 `ProcessCatalog` → 数据库 staging release 的显式转换器；先导入 material/device/capability/resource/zone，再导入 recipe/operation/route/entry 和关联表。
2. 通过参数化 SQL 写入，在单一事务内完成一个 staging release；任何校验失败都回滚该批次。
3. 查询仓储从已发布 release 构造现有不可变领域数据类；验证外键完整性和全部领域校验后才返回目录。
4. 对比数据库往返前后的稳定 `as_dict()` 输出，并校验资源顺序、输入/输出顺序、工步顺序、路线顺序、候选设备顺序及前置策略。
5. 提供显式的初始化/导入入口；尚未迁移 CLI 前，生产仿真继续使用原默认目录，不做静默 fallback。

验收：当前默认目录可导入、读取并重建；非法引用、重复 order、循环依赖及未登记执行器 ID 均在发布前失败；读取 staging release 不会改变当前发布指针。

### 阶段 3：影子读取与切换

1. 在测试与诊断命令中并行构建“代码目录”和“数据库目录”，对规范化序列化值做 diff；运行时先不切换。
2. 清理所有 diff：区分无语义字段顺序、明确的展示字段和真实数据差异；不能用放宽比较掩盖顺序语义差异。
3. 增加只读数据库目录选择配置；默认仍为现有行为，显式选库才进入数据库目录路径。
4. 运行纯逻辑测试及相关流程测试；验证场景配置摘要、process model 版本和已验收 full_flow 行为不变。
5. 达到发布门槛后，才将新部署默认目录来源改为数据库；保留代码内默认目录作为可重复导入的 seed 和迁移对照，直到稳定迁移策略确定。

验收：等价性 diff 为空；数据库不可用时错误清晰且不会悄悄使用过期空目录；full_flow 结果契约和工件内容未被目录读取切换意外改变。

### 阶段 4：运行记录（独立决策门）

1. 先确定要支持的实际查询（例如按 run 查询最终产物谱系、资源等待原因、工序执行耗时）；没有明确查询则暂不实现该阶段。
2. 为所需查询设计最小运行记录表和索引，明确每种记录的权威提交点、幂等键、时间基准和事件保留规则。
3. 在应用层明确批量提交边界，只在事件生成/工序提交边界写库；不在每个仿真 tick 做同步 SQL。
4. 为写入失败定义可观察结果：运行报告必须标记持久化失败；不得把记录缺失当作成功，也不得自动重放会重复应用物料提交的命令。
5. 测试事务中断、进程异常结束、重复事件导入及由事件重算的现有指标。

验收：指定查询可由持久化记录重建；`ResourceTable` 的 acquire-all/释放顺序和 `MaterialRegistry` 原子配对语义不因写库而改变。

### 阶段 5：知识图谱抽取与导入

1. 只有 `fmpmm.dump` 来源和官方解析方式已确认后，新增格式适配器；适配器只负责 source → staging 中间记录，不依赖仿真运行代码。
2. 按 4 节流程记录来源、摘要、转换器版本、原始 ID、实体/关系、来源定位和冲突。
3. 对实体与谓词实行受控词表；先确定当前项目需要查询的关系，再加关系类型，不建设通用可执行规则语言。
4. 在 staging 数据库生成导入报告，覆盖计数、冲突、悬空关系、无来源关系、无法映射属性和样例回溯。
5. 经过数据审核并通过质量门槛后事务发布；建立针对真实检索问题的 SQL 查询和结果样例。
6. 若查询性能确有不足，记录数据规模、查询计划、响应基线和 SQL 优化尝试，再单独评估 Neo4j；图数据库是派生视图，必须能从已发布关系数据重建。

验收：原文件摘要可追踪；同输入重跑幂等；每个发布实体/关系可回溯来源；冲突没有被静默覆盖；未知格式继续保持未发布状态。

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

重复执行脚本后，节点和关系数量不应重复增长；这只验证容器、认证、Cypher 和幂等样例，不代表 `fmpmm.dump` 已导入。

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
- 不在 `fmpmm.dump` 格式未确认时执行恢复、加载或批量导入。

## 8. 测试与交付要求

- migration 测试：空库迁移、连续升级、迁移失败、重复启动、checksum 不匹配。
- 约束测试：外键、唯一键、容量范围、受控枚举、路线 ordinal、禁止自环和非法关联。
- 转换测试：默认 catalog 往返等价、顺序语义保持、缺失引用给出具体诊断。
- 发布测试：staging 失败不动 current release；成功发布在一个事务边界内切换。
- 边界测试：`domain` 不导入 `sqlite3`/persistence；仿真热路径不执行同步数据库写入；数据库目录无句柄和临时运行状态。
- 本阶段完整自动回归暂缓；交付物提供 7 节手动验证步骤。后续自动化测试必须以已通过的手工步骤为基础，并单独立项。
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
- Neo4j CSV Import: <https://neo4j.com/docs/getting-started/data-import/csv-import/>（关系数据导出后按需构造图导入。）
- Neo4j relational-to-graph import: <https://neo4j.com/docs/getting-started/data-import/relational-to-graph-import/>（关系模型到图模型的派生导入参考。）

