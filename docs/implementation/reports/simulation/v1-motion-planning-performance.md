---
title: 仿真与运动规划效率提升实施报告 — P0 计时与 P1 固定路径回放
type: report
status: review
created: 2026-11-20
updated: 2026-11-20
scope: v1 运动规划（P0 计时字段、P1 路径回放）
---

# 仿真与运动规划效率提升实施报告

本报告记录
[仿真与运动规划效率提升方案](../../plans/simulation/simulation-motion-planning-performance.md)
P0 与 P1 的代码实施结果、离线测试和 2026-11-20 的一次 focused
CoppeliaSim 对照。该对照只覆盖一个固定 A 臂 `PLACING` 段：同一
`housing_direct_A` 任务先用 `PLAN_ONLY` 在线规划并导出，再重载场景用
`CACHE_ONLY` 回放同一段；未覆盖 `APPROACHING` / `LIFTING` /
`TRANSFERRING` 段，也没有 P2/P3 结论。本报告不能作为全流程提速或完整
功能验收依据。

## 1. 结论

1. **P0 计时字段已落地。** `MotionPlan` 新增 `timings`，只记录
   `search_wall_s`、`verify_wall_s`、`postprocess_wall_s`；总墙钟仍由
   `wall_s` 表示。成功计划的 `wall_s` 现在覆盖后处理与最终复核，不再只
   包含粗筛时间。旧报告中的 `wall_s` 不能与新值直接逐项比较。
2. **P1 路径库与回放策略已落地。** 新增
   `simulation/motion/path_library.py`（记录版本
   `v1_path_record_20261120`）与 `simulation/motion/path_reuse.py`
   （策略版本 `v1_path_reuse_20261120`），实现精确键匹配、
   `PLAN_ONLY` / `CACHE_ONLY` / `CACHE_THEN_PLAN` 三种模式和
   `PR_LOOKUP` / `PR_REPLAY` / `PR_PLAN_FALLBACK` / `PR_DONE` 阶段。
3. **首版只接入一个试点段。** `Stage2UnitController._build_plan()` 仅在
   `PLACING` 的 `place` 段查询策略；策略未注入时继续走原
   `LocalPlanner.plan()`。其他动作、`LocalPlanner` 搜索行为、执行期
   `JointStreamExecutor` 的碰撞/偏差/超时/分支联锁均未改动。
4. **默认运行不变。** 策略通过函数参数传入，生产入口不传时为
   `PLAN_ONLY` 等价行为；未新增 `config*.py` 常量，未修改
   `config_digest()`、`layout_digest()`、场景工件或 `sim.step()` 所有权。
5. **离线回归通过。** 新增 23 个路径复用用例和 1 个规划计时断言；全量
   `python -m pytest -q` 为 **728 passed / 8 skipped**。8 个跳过项包括
   7 个既有 focused 仿真用例和新增的路径复用 focused 用例；未设置
   `FLEXIBLE_FACTORY_SIM=1` 时均不执行。
6. **focused 实机对照通过。** 在 CoppeliaSim 4.10.0.0、工件
   `v1/L5_UFLOW/v005_20261117_110924` 上，`housing_direct_A` 的
   `PLACING` 段先在线规划并导出，再重载场景用 `CACHE_ONLY` 回放：
   两次任务均 `DONE`，无安全锁存；回放 `planner_calls=0`、查找
   `0.015 s`，在线规划 `3.312 s`；目标误差分别为
   `0.000845 m / 0.001370 rad` 与 `0.000845 m / 0.001368 rad`，
   最终 TCP 距离 `1e-6 m`；执行均为 81 步、20 条命令、4.0 s 仿真时间。
7. **不能据此推断全流程规划是主要瓶颈。** 该次 full task 墙钟约
   `104 s`、仿真 `48.05 s`，`PLACING` 仅有 3.3 s 规划；抓取、
   抬升、转运、传送带等待和 AGV 等阶段没有做同样拆分。P2/P3 仍未开始。
8. **后续五臂扩展已另立方案/报告。** 记录/策略版本升级为 `20261121`，
   `run --path-reuse` 默认 `off`；A/C/E 固定汇、B/D AGV 回退。本文的
   `20261120` 记录和数字只对应 A 臂试点，不与五臂结果混用。

## 2. P0：基线与时计

### 2.1 现有测试基线

改动前先运行方案指定的三组运动测试：

```text
python -m pytest -q tests/test_v1_motion_path_planning.py \
  tests/test_v1_motion_trajectory.py tests/test_v1_motion_agv_transfer.py
```

结果：`29 passed in 0.33s`。这些是纯逻辑测试，只证明规划/执行器的现有
检查与失败路径未被破坏，不包含真实机械臂耗时。

### 2.2 新增计时字段

| 字段 | 含义 |
|---|---|
| `search_wall_s` | `_attempt()`：IK、候选搜索、粗筛与中转段 |
| `verify_wall_s` | `_verify_plan()` 最终分段复核；压缩/平滑回退后的复核也计入 |
| `postprocess_wall_s` | 压缩、平滑与逐段指标计算 |
| `wall_s` | 从 `plan()` 入口到返回的总墙钟（不属于 `timings`） |

`MotionPlan.as_dict()` 与 `summary_dict()` 都会输出这些字段；运行报告
无需新增逐 tick 日志即可判断搜索、复核和后处理哪一段耗时较高。

2026-11-20 的 focused `PLACING` 段实测（`housing_direct_A`，A 臂）：

| 指标 | 实测值 |
|---|---|
| 在线规划 `wall_s` | `3.312 s` |
| `search_wall_s` | `1.266 s` |
| `verify_wall_s` | `1.016 s` |
| `postprocess_wall_s` | `0.562 s` |
| 规划采样数 | `51` |
| 执行步数 / 命令数 | `81 / 20` |
| 执行仿真时长 | `4.0 s` |
| 本次 full task 墙钟 / 仿真时间 | `104.0 s / 48.05 s` |

这只能说明该固定放置段中搜索与最终复核都不可忽略；抓取和抬升/转运段
尚未做同样拆分，不能据此判断全流程主要瓶颈。

## 3. P1：路径记录与回放

### 3.1 新增模块

| 文件 | 职责 |
|---|---|
| `simulation/motion/path_library.py` | `PathKey`、`PathRecord`、`PathLibrary.get_exact()/put()`；JSON 原子写入；缺文件/损坏/版本不符/键不符返回确定结果 |
| `simulation/motion/path_reuse.py` | `PathReusePolicy` 三种模式；命中后用当前 `MotionContext` 组装 `MotionPlan`；`mark_replay_done()` 补 `PR_DONE` |

记录只保存关节 waypoint、语义段名称/点数、起终点、目标位姿、锁轴字段、
场景工件 ID、机器人/工具/载荷标识、腕部分支和运动配置版本。不保存
CoppeliaSim 句柄、`contexts` 或旧运行期对象。示例目录：
`<root>/<scene-version>/<motion-phase>/<path-id>.json`；根目录由调用方
传入，不写死到运行配置。

### 3.2 精确匹配键

`PathKey` 至少包含：

- 场景工件 ID（`scene`）；
- 动作阶段（`PLACING:place`）与工位（`A`–`E`）；
- 机器人、工具（UR5 + 实测杯口半径）和载荷（包围盒 + 期望类型）；
- 起始关节（按 `EXEC_JOINT_TOL` 量化）和目标摘要（位姿/接触/锁轴/
  `forced_q`）；
- 由 `MOTION_OPT_VERSION`、`TRANSFER_TRAJECTORY_VERSION` 及现有采样、
  碰撞、锁轴、转运、执行容差参数形成的 `motion_version`。

运动版本由标准库 SHA-256 生成，不增加配置常量。`get_exact()` 在读取后
再次比较完整键；回放前还检查起始关节距离、腕部裕量和分支锚点，任一不符
都按未命中处理。首版只回放单目标动作。

### 3.3 三种模式

| 模式 | 行为 |
|---|---|
| `PLAN_ONLY` | 始终调用现有规划器；可选导出成功记录 |
| `CACHE_ONLY` | 只回放；未命中返回 `PATH_REPLAY_MISS`，不调用规划器 |
| `CACHE_THEN_PLAN` | 先回放；未命中调用一次现有规划器，可选导出 |

导出只在计划 `ok` 且段点数与 waypoint 总数一致时发生；写盘失败只记录在
`plan.replay["export"]`，不改写仿真或计划结果。

### 3.4 控制器与执行器接入

- `Stage2UnitController` 新增可选 `path_reuse` 参数；只有
  `_path_reuse_applies()` 为真且状态为 `PLACING` 时进入策略，其余状态
  保持原调用路径。
- 命中计划由当前 `MotionContext` 重建，仍交给原
  `JointStreamExecutor`；执行期每步快速碰撞检查、偏差、超时和腕部分支
  联锁未增加第二套实现。
- `_motion_reports` 与 `summary()["path_reuse"]` 记录来源、PR 阶段、
  查找/回放/回退次数和规划器调用次数；`plan_source` 只取
  `cache` / `online`。
- `Stage4Coordinator` 与 `ArmExecutor` 新增可选 `path_reuse` 参数，
  便于 focused harness 注入；默认 `None`，CLI 和 `full_flow` 未启用。

### 3.5 focused 实机对照

测试入口：`tests/test_v1_stage2_arm_sim.py -k path_reuse`，命令：

```text
FLEXIBLE_FACTORY_SIM=1 python -m pytest -q \
  tests/test_v1_stage2_arm_sim.py -k path_reuse -s
```

结果：`1 passed in 303.84s`。两次运行均使用工件
`v1/L5_UFLOW/v005_20261117_110924`、任务 `housing_direct_A`、A 臂；
第一次 `PLAN_ONLY` 导出，第二次重载场景 `CACHE_ONLY` 回放。关键字段：

| 字段 | 第一次 `PLAN_ONLY` | 第二次 `CACHE_ONLY` |
|---|---|---|
| 任务状态 / 安全锁存 | `DONE` / 未触发 | `DONE` / 未触发 |
| `PLACING` 规划来源 | `online` | `cache` |
| 规划器调用次数 | `1` | `0` |
| 路径查找耗时 | 不适用 | `0.015 s` |
| 规划/回放 `wall_s` | `3.312 s` | `0.015 s` |
| 目标位置误差 | `0.000845 m` | `0.000845 m` |
| 目标姿态误差 | `0.001370 rad` | `0.001368 rad` |
| 执行步数 / 命令数 | `81 / 20` | `81 / 20` |
| 最终 TCP 距离 | — | `1e-6 m`（相对第一次） |

记录 ID `e63675d4372253a9`；回放阶段为 `PR_LOOKUP → PR_REPLAY →
PR_DONE`，未进入 `PR_PLAN_FALLBACK`。正式精简证据见
[`v1_motion_path_reuse_20261120.json`](../../evidence/simulation/v1_motion_path_reuse_20261120.json)。

## 4. 测试结果

| 测试 | 覆盖 | 结果 |
|---|---|---|
| `tests/test_v1_motion_path_reuse.py` | 记录读写/版本/损坏、键不匹配、起点/分支容差、三种模式、控制器接入 | 23 passed |
| `tests/test_v1_motion_path_planning.py` | 原规划测试 + 分段计时字段 | 13 passed |
| 全量离线回归 | `python -m pytest -q` | 728 passed / 8 skipped |
| focused CoppeliaSim | `PLAN_ONLY` → 重载 → `CACHE_ONLY`，同一固定 `PLACING` 段 | 1 passed / 303.84 s |

8 个跳过项包括 7 个既有 focused 仿真用例和新增路径复用 focused 用例；
未设置 `FLEXIBLE_FACTORY_SIM=1` 时不执行，不能把跳过记为通过。

## 5. 验收对照与未完成项

| 方案验收条件 | 本轮状态 |
|---|---|
| 缓存命中时 `LocalPlanner.plan()` 调用次数为 0 | 离线 fake planner 与 focused 实机均已验证 |
| 键不匹配时 `CACHE_ONLY` 明确失败 | 离线已验证，状态 `PATH_REPLAY_MISS` |
| `CACHE_THEN_PLAN` 未命中单次回退 | 离线已验证，第二次请求命中且规划器调用仍为 1 |
| 回放与在线规划到达同一目标容差 | focused 实机通过：位置误差均 `0.000845 m`，姿态误差均 ≤ `0.001370 rad` |
| 执行期联锁仍由原执行器完成 | focused 实机通过：两次均 `DONE`、无安全锁存，执行 81 步 / 20 命令 |
| 固定场景 `PLAN_ONLY` / `CACHE_ONLY` 对照（第 6 步） | 已执行，证据见上 |
| P0 固定抓取段 + 固定放置/转运段基线 | 仅采集固定 `PLACING` 段；抓取/抬升/转运段未采集 |
| P2 按计时证据选择热点 | **未开始**；现有证据不足以证明全流程规划是主要瓶颈 |
| P3 贝塞尔后处理 | 未触发、未实施 |

因此当前可声明 P0 计时字段、P1 离线闭环和一个固定 `PLACING` 段的
focused 实机回放通过；不能声明全流程提速、抓取/转运段回放或
`full_flow` 验收通过。

## 6. 相关文件

- [仿真与运动规划效率提升方案](../../plans/simulation/simulation-motion-planning-performance.md)
- [仿真模块清单](../../reference/simulation/simulation-module-inventory.md)
- [工程参考](../../../../PROJECT_ENGINEERING_REFERENCE.md)
- [路径复用测试](../../../../tests/test_v1_motion_path_reuse.py) ·
  [focused 仿真测试](../../../../tests/test_v1_stage2_arm_sim.py)
- [focused 验收证据](../../evidence/simulation/v1_motion_path_reuse_20261120.json)
- [规划器](../../../../src/flexible_factory/simulation/motion/plan_search.py) ·
  [路径库](../../../../src/flexible_factory/simulation/motion/path_library.py) ·
  [回放策略](../../../../src/flexible_factory/simulation/motion/path_reuse.py)