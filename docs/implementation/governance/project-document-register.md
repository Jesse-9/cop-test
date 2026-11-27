---
title: 项目文档登记表与归档规则
type: reference
tags:
  - 类别/项目管理
  - 类别/实施计划
created: 2026-11-05
updated: 2026-11-24
status: published
---

# 项目文档登记表与归档规则

本文规定项目规划、实施方案、报告和证据的职责边界及相互对应关系。目录入口见
[实施资料](../README.md)。

## 文档职责

| 文档层 | 归档位置 | 记录内容 |
|---|---|---|
| 项目治理 | `implementation/governance/` | 产品范围、决策、冻结基线和文档规则 |
| 业务域规划 | `wiki/06-工业案例/柔性车间/` | 场景背景、工艺路线、领域对象、阶段目标和业务验收意图 |
| 技术实施方案 | `implementation/plans/{architecture,production-flow,data-platform,simulation}/`（仅未实施）；`implementation/archive-plans/{主题}/`（已完成/部分实施原文件） | 活动规划只写剩余工作包、代码落点、测试门槛和回退条件；归档原文件只供追溯 |
| 实施报告 | `implementation/reports/{product,production-flow,order-orchestration,data-platform,simulation}/` | 按产品或业务主题归档实际改动、测试/仿真结果、证据和未覆盖限制；不随规划移动 |
| 归档报告 | `implementation/archive-reports/` | 被完整取代的报告副本，不作为验收依据 |
| 技术参考 | `implementation/reference/{data-platform,simulation}/` | 字段盘点、数据库查看与模块职责等实施输入，不作为验收结论 |
| 验收材料 | `implementation/evidence/{production-flow,order-orchestration,data-platform}/`、`implementation/runs/` | 正式精简证据按主题归档；runs 为被忽略的临时运行输出 |

业务规划回答“建设什么”，实施方案回答“准备如何实现”，实施报告回答“实际完成
什么”。只有报告和正式 evidence 能作为完成结论的依据；目录、文件名或方案中的计划
状态不能代替验收事实。

## 阶段对应

当前 v1→v2 的完整状态判断及物理/运行风险见
[v2 启动准备度盘点](v2-readiness-assessment.md)。下表保留业务规划与实施资料的定位关系；
其中业务阶段编号和订单 DAG 实施阶段编号是不同序列，不应混用。

| 业务域规划 | 实施资料 | 状态说明 |
|---|---|---|
| `06-18` 总体演进规划 | [v1 范围与迁移方案](v1-scope-and-migration-plan.md)、[M0 基线](v1-baseline-m0.md)、[产品实施报告](../reports/product/v1-product-implementation.md) | v1 已收敛；后续能力分阶段评估 |
| `06-19` 阶段 0–1 静态工艺目录 | [阶段 0–1 报告](../reports/production-flow/v1-stage-00-01-static-process-catalog.md) | 移植完成 |
| `06-20` 阶段 2 设备目录与执行适配器 | [阶段 2 实施方案](../archive-plans/production-flow/v1-stage-02-device-executor-implementation.md)、[阶段 2 报告](../reports/production-flow/v1-stage-02-device-executor.md) | 实施与报告完成 |
| `06-21` 阶段 3 配方实例化与工序 DAG | [阶段 3 报告](../reports/production-flow/v1-stage-03-operation-dag.md) | 实施与报告完成 |
| `06-22` 阶段 4 产品变体与能力匹配调度 | [阶段 4 实施方案](../archive-plans/production-flow/v1-stage-04-capability-scheduling-implementation.md)、[阶段 4 报告](../reports/production-flow/v1-stage-04-capability-scheduling.md) | 已实施；默认工艺仍使用固定设备候选 |
| `06-23` 业务阶段 5 调度优化与设备状态 | [阶段 5 剩余工作](../plans/production-flow/v1-stage-05-remaining-work.md)、[阶段 5 完整方案（归档）](../archive-plans/production-flow/v1-stage-05-finite-recovery-implementation.md)、[阶段 5 实施报告](../reports/production-flow/v1-stage-05-finite-recovery.md) | WP0（`cart_2` 不可用业务用例与冻结基线）和 WP1（订单级调度观测）已实施并通过纯测试；活动规划只保留 WP2/WP3/WP6（设备状态、未启动工序改派、规则代价）；WP4/WP5 异常恢复规划已移除，实机故障复现未执行 |
| `06-24` 术语与对象模型、`06-25` 工艺流程映射 | [混线生产业务与代码运行说明](../../wiki/06-工业案例/柔性车间/混线生产业务与代码运行说明.md)、相关阶段报告 | 参考资料，不单独作为验收证据 |

## 产品能力提案（归档与活动）

订单 DAG、方案目录和运行路径复用的规划原文已移动到 `archive-plans/`，不再作为
活动计划维护；仪表盘优化仍未实施，保留在 `plans/`。活动规划索引见
[plans/README.md](../plans/README.md)。

| 主题 | 实施方案 | 状态说明 |
|---|---|---|
| 自建订单与工序 DAG 自主编排 | [路线图](../archive-plans/order-orchestration/v1-self-built-order-dag-orchestration.md)、[阶段 1](../archive-plans/order-orchestration/v1-order-dag-phase-01-foundation.md)、[阶段 2](../archive-plans/order-orchestration/v1-order-dag-phase-02-failure-isolation.md)、[阶段 3](../archive-plans/order-orchestration/v1-order-dag-phase-03-api-persistence.md)、[阶段 4](../archive-plans/order-orchestration/v1-order-dag-phase-04-auto-planner.md)、[阶段 5 方案](../archive-plans/order-orchestration/v1-order-dag-phase-05-frontend.md)、[阶段 5 UI 规格](../archive-plans/order-orchestration/v1-order-dag-phase-05-ui-design-spec.md)、[阶段 6](../archive-plans/order-orchestration/v1-order-dag-phase-06-simulation-acceptance.md)、[阶段 7 双车](../archive-plans/order-orchestration/v1-order-dag-phase-07-dual-fleet.md)、[阶段 8 车队感知 planner](../archive-plans/order-orchestration/v1-order-dag-phase-08-fleet-aware-planner.md)、[阶段 9 场景 profile 目录](../archive-plans/order-orchestration/v1-order-dag-phase-09-scene-profile-order-catalog.md)、[订单方案目录](../archive-plans/order-orchestration/v1-order-variant-catalog-workbench.md)、[订单运行路径复用](../archive-plans/order-orchestration/v1-order-run-path-reuse.md) | 订单 DAG 实施阶段 1–6 已完成各自限定交付；阶段 7 已完成运行配置透传、双车派工一致性、迁移和启动 UI，并在 `v005` 上通过 single 回归、dual 手工订单、dual planner 订单和真实 UI 双车启动；路线并行、planner UI 启动和轮式物理未验证。阶段 8 已实施车队假设、静态可用性、候选降权/explain 和可选 ETA 排序，运行派工边界不变。阶段 9 已实施工件 manifest 驱动的 profile 能力目录、前端目录过滤/跨 profile 阻断、planner 直接来料 join 与启动前 profile 校验；2026-11-21 在 CoppeliaSim 4.10 完成 full_flow 两路线和两个 MVP 产品的 UI run（full_flow 因 single E 臂托盘对位问题使用 dual；MVP 旧工件按当前 config 重建为 v002），跨 profile 启动返回 422。订单方案目录阶段 10 已实施：15 个内置 OrderVariant、确定性 materializer、`/api/order-variants` 和订单列表目录；`source=manual` + `note=variant:<id>`，无 DB 迁移、不自动启动，车队仅展示提示。订单运行路径复用接入已实施：前端启动默认 `CACHE_THEN_PLAN`，`api serve`/`order start`/API body 可选四种模式，runner 把策略传给 coordinator 并在 summary 记录统计；仅五臂 `PLACING`，无 DB 迁移，未新增实机验收。报告入口见[阶段 1–3](../reports/order-orchestration/v1-order-dag-phase-01-03.md)、[阶段 4](../reports/order-orchestration/v1-order-dag-phase-04-auto-planner.md)、[阶段 5](../reports/order-orchestration/v1-order-dag-phase-05-frontend.md)、[阶段 6](../reports/order-orchestration/v1-order-dag-phase-06-simulation-acceptance.md)、[阶段 7](../reports/order-orchestration/v1-order-dag-phase-07-dual-fleet.md)、[阶段 8](../reports/order-orchestration/v1-order-dag-phase-08-fleet-aware-planner.md)、[阶段 9](../reports/order-orchestration/v1-order-dag-phase-09-scene-profile-order-catalog.md)、[订单方案目录](../reports/order-orchestration/v1-order-variant-catalog-workbench.md)、[订单运行路径复用](../reports/order-orchestration/v1-order-run-path-reuse.md)。真实 Arm/AGV 运动故障与 ZMQ/step 断连仍未获 NODE 准入 |
| 本地数据库仪表盘优化 | [前端优化方案](../plans/data-platform/db-dashboard-frontend-optimization.md)、[UI 蓝图](../plans/data-platform/db-dashboard-ui-blueprint.md)、[数据库查看指南](../reference/data-platform/database-inspection-guide.md) | 未实施（活动规划）；保持只读离线 HTML，方案尚未形成实施报告 |

## 跨阶段技术主题

| 技术方案 | 实施记录 | 当前状态 |
|---|---|---|
| [应用编排模块边界整理](../plans/architecture/application-module-boundaries.md) · [扩展场景与设备拓扑边界](../plans/architecture/scenario-extension-boundaries.md) | 前者以 `application/coordinator.py` 为首要候选，按职责分批迁移并保持运行契约；后者定义场景拓扑扩展与核心调度循环的协作边界 | proposed；均未实施，planner 拆分已有报告记录，不重复立项 |
| [资源目录数据库化与知识图谱（归档）](../archive-plans/data-platform/resource-catalog-database-and-knowledge-graph.md) · [剩余工作](../plans/data-platform/resource-catalog-database-and-knowledge-graph-remaining.md) | [字段盘点](../reference/data-platform/static-catalog-field-inventory.md)、[目录持久化](../reports/data-platform/v1-catalog-persistence.md)、[运行记录](../reports/data-platform/v1-run-record-persistence.md)、[知识图谱导入](../reports/data-platform/v1-knowledge-graph-import.md) | 阶段 0–5 已有实施记录；阶段 6 部分实施；活动规划只保留语义融合、运维、Q5/Q6 |
| [混线生产灵活性扩展示范](../archive-plans/production-flow/v1-mixed-production-flexibility-demo.md) | [有限批次配比实施报告](../reports/production-flow/v1-mixed-production-flexibility-demo.md) | WP1/WP2 已实施并实机验收；WP3/WP4 为条件包 |
| Neo4j 图谱混线装配 MVP | [实施方案](../archive-plans/production-flow/v1-neo4j-assembly-mixed-line-mvp.md)、[实施报告](../reports/production-flow/v1-neo4j-assembly-mixed-line-mvp.md)、[订单 fixture 与复现](../reference/production-flow/assembly-mvp/README.md) | WP0–WP3 已实施；两产品在同一实验场景交替实机通过；多订单并发与真实装配未实现 |
| [仿真与运动规划性能（归档）](../archive-plans/simulation/simulation-motion-planning-performance.md) · [剩余工作](../plans/simulation/simulation-motion-planning-performance-remaining.md) | [实施报告](../reports/simulation/v1-motion-planning-performance.md)、[focused 证据](../evidence/simulation/v1_motion_path_reuse_20261120.json) | partially-implemented；P0 计时字段与 P1 固定路径回放已落地；固定 A 臂 `PLACING` 段 focused 实机对照通过；抓取/转运段、第二个动作、P2/P3 未执行 |
| [五臂固定路径回放扩展（归档）](../archive-plans/simulation/v1-all-arm-path-reuse-extension.md) | [实施报告](../reports/simulation/v1-all-arm-path-reuse.md)、[full_flow 证据](../evidence/simulation/v1_full_flow_path_reuse_20261121.json) | implemented；五臂 `PLACING`、`run --path-reuse`、`docs/path_library`；v005 full_flow 9 次查找/7 次回放/2 次 B/D 回退，A/C/E `planner_calls=0`，两次 acceptance 均通过；其他运动段、订单 runner、P2/P3 未纳入 |
| [v1 启动性能优化（归档）](../archive-plans/simulation/v1-startup-performance-optimization.md) · [剩余工作](../plans/simulation/v1-startup-performance-optimization-remaining.md) | [启动阶段计时工具](../../../tools/measure_startup_stages.py)、[实施报告](../reports/simulation/v1-startup-performance-optimization.md)、[计时证据](../evidence/simulation/)、[使用指南](../../wiki/98-ProjectUsage/USAGE.md) | partially-implemented；P1/P2、P3 子集臂已实施；P4 长驻 worker 复用与计时归因仍为活动规划 |
| [仿真模块代码优化（归档）](../archive-plans/simulation/simulation-code-optimization-plan.md) · [剩余工作](../plans/simulation/simulation-code-optimization-remaining.md) | [实施报告](../reports/simulation/v1-simulation-code-optimization.md)、[模块清单](../reference/simulation/simulation-module-inventory.md) | P1/P2、P3 5.1/5.3 与第 6 节已实施并离线验证；5.2 异常路径回滚已移出规划，5.3 性能优化和模块命名为活动规划 |
| [v1 运行录像](../reports/simulation/v1-simulation-recording.md) | [使用指南](../../wiki/98-ProjectUsage/USAGE.md) | implemented（离线）；`run`/`order start`/`api serve` 的 `--record*` 与前端启动开关、Range 视频接口；后端 813 passed / 8 skipped，前端 16 passed；已完成 20 s 实机短片验证，完整 full_flow 录像未执行 |

## 命名约定

- 项目治理文件采用明确主题名，例如 `v1-baseline-m0.md`、`v1-scope-and-migration-plan.md`。
- 阶段实施方案采用 `v1-stage-{阶段}-{主题}-implementation.md`；归档后保留原文件名。
- 实施报告采用 `v1-stage-{阶段}-{主题}.md`；跨阶段报告用 `v1-{主题}.md`。
- 活动规划只放入 `plans/`，且只写未实施工作；已完成/部分实施的规划原文件放入
  `archive-plans/{主题}/`。报告和归档报告分别放入 `reports/`、`archive-reports/`。
- 保留稳定的阶段编号只用于业务域 wiki 文件，避免编号同时承担多个用途。

新增或归档文档时，同步更新 `implementation/README.md`、
[活动规划索引](../plans/README.md)、[规划归档索引](../archive-plans/README.md)、
[报告索引](../reports/README.md)和本登记表中的阶段关系。
