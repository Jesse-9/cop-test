---
title: v1 静态目录字段盘点与术语对照
type: reference
status: review
created: 2026-11-05
updated: 2026-11-05
scope: RESOURCE_DATABASE_PLAN 阶段 0 交付物
related:
  - RESOURCE_DATABASE_PLAN.md
  - V1_CATALOG_PERSISTENCE_REPORT.md
---

# v1 静态目录字段盘点与术语对照

本文是
[资源目录数据库化与知识图谱实施方案](RESOURCE_DATABASE_PLAN.md)
阶段 0 的交付物：记录哪些字段进入静态目录数据库、来源模块、业务顺序和
消费者，并明确不进入数据库的运行时状态。字段的权威定义仍以
`domain/process_model.py`、`domain/rules.py` 和
`simulation/device_binding.py` 为准；本文只做盘点，不复制实现。

## 1. 术语对照

| 术语 | 代码来源 | 语义 | 不是 |
|---|---|---|---|
| 物料类型 `material_type` | `domain.rules.ITEM_TYPES` / `ItemType` | 静态物料种类与颜色 | 物料实例/UID |
| 设备 `device` | `ProcessCatalog.devices` / `DeviceSpec` | 提供能力的稳定设备或逻辑处理器 | 场景对象别名、运行时组件 |
| 能力 `capability` | `ProcessCatalog.capabilities` / `CapabilitySpec` | 工序要求的抽象能力 | 设备实例状态 |
| 资源 `resource` | `domain.rules.RESOURCE_ORDER` / `RESOURCE_ROLES` | 固定顺序的互斥锁条目 | 区域容量、场景 handle |
| 区域 `zone` | `domain.rules.ZONE_CAPACITY` / `ZoneSpec` | 物料位置与容量 | 互斥资源 |
| 配方 `recipe` | `RecipeSpec` | 某产品的工序/路线/入口集合 | 生产订单 |
| 工序模板 `operation` | `OperationTemplate` | 可调度工作模块及其端口/资源/依赖 | 工序实例 |
| 工步模板 `work_step` | `WORK_STEP_TEMPLATES` | 已登记的执行路径标识 | 可执行代码 |
| 路线 `route_variant` | `RouteVariant` | 同组互斥的工序序列 | 动态选路结果 |
| 订单入口 `order_entry` | `OrderEntry` | 来源线、来料类型、首工序和固定路线 | 当前订单实体 |
| 场景绑定 `scene_binding` | `simulation.device_binding` | 设备 ID → 场景 alias/角色 | 句柄或运行时对象 |

同一字符串在不同表中的含义不同，禁止按名称互相推断：

- `line_s1a` 既是 **resource**（互斥锁）也是 **zone**（物料位置）；两者在
  数据库中分别建行，`device_resource` 只表达设备实际占用。
- `arm_A` 是 **resource**（运动包络锁）也是 **device_id**；`device_resource`
  显式登记二者关系，不从前缀推断设备类型。
- `dock_A` 是 **resource**；`route_origin_dock`/`route_destination_dock`
  保存不带 `dock_` 前缀的短名，导入时由领域校验核对资源存在性。
- `agv` 是 **zone**（载台）而不是设备；设备是 `agv_1`。

## 2. 首期持久化字段

### 2.1 发布与物料

| 表.字段 | 来源 | 静态/顺序 | 说明 |
|---|---|---|---|
| `catalog_release.release_id` | 导入参数 | 主键 | 新版本使用新 ID，不原地改写 |
| `catalog_release.catalog_version` | `ProcessCatalog.version` | 版本 | 与 `PROCESS_MODEL_VERSION` 一致 |
| `catalog_release.status` | 导入/发布流程 | 状态 | `staging` → `published` → `superseded` |
| `catalog_release.source_digest` | `seed_digest(CatalogSeed)` | 摘要 | SHA-256；读取时重算比较 |
| `catalog_release.created_at` / `published_at` | 持久化时钟 | 时间 | UTC ISO-8601 |
| `material_type.material_type_id` | `rules.ITEM_TYPES` | 主键/顺序 | `ordinal` 保留枚举顺序 |
| `material_type.description` | 当前为空 | 静态 | 预留 |
| `material_type.color_json` | `rules.ITEM_COLORS` | 静态 JSON | 仅扩展属性；不参与关系查询 |

### 2.2 资源、区域与设备

| 表.字段 | 来源 | 静态/顺序 | 说明 |
|---|---|---|---|
| `resource.resource_id` | `rules.RESOURCE_ORDER` | 主键 | 固定资源集合 |
| `resource.role` | `rules.RESOURCE_ROLES` | 静态 | 人类可读说明 |
| `resource.kind` | `application.catalog._RESOURCE_KINDS` | 静态 | 显式分类，不按名称推断 |
| `resource.capacity` / `policy` | 当前均为 1 / `exclusive` | 静态 | 数据库 CHECK 范围 |
| `resource.acquire_order` | `RESOURCE_ORDER` 下标 | 业务顺序 | 唯一且从 0 连续 |
| `zone.zone_id` | `rules.ZONE_CAPACITY` / `ZoneSpec` | 主键/顺序 | 区域目录 |
| `zone.capacity` | `rules.ZONE_CAPACITY` | 静态 | `output_e=4`，其余为 1 |
| `zone.description` | `default_process._build_zones` | 静态 | 可为空 |
| `zone_material_type` | 工序输入/输出端口派生 | 关系 | 区域可出现的物料类型 |
| `device.device_id` | `DeviceSpec.id` | 主键/顺序 | 五臂、`agv_1`、`logic_E` |
| `device.kind` | `DeviceSpec.kind` | 静态 | `arm`/`agv`/`logic` |
| `device.physical` | `DeviceSpec.physical` | 静态 | 逻辑设备为 false |
| `device.default_line_id` | `DeviceSpec.default_line_id` | 关系 | 仅机械臂；外键到 `resource` |
| `device_capability` | `DeviceSpec.capabilities` | 关系/顺序 | 设备能力 |
| `device_resource` | `DeviceSpec.resources` | 关系/顺序 | `relation_kind='occupies'` |
| `scene_binding` | `device_binding.device_bindings()` | 关系/顺序 | role + alias；无 handle |

### 2.3 配方、工序、路线与入口

| 表.字段 | 来源 | 静态/顺序 | 说明 |
|---|---|---|---|
| `recipe.recipe_id` / `version` / `product_type` | `RecipeSpec` | 主键/版本 | `mixed_line_v1` |
| `recipe.join_operation_id` | `RecipeSpec.join_operation_id` | 关系 | 逻辑合流；无数据库外键（建表环） |
| `operation_template.operation_id` | `OperationTemplate.id` | 主键/顺序 | 全局唯一 |
| `operation_template.recipe_id` | `RecipeSpec.id` | 关系 | 同配额外键 |
| `operation_template.kind` / `semantics` | `OperationTemplate` | 静态 | 兼容标识/说明 |
| `operation_template.executor_kind` | `OperationTemplate` | 受控枚举 | `arm`/`agv`/`logic` |
| `operation_template.capability_id` | `OperationTemplate.capability` | 关系 | 能力目录 |
| `operation_template.priority` | `OperationTemplate.priority` | 静态 | `>= 0` |
| `operation_template.resource_policy` | `OperationTemplate` | 受控枚举 | `fixed`/`agv_route`/`logical_join` |
| `operation_template.predecessor_policy` | `OperationTemplate` | 受控枚举 | `all`/`any` |
| `operation_template.completion_verifier` | `OperationTemplate` | 登记 ID | 领域校验 |
| `operation_template.source_kind` / `sink_kind` | `OperationTemplate` | 受控枚举 | 物料流端点 |
| `operation_template.dock` / `route_*_dock` | `OperationTemplate` | 静态 | 可为空 |
| `operation_input` | `OperationTemplate.inputs` | 关系/顺序 | `ordinal` |
| `operation_output` | `OperationTemplate.output` | 关系 | 0..1 |
| `operation_candidate_device` | `OperationTemplate.candidate_devices` | 关系/顺序 | `ordinal` |
| `operation_resource_request` | `OperationTemplate.resources` | 关系/顺序 | 当前 `quantity=1` |
| `operation_work_step` | `OperationTemplate.work_steps` | 关系/顺序 | 登记模板 ID |
| `operation_dependency` | `depends_on` / `successors` | 关系/顺序 | 同时保存两端顺序 |
| `route_variant` | `RouteVariant` | 关系/顺序 | group/entry/exit |
| `route_operation` | `RouteVariant.operation_ids` | 关系/顺序 | `ordinal` |
| `order_entry` | `OrderEntry` | 关系/顺序 | line/material/route |

## 3. 明确不持久化

| 运行时状态 | 当前来源 | 原因 |
|---|---|---|
| CoppeliaSim handle | `scene`/`unit` 缓存 | 每次加载重建，跨运行无效 |
| `ResourceTable.owners` / `task_of` | `domain/resources.py` | 单次仿真互斥状态 |
| `ResourceTable.waiting` / `events` | `domain/resources.py` | 运行事件，后续阶段按批写入 |
| `MaterialRecord4.sim_handle` | `domain/materials.py` | 场景句柄 |
| UID / 父子谱系 | `MaterialRegistry` | 运行事实，后续 `material_instance` |
| 控制器/执行器对象 | `application`/`simulation` | 不可序列化对象 |
| 仿真时刻 `sim_time` | 运行时 | 不属于静态目录 |
| 场景 SHA-256 / manifest | `artifacts/store.py` | 仍由 ArtifactStore 管理 |

## 4. 后续阶段数据

- `PROCESS_TRANSITIONS`：当前由 `domain.rules` 维护；如需查询再新增
  `material_transition` 表。
- support 绑定（传送带/交接台/缓冲/输出台别名）：仍由
  `device_binding.support_bindings()` 在代码中校验，未写入
  `scene_binding`。
- 运行记录表：已在 revision `0003_run_records` 实施（方案 §2.2），见
  [V1_RUN_RECORDS_REPORT.md](V1_RUN_RECORDS_REPORT.md)。
- 知识图谱表：已在 revision `0002_knowledge_graph` 实施（方案 §2.3）；
  真实 `fmpmm.dump` 的 Docker/Neo4j 端到端导入未执行，见
  [V1_KNOWLEDGE_GRAPH_IMPORT_REPORT.md](V1_KNOWLEDGE_GRAPH_IMPORT_REPORT.md)。

## 5. `fmpmm.dump` 来源状态

| 属性 | 值 |
|---|---|
| 大小 | 273,272 字节 |
| SHA-256 | `9413d53dd5b6206e5f6082bf134757a3aa8f28e367f2fb95bea094cfdc75e344` |
| 开头字节 | ASCII `DZV1` + `28 b5 2f fd` 字节序列 |

生成软件、版本、容器格式、是否加密和官方恢复方式最初未确认；用户提供的
`ref/scripts/` 后续确认它是 **Neo4j 数据库 dump**，官方读取路径为
`neo4j-admin database load` / `migrate`。项目仍不解析二进制 dump；真实图
（222 节点/384 关系）已通过官方工具载入、导出并成功导入知识表，见
[V1_KNOWLEDGE_GRAPH_IMPORT_REPORT.md](V1_KNOWLEDGE_GRAPH_IMPORT_REPORT.md)。
该项不阻塞静态目录数据库。
