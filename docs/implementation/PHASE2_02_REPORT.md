# 阶段 2.02 实施报告：AGV 转运路径优化

日期：2026-10-25
分支：`codex/phase2-physics-motion`
上游计划：[06-10-柔性工业车间阶段2.01-动作轨迹优化](../wiki/06-工业案例/06-10-柔性工业车间阶段2.01-动作轨迹优化.md) §8
父工件：`stage1/L1_S1/v002_20261020_234403`
阶段二工件：`stage2/L1_S1/v013_20261024_232558`

## 1. 结论

阶段 2.02 按 06-10 §8 实现了 AGV 转运路径的**可诊断基线、路径变体、有界整链
选链、分层代价、执行剖面与平滑钩子**，并保持阶段 2.01 的生产默认行为不变：
`TRANSFER_PATH_POLICY="baseline"`、`CARTESIAN_CHAIN_SEARCH="greedy"`、
`TRAJECTORY_SMOOTHING_ENABLED=False`。新工具用于测量和后续 A/B，而不是未经
实测就替换默认路径。

2026-10-25 使用新工件 v013 和 AGV 实机停止后的 pick pose
`(-0.30784, 0.00012, 0.5)` 完成影子规划 A/B：

| 配置 | 计划 TV (rad) | TV(q[0]) | TV(q[3]) | 分层代价 J |
|---|---:|---:|---:|---:|
| baseline 0.20 m（默认） | 14.3453 | 1.5925 | 7.1164 | 41.6005 |
| baseline 0.10 m | 7.5726 | 2.1249 | 1.4566 | 15.6806 |
| baseline 0.00 m | 7.5464 | 2.0675 | 1.4566 | 15.8404 |
| high_align 0.20 m | 9.4363 | 2.1811 | 1.5954 | 19.5932 |
| high_align 0.00 m | 7.5464 | 2.0675 | 1.4566 | 15.8404 |
| detour +0.06 m @0.20 | 9.6443 | 2.2550 | 1.6046 | 19.5465 |

0.10/0.00 m 偏移把 transfer TV 降低约 47%、q[3] 降低约 79.5%，但
`rise → transit` 的 q[0] TV 从 1.510 rad 升到 2.065–2.094 rad，不满足 §8.4 B
“q[0] TV 稳定下降”的采纳条件，因此**保留 0.20 m 默认值**。beam 与 greedy 在
24 组配置中结果完全一致；detour 只增加 TV；0.10/0.00 m 的实机重复运行尚未完成。
结论是“能力已实现、候选有明确收益信号、但未达到默认切换门槛”。

## 2. 代码与契约

| 文件 | 变更 |
|---|---|
| `src/stage2/config.py` | `AGV_TRANSFER_OPT_VERSION=2.02`；路径策略/偏移/绕行、beam、分层代价、平滑、诊断参数；契约 `stage2_contract_20261024_v3` |
| `src/stage2/diagnostics.py`（新） | 逐 waypoint q、FK TCP、载荷影子位姿、腕部分支/裕量、锁定偏差、工作区/载荷净空；查询异常降级记录 |
| `src/stage2/path_planning.py` | `CARTESIAN_CHAIN_SEARCH="beam"` 有界整链候选（beam 宽度 8、每层 6 候选、节点上限 512）；`chain_cost_increment` 分层代价；`smooth_plan_waypoints` + 全段复核/回退；`MotionPlan` 新增 transfer/chain/smoothing/diagnostics 字段 |
| `src/stage2/metrics.py` | `chain_cost`、`chain_cost_increment`、`direction_reversals`、`motion_profile`（vel/acc/jerk 峰值与 RMS） |
| `src/stage2/trajectory.py` | 执行实测 profile 与采样 `trace`（超限等间隔降采样） |
| `src/stage2/unit_controller.py` | `transfer_goal_variants`（baseline/high_align/detour/auto）、`transfer_detour_pose`、变体失败记录、版本字段 |
| `src/stage2/collisions.py` | `load_distance_margin` 精确载荷净空查询（诊断用） |
| `src/stage2/scene_contract.py` | §8 参数写入 `extra.stage2`；校验 `agv_transfer_opt_version` |
| `docs/measurement/probe_stage2_agv_transfer.py`（新） | 实验卡 A–E 探针：基线、偏移、路径类型、IK 覆盖、greedy/beam A/B |
| `tests/test_stage2_agv_transfer.py`（新） | 21 项测试覆盖代价、剖面、beam、路径变体、诊断、平滑、契约 |

阶段一默认调用路径不变；阶段二默认规划/执行路径不变。

## 3. 测量与决策

### 3.1 基线复现

实机 AGV 运行 `phase2_02_run_agv_none_20261024_235155.json` 的计划 transfer TV
**14.3453 rad**、执行 TV **14.3368 rad**、执行时长 **19.65 s**；
`transit` q[0] 净变化 **−1.5101 rad**，`pre_place` q[3] 净变化
**+5.0195 rad**。与 06-10 §8.1 的 −1.511 / +5.019 / 11.15 s 一致，确认新工件
没有改变现象，且探针在相同 pick pose 下复现同一计划 TV。

### 3.2 路径变体（gate B/C）

- baseline 0.10 m：TV 7.5726（−47.2%），q[3] 1.4566（−79.5%），q[0] 2.1249
  （+33.4%），q[1] 0.629（−64.9%），q[2] 1.238（−45.1%）。
- baseline 0.00 m：TV 7.5464（−47.4%），q[3] 1.4566，q[0] 2.0675（+29.8%）。
- high_align 0.20 m：TV 9.4363（仍比 0.20 m 基线低 34.2%），但比 0.10/0.00 m
  基线高；high_align 0.00 m 与 baseline 0.00 m 几何相同、TV 相同。
- detour +0.06 m：在 0.20/0.10/0.00 m 下分别为 9.6443/8.8274/7.9930，均高于
  对应直达路径；本次没有直线路径被碰撞拒绝，按 §8.2 “只加入能解释碰撞的最少
  中间点”不引入绕行。

判定：q[0] TV 未下降，保留默认 0.20 m；若后续决定以总 TV/q[3] 为主，0.10 m
是更稳妥的候选（总 TV 与 q[0] 均略优于 0.00 m 以外的配置，且不引入绕行）。

### 3.3 整链候选（gate D/E）

37 个 transit→pre_place 采样位姿的 IK 覆盖：最少 6 个候选、最大 9 个、零覆盖
样本 0。greedy 与 beam 在主 A/B（baseline/high_align × 3 偏移 × 2 链模式）
12 组和 detour 探针 12 组中计划 TV、分层代价完全相同。说明当前瓶颈不是逐点
候选选择，而是端点/中转几何；保留 greedy 默认，beam 作为可复现实验能力保留。

### 3.4 执行平顺性（gate F）

实机 station/agv 运行的 `execution.totals.profile`：

| 段 | vel 峰值 (rad/s) | acc 峰值 (rad/s²) | jerk 峰值 (rad/s³) |
|---|---:|---:|---:|
| APPROACHING | 0.903 | 5.999 | 99.06 |
| LIFTING | 0.698 | 5.777 | **117.82** |
| TRANSFERRING (AGV) | 0.895 | 5.998 | **120.65** |
| RETREATING | 0.734 | 5.798 | 98.92 |

限值为 1.5 rad/s、6.0 rad/s²、100 rad/s³。jerk 峰值超限说明“设置关节目标
motion 参数”不等于“实测平顺”；在完成时间参数化/平滑并重新执行全段
FK/分支/载荷/碰撞复核前，不报告平顺性已解决。

## 4. 验收

### 4.1 测试

`python -m pytest tests/ -q`：**385 passed, 3 skipped**（CoppeliaSim 已启动时；
未启动时为 367 passed, 20 skipped）。新增 `tests/test_stage2_agv_transfer.py`
21 项，覆盖分层代价、执行剖面、beam 选链、路径变体、逐点诊断、平滑与契约；
原有测试全部保持通过。

### 4.2 实机

| 场景 | 结果 | 证据 |
|---|---|---|
| station | DONE，motion TV 11.9698，4/4 段执行 | `phase2_02_run_station_none_20261024_235047.json` |
| agv | DONE，transfer TV 14.3368，AGV 跟随 0.0086 m、行驶 0.5016 m | `phase2_02_run_agv_none_20261024_235155.json` |
| both | 两任务 DONE，station 11.9714 / AGV 22.5600 | `phase2_02_run_both_none_20261025_000623.json` |
| 运行期障碍 | `FAILED`，`SAFETY_LATCH: COLLISION`，`link3_visible <-> Fault_Runtime_Obstacle`，停带确认 4e-5 m/s | `phase2_02_run_station_runtime_obstacle_20261025_000901.json` |
| 工件 | v013 只读校验 **145 项通过**，父差异 `[]` | `docs/scene_artifacts/stage2/L1_S1/v013_20261024_232558/` |
| 规划 A/B | 主 A/B 12 组 + detour 12 组；同一起始 q/pick pose 三次重复逐项一致；覆盖扫描 37 点、零覆盖 0 | `phase2_02_agv_transfer_probe_20261025_000100.json`、`phase2_02_agv_transfer_repeat_*.json`、`phase2_02_agv_transfer_detour_20261025_001651.json` |

## 5. 未实现 / 边界

- 0.10/0.00 m 偏移未做 3 次实机重复运行；按 §8.4 G 不进入默认策略。
- beam 搜索已实现，但本布局无收益；不得因“能力存在”而默认打开。
- detour 只实现“高位水平绕行点”一种最小中间点；若未来垂直下降被拒绝，需要
  重新定义可解释的最小中间点，不能沿用本实现硬套。
- 平滑默认关闭；打开后必须依赖 `_verify_plan` 全段复核与回退，失败时保留原路径。
- 执行 jerk 峰值超限是实测现象，不是本阶段的修复项；时间参数化/平滑属后续
  2.03 候选工作。
- AGV 实际运行的分支仍对停止后的 pick pose 敏感；本阶段固定了实机 pick pose
  做 A/B，不承诺其他停带位姿得到同样偏移排序。

## 6. 证据与复现

```bash
# 工件派生（父工件 stage1 v002 已加载并 validate）
.venv/Scripts/python src/run_scene_lifecycle.py save --phase stage2 \
  --parent stage1/L1_S1/v002_20261020_234403 \
  --note "阶段2.02 AGV转运路径优化（实验开关默认关闭）"

# 影子规划 A/B
.venv/Scripts/python docs/measurement/probe_stage2_agv_transfer.py \
  --targets agv --pick-pose -0.30784 0.00012 0.5 \
  --policies baseline high_align --offsets 0.20 0.10 0.00 \
  --max-variants 1 --chain-modes greedy beam

# 实机回归
.venv/Scripts/python src/run_stage2.py --target station --time 150
.venv/Scripts/python src/run_stage2.py --target agv --time 150
.venv/Scripts/python src/run_stage2.py --target both --time 180
.venv/Scripts/python src/run_stage2.py --target station \
  --fault runtime_obstacle --time 150
```

证据副本在 `docs/implementation/evidence/phase2_02_*`；原始运行报告在
`docs/implementation/runs/`（不入库）；测量 JSON 在 `docs/measurement/`。
