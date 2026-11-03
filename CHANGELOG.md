# Changelog

## Unreleased

- 新增执行适配层 `simulation/execution/`：`ArmExecutor` 包装
  `Stage4UnitController`，`AgvExecutor` 包装 `AGVRouteController4`；
  协调器改为经 `start/pre_tick/post_tick/poll` 启动与轮询，唯一
  `sim.step()` 调用点仍在 `Stage4Coordinator.step()`。
- 新增 `simulation/device_binding.py`：device_id → alias/资源/区域 的静态
  绑定表，覆盖五台臂、AGV、逻辑配对处理器与线/交接/缓冲承载。
- 产品入口、`full_flow` 行为、场景工件、契约与工艺目录版本均未改变。
- 阶段 2 focused 仿真实测通过：`arm_A` 完成一次取放，AGV 空载 `A→B`
  与载货 `B→A` 均到站（证据见 `docs/implementation/evidence/`）。

## 1.0.0 (2026-11-03)

v1 收敛：把阶段一至四的实现收敛为独立产品包，只交付五岛 `L5_UFLOW`
场景和 `full_flow` 用户运行模式。

- 新增 `src/flexible_factory/`：`domain`、`simulation`、`artifacts`、
  `application`、`cli`，运行期不导入 `stage1`–`stage4` 或 `reference`。
- 新增唯一入口 `flexible-factory`（console script），命令包括 `list`、
  `status`、`load`、`validate`、`stop`、`migrate`、`build`、`run`。
- 新增 v1 工件命名空间 `docs/scene_artifacts/v1/`，manifest schema `2.0`，
  场景契约 `v1_contract_20261103`。
- 新增 `migrate`：从已验收 stage4 `L5_UFLOW` 工件幂等迁移，源工件只读，
  `v1/current.json` 原子更新。
- 当前 v1 工件 `v1/L5_UFLOW/v001_20261103_004317` 由
  `stage4/L5_UFLOW/v004_20261029_133249` 迁移，场景逐字节一致。
- 发行名从 `factory-sim` 改为 `flexible-factory`；移除 `mcp` 运行时依赖。
- MCP `factory-sim` 服务器不在 v1 范围；`.mcp.json` 和旧 MCP 实现已删除。
- 清理旧阶段源码、入口脚本、报告、测量资料与旧证据；仅保留 v1
  迁移追溯所需的 stage3/stage4 来源工件。
- 实机验收：`full_flow` `all_ok=true`（FINAL 1 件，父 UID [1,3]）；
  `conveyor_fault` 回归冻结/阻塞/无 FINAL/资源释放通过。证据见
  `docs/implementation/evidence/v1_*_acceptance_20261103_*.json`。
