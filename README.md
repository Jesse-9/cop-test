# Flexible Factory v1

基于 CoppeliaSim 4.10+、Python 和 ZeroMQ Remote API 的柔性工业车间 v1。
v1 只交付五岛 `L5_UFLOW` 场景；用户运行模式为 `full_flow`，入口为
`flexible-factory`。阶段一至阶段四代码已按职责迁移到
`src/flexible_factory/`，旧 `stage1`–`stage4`、`stage1_1` 和 `reference`
包已删除，仅保留迁移来源工件与 v1 报告中的历史记录。

## 从哪里开始

- 安装、ZMQ 连接、工件操作与 v1 运行命令：[项目使用指南](docs/wiki/98-ProjectUsage/USAGE.md)
- v1 收敛方案、基线与验收记录：[V1 方案](docs/implementation/governance/v1-scope-and-migration-plan.md) · [M0 基线](docs/implementation/governance/v1-baseline-m0.md) · [v1 报告](docs/implementation/reports/v1-product-implementation.md)
- 工件 schema、保护策略、current 指针和迁移规则：[场景工件说明](docs/scene_artifacts/README.md)
- 阶段 2 设备绑定与执行适配器：[方案](docs/implementation/plans/v1-stage-02-device-executor-implementation.md) · [实施报告](docs/implementation/reports/v1-stage-02-device-executor.md)
- 阶段 3 配方实例化与工序 DAG：[规划](docs/wiki/06-工业案例/柔性车间/当前规划/06-21-混线生产阶段3配方实例化与工序DAG.md) · [实施报告](docs/implementation/reports/v1-stage-03-operation-dag.md)
- 阶段 4 能力匹配调度：[方案](docs/implementation/plans/v1-stage-04-capability-scheduling-implementation.md) · [实施报告](docs/implementation/reports/v1-stage-04-capability-scheduling.md)
- 静态资源/工艺目录数据库化：[方案](docs/implementation/plans/resource-catalog-database-and-knowledge-graph.md) · [实施报告](docs/implementation/reports/v1-catalog-persistence.md)
- 知识图谱导入（Neo4j dump）：[实施报告](docs/implementation/reports/v1-knowledge-graph-import.md) · [本地 Neo4j 工具](tools/neo4j/README.md)
- 运行记录与 Q1–Q4 查询：[实施报告](docs/implementation/reports/v1-run-record-persistence.md)
- 工程不变量、模块职责和实测陷阱：[工程参考](PROJECT_ENGINEERING_REFERENCE.md)
- 知识库结构与写作规范：[docs/README.md](docs/README.md) · [docs/CLAUDE.md](docs/CLAUDE.md)

## 快速开始

    uv sync
    flexible-factory load
    flexible-factory validate      # 可选诊断; run 仍会在启动点全量校验
    flexible-factory run --scenario full_flow --time 700

`load` 只加载 `docs/scene_artifacts/v1/current.json` 指向的 v1 工件，
不会隐式构建或清场。`run` 先取得运行锁并执行只读校验；校验失败拒绝启动。
`--scenario` 的其他取值仅用于内部回归/诊断。

## 当前状态

| 项目 | 状态 |
|---|---|
| v1 产品范围 | 五岛 `L5_UFLOW`、`full_flow` 唯一用户入口 |
| v1 当前工件 | `v1/L5_UFLOW/v001_20261103_004317`（由 stage4 `L5_UFLOW/v004_20261029_133249` 迁移，源工件只读） |
| 代码入口 | `src/flexible_factory/cli.py`（console script `flexible-factory`） |
| 工艺目录 | 阶段 0-1 静态 `RecipeSpec`/`ProcessCatalog` 已接入 `flexible_factory` |
| 设备执行适配层 | 阶段 2 完成：`device_binding` 静态绑定 + `simulation/execution/`（臂/AGV 适配器），见 [阶段 2 报告](docs/implementation/reports/v1-stage-02-device-executor.md) |
| 工序运行图 | 阶段 3 完成：入口固定选路 → `OperationGraph` → ready 集合解锁 → E 合流绑定父实例，见 [阶段 3 报告](docs/implementation/reports/v1-stage-03-operation-dag.md)；阶段 4 完成：能力匹配调度、设备选择决策与 runtime 写入口收敛，`full_flow` 实机验收通过，见 [阶段 4 报告](docs/implementation/reports/v1-stage-04-capability-scheduling.md)；阶段 5 未实现 |
| 静态目录数据库 | 阶段 0–3 完成：SQLAlchemy 2.x + Alembic/SQLite，默认目录可导入、校验、发布并重建等价目录；默认运行仍读代码目录，显式 `--catalog-source db` 才切换，见 [实施报告](docs/implementation/reports/v1-catalog-persistence.md) |
| 知识图谱导入 | 阶段 5 完成：`fmpmm.dump` 经官方 Neo4j 载入/只读导出，`knowledge import` 写 staging 并经审核发布；真实图 222 节点/384 关系已成功导入并发布，见 [实施报告](docs/implementation/reports/v1-knowledge-graph-import.md) |
| 运行记录 | 阶段 4 完成：运行开始/结束批量写入 `production_run`/工序/物料/谱系/资源事件，提供 `report run/genealogy/waits/timeline`（Q1–Q4）；写库失败只标记持久化失败，见 [实施报告](docs/implementation/reports/v1-run-record-persistence.md)。用户实机 `full_flow` 仿真成功；首次运行记录因 `OperationRuntime.instances()` 缺失失败，已修复，待重跑生成成功记录 |
| 测试 | 纯逻辑测试 `python -m pytest -q`；本轮新增 23 个目录持久化、10 个知识图谱、14 个运行记录测试，完整回归计数未刷新（见实施报告）。三项 focused/实机验收需 `FLEXIBLE_FACTORY_SIM=1` 与运行中的 CoppeliaSim |
| MCP | 不在 v1 范围；旧 `factory-sim` 注册和实现已移除 |
| 故障自动恢复 | 未实现；安全锁存不得自动清除 |
| 历史阶段 | 旧阶段源码、入口、报告和证据已清理；迁移来源工件见 [场景工件说明](docs/scene_artifacts/README.md) |

## 入口与结构

    src/flexible_factory/
      domain/        物料、任务、资源、指标、工艺目录、工序运行图 (纯 Python)
      simulation/    场景、布局、物理、运动、AGV、运行时、device_binding
        execution/   设备执行适配层（臂/AGV 包装，不调用 sim.step）
      artifacts/     v1 manifest、工件仓库、迁移
      persistence/   静态目录/知识表 ORM、SQLite/Alembic 迁移、导入/发布/仓储
      application/   生命周期、运行用例、多岛协调、目录 seed/知识导入用例
      cli.py         唯一 v1 命令入口
      spec.py        产品/契约/schema 版本
    tools/
      neo4j/         本地 Neo4j Compose、官方 dump 载入脚本与说明
      neo4j_export.py 只读导出 Neo4j 图为知识导入 JSON
      db_dashboard.py 从本地 SQLite 生成自包含 HTML 仪表盘

旧阶段包（`src/stage1/` … `src/stage4/`、`src/stage1_1/`、
`src/reference/`）、`run_stage*.py` 入口、旧报告与旧证据均已删除，不得被
v1 运行路径导入。v1 行为与验收结论以
[V1 实施报告](docs/implementation/reports/v1-product-implementation.md) 为准；运行方式以
[项目使用指南](docs/wiki/98-ProjectUsage/USAGE.md) 与实际 `--help` 为准。

安全锁存在失败/停止后冻结原任务，不自动恢复；统一故障恢复仍未实现。
