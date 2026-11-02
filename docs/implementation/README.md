# 实施记录归档

本目录保存项目实施阶段的结论、计划和被报告引用的证据。当前报告文件名保持在本目录根部，以维持 README、wiki、源码和场景工件中的既有链接。

## 阶段报告

| 范围 | 文档 | 内容 |
|---|---|---|
| 阶段零 | [PHASE0_BASELINE.md](PHASE0_BASELINE.md) | 基线、环境和参数冻结 |
| 阶段一 | [PHASE1_REPORT.md](PHASE1_REPORT.md) | 单工位闭环实现与验收 |
| 步骤 1.1 | [STEP1_1_REPORT.md](STEP1_1_REPORT.md) | 布局、取料验证和实验结果 |
| 步骤 1.2 | [STEP1_2_REPORT.md](STEP1_2_REPORT.md) | 场景工件与生命周期实现 |
| 阶段二 | [PHASE2_REPORT.md](PHASE2_REPORT.md) | 物理约束、运动规划和安全联锁 |
| 阶段 2.01 | [PHASE2_01_REPORT.md](PHASE2_01_REPORT.md) | 基础动作与轨迹优化 |
| 阶段 2.02 | [PHASE2_02_REPORT.md](PHASE2_02_REPORT.md) | AGV 转运实验能力的历史记录；路径方案已由 [06-15](../wiki/06-工业案例/06-15-柔性工业车间水平带载转运与放置下探修改方案.md) 取代 |
| 阶段三 | [PHASE3_REPORT.md](PHASE3_REPORT.md) | 双工位、资源和 AGV 转运验收 |
| 阶段四 | [PHASE4_REPORT.md](PHASE4_REPORT.md) | 五岛物流链、共享 AGV 调度、E 岛配对与故障冻结验收；§12 为阶段 4.1 `L5_UFLOW` 布局/路网/高度 datum 与交错批次 |

## 方案与审查

- [LIFECYCLE_VALIDATION_WORKFLOW_PLAN.md](LIFECYCLE_VALIDATION_WORKFLOW_PLAN.md)：生命周期校验流程方案及实施记录。
- [SUCTION_PAD_MOUNT_REVIEW.md](SUCTION_PAD_MOUNT_REVIEW.md)：吸盘安装审查、证据和修改记录。
- 阶段级设计方案维护在 [wiki/06-工业案例](../wiki/06-工业案例/)；全局实施顺序见 [06-05 分阶段执行步骤](../wiki/06-工业案例/06-05-柔性工业车间分阶段执行步骤.md)。

## 运行录制与视频

- [RECORDINGS_REPORT.md](RECORDINGS_REPORT.md)：阶段一至四不同 target/scenario/fault 的录制清单、机位、未完成项与复现命令。视频位于 `runs/recordings/`（Git 忽略），逐条索引见该目录 `INDEX.md`。

## 证据与运行输出

- [evidence/](evidence/)：仅存放被正式报告引用、需要随版本追溯的 JSON/JSONL 证据副本；文件名保留原始时间戳。清单见 [evidence/README.md](evidence/README.md)。
- `runs/`：仿真器或探针生成的临时报告，默认由 `.gitignore` 排除。它不是长期归档；需要支撑正式结论的文件应复制到 `evidence/`，并在报告中引用。
- 运行日志保留在仓库根目录或用户指定位置，不作为阶段报告证据的替代品。

## 归档规则

1. 阶段结论写入对应 `PHASE*_REPORT.md` 或 `STEP*_REPORT.md`，报告链接设计方案和必要证据，不复制整份方案。
2. 计划中的目标和门槛放在 wiki 阶段方案；实施状态和实测结果放在报告。能力被后续方案取代时，旧报告保留为历史记录并明确标注，不覆盖历史数据。
3. 可重复测量脚本与原始探针结果放在 [measurement/](../measurement/)；只有正式引用的证据副本进入 `evidence/`。
