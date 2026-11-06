# 实施资料

本目录按项目管理职责归档 v1 的方案、基线、实施报告和证据。业务目标、工艺路线
与领域对象见 [柔性车间资料导航](../wiki/06-工业案例/柔性车间/README.md)。
两边的职责和阶段对应见
[项目文档登记表](governance/project-document-register.md)。

## 目录

| 目录 | 内容 |
|---|---|
| [governance/](governance/) | 项目登记规则、v1 范围/迁移方案和 M0 基线 |
| [plans/](plans/) | 阶段实施方案及跨阶段技术提案 |
| [reports/](reports/) | 产品、阶段和技术专题实施报告，记录实际结果与限制 |
| [reference/](reference/) | 实施方案的输入盘点及技术参考 |
| [evidence/](evidence/) | 报告引用的正式、精简验收证据 |
| [runs/](runs/) | 被 Git 忽略的临时运行输出；不作为正式证据 |

## 项目治理与基线

- [v1 范围与迁移方案](governance/v1-scope-and-migration-plan.md)：产品边界、迁移映射、工件策略和实施门槛。
- [v1 M0 基线](governance/v1-baseline-m0.md)：冻结范围、决策、代码与工件基线。
- [项目文档登记表](governance/project-document-register.md)：业务规划与实施资料的分层、对应和命名。

## 阶段实施

| 阶段/主题 | 实施方案 | 实施报告 |
|---|---|---|
| v1 产品收敛 | [范围与迁移方案](governance/v1-scope-and-migration-plan.md) | [产品实施报告](reports/v1-product-implementation.md) |
| 阶段 0–1 静态工艺目录 | [业务规划](../wiki/06-工业案例/柔性车间/当前规划/06-19-混线生产阶段0-1基线与静态工艺目录.md) | [工艺目录移植报告](reports/v1-stage-00-01-static-process-catalog.md) |
| 阶段 2 设备执行适配 | [实施方案](plans/v1-stage-02-device-executor-implementation.md) | [实施报告](reports/v1-stage-02-device-executor.md) |
| 阶段 3 工序 DAG | [业务规划](../wiki/06-工业案例/柔性车间/当前规划/06-21-混线生产阶段3配方实例化与工序DAG.md) | [实施报告](reports/v1-stage-03-operation-dag.md) |
| 阶段 4 能力匹配调度 | [实施方案](plans/v1-stage-04-capability-scheduling-implementation.md) | [实施报告](reports/v1-stage-04-capability-scheduling.md) |

## 跨阶段方案与专项报告

- [混线生产灵活性扩展示范方案](plans/v1-mixed-production-flexibility-demo.md)与[实施报告](reports/v1-mixed-production-flexibility-demo.md)：WP1/WP2 已实施并完成实机验收；WP3/WP4 仍为条件包。
- [资源目录数据库化与知识图谱方案](plans/resource-catalog-database-and-knowledge-graph.md)：阶段 0–6 的总体架构与门槛。
- [仿真与运动规划性能方案](plans/simulation-motion-planning-performance.md)：路径复用和性能提案。
- [仿真模块代码优化方案](plans/simulation-code-optimization-plan.md)与[实施报告](reports/v1-simulation-code-optimization.md)：P1/P2、P3 5.1/5.3 与第 6 节已实施；P3 5.2 未实施。
- [仿真模块清单](reference/simulation-module-inventory.md)：仿真包模块职责、兼容层与遗留代码处置。
- [静态目录字段盘点](reference/static-catalog-field-inventory.md)：数据库化方案阶段 0 的输入盘点。
- [静态目录持久化报告](reports/v1-catalog-persistence.md)：数据库 schema、导入、发布及选库验证。
- [运行记录持久化报告](reports/v1-run-record-persistence.md)：运行写入及 Q1–Q4 查询。
- [知识图谱导入报告](reports/v1-knowledge-graph-import.md)：Neo4j dump 导出、staging 和审核发布。

提案状态不表示已实现。能力完成情况以对应实施报告和 `evidence/` 为准；旧阶段运行输出留在 `runs/`，不要当成验收证据。
