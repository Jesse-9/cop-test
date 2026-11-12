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
6. **纯逻辑回归已通过：607 passed / 7 skipped。** 7 个跳过项为需要
   `FLEXIBLE_FACTORY_SIM=1` 且本机运行 CoppeliaSim 4.10 的 focused 测试；
   `full_flow` 验收仍未执行。

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

精确拆分需要在 `_ensure_scene`/`adopt_stage4_scene`、`build_validation_context`、
`validate_artifact`、`validate_contract` 内分别打点；当前工具只覆盖到
`prepare_start_validation` 一整段。

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

已补充并执行纯逻辑测试：`uv run pytest -q` 为 **607 passed / 7 skipped**。
7 个跳过项为需要 `FLEXIBLE_FACTORY_SIM=1` 且本机运行 CoppeliaSim 4.10
（ZMQ 23000）的 focused 测试。新增用例覆盖：

- `tests/test_v1_contract_validation.py`：context 复用、枚举次数、
  scene 异常记录；
- `tests/test_v1_motion_collisions.py`：批量集合加入、抛错回退、静默批量修复、
  无批量 API 回退；
- `tests/test_v1_runtime_common.py`：发现缓存复用、查询属性只应用一次、
  FK 索引只建一次。

## 6. 验证缺口与风险

本报告**不声明**以下事项已完成：

- 未运行 `full_flow`，集合成员数、FINAL/UID/AGV 路线和启动摘要未与基线
  逐项比对；
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

纯逻辑回归已完成（607 passed / 7 skipped）；以下为剩余步骤：

| 优先级 | 步骤 | 预期收益 | 验收 |
|---|---|---|---|
| 高（必须） | 跑 `full_flow` 验收 | 确认集合成员、FINAL/UID/AGV 路线、启动摘要不变 | `all_ok`；与基线逐项一致 |
| 中 | 给计时工具增加子阶段打点：`loadScene` vs `adopt_stage4_scene`；`_ensure_scene`、`build_validation_context`、`validate_artifact`、`validate_contract`；并记录 CoppeliaSim 版本/引擎、artifact digest | 确认 74 s 的准确构成，补齐可复现环境信息 | 三次运行 JSON 含子阶段和环境字段；不改变产品代码 |
| 中 | 复用 `adopt_stage4_scene` 已建立的 alias 索引给 `ValidationContext`；评估把索引传入 `scene.verify()` | 去掉 1–2 次全场景枚举 | 校验检查集与差异不变；`prepare_start` 中位数下降 |
| 中 | 优化 `runtime_destroy`：批量移除影子根、批量销毁 collections、合并查询属性恢复 | 当前 36.7–43.7 s 是最大清理成本 | 无 `Shadow_*` 残留；查询属性恢复；清理中位数下降 |
| 中 | 确认 4.10 `addItemsToCollection` / `getCollectionObjects` 语义 | 验证批量集合写入的实机行为 | 9 个集合成员数与基线一致；回退路径可触发 |
| 条件项 | P3 订单按需构建子集臂 | 每省一台约数秒（P2 后绝对值下降） | 仅订单模式；`full_flow` 仍五台；未构建臂 fail closed |
| 暂缓 | P4 长驻 worker 运行时复用 | 同进程第 2 次及以后省去 adopt/构建 | 重置协议、残留检查和 fail closed 完成后再立项 |

**建议顺序**：先完成 P0 验证门槛，再决定是否做 P1 索引复用和 P2 清理优化；
在 P1/P2 未通过 `full_flow` 前，不进入 P3。

## 8. 复现命令

```powershell
# 完整纯逻辑回归（2026-11-12: 607 passed / 7 skipped）
uv run pytest -q

# 启动计时（建议连续 3 次取中位数）
uv run python tools/measure_startup_stages.py --run-records off
uv run python tools/measure_startup_stages.py --run-records off --no-load
```
