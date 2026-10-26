# cop-test 项目索引

本文件作为仓库级 AI 工作索引，保持短小，避免把阶段报告、运行手册和工程细节重复注入每次工作上下文。开始任务时按下表读取所需资料；文档与代码冲突时，以当前实现和可复现证据为准，并修正文档。

## 必须遵循的项目不变量

- 不覆盖用户未提交的场景、日志、文档或配置；禁止使用破坏性回退丢弃改动。
- 当前主线是阶段一至三；src/reference/ 是历史五岛兼容实现，不能误认作当前生产入口。
- 控制器使用 CoppeliaSim 4.10+ ZeroMQ Remote API；正式模型和控制约束、物理/UID/停止联锁要求见 [工程参考](PROJECT_ENGINEERING_REFERENCE.md)。
- 场景 start 不隐式构建或清空；必须使用对应阶段工件并通过校验。阶段二、三从规定的已验收 parent 派生，不能修改受保护工件。
- 代码、场景、运行命令和文档需保持一致；运行方式或阶段入口变化时同步更新 [项目使用指南](docs/wiki/98-ProjectUsage/USAGE.md)。
- 阶段三安全锁存不得自动恢复；统一故障恢复和阶段四目前未实现。不得将计划描述成已具备能力。

## 按任务读取

| 工作 | 规范或唯一/权威资料 |
|---|---|
| 启动 CoppeliaSim、配置 ZMQ、加载工件、运行各阶段 | [项目使用指南](docs/wiki/98-ProjectUsage/USAGE.md) |
| MCP 工具、参数和运行行为 | [MCP_USAGE.md](docs/wiki/98-ProjectUsage/MCP_USAGE.md) |
| 工程不变量、当前模块职责、阶段设计细节、实测陷阱 | [工程参考](PROJECT_ENGINEERING_REFERENCE.md) |
| 阶段实施方案与未来阶段边界 | [06-05 分阶段执行步骤](docs/wiki/06-工业案例/06-05-柔性工业车间分阶段执行步骤.md) |
| 工件 schema、保护策略、current 指针和派生规则 | [场景工件说明](docs/scene_artifacts/README.md) |
| 项目状态、阶段验收摘要、代码结构 | [README.md](README.md) |
| wiki 笔记 schema、标签、撰写流程 | [docs/CLAUDE.md](docs/CLAUDE.md) |

只在任务需要时读取链接文档。改动相关规范或能力后，再更新对应的权威资料，不复制整段内容到本文件。
