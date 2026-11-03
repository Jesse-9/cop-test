---
title: 柔性工业车间 v1 实施基线（M0）
type: report
status: review
created: 2026-11-03
updated: 2026-11-03
scope: v1 收敛与迁移
---

# 柔性工业车间 v1 实施基线（M0）

本文件记录 [V1 收敛与迁移方案](V1_CONSOLIDATION_PLAN.md) §8 M0 的冻结结果。
它只记录**已实测事实**与已固定决策；实现进度和验收结论写入
`V1_REPORT.md`，不得把计划改写成已完成。

## 1. 范围与入口决策

| 决策项 | 结论 | 依据 |
|---|---|---|
| 产品包名 | `flexible_factory`；发行名 `flexible-factory`，版本 `1.0.0` | 方案 §4、§11-1 |
| 唯一用户运行模式 | `full_flow` | 方案 §1 |
| 内部回归场景 | `no_pair`、`agv_priority`、`agv_blocked`、`conveyor_fault`、`staggered_batch` 仅通过显式 `--scenario` 供测试/诊断使用，不作为用户入口承诺 | 方案 §1 |
| MCP | **不纳入 v1 产品范围**。`.mcp.json`、`src/reference/factory_mcp_server.py` 与 `tests/test_mcp_*` 已删除，不再作为 v1 接入方式 | 方案 §3.2、§11-1 |
| 工件根 | `docs/scene_artifacts/v1`；`current.json` 位于该根下 | 方案 §5.1 |
| 工件命名 | `L5_UFLOW/v001_<YYYYmmdd_HHMMSS>` | 方案 §5.1 |
| CI | 当前无 Windows + CoppeliaSim CI agent；集成验收由受控本地流程执行，证据归档到 `docs/implementation/evidence/` | 方案 §11-4 |

## 2. 代码基线（2026-11-03）

- 分支：`chore/workspace-cleanup`
- HEAD：`1da51e9 docs: organize flexible workshop plans`
- 工作区既有改动（不得覆盖）：`docs/implementation/README.md`、`stage4_sim.log`
- Python：3.11.15（`.venv`）
- 测试基线：`492 passed, 3 skipped in 66.64s`
  - 命令：`.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`
  - 3 个 skip 为 CoppeliaSim 未运行时的 MCP 集成测试（M0 基线快照；相关 MCP 测试已删除）。

## 3. 场景工件基线

| 项目 | 值 |
|---|---|
| 当前 v4 工件 | `stage4/L5_UFLOW/v004_20261029_133249` |
| `source_phase` | `stage4` |
| `protected` | `true` |
| manifest SHA-256 | `e5f4d75c33c866394c60d2e444c029148a5de80051b4e15a0187f0a73fca5c3a` |
| scene.ttt SHA-256 | `bf7e4df6681abf8027e3f04e3bb214fc5a1f707d1be8922a733fce424a2a352c` |
| scene.ttt 大小 | 2,797,905 B |
| 父工件 | `stage3/L2_MIRROR/v008_20261029_112305` |
| 构建契约 | `contract.ok = true` |
| 历史对照 | `stage4/L4_COLUMN/v008_20261028_225634`（不是 v1 默认场景） |

迁移过程禁止修改上述文件和 `docs/scene_artifacts/stage4/current.json`。

## 4. stage4 对 stage1–stage3 的依赖清单

M1/M2 必须把下表能力的实现复制到 `src/flexible_factory/`，而不是在 v1
运行期导入旧包。

### 4.1 stage1

| 符号/模块 | 被 v4 使用处 |
|---|---|
| `stage1.config` | `stage4.config`、`coordinator`、`layout`、`run`、`runtime`、`scene`、`scene_contract`、`unit_controller` |
| `stage1.physics`（`snapshot_physics` 等） | `coordinator`、`run`、`scene`、`scene_contract` |
| `stage1.scene`（`Stage1Scene`、`alias_of`、`all_objects`、`subtree`、`_chain_joints`、`_load_model`、`adopt_scene`） | `scene`、`runtime`、`coordinator`、`run`、`scene_contract` |
| `stage1.conveyor_controller`（`BeltGeometry`、`configure_conveyor_model`、`compensation_allowed`、`TransportFault`、`ConveyorController`） | `layout`、`scene`、`unit_controller`、`run` |
| `stage1.pioneer_controller.PioneerController` | `agv_map` |
| `stage1.ur5_ik_controller.Ur5IkController` | `run` |
| `stage1.unit_controller`（`Stage1UnitController`、`UnitTaskState`、`quat_tilt`、`MismatchReason`、`PickMismatch`） | `coordinator`、`unit_controller`、`run`、`runtime` |
| `stage1.material_manager.MaterialState` | `coordinator`、`unit_controller` |
| `stage1.suction_controller` | `stage2.suction` |
| `stage1.run`（`connect`、`build_components`、`probe_conveyor_drive`、`start_stepping`、`stop_simulation`、`setup_logger`） | `stage4.run` |
| `stage1.scene_artifact` / `stage1.lifecycle` | 工件构建、保存、加载、校验、启动 |

### 4.2 stage2

| 符号/模块 | 被 v4 使用处 |
|---|---|
| `stage2.config` | `stage4.config`、`runtime`、`unit_controller`、`scene_contract` |
| `stage2.contact.contact_pose` | `unit_controller` |
| `stage2.conveyor_safety`（`ConveyorStopMonitor`、`StopStatus`、`MaterialStabilityTracker`、`bounded_transport_step`） | `unit_controller`、测试 |
| `stage2.path_planning`（`Goal`、`MotionContext`、`PlanStatus`、`ConfigurationChecker`、`LocalPlanner`） | `unit_controller`、`runtime` |
| `stage2.trajectory.joint_distance` | `unit_controller` |
| `stage2.unit_controller`（`Stage2UnitController`、`Stage2TaskState`、`_motion_params`） | `unit_controller` |
| `stage2.collisions`（`CollisionRuntime`、`CollisionError`、`CollisionChecker`） | `runtime` |
| `stage2.safety.Stage2Safety` | `runtime` |
| `stage2.shadow`（`ShadowProbe`、`build_shadow_arm`） | `runtime` |
| `stage2.suction.Stage2SuctionController` | `run` |
| `stage2.scene_contract`（摘要清洗、digest） | `scene_contract`（v1 改为独立契约，仅保留 provenance 语义） |
| `stage2.branch`、`stage2.diagnostics`、`stage2.metrics` | 被 `path_planning`/`trajectory` 间接使用 |

### 4.3 stage3

| 符号/模块 | 被 v4 使用处 |
|---|---|
| `stage3.config` | `stage4.config`、`scene_contract` |
| `stage3.layout`（`quat_from_yaw`、`yaw_from_quat`、`Stage3Layout`、`UnitLayoutView`） | `layout`、`scene`、`runtime`、`unit_controller` |
| `stage3.scene`（`Stage3Scene`、`Stage3Unit`、`adopt_stage3_scene`、`build_second_unit`、`_create_dummy`） | `scene`、lifecycle build |
| `stage3.agv_route`（`AGVRouteController`、`RouteState`、`pose_yaw`） | `agv_map`、`coordinator` |
| `stage3.runtime`（`Stage3ArmRuntime`、`Stage3Runtime`、`UnitSceneView`） | `runtime` |
| `stage3.unit_controller.Stage3UnitController` | `unit_controller`、`coordinator` |
| `stage3.resources.ResourceTable` | `stage4.resources`（基类） |
| `stage3.scene_contract`（digest、清洗） | `scene_contract`（v1 改为 provenance） |

## 5. 回归矩阵（迁移期间必须保持）

| 层级 | 现有覆盖 | v1 迁移后要求 |
|---|---|---|
| Domain | `test_stage4_materials`、`test_stage4_tasks_resources`、`test_stage4_metrics_contract` | 原样迁移到 `tests/test_v1_*`，断言不变 |
| Layout/AGV | `test_stage4_config`、`test_stage3_layout`、`test_stage3_agv_route` | 几何、节点、边、停靠容差不变 |
| 运动/安全 | `test_stage2_*`（contact/collisions/path/trajectory/safety/suction/conveyor） | 参数与判据不变 |
| 场景契约 | `test_scene_contract`、`test_stage4_config` 中的额外契约断言 | v1 manifest 校验覆盖来源 provenance，不再比较 stage2/3 当前摘要 |
| 生命周期 | `test_scene_lifecycle_*`、`test_scene_artifact` | v1 工件 load/save/validate/current 原子性 |
| 集成 | CoppeliaSim 运行时手工/脚本运行 | `full_flow` 与代表性故障；无运行器时明确记录“未执行” |

## 6. M0 结论

- 范围、运行模式、MCP 归属、工件根和 CI 策略已固定。
- 代码依赖清单、工件基线和测试基线已记录。
- 进入 M1：建立 `src/flexible_factory/` 骨架并迁移 domain。
