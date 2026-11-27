# 实施资料归档

本目录保存项目治理、主题方案、实施报告、技术参考和正式验收证据。柔性车间业务目标、工艺路线与领域模型见[业务资料导航](../wiki/06-工业案例/柔性车间/README.md)；v1 实现状态、未完成项和 v2 风险以[启动准备度盘点](governance/v2-readiness-assessment.md)为当前总表。

## 目录边界

| 目录 | 职责 |
|---|---|
| [governance/](governance/) | 范围、基线、文档规则和当前阶段总表 |
| [plans/](plans/README.md) | **仅未实施**的活动规划及技术提案 |
| [archive-plans/](archive-plans/README.md) | 已完成规划原文，供历史追溯；不再作为活动计划维护 |
| [reports/](reports/README.md) | 按业务主题归档实施事实、测试结果和未覆盖边界 |
| [archive-reports/](archive-reports/README.md) | 被完整取代的报告副本，不作为验收依据 |
| [reference/](reference/) | 方案输入、字段盘点和技术操作参考，不单独作为验收结论 |
| [evidence/](evidence/) | 随版本维护、由报告引用的精简正式证据 |
| [runs/](runs/) | Git 忽略的临时运行输出，不作为正式归档证据 |

## 活动规划（仅未实施）

| 主题 | 活动规划 |
|---|---|
| 架构整理 | [应用编排模块边界整理](plans/architecture/application-module-boundaries.md) · [扩展场景与设备拓扑边界](plans/architecture/scenario-extension-boundaries.md) |
| 业务阶段 5 | [设备状态、未启动工序改派与规则调度](plans/production-flow/v1-stage-05-remaining-work.md) |
| 仿真模块维护 | [性能与命名](plans/simulation/simulation-code-optimization-remaining.md) |
| 运动规划效率 | [全阶段路径缓存、P2/P3 与未覆盖运动段](plans/simulation/simulation-motion-planning-performance-remaining.md) |
| 启动性能 | [P4 与剩余计时归因](plans/simulation/v1-startup-performance-optimization-remaining.md) |
| 数据平台 | [资源目录与知识图谱剩余工作](plans/data-platform/resource-catalog-database-and-knowledge-graph-remaining.md) · [数据库仪表盘优化](plans/data-platform/db-dashboard-frontend-optimization.md) · [仪表盘 UI 蓝图](plans/data-platform/db-dashboard-ui-blueprint.md) |

已完成的规划原文统一在 [archive-plans/](archive-plans/README.md)；活动规划
索引和归档规则见 [plans/README.md](plans/README.md)。

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

业务阶段 0–4、设备执行、工序图、能力调度和有限批次示范。业务阶段 5 已实施 WP0（`cart_2` 不可用业务用例与冻结基线）和 WP1（订单级调度观测），WP2/WP3/WP6（设备状态、改派、规则代价）未实施；WP4/WP5 异常恢复规划已移除，见[阶段 5 剩余工作](plans/production-flow/v1-stage-05-remaining-work.md) · [阶段 5 实施报告](reports/production-flow/v1-stage-05-finite-recovery.md) · [业务规划](../wiki/06-工业案例/柔性车间/当前规划/06-23-混线生产阶段5调度优化与设备状态.md)。

| 类型 | 文档 |
|---|---|
| 活动方案 | [阶段 5 剩余工作（WP2/WP3/WP6）](plans/production-flow/v1-stage-05-remaining-work.md) |
| 归档方案 | [阶段 2 设备执行适配](archive-plans/production-flow/v1-stage-02-device-executor-implementation.md) · [阶段 4 能力调度](archive-plans/production-flow/v1-stage-04-capability-scheduling-implementation.md) · [混线生产灵活性示范](archive-plans/production-flow/v1-mixed-production-flexibility-demo.md) · [Neo4j 图谱混线装配 MVP](archive-plans/production-flow/v1-neo4j-assembly-mixed-line-mvp.md) · [阶段 5 完整方案](archive-plans/production-flow/v1-stage-05-finite-recovery-implementation.md) |
| 报告 | [阶段 0–1 静态工艺目录](reports/production-flow/v1-stage-00-01-static-process-catalog.md) · [阶段 2 设备执行](reports/production-flow/v1-stage-02-device-executor.md) · [阶段 3 工序 DAG](reports/production-flow/v1-stage-03-operation-dag.md) · [阶段 4 能力调度](reports/production-flow/v1-stage-04-capability-scheduling.md) · [混线灵活性示范](reports/production-flow/v1-mixed-production-flexibility-demo.md) · [Neo4j 图谱混线装配 MVP](reports/production-flow/v1-neo4j-assembly-mixed-line-mvp.md) · [阶段 5 基线](reports/production-flow/v1-stage-05-finite-recovery.md) |
| 参考 | [assembly_mvp 订单 fixture 与复现](reference/production-flow/assembly-mvp/README.md) |
| 证据 | [生产流程验收证据目录](evidence/README.md) · [阶段 5 基线证据](evidence/production-flow/v1_stage5_baseline_20261124.json) |

### 订单编排

订单契约、失败隔离、API/持久化、确定性 planner、前端和限定范围的仿真实机验收。阶段 1–6 的限制和未准入故障以各阶段报告为准；阶段 7 已接入双车运行配置、派工一致性和启动 UI，并在 `v005` 上通过 single 回归、dual 手工订单、dual planner 订单和真实 UI 双车启动；路线并行与 planner UI 启动未验证。阶段 8 已让 planner 使用 single/dual 假设、固定分工和可选静态 ETA 做候选评估与 explain，DAG/运行派工边界不变。阶段 9 增加场景 profile 能力目录、前端按工件过滤物料/模板、planner 直接来料 join 和跨 profile 启动拒绝；CoppeliaSim 实机完成 full_flow 两路线和两个 MVP 产品的 UI run（full_flow 因 single E 臂托盘对位问题使用 dual）。阶段 10 增加 15 个内置订单方案变体（7 个完整路线 + 8 个工序前缀/运输变体），在订单列表页按机械臂/运输/差异直观展示并一键创建普通订单；不自动启动、不新增 DB 迁移。订单运行路径复用已接入 API/CLI/前端启动对话框：前端默认 `CACHE_THEN_PLAN`，`api serve`/`order start` 默认 `off`；只覆盖五臂 `PLACING`，本轮未新增 CoppeliaSim 实机验收。

| 类型 | 文档 |
|---|---|
| 活动方案 | 无；阶段 1–10、方案目录和路径复用均已实施，后续扩展需重新立项 |
| 归档方案 | [总体路线图](archive-plans/order-orchestration/v1-self-built-order-dag-orchestration.md) · [阶段 1 契约与编译](archive-plans/order-orchestration/v1-order-dag-phase-01-foundation.md) · [阶段 2 失败隔离](archive-plans/order-orchestration/v1-order-dag-phase-02-failure-isolation.md) · [阶段 3 API 与持久化](archive-plans/order-orchestration/v1-order-dag-phase-03-api-persistence.md) · [阶段 4 planner](archive-plans/order-orchestration/v1-order-dag-phase-04-auto-planner.md) · [阶段 5 前端](archive-plans/order-orchestration/v1-order-dag-phase-05-frontend.md) · [阶段 5 UI 规格](archive-plans/order-orchestration/v1-order-dag-phase-05-ui-design-spec.md) · [阶段 6 仿真验收](archive-plans/order-orchestration/v1-order-dag-phase-06-simulation-acceptance.md) · [阶段 7 双车](archive-plans/order-orchestration/v1-order-dag-phase-07-dual-fleet.md) · [阶段 8 车队感知 planner](archive-plans/order-orchestration/v1-order-dag-phase-08-fleet-aware-planner.md) · [阶段 9 场景 profile 目录](archive-plans/order-orchestration/v1-order-dag-phase-09-scene-profile-order-catalog.md) · [订单方案目录](archive-plans/order-orchestration/v1-order-variant-catalog-workbench.md) · [订单运行路径复用](archive-plans/order-orchestration/v1-order-run-path-reuse.md) |
| 报告 | [阶段 1–3](reports/order-orchestration/v1-order-dag-phase-01-03.md) · [阶段 4](reports/order-orchestration/v1-order-dag-phase-04-auto-planner.md) · [阶段 5 前端](reports/order-orchestration/v1-order-dag-phase-05-frontend.md) · [阶段 6 仿真验收](reports/order-orchestration/v1-order-dag-phase-06-simulation-acceptance.md) · [阶段 7 双车](reports/order-orchestration/v1-order-dag-phase-07-dual-fleet.md) · [阶段 8 车队感知 planner](reports/order-orchestration/v1-order-dag-phase-08-fleet-aware-planner.md) · [阶段 9 场景 profile 目录](reports/order-orchestration/v1-order-dag-phase-09-scene-profile-order-catalog.md) · [订单方案目录](reports/order-orchestration/v1-order-variant-catalog-workbench.md) · [订单运行路径复用](reports/order-orchestration/v1-order-run-path-reuse.md) |
| 证据 | [订单编排阶段 6 验收证据](evidence/order-orchestration/v1_order_dag_phase6_acceptance_20261108.json) · [阶段 9 场景 profile 验收证据](evidence/order-orchestration/v1_order_dag_phase9_scene_profile_20261121_153000.json) · [订单方案目录逻辑/API/E2E 证据](evidence/order-orchestration/v1_order_variant_catalog_20261122.json) · [订单运行路径复用逻辑/API/E2E 证据](evidence/order-orchestration/v1_order_run_path_reuse_20261124.json) |

### 数据平台

静态目录、运行记录、知识图谱导入和离线数据库查看工具。

| 类型 | 文档 |
|---|---|
| 活动方案 | [资源目录与知识图谱剩余工作](plans/data-platform/resource-catalog-database-and-knowledge-graph-remaining.md) · [数据库仪表盘优化](plans/data-platform/db-dashboard-frontend-optimization.md) · [仪表盘 UI 蓝图](plans/data-platform/db-dashboard-ui-blueprint.md) |
| 归档方案 | [资源目录数据库化与知识图谱完整方案](archive-plans/data-platform/resource-catalog-database-and-knowledge-graph.md) |
| 报告 | [目录持久化](reports/data-platform/v1-catalog-persistence.md) · [运行记录持久化](reports/data-platform/v1-run-record-persistence.md) · [知识图谱导入](reports/data-platform/v1-knowledge-graph-import.md) |
| 参考 | [静态目录字段盘点](reference/data-platform/static-catalog-field-inventory.md) · [数据库查看指南](reference/data-platform/database-inspection-guide.md) · [Neo4j 图数据导出](reference/data-platform/neo4j-graph-export/README.md) |
| 证据 | [数据平台验收证据目录](evidence/README.md) |

### 仿真工程

仿真模块职责、代码拆分、运动规划性能、五臂路径回放扩展、运行录像与启动性能计时。

| 类型 | 文档 |
|---|---|
| 活动方案 | [仿真模块代码优化剩余工作](plans/simulation/simulation-code-optimization-remaining.md) · [运动规划效率剩余工作](plans/simulation/simulation-motion-planning-performance-remaining.md) · [启动性能剩余工作](plans/simulation/v1-startup-performance-optimization-remaining.md) |
| 归档方案 | [仿真模块代码优化完整方案](archive-plans/simulation/simulation-code-optimization-plan.md) · [运动规划性能与路径回放完整方案](archive-plans/simulation/simulation-motion-planning-performance.md) · [五臂路径回放扩展](archive-plans/simulation/v1-all-arm-path-reuse-extension.md) · [启动性能完整方案](archive-plans/simulation/v1-startup-performance-optimization.md) |
| 报告 | [仿真模块代码优化报告](reports/simulation/v1-simulation-code-optimization.md) · [运动规划性能报告](reports/simulation/v1-motion-planning-performance.md) · [五臂路径回放扩展报告](reports/simulation/v1-all-arm-path-reuse.md) · [运行录像报告](reports/simulation/v1-simulation-recording.md) · [启动性能优化报告](reports/simulation/v1-startup-performance-optimization.md) |
| 参考 | [仿真模块清单](reference/simulation/simulation-module-inventory.md) |
| 证据 | [仿真工程证据](evidence/simulation/) |

## 归档与维护规则

- 业务规划回答“建设什么”；实施方案回答“准备如何实现”；报告回答“实际完成什么”；证据保存可核验的验收摘要。各类文件按主题放入对应目录。
- **活动规划只保留未实施工作**：已完成的规划原文移动到 `archive-plans/{主题}/`；部分实施的规划只把剩余工作包写入 `plans/`，已完成步骤留在归档原文件和报告中。
- 报告按主题保留在 `reports/{theme}/`，不随规划归档移动；只有报告本身被完整取代时才放入 `archive-reports/`。
- 文件状态以 frontmatter 为准；跨主题当前状态只在 v2 准备度盘点维护。报告保留其执行时点的测试和验收事实，不把旧计数解释为当前测试结果。
- 方案与报告有不同职责，不因内容引用相同而视为重复。只有内容、用途和证据来源均被另一个权威文档完整取代时，才合并或移除旧文档。
- 新增或迁移文档时，更新本索引、[活动规划索引](plans/README.md)、[归档索引](archive-plans/README.md)、[报告索引](reports/README.md)、[文档登记表](governance/project-document-register.md)和所有受影响的相对链接；正式证据同时更新[evidence 目录](evidence/README.md)。
