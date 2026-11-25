---
title: 五臂固定路径回放扩展实施报告
type: report
status: review
created: 2026-11-21
updated: 2026-11-24
scope: v1 运动规划（五臂 PLACING 路径回放）
---

# 五臂固定路径回放扩展实施报告

本报告记录
[五臂固定路径回放扩展方案](../../archive-plans/simulation/v1-all-arm-path-reuse-extension.md)
的代码、CLI、路径库和真实 `full_flow` 验收。上一轮 A 臂 focused 结果见
[P0/P1 实施报告](v1-motion-planning-performance.md)；本报告不回写旧数字。

## 1. 实施内容

- `PathReusePolicy` 默认五臂 `PLACING`；`stats(unit_id)` 返回单臂统计，
  汇总包含 `by_unit`；记录查找/规划墙钟、导出数和 `last_miss_reason`。
- `PathLibrary` 增加 `scene_dir()/count_records()/explain_miss()`；记录版本
  升为 `v1_path_record_20261121`，策略版本 `v1_path_reuse_20261121`。
- `_tool_id()`/`_payload_id()` 量化到 0.1 mm，减少浮点噪声假未命中。
- `run --path-reuse {off,PLAN_ONLY,CACHE_ONLY,CACHE_THEN_PLAN}`，默认 `off`；
  `--path-library-root` 默认 `docs/path_library`。路径库根目录不得位于
  `docs/scene_artifacts/`；`CACHE_ONLY` 启动前预检场景记录。
- `run_loaded_scene_v1()/run_scenario_v1()` 把策略传入协调器，并在快照、
  运行 summary 和 evidence 中记录 `path_reuse`；协调器和控制器 summary
  均包含按臂统计。
- 路径记录位于 `docs/path_library/<scene>/PLACING_place/`，只保存静态
  waypoint/段/键字段，不保存句柄或旧 context。

## 2. 路径记录生成（v005）

命令：

```text
flexible-factory run --artifact v1/L5_UFLOW/v005_20261117_110924 \
  --scenario full_flow --path-reuse PLAN_ONLY \
  --path-library-root docs/path_library --run-records off \
  --out-dir docs/implementation/runs \
  --log docs/implementation/runs/path_reuse_plan_only.log
```

结果：`all_ok=true`，仿真 `337.05 s`、`6724` 步；`PLACING` 在线规划
9 次、全部导出，落盘 7 条唯一记录（重复动作同键覆盖）。覆盖 A–E 五臂。

| 臂 | 工序数 | 规划次数 | 导出次数 | 规划墙钟 |
|---|---:|---:|---:|---:|
| A | 2 | 2 | 2 | 8.452 s |
| B | 1 | 1 | 1 | 6.328 s |
| C | 2 | 2 | 2 | 3.233 s |
| D | 1 | 1 | 1 | 7.750 s |
| E | 3 | 3 | 3 | 15.297 s |
| **合计** | **9** | **9** | **9** | **41.060 s** |

单条 `PLACING` 在线规划墙钟范围约 `1.59–7.75 s`；A/C/E 的固定汇记录和
B/D 的 AGV 记录均已生成，未手工补造。

## 3. 缓存运行结果

用全新 CoppeliaSim 进程加载同一 v005 工件：

```text
flexible-factory run --artifact v1/L5_UFLOW/v005_20261117_110924 \
  --scenario full_flow --path-reuse CACHE_THEN_PLAN \
  --path-library-root docs/path_library --run-records off \
  --out-dir docs/implementation/runs \
  --log docs/implementation/runs/path_reuse_cache.log
```

结果：`all_ok=true`、`acceptance.ok=true`、无安全锁存、`frozen=false`、
`tasks_done=true`、`final_count=1`、`unpaired_semi_ce=1`、资源释放和
`route_ok` 均通过；仿真 `337.25 s`、`6728` 步。

### 3.1 按臂统计

| 臂 | lookup | replay | fallback | planner_calls | 生成规划墙钟 | 缓存规划墙钟 | 回退原因 |
|---|---:|---:|---:|---:|---:|---:|---|
| A | 2 | 2 | 0 | 0 | 8.452 s | 0 | — |
| B | 1 | 0 | 1 | 1 | 6.328 s | 6.578 s | `targets_digest` |
| C | 2 | 2 | 0 | 0 | 3.233 s | 0 | — |
| D | 1 | 0 | 1 | 1 | 7.750 s | 6.375 s | `start_digest,targets_digest` |
| E | 3 | 3 | 0 | 0 | 15.297 s | 0 | — |
| **合计** | **9** | **7** | **2** | **2** | **41.060 s** | **12.953 s** | — |

- 7/9 次 `PLACING` 精确命中；A/C/E 的 `planner_calls` 均为 0。
- 查找总墙钟 `0.015 s`；缓存运行剩余 `12.953 s` 规划全部来自 B/D 回退。
- B 只差 `targets_digest`，D 差 `start_digest,targets_digest`：与 AGV 实测
  托盘位姿变化一致，不是记录版本/文件损坏。
- `PLACING` 规划墙钟从 `41.060 s` 降到 `12.953 s`，节省 `28.107 s`。

### 3.2 执行与目标误差

| 臂/任务 | 来源 | 执行步数 | 位置误差 | 姿态误差 |
|---|---|---:|---:|---:|
| A `a_conveyor_uid1` | cache | 81 | 0.000845 m | 0.001376 rad |
| A `a_agv_uid2` | cache | 80 | 0.000416 m | 0.002208 rad |
| B `b_conveyor_uid2` | online 回退 | 101 | 0.000738 m | 0.001328 rad |
| C `c_handoff_uid1` | cache | 27 | 0.000251 m | 0.000596 rad |
| C `c_handoff_uid2` | cache | 27 | 0.000469 m | 0.000436 rad |
| D `d_conveyor_uid3` | online 回退 | 107 | 0.000729 m | 0.000091 rad |
| E `e_conveyor_uid1` | cache | 91 | 0.000502 m | 0.000699 rad |
| E `e_agv_uid3` | cache | 87 | 0.000790 m | 0.002330 rad |
| E `e_conveyor_uid2` | cache | 91 | 0.000497 m | 0.000817 rad |

所有 `PLACING` 均在 `EXEC_TCP_POS_TOL=0.010 m` / `EXEC_TCP_ANG_TOL=0.05 rad`
内；缓存与在线执行步数差异不超过 4 步，没有安全锁存。

### 3.3 完整任务时间

| 运行 | 墙钟 | 仿真时间 | 步数 |
|---|---:|---:|---:|
| 生成（PLAN_ONLY） | 1158 s（19m18s） | 337.05 s | 6724 |
| 缓存（CACHE_THEN_PLAN） | 1317 s（21m57s） | 337.25 s | 6728 |

缓存运行的墙钟反而更长，主要受调度、非 `PLACING` 规划和执行波动影响；
本次**不能声明 full_flow 端到端提速**。可确认的是 `PLACING` 规划阶段在
7 次命中中从约 `27.0 s` 降到约 `0.015 s`，但它在整条约 20 分钟的运行中
占比很小。

正式精简证据：
[`v1_full_flow_path_reuse_20261121.json`](../../evidence/simulation/v1_full_flow_path_reuse_20261121.json)。

### 3.4 验收结论

- 五臂 `PLACING` 均已接入；A/C/E 在本次 full_flow 中全部精确命中
  （`planner_calls=0`），验证了固定交接台/传送带/缓冲的可缓存性。
- B/D 按设计回退；`last_miss_reason` 指向 `targets_digest` /
  `start_digest`，与 AGV 实测托盘位姿变化一致，没有放宽键或目标容差。
- 两次 full_flow 的 acceptance、安全锁存、FINAL/未配对数量和资源释放
  一致；`PLACING` 执行步数与目标误差无退化。
- 不声明端到端提速；仅声明 `PLACING` 规划阶段在命中的 7 次中节省约
  `27 s` 规划墙钟。

## 4. 测试

| 测试 | 覆盖 | 结果 |
|---|---|---|
| `tests/test_v1_motion_path_reuse.py` | 五臂默认/单臂限制、按臂统计、miss 原因、工具/载荷量化、命中/回退 | 26 passed |
| `tests/test_v1_path_reuse_options.py` | CLI 解析、模式/工件 ID/场景目录校验、路径库预检 | 9 passed |
| `tests/test_v1_path_library_artifacts.py` | 逐条校验路径库记录并断言 A–E 覆盖 | 1 passed |
| 全量离线回归 | `python -m pytest -q` | 773 passed / 8 skipped |

8 个跳过项为需要 `FLEXIBLE_FACTORY_SIM=1` 的既有 focused 仿真用例；
本次两次 full_flow 为 CLI 真实运行，未把跳过项计为通过。

## 5. 限制

- 只扩展 `PLACING`；其他运动段未接入缓存。
- B/D 的 AGV 托盘位姿变化时精确键未命中，`CACHE_THEN_PLAN` 回退在线规划；
  本次不做托盘相对路径/平移/局部修复。
- 当时订单 runner/HTTP API 尚未接入路径复用配置；2026-11-24 已由
  [订单运行路径复用接入报告](../order-orchestration/v1-order-run-path-reuse.md)
  补齐运行选项，但未重新执行本报告的实机验收。
- 未做 P2 热点优化或 P3 Bézier；未改用放宽安全容差换取命中。