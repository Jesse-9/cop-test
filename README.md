# Flexible Factory Simulation

基于 CoppeliaSim 4.10+、Python 和 ZeroMQ Remote API 的分阶段柔性工业车间仿真。当前可运行主线为阶段一单元闭环、阶段二物理约束与规划、阶段三双工位搬运；统一故障恢复和阶段四多岛生产尚未实现。src/reference/ 保留旧五岛实现，供迁移与诊断使用。

## 从哪里开始

- 仿真软件启动、ZMQ 连接、场景工件操作、阶段运行命令与故障排查：[项目使用指南](docs/wiki/98-ProjectUsage/USAGE.md)
- MCP 工具与 Claude Code 接入：[MCP 使用说明](docs/wiki/98-ProjectUsage/MCP_USAGE.md)
- 阶段设计和未来工作边界：[分阶段执行计划](docs/wiki/06-工业案例/06-05-柔性工业车间分阶段执行步骤.md)
- 工件版本、保护、校验和派生：[场景工件说明](docs/scene_artifacts/README.md)
- 工程不变量、模块实现细节和实测陷阱：[工程参考](PROJECT_ENGINEERING_REFERENCE.md)
- 知识库结构与写作规范：[docs/README.md](docs/README.md) · [docs/CLAUDE.md](docs/CLAUDE.md)

## 当前状态

| 阶段 | 状态 | 主要结果 | 详细记录 |
|---|---|---|---|
| 阶段一 | 已验收 | 单工位取放、传送带和 pioneer p3dx AGV；只读基线 stage1/L1_S1/v002_20261020_234403 | [阶段一报告](docs/implementation/PHASE1_REPORT.md) |
| 步骤 1.1 / 1.2 | 已完成 | L1_S1 布局与取料流程收敛；版本化场景生命周期 | [1.1 报告](docs/implementation/STEP1_1_REPORT.md) · [1.2 报告](docs/implementation/STEP1_2_REPORT.md) |
| 阶段二 | 已验收 | 碰撞检查、局部规划、轨迹执行、接触判据与安全锁存；基线 stage2/L1_S1/v013_20261024_232558 | [阶段二报告](docs/implementation/PHASE2_REPORT.md) |
| 阶段 2.01 | 已验收 | 动作与轨迹优化；生产路径采用已验证配置 | [报告](docs/implementation/PHASE2_01_REPORT.md) |
| 阶段 2.02 | 已实现，实验能力默认关闭 | AGV 转运候选优化尚未满足切换默认配置的门槛 | [报告](docs/implementation/PHASE2_02_REPORT.md) |
| 阶段三 | 已通过五个验收场景 | 双工位、资源锁和 AGV 两站转运；基线 stage3/L2_MIRROR/v004_20261025_131837 | [阶段三报告](docs/implementation/PHASE3_REPORT.md) |
| 阶段 2.1 统一故障恢复 | 未开始 | 自动复位与任务恢复契约尚不存在 | [分阶段执行计划](docs/wiki/06-工业案例/06-05-柔性工业车间分阶段执行步骤.md) |
| 阶段四多岛生产 | 未开始 | A–E 拓扑、仓库供料、节拍与共享 AGV 调度 | [分阶段执行计划](docs/wiki/06-工业案例/06-05-柔性工业车间分阶段执行步骤.md) |

生命周期校验流程（2026-10-26 已实施）：load 后可直接 start，validate 为可选诊断；start 在运行锁内执行完整校验，save 仍要求已校验场景。详见[方案与实施记录](docs/implementation/LIFECYCLE_VALIDATION_WORKFLOW_PLAN.md)。

阶段三遇到阶段二安全锁存时会冻结原任务，不自动恢复。阶段 2.1 尚未实现前，不得加入自动清除安全锁存或重启失败任务的行为。

## 入口与结构

- Python 依赖和可执行入口定义在 pyproject.toml，本地环境通常由 uv sync 管理。
- 当前阶段控制器位于 src/stage1/、src/stage2/、src/stage3/；命令包装器为 src/run_stage1.py、src/run_stage2.py、src/run_stage3.py。
- 场景生命周期和工件操作入口为 src/run_scene_lifecycle.py；它不会在 start 时隐式构建或清空场景。
- src/stage1_1/ 保存布局验证与受控实验工具；src/reference/ 是历史五岛实现，包括 MCP 服务器。
- 自动化测试位于 tests/；阶段验收报告位于 docs/implementation/。

当前各入口的准确参数和可复制命令以 [项目使用指南](docs/wiki/98-ProjectUsage/USAGE.md) 与运行 --help 的结果为准。修改入口、阶段状态或配置约束时，同步维护对应的唯一说明文档，避免在 README 中复制整套操作步骤。
