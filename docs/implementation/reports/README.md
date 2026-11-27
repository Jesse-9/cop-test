---
title: 实施报告归档索引
type: index
status: current
created: 2026-11-25
updated: 2026-11-25
---

# 实施报告归档索引

本目录按主题保存实施报告，回答“实际完成了什么、测试结果和未覆盖边界”。
报告是历史事实记录，不随规划归档移动；活动规划见
[plans/README.md](../plans/README.md)，已完成规划原文见
[archive-plans/README.md](../archive-plans/README.md)。

## 产品

| 报告 | 范围 |
|---|---|
| [v1 产品实施报告](product/v1-product-implementation.md) | v1 迁移、受保护工件、`full_flow` 基线 |

## 生产流程

| 报告 | 范围 |
|---|---|
| [阶段 0–1 静态工艺目录](production-flow/v1-stage-00-01-static-process-catalog.md) | 工艺目录移植 |
| [阶段 2 设备执行](production-flow/v1-stage-02-device-executor.md) | 设备目录与执行适配器 |
| [阶段 3 工序 DAG](production-flow/v1-stage-03-operation-dag.md) | 配方实例化与工序 DAG |
| [阶段 4 能力调度](production-flow/v1-stage-04-capability-scheduling.md) | 产品变体与能力匹配调度 |
| [混线灵活性示范](production-flow/v1-mixed-production-flexibility-demo.md) | 有限批次配比 |
| [Neo4j 装配 MVP](production-flow/v1-neo4j-assembly-mixed-line-mvp.md) | 图谱驱动混线装配实验 |
| [阶段 5 业务用例与基线](production-flow/v1-stage-05-finite-recovery.md) | WP0/WP1 离线基线；WP2/WP3/WP6 未实施；WP4/WP5 异常恢复规划已移除 |

## 订单编排

| 报告 | 范围 |
|---|---|
| [阶段 1–3](order-orchestration/v1-order-dag-phase-01-03.md) | 契约、失败隔离、API/持久化 |
| [阶段 4 planner](order-orchestration/v1-order-dag-phase-04-auto-planner.md) | 确定性自主编排 |
| [阶段 5 前端](order-orchestration/v1-order-dag-phase-05-frontend.md) | 订单工作台 |
| [阶段 6 仿真验收](order-orchestration/v1-order-dag-phase-06-simulation-acceptance.md) | 限定实机矩阵 |
| [阶段 7 双车](order-orchestration/v1-order-dag-phase-07-dual-fleet.md) | 双车运行配置与派工 |
| [阶段 8 车队感知 planner](order-orchestration/v1-order-dag-phase-08-fleet-aware-planner.md) | 车队假设与 explain |
| [阶段 9 场景 profile 目录](order-orchestration/v1-order-dag-phase-09-scene-profile-order-catalog.md) | profile 能力目录与直接来料 |
| [订单方案目录](order-orchestration/v1-order-variant-catalog-workbench.md) | 15 个内置变体 |
| [订单运行路径复用](order-orchestration/v1-order-run-path-reuse.md) | API/CLI/前端模式接入 |

## 仿真工程

| 报告 | 范围 |
|---|---|
| [仿真模块代码优化](simulation/v1-simulation-code-optimization.md) | 控制器/runtime/规划器维护 |
| [运动规划性能](simulation/v1-motion-planning-performance.md) | P0 计时与 P1 路径回放 |
| [五臂路径回放扩展](simulation/v1-all-arm-path-reuse.md) | 五臂 `PLACING` 实机对照 |
| [启动性能优化](simulation/v1-startup-performance-optimization.md) | P1/P2/P3 与计时 |
| [运行录像](simulation/v1-simulation-recording.md) | 可选视频录制与接口 |

## 数据平台

| 报告 | 范围 |
|---|---|
| [目录持久化](data-platform/v1-catalog-persistence.md) | 静态目录 SQLite |
| [运行记录持久化](data-platform/v1-run-record-persistence.md) | Q1–Q4 批量写入与查询 |
| [知识图谱导入](data-platform/v1-knowledge-graph-import.md) | Neo4j dump 导入/发布 |

## 归档报告

- [已废弃的阶段 3 报告副本](../archive-reports/v1-stage-03-operation-dag.md)：
  与 `production-flow/v1-stage-03-operation-dag.md` 内容相同但证据路径已过期，
  仅保留追溯；权威版本以生产流程目录为准。

## 维护规则

- 新报告按主题放入对应目录，命名沿用 `v1-{主题}.md` 或阶段编号。
- 报告保留执行时点的测试计数和验收事实，不把旧计数解释为当前测试结果。
- 报告完成且被归档规划覆盖时，不移动到 `archive-plans/`；只有报告本身被
  完整取代时才放入 `archive-reports/`。
- 新增/移动报告时更新本索引、[实施资料索引](../README.md)、
  [文档登记表](../governance/project-document-register.md)和相关证据链接。