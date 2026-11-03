# 文档导航

`docs/` 只服务 v1 产品：通用 CoppeliaSim 知识、v1 操作指南、v1 实施/验收
记录、场景工件和维护规范。运行命令以项目使用指南为准，工程约束以工程参考
为准，验收事实以 v1 报告和证据为准。

## 按用途查找

| 内容 | 位置 | 职责 |
|---|---|---|
| CoppeliaSim 通用知识 | [wiki/index.md](wiki/index.md)、`wiki/00`–`wiki/05`、`wiki/99` | 平台概念、API、建模、运动、视觉、协同仿真与附录 |
| v1 操作指南 | [项目使用指南](wiki/98-ProjectUsage/USAGE.md) | 启动仿真器、连接 ZMQ、加载/校验 v1 工件、运行 `full_flow` |
| v1 方案与验收 | [V1 方案](implementation/V1_CONSOLIDATION_PLAN.md)、[M0 基线](implementation/V1_BASELINE.md)、[v1 报告](implementation/V1_REPORT.md) | 产品边界、迁移映射、实施与实测记录 |
| 工件与证据 | [实施记录](implementation/README.md)、[场景工件](scene_artifacts/README.md) | v1 工件、manifest、校验摘要和验收证据 |
| 知识库维护规范 | [CLAUDE.md](CLAUDE.md) | wiki 分类、元数据、内容质量和维护要求 |
| 工程实现参考 | [../PROJECT_ENGINEERING_REFERENCE.md](../PROJECT_ENGINEERING_REFERENCE.md) | 模块职责、工程不变量、实现细节和实测陷阱 |
| 官方手册原始资料 | `raw/`（本地资料，不纳入版本控制） | 供知识条目追溯的来源材料；整理后的结论放在 `wiki/` |

## 目录边界

- `wiki/` 是按主题编排的知识库，不作为运行证据或原始测量数据的存放处。
- `implementation/` 只保存 v1 方案、基线、报告和被引用的证据摘要；
  `implementation/runs/` 是忽略跟踪的临时运行输出。
- `scene_artifacts/v1/` 保存 v1 场景工件与生命周期数据；stage3/stage4
  来源工件只作为迁移追溯保留。
- 历史阶段报告、测量脚本和旧证据已清理，不再维护。

## 快速入口

| 想做什么 | 从这里开始 |
|---|---|
| 从安装到运行仿真 | [项目使用指南](wiki/98-ProjectUsage/USAGE.md) |
| 查看 v1 产品范围和验收 | [v1 报告](implementation/V1_REPORT.md)、[项目 README](../README.md) |
| 理解当前实现约束 | [工程参考](../PROJECT_ENGINEERING_REFERENCE.md) |
| 学习 CoppeliaSim 通用功能 | [知识库索引](wiki/index.md) |
| 查场景版本及派生规则 | [场景工件说明](scene_artifacts/README.md) |

代码、配置或运行入口变更后，更新其唯一权威说明及相应索引；不要在多个入口
复制完整操作流程。
