# 正式验收证据目录

此处按主题收纳由实施报告引用、纳入版本控制的精简证据。报告解释验收范围和结论，JSON 保存可核对字段；`docs/implementation/runs/` 是 Git 忽略的临时运行输出，不属于本目录。

| 主题 | 证据 |
|---|---|
| [生产流程](production-flow/) | v1 与阶段 0–4 的 `full_flow`、故障回归、设备 focused 验收、混线批次证据和 2026-11-14 `assembly_mvp` 压缩机→法兰→压缩机实机交替验收；2026-11-24 阶段 5 WP0/WP1 的 `cart_2` 不可用业务用例、归档运行基线重算与故障基线逻辑证据（未启动 CoppeliaSim）；对应[生产流程报告](../reports/production-flow/) |
| [订单编排](order-orchestration/) | 订单 DAG 阶段 6 的基线、手工/planner 订单、NODE 前置拒绝和 GLOBAL 安全锁存验收；阶段 7 的 single 回归、dual 手工/planner 订单和真实 UI 双车启动（`cart_2` D→E / `agv_1` B→A）；阶段 8 的车队感知 planner dual 假设、dual/single 运行及假设不一致标记；阶段 9 的 scene profile 目录、full_flow 直达/绕行与 MVP 压缩机/法兰 UI run、跨 profile 422 拒绝；阶段 10 的 15 个订单方案变体、物化/API/前端 E2E 逻辑证据；订单运行路径复用接入的服务/API/runner 单元与前端 E2E 逻辑证据；分别对应[阶段 6 报告](../reports/order-orchestration/v1-order-dag-phase-06-simulation-acceptance.md)、[阶段 7 报告](../reports/order-orchestration/v1-order-dag-phase-07-dual-fleet.md)、[阶段 8 报告](../reports/order-orchestration/v1-order-dag-phase-08-fleet-aware-planner.md)、[阶段 9 报告](../reports/order-orchestration/v1-order-dag-phase-09-scene-profile-order-catalog.md)、[订单方案目录报告](../reports/order-orchestration/v1-order-variant-catalog-workbench.md)和[订单运行路径复用报告](../reports/order-orchestration/v1-order-run-path-reuse.md) |
| [数据平台](data-platform/) | 目录/运行记录和知识图谱导入证据；对应[data 平台报告](../reports/data-platform/) |
| [仿真工程](simulation/) | 2026-11-12 启动阶段计时（两次 load、一次 `--no-load`）、2026-11-13 alias/销毁优化后复测、`full_flow` 功能验收摘要和 4 臂子集订单验收；对应[启动性能优化报告](../reports/simulation/v1-startup-performance-optimization.md)。2026-11-20 固定 A 臂 `PLACING` 段 `PLAN_ONLY` → `CACHE_ONLY` focused 对照，对应[运动规划性能报告](../reports/simulation/v1-motion-planning-performance.md)；2026-11-21 五臂 `PLAN_ONLY` 生成与 `CACHE_THEN_PLAN` full_flow 对照，对应[五臂路径回放扩展报告](../reports/simulation/v1-all-arm-path-reuse.md)；2026-11-24 两次 10–20 s 运行录像短片元数据，对应[运行录像报告](../reports/simulation/v1-simulation-recording.md)。这些是性能/运行记录，不是完整功能验收结论 |

场景包只读校验证据保存在 `docs/scene_artifacts/v1/L5_UFLOW/v001_20261103_004317/validation.json`，生命周期和校验规则见[场景包说明](../../scene_artifacts/README.md)。

证据文件命名保留验收主题和时间戳。不得覆盖失败 attempt 或将准备性运行作为正式通过证据；报告需说明未执行项和适用范围。
