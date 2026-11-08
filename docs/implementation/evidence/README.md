# 证据目录

本目录只存放被产品、阶段或专项实施报告引用的 v1 验收证据，随代码提交。

- `v1_full_flow_acceptance_20261103_010923.json` — v1 `full_flow` 实机验收摘要；详见[产品实施报告](../reports/v1-product-implementation.md)。
- `v1_stage0_1_full_flow_acceptance_20261103_132919.json` — 阶段 0–1 `full_flow` 验收摘要；详见[阶段 0–1 报告](../reports/v1-stage-00-01-static-process-catalog.md)。
- `v1_conveyor_fault_acceptance_20261103_014225.json` — v1 `conveyor_fault` 回归摘要；详见[产品实施报告](../reports/v1-product-implementation.md)。
- `v1_stage3_full_flow_20261104_153954.json` — 阶段 3 `full_flow` 验收摘要；详见[阶段 3 报告](../reports/v1-stage-03-operation-dag.md)。
- `v1_full_flow_default_20261105_225825.json` — 默认批次 `full_flow` 实机验收；详见[混线灵活性报告](../reports/v1-mixed-production-flexibility-demo.md)。
- `v1_full_flow_direct_heavy_20261105_233212.json` — `direct_heavy` 批次实机验收；详见[混线灵活性报告](../reports/v1-mixed-production-flexibility-demo.md)。
- `v1_full_flow_detour_heavy_20261106_003836.json` — `detour_heavy` 批次实机验收；详见[混线灵活性报告](../reports/v1-mixed-production-flexibility-demo.md)。
- `v1_full_flow_detour_heavy_rejected_0_2_2_20261106_000425.json` — 初版 `detour_heavy=0/2/2` 的 `BACKPRESSURE_TIMEOUT` 失败边界；详见[混线灵活性报告](../reports/v1-mixed-production-flexibility-demo.md)。
- `v1_order_dag_phase6_acceptance_20261108.json` — 阶段 6 订单 DAG 实机验收：full_flow 基线摘要、UI 手工订单、planner 订单、`NODE_PRECHECK_REJECTED` 局部拒绝、安全锁存 GLOBAL 停止、失败 attempt 与未覆盖边界；详见[阶段 6 实施报告](../reports/v1-order-dag-phase-06-simulation-acceptance.md)。

工件级只读校验证据位于
`docs/scene_artifacts/v1/L5_UFLOW/v001_20261103_004317/validation.json`；校验细节见
[场景工件说明](../../scene_artifacts/README.md)和产品实施报告。

历史阶段证据已清理；旧阶段场景工件仅保留 v1 迁移来源
（`stage3/L2_MIRROR` 与 `stage4/L5_UFLOW`），见
[场景工件说明](../../scene_artifacts/README.md)。
