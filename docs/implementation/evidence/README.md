# 证据目录

本目录只存放被 [v1 产品实施报告](../reports/v1-product-implementation.md) 引用的 v1 验收证据，随代码提交。

- `v1_full_flow_acceptance_20261103_010923.json` — v1 `full_flow` 实机验收摘要
  （`all_ok=true`、FINAL UID 4 父 UID [1,3]、无安全锁存、资源释放、双 AGV
  航线通过）。完整运行报告保留在被 Git 忽略的
  `docs/implementation/runs/v1_run_full_flow_20261103_010923.json`。
- `v1_stage0_1_full_flow_acceptance_20261103_132919.json` — 阶段 0-1
  工艺目录移植后的 `full_flow` 实机验收摘要（337.45 s、13/13 DONE、
  FINAL 父 UID [1,3]、无安全锁存、资源释放、双 AGV 航线通过）。
  完整运行报告保留在
  `docs/implementation/runs/v1_run_full_flow_20261103_132919.json`。
- `v1_conveyor_fault_acceptance_20261103_014225.json` — v1 `conveyor_fault`
  代表性故障回归摘要（冻结、`CONVEYOR_FAULT: line_ce 停止`、无 FINAL、
  资源释放、验收 `ok=true`）。
- `v1_stage3_full_flow_20261104_153954.json` — 阶段 3 配方实例化与工序 DAG
  的 `full_flow` 实机验收摘要（337.85 s、13/13 任务 DONE、12 个工序实例全部
  提交、`graph_complete=true`、FINAL UID 4 父 UID [1,3]、未配对 SEMI_CE 1
  件、`rejections=[]`）。相对完整报告裁掉了逐 tick 控制器轨迹与规划详情。
  完整报告保留在
  `docs/implementation/runs/v1_run_full_flow_20261104_153954.json`。

工件级只读校验证据位于
`docs/scene_artifacts/v1/L5_UFLOW/v001_20261103_004317/validation.json`
（迁移时 452 项）和 `flexible-factory validate` 的 454 项实时校验报告。

历史阶段证据已清理；旧阶段场景工件仅保留 v1 迁移来源
（`stage3/L2_MIRROR` 与 `stage4/L5_UFLOW`），见
[场景工件说明](../../scene_artifacts/README.md)。
