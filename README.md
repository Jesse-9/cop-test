# Flexible Factory v1

基于 CoppeliaSim 4.10+、Python 和 ZeroMQ Remote API 的柔性工业车间 v1。
v1 只交付五岛 `L5_UFLOW` 场景；用户运行模式为 `full_flow`，入口为
`flexible-factory`。阶段一至阶段四代码已按职责迁移到
`src/flexible_factory/`，旧 `stage1`–`stage4`、`stage1_1` 和 `reference`
包已删除，仅保留迁移来源场景包与 v1 报告中的历史记录。

## 从哪里开始

- 安装、ZMQ 连接、场景包操作与 v1 运行流程：[项目使用指南](docs/wiki/98-ProjectUsage/USAGE.md)
- CLI 全部命令、参数、默认值和示例：[CLI 参数手册](docs/wiki/98-ProjectUsage/CLI_REFERENCE.md)
- 实施方案、基线、报告与证据：[实施资料索引](docs/implementation/README.md)
- 场景包清单、保护策略、current 指针和迁移规则：[场景包说明](docs/scene_artifacts/README.md)
- 工程不变量、模块职责和实测陷阱：[工程参考](PROJECT_ENGINEERING_REFERENCE.md)
- CoppeliaSim 知识库：[知识库索引](docs/wiki/index.md)
- 文档导航与 Neo4j 维护入口：[文档导航](docs/README.md) · [Neo4j 工具](tools/neo4j/README.md)

## 快速开始

    uv sync
    flexible-factory load
    flexible-factory validate      # 可选诊断; run 仍会在启动点全量校验
    flexible-factory run --scenario full_flow --time 700

`load` 只加载 `docs/scene_artifacts/v1/current.json` 指向的 v1 场景包，
不会隐式构建或清场。`run` 先取得运行锁并执行只读校验；校验失败拒绝启动。
`--scenario` 的其他取值仅用于内部回归/诊断。

## 当前状态

当前场景包与阶段交付状态以[场景版本包说明](docs/scene_artifacts/README.md)和
[实施资料索引](docs/implementation/README.md)为准；报告和证据是测试与验收事实的权威记录。

MCP 和自动故障恢复不在当前 v1 能力范围；安全锁存不会自动清除。

## 入口与结构

    src/flexible_factory/
      domain/        物料、任务、资源、指标、工艺目录、工序运行图、订单 DAG
                     契约/校验/编译、故障分类 (纯 Python)
      simulation/    场景、布局、物理、运动、AGV、运行时、device_binding
        execution/   设备执行适配层（臂/AGV 包装，不调用 sim.step）
      artifacts/     v1 manifest、场景包仓库、迁移
      persistence/   静态目录/知识表/运行记录/版本化订单 ORM、
                     SQLite/Alembic 迁移、导入/发布/仓储
      application/   生命周期、运行用例、多岛协调、目录/知识用例、
                     订单服务与 worker、自主 planner
      api/           本地订单 HTTP adapter（仅传输层）
      cli.py         唯一 v1 命令入口
    frontend/        独立 React/TypeScript 订单工作台（只消费 /api）
      spec.py        产品/契约/schema 版本
    tools/
      neo4j/         本地 Neo4j Compose、官方 dump 载入脚本与说明
      neo4j_export.py 只读导出 Neo4j 图为知识导入 JSON
      db_dashboard.py 从本地 SQLite 生成自包含 HTML 仪表盘
      measure_startup_stages.py 测量连接→仿真启动各阶段耗时 (不运行场景)

旧阶段包（`src/stage1/` … `src/stage4/`、`src/stage1_1/`、
`src/reference/`）、`run_stage*.py` 入口、旧报告与旧证据均已删除，不得被
v1 运行路径导入。v1 行为与验收结论以
[V1 实施报告](docs/implementation/reports/product/v1-product-implementation.md) 为准；运行方式以
[项目使用指南](docs/wiki/98-ProjectUsage/USAGE.md) 与实际 `--help` 为准。

安全锁存在失败/停止后冻结原任务，不自动恢复；统一故障恢复仍未实现。
