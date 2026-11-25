---
title: 自建订单 DAG 阶段 8 — 车队感知的自主规划扩展
type: plan
status: implemented
created: 2026-11-21
updated: 2026-11-21
scope: v1 UFLOW / planner / fleet context
related:
  - v1-self-built-order-dag-orchestration.md
  - v1-order-dag-phase-07-dual-fleet.md
  - v1-order-dag-phase-04-auto-planner.md
  - ../production-flow/v1-stage-04-capability-scheduling-implementation.md
  - ../../reports/order-orchestration/v1-order-dag-phase-07-dual-fleet.md
  - ../../reports/order-orchestration/v1-order-dag-phase-08-fleet-aware-planner.md
---

# 自建订单 DAG 阶段 8：车队感知的自主规划扩展

## 目标与边界

让 planner 在生成订单草稿时**使用车队运行上下文**：`single` / `dual` 模式、
固定分工和车辆能力作为规划假设参与路线可行性判断、候选排序和解释输出。
目标是消除“planner 生成的 DAG 与随后启动的车队模式相互不知情”的断层，
同时保持订单 DAG 只表达物料工序与依赖。

首期不把车辆写成订单节点或边，不在 `OrderSpec` 中保存派车结果，也不让
planner 替代 coordinator 做实际派工。planner 输出仍是普通不可变订单版本；
运行时的设备选择、资源锁和故障语义继续由 coordinator 权威决定。

以下能力**不进入首期扩展**：

- 按实时车辆位置、队列、健康、故障或 ETA 动态选车；
- 运行中换车、重试或将已派发节点改派另一车辆；
- 用 planner 的静态 ETA 模型替代真实路线资源锁或运动学验收；
- 在未完成路线冲突验收前宣称双车并行、吞吐提升或轮式物理控制。

## 当前基线与缺口

- `application/planner.py` 只消费 `PlannerInput`（目标、投料、场景、
  `allowed_template_ids`、`allowed_device_ids`、路线偏好等），没有
  `transport_fleet` 字段；planner 策略版本为 `v1_planner_20261107`。
- planner 以 `RouteVariant` 为路线宏，按模板/场景/允许设备过滤，再做
  有界搜索和评分；它不选择具体设备，生成的 `OrderSpec` 不包含设备字段。
- `transport_DE` 的静态候选为 `("agv_1", "cart_2")`；编译后的实例保留
  两个候选，`selected_device_id` 为空，由 coordinator 在运行时按
  `FleetConfig.vehicle_for(kind)` 收窄并选择。
- 阶段 7 已验证 planner 订单可选择 `dual` 启动，`transport_DE` 由
  `cart_2` 实际执行，DAG digest 不随运行模式改变；但 planner 的候选排序
  和 explain 完全不感知车队模式。
- `simulation/transport_schedule.py` 已有离散事件双车模型
  （`DualCartSimulator` / `compare_policies`），可用于静态 ETA 对比和
  单元测试，但它不是 CoppeliaSim 路线执行或碰撞验收的替代。
- 当前车队契约（模式、分工、已验证组合）位于
  `simulation/transport_fleet.py`，planner 直接依赖它会引入仿真执行层；
  扩展前应把纯策略部分抽到 `domain` 层。

缺口本质：**planner 选择“工艺上可行的路线”，但没有选择“在给定车队
编制下更合适的路线/分支”，也没有把车队假设写入 plan explain。**
运行时仍能正确处理 dual，但用户无法在规划阶段比较 single/dual 的候选
差异，也无法在计划中看到“该草稿假设哪台车承运哪类运输”。

## 规划契约扩展

### 8.1 PlannerInput

在 `PlannerInput` 增加可选字段：

```json
{
  "target_item_type": "FINAL",
  "target_quantity": 1,
  "inputs": [{"item_type": "RAW_A", "quantity": 1},
             {"item_type": "RAW_D", "quantity": 1}],
  "scene_id": "L5_UFLOW",
  "transport_fleet": "dual",
  "fleet_assumptions": {
    "available_vehicle_ids": ["agv_1", "cart_2"]
  }
}
```

- `transport_fleet` 仅接受 `single` / `dual`，缺省 `single`，与订单启动
  API 使用同一规范化和错误码。
- `fleet_assumptions.available_vehicle_ids` 是**静态假设**，只用于规划
  可行性/排序，不是实时状态。缺省为当前编制包含的车辆；显式列出的不可用
  车辆会在路线评估中标记，不触发运行期改派。
- `allowed_device_ids` 语义保持不变：它是 planner 的静态允许设备过滤，
  不选择具体执行车辆。
- 输入仍写入 `order_version.planner_input_json`，因此 planner 使用的
  `transport_fleet` 自动可追溯；不新增订单 schema 字段。

### 8.2 纯车队策略契约

把 `FleetConfig` 中的模式、分工、已验证组合和 `vehicle_for(kind)` 迁到
新的 `domain/fleet_policy.py`（只依赖 `spec` / `domain`，不导入
CoppeliaSim、执行器或资源表）。`simulation/transport_fleet.py` 与
`application/coordinator.py` 继续从该模块导入/重导出，保持现有行为与
测试兼容。`FLEET_POLICY_VERSION` 仍由 `spec.py` 唯一维护。

planner 通过该纯契约计算：

```python
FleetConfig(mode).vehicle_for(template.kind)
FleetConfig(mode).assignment_for_route(route_operation_ids)
```

不复制一份 planner 专用分工表。

### 8.3 plan explain 与诊断

`PlannerResult.explanation` 增加只读块：

```json
"fleet_context": {
  "mode": "dual",
  "policy_version": "v1_fleet_20261119",
  "assignment": {"transport_ba": "agv_1", "transport_de": "cart_2"},
  "available_vehicle_ids": ["agv_1", "cart_2"],
  "route_vehicles": [{"route_id": "bracket_route",
                      "kind": "transport_de", "vehicle_id": "cart_2"}],
  "assumptions": ["static_catalog_assignment", "no_realtime_vehicle_state"]
}
```

新增稳定诊断码（沿用现有 `PlannerDiagnostic` 结构）：

| code | 含义 |
|---|---|
| `PLANNER_FLEET_MODE_INVALID` | `transport_fleet` 不是 `single`/`dual` |
| `PLANNER_FLEET_ASSIGNMENT_UNAVAILABLE` | 分工车辆不在候选或静态可用集合 |
| `PLANNER_FLEET_ASSUMPTION_ONLY` | 仅记录假设、未做实时可用性判断 |
| `PLANNER_FLEET_ROUTE_PENALIZED` | 候选因车队假设被降权（不是拒绝） |

不可行/降权必须区分：路线仍可在运行时执行但 planner 假设不满足时，默认
记录 warning 并保留候选，不静默删除，除非显式 `strict_fleet=true`。

### 8.4 DAG 与运行模式

- planner 输出仍走 `OrderSpec → validator → compiler`；DAG 节点/边、digest
  不因 `transport_fleet` 改变。
- planner 不写 `selected_device_id`；编译实例继续保留静态候选，由运行时
  coordinator 收窄。
- planner 选择的模式只是**规划假设**；启动时仍由用户在启动对话框独立
  选择 `single/dual`。服务端在 run 元数据记录实际运行模式，planner
  元数据记录规划假设；两者不一致时运行仍按实际模式执行，并在报告
  `fleet.planner_assumption_mismatch` 标记，不静默改变订单。
- 若后续需要“规划假设与运行模式必须一致”，由 API 增加显式校验开关，
  不改变默认兼容行为。

## 实施步骤

### 8.1 抽取纯车队策略

1. 新建 `domain/fleet_policy.py`，迁入 `FLEET_SINGLE`/`FLEET_DUAL`、
   `FLEET_MODES`、`normalize_fleet_mode`、`FleetConfig`、固定分工表、
   `DUAL_VERIFIED_PAIRS`、`policy_version`。
2. `simulation/transport_fleet.py` 从该模块重导出；coordinator、
   `OrderService`、API/CLI 的导入路径保持不变或显式改为 domain 路径。
3. 保持 `spec.FLEET_POLICY_VERSION` 唯一来源；不改变现有运行行为、DB 列或
   运行报告字段。

主要落点：`domain/fleet_policy.py`（新增）、`simulation/transport_fleet.py`、
`application/orders.py`、`api/server.py`、`cli.py`。

### 8.2 扩展 planner 输入与入口

1. `PlannerInput` 增加 `transport_fleet`、`fleet_assumptions`，严格校验
   未知字段/非法值；不改变现有调用方缺省行为。
2. `application/planner.py` 在搜索前构建 `FleetConfig`；无车队字段时等价
   于 `single`。
3. CLI `order plan` 增加 `--transport-fleet single|dual`。
4. API `POST /api/planner/plan`、`/api/planner/drafts` 接受同一字段；
   与 start 使用相同 `ORDER_RUN_CONFIG`/参数错误语义。
5. 前端 planner 页增加“规划车队假设”选择（默认 single），与启动对话框
   的运行模式独立；`types.ts` / `client.ts` 同步。

主要落点：`application/planner.py`、`cli.py`、`api/server.py`、
`frontend/src/pages/PlannerPage.tsx`、`frontend/src/api/types.ts`。

### 8.3 车队感知的路线可行性

1. 对每个 `RouteVariant` 的运输模板计算固定分工车辆，检查车辆是否在
   `candidate_devices` 内；不满足时记录
   `PLANNER_FLEET_ASSIGNMENT_UNAVAILABLE` 并按策略拒绝或降权。
2. 对臂工序的 `source_kind/sink_kind=agv`，记录其依赖车辆；不改变模板
   候选设备，但把车辆假设写入 explain。
3. `allowed_device_ids` 与车队假设同时存在时，二者取交集；交集为空才
   拒绝路线，避免用允许设备列表静默覆盖车队分工。
4. 不修改 `OrderSpec`、compiler 或 DAG digest；可行性结果只影响 planner
   候选排序/解释。
5. 现有规划搜索预算、路线宏、合流绑定和编译验证全部复用。

主要落点：`application/planner.py`、`domain/fleet_policy.py`。

### 8.4 静态 ETA 评分（可选子阶段）

1. 对每个候选分支，把其运输模板映射为
   `simulation/transport_schedule.TransportJob`，使用
   `DualCartSimulator` 在给定编制下推演 makespan/等待。
2. 只在**同分候选**间作为 tie-breaker，并在 explain 中记录模型版本、
   车辆假设和“离散事件模型，不是物理验收”。
3. 评分受独立策略开关控制；未启用时排序与阶段 4 完全一致。
4. 不同时启用“按实时状态派车”；ETA 结果不改变运行时 coordinator 的
   派工与资源锁。

主要落点：`application/planner.py`、`simulation/transport_schedule.py`
（只读复用）、planner 单元测试。

### 8.5 验证

纯逻辑测试覆盖：

- `transport_fleet` 缺省/非法值、CLI/API 参数边界；
- single/dual 下同一目标/投料的候选 explain 差异；
- 车队分工车辆不在候选、静态不可用时诊断码；
- planner DAG digest 在 single/dual 假设下保持一致；
- `fleet_context` 可 JSON 序列化、无仿真句柄；
- 启动实际模式与规划假设不一致时的标记（若实施该标记）。

CoppeliaSim focused 验收：

1. planner 以 `single` 假设保存订单，启动 `single`，基线路线/选车不变；
2. planner 以 `dual` 假设保存订单，启动 `dual`，`transport_DE` 由 `cart_2`
   执行，DAG digest 与 `single` 假设保存时一致；
3. 规划假设 `dual`、实际启动 `single`，运行按 `single` 执行且报告标记
   不一致，不自动改单；
4. planner explain 的 `fleet_context` 与运行报告的实际车辆对照一致。

不重复阶段 4/7 的目标/投料、失败隔离或资源锁矩阵。

## 分阶段交付与依赖

| 子阶段 | 交付 | 进入条件 |
|---|---|---|
| A. 纯策略抽取 | `domain/fleet_policy.py`，现有行为/测试不变 | 全量回归通过；无导入环 |
| B. 输入与入口 | planner API/CLI/UI 接受车队假设，缺省兼容 | 参数边界与旧客户端测试通过 |
| C. 可行性/解释 | `fleet_context`、诊断码、DAG digest 不漂移 | planner 候选对照测试通过 |
| D. 静态 ETA | 可选 tie-breaker 与 explain 标记 | 独立开关；模型/单元测试通过 |
| E. 仿真实机验收 | planner single/dual 与假设不一致用例 | A–C 完成；D 可选 |

A、B 是基础；C 是“双车进入自主规划”的最小可用交付；D 不阻塞 C，且不得
据此宣称实时派车或并行提速；E 是发布门槛。

## 版本与兼容

- `ORDER_PLANNER_POLICY_VERSION` 在 C/D 改变候选排序或 explain 语义时递增
  （例如 `v1_planner_20261121`），并写入 planner 元数据。
- `FLEET_POLICY_VERSION` 只在分工表/已验证组合变化时递增；抽取模块本身
  不改变版本。
- 订单契约版本、场景契约和 DAG digest 不变；旧订单/旧客户端缺省
  `transport_fleet=single` 的行为不变。
- 旧 planner 输入/记录缺少 `fleet_context` 时按空块读取，不伪造历史假设。

## 风险与回滚

| 风险 | 处理 |
|---|---|
| planner 与 coordinator 对同一模式理解不一致 | 共用 `domain/fleet_policy.py` 和 `FLEET_POLICY_VERSION`；启动报告记录实际模式并标记假设差异 |
| 静态 ETA 被误解为物理/并行证明 | explain/报告显式标注离散事件模型；不进入运行验收结论 |
| 规划阶段删除“运行时本可执行”的候选 | 默认只降权+warning，strict 模式才拒绝；诊断可追溯 |
| 车队模式进入订单 digest 导致版本漂移 | `OrderSpec` 不新增字段；planner 输入/假设只进 planner 元数据 |
| 抽取模块引入导入环 | 新模块只依赖 `spec`/`domain`；仿真/应用层单向重导出 |

回滚：关闭 planner 车队字段（缺省 single）即可恢复阶段 4 排序；纯策略抽取
保留重导出时不影响现有运行路径。

## 不纳入本阶段

- 实时车辆状态、队列、健康、故障和 ETA 驱动的动态选车/改派；
- planner 直接生成派车计划或资源锁序列；
- 运行中重试、换车、故障降级；
- 在路线冲突和 CoppeliaSim 验收前声明双车并行或吞吐提升；
- 把 `cart_2` 描述为已验证的轮式物理控制。

## 实施状态（2026-11-21）

子阶段 A–D 已实施：`domain/fleet_policy.py` 提供纯车队契约；
`PlannerInput` 支持 `transport_fleet`、静态 `fleet_assumptions`、
`strict_fleet` 和可选 `fleet_eta_ranking`；planner 输出
`fleet_context`、车队诊断和候选降权，DAG digest 不随假设变化；CLI 与前端
planner 页可设置规划车队假设。运行报告记录 planner 假设，并在与实际运行
模式不一致时输出 `planner_assumption_mismatch`，仍按实际模式派工。

`ORDER_PLANNER_POLICY_VERSION` 升至 `v1_planner_20261121`。自动化测试
737 passed / 8 skipped，前端 13 passed、E2E 4 passed。CoppeliaSim 验收
通过 planner dual 假设 + dual 运行与 planner dual 假设 + single 运行
（均 1 个 `FINAL`，后者正确标记不一致）。执行记录、边界和未覆盖项见
[阶段 8 实施报告](../../reports/order-orchestration/v1-order-dag-phase-08-fleet-aware-planner.md)。