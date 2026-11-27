---
title: 仿真模块代码优化剩余工作 — 性能与命名
type: plan
status: proposed
created: 2026-11-24
updated: 2026-11-26
scope: src/flexible_factory/simulation / 未实施维护项
related:
  - ../../reports/simulation/v1-simulation-code-optimization.md
  - ../../archive-plans/simulation/simulation-code-optimization-plan.md
  - ../../reference/simulation/simulation-module-inventory.md
---

# 仿真模块代码优化剩余工作 — 性能与命名

本方案只保留仿真模块代码优化的**未实施**维护项；P3 5.2 异常路径回滚属于
异常恢复范围，已从当前规划移除。P1（控制器输送带取料收敛）、P2（Stage3/v1
运行时装配公共逻辑）、P3 5.1（相邻阶段模块合并）、P3 5.3 的规划器模块拆分和
§6 模块清理已实施，见
[实施报告](../../reports/simulation/v1-simulation-code-optimization.md)；
完整原始方案已归档到
[仿真模块代码优化完整方案](../../archive-plans/simulation/simulation-code-optimization-plan.md)。

## 1. P3 5.3：性能优化（未开始）

**现状：**规划器已拆分为 `plan_types`、`plan_checks`、`plan_search`、
`plan_postprocess`，算法/容差/预算未改；没有新的规划统计或
`motion/metrics.py` 证据支持调整采样、缓存、碰撞查询或仿真 API 调用。

**工作：**

1. 先复用 `motion/metrics.py` 和现有计时字段，采集一个固定动作的
   `search_wall_s`、`verify_wall_s`、IK、碰撞查询和命令数基线。
2. 只有某一项被证据证明是主要耗时，才做单因素优化；每轮保持相同场景、
   起点、目标、故障设置和验收指标。
3. 不通过放宽碰撞采样、安全容差或执行联锁换取速度；收益不稳定或安全/
   终点指标退化时不合入。

**通过门槛：**有可重复的计时对照和成功率/终点误差/最小净距/命令数报告；
没有证据时不做代码改动。

## 2. v1 模块重命名（未实施）

**现状：**`scene.py`、`layout.py`、`unit_controller.py` 等名称与当前职责
基本一致；没有足够证据表明名称造成持续误导，且重命名需要与契约文档、
测试和调用方一起迁移。

**工作：**只有在出现明确的维护成本或命名冲突时，才单独提出重命名方案；
一次性迁移契约、测试、文档和兼容层，不保留长期双名称。

**通过门槛：**调用方和文档全部更新、导入边界测试通过、无兼容性回归。

## 3. 验证与边界

- 若需要实机结论，启动 CoppeliaSim 4.10 后运行受影响的 Stage2/Stage4
  实机用例，并按正式 evidence 流程记录；本轮不预设通过。
- 不改变场景工件、`config_digest()`/`layout_digest()`、控制器 ID 或
  碰撞/运动安全判据。
- 不把本方案当作功能验收；每个工作包独立提交、独立报告。