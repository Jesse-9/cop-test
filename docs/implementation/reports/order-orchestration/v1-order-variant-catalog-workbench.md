---
title: 订单方案目录实施报告 — 15 个内置订单变体与工作台直连
type: report
status: review
created: 2026-11-22
updated: 2026-11-22
scope: v1 UFLOW / order workbench / order variants / planner / scene profile / fleet config
related:
  - ../../archive-plans/order-orchestration/v1-order-variant-catalog-workbench.md
  - ../../archive-plans/order-orchestration/v1-order-dag-phase-09-scene-profile-order-catalog.md
  - ../../evidence/order-orchestration/v1_order_variant_catalog_20261122.json
  - v1-order-dag-phase-09-scene-profile-order-catalog.md
  - v1-order-dag-phase-07-dual-fleet.md
---

# 订单方案目录实施报告

本报告记录
[订单方案目录方案](../../archive-plans/order-orchestration/v1-order-variant-catalog-workbench.md)
的实施结果：把已登记路线、路线前缀、FINAL 合流和 MVP 直接来料包装成
15 个命名 `OrderVariant`，在订单列表页直观展示并一键创建普通订单。

结论：**已实施**。方案目录是内置只读目录，创建只生成普通订单版本，
**不自动启动仿真**；不新增数据库表/列、不新增 `OrderSource`、不改变
`OrderSpec`/DAG digest/compiler/validator。15 个变体中 7 个为完整路线
（4 个生产 + 3 个中间件完整路线），8 个为工序前缀/运输变体；生产蓝图
复用阶段 7/9 实机证据，前缀/运输变体明确标为 compiler-only。

## 1. 目录与分类

15 个变体：

| 组 | 变体 | 目标 | 臂 | 运输 | scope | verification |
|---|---|---|---|---|---|---|
| FINAL | `final_direct_kit` | FINAL | A,C,D,E | D→E | production | real_run |
| FINAL | `final_alt_kit` | FINAL | A,B,C,D,E | B→A,D→E | production | real_run |
| 直达 | `semi_ce_direct` | SEMI_CE | A,C,E | 无 | intermediate | planner_test |
| 直达前缀 | `semi_ac_direct_a` | SEMI_AC | A | 无 | intermediate | compiler_only |
| 直达前缀 | `semi_ce_direct_ac` | SEMI_CE | A,C | 无 | intermediate | compiler_only |
| 绕行 | `semi_ce_alt` | SEMI_CE | A,B,C,E | B→A | intermediate | compiler_only |
| 绕行前缀 | `semi_ab_alt_b` | SEMI_AB | B | 无 | transit | compiler_only |
| 绕行前缀 | `semi_ab_alt_ba` | SEMI_AB | B | B→A | transit | compiler_only |
| 绕行前缀 | `semi_ac_alt_a` | SEMI_AC | A,B | B→A | intermediate | compiler_only |
| 绕行前缀 | `semi_ce_alt_ac` | SEMI_CE | A,B,C | B→A | intermediate | compiler_only |
| 支架前缀 | `part_de_d` | PART_DE | D | 无 | transit | compiler_only |
| 支架前缀 | `part_de_de` | PART_DE | D | D→E | transit | compiler_only |
| 支架 | `part_de_bracket` | PART_DE | D,E | D→E | intermediate | compiler_only |
| MVP | `mvp_compressor_pair` | COMPRESSOR_ASSY | 无 | 无 | production | real_run |
| MVP | `mvp_flange_pair` | FLANGE_ASSY | 无 | 无 | production | real_run |

- 7 个 `route_complete`：两个 FINAL、两个 SEMI_CE 完整路线、PART_DE 完整
  路线、两个 MVP。
- 8 个工序/运输变体由路线前缀生成；相邻前缀只差一个工序，`difference_from`
  默认指向完整路线，`difference_summary` 展示缺失工序。
- `transit` 变体目标停在 AGV 上，仅用于运输/机械臂演示，不作为生产订单。
- `verification` 是证据级别，不是物理可行性保证；只有 4 个生产变体标
  `real_run`。

## 2. 物化器

新增 `OrderVariantMaterializer`（`domain/order_variants.py`）：

- 每个目标单位重复分支/join 结构；
- 分支按显式 `VariantStep(template_id, to_port)` 线性物化；
- 首工序由 `entry_item_type/entry_zone` 和显式端口创建 `OrderInput`；
- 后续工序按前一输出匹配显式端口创建 `OrderEdge`；
- join 用显式 `join_ports` 绑定分支末端；MVP 直接来料用
  `direct_inputs` 绑定 join 端口；
- 生成 `OrderSpec(source="manual")` 后调用现有 `OrderDagCompiler.preview`；
  失败不保存；
- 节点上限 `MAX_VARIANT_NODES=64`，超过返回 `ORDER_VARIANT_NODE_LIMIT`；
- 不分配 UID、不申请资源、不创建运行实例、不实现任意 recipe/DAG 搜索。

## 3. 服务与 API

`OrderService` 新增：

- `order_variants(artifact_id, scope, target_item_type)`：按
  `SceneCapabilities` 过滤 profile，返回方案目录及 `available`/
  `unavailable_reason`；
- `create_order_from_variant(variant_id, payload, artifact_id)`：
  - 未知方案 404 `ORDER_VARIANT_UNKNOWN`；
  - profile/结构不可用 422 `ORDER_VARIANT_UNAVAILABLE`；
  - 物化/编译失败 422 `ORDER_VARIANT_COMPILE`；
  - 数量超节点上限 422 `ORDER_VARIANT_NODE_LIMIT`；
  - 保存为普通 `source=manual` 版本，`note="variant:<id> | 用户备注"`；
  - 已存在订单创建新版本，沿用不可变版本/并发冲突语义。

HTTP：

```text
GET  /api/order-variants?artifact_id=&scope=&target=
POST /api/order-variants/{variant_id}/orders
```

`/api/health` 增加 `order_variant_version`。API 不接收前端提交的路线/模板
组合，只接收 `variant_id`、`order_id`、`quantity`、`note`。

## 4. 前端

`OrdersPage` 增加 `OrderVariantCatalog`：

- 完整路线方案卡片 + 工序/运输变体表格；
- 展示中文名、说明、目标/投料、机械臂 chips（A–E）、运输 chips
  （B→A/D→E）、join、差异说明、层级/验证级别、车队提示、工件可用性；
- 筛选：搜索、层级、目标、机械臂、运输、验证级别；
- “使用此方案创建订单”打开创建面板：订单 ID（预填）、数量、备注；
  创建后跳转订单详情，不自动启动；
- 已保存订单从 `version.note` 的 `variant:` 前缀显示“方案”徽标；
- `profile_source=unbound` 显示提示；不可用方案禁用创建并显示原因。

## 5. 版本与数据库

- 新增 `spec.ORDER_VARIANT_VERSION = "v1_order_variants_20261122"`；
- `OrderSpec`/`ORDER_CONTRACT_VERSION`/DAG digest/compiler/validator 不变；
- `ORDER_PLANNER_POLICY_VERSION` 不变（方案目录不走 planner 搜索）；
- `PROCESS_MODEL_VERSION`/`SCENE_PROFILE_VERSION` 不变；
- 不新增 `OrderSource`、表/列或迁移；`note` 记录方案来源；
- 无 DB 可编辑变体、无自动启动、无 cart 绑定。

## 6. 工件与车队覆盖

| 工件 | 可用方案 |
|---|---|
| `v005_20261117_110924`（full_flow，有 Cart2） | 13 个 full-flow 变体；MVP 跨 profile 不显示 |
| `v002_20261121_152334`（assembly_mvp，无 Cart2） | 全部 15 个；full-flow 仅 single |
| 历史 v001 | 不作为当前可用工件 |

车队仍为启动配置：

- `final_alt_kit` 同时使用 A/B/C/D/E；只有它 + v005 + `dual` 同时使用
  `agv_1`（B→A）和 `cart_2`（D→E）载货；
- `final_direct_kit` + `dual` 只用 `cart_2`；`single` 只用 `agv_1`；
- 仅含 `transport_BA` 的绕行前缀只使用 `agv_1`；
- MVP 不使用 AGV；
- 方案目录不把 `single/dual` 写入订单，启动对话框仍是权威选择。

## 7. 测试

执行日期：2026-11-22。

| 命令 | 结果 |
|---|---|
| `uv run pytest -q` | **785 passed, 8 skipped** |
| `cd frontend && npm test` | **16 passed** |
| `cd frontend && npm run e2e` | **5 passed** |
| `npm run typecheck` / `npm run build` | 通过 |

新增 `tests/test_v1_order_variants.py`：15 个变体唯一、全部编译、scope/
verification 计数、arm/transport 元数据、前缀差异、profile 过滤、数量/
节点上限、service 创建/版本化/跨 profile/未知、HTTP API/health。前端新增
方案目录 E2E 旅程（mock）。

## 8. 边界与未覆盖

- 前缀/中间件/运输变体为 compiler-only，没有新增 CoppeliaSim 实机验收；
  不把编译通过宣传为物理可行。
- 生产变体的实机证据来自阶段 7/9；本轮未重新运行仿真。
- 车队仅展示提示；显式把 `agv_1/cart_2` 绑定到订单需要扩展
  `OrderSpec`/模板，本文不做。
- 未实现任意 recipe/DAG 组合、DB 可编辑方案、自动启动、多订单并发或
  路线并行。
- 数量验证覆盖到 2 和节点上限负例；更大数量的供料/区域/批次约束仍需
  运行期验证。

证据：[v1_order_variant_catalog_20261122.json](../../evidence/order-orchestration/v1_order_variant_catalog_20261122.json)。