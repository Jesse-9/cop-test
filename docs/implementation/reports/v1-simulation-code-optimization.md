---
title: 仿真模块代码优化实施报告 — 控制器取料与运行时装配
type: report
status: review
created: 2026-11-06
updated: 2026-11-06
scope: src/flexible_factory/simulation
---

# 仿真模块代码优化实施报告：控制器取料与运行时装配

本报告记录
[仿真模块代码优化实施计划](../plans/simulation-code-optimization-plan.md)
P1/P2 的实施结果。目标是减少控制器与运行时装配中有证据的重复实现，
不改变场景工件、配置/布局摘要、控制器 ID、碰撞与运动安全判据。

结论：P1 与 P2 已实施，新增 11 个离线用例，全量
`python -m pytest -q` 为 **542 passed / 3 skipped**。3 个跳过用例是
需要 CoppeliaSim 4.10 + ZMQ 23000 的 Stage2/Stage4 实机测试；本次环境
未启动仿真器，因此**未执行**实机验收，不能据此宣称 `full_flow` 通过。
P3 未实施。

## 1. P1：控制器输送带取料收敛

### 1.1 对照结论

| 层 | 停带确认 | 物料稳定 | 结论 |
|---|---|---|---|
| 基础层 `Stage1UnitController` | 控制器内联 pad 位姿/速度容差确认 | 内联稳定计数 + `MATERIAL_STABLE_*` | 与运动层算法不同，保留原实现 |
| 运动层 `Stage2UnitController` | `ConveyorStopMonitor` | `MaterialStabilityTracker` | 作为共享流程 |
| 双站层 `Stage3UnitController` | 仅按 `source` 转发 | 同左 | 不重复实现 |
| UFLOW 层 `Stage4UnitController` | 与运动层同算法，按本岛物流线复制 | 同左 | 删除复制，改为线级覆盖 |

基础层与运动层的差别不只在参数：基础层没有 `SpeedReading` 的
“有效/不可用”区分，也没有运动层安全锁存。按计划 §3.2 的判断门槛，
未把两层强行合并，未改动基础层停带流程。

### 1.2 改动

- `unit_controller_motion.py` 新增 `ConveyorPickContext`，只承载一次
  取料的线级差异：`conveyor_alias`、`belt_geometry`、`belt_speed`、
  `pick_region`、`sensor_alias`、日志 `label`。
- 新增窄覆盖点：
  - `_pick_context()`：运动层返回基础层单线配置，v1 返回本岛物流线；
  - `_pick_sensor_hits()`：无独立线传感器时沿用
    `scene.pick_sensor_hits()`，有传感器别名时按线过滤；
  - `_check_pick_candidate()`：运动层默认无额外校验，v1 在区域/倾角
    判定前执行 `_assert_expected_type()`。
- `Stage2UnitController` 的 `_update_conveyor()`、`_read_pick_sensor()`、
  `_command_pick_stop()`、`_advance_stopping()`、
  `_advance_material_settling()` 全部改为读取 `ConveyorPickContext`；
  停带与稳定测量仍委托 `motion/conveyor_safety.py`，未新增状态机。
- `Stage4UnitController` 删除同名重复实现，只保留：
  - `_update_conveyor()` 的 `line_fault` 短路；
  - `_conveyor_pick_alias()` 返回本岛线别名；
  - `_pick_context()` 返回本岛线参数；
  - `_check_pick_candidate()` 委托类型校验。
- `Stage1UnitController` 新增 `_conveyor_pick_alias()`，`_pad_velocity()`、
  `_pad_position()`、`_find_pad()` 改为读取该别名；基础层默认仍是
  `config_base.ALIASES["conveyor"]`，双站/五岛层的 pad 读数与各自停带
  监视保持同一条线。

### 1.3 行为差异

| 项 | 结果 |
|---|---|
| 停带容差、确认步数、超时 | 未改，仍由 `ConveyorStopMonitor` / `config_motion` 决定 |
| 物料稳定容差、步数、超时 | 未改，仍由 `MaterialStabilityTracker` / `config_motion` 决定 |
| 抓取区、物料类型、目标 UID | 未改；v1 仍用 `line.pick_region` 与 `expected_types` |
| v1 稳定窗口起点 | 从“进入 `MATERIAL_SETTLING` 的第一步”提前到“停带确认时刻”，与运动层一致；窗口只收紧约一个 `SIM_STEP`，未放宽 |
| v1 输送带故障注入 | 继承运动层 `FAULT_NO_TARGET`/`FAULT_UID_MISMATCH` 分支；v1 executor 固定传 `FAULT_NONE`，正常运行无影响 |
| 事件/日志 | v1 新增 `TRANSPORT_OVERSPEED` 事件；部分文本带物流线 ID，均为诊断文本，不进入契约字段 |
| 失败文案 | `STOP_POSE_UNREADABLE` 等个别失败原因改用运动层文案；仅失败路径可见 |

## 2. P2：Stage3/v1 运行时装配公共逻辑

### 2.1 改动

新增 `src/flexible_factory/simulation/runtime_common.py`：

- 迁入 `UnitSceneView`、`Stage3ArmRuntime`、`Stage3Runtime` 及
  `_shadow_aliases()`、`_ensure_queryable()`、`_measure_cup_radius()`；
- 新增 `build_arm_runtime()`，统一执行：停止态前置检查后的影子臂创建、
  实测构型写入、真实/影子 FK 核对、杯口半径核对、碰撞集合构建、可选的
  碰撞查询语义自检、`ConfigurationChecker`/`LocalPlanner`/`Stage2Safety`
  装配、启动摘要生成与本臂半成品清理；
- 场景差异仍通过入参传入：工位视图、影子别名、station/conveyor aliases、
  障碍句柄、障碍诊断键、IK 控制器、日志和层专属启动摘要字段。

`runtime_dual.py` 与 `runtime.py`：

- `build_stage3_runtime()` 保留双臂、另一台真实臂障碍、A/B station aliases；
- `build_v1_runtime()` 保留五岛、其余四台真实臂、按包络筛选的物流线；
- 两者都改为调用 `build_arm_runtime()`，不再各自维护同一段构建序列；
- `runtime_dual.py` 继续导出 `Stage3ArmRuntime`、`Stage3Runtime`、
  `UnitSceneView`，旧导入路径可用；
- 删除 `runtime.py` 中未使用的重复 `_shadow_aliases()`；
- `Stage3Runtime` 增加 `session_prefix` 与 `build_error` 字段，使共享容器
  不反向依赖 `runtime_dual` 的清理前缀或异常类型。类名、
  `STAGE3_CONTROLLER_ID`、`STAGE3_CONTRACT_VERSION` 未改。

### 2.2 保持不变的诊断接口

- 启动摘要字段及顺序保持：
  `fk → cup_radius_measured → cup_radius_design → other_unit(s) →
  self_pairs → collections → excluded → workcell_aliases →
  shadow_hidden → station_aliases → 层专属字段`；
- v1 的层专属字段仍为 `conveyor_aliases`，Stage3 仍为 `other_unit`；
- `Stage3ArmRuntime.as_dict()` 仍报告 `STAGE3_CONTROLLER_ID`；
  `Stage4Runtime.as_dict()` 顶层仍报告 Stage4 控制器 ID 与契约版本；
- 碰撞集合范围、`stage3`/`v1` 的障碍策略、正常销毁报告字段未改。

行为差异仅限错误与清理的层归属：`Stage4Runtime.arm(<未知工位>)` 现在抛
v1 `RuntimeBuildError`（旧实现继承 Stage3 异常类），会话资源前缀改由各层
自己的 `SESSION_RESOURCE_PREFIX` 传入（当前两值均为 `Shadow_`）。仓库内
没有捕获该异常类型的调用方，正常构建/销毁路径不受影响。

### 2.3 失败清理

`build_arm_runtime()` 在本臂构建失败时销毁已创建的碰撞集合与影子探针；
两个公开 builder 仍会清理此前已建成的臂。`_ensure_queryable()` 记录的
`collidable`/`measurable` 原值仍只在正常 `destroy()` 时恢复；构建异常
路径的属性恢复仍属 P3 未实施项，本次未扩大处理范围。

## 3. 测试与验证

| 测试 | 用例数 | 覆盖 |
|---|---|---|
| `tests/test_v1_unit_controller_conveyor_pick.py`（新增） | 5 | 运动层默认单线上下文；v1 线级上下文；只读本线传感器；忽略其他线命中；本线错料不触发停带 |
| `tests/test_v1_runtime_common.py`（新增） | 6 | 两个 builder 拒绝运行态仿真；层专属错误类型；启动摘要顺序与字段；层专属摘要追加；构建失败清理本臂；语义自检失败清理碰撞集合与探针 |
| 既有离线用例 | — | 全量 `python -m pytest -q`：**542 passed / 3 skipped** |

未执行项：

- `tests/test_v1_stage2_arm_sim.py`、`tests/test_v1_stage2_agv_sim.py`、
  `tests/test_v1_stage4_scheduler_sim.py` 的实机用例因 CoppeliaSim
  ZMQ 23000 未启动而跳过；本次未运行 `full_flow`，不提供实机证据。
- Stage3/v1 构建的启动摘要、碰撞集合范围与销毁报告需要在仿真器上复核；
  离线用例只锁定 builder 的摘要结构与清理行为。

## 4. 版本与契约处置

| 项 | 结果 |
|---|---|
| `pyproject.toml` / `PRODUCT_VERSION` | 未改 |
| `SCHEMA_VERSION`、`SCENE_CONTRACT_VERSION`、`SCENE_BUILDER_VERSION` | 未改 |
| `config_digest()` / `layout_digest()` | 未改；未触碰配置与布局摘要 |
| 控制器/协调器 ID、manifest 字段 | 未改 |
| 场景工件、`scene.ttt`、manifest | 未改 |
| `sim.step()` 所有权 | 未改，仍只有协调器推进仿真 |

## 5. 文档同步

- 计划状态改为 `partially-implemented`，补充 P1/P2 实施状态和 P3 未实施说明；
- 工程参考补充 `runtime_common.py` 的职责边界；
- `implementation/README.md` 与项目文档登记表加入本方案与报告。

## 6. 未覆盖与后续

- P3 的运行时异常路径资源恢复、`path_planning.py` 拆包和命名迁移未实施；
- 若后续需要实机结论，应在启动 CoppeliaSim 后运行受影响的 Stage2/Stage4
  实机用例，并按正式 evidence 流程记录；本报告不预设这些用例通过。
