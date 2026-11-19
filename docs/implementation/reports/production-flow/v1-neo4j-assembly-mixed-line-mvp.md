---
title: Neo4j 图谱混线装配 MVP 实施报告
type: report
tags:
  - 类别/实施报告
  - 类别/生产流程
created: 2026-11-14
updated: 2026-11-14
status: published
related:
  - docs/implementation/plans/production-flow/v1-neo4j-assembly-mixed-line-mvp.md
  - docs/implementation/reference/production-flow/assembly-mvp/README.md
  - docs/implementation/evidence/production-flow/v1_assembly_mvp_acceptance_20261114.json
---

# Neo4j 图谱混线装配 MVP 实施报告

## 1. 结论

方案 WP0–WP3 已完成限定范围交付：在同一 `L5_UFLOW` 底图上派生实验场景工件，
压缩机与法兰两个产品分别通过声明式订单完成“两个来料槽 → 逻辑齐套/装配 →
成品”的闭环，并在真实 CoppeliaSim 4.10 会话中按“压缩机 → 法兰 → 压缩机”
交替运行通过。

首版只验证产品切换和谱系正确；**没有**实现多订单并发、工位互斥、真实抓取、
轴孔插装、螺纹旋合或质量判定。报告中的“装配”均指物料账本原子消费与成品登记。

## 2. 交付物

| 交付物 | 位置 |
|---|---|
| 场景扩展模块 | `src/flexible_factory/simulation/assembly_mvp_scene.py` |
| 领域扩展常量 | `src/flexible_factory/domain/assembly_mvp.py` |
| 两个逻辑配方/模板 | `src/flexible_factory/domain/default_process.py` |
| 订单 fixture | `docs/implementation/reference/production-flow/assembly-mvp/order_*.json` |
| 实验工件 | `docs/scene_artifacts/experiments/assembly-mvp/v1/L5_UFLOW/v001_20261114_120734` |
| 验收证据 | `docs/implementation/evidence/production-flow/v1_assembly_mvp_acceptance_20261114.json` |
| 纯逻辑测试 | `tests/test_v1_assembly_mvp.py` |

## 3. 关键实现决策

### 3.1 v1 冻结类型不扩展，MVP 类型走 profile 分支

方案 §4 要求增加 6 个物料类型，但受保护 v1 工件的
`extra.v1.material_model.types` 会与 `domain.rules.ITEM_TYPES` 逐项比较；
把它直接扩成 14 项会使旧工件 `validate`/`start` 失败。实现采用：

- `domain.rules.ITEM_TYPES` 保持 8 个 v1 类型不变；
- MVP 类型放入 `domain.assembly_mvp.ITEM_TYPES`，并加入 `ItemType` 枚举、
  `ITEM_COLORS` 和 `rules.ALL_ITEM_TYPES`；
- 新工件在 `extra.v1.scene_profile=assembly_mvp` 时把 MVP 类型写入
  `material_model.types`；旧工件（无 profile）仍按 8 项校验；
- `config_digest`、`layout_digest`、`SCENE_CONTRACT_VERSION` 和受保护 v1
  场景包均未改变。

这是对方案 §4“类型集合增加”的显式收敛：新增类型仍封闭、有限且可追溯，
但不破坏既有工件契约。详见 `contract.validate_contract` 的 profile 分支。

### 3.2 两个独立配方，订单输入直接绑定逻辑合流端口

- 新增 `mvp_compressor_v1`、`mvp_flange_v1` 两个 `RecipeSpec`，各只有一个
  `mvp_pair_assembly` 逻辑工序，无静态 route/entry；
- `RecipeSpec.direct_input` 由“全部工序都是已登记直接来料 join”派生，
  只豁免 route/entry 覆盖检查，不引入通用配方编译器；
- `mvp_pair_assembly` 是唯一允许订单输入直接绑定逻辑端口的 handler
  (`DIRECT_INPUT_JOIN_HANDLERS`)；v1 `kit_join`/`e_material_pair` 仍要求
  前置工序供给；
- 订单输入显式写 `node_id`/`port_id`，编译器按模板端口顺序绑定 UID；运行时
  只按 `instance.input_uids` 和模板端口类型/区域匹配，不按“先到先配”。

### 3.3 物料账本通用提交

`MaterialRegistry.commit_pair()` 按端口顺序接收
`parent_uids + (zone, item_type)`，保留 legacy `commit_join()` 与
`commit_join_uids()` 的原常量契约和错误语义。订单 join 现在统一走
`commit_pair`，因此 v1 的 `SEMI_CE + PART_DE → FINAL` 与 MVP 的
`COMPRESSOR + BASEPLATE → COMPRESSOR_ASSY` /
`FRONT_FLANGE + REAR_FLANGE → FLANGE_ASSY` 共享消费检查、重复配对拒绝、
父子记录和 `JOIN_COMMIT` 事件。

### 3.4 场景 profile 与工件隔离

- `flexible-factory build --profile assembly_mvp` 在 v1 五岛底图上追加两个
  独立岛；默认 `full_flow` 行为不变；
- 扩展对象全部为静态、不可碰撞的岛面/托盘/标记，不新增机器人、传送带、
  传感器或 AGV；
- 实验工件使用独立 artifact root；`current` 指针在实验根内，不触碰
  `docs/scene_artifacts/v1/current.json`；
- `scene_profile` 写入 manifest `extra.v1` 和会话状态；加载/校验按 profile
  选择 8 项或 14 项物料契约。

## 4. 真实场景与订单验收

### 4.1 场景工件

- 构建来源：`stage3/L2_MIRROR/v008_20261029_112305`（只读父工件）；
- 实验工件：`v1/L5_UFLOW/v001_20261114_120734`；
- `validation.json`：`ok=true`，557 项检查通过；manifest 构建契约记录
  341 个唯一检查名（重复行 869），其中 MVP 扩展 154 个唯一检查名
  （重复行 308）全部通过；
- 18 个 `MVP_*` alias 写入 manifest 并在保存后重载校验中保持唯一/位置一致。

### 4.2 混线序列

在独立的 CoppeliaSim 4.10 进程（ZMQ 23001）上执行，未改动用户 23000 会话：

| 序 | 订单 | 运行 ID | 输入（UID/类型） | 成品父 UID | 结果 |
|---|---|---|---|---|---|
| 1 | `mvp-compressor-001` | `order_mvp-compressor-001_20261114_041006_bad9df` | `1=BASEPLATE`、`2=COMPRESSOR` | `(2, 1)` | `SUCCEEDED`, `ok=true` |
| 2 | `mvp-flange-001` | `order_mvp-flange-001_20261114_041251_62a85e` | `1=FRONT_FLANGE`、`2=REAR_FLANGE` | `(1, 2)` | `SUCCEEDED`, `ok=true` |
| 3 | `mvp-compressor-002` | `order_mvp-compressor-002_20261114_041811_5e3a6c` | `1=BASEPLATE`、`2=COMPRESSOR` | `(2, 1)` | `SUCCEEDED`, `ok=true` |

每次运行都重新加载同一实验工件，未复用上一笔的输入/输出对象。运行报告在
`docs/implementation/runs/`（Git 忽略），精简证据见
`docs/implementation/evidence/production-flow/v1_assembly_mvp_acceptance_20261114.json`。

## 5. 测试

- 新增 `tests/test_v1_assembly_mvp.py`（20 项）：目录/类型边界、两份 fixture
  校验/编译、错误端口、跨产品绑定、缺件、重复消费、谱系、混线序列、
  场景扩展构建/校验/契约往返、profile 契约类型分支、运行记录成品/谱系写入，
  以及现有订单服务/HTTP API 路径的 fixture 预览、创建和启动；
- 全量 pytest：**635 passed / 7 skipped**（7 个实机 pytest 用例按既有约定
  跳过；实机验收由 CLI 完成）；
- 既有 `full_flow` 订单纯逻辑端到端仍通过：`SUCCEEDED`、1 个 `FINAL`、
  父 UID `(1, 2)`；
- 受保护 v1 工件真实 `full_flow` 回归：`all_ok=true`，337.15 s / 6726 步，
  1 个 `FINAL`（父 UID `[1, 3]`）、1 个未配对 `SEMI_CE`、0 failure、
  `Shadow_*` 残留 `[]`；运行中 C 臂水平转移一次 IK 失败后由既有
  `horizontal_detour_+0.30` 备选路径恢复，未升级为任务失败；
- 运行记录 Q1–Q4：MVP 运行记录写入 `final_uids=[3]`、2 条谱系边、
  `BASEPLATE/COMPRESSOR/COMPRESSOR_ASSY` 各 1 条，`report_genealogy` 可还原
  父 UID `[1, 2]`；`report_run.final_uids` 已扩展到全部产品类型，v1 只含
  `FINAL` 时结果不变；
- 受保护 v1 工件契约检查保持通过：`config_digest`、`layout_digest`、
  `material_model.types` 均与保存值一致。

## 6. 版本与目录影响

| 项 | 变化 |
|---|---|
| `PROCESS_MODEL_VERSION` | `v1_process_20261106a` → `v1_process_20261114a` |
| `assembly_mvp.EXTENSION_VERSION` | `v1_assembly_mvp_20261114` |
| `SCENE_CONTRACT_VERSION` / `config_digest` / `layout_digest` | 不变 |
| 目录设备 | 新增 `logic_MVP`（无场景对象、无资源） |
| 目录区域 | 新增 8 个 `mvp_*` 区域（容量 1） |
| 目录配方/工序 | 新增 2 个直接来料逻辑配方（工序总数 10 → 12） |
| 物料类型记录 | 目录 seed 覆盖 14 个类型；v1 冻结契约仍为 8 个 |

静态目录数据库的旧 release 不包含新增设备/区域/类型；使用
`--catalog-source db` 前必须重新 `catalog import` / `catalog publish`。
默认 `--catalog-source code` 不受影响。本轮已在本地 `var/catalog/catalog.db`
导入并发布 `rel_v1_process_20261114a_20261114_042913`，
`catalog diff`/`verify` 均为 `ok=true`，并验证 MVP fixture 可用 DB 目录预览。

## 7. 未覆盖与边界

- 未验证多订单并发、两个岛同时运行、工位/AGV 资源争抢或互斥；
- 未实现真实轴孔插装、螺纹旋合、抓取姿态、视觉定位、公差与节拍；
- 未扩展 planner 对直接来料配方的搜索；MVP 订单使用显式 fixture；
- 未新增专用执行器、工位计时状态或新资源锁；逻辑 join 仍在 coordinator
  tick 中即时提交；
- 订单 runner 在没有臂候选时仍保守构建五台臂运行时（仅用于兼容既有
  `full_flow` 路径）；MVP 订单不派发任何臂/AGV 任务，实际仿真时间约
  0.05 s，但启动耗时包含这些未使用的运行时；
- 未把图谱文本字段（设备、环境、异常、注意事项）用作仿真约束；
- 未修改受保护 v1 场景包、current 指针或既有 `full_flow` 场景契约。