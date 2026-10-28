# 文档导航

`docs/` 按读者任务和资料生命周期归档。这里提供入口与职责说明；运行命令以项目使用指南为准，工程约束以工程参考为准，阶段事实以对应报告和可复现证据为准。

## 按用途查找

| 内容 | 位置 | 职责 |
|---|---|---|
| CoppeliaSim 通用知识 | [wiki/index.md](wiki/index.md)、`wiki/00`–`wiki/07`、`wiki/99` | 平台概念、API、建模、运动、视觉、协同仿真、案例与附录 |
| 本项目操作指南 | [项目使用指南](wiki/98-ProjectUsage/USAGE.md)、[MCP 指南](wiki/98-ProjectUsage/MCP_USAGE.md) | 打开仿真器、连接 ZMQ、加载/验证工件、运行阶段和 MCP 工具 |
| 项目实施记录 | [implementation/README.md](implementation/README.md) | 阶段报告、实施方案、审查记录、归档证据及临时运行输出约定 |
| 测量脚本与原始结果 | [measurement/README.md](measurement/README.md) | 可复现探针、分析脚本和测量数据；说明与报告证据副本的关系 |
| 场景工件 | [scene_artifacts/README.md](scene_artifacts/README.md) | 版本化 `.ttt`、manifest、校验结果、派生与保护规则 |
| 知识库维护规范 | [CLAUDE.md](CLAUDE.md) | wiki 分类、元数据、内容质量和维护要求 |
| 工程实现参考 | [../PROJECT_ENGINEERING_REFERENCE.md](../PROJECT_ENGINEERING_REFERENCE.md) | 模块职责、工程不变量、实现细节和实测陷阱 |
| 官方手册原始资料 | `raw/`（本地资料，不纳入版本控制） | 供知识条目追溯的来源材料；整理后的结论放在 `wiki/` |

## 目录边界

- `wiki/` 是按主题编排的知识库，不作为运行证据或原始测量数据的存放处。
- `implementation/` 记录项目实施结论；`implementation/evidence/` 只保留被报告引用的证据副本，`implementation/runs/` 是忽略跟踪的临时运行输出。
- `measurement/` 存放生成/分析数据的脚本及其原始测量结果。若一份结果被正式报告引用，再复制到 `implementation/evidence/` 并保留时间戳。
- `scene_artifacts/` 保存场景工件与生命周期数据，不与普通报告或临时运行日志混放。

## 快速入口

| 想做什么 | 从这里开始 |
|---|---|
| 从安装到运行仿真 | [项目使用指南](wiki/98-ProjectUsage/USAGE.md) |
| 使用 MCP 控制和诊断 | [MCP 使用说明](wiki/98-ProjectUsage/MCP_USAGE.md) |
| 查看当前阶段和验收记录 | [项目 README](../README.md)、[实施记录索引](implementation/README.md) |
| 理解当前实现约束 | [工程参考](../PROJECT_ENGINEERING_REFERENCE.md) |
| 学习 CoppeliaSim 通用功能 | [知识库索引](wiki/index.md) |
| 查场景版本及派生规则 | [场景工件说明](scene_artifacts/README.md) |

代码、配置或运行入口变更后，更新其唯一权威说明及相应索引；不要在多个入口复制完整操作流程。
