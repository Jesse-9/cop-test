---
title: 项目文档登记表与归档规则
type: reference
tags:
  - 类别/项目管理
  - 类别/实施计划
created: 2026-11-05
updated: 2026-11-05
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
| 技术实施方案 | `implementation/plans/` | 工作包、代码落点、测试门槛、技术决策和回退条件 |
| 实施报告 | `implementation/reports/` | 实际改动、测试/仿真结果、证据和未覆盖限制 |
| 技术参考 | `implementation/reference/` | 字段盘点、来源追溯等实施输入，不作为验收结论 |
| 验收材料 | `implementation/evidence/`、`implementation/runs/` | 正式精简证据、被忽略的临时运行输出 |

业务规划回答“建设什么”，实施方案回答“准备如何实现”，实施报告回答“实际完成
什么”。只有报告和正式 evidence 能作为完成结论的依据；目录、文件名或方案中的计划
状态不能代替验收事实。

## 阶段对应

| 业务域规划 | 实施资料 | 状态说明 |
|---|---|---|
| `06-18` 总体演进规划 | [v1 范围与迁移方案](v1-scope-and-migration-plan.md)、[M0 基线](v1-baseline-m0.md)、[产品实施报告](../reports/v1-product-implementation.md) | v1 已收敛；后续能力分阶段评估 |
| `06-19` 阶段 0–1 静态工艺目录 | [阶段 0–1 报告](../reports/v1-stage-00-01-static-process-catalog.md) | 移植完成 |
| `06-20` 阶段 2 设备目录与执行适配器 | [阶段 2 实施方案](../plans/v1-stage-02-device-executor-implementation.md)、[阶段 2 报告](../reports/v1-stage-02-device-executor.md) | 实施与报告完成 |
| `06-21` 阶段 3 配方实例化与工序 DAG | [阶段 3 报告](../reports/v1-stage-03-operation-dag.md) | 实施与报告完成 |
| `06-22` 阶段 4 产品变体与能力匹配调度 | [阶段 4 实施方案](../plans/v1-stage-04-capability-scheduling-implementation.md)、[阶段 4 报告](../reports/v1-stage-04-capability-scheduling.md) | 已实施；默认工艺仍使用固定设备候选 |
| `06-23` 阶段 5 调度优化与有限恢复 | 暂无实施报告 | 暂缓；仍按业务规划中的门槛重新立项 |
| `06-24` 术语与对象模型、`06-25` 工艺流程映射 | [混线生产业务与代码运行说明](../../wiki/06-工业案例/柔性车间/混线生产业务与代码运行说明.md)、相关阶段报告 | 参考资料，不单独作为验收证据 |

## 跨阶段技术主题

| 技术方案 | 实施记录 | 当前状态 |
|---|---|---|
| [资源目录数据库化与知识图谱](../plans/resource-catalog-database-and-knowledge-graph.md) | [字段盘点](../reference/static-catalog-field-inventory.md)、[目录持久化](../reports/v1-catalog-persistence.md)、[运行记录](../reports/v1-run-record-persistence.md)、[知识图谱导入](../reports/v1-knowledge-graph-import.md) | 阶段 0–5 已有实施记录；阶段 6 部分实施 |
| [混线生产灵活性扩展示范](../plans/v1-mixed-production-flexibility-demo.md) | 暂无 | proposed |
| [仿真与运动规划性能](../plans/simulation-motion-planning-performance.md) | 暂无 | proposed |

## 命名约定

- 项目治理文件采用明确主题名，例如 `v1-baseline-m0.md`、`v1-scope-and-migration-plan.md`。
- 阶段实施方案采用 `v1-stage-{阶段}-{主题}-implementation.md`。
- 实施报告采用 `v1-stage-{阶段}-{主题}.md`；跨阶段报告用 `v1-{主题}.md`。
- 方案和报告按职责分别放入 `plans/`、`reports/`，主题名保持一致。
- 保留稳定的阶段编号只用于业务域 wiki 文件，避免编号同时承担多个用途。

新增文档时同步更新 `implementation/README.md` 和本登记表中的阶段关系；旧报告中的历史
文件名只在明确需要保留历史追溯时出现。
