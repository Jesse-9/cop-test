# 阶段 2.01 实施报告：基础动作与轨迹优化

日期：2026-10-21
分支：`codex/phase2-physics-motion`
上游计划：[06-10-柔性工业车间阶段2.01-动作轨迹优化](../wiki/06-工业案例/06-10-柔性工业车间阶段2.01-动作轨迹优化.md)
父工件：`stage1/L1_S1/v002_20261020_234403`
阶段二工件：`stage2/L1_S1/v012_20261021_210539`

> 阶段编号表示实施工作包，不等同于代码或工件契约版本。阶段 2.01 完成基础动作优化；
> 2026-10-25 阶段 2.02（AGV 转运专项）已从同一 stage1 父工件派生
> `stage2/L1_S1/v013_20261024_232558`（契约 `stage2_contract_20261024_v3`），
> 阶段 2.01 的基础默认行为与本文结论不变；阶段 2.02 已实现 AGV 专项实验能力，
> 候选策略未采纳且默认关闭，见
> [PHASE2_02_REPORT.md](PHASE2_02_REPORT.md)。

## 1. 结论

阶段 2.01 的基础工作已完成：在不改变场景几何、布局、目标放置位姿、物理参数和控制模式的
前提下，加入腕部分支硬约束、段内固定 q[0]、逐段关节/腕部指标、候选一步前瞻与
可行性点精简。正常 station/agv/both、运行期障碍锁存和 364 项测试均通过。

关键结果（真实执行证据，station 同一布局/任务）：

| 指标 | 优化前 `202313` | 优化后 `210605` | 变化 |
|---|---:|---:|---:|
| 任务总关节 TV (rad) | 19.897 | 11.966 | **−39.9%** |
| 任务运动时长 (s) | 27.60 | 21.10 | **−23.6%** |
| transfer TV (rad) | 15.644 | 7.601 | **−51.4%** |
| pre_place TV (rad) | 10.595 | 2.515 | **−76.3%** |
| pre_place q[0] 变化 (rad) | −2.724 | +0.071 | 消除无收益基座翻转 |
| 腕部分支 | positive→positive | positive→positive | 全程不跨支 |

优化前指阶段 2.01 中间的 first-feasible 规划（已含腕部守卫与竖直段锁轴，但尚未
加入候选前瞻、实际 TCP 承接和冻结规划起点）；两次运行使用同一 stage1 父场景
（v002，工件分别为 v010/v012），`parent_artifact.differences=[]`。

AGV 任务仍 DONE、载荷跟随误差 0.0100 m；其真实加载状态使低 TV 的 pre_place
分支在最终竖直下降时不可行，规划选择另一条完整安全链。A/B 规划探针显示 AGV 的
`production` 与无锁 `no_split_free` 计划 TV 完全相同（11.968 rad），即 2.01 的
候选排序没有改变 AGV 的计划路线，也没有使其变差。

## 2. 代码与契约

| 文件 | 变更 |
|---|---|
| `src/stage2/branch.py` (新) | q[4] 归一化角、`positive/negative/singular` 分支、距奇异边界 0/±π 裕量 |
| `src/stage2/metrics.py` (新) | 逐段 TV、最大单步、点数、q[0] 变化、锁定偏差、腕部统计、TCP 误差 |
| `src/stage2/config.py` | `WRIST_BRANCH_*`、`Q0_LOCK_*`、lookahead 权重、压缩容差；配置功能标识 `MOTION_OPT_VERSION=2.01`；工件契约版本 `stage2_contract_20261021_v2` |
| `src/stage2/path_planning.py` | 分支硬约束；`simIK` 段内关节区间锁定；候选一步前瞻排序；用上一构型实际 TCP 承接笛卡尔段；逐段复核/指标；可行性点精简 |
| `src/stage2/trajectory.py` | 执行器记录实测 q/命令，输出逐段执行指标；新增 `WRIST_BRANCH` 状态 |
| `src/stage2/unit_controller.py` | 腕部锚点与实测越界锁存；按段下发锁轴；规划优先从上一计划终点开始；`motion` 逐段证据（含失败/锁存部分） |
| `src/stage2/scene_contract.py` | `extra.stage2` 增加腕部分支、锁轴、前瞻、压缩字段 |
| `src/stage1/ur5_ik_controller.py` | `solve_candidates(lock_joint, lock_value, lock_tol)`：同步后收窄 IK 关节区间，求解后恢复 |
| `src/stage2/__init__.py` | `__version__ = 0.2.0` |

阶段一默认调用路径不变；`solve()` 未使用锁轴参数。

## 3. 测量与决策

### 3.1 腕部分支标定

探针 `docs/measurement/probe_stage2_motion_opt.py` 对 pick/lift/station/AGV 等 8 个
位姿枚举 IK 候选：

- 工具竖直时 q[4] 归一化角约为 ±π/2；同分支候选最小裕量 **1.566 rad**；
- 近奇异候选归一化后 0.147 rad，被 `WRIST_BRANCH_MARGIN=0.30` 拒绝；
- 4.7124 rad 归一化为 −π/2，按原始正数判分会误判，已由 `wrap_angle` 处理；
- 真实运行全段 `branch_start=branch_end=positive`，最小裕量 1.5708 rad。

分支锚点取规划起始实测构型；候选、路径采样、最终复核和执行步进共用同一检查入口。
跨支/近奇异候选一律拒绝，不回退到另一分支。关闭守卫的对照探针（同一最终
planner）仍选择了 positive 路线，说明本阶段的 TV 下降主要来自候选前瞻与
实际 TCP 承接；守卫的价值是提供**硬性**的跨支拒绝，而不是唯一优化来源。

### 3.2 段内固定 q[0]

- 默认锁定 `lift`、`rise`、`place`、`retreat`；`pre_place` **不锁定**：
  station 实测锁该对角段会迫使腕部跨支，AGV 实测载荷扫过 conveyor pad
  （`load_vs_workcell`），两者均按失败记录并保留自由 IK。
- 锁定通过 `simIK.setJointInterval` 临时收窄区间实现；`syncFromSim` 会重置区间，
  因此必须在同步后应用、`handleGroup` 后恢复。不改场景关节限位。IK 区间半宽
  `0.010 rad`，验收容差 `0.020 rad`。
- 正常流程 `lock_ok=true`，锁定段实测 q[0] 变化 ≤0.003 rad；无锁段仍受分支和
  碰撞约束。

### 3.3 转运末段拆分（实测未采纳为默认）

实现了 `transit → high_above_place → pre_place` 能力（`TRANSFER_SPLIT_HIGH_ABOVE_PLACE`），
并完成 A/B：

| 目标 | production（不拆分） | split（拆分） | 变化 |
|---|---:|---:|---:|
| station 计划 TV | 12.395 | 16.013 | **+29.2%** |
| agv 计划 TV | 11.968 | 13.354 | **+11.6%** |

拆分把 station transit 的 q[0] 变化推高到 3.193 rad，并新增一条 1.525 rad 的
水平段；总行程显著增加。按 06-10 §5“没有稳定收益不合并”，默认关闭拆分，
保留配置开关和测试，供未来布局/净空验证；不删除已证明必要的高位中转。

### 3.4 候选选择与轨迹点

- first-feasible DFS 会选中第一个可行链；在 station 上该链的 pre_place q[0]
  跳到 −2.724 rad。改为对每个自由目标做**一步前瞻**（只用于候选排序），并让
  后续笛卡尔段从上一构型的**实际 TCP** 承接（上一 IK 端点允许 10 mm 误差，
  直接用目标位姿会误报 7.4 mm 横向偏差）。
- 语义 waypoint（pre_pick/pick/lift/rise/transit/pre_place/place/retreat）全部保留；
  只对段内共线且 FK 偏差 ≤1 mm / 0.01 rad 的数值点做压缩。探针中 approach
  17→13 点、lift 16→12 点、transfer 30→21 点，最大 TCP 偏差 0.866 mm。
- 规划优先从上一段已验证计划终点开始（实测偏差 ≤2×`EXEC_JOINT_TOL`），
  使 A/B 和复跑使用同一冻结起点；超限时退回实测并写 `PLAN_START_ACTUAL` 事件。

## 4. 验收

### 4.1 测试

`python -m pytest tests/ -q`：**364 passed, 3 skipped**。新增
`tests/test_stage2_motion_optimization.py` 覆盖分支归一化、近奇异拒绝、
TV 不跨零折叠、锁轴偏差、锁轴回退、转运语义点、压缩共线/曲线和逐段执行指标。

### 4.2 实机

| 场景 | 结果 | 证据 |
|---|---|---|
| station | DONE，物料误差 **0.00013 m**，安全未锁存 | `phase2_01_run_station_none_20261021_210605.json` |
| agv | DONE，载荷跟随 **0.0100 m**，轮速行驶 0.505 m | `phase2_01_run_agv_none_20261021_210713.json` |
| both | 两任务 DONE，无串任务（station 0.00014 m / AGV 0.0099 m） | `phase2_01_run_both_none_20261021_210846.json` |
| 运行期障碍 | `FAILED`，`SAFETY_LATCH: COLLISION`，`link3_visible <-> Fault_Runtime_Obstacle`，停带确认 4e-5 m/s，未自动恢复 | `phase2_01_run_station_runtime_obstacle_20261021_211101.json` |
| 工件 | v012 只读校验 **144 项通过**，父差异 `[]` | `docs/scene_artifacts/stage2/L1_S1/v012_20261021_210539/` |

运行期障碍证据保留 TRANSFERRING 的失败段实测（`motion` 中 `status=FAILED`），
不再只报告已完成的 approach/lift。

## 5. 未实现 / 边界

- OMPL 未启用（N/A）；局部规划失败仍停止并报告，不回退未检查直线。
- 故障恢复、自动重试归项目最终阶段（06-05 §6）；本阶段锁存后保持最后实测构型。
- `TRANSFER_SPLIT_HIGH_ABOVE_PLACE` 默认关闭；只有重新定义高位/预接近语义并
  完成整段净空验证后才应打开。
- `pre_place` 锁轴在当前 L1_S1 布局不可用；若未来布局改变，必须重新执行
  station/AGV 锁轴可行性测试，不得直接放宽容差。
- AGV 的最终竖直下降分支对载荷实际位姿敏感；本阶段只保证任务安全完成和
  计划级 A/B 无回退，不承诺 AGV 总 TV 与 station 同幅度下降。

## 6. 证据与复现

```bash
# A/B 规划探针（影子臂，不启动仿真）
.venv/Scripts/python docs/measurement/probe_stage2_motion_opt.py
.venv/Scripts/python docs/measurement/probe_stage2_motion_opt.py --no-branch-guard

# 正常/组合/运行期障碍
.venv/Scripts/python src/run_stage2.py --target station --time 150
.venv/Scripts/python src/run_stage2.py --target agv --time 150
.venv/Scripts/python src/run_stage2.py --target both --time 180
.venv/Scripts/python src/run_stage2.py --target station \
  --fault runtime_obstacle --time 150

# 工件派生（父工件必须先加载并 validate）
.venv/Scripts/python src/run_scene_lifecycle.py load stage1/L1_S1/v002_20261020_234403
.venv/Scripts/python src/run_scene_lifecycle.py validate
.venv/Scripts/python src/run_scene_lifecycle.py save --phase stage2 \
  --parent stage1/L1_S1/v002_20261020_234403 --note "阶段2.01 ..."
```

证据副本在 `docs/implementation/evidence/phase2_01_*`；原始运行报告在
`docs/implementation/runs/`（不入库）；测量 JSON 在 `docs/measurement/`。
