---
title: 自建订单 DAG 阶段 6 — CoppeliaSim 集成验收与发布
type: plan
status: proposed
created: 2026-11-06
updated: 2026-11-06
scope: v1 UFLOW / CoppeliaSim 4.10
related:
  - v1-self-built-order-dag-orchestration.md
  - v1-order-dag-phase-01-foundation.md
  - v1-order-dag-phase-02-failure-isolation.md
  - v1-order-dag-phase-03-api-persistence.md
  - v1-order-dag-phase-04-auto-planner.md
  - v1-order-dag-phase-05-frontend.md
  - ../reports/v1-stage-04-capability-scheduling.md
---

# 自建订单 DAG 阶段 6：CoppeliaSim 集成验收与发布

## 目标与前置条件

证明手工订单和 planner 订单经过同一编译/调度/执行链，在受保护的 `L5_UFLOW` 场景运行；验证局部失败隔离与全局停止分别符合契约。必须先完成阶段 1–5 的门槛；运行前确认 CoppeliaSim 4.10、ZMQ 23000、当前工件 digest 和基线 evidence。不得为了验收覆盖或修改受保护 scene artifact。

## 具体执行步骤

1. 运行新增纯逻辑、API、planner、UI contract 和全量既有测试；保存测试版本/提交号及结果。若失败，先修复，不进入实机运行。
2. 确认当前 v1 工件 manifest、scene/config/layout digest 与阶段 4 evidence 可比；确认运行锁、报告输出路径、SQLite 备份策略和单实例 CoppeliaSim 会话可用。
3. 先用记录型 executor 完成 dry-run：订单版本加载、DAG 编译、依赖 ready/commit、物料账本、订单终态与 run record 全流程。
4. 运行模板回归订单，对照既有 `full_flow` evidence：投料/完成数量、工序实例与顺序约束、FINAL UID/父 UID、AGV 路线、资源清零、运行报告和 runtime destroy。
5. 运行一份用户手工构造的最小合法订单，再运行一份后端 planner 生成的订单；分别保存订单版本、digest、工件 ID、测试命令与 evidence，确认两者调用同一 runner。
6. 进行局部失败 focused case：在经分类允许局部隔离的节点触发失败，同时安排一个无依赖分支处于 ready/running。核对失败节点标记、后继阻断、独立分支继续和提交、工位安全状态、资源释放与 `PARTIAL_FAILED`/`FAILED` 订单终态。
7. 进行全局失败 focused case：触发安全锁存；另用可控 harness 覆盖仿真 step/连接故障。确认所有活动执行停止、普通资源释放、安全锁存仍保留、终态为 `ABORTED`，无节点误报成功。
8. 核对前端显示的节点状态、事件顺序、运行报告和持久化记录一致；刷新页面后仍能读取终态和失败诊断。
9. 汇总每次运行，不把多个失败尝试拼成一次通过；记录未执行项、物理边界和已分类仍为 global 的失败类型，形成实施报告与精简 evidence。

测试策略：纯逻辑/API/UI 测试在前序阶段完成；本阶段只增加真实仿真无法由 mock 证明的验收。每种成功订单跑一条代表性全链路，每类故障跑一个 focused case；不为同一失败传播语义重复堆叠仿真矩阵。

## 禁止的验收替代

- 纯逻辑 dry-run 不能替代设备运动、碰撞、抓取/放置和物料物理状态验收。
- 默认订单通过不能替代用户自建图、planner 图或失败隔离验收。
- 局部故障 harness 不能替代真实运动风险审查；未确认工位达到已知安全状态的错误不得在实机开放为 NODE scope。
- 不得以放宽 IK、碰撞、停靠、接触、安全阈值或工件契约掩盖失败。

## 交付与发布门槛

- 实施报告分别给出纯逻辑、API/UI、默认仿真、自建订单、planner 订单、局部失败和全局故障结果。
- 每条 evidence 可追溯到 order version、DAG digest、artifact ID、目录版本和 run ID；记录失败节点/范围及受阻/继续分支。
- 所有测试和验收结果通过；失败尝试如实保留；未验证的故障分类维持 global；protected artifact 未被改写。
- 发布说明包含服务/API/UI 启动方式、CoppeliaSim 前置条件、订单状态含义、停止行为和已知限制。
