---
title: v1 启动性能优化剩余工作 — P4 与计时归因
type: plan
status: proposed
created: 2026-11-24
updated: 2026-11-25
scope: v1 启动性能 / 长驻 worker / 未实施
related:
  - ../../reports/simulation/v1-startup-performance-optimization.md
  - ../../archive-plans/simulation/v1-startup-performance-optimization.md
  - ../../../tools/measure_startup_stages.py
---

# v1 启动性能优化剩余工作 — P4 与计时归因

本方案只保留启动性能优化的**未实施/未归因**项。P1/P2、P3 订单子集臂和
清理修复已实施；已有计时与实机结论见
[实施报告](../../reports/simulation/v1-startup-performance-optimization.md)。
完整原始方案已归档到
[启动性能完整方案](../../archive-plans/simulation/v1-startup-performance-optimization.md)。

## 1. P4：长驻 worker 运行时复用（暂缓）

**方向：**只对 `api serve` 等长驻 worker 的第 2 次及以后运行复用
`Stage4Runtime`，不重新 `loadScene`。CLI/新进程不适用。

**前置条件（缺一不可）：**

1. 每次运行前重置 target/parts/hazards/载荷开关，并重新执行 FK 与
   `base_vs_workcell` 检查。
2. 场景重新加载、工件变更或发现 `Shadow_*`/collection 残留时 fail closed，
   回退完整重建。
3. 定义运行状态复用边界：资源 owner、控制器、航线、物料账本和安全锁存
   必须在运行之间清空或显式证明无残留。
4. 有可重复的启动计时对照，证明复用收益稳定且不改变 `full_flow` 语义。

**通过门槛：**重置协议、残留检查和 fail closed 完成后再立项；未证明前
不实现、不把“长驻 worker 可能更快”写成已具备能力。

## 2. 计时归因补齐（部分已具备）

**现状：**`tools/measure_startup_stages.py` 已实现
`profile.adopt_stage4_scene`、`profile.ensure_scene`、
`profile.build_validation_context`、`profile.validate_artifact`、
`profile.validate_contract` 子阶段计时，并记录 simulator、config digest、
artifact ID 和 manifest digest；清理残留复测已有 `leftover=[]` 结果。
仍未完成：独立的 `loadScene` 计时（当前只合并在 `artifact_load` 中）、
`layout_digest` 字段，以及同机 A/B 交替归因。

**剩余工作：**

1. 在 `tools/measure_startup_stages.py` 中把 `loadScene` 从
   `artifact_load` 中拆成独立计时字段，或明确记录 `artifact_load −
   profile.adopt_stage4_scene` 的可复算关系；不改变产品代码。
2. 在计时 JSON 中补充 `layout_digest`；simulator/config/artifact 字段已有，
   保持兼容。
3. 在同一机器上做 A/B 交替（至少 3 次/组），报告中位数和波动；不只比较
   历史两次绝对值。
4. 清理残留复测保持 `leftover=[]`；若出现残留，先修清理再谈性能。

**通过门槛：**三次运行 JSON 含 loadScene 独立字段、layout digest 和环境字段；
A/B 结论可重复；不改变产品代码和场景工件。

## 3. 边界

- 不通过跳过启动点校验、减少集合成员或放宽清理检查换启动时间。
- 不在运行中懒构建影子臂/集合；构建要求停止态。
- 不把单机、单场景、3 次运行的计时当作跨机器/跨场景保证。
- 每个工作包独立提交、独立报告；P4 未立项前保持默认完整重建。