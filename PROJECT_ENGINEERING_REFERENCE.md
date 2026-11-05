# cop-test 工程参考（v1）

本文件记录 v1 产品的工程约束、模块职责和仿真实测陷阱。项目状态见
[README.md](README.md)，运行命令见
[docs/wiki/98-ProjectUsage/USAGE.md](docs/wiki/98-ProjectUsage/USAGE.md)，
实施与验收见 [v1 产品实施报告](docs/implementation/reports/v1-product-implementation.md)。

## 1. 产品边界

- 产品：`flexible-factory` 1.0.0；Python 包 `flexible_factory`；唯一入口
  `flexible-factory`。
- 场景：五岛 `L5_UFLOW`（A–E、S1/S2 仓库、四条物流线、共享 pioneer p3dx
  AGV、E 岛双输入缓冲与配对输出）。
- 用户运行模式：`full_flow`。其他 scenario 仅用于内部回归/诊断。
- 运行期不导入旧 `stage1`–`stage4`、`stage1_1` 或 `reference`；旧实现已移除。
- 统一故障自动恢复未实现；安全锁存不得自动清除。

## 2. 目录与依赖方向

```text
src/flexible_factory/
  spec.py            产品/契约/schema 版本、模型路径
  domain/            rules, materials, tasks, resources, metrics,
                     process_model, default_process, operation_runtime,
                     scheduling（纯 Python）
  simulation/        config, scene, layout, physics, conveyor, pioneer,
                     ur5_ik, material_manager, agv, runtime, unit_controller,
                     device_binding
    motion/          contact, collisions, path_planning, trajectory, safety,
                     shadow, suction, conveyor_safety, branch, diagnostics,
                     metrics
    execution/       base（执行契约）, arm（ArmExecutor）, agv（AgvExecutor）
  artifacts/         store（manifest/仓库/校验）, migration
  persistence/       SQLAlchemy 2.x ORM、SQLite Engine/Session、Alembic 迁移、
                     catalog seed 导入/发布/只读仓储、知识图谱导入、运行记录
  application/       lifecycle, coordinator, run, catalog, knowledge, report
  cli.py             唯一命令入口
  tools/neo4j/       本地 Neo4j Compose 与官方 dump 载入脚本
  tools/neo4j_export.py 只读导出 Neo4j 图为知识导入 JSON
  tools/db_dashboard.py 从 SQLite 生成自包含 HTML 仪表盘
```

依赖方向：`cli → application → domain / simulation / artifacts / persistence`。
`persistence` 只导入 `domain`、`util` 与 SQLAlchemy/Alembic，不导入
`simulation`、`application` 或 `cli`；`domain` 不导入 SQLAlchemy、SQLite 或
persistence。ORM 对象不越过 persistence 边界，仓储返回领域数据类。
阶段 0-1 工艺目录的边界：

- `domain/process_model.py` 保存静态 `RecipeSpec`/`OperationTemplate`/
  `RouteVariant`/`OrderEntry`/`ProcessCatalog`；不持有 UID、句柄或时间。
- `domain/default_process.py` 是唯一默认工艺来源；修改 recipe/operation/
  route/设备能力时递增 `PROCESS_MODEL_VERSION`。
- `domain/operation_runtime.py` 的 `OperationInstance` 是任务生命周期字段的
  唯一存储；`domain/tasks.py` 的 `Stage4Task` 旧字段是只读/受控投影。
  阶段 3 起该模块还持有 `RecipeRunContext`、`OperationGraph` 与
  `OperationRuntime`：按入口固定 route variant 实例化工序链、维护前驱/后继
  索引、给出 ready 集合，并在物料账本提交成功后 `commit_success`。任务队列
  只承接已 ready 的实例，依赖未满足的实例留在图中。
- `application/coordinator.py` 从目录读取任务字段；`_spawn_source()` 建图、
  `_dispatch_ready()` 调用 `domain/scheduling.py` 的纯策略为 ready 实例
  选择候选设备（记录候选、拒绝/等待原因与策略版本），再由
  `_poll_unit()` / `_start_transport()` 全量申请资源并启动执行；
  `_commit_operation()` 是唯一成功边界，模板 `successors` 不再驱动后继。
  逻辑合流 `kit_join` 不创建 `Stage4Task`，由 `_check_join` 在两条输入工序
  都提交后建节点并调用 `MaterialRegistry.commit_join()`。
- `domain/scheduling.py` 只消费 `SchedulingSnapshot` 并输出可序列化
  `DispatchDecision`；不导入 simulation/application、不申请资源、不改工序
  状态。`SCHEDULER_POLICY_VERSION` 在 `spec.py`，不进入
  `simulation/config.py` 的冻结配置摘要。
- recipe-backed `Stage4Task` 的生命周期字段只能经 `OperationRuntime` 窄接口
  （`claim_instance`/`select_device`/`mark_waiting`/`mark_blocked`/
  `mark_resumed`/`mark_started`/`commit_success`/`mark_failed` 等）更新；
  `TaskQueue` 只服务非配方任务（空载调机/兼容路径），直接赋值会抛
  `TaskError`。
- `simulation/contract.py` 在 manifest `task_flow` 记录 process model 版本、
  recipe 和路线摘要；旧工件缺少这些字段时按存在才校验处理。


- `domain` 只导入 `domain.rules` 和 `spec`，不导入 CoppeliaSim、文件系统
  实现或 CLI。
- `simulation` 不导入 `application` 或 `cli`。
- `artifacts` 负责 manifest/工件；场景对象校验由 `simulation.contract`
  提供。
- `simulation/unit_controller*.py` 仍保留已验收的继承链
  （Stage1→Stage2→Stage3→Stage4）；组合化重构未实施，不得声称已完成。
- `simulation/execution/` 是阶段 2 的执行适配层：只包装
  `Stage4UnitController` 与 `AGVRouteController4`，不导入 `application`/
  `cli`、不调用 `sim.step()`；唯一 stepping owner 仍是
  `Stage4Coordinator.step()`。`simulation/device_binding.py` 只存
  device_id → alias/资源/区域的静态映射，不存句柄、不写 manifest；
  `alias_roles` 与 `aliases` 一一对应，供 `persistence.scene_binding` 使用。
- `application/catalog.py` 是 persistence 与领域/场景来源的唯一适配层：
  默认 `full_flow` 仍读代码内默认目录；`run --catalog-source db` 只有数据库
  目录与当前代码目录逐字段等价时才切换，否则拒绝启动。
- `persistence/` 采用 SQLAlchemy 2.x ORM + Alembic；revision
  `0001_catalog_core` 是静态目录，`0002_knowledge_graph` 是知识图谱表和
  来源追溯。数据库默认路径 `var/catalog/catalog.db`（`.gitignore`），不得
  写入场景工件目录；连接级 `PRAGMA foreign_keys=ON` 失败即拒绝运行。
- 知识图谱导入只消费 `tools/neo4j_export.py` 的 JSON：原始 `fmpmm.dump`
  由官方 `neo4j-admin` 载入/迁移，项目不解析二进制 dump。`knowledge import`
  只创建 staging；未映射标签/谓词、端点缺失或 canonical ID 冲突会阻止
  `knowledge publish`，不得绕过报告直接改状态。
- 运行记录只允许在 `RunRecorder.start` 与 `RunRecorder.finish` 两个边界写库；
  `sim.step()` 热路径不得执行同步 SQL。写库失败只在运行报告中标记
  `run_record.ok=false`，不回滚仿真、不自动重放；失败/中止运行也写终态。
- `application/coordinator.py` 仍是多岛编排整体；业务规则已抽到 `domain`，
  但“领域决策/仿真动作端口”的进一步拆分未实施。

### 2.1 模块命名约定

v1 保留迁移时的分层后缀，避免改动契约 ID 与已验收工件：

| 文件族 | 现行语义 |
|---|---|
| `config_base` / `layout_base` / `scene_base` / `unit_controller_base` | 基础层：A/B 单站闭环 |
| `config_motion` / `motion/*` / `unit_controller_motion` | 运动层：碰撞、规划、轨迹、安全、吸附 |
| `config_dual` / `layout_dual` / `scene_dual` / `unit_controller_dual` / `runtime_dual` | 双站层：`L2_MIRROR` 双工位 |
| `config` / `layout` / `scene` / `unit_controller` / `runtime` / `coordinator` | UFLOW 层：五岛 `L5_UFLOW` |

`Stage3*`/`Stage4*` 类名、`spec.CONTROLLER_ID`/`COORDINATOR_ID` 和工件
`source_phase=stage4` 是 v1 契约或迁移溯源的一部分，不能单独改名；改名需要
新契约版本和新工件。日常文档使用“基础层 / 运动层 / 双站层 / UFLOW 层”，
不把旧阶段名当作现行包名。

## 3. 时钟与运动控制

- Python 端始终是 clock master：`sim.setStepping(True)` 后每步只
  `sim.step()` 一次，`SIM_STEP = 0.05 s`。
- 五台 UR5 使用内置 `UR5.ttm`，末端为 `suction pad.ttm`，AGV 为
  `pioneer p3dx.ttm`，传送带为 `generic conveyor (belt).ttm`。
- 生产模式只允许 `HYBRID_DYNAMIC`；物理输送不足时沿带切线补偿，有速度上限
  并记录原因。
- 机械臂运动阶段：APPROACHING → ATTACHING → LIFTING → TRANSFERRING →
  PLACING → RELEASING → RETREATING；带载水平段 `H_transfer = 0.65 m`。
- 规划/碰撞/安全容差在 `simulation/config_motion.py` 和
  `simulation/config.py` 中冻结；重构不得放宽。

## 4. 场景命名与句柄

- 对象 alias 必须是扁平下划线形式；禁止把 `/` 传给 `setObjectAlias`。
- 运行时句柄在加载后一次性建立并缓存；每步循环不得重复枚举全场景。
- 固定位置检测使用 proximity sensor；传感器命中只表示物理条件满足。
- 会话资源前缀 `Shadow_`；保存后的工件不得包含影子臂/碰撞集合/载荷副本。

## 5. 物理与物料

- 物料 `MATERIAL_SIZE=(0.10,0.10,0.10)`、`MATERIAL_MASS=0.10`、
  `MATERIAL_FRICTION=0.6`、respondable mask `0xFFFF`。
- 物料中心 = 承载面 + `MATERIAL_HALF_HEIGHT`；吸盘接触面在物料顶面。
- E 岛配对：`SEMI_CE + PART_DE → FINAL`，原子消费，重复父对拒绝；
  缺件不生成 FINAL。
- 区域容量/背压：每条线、交接、缓冲、载台默认 1 个占位；输出区 4。

## 6. 资源与安全

- 资源表固定顺序，任务一次性申请完整资源集（全有或全无）；任一忙则不持有
  部分资源。
- AGV 航线要求全部 dock 空闲；航线持有实际经过的直道资源。
- 臂间互锁区按基座间距导出；相邻臂不同时运动。
- 失败/停止释放普通调度锁；安全锁存由运动层独立持有，不由资源释放清除。
- 安全锁存、航线失败、停带故障后冻结现场，任务不得改写成 DONE。

## 7. v1 工件与生命周期

- 工件根：`docs/scene_artifacts/v1/`；`current.json` 指向当前工件。
- manifest schema `2.0`；`source_phase="v1"`；契约键 `extra.v1`。
- manifest 记录 `schema_version`、`product_version`、场景契约版本、配置/
  布局摘要、场景 SHA-256、来源 provenance、对象 alias、物理/脚本策略。
- `load` 只加载工件，不构建、不清场；`start/run` 先取得运行锁再执行完整
  只读校验；`save/migrate` 失败不更新 current。
- 已验收工件 `protected: true`、文件只读；不得手工修改 manifest 或用 GUI
  覆盖 `scene.ttt`。
- v1 运行期只比较 v1 自身 config/layout 摘要；stage2/3 来源只记在
  `provenance`，不比较其当前摘要。

## 8. 实测陷阱（CoppeliaSim 4.10）

- 内置 conveyor 的 `__config__.accel` 默认 0.05 m/s²，停车要数秒；构建期
  改为 2.0，0.2 m/s 下约 0.10 s 停住。
- 必须剥离 UR5/suction pad/pioneer 的 customization/child script，避免
  4.10 启动冻结与外部控制冲突。
- `setObjectParent` 挂父会清掉被挂 shape 的 `static`；UR5 基座挂到 island
  后必须写回 `static=1`。
- alias 中的 `/` 会被 4.10 转成 `_`；`getObject("/path")` 只用于路径查询，
  不作为运行时句柄来源。
- 规划/碰撞查询在仿真停止态与运行态的句柄行为不同；影子对象只在停止态
  创建/销毁。
- 运行结束必须 `stopSimulation()` 后销毁影子臂/碰撞集合；保存工件前不得
  残留 `Shadow_*`。

## 9. 测试与证据

- 纯逻辑测试：`python -m pytest -q`（无仿真环境）。本轮新增目录持久化
  测试 `tests/test_v1_catalog_persistence.py`、知识图谱测试
  `tests/test_v1_knowledge_graph.py` 和运行记录测试
  `tests/test_v1_run_records.py`；完整回归计数未在这些实施轮刷新，见
  [目录实施报告](docs/implementation/reports/v1-catalog-persistence.md)、
  [知识图谱报告](docs/implementation/reports/v1-knowledge-graph-import.md) 与
  [运行记录报告](docs/implementation/reports/v1-run-record-persistence.md)。
- 目录数据库验证：`flexible-factory catalog init/import/publish/verify/diff`
  （不需要 CoppeliaSim）；数据库默认 `var/catalog/catalog.db`。
- 运行记录验证：`flexible-factory report runs/run/genealogy/waits/timeline`
  （不需要 CoppeliaSim）；`run` 默认在开始/结束批量写入，`--run-records off`
  可关闭。
- 知识图谱验证：`flexible-factory knowledge import/status/verify/publish`
  （不需要 CoppeliaSim）；真实 dump 的载入/导出需要 Docker Desktop 和
  Neo4j；操作步骤见 [tools/neo4j/README.md](tools/neo4j/README.md)，实测结果以
  [知识图谱实施报告](docs/implementation/reports/v1-knowledge-graph-import.md)为准。
- 设备级 focused 验收与阶段 4 `full_flow`（需要运行中的 CoppeliaSim 4.10 +
  ZMQ 23000）：每个文件单独一次进程，

      FLEXIBLE_FACTORY_SIM=1 python -m pytest -q tests/test_v1_stage2_arm_sim.py -s
      FLEXIBLE_FACTORY_SIM=1 python -m pytest -q tests/test_v1_stage2_agv_sim.py -s
      FLEXIBLE_FACTORY_SIM=1 python -m pytest -q tests/test_v1_stage4_scheduler_sim.py -s

  用例由 `tests/stage2_harness.py` 加载当前工件并只调用
  `coordinator.step()`；阶段 4 用例运行完整 `full_flow` 批次，证据写入
  `docs/implementation/runs/`。
- 实机验收：`flexible-factory run --scenario full_flow --time 700`；报告在
  `docs/implementation/runs/`，正式证据摘要复制到
  `docs/implementation/evidence/`。
- 无 CoppeliaSim 时集成测试必须明确标为未执行，不得报告为通过。
