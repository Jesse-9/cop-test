# cop-test 项目索引

本文件作为仓库级 AI 工作索引，保持短小，避免把阶段报告、运行手册和工程细节重复注入每次工作上下文。开始任务时按下表读取所需资料；文档与代码冲突时，以当前实现和可复现证据为准，并修正文档。

## 必须遵循的项目不变量

- 不覆盖用户未提交的场景、日志、文档或配置；禁止使用破坏性回退丢弃改动。
- 当前主线是 **v1 五岛 `L5_UFLOW` 产品**：`src/flexible_factory/`，唯一用户入口 `flexible-factory`，唯一用户运行模式 `full_flow`。
- `src/stage1/`–`src/stage4/`、`src/stage1_1/`、`src/reference/` 已删除；v1 运行路径不得重新引入旧阶段包。
- 控制器使用 CoppeliaSim 4.10+ ZeroMQ Remote API；正式模型和控制约束、物理/UID/停止联锁要求见 [工程参考](PROJECT_ENGINEERING_REFERENCE.md)。
- 场景 start 不隐式构建或清空；必须加载已校验的 v1 工件。迁移与派生不得覆盖保留的迁移来源工件或 v1 已验收工件。
- 代码、场景、运行命令和文档需保持一致；运行方式或入口变化时同步更新 [项目使用指南](docs/wiki/98-ProjectUsage/USAGE.md)。
- 阶段三安全锁存不得自动恢复；统一故障恢复和阶段四之后的混线规划未实现。不得将计划描述成已具备能力。
- MCP 不在 v1 范围；`.mcp.json` 与旧 `factory-sim` 实现已移除。

## 按任务读取

| 工作 | 规范或唯一/权威资料 |
|---|---|
| 启动 CoppeliaSim、配置 ZMQ、加载工件、运行 v1 | [项目使用指南](docs/wiki/98-ProjectUsage/USAGE.md) |
| v1 产品边界、代码结构、迁移与验收 | [V1 收敛方案](docs/implementation/V1_CONSOLIDATION_PLAN.md) · [M0 基线](docs/implementation/V1_BASELINE.md) · [v1 报告](docs/implementation/V1_REPORT.md) |
| 工程不变量、当前模块职责、实测陷阱 | [工程参考](PROJECT_ENGINEERING_REFERENCE.md) |
| 工件 schema、保护策略、current 指针和派生规则 | [场景工件说明](docs/scene_artifacts/README.md) |
| 项目状态与代码结构 | [README.md](README.md) |
| 历史阶段结论与未来边界 | [V1 实施报告](docs/implementation/V1_REPORT.md) |
| wiki 笔记 schema、标签、撰写流程 | [docs/CLAUDE.md](docs/CLAUDE.md) |

只在任务需要时读取链接文档。改动相关规范或能力后，再更新对应的权威资料，不复制整段内容到本文件。
