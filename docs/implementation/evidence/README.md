# 正式验收证据目录

此处按主题收纳由实施报告引用、纳入版本控制的精简证据。报告解释验收范围和结论，JSON 保存可核对字段；`docs/implementation/runs/` 是 Git 忽略的临时运行输出，不属于本目录。

| 主题 | 证据 |
|---|---|
| [生产流程](production-flow/) | v1 与阶段 0–4 的 `full_flow`、故障回归、设备 focused 验收和混线批次证据；对应[生产流程报告](../reports/production-flow/) |
| [订单编排](order-orchestration/) | 订单 DAG 阶段 6 的基线、手工/planner 订单、NODE 前置拒绝和 GLOBAL 安全锁存验收；对应[阶段 6 报告](../reports/order-orchestration/v1-order-dag-phase-06-simulation-acceptance.md) |
| [数据平台](data-platform/) | 目录/运行记录和知识图谱导入证据；对应[data 平台报告](../reports/data-platform/) |

场景包只读校验证据保存在 `docs/scene_artifacts/v1/L5_UFLOW/v001_20261103_004317/validation.json`，生命周期和校验规则见[场景包说明](../../scene_artifacts/README.md)。

证据文件命名保留验收主题和时间戳。不得覆盖失败 attempt 或将准备性运行作为正式通过证据；报告需说明未执行项和适用范围。
