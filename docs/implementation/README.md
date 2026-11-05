# 实施记录

本目录只保留 v1 产品的实施记录与证据。历史阶段一至四的报告与正式证据已按
v1 收敛要求清理；`runs/` 中可能残留本地忽略的旧阶段运行输出，不纳入版本控制，
也不作为 v1 证据。旧实现已从 `src/` 移除，v1 源码见
[`src/flexible_factory/`](../../src/flexible_factory/)。

目录边界、阶段对应关系和命名规则见
[项目文档登记表](PROJECT_DOCUMENT_REGISTER.md)。本目录记录如何实施和如何验收；
业务目标、工艺路线和领域对象统一放在
[`wiki/06-工业案例/柔性车间/`](../wiki/06-工业案例/柔性车间/README.md)。

## v1 文档

| 文档 | 内容 |
|---|---|
| [V1_CONSOLIDATION_PLAN.md](V1_CONSOLIDATION_PLAN.md) | v1 产品边界、迁移映射、工件策略、测试矩阵和分批门槛 |
| [V1_BASELINE.md](V1_BASELINE.md) | M0 范围、决策、代码/工件/测试基线 |
| [V1_REPORT.md](V1_REPORT.md) | v1 实施、迁移、测试和实机验收记录；未执行项与限制 |
| [V1_STAGE0_1_REPORT.md](V1_STAGE0_1_REPORT.md) | 阶段 0-1 静态工艺目录移植到 `flexible_factory` 的代码映射与验证 |
| [V1_STAGE2_EXECUTOR_PLAN.md](V1_STAGE2_EXECUTOR_PLAN.md) | 阶段 2 设备目录与执行适配器在 v1 代码上的实施方案 |
| [V1_STAGE2_REPORT.md](V1_STAGE2_REPORT.md) | 阶段 2 实施记录：静态绑定、纯测试与代表臂/代表 AGV focused 实测 |
| [V1_STAGE3_OPERATION_DAG_REPORT.md](V1_STAGE3_OPERATION_DAG_REPORT.md) | 阶段 3 实施记录：配方实例化、工序 DAG、ready 解锁与 E 合流，含 `full_flow` 基线对照 |
| [V1_STAGE4_SCHEDULER_PLAN.md](V1_STAGE4_SCHEDULER_PLAN.md) | 阶段 4 实施方案：runtime 写入口修复、纯调度策略、能力/资源筛选、coordinator 接入与分层验收 |
| [V1_STAGE4_REPORT.md](V1_STAGE4_REPORT.md) | 阶段 4 实施记录：纯调度策略、设备选择决策、runtime 写入口边界与 `full_flow` 验收 |
| [V1_MIXED_PRODUCTION_FLEXIBILITY_PLAN.md](V1_MIXED_PRODUCTION_FLEXIBILITY_PLAN.md) | 混线生产灵活性扩展示范方案：有限批次 profile、设备替代和同一物料路线选择的分阶段实施与验收 |
| [RESOURCE_DATABASE_PLAN.md](RESOURCE_DATABASE_PLAN.md) | 资源目录数据库化与知识图谱方案：目标 schema、现有数据映射、dump 识别流程、分阶段执行与验收门槛 |
| [PERFORMANCE_AND_PATH_REUSE_PLAN.md](PERFORMANCE_AND_PATH_REUSE_PLAN.md) | 路径复用与规划性能的跨阶段技术提案，尚未纳入当前 v1 范围 |

## 文档分组

- **治理与基线**：`V1_CONSOLIDATION_PLAN.md`、`V1_BASELINE.md`、`V1_REPORT.md`。
- **阶段实施**：`V1_STAGE*_..._PLAN.md` 与对应的 `V1_STAGE*_..._REPORT.md`。
- **跨阶段提案**：`V1_MIXED_PRODUCTION_FLEXIBILITY_PLAN.md`、
  `RESOURCE_DATABASE_PLAN.md`、`PERFORMANCE_AND_PATH_REUSE_PLAN.md`；这些文件有
  `proposed` 状态时只是候选工作，不改变当前 v1 能力。
- **正式证据**：`evidence/` 只保存被报告引用的裁剪证据，`runs/` 只保存被忽略的临时输出。

## 证据

- [evidence/](evidence/)：被报告引用的 v1 验收摘要，包括
  `v1_full_flow_acceptance_*.json`、`v1_conveyor_fault_acceptance_*.json`、
  `v1_stage0_1_full_flow_acceptance_*.json`、阶段 2 的
  `v1_stage2_arm_*.json`、`v1_stage2_agv_*.json`、阶段 3 的
  `v1_stage3_full_flow_*.json` 与阶段 4 的
  `v1_stage4_full_flow_20261104_210605.json`。
- `runs/`：仿真器生成的临时报告，默认由 `.gitignore` 排除；正式结论引用的
  文件应复制到 `evidence/` 并在报告中引用。

## 归档规则

1. 当前行为写入 v1 报告和 [项目使用指南](../wiki/98-ProjectUsage/USAGE.md)；
   不保留与 v1 无关的阶段计划、阶段报告和旧证据。
2. 能力变化时更新 v1 报告，明确区分已实测、未执行和已知限制。
3. 可复现测量脚本如需保留，应直接面向 v1 包；历史阶段测量不再维护。
