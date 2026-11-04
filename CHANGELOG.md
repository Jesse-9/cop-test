# Changelog

## Unreleased

- 新增配方运行图：`domain/operation_runtime.py` 增加 `RecipeRunContext`、
  `OperationGraph` 与 `OperationRuntime`；`OperationInstance` 扩展
  run/recipe/route/前驱/输入输出/工步字段，仍是任务状态唯一存储。
- 协调器改为由 ready 集合驱动：`_spawn_source()` 按入口固定 route variant
  建图，逐 tick 从 ready 实例投影任务入队；完成提交（物料账本）成功后才
  标记 DONE 并解锁后继，`_submit_followup()` 的静态 successor 推进已删除。
- E 岛合流纳入运行图：输入工序与父实例都就绪后才建 `kit_join` 节点，沿用
  `MaterialRegistry.commit_join()` 的原子双父消费与重复配对拒绝。
- `OrderEntry` 增加 `route_variant_id`（入口 → 路线固定对应，不是动态选路），
  默认配方显式声明 `s1a→housing_direct`、`s1b→housing_alt`、`s2d→bracket_route`；
  `PROCESS_MODEL_VERSION` 升至 `v1_process_20261103b`。
- 新增测试 `tests/test_v1_operation_runtime.py`（20）与
  `tests/test_v1_coordinator_dag.py`（5）；纯逻辑测试 426 passed / 2 skipped。
- 产品入口、场景工件、契约与 `full_flow` 用户运行模式未改变。
- 新增执行适配层 `simulation/execution/`：`ArmExecutor` 包装
  `Stage4UnitController`，`AgvExecutor` 包装 `AGVRouteController4`；
  协调器改为经 `start/pre_tick/post_tick/poll` 启动与轮询，唯一
  `sim.step()` 调用点仍在 `Stage4Coordinator.step()`。
- 新增 `simulation/device_binding.py`：device_id → alias/资源/区域 的静态
  绑定表，覆盖五台臂、AGV、逻辑配对处理器与线/交接/缓冲承载。
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
