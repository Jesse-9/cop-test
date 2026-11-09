---
title: 项目文档登记表与归档规则
type: reference
tags:
  - 类别/项目管理
  - 类别/实施计划
created: 2026-11-05
updated: 2026-11-09
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
| 技术实施方案 | `implementation/plans/{production-flow,order-orchestration,data-platform,simulation}/` | 按业务主题归档工作包、代码落点、测试门槛、技术决策和回退条件 |
| 实施报告 | `implementation/reports/{product,production-flow,order-orchestration,data-platform,simulation}/` | 按产品或业务主题归档实际改动、测试/仿真结果、证据和未覆盖限制 |
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
| `06-20` 阶段 2 设备目录与执行适配器 | [阶段 2 实施方案](../plans/production-flow/v1-stage-02-device-executor-implementation.md)、[阶段 2 报告](../reports/production-flow/v1-stage-02-device-executor.md) | 实施与报告完成 |
| `06-21` 阶段 3 配方实例化与工序 DAG | [阶段 3 报告](../reports/production-flow/v1-stage-03-operation-dag.md) | 实施与报告完成 |
| `06-22` 阶段 4 产品变体与能力匹配调度 | [阶段 4 实施方案](../plans/production-flow/v1-stage-04-capability-scheduling-implementation.md)、[阶段 4 报告](../reports/production-flow/v1-stage-04-capability-scheduling.md) | 已实施；默认工艺仍使用固定设备候选 |
| `06-23` 业务阶段 5 调度优化与有限恢复 | 暂无实施报告 | 暂缓；v2 是否实施须由具体业务用例和基线重新立项 |
| `06-24` 术语与对象模型、`06-25` 工艺流程映射 | [混线生产业务与代码运行说明](../../wiki/06-工业案例/柔性车间/混线生产业务与代码运行说明.md)、相关阶段报告 | 参考资料，不单独作为验收证据 |

## 后续产品能力提案

| 主题 | 实施方案 | 状态说明 |
|---|---|---|
| 自建订单与工序 DAG 自主编排 | [路线图](../plans/order-orchestration/v1-self-built-order-dag-orchestration.md)、[阶段 1](../plans/order-orchestration/v1-order-dag-phase-01-foundation.md)、[阶段 2](../plans/order-orchestration/v1-order-dag-phase-02-failure-isolation.md)、[阶段 3](../plans/order-orchestration/v1-order-dag-phase-03-api-persistence.md)、[阶段 4](../plans/order-orchestration/v1-order-dag-phase-04-auto-planner.md)、[阶段 5 方案](../plans/order-orchestration/v1-order-dag-phase-05-frontend.md)、[阶段 5 UI 规格](../plans/order-orchestration/v1-order-dag-phase-05-ui-design-spec.md)、[阶段 6](../plans/order-orchestration/v1-order-dag-phase-06-simulation-acceptance.md) | 订单 DAG 实施阶段 1–6 已完成各自限定交付；报告入口见[阶段 1–3](../reports/order-orchestration/v1-order-dag-phase-01-03.md)、[阶段 4](../reports/order-orchestration/v1-order-dag-phase-04-auto-planner.md)、[阶段 5](../reports/order-orchestration/v1-order-dag-phase-05-frontend.md)、[阶段 6](../reports/order-orchestration/v1-order-dag-phase-06-simulation-acceptance.md)。真实 Arm/AGV 运动故障与 ZMQ/step 断连仍未获 NODE 准入 |
| 本地数据库仪表盘优化 | [前端优化方案](../plans/data-platform/db-dashboard-frontend-optimization.md)、[UI 蓝图](../plans/data-platform/db-dashboard-ui-blueprint.md)、[数据库查看指南](../reference/data-platform/database-inspection-guide.md) | 保持只读离线 HTML；方案尚未实施 |

## 跨阶段技术主题

| 技术方案 | 实施记录 | 当前状态 |
|---|---|---|
| [资源目录数据库化与知识图谱](../plans/data-platform/resource-catalog-database-and-knowledge-graph.md) | [字段盘点](../reference/data-platform/static-catalog-field-inventory.md)、[目录持久化](../reports/data-platform/v1-catalog-persistence.md)、[运行记录](../reports/data-platform/v1-run-record-persistence.md)、[知识图谱导入](../reports/data-platform/v1-knowledge-graph-import.md) | 阶段 0–5 已有实施记录；阶段 6 部分实施 |
| [混线生产灵活性扩展示范](../plans/production-flow/v1-mixed-production-flexibility-demo.md) | [有限批次配比实施报告](../reports/production-flow/v1-mixed-production-flexibility-demo.md) | WP1/WP2 已实施并实机验收；WP3/WP4 为条件包 |
| [仿真与运动规划性能](../plans/simulation/simulation-motion-planning-performance.md) | 暂无 | proposed |
| [仿真模块代码优化](../plans/simulation/simulation-code-optimization-plan.md) | [实施报告](../reports/simulation/v1-simulation-code-optimization.md)、[模块清单](../reference/simulation/simulation-module-inventory.md) | P1/P2、P3 5.1/5.3 与第 6 节已实施并离线验证；5.2 未实施 |

## 命名约定

- 项目治理文件采用明确主题名，例如 `v1-baseline-m0.md`、`v1-scope-and-migration-plan.md`。
- 阶段实施方案采用 `v1-stage-{阶段}-{主题}-implementation.md`。
- 实施报告采用 `v1-stage-{阶段}-{主题}.md`；跨阶段报告用 `v1-{主题}.md`。
- 方案和报告按职责分别放入 `plans/`、`reports/`，主题名保持一致。
- 保留稳定的阶段编号只用于业务域 wiki 文件，避免编号同时承担多个用途。

新增文档时同步更新 `implementation/README.md` 和本登记表中的阶段关系；旧报告中的历史
文件名只在明确需要保留历史追溯时出现。
