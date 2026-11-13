---
title: v1 启动性能优化实施报告 — 校验、影子臂构建与启动计时
type: report
status: review
created: 2026-11-12
updated: 2026-11-12
scope: v1 启动路径（lifecycle 校验、simulation runtime）
---

# v1 启动性能优化实施报告

本报告记录
[启动性能优化方案](../../plans/simulation/v1-startup-performance-optimization.md)
P1/P2 的代码实施结果、pytest 回归、2026-11-12 的三次启动计时和后续步骤。
本轮已完成纯逻辑回归和启动计时；**未运行场景、未完成 `full_flow` 验收**，
因此不能把本报告当作功能验收或性能收益的最终结论。

## 1. 结论

1. **P1 校验路径去重已落地。** 正常加载路径的 `prepare_start` 从
   2026-11-09 基准约 25 s 降到 **12.1–12.2 s**（约 −51%）。
2. **P2 影子臂/碰撞集合优化已落地。** 五台运行时从基准约 47.9 s 降到
   **20.3–21.4 s**（约 −56%），B 臂从 11.8 s 降到 4.5–4.8 s。
3. **加载完成 → 仿真启动**从基准约 74.65 s 降到 **33.7–35.4 s**
   （约 −53% 至 −55%）。
4. **`--no-load` 不是性能优化。** 它跳过 `manager.load()`，但
   `prepare_start()` 内的 `_ensure_scene()` 必须执行 `adopt_stage4_scene()`，
   把正常 `artifact_load` 中的“句柄解析”工作搬进了
   `prepare_start_validation`；第 3 次 73.96 s 不能与前两次的 12.1–12.2 s
   直接比较。
5. **主要剩余瓶颈**是 `artifact_load` 中的 `adopt_stage4_scene()`（大量同步
   ZMQ 枚举）和清理阶段的 `runtime_destroy`（36.7–43.7 s）。前者需要子计时
   才能精确拆分，后者可直接作为下一轮优化目标。
6. **纯逻辑回归已通过：615 passed / 7 skipped。** 7 个跳过项为需要
   `FLEXIBLE_FACTORY_SIM=1` 且本机运行 CoppeliaSim 4.10 的 focused 测试；
   `full_flow` 验收仍未执行。
7. **后续优化代码已落地：** `adopt_stage4_scene` 的 alias 索引缓存复用、
   `ShadowProbe.destroy` 影子根快速删除、计时工具 `profile.*` 子阶段；
   P3 订单子集臂的 builder/runner 代码已实现。子集订单实机验收仍待执行。
8. **全量验收：** 干净 `full_flow` 运行 `all_ok=true`（1 个 FINAL、
   6860 步、sim 343.85 s、0 failure）。该次运行使用了修复前的快速销毁
   路径，结束时留下 15 个 `Shadow_*` 别名；修复后由启动计时复测确认
   `leftover=[]`、清理 4.01 s。首次 `full_flow` 尝试在 E 岛
   `robot_vs_parts` 处中止，且同一时段存在残留进程重新 `load` 场景，
   该次未作为通过证据。
9. **P3 子集订单：** `order-subset-4arm` 重试通过：`active_unit_ids=A/C/D/E`
   （B 未构建），安全锁存仅包含 A/C/D/E，1 个 FINAL（父 UID `[1,2]`），
   sim 163.4 s、3268 步、0 failure、清理 `leftover=[]`。首次尝试在 E 岛
   `bracket_to_E` 释放校正超限（pos 0.0063 m / ang 0.5486 rad）中止；
   重试通过，说明该释放失败是间歇性运动问题，不是子集构建错误。
10. **计时口径修正：** 旧版 `measured_stages_total` 把 `profile.*` 与主阶段
    重复相加，打印出 130–210 s 的假总时间；真实 `startup_total_wall` 为
    79.97–88.61 s（2026-11-13 21:31–21:53 四次复测）。工具已改为
    `non_overlapping_stages_total`，同时排除 `build.*` 和 `profile.*`。

## 2. 测量方法与证据

工具：[tools/measure_startup_stages.py](../../../../tools/measure_startup_stages.py)；
方法说明见[使用指南](../../../wiki/98-ProjectUsage/USAGE.md)的“启动阶段计时”。

执行命令：

```powershell
uv run python tools/measure_startup_stages.py --run-records off
uv run python tools/measure_startup_stages.py --run-records off --no-load
```

- 三次运行均使用 `v1/L5_UFLOW/v001_20261103_004317`，批次 `default`；
- 运行 1、2 正常 `load`，运行 3 使用 `--no-load`；
- 三次都在 `start_stepping()` 返回后截断，未执行场景循环；
- 清理阶段（停仿真、销毁影子资源、结束生命周期）单独计时。

正式证据（由 `docs/implementation/runs/` 复制，纳入版本控制）：

- [运行 1，load](../../evidence/simulation/v1_startup_timing_load_20261112_082956.json)
- [运行 2，load](../../evidence/simulation/v1_startup_timing_load_20261112_083249.json)
- [运行 3，no-load](../../evidence/simulation/v1_startup_timing_noload_20261112_083535.json)
- [2026-11-13 alias/销毁优化后复测](../../evidence/simulation/v1_startup_timing_after_alias_destroy_20261113_001019.json)
- [2026-11-13 21:33 load 复测](../../evidence/simulation/v1_startup_timing_load_20261113_213332.json)
- [2026-11-13 21:53 no-load 复测](../../evidence/simulation/v1_startup_timing_noload_20261113_215332.json)
- [2026-11-12 干净 full_flow 功能验收摘要](../../evidence/simulation/v1_full_flow_startup_validation_20261112_234224.json)
- [2026-11-13 4 臂子集订单验收](../../evidence/simulation/v1_order_subset_4arm_20261113_004150.json)

环境：本机 Windows；CoppeliaSim 4.10 + ZMQ Remote API `localhost:23000`；
`--run-records off`；Python 环境由 `uv` 管理。本报告未记录 CoppeliaSim
完整版本字符串，后续正式验收应在证据中补充。

## 3. 测量结果

### 3.1 分阶段耗时

| 阶段 | 运行 1（load） | 运行 2（load） | 运行 3（no-load） |
|---|---:|---:|---:|
| `process_import` | 4.48 s | 2.50 s | 2.46 s |
| `zmq_connect` | 0.40 s | 0.33 s | 0.26 s |
| `artifact_load` | 57.14 s | 53.78 s | —（未执行） |
| `prepare_start_validation` | 12.13 s | 12.24 s | **73.96 s** |
| `build.shadow_runtime` | 21.10 s | 20.32 s | 21.25 s |
| `build_components` | 21.37 s | 20.57 s | 21.41 s |
| `snapshot_write` | 0.60 s | 0.61 s | 0.59 s |
| `simulation_start` | 0.37 s | 0.22 s | 0.22 s |
| **`startup_total_wall`** | **96.48 s** | **90.27 s** | **98.91 s** |
| 清理 `runtime_destroy` | 36.73 s | 37.27 s | 43.68 s |

### 3.2 五台影子臂/碰撞集合

| 工位 | 2026-11-09 基准 | 运行 1 | 运行 2 | 运行 3 |
|---|---:|---:|---:|---:|
| A | 9.03 s | 6.24 s | 5.30 s | 5.45 s |
| B | 11.76 s | 4.73 s | 4.48 s | 4.83 s |
| C | 8.80 s | 3.30 s | 3.63 s | 3.84 s |
| D | 9.34 s | 3.59 s | 3.78 s | 3.80 s |
| E | 8.96 s | 3.23 s | 3.13 s | 3.33 s |
| **合计** | **47.89 s** | **21.10 s** | **20.32 s** | **21.25 s** |

A 臂仍包含首次 `query_semantics_probe` 和缓存冷启动，因此略慢于后续臂；
B 臂的 workcell 为 146 个对象，其余为 96 个。

### 3.3 与 2026-11-09 基准比较

| 指标 | 2026-11-09 | 2026-11-12 | 变化 |
|---|---:|---:|---:|
| 加载完成 → `prepare_start` 完成 | ≈ 25 s | 12.1–12.2 s | ≈ −51% |
| 五台影子臂/碰撞集合 | ≈ 47.9 s | 20.3–21.4 s | ≈ −56% |
| 加载完成 → 仿真启动 | ≈ 74.65 s | 33.7–35.4 s | ≈ −53% 至 −55% |

2026-11-09 数据来自历史日志（秒级时间戳），只作量级对照；本轮数据来自
P0 计时工具，秒表精度更高。

### 3.4 `--no-load` 的归属变化

运行 3 没有 `artifact_load` 条目。`prepare_start()` 在场景尚未由本进程
adopt 时调用 `_ensure_scene()`，后者执行 `adopt_stage4_scene()`，即正常
`manager.load()` 中的“句柄解析”部分；`sim.loadScene`、manifest schema 和
scene.ttt 的 SHA-256 没有执行。

因此：

- **不是脚本把 `artifact_load` 计时器直接加到 `prepare_start_validation`；**
- 而是 `artifact_load` 中的 adopt/句柄解析工作换了归属；
- 运行 1/2 的 `artifact_load + prepare_start_validation` 合计为 69.27 s /
  66.02 s，运行 3 的 `prepare_start_validation` 为 73.96 s，量级接近，
  说明 adopt 是 `artifact_load` 的主要部分，且 `--no-load` 没有省掉它；
- 运行 3 的 `prepare_start` 还包含 `build_validation_context` 和
  `scene.verify()` 内部的全场景枚举，所以不能把 73.96 s 与 12.1 s 直接
  比较。

精确拆分已由 2026-11-13 的 `profile.*` 子阶段补充；见 §3.5。

### 3.5 2026-11-13 复测（alias 缓存 + 销毁优化）

在 alias 索引缓存复用和 `ShadowProbe.destroy` 快速删除落地后，重新跑一次
`load` 启动计时：

| 阶段/子阶段 | 耗时 | 说明 |
|---|---:|---|
| `artifact_load` | 54.33 s | 含 manifest/哈希、`loadScene`、adopt |
| `profile.adopt_stage4_scene` | 49.72 s | 句柄解析，占 artifact_load 主要部分 |
| `prepare_start_validation` | **9.63 s** | 从 12.1–12.2 s 继续下降约 21% |
| `profile.build_validation_context` | 6.89 s | 复用 adopt 的 alias 索引 |
| `profile.validate_artifact` | 2.47 s | 文件/环境/场景对象校验 |
| `profile.validate_contract` | 0.13 s | 契约校验 |
| `build.shadow_runtime` | 20.75 s | 五台合计，保持 P2 水平 |
| `cleanup.runtime_destroy` | **4.01 s** | 从 36.7–43.7 s 下降；`leftover=[]`，恢复 80 项 |
| `startup_total_wall` | 89.01 s | 进程开始 → 仿真启动 |

证据：[v1_startup_timing_after_alias_destroy_20261113_001019.json](../../evidence/simulation/v1_startup_timing_after_alias_destroy_20261113_001019.json)。
该 JSON 同时记录 `simulator.product_version=4.10.0.0`、引擎 `bullet` 和
`config_digest`。`artifact_load - adopt ≈ 4.6 s`，包含 `loadScene`、manifest
校验、SHA-256 和状态写入，不能全部归因于 `loadScene`。

### 3.6 计时口径修正：`profile.*` 不能与主阶段相加

`profile.adopt_stage4_scene` 包含在 `artifact_load` 内，
`profile.build_validation_context` / `profile.validate_artifact` /
`profile.validate_contract` 包含在 `prepare_start_validation` 内。旧版工具
打印的 `measured_stages_total` 只排除了 `build.*`，没有排除 `profile.*`，
因此把同一段工作重复相加，出现 130–210 s 的假总时间。真实总时间只看
`startup_total_wall`。

| 运行 | 真实 `startup_total_wall` | 旧口径合计（重复计数） | 修正后非重叠合计 |
|---|---:|---:|---:|
| 21:31 load | 83.26 s | 137.85 s | 83.25 s |
| 21:33 load | 79.97 s | 133.20 s | 79.96 s |
| 21:50 load | 79.99 s | 133.91 s | 79.99 s |
| 21:53 no-load | 88.61 s | **210.26 s** | 88.60 s |

工具已修正：`non_overlapping_stages_total` 同时排除 `build.*` 和
`profile.*`，并在表头说明“子项与主阶段重叠、百分比不可相加；以
`startup_total_wall` 为准”。

补充证据：
[21:33 load](../../evidence/simulation/v1_startup_timing_load_20261113_213332.json)、
[21:53 no-load](../../evidence/simulation/v1_startup_timing_noload_20261113_215332.json)。

## 4. `artifact_load` 为什么长

`manager.load()` 的顺序：

1. manifest schema 校验；
2. 对 2.7 MB 的 `scene.ttt` 计算 SHA-256；
3. `sim.loadScene(scene.ttt)`；
4. `adopt_stage4_scene()`；
5. 生命周期事件/会话写入。

第 1、2 步是本地操作，2.7 MB 文件读取和哈希为毫秒级；第 3 步是一次阻塞
RPC，可能数秒；主要成本在第 4 步的同步 ZMQ 往返：

- `alias_index(sim)`：`all_objects()` 按对象类型逐个 `getObjects(i, type)`
  枚举，再对每个对象调用 `getObjectAlias`；
- `_resolve_unit4()` ×5：`subtree()`、`getObjectType`/`getObjectAlias`
  逐对象扫描、关节链、吸盘传感器和目标解析；
- `_adopt_line_meta()` ×4：`_measure_top_run()` 遍历每条线的约 50 个 pad，
  并对每个 pad 读取位置和包围盒，另有脚本发现和 `readCustomTableData`；
- AGV 子树发现。

场景包含约 200 个传送带 pad、5 台 UR5 和其他对象，单次全场景枚举就是
上千次 RPC；每次同步往返累计到几十秒。`--no-load` 跳过 `loadScene` 后
`prepare_start` 仍达到 74 s，进一步说明瓶颈主要不是文件加载，而是句柄解析
和全场景枚举。

## 5. 代码改动

### 5.1 P1 校验路径去重

- `simulation/scene_base.py`：新增 `ValidationContext` 和
  `build_validation_context()`，一次建立对象列表、alias 索引和
  `scene.verify()` 结果；`scene.verify()` 异常记录为 `scene_error`。
- `artifacts/store.py`：`validate_artifact()` /
  `validate_scene_against_manifest()` 接受并复用 context；删除重复对象枚举
  和重复 `getObjectType` 调用。
- `simulation/contract.py`：`validate_contract()` 复用 alias 索引和 scene
  报告，删除 `_resolve_aliases()`。
- `application/lifecycle.py`、`artifacts/migration.py`：`prepare_start`、
  `validate`、保存后重载校验和迁移校验均只建立一次 context。

### 5.2 P2 运行时构建优化

- `simulation/motion/collisions.py`：删除未被读取的 `SceneGeometry.inventory`
  和逐对象 `describe_object` 热路径调用；新增带成员校验/逐项回退的
  `_add_items_to_collection()`；`discover_workcell()` 复用已发现的 pads。
- `simulation/runtime_common.py`：新增 `DiscoveryCache`，在一次停止态构建内
  复用传送带 pads、真实臂连杆/吸盘和查询属性原值。
- `simulation/runtime.py`：Stage3/v1 builder 共享 `DiscoveryCache`，
  `query_restore` 去重。
- `simulation/motion/shadow.py`：`verify_fk()` 一次建立真实臂 alias 索引。

### 5.3 测试

已补充并执行纯逻辑测试：`uv run pytest -q` 为 **615 passed / 7 skipped**。
7 个跳过项为需要 `FLEXIBLE_FACTORY_SIM=1` 且本机运行 CoppeliaSim 4.10
（ZMQ 23000）的 focused 测试。新增用例覆盖：

- `tests/test_v1_contract_validation.py`：context 复用、枚举次数、
  scene 异常记录；
- `tests/test_v1_motion_collisions.py`：批量集合加入、抛错回退、静默批量修复、
  无批量 API 回退；
- `tests/test_v1_runtime_common.py`：发现缓存复用、查询属性只应用一次、
  FK 索引只建一次。

### 5.4 后续优化与 P3 代码

- `scene.py` / `scene_base.py`：`adopt_stage4_scene` 缓存停止态
  `_validation_objects` / `_validation_alias_index`，`build_validation_context`
  复用；`LifecycleManager.finish_run` 清除缓存，避免运行后使用陈旧索引。
- `motion/shadow.py`：`ShadowProbe.destroy()` 正常路径直接删除影子臂根
  （吸盘/TCP/载荷是其子对象），不再执行多轮全场景枚举；失败才回退按别名清理。
- `runtime.py` / `application/run.py`：新增 `normalize_unit_ids()` 和
  `unit_ids` 参数；`build_components_v1` 只为指定工位构建 IK/吸盘/运行时，
  快照和报告记录 `active_unit_ids`。
- `application/order_runner.py`：`required_arm_units()` 取订单实例
  `candidate_device_ids ∪ selected_device_id` 的臂工位并集；订单运行按该
  子集构建，并记录 `active_unit_ids` 与 `runtime_destroy`。
- `tools/measure_startup_stages.py`：新增 `profile.adopt_stage4_scene`、
  `profile.ensure_scene`、`profile.build_validation_context`、
  `profile.validate_artifact`、`profile.validate_contract` 子阶段。
- 新增 `tests/test_v1_order_runner_units.py`；当前纯逻辑回归
  **615 passed / 7 skipped**。

P3 4 臂子集订单已通过（见 §5.5）；3 臂场景由同一 `unit_ids` 路径覆盖，
未单独运行。

### 5.5 全量验收与 P3 子集订单

- **首次 `full_flow` 尝试**（23:17:59 启动）在 sim 283.05 的 E 岛
  `bracket_to_E` 处 `GOAL_CONFIG_INVALID: robot_vs_parts (682,705)` 中止；
  同一时段有一个残留启动进程在 23:18:48 重新 `load` 场景。该次不作为
  通过证据。
- **干净重跑**（23:42:15–00:08:15）：`all_ok=true`，1 个 `FINAL`，
  6860 步，sim 343.85 s，0 failure，`active_unit_ids=A–E`。功能验收通过。
  该次运行使用修复前的快速销毁路径，结束时留下 15 个 `Shadow_*` 别名；
  修复 `ShadowProbe.destroy` 后，启动计时复测得到 `leftover=[]`、
  `runtime_destroy=4.01 s`。
- **P3 子集订单**：`order-subset-4arm` 首次尝试在 E 岛 `bracket_to_E`
  释放校正超限中止（pos 0.0063 m / ang 0.5486 rad）；重试通过：
  `active_unit_ids=A/C/D/E`（B 未构建），安全锁存仅 A/C/D/E，1 个 FINAL
  （父 UID `[1,2]`），sim 163.4 s、3268 步、0 failure、清理
  `leftover=[]`。证据：
  [v1_order_subset_4arm_20261113_004150.json](../../evidence/simulation/v1_order_subset_4arm_20261113_004150.json)。

## 6. 验证缺口与风险

本报告**不声明**以下事项已完成：

- `full_flow` 功能验收已通过（`all_ok=true`），但该次运行使用修复前的
  快速销毁路径并留下 15 个 `Shadow_*`；修复后仅通过启动计时复测确认
  `leftover=[]`，**没有重新跑完整 `full_flow`**；
- P3 4 臂子集订单重试通过，但首次尝试在 E 岛释放校正处失败；这是间歇性
  运动问题，不能把单次通过解释为所有子集订单稳定；3 臂场景未单独运行，
  仅由同一 `unit_ids` 参数路径覆盖；
- alias 索引缓存已把 `prepare_start` 降到 9.63 s；`ShadowProbe.destroy`
  快速路径已把清理降到 4.01 s，两者均有本次计时证据；
- `sim.addItemsToCollection` 的 4.10 实机语义未单独确认；实现有成员校验和
  逐项回退，但正式验收仍需实机覆盖；
- 历史与今日校验报告的 `name` 检查项均为 454 项，`differences` 为 0；
  此前按 `"ok"` 字段计数的 456 包含非检查字段，不能作为检查数变化。
  正式验收仍应逐项 diff 检查名称和对象覆盖；
- `query_properties[*].before` 现在记录首次应用前的原值（后续臂复用同一
  证据），字段结构未变，但值可能与旧快照不同；
- 精确的 `loadScene` vs `adopt_stage4_scene`、`_ensure_scene` vs 校验耗时
  仍未拆分；
- 历史基准与今日测量不在同一连续会话，未做同机 A/B 交替；12.1–12.2 s
  和 20.3–21.4 s 的下降与 P1/P2 改动一致，但不能单独归因于代码；
- 只在单机、单场景、3 次运行上测量；未覆盖模型/场景切换和不同机器。

## 7. 后续步骤

纯逻辑回归已完成（615 passed / 7 skipped）；以下为剩余步骤：

| 优先级 | 步骤 | 预期收益 | 验收 |
|---|---|---|---|
| 高（必须） | 跑 `full_flow` 验收 | 确认集合成员、FINAL/UID/AGV 路线、启动摘要不变 | 已通过 `all_ok`；但清理残留需在修复后复测（见 §5.5） |
| 中 | 给计时工具增加子阶段打点：`loadScene` vs `adopt_stage4_scene`；`_ensure_scene`、`build_validation_context`、`validate_artifact`、`validate_contract`；并记录 CoppeliaSim 版本/引擎、artifact digest | 确认 74 s 的准确构成，补齐可复现环境信息 | 三次运行 JSON 含子阶段和环境字段；不改变产品代码 |
| 中 | 复用 `adopt_stage4_scene` 已建立的 alias 索引给 `ValidationContext`；评估把索引传入 `scene.verify()` | 去掉 1–2 次全场景枚举 | 已实现索引缓存复用；`prepare_start` 9.63 s |
| 中 | 优化 `runtime_destroy`：批量移除影子根、批量销毁 collections、合并查询属性恢复 | 当前 36.7–43.7 s 是最大清理成本 | 已实现影子根快速删除；清理 4.01 s、`leftover=[]` |
| 中 | 实机确认 4.10 `addItemsToCollection` / `getCollectionObjects` 语义 | 验证批量集合写入的实机行为 | 9 个集合成员数与基线一致；回退路径可触发 |
| 条件项 | P3 订单按需构建子集臂 | 每省一台约数秒（P2 后绝对值下降） | 4 臂子集订单通过（B 跳过，1 FINAL，`leftover=[]`）；3 臂场景由同一 `unit_ids` 路径覆盖 |
| 暂缓 | P4 长驻 worker 运行时复用 | 同进程第 2 次及以后省去 adopt/构建 | 重置协议、残留检查和 fail closed 完成后再立项 |

**建议顺序**：先完成 P0 验证门槛，再决定是否做 P1 索引复用和 P2 清理优化；
在 P1/P2 未通过 `full_flow` 前，不进入 P3。

## 8. 复现命令

```powershell
# 完整纯逻辑回归（2026-11-12: 615 passed / 7 skipped）
uv run pytest -q

# 启动计时（建议连续 3 次取中位数）
uv run python tools/measure_startup_stages.py --run-records off
uv run python tools/measure_startup_stages.py --run-records off --no-load
```
