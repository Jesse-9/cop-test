# Changelog

## Unreleased

- 新增 `tools/measure_startup_stages.py`：复用 `run` 启动路径，测量连接、
  工件加载、启动点校验、组件构建到 `start_stepping` 的各阶段耗时，并在
  仿真刚启动时截断，不执行场景循环；方法见
  [项目使用指南](docs/wiki/98-ProjectUsage/USAGE.md)的“启动阶段计时”一节。
  优化步骤、验收与回退条件见
  [v1 启动性能优化方案](docs/implementation/plans/simulation/v1-startup-performance-optimization.md)。
- 启动性能优化 P1/P2 代码落地并完成 3 次启动计时：新增只读校验上下文，
  复用全场景 alias 索引与一次 `scene.verify()`；删除碰撞构建死代码
  `inventory`；集合成员优先批量加入并带成员校验/逐项回退；共享传送带与
  真实臂发现结果及 `collidable`/`measurable` 原值；`verify_fk` 一次建立
  真实臂索引。实测（正常 load 路径）`prepare_start` 25 s → 12.1–12.2 s、
  五台影子运行时 47.9 s → 20.3–21.4 s、加载完成 → 仿真启动
  74.65 s → 33.7–35.4 s。
  pytest 615 passed / 7 skipped；集合成员核对和 `full_flow` 验收仍未执行，见
  [实施报告](docs/implementation/reports/simulation/v1-startup-performance-optimization.md)。
- 启动性能后续项：复用 `adopt_stage4_scene` 的 alias 索引、影子臂根快速
  删除、计时工具 `profile.*` 子阶段；P3 订单子集臂的 builder/runner 代码
  落地（`unit_ids`、候选工位并集、`active_unit_ids` 证据）。干净
  `full_flow` 功能验收通过（`all_ok=true`，1 FINAL、343.85 s、0 failure）；
  首次尝试因残留进程重载场景中止。快速删除路径的 `Shadow_*` 残留已修复，
  启动计时复测 `leftover=[]`、清理 4.01 s。4 臂子集订单 `order-subset-4arm`
  重试通过：A/C/D/E（B 未构建）、1 FINAL、sim 163.4 s、3268 步、
  `leftover=[]`；首次尝试在 E 释放校正处失败，为重试通过的间歇性问题。
  计时口径修正：`build.*`/`profile.*` 是重叠子项，不能相加；最新
  `startup_total_wall` 为 79.97–88.61 s，旧打印的 130–210 s 是重复求和。
- 修复订单工作台列表页在真实 `GET /api/orders` 摘要响应上崩溃的问题：
  列表接口返回 `OrderSummary`（不含 `versions`），页面改为从版本详情读取
  `dag_digest`，不再假设列表包含版本数组。补充订单列表 Playwright 旅程，
  并将 mock 路由匹配扩展到带查询串的 API 请求。
- Vite 开发服务器显式绑定 `127.0.0.1`（与文档、代理目标和 Playwright
  baseURL 一致）；`npm run dev` 后 `http://127.0.0.1:5173` 可直接访问。
- 自建订单 DAG（阶段 1–6）：新增声明式 `OrderSpec`、端口/结构校验器、
  确定性编译器、NODE/GLOBAL 失败隔离、版本化订单仓储、本地 HTTP API、
  有界确定性 planner 和 React 订单工作台；订单运行绑定不可变版本、目录版本
  和具体 artifact，HTTP handler 不持有仿真句柄。
- 阶段 6 实机验收：在受保护工件 `v1/L5_UFLOW/v001_20261103_004317` 上，
  UI 手工订单和 planner 订单各完成 1 个 `FINAL`（父 UID `[1,2]`）；
  `NODE_PRECHECK_REJECTED` 只阻断依赖后继；真实安全锁存使订单 `ABORTED`
  并保留锁存。证据见
  `docs/implementation/evidence/order-orchestration/v1_order_dag_phase6_acceptance_20261108.json`，
  报告见
  `docs/implementation/reports/order-orchestration/v1-order-dag-phase-06-simulation-acceptance.md`。
- 修复订单 runner 的 `connect()` 解包、目录来源记录和订单目标数量判据；
  `api serve` 新增 `--sim-host`/`--sim-port`，与 HTTP 绑定分离。
- 阶段 6 全量非仿真回归 598 passed / 7 skipped；前端 Vitest 12 passed、
  Playwright mock E2E 3 passed、生产构建通过。
- 未验证的真实 Arm/AGV 运动错误、真实 ZMQ 断开/stepping 故障、任意 DAG/
  设备替换、自动恢复、多订单并行、MES/ERP 和跨场景调度仍未实现或仍按
  GLOBAL 处理；`NODE_PRECHECK_REJECTED` 仍是唯一 NODE 准入 code。
- 混线生产灵活性 WP1（有限批次配比）：新增
  `application/batch.py`，校验运行边界传入的 `batch` 映射并解析命名
  profile（`default`、`direct_heavy=2/0/2`、`detour_heavy=0/1/1`）；
  未知键、负数、非整数、布尔值在创建任务前以 `BatchError` 拒绝。非默认
  profile 只用于 `full_flow`，不改变 scenario、固定路线、设备候选或场景
  工件。
- `Stage4Coordinator` 新增可选 `batch` 参数；`COORDINATOR_START`、
  `summary()`、`acceptance()`、运行快照和运行证据统一记录实际生效批次；
  `acceptance()` 的 FINAL/未配对数量改由批次推导，默认 `full_flow`
  结果不变。
- CLI 新增 `run --batch-profile`，在连接仿真和取得运行锁之前校验。
- `spec.py` 新增 `BATCH_INPUT_VERSION = v1_batch_20261105`；未修改
  `simulation/config.py`，`config_digest` 与受保护工件不变。
- 新增 `tests/test_v1_batch_profiles.py`（24 passed），扩展协调器批次
  测试；纯逻辑回归 531 passed / 3 skipped（3 个 pytest 实机用例未执行，
  实机验收改由 CLI 执行）。
- WP2 实机验收：`default`、`direct_heavy`、`detour_heavy` 三组
  `all_ok=true`；FINAL 分别为 1/2/1，资源均清零，运行记录 `DONE`。
  证据见 `docs/implementation/evidence/v1_full_flow_*_2026110*.json`。
- 初版 `detour_heavy=0/2/2` 在第 2 个 `RAW_B` 等待 AGV 空载调机达到
  300 s 时触发 `BACKPRESSURE_TIMEOUT`（仿真 414.35 s），保留为失败边界
  证据；profile 收窄为 `0/1/1` 后重跑通过。未放宽背压超时或调度策略。
- 同一 CoppeliaSim 会话第 4 次连续运行时进程以 `0xC0000409` 退出；重启
  全新进程后完成 `detour_heavy` 验收。正式验收建议每次使用新进程。
- WP3（设备替代）/WP4（同一 UID 动态选路）未实施。详见
  `docs/implementation/reports/production-flow/v1-mixed-production-flexibility-demo.md`。
- 运行记录（阶段 4）：新增 Alembic revision `0003_run_records` 与
  `RunRecorder`，只在运行开始/结束两个边界批量写入 `production_run`、
  工序实例、物料实例、谱系、资源事件和运行事件；`sim.step()` 热路径不写库。
- 新增 `flexible-factory report runs/run/genealogy/waits/timeline`，对应
  Q1 运行汇总、Q2 物料谱系、Q3 资源等待、Q4 工序时间线与失败取证；支持
  最近一次运行和直接 SQLite 查询。
- 失败/中止运行同样写终态；写库失败只标记运行报告
  `run_record.ok=false`，不回滚仿真、不自动重放。`run --run-records off`
  可关闭。
- 新增 [本地数据库查看指南](docs/implementation/reference/data-platform/database-inspection-guide.md)：
  汇总 CLI、HTML 仪表盘、原始 SQLite 和 Neo4j Browser 的数据库查看入口。
- 扩展 `tools/db_dashboard.py`：展示已发布工艺目录的工序依赖 DAG、路线
  卡片、工序详情、资源申请顺序、设备/能力、区域、物料、订单入口和场景
  绑定；DAG 与路线可点击联动工序详情。工艺目录需先 `catalog import` /
  `publish`，否则显示空状态。
- 新增 `tools/db_dashboard.py`：从本地 SQLite 生成自包含 HTML 仪表盘，
  展示知识图谱（按类型分组）、实体/谓词计数、运行记录 Q2–Q4、目录发布和
  来源状态；支持浅色/深色主题、节点筛选/搜索和表格视图。新增
  `tests/test_v1_db_dashboard.py`（2 passed）。
- 修复运行记录首次实机运行失败：`OperationRuntime` 增加只读
  `instances()`，`RunRecorder` 可读取真实工序实例；新增真实 runtime 回归
  测试。修复后已生成 3 条 `DONE` `full_flow` 运行记录（FINAL 1，资源清零，
  Q2–Q4 可查询），另有 2 条 `ABORTED` 记录验证失败/中止路径。
- 新增 `tests/test_v1_run_records.py`（14 passed）；目录/知识/运行/边界
  组合测试 47 passed，相关聚焦回归 164 passed。运行记录实机验收证据见
  `docs/implementation/evidence/data-platform/v1_run_records_acceptance_20261106.json`。
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
  `docs/implementation/evidence/production-flow/v1_stage4_full_flow_20261104_210605.json`。
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
