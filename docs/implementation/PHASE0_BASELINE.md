# Phase 0 基线记录 — 柔性工业车间重构

**实施分支**: `codex/flexible-workshop-phase1`（自 `main`@`2494d2f` 创建）
**src 基线来源**: `1388dc5`（src 最后存在的提交；`2494d2f` 将其删除）
**tests 基线来源**: `c5d9267`（tests 最后存在的提交；`1388dc5` 将其删除）
**恢复日期**: 2026-10-16

## 背景

当前 `main` 最近两个提交已删除全部控制层与测试：

| 提交 | 删除内容 |
|------|---------|
| `1388dc5` | 10 个旧单元测试文件（同时改进了 src） |
| `2494d2f` | 14 个 src 控制层文件 + USAGE.md |

```
... c5d9267 (src@v1, tests 完整)
      ↓
    1388dc5 (src@v2 最终版, tests 删除)
      ↓
    2494d2f (src 删除) = main
```

因此恢复分两段：`git restore --source=1388dc5 -- src/` 与
`git restore --source=c5d9267 -- tests/`（均为非破坏性, 不覆盖用户未提交的
`.ttt` / `CLAUDE.md` / `docs/wiki/*` 修改）。

## 基线文件清单（31 个）

**src/（14 个，自 `1388dc5`）**:
build_scene.py, factory_agv.py, factory_config.py, factory_controller.py,
factory_exceptions.py, factory_island.py, factory_mcp_server.py,
factory_physics.py, factory_robot.py, factory_scene.py, factory_types.py,
main.py, run_simulation.py

**tests/（11 个，自 `c5d9267`）**:
conftest.py, test_build_scene_arms.py, test_factory_conveyor_behavior.py,
test_factory_physics.py, test_factory_robot.py, test_mcp_control_tools.py,
test_mcp_diagnose_tools.py, test_mcp_read_tools.py, test_robot_integration.py,
test_state_codes.py

## 基线局限（重构原因）

1. **机械臂驱动方式**：`factory_robot.py` 的 `RobotArmAnimator` 用关键帧插值，
   方案 §5 要求改用 `simIK` 求解 6 关节角，禁止用关键帧伪装 IK 成功。
   （1388dc5 已含 `RobotArmIKContext` 雏形与 IK 参数，需按阶段一验证与替换。）
2. **模块边界**：基线无 `conveyor_controller.py` / `ur5_ik_controller.py` /
   `suction_controller.py` / `pioneer_controller.py` / `material_manager.py`，
   与方案 §1.1 的模块划分不符。
3. **物料生命周期**：基线用 `Material` 数据类，无方案 §2.5 的 7 状态显式状态机
   （`SPAWNED/ON_CONVEYOR/READY_FOR_PICK/HELD_BY_SUCTION/ON_TRAY/RELEASED/FAULT`）
   与 UID 唯一绑定。

## 不覆盖清单

- `3arm_simulation_test.ttt`、`factory-sim.ttt`（用户场景）
- `CLAUDE.md`、`docs/wiki/index.md`（用户文档修改）
- `docs/wiki/06-工业案例/06-04-*`、`06-05-*`（本计划规范来源）
