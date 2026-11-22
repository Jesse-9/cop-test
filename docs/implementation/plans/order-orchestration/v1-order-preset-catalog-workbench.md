---
title: v1 订单预设目录与工作台直连方案
type: plan
status: planned
created: 2026-11-22
updated: 2026-11-22
scope: v1 UFLOW / order workbench / planner / scene profile / fleet config
related:
  - v1-order-dag-phase-09-scene-profile-order-catalog.md
  - v1-order-dag-phase-08-fleet-aware-planner.md
  - v1-order-dag-phase-07-dual-fleet.md
  - ../../reports/order-orchestration/v1-order-dag-phase-09-scene-profile-order-catalog.md
  - ../../reports/order-orchestration/v1-order-dag-phase-07-dual-fleet.md
  - ../production-flow/v1-neo4j-assembly-mixed-line-mvp.md
---

# v1 订单预设目录与工作台直连方案

## 1. 目标与结论

在订单工作台增加“可用工艺路线”区域：把当前已登记、已验证的四个订单
蓝图包装成有名称、有说明、可筛选的 `OrderPreset`，用户可在订单列表页
直接生成普通订单，再进入现有预览/启动/报告链路。

首版范围：

- 只做**代码内置、只读**的预设目录；
- 只做**创建并保存订单**，不自动启动仿真；
- 不为预设新增数据库表，不新增 `OrderSource`，不做运行中动态改派。

结论：**不是只改数据库，且推荐方案不需要数据库迁移。**

原因：

1. 只改数据库无法生成订单、暴露 API 或出现前端入口；数据库不是执行入口。
2. `route_variant` 表是代码目录的只读发布镜像，没有 `display_name`；
   `runtime_catalog()` 要求数据库与代码目录逐字段等价，单独加表/字段会在
   下一次发布或等价性校验中失效。
3. 一条 `RouteVariant` 不是完整订单：`FINAL` 需要“壳体路线 + 支架路线 +
   `kit_join`”，MVP 需要直接来料逻辑节点；必须有服务端组合与校验逻辑。
4. 现有 `order_version.planner_input_json` 已能保存 `preset_id`，无需新列/
   新表；预设创建仍走 planner + validator/compiler + 订单版本仓储。

## 2. 现状与约束

| 现状 | 影响 |
|---|---|
| `domain/process_model.py` 的 `RouteVariant` 只有 `id/group/semantics/entry_zone/exit_zone/operation_ids` | 缺少用户可读名称、目标产物和完整订单语义 |
| `domain/default_process.py` 已登记 `housing_direct`、`housing_alt`、`bracket_route` 与两个 MVP 直接来料 recipe | 四条完整蓝图已存在，预设只做命名包装，不新增工艺 |
| `application/planner.py` 已支持按目标反向组合 route/join、`scene_profile` 和直接来料 join | 直接复用 planner，不再写第二套 DAG 构造器 |
| `persistence/repository.py` 从目录 DB 重建 `ProcessCatalog` 时不读取显示名/预设 | 预设定义应放代码 domain；目录 DB 不承担工作台展示元数据 |
| `frontend/src/pages/OrdersPage.tsx` 只展示已保存订单 | 需要新增“可用工艺路线”区域和创建动作 |
| `order_version.planner_input_json` 已存在 | `preset_id` 可直接落库，无需迁移 |

约束：

- 预设只能引用目录中已登记的模板/路线/区域；不允许任意 recipe、脚本或
  未登记执行器。
- 预设创建出的订单必须通过现有 `OrderValidator`/`OrderDagCompiler`；
  planner 签名不一致时拒绝，不静默修补。
- 预设不改变 `OrderSpec` 契约、DAG digest 算法或运行时派工语义。

## 3. 数据模型：`OrderPreset`

新增 `domain/order_presets.py`，只保存有限、封闭的预设定义与校验，不导入
CoppeliaSim、SQLAlchemy 或 application 层。

```python
OrderPreset(
    preset_id="final_housing_direct",
    display_name="壳体直达 + 支架齐套 → FINAL",
    description="RAW_A 直达 + RAW_D 支架路线，在 E 岛 kit_join 合流",
    scene_profile="full_flow",
    target_item_type="FINAL",
    target_quantity=1,
    inputs=(
        PresetInput("RAW_A", "line_s1a", 1),
        PresetInput("RAW_D", "line_s2d", 1),
    ),
    route_ids=("housing_direct", "bracket_route"),
    template_ids=(...),  # 完整节点链
    join_template_id="kit_join",
    recommended_transport_fleet="single",  # 仅展示提示，不写入订单
)
```

字段约束：

- `preset_id`：稳定 ASCII 标识，匹配订单 ID 字符集；全目录唯一。
- `display_name`/`description`：面向工作台的中文名称与说明。
- `scene_profile`：`full_flow` 或 `assembly_mvp`。
- `target_item_type`/`target_quantity`：默认目标与数量；数量可由用户覆盖。
- `inputs`：每种投料、区域、每目标数量；区域必须是可生成来料的区域。
- `route_ids`/`template_ids`/`join_template_id`：完整订单签名，用于锁定
  planner 搜索空间并检测目录漂移。
- `recommended_transport_fleet`：`single`/`dual`/空；**仅前端提示**，
  不写入 `OrderSpec`/DAG，也不覆盖启动对话框的实际选择。

## 4. 首批四个预设

| preset_id | 展示名 | profile | 目标 | 投料 | 路线/模板 |
|---|---|---|---|---|---|
| `final_housing_direct` | 壳体直达 + 支架齐套 → FINAL | `full_flow` | `FINAL` × 1 | `RAW_A@line_s1a`、`RAW_D@line_s2d` | `housing_direct` + `bracket_route` + `kit_join`（7 节点） |
| `final_housing_alt` | 壳体绕行 + 支架齐套 → FINAL | `full_flow` | `FINAL` × 1 | `RAW_B@line_s1b`、`RAW_D@line_s2d` | `housing_alt` + `bracket_route` + `kit_join`（9 节点） |
| `mvp_compressor_pair` | 压缩机 + 底板 → 压缩机装配体 | `assembly_mvp` | `COMPRESSOR_ASSY` × 1 | `COMPRESSOR@mvp_source_compressor`、`BASEPLATE@mvp_source_baseplate` | `mvp_compressor_assembly`（直接来料） |
| `mvp_flange_pair` | 前法兰 + 后法兰 → 法兰装配体 | `assembly_mvp` | `FLANGE_ASSY` × 1 | `FRONT_FLANGE@mvp_source_front_flange`、`REAR_FLANGE@mvp_source_rear_flange` | `mvp_flange_assembly`（直接来料） |

不把单独的 `housing_direct`/`housing_alt`/`bracket_route` 当作可下单预设：
它们不是完整订单目标；只作为预设卡片中的“包含路线”详情展示。若未来需
单路线调试订单，应另立有限的调试入口，不混入生产预设。

## 5. 生成与保存机制

`OrderService.create_order_from_preset(preset_id, body, artifact_id="")`：

1. 取当前 `SceneCapabilities`，按 profile/目标/投料/模板/路线做结构可用性
   检查；不可用返回 `ORDER_PRESET_UNAVAILABLE` 及原因。
2. 构造 `PlannerInput`：
   - `target_item_type`/`target_quantity`；
   - `inputs` 按目标数量生成足够 supply；
   - `allowed_template_ids = preset.template_ids`，锁定节点集合；
   - `preferred_route_ids = preset.route_ids`；
   - `scene_profile = preset.scene_profile`；
   - `preset_id`（新增可选字段）。
3. 调用现有 `OrderPlanner.plan()`。
4. 校验选中分支的 `route_ids`/`template_ids` 与预设签名一致；不一致返回
   `ORDER_PRESET_SIGNATURE`，不保存。
5. 复用 `plan_and_save()` 的保存逻辑写入普通 `source=planner` 订单版本；
   `preset_id` 保存在 `planner_input_json`。
6. 返回 `saved_order`、`preview`、`digest`；前端跳转订单详情，启动仍走
   现有启动对话框。

重构建议：

```python
def plan_and_save(payload, *, note=""):
    result = self.plan(payload)
    return self._save_plan_result(result, note=note)

def _save_plan_result(self, result, *, note=""):
    ...  # 现有保存逻辑
```

预设创建只规划一次，再调用 `_save_plan_result()`，避免重复搜索和物化。

## 6. API 契约

### 6.1 列表

```http
GET /api/order-presets?artifact_id={ref}
```

```json
{
  "ok": true,
  "artifact_id": "v1/L5_UFLOW/v005_20261117_110924",
  "scene_profile": "full_flow",
  "profile_source": "default",
  "preset_version": "v1_order_presets_20261122",
  "presets": [
    {
      "preset_id": "final_housing_direct",
      "display_name": "壳体直达 + 支架齐套 → FINAL",
      "description": "RAW_A 直达 + RAW_D 支架路线，在 E 岛 kit_join 合流",
      "scene_profile": "full_flow",
      "target": {"item_type": "FINAL", "quantity": 1},
      "inputs": [
        {"item_type": "RAW_A", "zone": "line_s1a", "quantity": 1},
        {"item_type": "RAW_D", "zone": "line_s2d", "quantity": 1}
      ],
      "route_ids": ["housing_direct", "bracket_route"],
      "template_ids": ["housing_direct_A", "housing_finish",
                       "housing_to_E_buffer", "bracket_process",
                       "transport_DE", "bracket_to_E", "kit_join"],
      "join_template_id": "kit_join",
      "recommended_transport_fleet": "single",
      "available": true,
      "unavailable_reason": ""
    }
  ]
}
```

### 6.2 直接创建

```http
POST /api/order-presets/{preset_id}/orders
{
  "order_id": "preset-final-direct-20261122",
  "quantity": 1,
  "note": "from workbench preset"
}
```

响应复用现有 planner 草稿形状：

```json
{
  "ok": true,
  "preset_id": "final_housing_direct",
  "saved_order": {"order_id": "...", "version": 1, "dag_digest": "..."},
  "order": {...},
  "preview": {...}
}
```

错误：

| 场景 | HTTP/code |
|---|---|
| 未知 preset | 404 `ORDER_PRESET_UNKNOWN` |
| 当前 profile/工件不可用 | 422 `ORDER_PRESET_UNAVAILABLE` |
| planner 无解 | 422 `ORDER_PRESET_PLAN` |
| 选中分支与预设签名不一致 | 422 `ORDER_PRESET_SIGNATURE` |
| `order_id`/quantity 非法 | 沿用 `ORDER_SPEC`/`ORDER_RUN_CONFIG` |

首版不做 `preview` 独立端点；创建响应已包含 `preview`。如前端需要“查看
节点”再单独预览，可在后续加 `POST /api/order-presets/{id}/preview`。

## 7. 前端工作台

在 `frontend/src/pages/OrdersPage.tsx` 顶部、订单表格之前增加“可用工艺
路线”区域；空订单状态之前也可见。

- 卡片布局，复用现有 `panel`/`template-card` 样式；四个预设桌面两列、
  窄屏单列。
- 卡片展示：展示名、说明、目标×数量、投料（物料@区域）、包含路线/模板、
  profile、`recommended_transport_fleet` 提示、可用状态。
- 不可用卡片禁用创建按钮并显示 `unavailable_reason`。
- `profile_source=unbound` 时显示提示：当前服务未绑定工件，创建后启动仍
  需匹配 profile 的工件。
- 创建按钮打开轻量对话框：订单 ID（预填
  `preset-<preset_id>-<yyyyMMdd>`，可编辑）、数量、备注；提交后跳转
  `/orders/{order_id}`。
- 已保存订单若来自预设，可从 `version.planner.input.preset_id` 读取并显示
  “预设”徽标；`OrdersPage` 已逐个读取 `getVersion`，无需新增 API 字段。
- 首版**不自动启动**；启动仍由订单详情的 `StartRunDialog` 选择工件和
  `single/dual`。

前端类型与客户端：

- `api/types.ts`：`OrderPresetSummary`、`OrderPresetCreateBody`、
  `OrderPresetCreateResponse`。
- `api/client.ts`：`orderPresets(artifact?)`、
  `createOrderFromPreset(presetId, body)`；在解析边界校验 preset/route/
  template/item 结构，未知值抛 `INVALID_RESPONSE`。

## 8. 版本与数据库

推荐方案：

- 新增 `spec.ORDER_PRESET_VERSION = "v1_order_presets_20261122"`；
  API/health 返回该版本。
- `PlannerInput` 增加可选 `preset_id`；`explain`/`input_summary`
  记录；`ORDER_PLANNER_POLICY_VERSION` 升至 `v1_planner_20261122a`。
- 订单 `source` 保持 `planner`；不新增 `ORDER_SOURCES`/DB CHECK 迁移。
- `preset_id` 存入现有 `order_version.planner_input_json`；不新增表/列。
- 订单契约、`OrderSpec` 字段、DAG digest 算法、compiler/validator 不变。
- `PROCESS_MODEL_VERSION`/`SCENE_PROFILE_VERSION` 不变（预设不是新工艺）。

不做 DB 可编辑预设。若未来需要用户自定义名称/组合，应另立一个独立的
预设存储与发布机制；不应把可编辑数据写入当前只读静态目录 DB。

## 9. 可用工件与两辆小车的覆盖

这是本方案必须明确的边界：**四个订单蓝图覆盖产品和工艺组合，不覆盖
车队配置。**

### 9.1 可用工件

当前可用工件：

| 工件 | profile | Cart2 | 可用预设 |
|---|---|---|---|
| `v1/L5_UFLOW/v005_20261117_110924` | `full_flow` | 有 | 两个 full_flow 预设 |
| `v1/L5_UFLOW/v002_20261121_152334`（实验） | `assembly_mvp`（含 v1 底图） | 无 | 两个 full_flow + 两个 MVP 预设 |

历史 `v1/L5_UFLOW/v001_20261103_004317` 与 MVP 旧实验工件
`v001_20261114_120734` 保留作追溯，不属于当前可用工件；后者还会因旧
config digest 在启动前校验失败。预设目录以 `current`/显式可用工件为准。

因此：

- 四个预设作为整体覆盖当前两个可用工件和四种订单蓝图；
- 但**不是每个工件都显示全部四个**：v005 只显示两个 full_flow 预设，
  v002 显示全部四个；跨 profile 预设按能力目录过滤，创建 API 也会再次
  拒绝。
- 若要求“v005 也显示 MVP 预设”，那会生成无法在该工件运行的订单，不应做。

### 9.2 两辆小车

`transport_fleet`（`single`/`dual`）是**运行配置**，不是 `OrderSpec`/预设
字段；预设只描述订单 DAG。四个预设与车队的实际关系：

| 预设 | 工件 | `single` | `dual` |
|---|---|---|---|
| full_flow 直达 | v005 / v002* | `agv_1` 承运 D→E | `cart_2` 承运 D→E；`agv_1` 不承担本单载货运输 |
| full_flow 绕行 | v005 / v002* | `agv_1` 承运 B→A + D→E | `agv_1` 承运 B→A；`cart_2` 承运 D→E（**两车都参与载货运输**） |
| MVP 压缩机 | v002 | 无 AGV 运输（逻辑装配） | 不可用（工件无 Cart2） |
| MVP 法兰 | v002 | 无 AGV 运输（逻辑装配） | 不可用（工件无 Cart2） |

\* v002 无 Cart2，`dual` 不可用；full_flow 预设在该工件只能使用 `single`。
上表 `dual` 列仅适用于 v005。

结论：

- **四个蓝图本身没有“包含两辆小车”**；两车是否使用由启动时选择的
  `transport_fleet` 决定。
- 只有 **full_flow 绕行 + v005 + `dual`** 同时使用 `agv_1`（B→A）和
  `cart_2`（D→E）承担载货运输。
- full_flow 直达 + `dual` 只用 `cart_2` 载货；`single` 只用 `agv_1`；
  MVP 两例不使用 AGV。
- 预设可带 `recommended_transport_fleet` 展示提示（建议绕行提示 `dual`，
  直达/MVP 提示 `single`），但它不写入订单、不改变 DAG，也不覆盖启动
  对话框的实际选择。
- 该提示不是物理/验收保证：当前 v005 的 single full-flow 在 E 臂 AGV
  托盘对位仍有既有 `GOAL_CONFIG_INVALID` 风险（见阶段 9 报告）；实际启动
  时可按工件能力和运行结果选择 `single`/`dual`。
- 若要求“每个预设都必须演示两车”，应新增独立的**运行演示配置/场景**，
  不要复制成 fleet 专用订单预设；否则会把订单蓝图和运行配置混在一起。

## 10. 测试与验收

| 层 | 用例 | 规模 |
|---|---|---|
| Domain | 四个预设定义唯一；full_flow 能力只返回两个；assembly_mvp 返回四个；route/template 缺失时 `available=false` | 参数化小型用例 |
| Service | 四个预设各创建一次；目标/投料/路线/模板正确；preview/compiler 通过；重复 `order_id` 创建 v2；`preset_id` 写入 planner 元数据 | 4 正例 + 3 负例 |
| Service 负例 | 跨 profile、未知 preset、planner 签名漂移、非法 order_id/quantity | 各 1 条 |
| API | `GET /api/order-presets` 的 artifact/profile 过滤；`POST` 创建；404/422 映射 | 现有 HTTP 测试风格 |
| Frontend | client 解析；OrdersPage 加载预设、创建、跳转；不可用禁用；fleet 提示只展示不写入 | 1 条组件/API 集成 + 1 条 E2E mock |
| Regression | 现有 full-flow/MVP/planner/fleet/order 测试全量 | 不重写同义用例 |

实机验收：

- 预设只是既有四个蓝图的命名包装，创建只调用 planner + compiler，不新增
  物理能力；**不需要新增仿真验收**。
- 阶段 7/9 的 `v005`/`v002` 证据可作为蓝图基线。
- 可选 smoke：在 v005 创建 full_flow 绕行预设并以 `dual` 启动一次，验证
  两车分工提示与实际派工一致；不把该 smoke 作为预设目录的前置条件。

## 11. 明确不做

- 不做数据库可编辑预设、预设管理页或用户自定义名称/组合。
- 不自动启动仿真；不把“创建成功”当成运行/物理验收。
- 不新增 `OrderSource`，不迁移 SQLite；不改变 `OrderSpec`/DAG digest。
- 不把单条 `RouteVariant` 当作完整订单；不实现任意 recipe/直接输入 join
  配方语言。
- 不把 `recommended_transport_fleet` 写入订单或替代启动时的车队选择。
- 不新增多订单并发、动态改派、路线并行或吞吐证明。

## 12. 实施顺序

1. `domain/order_presets.py` + 纯逻辑测试（四个定义、profile 过滤）。
2. `PlannerInput.preset_id` + planner 测试/策略版本。
3. `OrderService` 列表/创建 + `_save_plan_result` 拆分 + service 测试。
4. API 路由 + HTTP 测试。
5. 前端类型/client + OrdersPage 预设区/创建对话框 + 单测/E2E。
6. 文档、CHANGELOG、版本与全量回归。

相对工作量：后端约 1 天，前端约 0.5–1 天，测试/文档约 0.5 天；无 DB
迁移。风险主要在预设签名漂移、数量扩展和“预设/运行配置”边界表述。