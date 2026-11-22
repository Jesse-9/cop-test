---
title: v1 订单方案目录与工作台直连方案
type: plan
status: planned
created: 2026-11-22
updated: 2026-11-22
scope: v1 UFLOW / order workbench / order variants / planner / scene profile / fleet config
related:
  - v1-order-dag-phase-09-scene-profile-order-catalog.md
  - v1-order-dag-phase-08-fleet-aware-planner.md
  - v1-order-dag-phase-07-dual-fleet.md
  - ../../reports/order-orchestration/v1-order-dag-phase-09-scene-profile-order-catalog.md
  - ../../reports/order-orchestration/v1-order-dag-phase-07-dual-fleet.md
  - ../production-flow/v1-neo4j-assembly-mixed-line-mvp.md
---

# v1 订单方案目录与工作台直连方案

## 1. 目标与结论

在订单工作台增加“订单方案目录”：把当前目录中**已登记、可编译**的工艺
组合（完整路线、路线前缀、合流和 MVP 直接来料）命名包装成 `OrderVariant`，
在订单列表页直观展示每条方案使用的机械臂、运输段、合流和目标，并支持
一键创建普通订单。

首版决策（已确认）：

- **内置只读变体目录**，不做数据库可编辑、不做预设管理页；
- **创建并保存订单**，不自动启动仿真；
- 变体包含**路线完整方案 + 工序前缀/运输中间态**，让“一个工序不同”也成为
  独立方案；
- 车队（`single/dual`）只作为**展示提示和启动时选择**，不写入订单 DAG，
  不新增 `OrderSpec` 设备字段；
- 不新增数据库表/列，不新增 `OrderSource`，不实现通用 recipe/DAG 编译器。

结论：**不是只改数据库，推荐方案不需要数据库迁移。**

原因：

1. 数据库无法生成订单、暴露 API 或渲染前端入口；数据库不是执行路径。
2. `route_variant` 是代码静态目录的只读发布镜像，没有显示名/组合语义；
   `runtime_catalog()` 要求数据库与代码目录等价，单独加表/字段不能形成
   可靠的运行时能力。
3. 单条 `RouteVariant` 不是完整订单；`FINAL` 需要“壳体路线 + 支架路线 +
   `kit_join`”，前缀变体需要线性工序链和端口绑定，MVP 需要直接来料
   join。必须由服务端确定性物化并交给现有 validator/compiler。
4. 现有 `order_version.note` 可记录 `variant:<id>`，不需要新增列；订单仍
   保存为普通 `source=manual` 版本。

旧“四预设”方案是本文的子集：四个生产蓝图保留为第一层，新增路线前缀/
中间态/运输演示变体作为第二层。

## 2. 现状与约束

| 现状 | 影响 |
|---|---|
| `domain/process_model.py` 的 `RouteVariant` 只有 id/group/semantics/entry/exit/operation_ids | 可推导路线前缀，但没有显示名、目标、臂/运输摘要 |
| `domain/default_process.py` 已登记 `housing_direct`、`housing_alt`、`bracket_route` 与两个 MVP 直接来料 recipe | 15 个变体都可以由现有模板/路线确定性地派生 |
| `application/planner.py` 只搜索完整 route/join 宏 | 无法生成路线前缀；变体目录需要独立的有限 materializer |
| `domain/order_validation.py`/`order_compiler.py` 接受已登记模板组成的 DAG | 变体仍走同一 validator/compiler；不新增编译规则 |
| `frontend/src/pages/OrdersPage.tsx` 只展示已保存订单 | 需要新增方案目录、筛选和一键创建 |
| `order_version.note` 已存在 | 可记录 `variant:<id>`，无需迁移 |

约束：

- 变体定义只引用目录中已登记的工序模板、端口、区域和 join handler；
- 不实现任意模板排列/组合搜索；变体是有限、显式、可测试的；
- 每个变体必须通过 `OrderValidator`/`OrderDagCompiler.preview`；
- 前缀/运输中/中间态变体必须标注验证级别，不冒充实机已验证；
- 变体不改变 `OrderSpec` 契约、DAG digest 算法、compiler/validator 或
  运行时派工语义。

## 3. 数据模型

新增 `domain/order_variants.py`，纯 domain，不导入 CoppeliaSim、SQLAlchemy
或 application 层。

```python
OrderVariant(
    variant_id="semi_ce_direct_ac",
    display_name="A+C 汇入传送带 → SEMI_CE（中间件）",
    description="A 臂上料 + C 臂搬运；不含 E 臂 buffer 搬运",
    scene_profile="full_flow",
    scope="intermediate",                 # production | intermediate | transit
    verification="compiler_only",          # real_run | planner_test | compiler_only
    target_item_type="SEMI_CE",
    target_quantity=1,
    branches=(
        VariantBranch(
            branch_id="housing",
            entry_item_type="RAW_A",
            entry_zone="line_s1a",
            steps=(
                VariantStep("housing_direct_A", "input-0"),
                VariantStep("housing_finish", "input-0"),
            ),
        ),
    ),
    join_template_id="",
    join_ports=(),
    direct_inputs=(),                    # MVP 直接来料时使用
    difference_from="semi_ce_direct",
    difference_summary="比完整直达路线少 housing_to_E_buffer（E 臂 buffer）",
    used_arms=("A", "C"),
    used_transports=(),
    recommended_transport_fleet="single",
)
```

字段约束：

- `variant_id`：稳定 ASCII 标识；订单 ID 字符集；全目录唯一。
- `scope`：
  - `production`：最终产品 `FINAL`/MVP 装配体；
  - `intermediate`：目标停在工位/传送带/缓冲区的部件；
  - `transit`：目标停在 AGV 上，只用于运输/机械臂演示。
- `verification`：
  - `real_run`：有阶段 7/9 的真实 CoppeliaSim run；
  - `planner_test`：有 planner/compiler 黄金用例，未实机；
  - `compiler_only`：validator/compiler 通过，未实机、未实跑。
- `branches`：每个分支是一条**线性工序链**；`entry_item_type/zone` 与
  首工序端口明确绑定。
- `join_template_id`/`join_ports`：可选；分支末工序输出连接到 join 端口。
- 直接来料变体（MVP）：`branches=()`，`direct_inputs` 显式绑定 join 端口。
- `used_arms`/`used_transports`：由模板派生，供前端 chips 和筛选。
- `difference_from`/`difference_summary`：与同族基线比较，满足“一个工序
  不同也算独立方案”的展示需求。
- `recommended_transport_fleet`：仅展示提示；不写入订单/不覆盖启动选择。

## 4. 变体目录

### 4.1 生成规则

`build_order_variants(catalog)` 只做有限派生：

1. 对每条 `RouteVariant` 生成所有**非空前缀**（按 operation_ids 顺序），
   每个前缀的目标是末工序输出；入口是路线 entry zone/type。
2. 对完整壳体路线（`housing_direct`/`housing_alt`）与完整支架路线
   （`bracket_route`）生成 `FINAL` 组合，并用 `kit_join` 合流。
3. 对两个 MVP 直接来料 recipe 生成单节点变体。
4. 按 `(target, 分支模板签名, join_template_id)` 去重。
5. 用 `OrderVariantMaterializer` + `OrderDagCompiler.preview` 校验每个
   变体；不通过的定义不进入目录，并记录目录诊断。
6. 对每个变体分类 `scope`、`verification`、`used_arms`、
   `used_transports`、`difference_from`。

命名策略：

- 生产/中间/运输主变体使用显式中文名（见下表）；
- 前缀变体由“末工序 + 目标 + 缺口”生成可读名称；
- 原始模板 ID 只放在详情/`mono` 字段，不作为主标题。

验证映射：

- `final_direct_kit`、`final_alt_kit`、两个 MVP：`real_run`（阶段 7/9）；
- `semi_ce_direct`：`planner_test`（阶段 4 route-only target 用例）；
- 其它完整路线/前缀/运输变体：`compiler_only`；
- 运输中变体永远不标 `real_run`，除非未来有专用验收。

### 4.2 变体清单（15 个）

| # | variant_id | 展示名 | 目标 | 分支/模板 | 臂 | 运输 | scope | verification |
|---|---|---|---|---|---|---|---|---|
| 1 | `final_direct_kit` | 壳体直达 + 支架齐套 → FINAL | FINAL | housing_direct 全 + bracket_route 全 + kit_join | A,C,D,E | D→E | production | real_run |
| 2 | `final_alt_kit` | 壳体绕行 + 支架齐套 → FINAL | FINAL | housing_alt 全 + bracket_route 全 + kit_join | A,B,C,D,E | B→A, D→E | production | real_run |
| 3 | `semi_ce_direct` | 壳体直达完整 → SEMI_CE | SEMI_CE | housing_direct 全 | A,C,E | 无 | intermediate | planner_test |
| 4 | `semi_ac_direct_a` | A 臂上料 → SEMI_AC | SEMI_AC | housing_direct_A | A | 无 | intermediate | compiler_only |
| 5 | `semi_ce_direct_ac` | A+C 汇入传送带 → SEMI_CE | SEMI_CE | housing_direct_A + housing_finish | A,C | 无 | intermediate | compiler_only |
| 6 | `semi_ce_alt` | 壳体绕行完整 → SEMI_CE | SEMI_CE | housing_alt 全 | A,B,C,E | B→A | intermediate | compiler_only |
| 7 | `semi_ab_alt_b` | B 臂上料 → SEMI_AB（AGV 在载） | SEMI_AB | housing_alt_B | B | 无 | transit | compiler_only |
| 8 | `semi_ab_alt_ba` | B 臂上料 + B→A → SEMI_AB（AGV 在载） | SEMI_AB | housing_alt_B + transport_BA | B | B→A | transit | compiler_only |
| 9 | `semi_ac_alt_a` | 绕行 A 臂卸料 → SEMI_AC | SEMI_AC | housing_alt_B + transport_BA + housing_alt_A | A,B | B→A | intermediate | compiler_only |
| 10 | `semi_ce_alt_ac` | 绕行 A+C → SEMI_CE | SEMI_CE | 前 9 的链 + housing_finish | A,B,C | B→A | intermediate | compiler_only |
| 11 | `part_de_d` | D 臂上料 → PART_DE（AGV 在载） | PART_DE | bracket_process | D | 无 | transit | compiler_only |
| 12 | `part_de_de` | D 臂上料 + D→E → PART_DE（AGV 在载） | PART_DE | bracket_process + transport_DE | D | D→E | transit | compiler_only |
| 13 | `part_de_bracket` | 支架完整 → PART_DE | PART_DE | bracket_process + transport_DE + bracket_to_E | D,E | D→E | intermediate | compiler_only |
| 14 | `mvp_compressor_pair` | 压缩机 + 底板 → 压缩机装配体 | COMPRESSOR_ASSY | mvp_compressor_assembly（直接来料） | 无 | 无 | production | real_run |
| 15 | `mvp_flange_pair` | 前法兰 + 后法兰 → 法兰装配体 | FLANGE_ASSY | mvp_flange_assembly（直接来料） | 无 | 无 | production | real_run |

说明：

- #4、#5 与 #3 是同族，依次多 `housing_finish`、`housing_to_E_buffer`，
  对应“一个工序不同也算”。
- #7→#8→#9→#10 依次多 `transport_BA`、`housing_alt_A`、`housing_finish`，
  展示 B 臂、A 臂、B→A 运输的逐级组合。
- #11→#12→#13 依次多 `transport_DE`、`bracket_to_E`，展示 D/E 臂与运输。
- #1/#2 是生产合流；#2 同时使用 A/B/C/D/E 五台臂；#1 不使用 B。
- 运输中变体（#7/#8/#11/#12）只作为运输/机械臂演示，不作为生产订单
  宣传；UI 必须有 `transit` 标记。

## 5. 确定性物化器

新增 `OrderVariantMaterializer`（domain 纯逻辑），只支持内置变体的**线性
分支 + 可选 join + 直接来料 join**，不是通用 recipe 编译器：

1. 对每个目标单位重复变体结构（`target_quantity`）。
2. 每个分支按 `steps` 顺序创建节点：
   - 首工序：按 `entry_item_type/entry_zone` 和显式入口端口创建
     `OrderInput`（`node_id`/`port_id`）；
   - 后续工序：根据前一工序输出与当前工序输入端口匹配创建 `OrderEdge`；
     端口匹配必须唯一，否则变体不可用。
3. 有 join 时创建 join 节点，按 `join_ports` 把各分支末工序连到端口；
   无 join 时分支末工序即目标。
4. 直接来料 join：不创建分支节点，直接为 join 的每个端口创建
   `OrderInput`。
5. 生成 `OrderSpec`（`source="manual"`），交给 `OrderValidator`/
   `OrderDagCompiler.preview`；失败则不保存并返回定位诊断。
6. 数量上限：按变体节点数 × 目标数量限制在配置上限（如 64），超过返回
   `ORDER_VARIANT_NODE_LIMIT`，不进入仿真。

节点/端口规则：

- 变体定义显式携带首工序端口和 join 端口，不依赖模糊匹配；
- 同一变体不得产生重复 `node_id`/`input_id`；命名包含变体 slug、单位和
  工序序号，满足 64 字符 ID 限制；
- 不生成循环、不自动补工序、不伪造 predecessor；
- 目标和输入 UID 由现有 compiler 在保存后分配；物化器不创建 UID。

## 6. 服务与 API

### 6.1 列表

```http
GET /api/order-variants?artifact_id={ref}&scope={all|production|intermediate|transit}&target={item}
```

响应：

```json
{
  "ok": true,
  "artifact_id": "v1/L5_UFLOW/v005_20261117_110924",
  "scene_profile": "full_flow",
  "profile_source": "default",
  "variant_version": "v1_order_variants_20261122",
  "variants": [
    {
      "variant_id": "final_alt_kit",
      "display_name": "壳体绕行 + 支架齐套 → FINAL",
      "description": "RAW_B 绕行 + RAW_D 支架，在 E 岛 kit_join 合流",
      "scene_profile": "full_flow",
      "scope": "production",
      "verification": "real_run",
      "target": {"item_type": "FINAL", "quantity": 1},
      "inputs": [
        {"item_type": "RAW_B", "zone": "line_s1b", "quantity": 1},
        {"item_type": "RAW_D", "zone": "line_s2d", "quantity": 1}
      ],
      "used_arms": ["A", "B", "C", "D", "E"],
      "used_transports": ["transport_BA", "transport_DE"],
      "join_template_id": "kit_join",
      "difference_from": "final_direct_kit",
      "difference_summary": "壳体改为 B 臂上料 + B→A 运输，多使用 B 臂",
      "recommended_transport_fleet": "dual",
      "available": true,
      "unavailable_reason": ""
    }
  ]
}
```

### 6.2 直接创建

```http
POST /api/order-variants/{variant_id}/orders
{
  "order_id": "variant-final-alt-20261122",
  "quantity": 1,
  "note": "from workbench variant catalog"
}
```

响应：

```json
{
  "ok": true,
  "variant_id": "final_alt_kit",
  "saved_order": {"order_id": "...", "version": 1, "dag_digest": "..."},
  "order": {...},
  "preview": {...}
}
```

错误：

| 场景 | HTTP/code |
|---|---|
| 未知变体 | 404 `ORDER_VARIANT_UNKNOWN` |
| 当前 profile/工件不可用 | 422 `ORDER_VARIANT_UNAVAILABLE` |
| 物化/编译失败 | 422 `ORDER_VARIANT_COMPILE` |
| 数量超节点上限 | 422 `ORDER_VARIANT_NODE_LIMIT` |
| `order_id`/quantity 非法 | 沿用 `ORDER_SPEC`/`ORDER_RUN_CONFIG` |

保存：

- `source="manual"`；`note` 默认 `variant:<variant_id>`，用户备注追加；
- 已有 `order_id` 时创建新版本，保持现有不可变版本语义；
- 不写 planner 元数据（变体不是 planner 搜索）；
- 不自动创建 run；
- 订单列表从 `version.note` 的 `variant:` 前缀显示“方案”徽标。

## 7. 前端工作台

在 `frontend/src/pages/OrdersPage.tsx` 增加“订单方案目录”区域：

### 7.1 完整路线方案卡片

- 第一层显示 7 个完整路线变体（4 个生产 + 3 个中间件完整路线），卡片网格；
- 每卡展示：
  - 中文名、说明、目标×数量、投料；
  - **机械臂 chips**：A/B/C/D/E；
  - **运输 chips**：B→A、D→E；
  - **join chip**：`kit_join` / `mvp_pair_assembly`；
  - **差异**：`difference_summary`（相对基线）；
  - **层级/验证**：生产/中间件/运输演示 + 实机/仅编译；
  - **车队提示**：`single`/`dual` 及实际载货车辆；
  - profile/工件可用性。
- “使用此方案创建订单”打开对话框（订单 ID、数量、备注），不自动启动。

### 7.2 工序/运输变体

- 第二层用可折叠表格/紧凑卡片展示 8 个前缀/中间/运输变体；
- 支持筛选：
  - 层级（生产/中间件/运输）；
  - 目标物料；
  - 机械臂（A/B/C/D/E）；
  - 运输（B→A、D→E、无）；
  - 车队覆盖（单车/双车/两车都参与）；
  - 验证级别（实机/planner/仅编译）。
- 运输中变体显示醒目“非生产/运输演示”标记，默认排序在生产方案之后。

### 7.3 交互与边界

- 创建成功跳转 `/orders/{order_id}`；启动仍由 `StartRunDialog` 选择工件和
  `single/dual`；
- 前端不提交路线/模板 ID，只提交 `variant_id`/`order_id`/`quantity`/`note`；
- `profile_source=unbound` 时显示提示：创建订单仍以实际启动工件 profile
  校验；
- 已保存订单读取 `version.note` 的 `variant:<id>` 显示“方案”徽标；
- 不新增自动启动、批量创建或运行调度入口。

## 8. 工件与车队覆盖

### 8.1 工件

| 工件 | profile | Cart2 | 可用变体 |
|---|---|---|---|
| `v1/L5_UFLOW/v005_20261117_110924` | `full_flow` | 有 | 所有 full_flow 变体（13 个）+ 不可用 MVP |
| `v1/L5_UFLOW/v002_20261121_152334`（实验） | `assembly_mvp`（含 v1 底图） | 无 | 所有 full_flow 变体（single）+ 2 个 MVP |
| 历史 `v001_20261103_004317` / MVP v001 | 历史/旧 config | — | 不作为当前可用工件 |

变体目录按 `SceneCapabilities` 过滤；跨 profile 变体不显示，创建 API 再次
拒绝。

### 8.2 车队

`single/dual` 是运行配置，不是订单字段；变体只提供提示。

| 变体组 | `single` | `dual` |
|---|---|---|
| FINAL 直达 | `agv_1` D→E | `cart_2` D→E；`agv_1` 不载货 |
| FINAL 绕行 | `agv_1` B→A + D→E | `agv_1` B→A；`cart_2` D→E（**两车都载货**） |
| SEMI_CE 直达/前缀 | 无运输 | 无运输 |
| SEMI_CE 绕行/含 B→A 前缀 | `agv_1` B→A | 仍为 `agv_1` B→A；`cart_2` 不参与 |
| PART_DE/含 D→E 变体 | `agv_1` D→E | `cart_2` D→E |
| MVP | 无 AGV | 无 AGV |

结论：

- **15 个变体作为目录覆盖 A/B/C/D/E 五台臂和 `transport_BA`/
  `transport_DE` 两条运输链**；
- 同时使用五台臂的变体是 `final_alt_kit`；
- 同时使用两辆小车载货的只有 `final_alt_kit` + v005 + `dual`；
- `final_direct_kit` + `dual` 只用 `cart_2` 载货；
- MVP 不使用 AGV；
- v002 无 Cart2，所有 full_flow 变体只能 `single`；
- 若要求“订单本身绑定 agv_1/cart_2”，需要扩展 `OrderSpec`/模板，本文
  不做；车队仍由启动对话框选择。

## 9. 版本与数据库

- 新增 `spec.ORDER_VARIANT_VERSION = "v1_order_variants_20261122"`；
  API/health 返回。
- `ORDER_CONTRACT_VERSION`、`OrderSpec` 字段、DAG digest、compiler/
  validator、`PROCESS_MODEL_VERSION`、`SCENE_PROFILE_VERSION` 不变。
- 不新增 `ORDER_SOURCES`、不新增表/列、不做 SQLite 迁移。
- 订单以 `source=manual` 保存，`note="variant:<id>"` 追溯；如未来需要
  结构化 `variant_id`，另立迁移，不在首版。
- 预设/变体定义是代码静态目录，随 `ORDER_VARIANT_VERSION` 发布；不做
  DB 可编辑。

## 10. 代码落点

| 层 | 文件 |
|---|---|
| Domain | 新增 `domain/order_variants.py`（定义/生成/materializer/分类） |
| Service | `application/orders.py`（`list_order_variants`、`create_order_from_variant`） |
| API | `api/server.py`（`GET /api/order-variants`、`POST .../orders`、health） |
| Frontend | `api/types.ts`、`api/client.ts`、`pages/OrdersPage.tsx`、复用 `components/ui.tsx` |
| Tests | `tests/test_v1_order_variants.py`、前端 `OrdersPage`/E2E mock |
| Docs | 本文、`implementation/README.md`、文档登记表、v2 准备度、USAGE/CHANGELOG |

不修改 `domain/orders.py`、`order_validation.py`、`order_compiler.py`、
`persistence/order_models.py` 或迁移脚本。

## 11. 测试与验收

| 层 | 用例 | 规模 |
|---|---|---|
| Domain | 15 个变体唯一；prefix 生成；scope/verification 分类；profile 过滤；目录诊断 | 参数化小型用例 |
| Materializer | 每个变体通过 validator/compiler；目标和端口正确；quantity=2；确定性 digest；歧义端口拒绝 | 每个变体 1 次 + 代表负例 |
| Service | 7 个完整路线变体各创建；代表前缀 2 个；重复 order_id 创建 v2；`note=variant:<id>`；跨 profile/未知/数量超限拒绝；不建 run | 7 + 2 + 负例 |
| API | `GET /api/order-variants` 过滤；`POST` 创建；404/422 映射 | 现有 HTTP 测试风格 |
| Frontend | 目录解析；筛选；创建对话框；arm/transport chips；运输变体禁用生产标记；E2E mock | 1 条组件/API 集成 + 1 条 E2E |
| Regression | 现有 full-flow/MVP/planner/fleet/order 全量 | 不重写同义用例 |

实机验收：

- 四个生产变体已有阶段 7/9 证据，可复用；
- 前缀/中间/运输变体为 compiler-only，不新增物理验收；
- 可选一条 full_flow 绕行 `dual` smoke，验证“建议 dual”与实际
  `transport_BA→agv_1`、`transport_DE→cart_2` 一致；不作为目录功能前置。

## 12. 明确不做

- 不做任意 recipe/DAG 搜索或用户自定义组合；只做内置有限变体。
- 不做 DB 可编辑变体、变体管理页或用户自定义名称。
- 不自动启动仿真；不把创建成功当成运行/物理验收。
- 不新增 `OrderSource`，不迁移数据库。
- 不把 `single/dual` 或 `agv_1/cart_2` 写入 `OrderSpec`；不生成
  fleet 专用订单变体。
- 不把 `transit`/`compiler_only` 变体宣传为生产已验收能力。
- 不改变节点级/全局失败模型、多订单并发或路线并行语义。

## 13. 实施顺序

1. `domain/order_variants.py`：变体定义/生成、scope/verification、15 个
   变体清单 + 纯逻辑测试。
2. `OrderVariantMaterializer`：线性分支 + join + 直接来料；全部变体
   validator/compiler 测试。
3. `OrderService.list_order_variants()` /
   `create_order_from_variant()` + service 测试。
4. API 路由、health、错误映射 + HTTP 测试。
5. 前端类型/client、OrdersPage 目录/筛选/创建对话框 + 单测/E2E。
6. 文档、CHANGELOG、版本与全量回归。

相对工作量：后端约 1.5 天，前端约 1 天，测试/文档约 0.5–1 天；无 DB
迁移。主要风险是前缀变体的端口绑定、数量扩展和验证级别表述。