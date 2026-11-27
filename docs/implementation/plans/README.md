---
title: 活动规划索引 — 仅未实施工作
type: index
status: current
created: 2026-11-25
updated: 2026-11-25
---

# 活动规划索引 — 仅未实施工作

本目录只保留**尚未实施**的规划。已完成的规划原文件归档到
[archive-plans/](../archive-plans/README.md)，实施事实与验收记录保留在
[reports/](../reports/README.md)；不要用本目录的计划状态替代报告/证据结论。

## 未实施规划

| 主题 | 规划 | 范围 |
|---|---|---|
| 架构整理 | [应用编排模块边界整理](architecture/application-module-boundaries.md) · [扩展场景与设备拓扑边界](architecture/scenario-extension-boundaries.md) | coordinator 按证据逐个迁移职责；场景拓扑扩展不复制核心调度循环 |
| 业务阶段 5 | [设备状态、未启动工序改派与规则调度](production-flow/v1-stage-05-remaining-work.md) | WP2/WP3/WP6；WP0/WP1 已实施；WP4/WP5 异常恢复规划已移除 |
| 仿真模块维护 | [性能与命名](simulation/simulation-code-optimization-remaining.md) | P3 5.3 性能、模块重命名 |
| 运动规划效率 | [全阶段路径缓存、P2/P3 与未覆盖运动段](simulation/simulation-motion-planning-performance-remaining.md) | 路径缓存从 `PLACING` 扩展到其余执行阶段；多目标/多段、默认值、写回与观测；P2/P3 |
| 启动性能 | [P4 与剩余计时归因](simulation/v1-startup-performance-optimization-remaining.md) | 长驻 worker 复用；loadScene/layout digest 与同机 A/B；子阶段计时已具备 |
| 数据平台 | [资源目录与知识图谱剩余工作](data-platform/resource-catalog-database-and-knowledge-graph-remaining.md) | 阶段 5.1 语义映射与候选发布（运行记录版本钉住/Q1–Q4 已具备）、阶段 6 运维/Q5/Q6 |
| 数据平台 | [数据库仪表盘前端优化](data-platform/db-dashboard-frontend-optimization.md) | 提案，未实施 |
| 数据平台 | [数据库仪表盘 UI 蓝图](data-platform/db-dashboard-ui-blueprint.md) | 提案，未实施 |

## 归档与分类规则

- **已完成的规划**（frontmatter `status: implemented`）移动到
  `archive-plans/{主题}/`；只保留历史追溯价值，不再作为活动计划维护。
- **部分实施的规划**把已完成步骤留在归档原文件，本目录只保留未实施的工作包；
  报告记录已完成范围和证据。
- **实施报告**按主题保留在 `reports/{theme}/`，不随规划移动；报告回答“实际
  完成了什么”，不是活动计划。
- **验收证据**保留在 `evidence/{theme}/`；`runs/` 仍是被 Git 忽略的临时输出。
- 新增活动规划时只写未实施工作；若计划全部完成，移动到 `archive-plans/` 并
  更新本索引、[文档登记表](../governance/project-document-register.md)和
  [实施资料索引](../README.md)。
