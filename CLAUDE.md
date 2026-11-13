# cop-test 项目索引

本文件作为仓库级 AI 工作索引，保持短小，避免把阶段报告、运行手册和工程细节重复注入每次工作上下文。开始任务时按下表读取所需资料；文档与代码冲突时，以当前实现和可复现证据为准，并修正文档。

## 必须遵循的项目不变量

- 不覆盖用户未提交的场景、日志、文档或配置；禁止使用破坏性回退丢弃改动。
- 当前主线是 **v1 五岛 `L5_UFLOW` 产品**：`src/flexible_factory/`，唯一用户入口 `flexible-factory`，唯一用户运行模式 `full_flow`。
- `src/stage1/`–`src/stage4/`、`src/stage1_1/`、`src/reference/` 已删除；v1 运行路径不得重新引入旧阶段包。
- 控制器使用 CoppeliaSim 4.10+ ZeroMQ Remote API；正式模型和控制约束、物理/UID/停止联锁要求见 [工程参考](PROJECT_ENGINEERING_REFERENCE.md)。
- 场景 start 不隐式构建或清空；必须加载已校验的 v1 工件。迁移与派生不得覆盖保留的迁移来源工件或 v1 已验收工件。
- 代码、场景、运行命令和文档需保持一致；运行方式或入口变化时同步更新 [项目使用指南](docs/wiki/98-ProjectUsage/USAGE.md)。
- 需要从命令行启动 CoppeliaSim 并留存控制台日志时，优先使用 `tools/conpty_relay.py`；该脚本会把控制台输出实时写入 `--log` 指定文件。它不是 pytest 钩子，测试运行不会自动调用；依赖缺失时先执行 `uv sync --group dev`。
- 阶段三安全锁存不得自动恢复；统一故障恢复和阶段四之后的混线规划未实现。不得将计划描述成已具备能力。
- 静态目录数据库（`persistence/`）只读/发布静态资源与工艺目录；默认运行仍读代码内 `default_catalog()`，`--catalog-source db` 必须与当前代码目录逐字段等价。数据库默认 `var/catalog/catalog.db`，不得写入场景工件目录。运行记录按 Q1–Q4 在运行开始/结束批量写入，禁止在 `sim.step()` 热路径写库；写库失败只标记持久化失败，不回滚仿真或自动重放。真实 `fmpmm.dump` 已通过官方 Neo4j 载入/导出并成功导入 222 实体/384 关系；不得把未重新执行的场景描述为已验证。
- MCP 不在 v1 范围；`.mcp.json` 与旧 `factory-sim` 实现已移除。

## 按任务读取

| 工作 | 规范或唯一/权威资料 |
|---|---|
| 启动 CoppeliaSim、配置 ZMQ、加载工件、运行 v1 | [项目使用指南](docs/wiki/98-ProjectUsage/USAGE.md) |
| v1 产品边界、代码结构、迁移与验收 | [V1 收敛方案](docs/implementation/governance/v1-scope-and-migration-plan.md) · [M0 基线](docs/implementation/governance/v1-baseline-m0.md) · [v1 报告](docs/implementation/reports/product/v1-product-implementation.md) |
| 工程不变量、当前模块职责、实测陷阱 | [工程参考](PROJECT_ENGINEERING_REFERENCE.md) |
| 工件 schema、保护策略、current 指针和派生规则 | [场景工件说明](docs/scene_artifacts/README.md) |
| 混线阶段 2：设备绑定与执行适配器 | [阶段 2 方案](docs/implementation/plans/production-flow/v1-stage-02-device-executor-implementation.md) · [阶段 2 报告](docs/implementation/reports/production-flow/v1-stage-02-device-executor.md) |
| 混线阶段 3：配方实例化与工序 DAG | [阶段 3 规划](docs/wiki/06-工业案例/柔性车间/当前规划/06-21-混线生产阶段3配方实例化与工序DAG.md) · [阶段 3 报告](docs/implementation/reports/production-flow/v1-stage-03-operation-dag.md) |
| 静态目录数据库：schema、导入/发布、影子对比与选库 | [实施报告](docs/implementation/reports/data-platform/v1-catalog-persistence.md) · [字段盘点](docs/implementation/reference/data-platform/static-catalog-field-inventory.md) · [方案](docs/implementation/plans/data-platform/resource-catalog-database-and-knowledge-graph.md) · [使用指南](docs/wiki/98-ProjectUsage/USAGE.md) |
| 知识图谱导入：Neo4j dump 载入/导出、staging 与发布 | [实施报告](docs/implementation/reports/data-platform/v1-knowledge-graph-import.md) · [Neo4j 工具](tools/neo4j/README.md) · [使用指南](docs/wiki/98-ProjectUsage/USAGE.md) |
| 运行记录：批量写入与 Q1–Q4 查询 | [实施报告](docs/implementation/reports/data-platform/v1-run-record-persistence.md) · [使用指南](docs/wiki/98-ProjectUsage/USAGE.md) |
| 启动阶段耗时测量与性能诊断 | [使用指南](docs/wiki/98-ProjectUsage/USAGE.md) · [计时脚本](tools/measure_startup_stages.py) · [优化方案](docs/implementation/plans/simulation/v1-startup-performance-optimization.md) · [实施报告](docs/implementation/reports/simulation/v1-startup-performance-optimization.md) |
| 本地数据库可视化 | [仪表盘工具](tools/db_dashboard.py) · [查看指南](docs/implementation/reference/data-platform/database-inspection-guide.md) · [使用指南](docs/wiki/98-ProjectUsage/USAGE.md) |
| 项目状态与代码结构 | [README.md](README.md) |
| 历史阶段结论与未来边界 | [V1 实施报告](docs/implementation/reports/product/v1-product-implementation.md) |
| wiki 笔记 schema、标签、撰写流程 | [docs/CLAUDE.md](docs/CLAUDE.md) |

只在任务需要时读取链接文档。改动相关规范或能力后，再更新对应的权威资料，不复制整段内容到本文件。
