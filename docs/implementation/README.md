# 实施资料归档

本目录保存项目治理、主题方案、实施报告、技术参考和正式验收证据。柔性车间业务目标、工艺路线与领域模型见[业务资料导航](../wiki/06-工业案例/柔性车间/README.md)；v1 实现状态、未完成项和 v2 风险以[启动准备度盘点](governance/v2-readiness-assessment.md)为当前总表。

## 目录边界

| 目录 | 职责 |
|---|---|
| [governance/](governance/) | 范围、基线、文档规则和当前阶段总表 |
| [plans/](plans/) | 按业务主题归档实施方案及技术提案 |
| [reports/](reports/) | 按业务主题归档实施事实、测试结果和未覆盖边界 |
| [reference/](reference/) | 方案输入、字段盘点和技术操作参考，不单独作为验收结论 |
| [evidence/](evidence/) | 随版本维护、由报告引用的精简正式证据 |
| [runs/](runs/) | Git 忽略的临时运行输出，不作为正式归档证据 |

## 治理与产品基线

| 文档 | 用途 |
|---|---|
| [v2 启动准备度与 v1 阶段盘点](governance/v2-readiness-assessment.md) | 当前唯一的跨主题状态总表、风险与 v2 进入门槛 |
| [项目文档登记表](governance/project-document-register.md) | 业务规划、方案、报告与证据的职责及阶段映射 |
| [v1 M0 基线](governance/v1-baseline-m0.md) | v1 初始范围、场景包、环境和测试冻结记录 |
| [v1 范围与迁移方案](governance/v1-scope-and-migration-plan.md) | v1 收敛目标、迁移决策和退出条件 |
| [v1 产品实施报告](reports/product/v1-product-implementation.md) | v1 产品迁移交付事实；后续阶段以专题报告为准 |

## 主题档案

### 生产流程

业务阶段 0–4、设备执行、工序图、能力调度和有限批次示范。业务阶段 5 的调度优化与恢复仍暂缓，见[阶段 5 业务规划](../wiki/06-工业案例/柔性车间/当前规划/06-23-混线生产阶段5调度优化与有限恢复.md)。

| 类型 | 文档 |
|---|---|
| 方案 | [阶段 2 设备执行适配](plans/production-flow/v1-stage-02-device-executor-implementation.md) · [阶段 4 能力调度](plans/production-flow/v1-stage-04-capability-scheduling-implementation.md) · [混线生产灵活性示范](plans/production-flow/v1-mixed-production-flexibility-demo.md) |
| 报告 | [阶段 0–1 静态工艺目录](reports/production-flow/v1-stage-00-01-static-process-catalog.md) · [阶段 2 设备执行](reports/production-flow/v1-stage-02-device-executor.md) · [阶段 3 工序 DAG](reports/production-flow/v1-stage-03-operation-dag.md) · [阶段 4 能力调度](reports/production-flow/v1-stage-04-capability-scheduling.md) · [混线灵活性示范](reports/production-flow/v1-mixed-production-flexibility-demo.md) |
| 证据 | [生产流程验收证据目录](evidence/README.md) |

### 订单编排

订单契约、失败隔离、API/持久化、确定性 planner、前端和限定范围的仿真实机验收。阶段 1–6 的限制和未准入故障以各阶段报告为准。

| 类型 | 文档 |
|---|---|
| 方案 | [总体路线图](plans/order-orchestration/v1-self-built-order-dag-orchestration.md) · [阶段 1 契约与编译](plans/order-orchestration/v1-order-dag-phase-01-foundation.md) · [阶段 2 失败隔离](plans/order-orchestration/v1-order-dag-phase-02-failure-isolation.md) · [阶段 3 API 与持久化](plans/order-orchestration/v1-order-dag-phase-03-api-persistence.md) · [阶段 4 planner](plans/order-orchestration/v1-order-dag-phase-04-auto-planner.md) · [阶段 5 前端](plans/order-orchestration/v1-order-dag-phase-05-frontend.md) · [阶段 5 UI 规格](plans/order-orchestration/v1-order-dag-phase-05-ui-design-spec.md) · [阶段 6 仿真验收](plans/order-orchestration/v1-order-dag-phase-06-simulation-acceptance.md) |
| 报告 | [阶段 1–3](reports/order-orchestration/v1-order-dag-phase-01-03.md) · [阶段 4](reports/order-orchestration/v1-order-dag-phase-04-auto-planner.md) · [阶段 5 前端](reports/order-orchestration/v1-order-dag-phase-05-frontend.md) · [阶段 6 仿真验收](reports/order-orchestration/v1-order-dag-phase-06-simulation-acceptance.md) |
| 证据 | [订单编排阶段 6 验收证据](evidence/order-orchestration/v1_order_dag_phase6_acceptance_20261108.json) |

### 数据平台

静态目录、运行记录、知识图谱导入和离线数据库查看工具。

| 类型 | 文档 |
|---|---|
| 方案 | [资源目录数据库化与知识图谱](plans/data-platform/resource-catalog-database-and-knowledge-graph.md) · [数据库仪表盘优化](plans/data-platform/db-dashboard-frontend-optimization.md) · [仪表盘 UI 蓝图](plans/data-platform/db-dashboard-ui-blueprint.md) |
| 报告 | [目录持久化](reports/data-platform/v1-catalog-persistence.md) · [运行记录持久化](reports/data-platform/v1-run-record-persistence.md) · [知识图谱导入](reports/data-platform/v1-knowledge-graph-import.md) |
| 参考 | [静态目录字段盘点](reference/data-platform/static-catalog-field-inventory.md) · [数据库查看指南](reference/data-platform/database-inspection-guide.md) |
| 证据 | [数据平台验收证据目录](evidence/README.md) |

### 仿真工程

仿真模块职责、代码拆分、运动规划性能提案和启动性能计时。

| 类型 | 文档 |
|---|---|
| 方案 | [仿真模块代码优化](plans/simulation/simulation-code-optimization-plan.md) · [运动规划性能提案](plans/simulation/simulation-motion-planning-performance.md) · [启动性能优化](plans/simulation/v1-startup-performance-optimization.md) |
| 报告 | [仿真模块代码优化报告](reports/simulation/v1-simulation-code-optimization.md) · [启动性能优化报告](reports/simulation/v1-startup-performance-optimization.md) |
| 参考 | [仿真模块清单](reference/simulation/simulation-module-inventory.md) |
| 证据 | [启动阶段计时证据](evidence/simulation/) |

## 归档与维护规则

- 业务规划回答“建设什么”；实施方案回答“准备如何实现”；报告回答“实际完成什么”；证据保存可核验的验收摘要。各类文件按主题放入对应目录。
- 文件状态以 frontmatter 为准；跨主题当前状态只在 v2 准备度盘点维护。报告保留其执行时点的测试和验收事实，不把旧计数解释为当前测试结果。
- 方案与报告有不同职责，不因内容引用相同而视为重复。只有内容、用途和证据来源均被另一个权威文档完整取代时，才合并或移除旧文档。
- 新增或迁移文档时，更新本索引、[文档登记表](governance/project-document-register.md)和所有受影响的相对链接；正式证据同时更新[evidence 目录](evidence/README.md)。
