---
title: v1 启动性能优化方案 — 校验、影子臂/碰撞集合与按需构建
type: plan
tags:
  - 类别/实施计划
  - 类别/仿真
created: 2026-11-12
updated: 2026-11-12
status: partially-implemented
scope: v1 启动路径（lifecycle 校验、simulation runtime、订单子集构建）
---

# v1 启动性能优化方案

本文件把当前已识别的启动耗时问题整理成可执行步骤：先建立分阶段测量基线，
再优化 `prepare_start` 校验、五台影子臂/碰撞集合构建，最后评估订单按需构建
子集臂。**本文件不声明任何优化已完成或已获得性能收益**；实施完成后由
`docs/implementation/reports/simulation/v1-startup-performance-optimization.md`
给出实测结论、证据和未覆盖边界。

## 0. 状态

| 工作项 | 状态 | 说明 |
|---|---|---|
| P0 分阶段测量 | 已完成 3 次启动计时（2 次 load + 1 次 no-load） | `tools/measure_startup_stages.py`，用法见[使用指南](../../../wiki/98-ProjectUsage/USAGE.md)的“启动阶段计时”；结果见[实施报告](../../reports/simulation/v1-startup-performance-optimization.md) |
| P1 校验路径去重 | 代码已实现；启动计时、pytest 与 full_flow 功能验收已通过 | 方案见 §4；`prepare_start` 从约 25 s 降到 12.1–12.2 s，alias 缓存后 8.5–9.6 s |
| P2 影子臂/碰撞集合构建优化 | 代码已实现；启动计时、pytest 与 full_flow 功能验收已通过 | 方案见 §5；五台运行时从约 47.9 s 降到 20.3–21.4 s，最新复测 18.2–19.5 s；清理修复后 3.5–4.4 s |
| P3 订单按需构建子集臂 | 已通过 4 臂子集订单（B 跳过）；3 臂由同一路径覆盖 | 方案见 §6；首次尝试 E 释放校正失败，重试通过；`full_flow` 仍建五台 |
| P4 长驻 worker 运行时复用 | 暂缓 | 方案见 §7；等 P1–P3 实测后再评估 |

2026-11-12 实现进度：P1/P2 代码改动已落地，并完成两次 `load` 与一次
`--no-load` 启动计时；`prepare_start`、影子臂构建和“加载完成 → 仿真启动”
均明显下降，纯逻辑回归为 **615 passed / 7 skipped**。P3 的 builder 子集参数、
订单候选并集和 `active_unit_ids` 证据也已实现。干净 `full_flow` 功能验收
`all_ok=true`（1 个 FINAL、343.85 s、0 failure），但该次运行使用修复前的
快速销毁路径并留下 `Shadow_*`；修复后启动计时复测得到 `leftover=[]`、清理 4.01 s。**4 臂子集订单重试
通过**（A/C/D/E，B 未构建，1 FINAL，`leftover=[]`）；首次尝试在 E 释放
校正处失败，属间歇性运动问题；3 臂场景由同一 `unit_ids` 路径覆盖。
2026-11-13 21:31–21:53 四次复测的 `startup_total_wall` 为
**79.97–88.61 s**；旧工具打印的 130–210 s 是 `profile.*` 子项重复求和，
不是墙钟。详见
[实施报告](../../reports/simulation/v1-startup-performance-optimization.md)。

## 1. 目标与非目标

### 1.1 目标

1. 缩短从 ZMQ 连接/工件加载到 `start_stepping()` 返回的墙钟时间。
2. 保持运行语义不变：相同的启动点校验覆盖、相同的碰撞集合内容、相同的
   安全阈值、相同的 `full_flow` 行为、相同的受保护工件契约。
3. 每一步都有独立的前后测量数据，避免把“代码更短”当成“启动更快”。

### 1.2 非目标

- 不优化场景执行循环、运动规划或仿真运行阶段。
- 不放宽 `prepare_start` 校验、碰撞检查、距离阈值或安全锁存。
- 不把影子臂/碰撞集合写入 v1 工件，也不允许 `Shadow_*` 跨运行残留。
- 不引入多 ZMQ 客户端并发建臂；服务端并发行为未验证。
- 不改变 `full_flow` 的五台臂构建，也不改变默认 CLI 运行路径。

## 2. 测量方法与基线（P0）

### 2.1 工具

`tools/measure_startup_stages.py` 复现 `flexible-factory run` 的启动路径
（批次校验 → 连接 → 生命周期初始化 → 加载工件 → `prepare_start` →
运行记录初始化 → 组件构建 → 写快照 → `start_stepping`），在仿真刚启动时
截断，不执行场景循环，随后安全清理。代码入口参考：

- `src/flexible_factory/cli.py` 的 `cmd_run`
- `src/flexible_factory/application/run.py` 的 `run_loaded_scene_v1`
- `src/flexible_factory/application/lifecycle.py` 的 `prepare_start`
- `src/flexible_factory/simulation/component_builder.py` 的 `start_stepping`

执行：

```powershell
uv run python tools/measure_startup_stages.py
uv run python tools/measure_startup_stages.py --run-records off
```

### 2.2 测量规则

1. 同一台机器、同一 CoppeliaSim 版本、同一场景工件和模型文件；记录
   `productVersion`、工件 ID 和 `config_digest`。
2. 先做一次预热运行（排除文件缓存/首次加载影响），再连续运行 3 次，取
   中位数；每次保留 `startup_timing_<stamp>.json`。
3. 默认用 `--run-records off` 隔离数据库写入；需要评估运行记录成本时再对比
   `on`。
4. `--probe` 会步进约 35 次，只在需要时启用；它不计入“仿真刚启动”边界。
5. 记录 `startup_total_wall_s` 和 `stages` 中的 `prepare_start_validation`、
   `build_components`、`build.shadow_runtime` 等阶段，不只记录总时间。
   `build.*` 和 `profile.*` 是**重叠子项**，不能相加；真实启动总时间只看
   `startup_total_wall_s`。

### 2.3 历史参考基线（非本次实测）

2026-11-09 19:08 的运行日志（秒级时间戳）显示以下量级，仅用于排序优化项。
2026-11-12 已用 P0 工具完成两次 `load` 和一次 `--no-load` 实测，结果见
[实施报告](../../reports/simulation/v1-startup-performance-optimization.md)。

| 区间/阶段 | 参考耗时 | 说明 |
|---|---:|---|
| 加载完成 → `prepare_start` 完成 | ≈ 25 s | 约 15 s 在 `validate_artifact` 返回前，其余在契约校验、报告落盘和状态写入 |
| 批次解析 → 组件构建完成 | ≈ 49.1 s | 五台臂串行 |
| ├ 基座组件 + A 臂 IK | ≈ 0.28 s | `build.base_components` |
| ├ B–E IK 初始化 | ≈ 0.14 s | `build.ik_initialize_total`（含 A） |
| ├ 五台影子臂/碰撞集合 | ≈ 47.9 s | `build.shadow_runtime`，单台 8.8–11.8 s |
| └ 五台吸盘 + 快照 + `start_stepping` | ≈ 0.79 s | |
| 传送带驱动探测（可选） | ≈ 2.24 s | 仅在 `--probe` |
| **加载完成 → 仿真启动** | **≈ 74.7 s** | 不含 ZMQ 连接耗时（日志未记录） |

## 3. 不可变约束

1. **不得改变 `config_digest()` / `layout_digest()`。** 不要在
   `simulation/config*.py` 新增大写常量；订单子集等运行参数必须作为函数/运行
   上下文参数传入。现有受保护工件 `v1/L5_UFLOW/v001_20261103_004317` 会逐字
   比对摘要。
2. **`prepare_start` 仍是每次运行的全量只读校验。** 不新增“跳过校验”、
   “跨运行缓存校验结果”或“只校验一次”的开关。
3. **碰撞与安全语义不变。** 集合名称、成员角色、物品数量、自碰撞对、距离阈值、
   安全锁存和 `base_vs_workcell` 初始检查不得因优化而减少或放宽。
4. **影子资源仍是会话资源。** 不写入工件；运行结束必须停仿真后销毁；
   `prepare_start` 继续拒绝 `Shadow_*` 残留。
5. **`sim.step()` 只在协调器步进路径。** 计时/构建代码不得引入第二个 stepping
   owner。
6. **版本影响：无。** 本轮不修改 `spec` 版本、`config_digest()` /
   `layout_digest()`、manifest schema、`extra.v1` 字段或受保护工件；不需要
   重新迁移/验收。后续若实现 P3，新增运行参数也必须留在运行上下文，不能
   进入 `config*.py` 的大写常量摘要。

## 4. P1：启动校验路径去重

### 4.1 现状

`prepare_start` 的 `_validate_current` 依次执行：

1. `artifacts/store.py::validate_artifact`
   - `validate_manifest_environment`：manifest schema、配置摘要、模型摘要、
     模拟器版本；
   - `validate_scene_against_manifest`：`all_objects()` 全场景枚举建立
     alias 索引；105 个 manifest 对象逐个检查类型/父级/位置；
   - `scene.verify()`：再次 `alias_index()` 全场景枚举，并检查五岛、
     物流线、AGV、传送带等。
2. `simulation/contract.py::validate_contract`
   - `_resolve_aliases()`：第三次全场景枚举；
   - 五个单元的关节模式/TCP/dock 等只读检查；
   - 再次 `scene.verify()`：第四次全场景枚举。

主要成本不是 SHA-256（scene.ttt 约 2.7 MB），而是数千次同步 ZMQ 往返：
`all_objects()` 按类型逐个 `getObjects(i, type)`，`alias_of()` 再逐对象
`getObjectAlias`。上次校验报告为 454 个 `name` 检查项、0 差异。

### 4.2 实施步骤

1. 在 `_validate_current` 内建立一次性校验上下文，至少包含：
   - `objects`: 全场景 handle 列表；
   - `alias_index`: alias → handles；
   - `scene_report`: 一次 `scene.verify()` 的结果。
2. 给 `validate_scene_against_manifest` 增加可选参数，接收上述索引/对象列表；
   未传入时保持现有行为，便于逐步迁移和回退。
3. 给 `contract.validate_contract` 增加可选参数，复用 alias 索引和
   `scene_report`；`scene_contract` 与 `v1_scene_contract` 两个汇总检查仍按原
   名称/明细写入。
4. 同一次 `_validate_current` 只调用一次 `scene.verify()`；不要改变其检查
   函数、顺序或阈值。注意 `scene.verify()` 内部仍会建立自己的 alias 索引；
   本轮只去掉调用方重复枚举。若要把该索引也传入 `verify()`，需单独验证
   检查顺序和所有场景层级，暂不在本轮实施。
5. 可选优化：在等价性验证通过后，把 `all_objects()` 的类型循环替换为一次
   `sim.getObjectsInTree(sim.handle_scene, sim.handle_all, 0)`。必须先证明返回
   集合与现实现完全相同（至少 3 次加载、包含所有对象类型和层级根），否则
   保留 `all_objects()`。当前实现保留 `all_objects()`，未启用该替换。
6. 保留 `validate()` 与 `prepare_start` 的现有行为；`validate` 仍可作为独立
   诊断命令。

### 4.3 验收

- 对当前工件：校验 `differences` 为空；检查名称、检查对象范围和明细与改动前
  一致。若因复用 `scene.verify()` 去掉重复汇总项，必须在报告中说明，并证明
  检查族和对象覆盖没有减少；报告中的重复检查计数允许减少，但不得减少检查
  种类或对象范围。
- 注入式回归仍能拒绝：scene 摘要不匹配、config digest 不匹配、alias 重复/
  缺失、父级/位置越界、`Shadow_*` 残留。
- `flexible-factory validate` 与 `prepare_start` 对同一状态给出相同结论。
- P0 工具显示 `prepare_start_validation` 中位数下降；首个目标为 ≥30%，达不到
  时保留改动但记录实际收益，或回退。

### 4.4 风险与回退

- 索引只能在一次停止态校验内使用；影子臂构建后不得复用旧 alias 索引。
- `scene.verify()` 必须保持只读；若发现副作用，停止复用。
- `getObjectsInTree` 的等价性没有证据时不得启用。
- 所有新参数默认 `None`，保留旧路径；每步单独提交，必要时直接回退。

## 5. P2：影子臂/碰撞集合构建优化

### 5.1 现状

`simulation/runtime.py::build_v1_runtime` 串行构建五台臂，单台
`runtime_common.py::build_arm_runtime` 的主要成本在
`motion/collisions.py::CollisionRuntime.build`：

- 每条物流线 `discover_conveyor_parts` 走两遍（`build` 一次、
  `discover_workcell` 一次），五台臂再各自重复；
- 对每个 workcell 对象调用 `describe_object` 填充 `SceneGeometry.inventory`，
  但该字段全仓库没有读取者；
- 9 个集合中，`stage2_workcell` 与 `stage2_workcell_mount` 对每个对象分别
  调用一次 `addItemToCollection`（单臂约 193–293 次）；
- `_unit_obstacles` 为每台臂重新发现其余四台真实臂的连杆/吸盘；
- `verify_fk` 对每个连杆重新遍历真实臂子树；
- `_ensure_queryable` 对同一批真实臂句柄重复设置 `collidable`/`measurable`。

日志显示单台运行时 8.8–11.8 s，五台约 47.9 s；B 的 workcell 为 146 个对象，
其余为 96 个。

### 5.2 实施步骤

按以下顺序逐项实施并测量，避免一次提交混入多个变量：

1. **删除死代码 `geometry.inventory`。**
   - 去掉 `CollisionRuntime.build` 中为 `workcell`/`mount`/`link`/`pad_body`
     填充 `describe_object` 的循环；
   - 先确认 `describe_object` 没有其他生产消费者；若仅用于诊断，可保留函数
     但不默认调用；
   - 这是纯删除，不改变集合、查询或报告。
2. **批量加入集合。**
   - 新增内部辅助函数：优先调用 `sim.addItemsToCollection(handle, what,
     handles, 0)`，异常或不可用时回退到逐项 `addItemToCollection`；
   - `CollectionSpec.items` 仍在本地构建，报告字段和数量不变；
   - 实现已加入“批量后读取 `getCollectionObjects`、只补缺失句柄”的保护；
     4.10 批量 API 的实机确认仍待执行。
3. **共享场景发现缓存。**
   - 每次运行构建一次 `conveyor_parts[alias] = (pads, coarse)`；
   - 每次运行构建一次 `real_arm_parts[unit_id] = (links, link_aliases, pad_body)`；
   - 传入各台臂的 `build_arm_runtime`/`CollisionRuntime.build`；
   - 缓存值只读，调用方需要修改时先复制。
4. **消除重复 pad 遍历。**
   - `CollisionRuntime.build` 已发现的 pads 传给 `discover_workcell`，
     `discover_workcell` 不再对同一线重复调用 `discover_conveyor_parts`。
5. **复用真实对象 alias 索引。**
   - `adopt_stage4_scene` 已建立一次 alias 索引；可在停止态构建期把它挂到
     场景对象上供 runtime 构建查询；
   - 影子臂创建后索引失效，不得用于影子对象的查找。
   - 已实现：`adopt_stage4_scene` 缓存 `_validation_objects` /
     `_validation_alias_index`，`build_validation_context` 复用；
     `LifecycleManager.finish_run` 清除缓存。
6. **降低 FK/查询属性开销。**
   - `verify_fk` 每次构建真实臂 alias → handle 索引一次，不再逐连杆遍历；
   - `_ensure_queryable` 按 handle 记录原值并只设置一次；销毁路径仍完整恢复。
   - startup 摘要中的 `query_properties[*].before` 因此记录首次应用前的原值，
     字段结构不变；后续臂复用同一行证据。
7. **可选：影子模型加载复用。**
   - 仅当第 1–6 步后 `sim.loadModel` 仍占单台构建主要时间时，评估“加载一次
     UR5/suction pad，再复制给其余臂”；
   - 复制后必须重新设置别名、关节模式、static/respondable、脚本剥离和可见性；
   - 没有等价性证据前不实施。

### 5.3 验收

- 单台仍为 9 个集合，名称与成员数量与改动前一致（A/C/D/E 96，B 146）；
- 真实/影子 FK、杯口半径、`base_vs_workcell` 初始检查和一次
  `query_semantics_probe` 仍通过；
- `full_flow` 的 FINAL 数量、父 UID、AGV 路线和验收结论不变；
- `runtime.destroy()` 后无 `Shadow_*` 残留，查询属性恢复；
- P0 工具显示 `build.shadow_runtime` 中位数下降；首个目标为 ≥50%，达不到时
  记录实际收益或回退。

### 5.4 风险与回退

- 批量 API 在 4.10 的成员语义未验证：必须保留逐项回退路径。
- 缓存若被调用方原地修改，会污染其他臂的集合；缓存对象按只读使用。
- 每台臂的 workcell 因其余真实臂不同而不同，只能共享“场景静态部分”，不能
  共享最终 workcell 列表。
- 影子对象创建后 alias 索引失效；不要在构建后继续使用旧索引。
- 任一步导致集合数量、检查项或 `full_flow` 行为变化，立即回退该步。

## 6. P3：订单按需构建子集臂（条件项）

### 6.0 代码状态（2026-11-12）

`build_components_v1` / `build_v1_runtime` 已接受可选 `unit_ids`；
`normalize_unit_ids()` 校验空集/未知工位；订单 runner 取
`candidate_device_ids ∪ selected_device_id` 的臂工位并集，并在报告记录
`active_unit_ids` 和 `runtime_destroy`。`test_v1_order_runner_units.py`、
`test_v1_runtime_common.py` 覆盖候选并集、子集构建和非法工位。

**4 臂子集订单验收已通过**：`order-subset-4arm` 重试得到
`active_unit_ids=A/C/D/E`（B 未构建）、1 个 FINAL（父 UID `[1,2]`）、
sim 163.4 s、3268 步、0 failure、`leftover=[]`。首次尝试在 E 岛
`bracket_to_E` 释放校正超限中止，说明该释放失败是间歇性运动问题；
3 臂场景未单独运行，由同一 `unit_ids` 路径覆盖。

### 6.1 前置条件

- 仅用于订单 DAG 运行；`full_flow` 和默认 CLI 路径仍构建 A–E 五台。
- 订单编译结果必须能给出“可能被选中的臂设备”集合。取所有 DAG 节点
  `candidate_devices` 中 `arm_*` 的**并集**，不能只取 planner 当前选择，
  否则调度改选时会缺少运行时。
- 不得新增 `simulation/config*.py` 大写常量；集合通过运行参数传递。
- 空闲臂的真实连杆仍通过 `_unit_obstacles` 进入活动臂的工作区障碍，不能因为
  少建影子臂而减少碰撞检查。

### 6.2 实施步骤

1. 在订单编译/运行侧增加只读辅助函数，返回 `required_unit_ids`；没有可识别
   臂节点时明确报错或按显式策略处理，不静默建零台。
2. 给 `application/run.py::build_components_v1` 和
   `simulation/runtime.py::build_v1_runtime` 增加可选 `unit_ids` 参数；
   默认 `None` 等价于 `cfg.UNIT_IDS`。
3. 处理 A 臂特例：`build_stage1_components` 目前总会构建 A 的 IK。要么传入
   是否包含 A 的参数，要么在 A 不在集合时跳过其 IK；传送带、物料、AGV 等
   基础组件始终构建。
4. 只为 `unit_ids` 构建 `comp["ik"]`、`comp["suction"]` 和 runtime 臂；
   其余字典项不创建。
5. `Stage4Runtime.arm()` 对未构建工位抛出明确错误；协调器
   `_poll_task`/`_poll_unit`、`ArmExecutor._build_controller`、安全锁存和
   运行汇总审计“缺失臂”路径，确保被调度到未构建臂时 fail closed。
6. 运行快照/报告记录 `active_unit_ids`，验收时与影子别名集合交叉核对。
7. 测试：3 臂订单启动并通过代表性任务；空闲臂不在 `Shadow_*` 中但仍在活动臂
   workcell 障碍中；请求第 4 台臂时明确失败；`full_flow` 仍建 5 台。

### 6.3 验收

- 3 臂订单能完成启动和代表性执行；仅存在 3 套影子臂/集合；
- 本次验收夹具为 A/C/D/E 四臂（跳过 B）；3 臂场景由同一 `unit_ids`
  参数路径覆盖，不另设分支；
- 空闲臂的真实连杆仍出现在活动臂的 `other_units` 障碍摘要中；
- `full_flow` 的构建、运行和验收结果不变；`config_digest()` 不变；
- P0 工具显示省略臂的构建时间被移除；P2 完成后需重新测量绝对收益。

### 6.4 风险与回退

- 候选集不等于最终选择集：必须用并集；运行中不能懒构建（构建要求停止态）。
- 订单可能包含纯逻辑节点或零臂节点，需要明确策略。
- 安全汇总/日志格式可能因臂数变化而改变，需保持字段兼容并记录活动集合。
- 默认路径保持五台；子集参数只由订单路径显式传入，可随时回退。

## 7. P4：长驻 worker 运行时复用（暂缓）

“沿用上次构建的影子臂”在默认 `run` 路径不可行：影子臂/集合是会话资源，
`loadScene` 会替换场景，跨进程 Python 运行时对象也不存在；`prepare_start`
还会拒绝 `Shadow_*` 残留。唯一可行的方向是长驻 worker（如 `api serve`）：

- 不重新 `loadScene`，保留同一 `Stage4Runtime`；
- 每次运行前重置 target/parts/hazards/载荷开关，重新执行 FK 与
  `base_vs_workcell` 检查；
- 场景被重新加载、工件变更或发现残留时 fail closed，回退到完整重建；
- 只对同一 worker 的第 2 次及以后运行有效，对 CLI/新进程无效。

该方向涉及运行状态复用和安全边界，**等 P1–P3 有实测结论后再单独立项**；
本方案不实施。

## 8. 实施顺序与提交边界

| 顺序 | 工作 | 前置 | 提交边界 | 停止/回退条件 |
|---|---|---|---|---|
| 0 | P0 基线测量 | 工具已交付，基线待采集 | 不改产品代码 | 无 |
| 1 | P1 校验去重 | P0 有 3 次基线 | 一个提交只改校验路径 | 检查覆盖减少或注入回归失效 |
| 2 | P2.1 删除 inventory + 批量集合 | P1 已验收 | 独立提交，单独测量 | 集合成员/数量变化 |
| 3 | P2.2 共享发现缓存 + FK/属性复用 | P2.1 已验收 | 独立提交，单独测量 | 状态污染或销毁残留 |
| 4 | P3 子集臂 | P2 已验收；订单需求确认 | 独立提交；默认五台 | 调度到未构建臂无法 fail closed |
| 5 | P4 worker 复用 | P3 后另立项 | 不纳入本方案 | 安全/状态边界不清 |

每一步都必须：改动前用 P0 工具记录基线、改动后记录 3 次中位数、保留 JSON
结果；连续两步收益低于 10% 或出现语义风险时，停止后续步骤并记录原因。

实际执行偏差（2026-11-12）：P1/P2 代码已先落地，随后完成 3 次启动计时和
完整纯逻辑回归（607 passed / 7 skipped）；集合成员核对和 `full_flow` 验收
仍未执行。因此在补齐这些验证前不得进入 P3，也不得把 P1/P2 标为功能验收
通过；性能数据见
[实施报告](../../reports/simulation/v1-startup-performance-optimization.md)。

### 8.1 基于实测的后续顺序

| 顺序 | 工作 | 前置/门槛 | 验收 |
|---|---|---|---|
| 1 | 跑 `full_flow` 验收 | 纯逻辑回归已通过（615 passed / 7 skipped） | 已通过 `all_ok`；修复后清理复测 `leftover=[]`，完整 full_flow 未重跑 |
| 2 | 给计时工具增加 `loadScene` vs `adopt_stage4_scene`、`_ensure_scene` vs 校验的子阶段打点，并记录 CoppeliaSim 版本/引擎和 artifact digest | 步骤 1 可用 | 已实现 `profile.*` 子阶段和 simulator/config 字段 |
| 3 | 复用 `adopt_stage4_scene` 的 alias 索引给 `ValidationContext`；评估把索引传入 `scene.verify()` | 步骤 2 有拆分数据 | 已实现索引缓存复用；`prepare_start` 9.63 s |
| 4 | 优化 `runtime_destroy`：批量移除影子根、批量销毁 collections、合并查询属性恢复 | 步骤 1 通过 | 已实现影子根快速删除；清理 4.01 s、`leftover=[]` |
| 5 | 实机确认 4.10 `addItemsToCollection`/`getCollectionObjects` | 步骤 1 可用 | 9 个集合成员数不变；回退路径可触发 |
| 6 | P3 订单子集臂（条件项）；P4 worker 复用（暂缓） | 步骤 1–5 完成且订单需求确认 | 4 臂子集订单通过；3 臂由同一路径覆盖 |

## 9. 验证矩阵

| 范围 | 入口 | 判定 |
|---|---|---|
| 工件/契约/生命周期 | `tests/test_v1_artifact_store.py`、`tests/test_v1_contract_validation.py`、`tests/test_v1_lifecycle_state.py` | 全通过；注入式差异仍被拒绝 |
| 碰撞/运行时 | `tests/test_v1_motion_collisions.py`、`tests/test_v1_runtime_common.py` | 集合名称/数量、FK、杯口半径、查询语义不变 |
| 协调器/订单 | `tests/test_v1_coordinator_*.py`、`tests/test_v1_order_*.py`、`tests/test_v1_order_runner_units.py` | 订单 DAG 与 full_flow 行为不变；子集臂 fail closed |
| 仿真 focused | 现有 `tests/test_v1_stage2_arm_sim.py`、`tests/test_v1_stage2_agv_sim.py`、`tests/test_v1_stage4_scheduler_sim.py`、`tests/test_v1_order_sim.py` | 按当前仿真测试入口执行；未执行项在报告中明确标注 |
| 启动计时 | `uv run python tools/measure_startup_stages.py` | 3 次中位数；记录 JSON |
| full_flow 验收 | `flexible-factory run --scenario full_flow --time 700` | `all_ok`；FINAL/UID/AGV 路线与基线一致 |
| 3 臂订单验收（P3） | 订单 DAG 运行入口 | 仅 3 套影子资源；代表性任务完成 |

## 10. 回退与停止条件

出现以下任一情况，停止当前步骤并回退该提交：

- 校验检查名称、对象覆盖或差异判定发生变化，且不能证明等价；
- 碰撞集合名称、成员数量、自碰撞对或初始检查结果变化；
- `full_flow` 的 FINAL 数、父 UID、AGV 路线或验收结论变化；
- `config_digest()`/`layout_digest()`/manifest 契约发生变化；
- `runtime.destroy()` 后出现 `Shadow_*` 残留或查询属性未恢复；
- 单步收益低于 10%，且没有明确的可维护性收益。

回退以单个提交为单位，不做跨步骤的“部分保留”。

## 11. 文档与报告同步

- 实施前：本方案登记到[实施资料索引](../../README.md)和
  [项目文档登记表](../../governance/project-document-register.md)。
- 实施中：若新增/修改内部函数或模块职责，同步更新
  [仿真模块清单](../../reference/simulation/simulation-module-inventory.md)。
- 实施后：新增
  `docs/implementation/reports/simulation/v1-startup-performance-optimization.md`，
  记录前后中位数、JSON 路径、测试结果、未执行项和回退/偏差。
- 若改变用户可见命令、选项或启动行为，同步更新
  [项目使用指南](../../../wiki/98-ProjectUsage/USAGE.md)和
  [CLI 参数手册](../../../wiki/98-ProjectUsage/CLI_REFERENCE.md)。
- 本方案不替代实施报告；方案状态不能作为性能收益的验收证据。
