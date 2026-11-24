---
title: 五臂固定路径回放扩展方案
type: plan
tags:
  - 类别/实施计划
  - 类别/运动规划
created: 2026-11-21
updated: 2026-11-24
status: implemented
scope: v1 运动规划 P1 扩展（五臂 PLACING）
---

# 五臂固定路径回放扩展方案

## 1. 目标与范围

把已验证的固定动作关节路径回放从 A 臂 `PLACING` 试点扩展到 A–E 五台
机械臂，并提供运行级开关、按臂命中统计、可版本化的路径库和真实
`full_flow` 证据。

本方案只扩展 `PLACING` 段。`APPROACHING`、`LIFTING`、`TRANSFERRING`、
`RETREATING` 没有逐段验收，不纳入本次。路径回放仍只跳过重复 IK/候选
搜索；执行期 `JointStreamExecutor` 的逐步碰撞、偏差、超时和腕部分支
联锁保持不变。

## 2. 已确认决策

1. **五臂全部启用 `CACHE_THEN_PLAN` + 自动回退**。A/C/E 的汇是固定
   交接台/传送带/缓冲，预期精确命中；B/D 的汇是 AGV 实测托盘，托盘位姿
   变化时会未命中并回退在线规划。本次不通过放宽键、目标容差或路径平移
   来强行命中。
2. **生成的全臂路径记录提交到 `docs/path_library/`**。记录按场景/阶段/
   路径键分目录，独立于 `.ttt` 场景工件；key 或版本不匹配时自然失效。

## 3. 五臂适用性

| 臂 | 主要 `PLACING` 工序 | 汇 | 预期 |
|---|---|---|---|
| A | `housing_direct_A` / `housing_alt_A` | `handoff_ac` 固定台 | 精确缓存 |
| B | `housing_alt_B` | AGV `dock_B` 托盘 | 托盘位姿变化时回退 |
| C | `housing_finish` | `line_ce` 固定放置点 | 精确缓存 |
| D | `bracket_process` | AGV `dock_D` 托盘 | 托盘位姿变化时回退 |
| E | `housing_to_E_buffer` / `bracket_to_E` | `buffer_e_c` / `buffer_e_d` | 精确缓存 |

AGV 汇的目标由 `agv_place_pose()` 按实测托盘位姿生成；精确键包含目标
摘要，所以 B/D 的命中依赖托盘位姿重复性。若需要稳定命中，应另立“托盘
相对路径/重新复核”方案，不在本次放宽匹配。

## 4. 代码改动

### 4.1 策略与路径库

- `simulation/motion/path_reuse.py`
  - 默认 `enabled_units=None`（五臂），`enabled_phases=("PLACING",)`。
  - 版本 `v1_path_reuse_20261121`。
  - 按臂统计 `lookup/replay/fallback/planner_calls/export_saved`、
    `lookup_wall_s/planner_wall_s` 和 `last_miss_reason`；
    `stats(unit_id)` 返回单臂，`stats()` 返回汇总 + `by_unit`。
  - 未命中调用 `PathLibrary.explain_miss()`，给出 key 差异类别。
- `simulation/motion/path_library.py`
  - 记录版本 `v1_path_record_20261121`。
  - 增加 `scene_dir()`、`count_records()`、`explain_miss()`；只读诊断，
    不改变精确匹配。
- `simulation/unit_controller_motion.py`
  - 工具杯口半径量化到 0.1 mm；载荷包围盒量化到 0.1 mm，避免浮点噪声
    造成假未命中；`unit` 仍区分各臂。
  - 不改规划器搜索、检查器、执行器或安全容差。

### 4.2 运行入口与 CLI

- `spec.PATH_LIBRARY_ROOT = docs/path_library`。
- `application/run.py` 新增 `build_path_reuse_policy()`：
  - `off` 返回 `None`；
  - 非 `off` 要求已加载工件 ID；
  - 拒绝路径库根目录位于 `docs/scene_artifacts/`；
  - `CACHE_ONLY` 预检场景目录至少有一条记录；
  - `PLAN_ONLY` 自动导出；`enabled_units=None`。
- `run_loaded_scene_v1()` / `run_scenario_v1()` 增加路径复用参数，
  在快照、运行 summary 和 evidence 中记录 `path_reuse`（模式、根目录、
  记录数、按臂计数/耗时）。
- `Stage4Coordinator.summary()/acceptance()` 增加 `path_reuse` 汇总，
  回退不计为失败。
- `cli.py run` 新增：

```text
--path-reuse {off,PLAN_ONLY,CACHE_ONLY,CACHE_THEN_PLAN}   # 默认 off
--path-library-root docs/path_library
```

默认 `off` 不改变现有生产行为；日常使用 `CACHE_THEN_PLAN`。
`CACHE_ONLY` 是严格模式，五臂全启时 B/D 可能按设计未命中并失败。

## 5. 路径库与生成流程

记录目录：

```text
docs/path_library/<scene-slug>/PLACING_place/<path-id>.json
```

生成（v005，五臂，在线规划并导出）：

```text
flexible-factory run --artifact v1/L5_UFLOW/v005_20261117_110924 \
  --scenario full_flow --path-reuse PLAN_ONLY \
  --path-library-root docs/path_library --run-records off \
  --out-dir docs/implementation/runs \
  --log docs/implementation/runs/path_reuse_plan_only.log
```

生成后检查：

- 覆盖 A–E 五臂；
- 每条记录版本、段点数、起终点、目标自洽；
- 若某个变体未出现，用对应 scenario/focused harness 补齐，不伪造记录；
- 记录和 `docs/path_library/README.md` 位于 `docs/path_library`；提交本次
  变更时一并纳入。

## 6. 真实缓存验收

用全新 CoppeliaSim 进程加载同一 v005 工件：

```text
flexible-factory run --artifact v1/L5_UFLOW/v005_20261117_110924 \
  --scenario full_flow --path-reuse CACHE_THEN_PLAN \
  --path-library-root docs/path_library --run-records off \
  --out-dir docs/implementation/runs \
  --log docs/implementation/runs/path_reuse_cache.log
```

验收：

- `all_ok=true`、无安全锁存、任务/航线/资源验收不变；
- A/C/E：`lookup > 0`、`replay == lookup`、`fallback == 0`、
  `planner_calls == 0`；
- B/D：`lookup == replay + fallback`；回退时记录 `last_miss_reason`，
  预期为 start/target 差异；
- 报告每臂 `lookup_wall_s` vs `planner_wall_s`、full_flow 墙钟/仿真时间；
- 执行步数/命令数不因回放改变。

若 A/C/E 出现回退，先定位 start/target 差异并修复记录/稳定性；不得通过
放宽 `EXEC_JOINT_TOL`、目标容差或碰撞阈值强行命中。

## 7. 测试

- 更新 `tests/test_v1_motion_path_reuse.py`：五臂默认、单臂限制、按臂统计、
  `lookup == replay + fallback`、miss 原因、工具/载荷量化。
- 新增 `tests/test_v1_path_reuse_options.py`：模式/工件 ID/场景目录校验、
  `PLAN_ONLY`/`CACHE_ONLY`/`CACHE_THEN_PLAN` 预检、CLI 解析。
- 新增 `tests/test_v1_path_library_artifacts.py`：逐条加载 `docs/path_library`
  记录并断言 A–E 覆盖。
- 保留现有 A 臂 focused 仿真测试；全臂真实验收由两次 CLI `full_flow`
  承担。

## 8. 不纳入本次

- B/D 的 AGV 托盘相对路径变换/局部修复；
- `APPROACHING`/`LIFTING`/`TRANSFERRING`/`RETREATING` 段缓存；
- 订单 runner/HTTP API 的路径复用配置（已由
  [订单运行路径复用方案](../order-orchestration/v1-order-run-path-reuse.md)
  另行接入，本文不重复）；
- P2 热点优化、P3 Bézier、动态/故障回放。

## 9. 回滚

`--path-reuse off` 即恢复原在线规划；删除 `docs/path_library` 记录不改变
场景工件、`config_digest()`/`layout_digest()`、产品版本或执行器联锁。

## 10. 实施结果（2026-11-21）

- v005 `PLAN_ONLY` full_flow：`all_ok=true`，9 次 `PLACING` 在线规划/7 条
  唯一记录，覆盖 A–E。
- v005 `CACHE_THEN_PLAN` full_flow：`all_ok=true`，9 次查找/7 次回放/
  2 次回退；A/C/E `planner_calls=0`，B/D 因 AGV 托盘 `targets_digest`
  （D 还含 `start_digest`）回退。
- `PLACING` 规划墙钟 `41.060 s → 12.953 s`；full_flow 墙钟未下降，
  不能宣称端到端提速。两次 acceptance/安全锁存一致。
- 详细数据、测试和限制见
  [实施报告](../../reports/simulation/v1-all-arm-path-reuse.md) 与
  [full_flow 证据](../../evidence/simulation/v1_full_flow_path_reuse_20261121.json)。