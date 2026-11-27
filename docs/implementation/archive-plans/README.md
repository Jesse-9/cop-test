---
title: 已完成规划归档索引
type: index
status: archived
created: 2026-11-25
updated: 2026-11-25
---

# 已完成规划归档索引

本目录保存已完成或已收敛的原始规划，供追溯实现约束和历史决策，不再作为
活动计划维护。**活动规划只放在 [plans/](../plans/README.md)**；实施事实、
测试结果和未覆盖边界以 [reports/](../reports/README.md)与
[evidence/](../evidence/README.md)为准。

部分归档原文件包含已实施步骤和少量剩余工作；剩余工作已另行整理为活动规划，
见下表“剩余工作”。归档文件保持原样，不因状态变化回写历史结论。

## 生产流程

| 归档规划 | 状态 | 报告 | 剩余工作 |
|---|---|---|---|
| [阶段 2 设备目录与执行适配器](production-flow/v1-stage-02-device-executor-implementation.md) | implemented | [阶段 2 报告](../reports/production-flow/v1-stage-02-device-executor.md) | — |
| [阶段 4 产品变体与能力匹配调度](production-flow/v1-stage-04-capability-scheduling-implementation.md) | implemented | [阶段 4 报告](../reports/production-flow/v1-stage-04-capability-scheduling.md) | — |
| [混线生产灵活性示范](production-flow/v1-mixed-production-flexibility-demo.md) | implemented（WP1/WP2） | [实施报告](../reports/production-flow/v1-mixed-production-flexibility-demo.md) | WP3/WP4 为条件包，未立项 |
| [Neo4j 图谱混线装配 MVP](production-flow/v1-neo4j-assembly-mixed-line-mvp.md) | implemented（WP0–WP3） | [实施报告](../reports/production-flow/v1-neo4j-assembly-mixed-line-mvp.md) | 多订单并发/真实装配未实现 |
| [阶段 5 历史完整方案（含已移除的 WP4/WP5）](production-flow/v1-stage-05-finite-recovery-implementation.md) | partially-implemented（WP0/WP1） | [阶段 5 报告](../reports/production-flow/v1-stage-05-finite-recovery.md) | [当前剩余 WP2/WP3/WP6](../plans/production-flow/v1-stage-05-remaining-work.md) |

## 订单编排

| 归档规划 | 状态 | 报告 |
|---|---|---|
| [总体路线图](order-orchestration/v1-self-built-order-dag-orchestration.md) | implemented（阶段 1–9） | [阶段 1–3](../reports/order-orchestration/v1-order-dag-phase-01-03.md)等 |
| [阶段 1 契约与编译](order-orchestration/v1-order-dag-phase-01-foundation.md) | implemented | [阶段 1–3 报告](../reports/order-orchestration/v1-order-dag-phase-01-03.md) |
| [阶段 2 失败隔离](order-orchestration/v1-order-dag-phase-02-failure-isolation.md) | implemented | [阶段 1–3 报告](../reports/order-orchestration/v1-order-dag-phase-01-03.md) |
| [阶段 3 API 与持久化](order-orchestration/v1-order-dag-phase-03-api-persistence.md) | implemented | [阶段 1–3 报告](../reports/order-orchestration/v1-order-dag-phase-01-03.md) |
| [阶段 4 planner](order-orchestration/v1-order-dag-phase-04-auto-planner.md) | implemented | [阶段 4 报告](../reports/order-orchestration/v1-order-dag-phase-04-auto-planner.md) |
| [阶段 5 前端](order-orchestration/v1-order-dag-phase-05-frontend.md) | implemented | [阶段 5 报告](../reports/order-orchestration/v1-order-dag-phase-05-frontend.md) |
| [阶段 5 UI 规格](order-orchestration/v1-order-dag-phase-05-ui-design-spec.md) | implemented | [阶段 5 报告](../reports/order-orchestration/v1-order-dag-phase-05-frontend.md) |
| [阶段 6 仿真验收](order-orchestration/v1-order-dag-phase-06-simulation-acceptance.md) | implemented（限定矩阵） | [阶段 6 报告](../reports/order-orchestration/v1-order-dag-phase-06-simulation-acceptance.md) |
| [阶段 7 双车](order-orchestration/v1-order-dag-phase-07-dual-fleet.md) | implemented（限定实机） | [阶段 7 报告](../reports/order-orchestration/v1-order-dag-phase-07-dual-fleet.md) |
| [阶段 8 车队感知 planner](order-orchestration/v1-order-dag-phase-08-fleet-aware-planner.md) | implemented | [阶段 8 报告](../reports/order-orchestration/v1-order-dag-phase-08-fleet-aware-planner.md) |
| [阶段 9 场景 profile 目录](order-orchestration/v1-order-dag-phase-09-scene-profile-order-catalog.md) | implemented（限定实机） | [阶段 9 报告](../reports/order-orchestration/v1-order-dag-phase-09-scene-profile-order-catalog.md) |
| [订单方案目录](order-orchestration/v1-order-variant-catalog-workbench.md) | implemented | [方案目录报告](../reports/order-orchestration/v1-order-variant-catalog-workbench.md) |
| [订单运行路径复用](order-orchestration/v1-order-run-path-reuse.md) | implemented | [路径复用报告](../reports/order-orchestration/v1-order-run-path-reuse.md) |

订单编排当前没有活动规划；后续扩展需重新立项，不能把归档路线图的“阶段 1–9
已完成”解释为任意 DAG、任意设备替换或真实故障恢复已具备。

## 仿真工程

| 归档规划 | 状态 | 报告 | 剩余工作 |
|---|---|---|---|
| [五臂固定路径回放扩展](simulation/v1-all-arm-path-reuse-extension.md) | implemented | [五臂报告](../reports/simulation/v1-all-arm-path-reuse.md) | — |
| [仿真模块代码优化完整方案](simulation/simulation-code-optimization-plan.md) | partially-implemented | [代码优化报告](../reports/simulation/v1-simulation-code-optimization.md) | [5.3/命名（5.2 已移出规划）](../plans/simulation/simulation-code-optimization-remaining.md) |
| [运动规划效率完整方案](simulation/simulation-motion-planning-performance.md) | partially-implemented | [运动规划报告](../reports/simulation/v1-motion-planning-performance.md) | [P2/P3/未覆盖段](../plans/simulation/simulation-motion-planning-performance-remaining.md) |
| [启动性能完整方案](simulation/v1-startup-performance-optimization.md) | partially-implemented | [启动性能报告](../reports/simulation/v1-startup-performance-optimization.md) | [P4/计时归因](../plans/simulation/v1-startup-performance-optimization-remaining.md) |

## 数据平台

| 归档规划 | 状态 | 报告 | 剩余工作 |
|---|---|---|---|
| [资源目录数据库化与知识图谱完整方案](data-platform/resource-catalog-database-and-knowledge-graph.md) | partially-implemented | [目录持久化](../reports/data-platform/v1-catalog-persistence.md)、[运行记录](../reports/data-platform/v1-run-record-persistence.md)、[知识图谱导入](../reports/data-platform/v1-knowledge-graph-import.md) | [语义融合/运维](../plans/data-platform/resource-catalog-database-and-knowledge-graph-remaining.md) |

## 归档规则

- 只归档规划原文，不移动报告和证据；报告按主题保留在 `reports/{theme}/`，
  证据保留在 `evidence/{theme}/`。
- 归档文件不再更新实现结论；若发现错误，在报告或活动规划中记录，不修改
  历史验收事实。
- 新增归档时更新本索引、[活动规划索引](../plans/README.md)、
  [实施资料索引](../README.md)和
  [项目文档登记表](../governance/project-document-register.md)。
- 本目录不代表当前能力；真实能力以报告、证据和代码为准。