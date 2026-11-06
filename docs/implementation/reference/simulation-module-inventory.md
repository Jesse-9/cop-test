---
title: 仿真模块清单与命名边界
type: reference
status: review
created: 2026-11-06
updated: 2026-11-06
scope: src/flexible_factory/simulation
related:
  - docs/implementation/plans/simulation-code-optimization-plan.md
  - docs/implementation/reports/v1-simulation-code-optimization.md
---

# 仿真模块清单与命名边界

本文是
[仿真模块代码优化实施计划](../plans/simulation-code-optimization-plan.md)
第 6 节的模块盘点。它记录当前 `src/flexible_factory/simulation/` 的职责、
消费者类别和命名处置；不复制实现，也不改变任何契约。模块的权威定义仍以
代码和 [工程参考](../../../PROJECT_ENGINEERING_REFERENCE.md) 为准。

## 1. 分类口径

| 类别 | 含义 |
|---|---|
| 公开入口 | 用户运行路径或应用层稳定调用的模块/符号 |
| v1 生产调用 | UFLOW 五岛运行路径实际导入的模块 |
| 阶段基础复用 | 基础层/运动层/双站层能力，仍被 v1 继承、复用或作为兼容入口 |
| 测试专用 | 仅测试或测试辅助代码使用，生产路径不导入 |
| 未引用 | 生产与测试均无消费者；核实后删除或给出保留理由 |

一个模块可以同时被多层使用；下表按**主要职责**归类，并在备注中列出
跨层复用。

## 2. 模块清单

### 2.1 公开入口

| 模块 | 主要职责 | 备注 |
|---|---|---|
| `simulation/runtime.py` | `build_stage3_runtime()`（双站双臂）与 `build_v1_runtime()`（五岛五臂） | 应用层唯一运行时构建入口；两个 builder 和错误类型分开保留 |
| `simulation/scene.py` | v1 `Stage4Scene` / `Stage4Unit` 场景解析与契约断言 | `application/coordinator.py`、`artifacts/migration.py` 使用 |
| `simulation/layout.py` | v1 `L5_UFLOW` 布局、物流线与 `UnitView4` | `application/*`、`execution/arm.py` 使用 |
| `simulation/unit_controller.py` | `Stage3UnitController` 与 `Stage4UnitController` | UFLOW 执行适配器通过本模块使用控制器类 |
| `simulation/execution/` | 机械臂/AGV 执行适配器 | `application/coordinator.py` 使用；不调用 `sim.step()` |
| `simulation/motion/path_planning.py` | 规划模块兼容导出层 | 旧导入路径；实现已拆到 `motion/plan_*` |

应用层用户入口仍为 `flexible_factory.cli` 的 `flexible-factory` 命令与
`full_flow` 运行模式，不在本包内新增第二个入口。

### 2.2 v1 生产调用

| 模块 | 主要职责 | 备注 |
|---|---|---|
| `config.py` / `contract.py` | v1 参数、场景契约与摘要 | 配置摘要参与场景工件校验 |
| `conveyor.py` / `material_manager.py` | 输送带驱动、物料生命期 | 控制器和组件构建共用 |
| `component_builder.py` | 场景组件/IK/吸盘/AGV 构建原语 | `application/run.py` 使用；也服务基础层构建 |
| `physics.py` / `ur5_ik.py` | 物理属性快照、UR5 simIK 适配 | 只读/执行适配，不承担业务提交 |
| `agv.py` / `agv_route.py` | AGV 车体与航线控制 | 协调器独占 AGV 驱动 |
| `device_binding.py` | 设备 ID → 场景 alias/资源/区域静态绑定 | 不持有句柄；与已删除的 `device_handle_map.py` 职责不同 |
| `runtime_common.py` | 单臂影子臂/碰撞集合/规划器/锁存构建与共享容器 | Stage3/v1 共用；场景障碍策略仍在 `runtime.py` |
| `motion/plan_types.py` | 计划状态、预算、上下文与结果类型 | `path_planning` 重导出 |
| `motion/plan_checks.py` | `ConfigurationChecker` 构型/段检查 | 不改变检查序列与容差 |
| `motion/plan_search.py` | `LocalPlanner` 有限局部搜索 | 候选顺序、预算与安全检查不变 |
| `motion/plan_postprocess.py` | 成功计划的平滑/压缩 | 开关、容差与恢复语义不变 |
| `motion/branch.py` / `collisions.py` / `contact.py` | 腕部分支、碰撞查询、接触几何 | 运动层安全判据 |
| `motion/conveyor_safety.py` | 停带监视、受限补偿、物料稳定 | 控制器不得复制测量算法 |
| `motion/diagnostics.py` / `metrics.py` / `trajectory.py` | 计划诊断、指标、轨迹执行 | 证据与执行统计 |
| `motion/safety.py` / `shadow.py` / `suction.py` | 安全锁存、影子探针、吸附 | 会话资源/执行期检查 |

### 2.3 阶段基础复用

| 模块 | 来源层 | 当前复用 |
|---|---|---|
| `config_base.py` / `config_motion.py` | 基础/运动层 | v1 常量和容差来源 |
| `config_dual.py` | 双站层 | Stage3 配置与 v1 共享的会话资源前缀 |
| `layout_base.py` / `layout_dual.py` | 基础/双站层 | v1 布局派生与双站视图 |
| `scene_base.py` / `scene_dual.py` | 基础/双站层 | 场景建立原语、Stage3 场景/单元类型 |
| `unit_controller_base.py` / `unit_controller_motion.py` | 基础/运动层 | v1 控制器继承链 |
| `suction_base.py` / `pioneer.py` | 基础层 | 吸盘与差速车基础行为，仍被 v1 组件使用 |

`unit_controller_base.py`、`unit_controller_motion.py` 同时承担 v1 继承链
中的基础业务流程和运动联锁，不能仅按文件名归为“历史代码”。

### 2.4 兼容层

| 模块 | 状态 | 删除条件 |
|---|---|---|
| `unit_controller_dual.py` | 仅 re-export `Stage3UnitController`、`TrayCheck` | 全仓库与外部脚本不再导入该路径 |
| `runtime_dual.py` | 仅 re-export Stage3 builder、共享类型和旧错误名 `RuntimeBuildError` | 全仓库与外部脚本不再导入该路径 |
| `motion/path_planning.py` | 仅 re-export `motion/plan_*` 的公共符号 | 所有调用方迁移到 `plan_*` 后可删除 |

兼容层的类名、控制器 ID、错误码保持不变；删除时只移除文件，不改变
`Stage3*`/`Stage4*` 契约。

### 2.5 测试专用与未引用

- 删除前：`device_handle_map.py` 仅被 `tests/test_v1_device_handle_map.py`
  导入，`src/`、`tools/` 和应用层无消费者；无外部 API/文档承诺。
- 处置：删除 `device_handle_map.py` 及仅覆盖它的用例；同文件中的
  `scene_base.arm_joint_modes_ok` 契约测试迁至
  `tests/test_v1_base_arm_joint_modes.py`，覆盖保留。
- 删除后：`simulation/` 内没有仅测试导入的模块，也没有已知未引用模块。
  新增未引用模块应在后续盘点中重新登记，不得默认保留。

## 3. 命名决定

1. `_base` / `_motion` / `_dual` 后缀表示能力来源和继承层级，不表示废弃；
   文档使用“基础层 / 运动层 / 双站层 / UFLOW 层”描述。
2. `scene.py`、`layout.py`、`unit_controller.py`、`runtime.py` 继续作为
   v1 五岛实现名称。它们被 `application/`、`artifacts/`、`execution/`、
   测试和文档广泛引用；当前没有“名称持续误导且调用方可一次迁移”的证据，
   因此本阶段不做批量重命名。
3. 新代码使用产品职责命名（例如 `plan_types`、`plan_checks`、
   `runtime_common`），不再新增以阶段编号命名的模块。
4. 未来若重命名 v1 模块，必须同步更新 `src/`、测试、文档和 CLI/工件契约
   涉及的字符串与导入；类名、控制器 ID、manifest 字段不得随文件改名而
   改变，并按独立变更记录验证范围。
