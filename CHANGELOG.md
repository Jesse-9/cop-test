# Changelog

## Unreleased

- 运行记录（阶段 4）：新增 Alembic revision `0003_run_records` 与
  `RunRecorder`，只在运行开始/结束两个边界批量写入 `production_run`、
  工序实例、物料实例、谱系、资源事件和运行事件；`sim.step()` 热路径不写库。
- 新增 `flexible-factory report runs/run/genealogy/waits/timeline`，对应
  Q1 运行汇总、Q2 物料谱系、Q3 资源等待、Q4 工序时间线与失败取证；支持
  最近一次运行和直接 SQLite 查询。
- 失败/中止运行同样写终态；写库失败只标记运行报告
  `run_record.ok=false`，不回滚仿真、不自动重放。`run --run-records off`
  可关闭。
- 修复运行记录首次实机运行失败：`OperationRuntime` 增加只读
  `instances()`，`RunRecorder` 可读取真实工序实例；新增真实 runtime 回归
  测试。用户 `full_flow` 仿真本身无误，修复后仍需重跑生成成功运行记录。
- 新增 `tests/test_v1_run_records.py`（14 passed）；目录/知识/运行/边界
  组合测试 47 passed，相关聚焦回归 157 passed。真实 `full_flow` 与运行
  记录联合验收待重跑。
- 知识图谱导入（阶段 5）：确认 `fmpmm.dump` 为 Neo4j 数据库 dump，新增
  Alembic revision `0002_knowledge_graph`（来源、抽取运行、实体/关系、
  来源追溯），新增 `tools/neo4j_export.py` 与 `tools/neo4j/` 官方
  `neo4j-admin` 载入/迁移工具。
- 新增 `flexible-factory knowledge import/status/verify/publish`：只读
  导出 JSON 写 staging extraction，记录来源 SHA-256、原始节点/关系 ID、
  来源定位与冲突；未映射标签/谓词、端点缺失或 canonical ID 冲突会阻止
  发布。复跑按来源摘要和抽取器版本幂等。
- 新增 `tests/test_v1_knowledge_graph.py`（10 passed）；目录/知识/边界
  组合测试 40 passed。真实 `ref/fmpmm.dump` 已通过 Docker Neo4j 5.13
  载入/迁移并只读导出：222 节点/384 关系，导入 222 实体/384 关系并发布
  成功，无未映射/冲突/悬空关系。
- 阶段 4（运行记录）仍未实施，受“明确查询需求”决策门约束；阶段 6 仅
  覆盖迁移测试、来源摘要和工具文档。
- 静态资源/工艺目录数据库化（阶段 0–3）：新增
  `src/flexible_factory/persistence/`，使用 SQLAlchemy 2.x ORM 与 Alembic
  管理 SQLite schema；首个 revision `0001_catalog_core` 覆盖物料类型、设备、
  能力、场景绑定、互斥资源、区域、配方/工序/路线/入口及关系表。
- 新增 `CatalogSeed` 单事务导入、staging → published 原子发布、只读仓储
  重建等价 `ProcessCatalog`，并校验来源摘要、领域规则和派生关系。
- 新增 `flexible-factory catalog init/import/publish/status/diff/verify`；
  `run` 默认仍读代码目录，`--catalog-source db` 仅在数据库目录与代码目录
  逐字段等价时切换。
- `DeviceBinding` 增加 `alias_roles`，使 `scene_binding.role` 显式可持久化；
  不保存 CoppeliaSim 句柄或运行时 owner/waiting 状态。
- 新增 `tests/test_v1_catalog_persistence.py` 与 domain/persistence 导入边界
  测试；相关纯逻辑回归 87 passed。完整 `pytest -q` 与实机目录切换验收未在
  本轮执行。
- `fmpmm.dump` 已确认为 Neo4j 数据库 dump；阶段 5 的官方载入/导出和
  staging 导入已实施，真实 dump 的 Docker 端到端执行未完成。运行记录表
  （阶段 4）仍未实施。详见
  `docs/implementation/V1_KNOWLEDGE_GRAPH_IMPORT_REPORT.md`。

- 阶段 4 纯调度策略：新增 `domain/scheduling.py`（`SchedulingSnapshot` →
  `DispatchDecision`），按 recipe allowlist、静态能力/绑定、物料/区域前提、
  设备占用与动态几何验收状态筛选候选；排序为业务优先级 → 等待时间 →
  ready 时间 → 稳定实例 ID。策略不导入 simulation/application、不申请资源。
- runtime-backed 任务写入口收紧：`Stage4Task` 生命周期 setter 与 `TaskQueue`
  状态方法对配方任务抛 `TaskError`；`OperationRuntime` 新增
  `claim_instance`/`select_device`/`record_phase`/`record_resources`/
  `record_upgraded`/`mark_waiting`/`mark_blocked`/`mark_resumed` 窄接口。
- 协调器按调度决策派工：`_dispatch_ready()` 记录 `SCHEDULE_DECISION`/
  `SCHEDULE_WAIT`；`_poll_unit()`/`_start_transport()` 仍用
  `ResourceTable.acquire_all()` 全有或全无申请资源，并把 `ACQUIRED`/`BUSY`、
  冲突资源与 owner 回填到决策；选定设备写入
  `OperationInstance.selected_device_id`，execution ID 在启动后回填。
  每条 decision 显式列出 `not_modeled` 维度（设备健康/工具载荷/姿态包络）。
- `spec.py` 新增 `SCHEDULER_POLICY_VERSION = v1_scheduler_20261104`；
  不修改 `simulation/config.py` 冻结常量，`config_digest` 与受保护工件不变。
- 新增测试 `tests/test_v1_scheduling.py`、`tests/test_v1_coordinator_scheduler.py`
  与 `tests/test_v1_stage4_scheduler_sim.py`；纯逻辑测试 450 passed / 3 skipped。
- 阶段 4 `full_flow` 实机验收通过：FINAL 1 件（父 UID `[1,3]`）、13 个任务
  全部完成、12 个工序实例、11 条调度决策全部可解释、资源清零；证据见
  `docs/implementation/evidence/v1_stage4_full_flow_20261104_210605.json`。
  WP5（等价候选扩展）未实施，WP6（运动余量复核）未执行。
- 新增配方运行图：`domain/operation_runtime.py` 增加 `RecipeRunContext`、
  `OperationGraph` 与 `OperationRuntime`；`OperationInstance` 扩展
  run/recipe/route/前驱/输入输出/工步字段，仍是任务状态唯一存储。
- 协调器改为由 ready 集合驱动：`_spawn_source()` 按入口固定 route variant
  建图，逐 tick 从 ready 实例投影任务入队；完成提交（物料账本）成功后才
  标记 DONE 并解锁后继，`_submit_followup()` 的静态 successor 推进已删除。
- E 岛合流纳入运行图：输入工序与父实例都就绪后才建 `kit_join` 节点，沿用
  `MaterialRegistry.commit_join()` 的原子双父消费与重复配对拒绝。
- `OrderEntry` 增加 `route_variant_id`（入口 → 路线固定对应，不是动态选路），
  默认配方显式声明 `s1a→housing_direct`、`s1b→housing_alt`、`s2d→bracket_route`；
  `PROCESS_MODEL_VERSION` 升至 `v1_process_20261103b`。
- 新增测试 `tests/test_v1_operation_runtime.py`（20）与
  `tests/test_v1_coordinator_dag.py`（5）；纯逻辑测试 426 passed / 2 skipped。
- 产品入口、场景工件、契约与 `full_flow` 用户运行模式未改变。
- 新增执行适配层 `simulation/execution/`：`ArmExecutor` 包装
  `Stage4UnitController`，`AgvExecutor` 包装 `AGVRouteController4`；
  协调器改为经 `start/pre_tick/post_tick/poll` 启动与轮询，唯一
  `sim.step()` 调用点仍在 `Stage4Coordinator.step()`。
- 新增 `simulation/device_binding.py`：device_id → alias/资源/区域 的静态
  绑定表，覆盖五台臂、AGV、逻辑配对处理器与线/交接/缓冲承载。
- 阶段 2 focused 仿真实测通过：`arm_A` 完成一次取放，AGV 空载 `A→B`
  与载货 `B→A` 均到站（证据见 `docs/implementation/evidence/`）。

## 1.0.0 (2026-11-03)

v1 收敛：把阶段一至四的实现收敛为独立产品包，只交付五岛 `L5_UFLOW`
场景和 `full_flow` 用户运行模式。

- 新增 `src/flexible_factory/`：`domain`、`simulation`、`artifacts`、
  `application`、`cli`，运行期不导入 `stage1`–`stage4` 或 `reference`。
- 新增唯一入口 `flexible-factory`（console script），命令包括 `list`、
  `status`、`load`、`validate`、`stop`、`migrate`、`build`、`run`。
- 新增 v1 工件命名空间 `docs/scene_artifacts/v1/`，manifest schema `2.0`，
  场景契约 `v1_contract_20261103`。
- 新增 `migrate`：从已验收 stage4 `L5_UFLOW` 工件幂等迁移，源工件只读，
  `v1/current.json` 原子更新。
- 当前 v1 工件 `v1/L5_UFLOW/v001_20261103_004317` 由
  `stage4/L5_UFLOW/v004_20261029_133249` 迁移，场景逐字节一致。
- 发行名从 `factory-sim` 改为 `flexible-factory`；移除 `mcp` 运行时依赖。
- MCP `factory-sim` 服务器不在 v1 范围；`.mcp.json` 和旧 MCP 实现已删除。
- 清理旧阶段源码、入口脚本、报告、测量资料与旧证据；仅保留 v1
  迁移追溯所需的 stage3/stage4 来源工件。
- 实机验收：`full_flow` `all_ok=true`（FINAL 1 件，父 UID [1,3]）；
  `conveyor_fault` 回归冻结/阻塞/无 FINAL/资源释放通过。证据见
  `docs/implementation/evidence/v1_*_acceptance_20261103_*.json`。
