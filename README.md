# Flexible Factory v1

基于 CoppeliaSim 4.10+、Python 和 ZeroMQ Remote API 的柔性工业车间 v1。
v1 只交付五岛 `L5_UFLOW` 场景；用户运行模式为 `full_flow`，入口为
`flexible-factory`。阶段一至阶段四代码已按职责迁移到
`src/flexible_factory/`，旧 `stage1`–`stage4`、`stage1_1` 和 `reference`
包已删除，仅保留迁移来源工件与 v1 报告中的历史记录。

## 从哪里开始

- 安装、ZMQ 连接、工件操作与 v1 运行命令：[项目使用指南](docs/wiki/98-ProjectUsage/USAGE.md)
- v1 收敛方案、基线与验收记录：[V1 方案](docs/implementation/V1_CONSOLIDATION_PLAN.md) · [M0 基线](docs/implementation/V1_BASELINE.md) · [v1 报告](docs/implementation/V1_REPORT.md)
- 工件 schema、保护策略、current 指针和迁移规则：[场景工件说明](docs/scene_artifacts/README.md)
- 阶段 2 设备绑定与执行适配器：[方案](docs/implementation/V1_STAGE2_EXECUTOR_PLAN.md) · [实施报告](docs/implementation/V1_STAGE2_REPORT.md)
- 阶段 3 配方实例化与工序 DAG：[规划](docs/wiki/06-工业案例/柔性车间/当前规划/06-21-混线生产阶段3配方实例化与工序DAG.md) · [实施报告](docs/implementation/V1_STAGE3_OPERATION_DAG_REPORT.md)
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
| 设备执行适配层 | 阶段 2 完成：`device_binding` 静态绑定 + `simulation/execution/`（臂/AGV 适配器），见 [阶段 2 报告](docs/implementation/V1_STAGE2_REPORT.md) |
| 工序运行图 | 阶段 3 完成：入口固定选路 → `OperationGraph` → ready 集合解锁 → E 合流绑定父实例，`full_flow` 实机验收通过，见 [阶段 3 报告](docs/implementation/V1_STAGE3_OPERATION_DAG_REPORT.md)；阶段 4–5 未实现 |
| 测试 | `python -m pytest -q`（426 passed / 2 skipped）；两项 focused 设备验收需 `FLEXIBLE_FACTORY_SIM=1` 与运行中的 CoppeliaSim |
| MCP | 不在 v1 范围；旧 `factory-sim` 注册和实现已移除 |
| 故障自动恢复 | 未实现；安全锁存不得自动清除 |
| 历史阶段 | 旧阶段源码、入口、报告和证据已清理；迁移来源工件见 [场景工件说明](docs/scene_artifacts/README.md) |

## 入口与结构

    src/flexible_factory/
      domain/        物料、任务、资源、指标、工艺目录、工序运行图 (纯 Python)
      simulation/    场景、布局、物理、运动、AGV、运行时、device_binding
        execution/   设备执行适配层（臂/AGV 包装，不调用 sim.step）
      artifacts/     v1 manifest、工件仓库、迁移
      application/   生命周期、运行用例、多岛协调
      cli.py         唯一 v1 命令入口
      spec.py        产品/契约/schema 版本

旧阶段包（`src/stage1/` … `src/stage4/`、`src/stage1_1/`、
`src/reference/`）、`run_stage*.py` 入口、旧报告与旧证据均已删除，不得被
v1 运行路径导入。v1 行为与验收结论以
[V1 实施报告](docs/implementation/V1_REPORT.md) 为准；运行方式以
[项目使用指南](docs/wiki/98-ProjectUsage/USAGE.md) 与实际 `--help` 为准。

安全锁存在失败/停止后冻结原任务，不自动恢复；统一故障恢复仍未实现。
