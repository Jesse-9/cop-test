---
title: 自建订单 DAG 阶段 7 — 双车订单运行接入
type: plan
status: implemented
created: 2026-11-19
updated: 2026-11-20
scope: v1 UFLOW / dual transport fleet / order runner
related:
  - v1-self-built-order-dag-orchestration.md
  - v1-order-dag-phase-03-api-persistence.md
  - v1-order-dag-phase-04-auto-planner.md
  - v1-order-dag-phase-05-frontend.md
  - v1-order-dag-phase-06-simulation-acceptance.md
  - ../../reports/order-orchestration/v1-order-dag-phase-07-dual-fleet.md
  - ../../reports/production-flow/v1-stage-04-capability-scheduling.md
---

# 自建订单 DAG 阶段 7：双车订单运行接入

## 目标与边界

让手工编排订单和 planner 生成订单都能在启动时选择单车或双车运行，并让订单运行中的设备选择、实际执行车辆、资源占用和运行记录一致。首期只接入现有固定分工策略：`agv_1` 承接壳体 B→A 相关物流，`cart_2` 承接支架 D→E 相关物流。订单 DAG 仍表达物料工序与依赖，不增加代表车辆的虚构工序节点。

双车订单仍走 `OrderSpec → validator/compiler → OrderService → order runner → Stage4Coordinator`。planner 继续根据目标和投料生成普通订单 DAG；用户在启动时选择车队模式。首期不让 planner 根据实时车辆状态做派车，不实现 ETA 动态分配，不改变既有 `single` 默认值。当前小车二由运动学执行器按仿真时间更新位姿，不是由物理轮组驱动；界面、报告和验收不得把它描述成已验证的轮式物理控制。

## 当前实现基线与阻断项

- 场景工件 `v005` 已包含 `Cart2` 和 `Cart2_Tray`；代码目录登记 `cart_2` 设备绑定，`transport_DE` 候选设备为 `agv_1` 与 `cart_2`。
- 常规场景 runner 与 CLI 已有 `single/dual` 参数；订单 API 启动请求、`OrderRunCommand` 和 `order_runner` 尚未传递车队模式，因此订单当前落到 coordinator 默认 `single`。
- `FleetConfig` 按工序 kind 固定分车；通用 `plan_ready()` 又独立按候选设备选择并把 `selected_device_id` 锁定到工序实例。必须统一候选过滤/策略与实际执行器选择，不能让记录选中 `agv_1` 而路线实际交给 `cart_2`。
- coordinator 的 `_busy_devices()` 和调度快照主要暴露 `agv_1` 状态；双车下需纳入 `cart_2` 的运行中状态与 dock/到站条件，或明确把这些条件留给 coordinator 的车辆派工层处理。
- 当前 `AGV_ROUTE_REQUIRES_ALL_DOCKS=True` 会使路线占用所有 dock。其效果是保守串行，不能据此声称真实 coordinator 已实现双车并行。是否缩窄锁定范围，必须结合现有通道/站点冲突语义证明不会碰撞或争用。
- `transport_schedule.py` 的 ETA / fixed 对比是离散事件模型。它可作为策略参考和单元测试证据，但不等价于 CoppeliaSim coordinator 的双车实机验收。

上述缺口在合并提交 `bb54cf5 feat(v1): add second transport cart and dual-fleet scheduling` 与当前订单运行链路中可定位。阶段实施前确认工件 `current` 指向含 Cart2 的受保护场景，并记录 catalog、工件、代码 revision。

## 运行契约

### 启动参数

在 `POST /api/orders/{order_id}/versions/{version}/start` 请求体增加可选字段：

```json
{
  "artifact_id": "v1/L5_UFLOW/v005_20261117_110924",
  "max_sim_time": 1200,
  "transport_fleet": "dual"
}
```

`transport_fleet` 仅接受 `single` 或 `dual`，缺省为 `single`，以兼容现有客户端。OrderService 解析并校验后将其冻结在 `OrderRunCommand`；runner 将同一值传给 coordinator。返回的运行记录、状态快照和最终报告都保存实际生效值及固定分工策略版本。未知值在创建 run/启动 worker 前拒绝。

双车启动要检查已加载工件确实有 `Cart2`、`Cart2_Tray` 且场景契约校验通过；缺失对象或目录绑定不兼容时明确失败，不能静默退回单车。`single` 模式保持当前默认行为，并按现有逻辑将 Cart2 停到不妨碍 AGV 的停车位置。

### DAG 和 planner 语义

DAG 描述工序、物料流与依赖关系。例如：

```text
RAW_D → bracket_process → transport_DE → bracket_to_E ┐
                                                       ├→ kit_join → FINAL
RAW_A → housing_direct_A → housing_finish → buffer_E ┘
```

`transport_DE` 已属于登记的 `bracket_route`，无需为双车新增节点或边。手工图与 planner 图在启动时使用相同的 `transport_fleet` 运行选项；planner 可继续解释它选择了哪些工序路线，但不负责选择车辆。运行选项属于一次 run 的执行配置，不改变已保存订单版本和 DAG digest。

## 实施步骤

### 7.1 冻结订单运行配置契约

1. 为车队模式和策略定义唯一合法值及缺省值；首期仅提供 `single`、`dual` 与 dual 固定分工，不暴露尚未接入 coordinator 的 `eta` 选项。
2. 扩展启动 API、请求解析、OrderService 和 `OrderRunCommand`，校验发生在 run 记录创建和 worker 启动之前。
3. 将模式、策略版本、工件 ID、catalog 版本记录到 run 元数据/结果 JSON；状态查询与报告返回相同值。
4. 保持已有请求不带字段时行为不变；不修改不可变订单版本，不让 planner 将运行模式写成 DAG 属性。

主要落点：`api/server.py`、`application/orders.py`、`application/order_runner.py`、运行持久化模型/API types。

### 7.2 对齐设备决策和实际双车执行

1. coordinator 按 `fleet_mode` 构建有效设备候选集。`single` 下运输任务只选择 `agv_1`；`dual` 下按固定分工限制物流工序候选，避免通用 scheduler 选择与 `FleetConfig.vehicle_for()` 冲突。
2. 为每个 transport instance 只记录一次车辆选择，并确保 `selected_device_id`、task/execution device、运行事件、资源 owner 和最终报告中的车辆相同。
3. 调度快照必须反映两车的忙闲、当前 dock、到站/停稳条件。`cart_2` 的条件不得错误复用 `agv_1` 传感器状态；小车二当前使用位姿容差判据，应继续通过统一、可测试的 vehicle adapter 查询。
4. 双车模式下 D/E 装卸工序的 dock 等待、托盘选择和物料在车托盘上的验证必须匹配实际服务车辆；检查 `Cart2_Tray` 与 AGV 托盘之间没有跨车误读。
5. 保留未知设备/能力/绑定不符时显式拒绝或等待的行为；不能在失败后无记录地改派到另一辆车。

主要落点：`application/coordinator.py`、`domain/scheduling.py`、`simulation/transport_fleet.py`、`simulation/execution/arm.py`、`simulation/device_binding.py`。优先复用现有设备候选与 executor 契约，不另造一套订单专用派工模型。

### 7.3 明确路线资源锁定并证明并行边界

1. 梳理 AGV/Cart2 路网、实际 route legs、共享车道和 dock 占用；将“路线行驶必须锁哪些资源”明确为单一规则，覆盖空载调机和载货运输。
2. 当前锁全部 dock 的配置使路线互斥。若改成按路线占用资源，必须保证整条路线原子 acquire、方向冲突被禁止、共用车道与装卸 dock 被正确锁定；禁止逐段边走边申请导致持有等待和死锁。
3. 对不冲突的两条路线证明允许并行；对同车道/同 dock/相向冲突路线证明至少一方等待且不会碰撞。若场景几何或冲突模型不足以安全缩窄锁范围，首期保留保守串行，并将双车收益表述为车辆分工/能力可用，不声称并行提速。
4. 离散事件 ETA 模型仅用来验证策略趋势和极端任务序列，不替代真实路线执行的资源锁与运动学/物理验收。

主要落点：`application/coordinator.py` 路线资源 acquire/release、`simulation/config.py` 路线资源策略及相应 coordinator 测试。只在真实实现和证据需要时调整 `AGV_ROUTE_REQUIRES_ALL_DOCKS`。

### 7.4 接入订单 UI 并保持两类订单一致

1. 在订单详情“确认启动”对话框增加单车/双车分段选项，缺省选择单车；当前场景工件没有 Cart2 时禁用双车并显示后端返回的明确原因。
2. 前端将选项传给唯一订单启动 API；不新增手工订单专属或 planner 专属启动端点。
3. 运行详情展示本次车队模式、策略版本、车辆分工和 transport 实例实际选车；planner 来源、输入和 DAG digest 的显示继续保留。
4. 刷新 run URL 后从服务端恢复模式和设备选择；失败提示区分工件/能力配置拒绝、资源等待和车辆执行失败。

主要落点：`frontend/src/api/types.ts`、`frontend/src/api/client.ts`、订单详情启动控件、运行详情/节点检查器。planner 页面本身无需添加车辆规划逻辑。

### 7.5 精简验证与 CoppeliaSim 验收

先运行受影响的订单 service/API、调度/coordinator、fleet 与前端契约测试，再运行一次全量非仿真回归。新增测试只覆盖本阶段引入的参数边界、设备一致性和路线冲突语义，不重复阶段 1–6 的 DAG/失败传播矩阵。

CoppeliaSim focused 验收按以下用例执行：

1. **single 回归：**使用既有 `RAW_A × 1 + RAW_D × 1 → FINAL × 1` 订单，不传 `transport_fleet`；确认仍走 AGV、Cart2 停车、物料谱系与订单终态保持基线。
2. **dual 手工订单：**在订单 UI 手工编排上述 7 节点、6 条边订单，启动时选 dual。确认工件含 Cart2，支架 D→E 运输实际由 Cart2 完成，壳体 B→A 相关物流由 AGV 完成，E 合流生成一个 `FINAL`，物料 UID 父子关系正确。
3. **dual planner 订单：**Planner 使用相同目标和投料生成 `bracket_route` 与 housing route，保存普通订单后在同一启动控件选择 dual；核对 planner DAG digest 不因运行模式改变，真实选车与固定分工记录一致。
4. **资源冲突：**用 coordinator harness 构造共享 lane/dock 冲突与一组无冲突任务，验证冲突方等待、无冲突路线可并行（仅当实施了资源锁缩窄）；资源释放后等待任务能继续。若保守全 dock 锁仍保留，只验证冲突安全等待，并在报告中记为暂不提供并行证明。
5. **配置拒绝：**至少验证非法模式、dual 工件缺少 Cart2、`Cart2_Tray` 缺失/绑定不符在启动前失败，不产生伪造 RUNNING 运行。

通过标准：所有成功 run 的订单终态、订单版本/DAG digest、车队配置和实际执行设备一致；目标数量、UID 谱系、节点状态、资源释放和生命周期清理通过。安全/基础设施故障继续遵循阶段 2 的 GLOBAL 规则。失败/跳过/无法确认的用例单独报告，不以纯策略测试代替仿真结果。

## 分阶段交付与依赖

| 子阶段 | 交付 | 进入条件 |
|---|---|---|
| A. 运行配置透传 | API/service/worker/runner/coordinator 接通 `single/dual`，run 元数据可追溯 | 请求校验和旧客户端缺省行为测试通过 |
| B. 真实派工一致性 | 候选过滤、selected device、实际执行器、双车 dock/托盘状态一致 | coordinator harness 证明固定分工与等待语义 |
| C. 路线并发边界 | 明确 route resource 契约；要么证明安全并行，要么保留保守串行 | 冲突用例与资源释放验证通过 |
| D. UI 与仿真验收 | 手工/planner 订单共用运行选项，single/dual 实机证据和报告 | A–C 完成；当前工件通过生命周期校验 |

A、B 是订单双车运行的必要条件。C 若不能安全证明并行，可保守保持串行，不阻塞固定分工双车订单接入；但不得宣传双车并行收益。D 是对用户开放功能的发布门槛。

## 实施状态（2026-11-20）

子阶段 A–D 的代码、API 与启动 UI 已落地：`transport_fleet` 在创建 run 前校验并冻结到 `OrderRunCommand`，运行记录新增 `transport_fleet` / `fleet_policy_version` 列（`0007_order_run_fleet`，历史行回填 `single`）；coordinator 按固定分工收窄运输候选、统一 `selected_device_id`/执行器/事件/资源 owner 的车辆记录，并按车辆查询 dock/停稳状态；双车启动前检查工件 manifest 的 `Cart2`/`Cart2_Tray`；订单详情启动对话框和运行详情已展示车队模式与分工。

路线资源首期仍按 `AGV_ROUTE_REQUIRES_ALL_DOCKS` 全有或全无锁定，任意两条路线互斥，因此**没有并行提速证据**；`cart_2` 仍是运动学执行器，不是物理轮式控制。

CoppeliaSim focused 验收已在受保护 `v005` 工件上执行：single 回归、dual 手工订单（B→A + D→E）和 dual planner 订单均 `SUCCEEDED` 并生成 1 个 `FINAL`；dual 下 `transport_BA → agv_1`、`transport_DE → cart_2`，planner DAG digest 不随模式变化。真实 UI 手工订单也已选择双车启动成功，运行页刷新后保持一致；planner 订单的真实 UI 启动、路线锁缩窄后的冲突矩阵和配置拒绝的仿真器内重复未执行。用例 5 由服务/API 测试、真实 `v001` manifest 预检和 CLI 拒绝（不新增 run）验证。执行记录和证据见[阶段 7 实施报告](../../reports/order-orchestration/v1-order-dag-phase-07-dual-fleet.md)。

## 不纳入本阶段

- planner 根据实时车辆位置、队列、健康、故障和 ETA 动态选车。
- 自动故障降级、重试、运行中换车或将已派发节点改派给另一车辆。
- 改写订单 DAG 来表达车辆实体，或复制一套手工/planner 专属运行 API。
- 在未完成路线冲突和 CoppeliaSim 验收前宣称真实双车并行、吞吐提升或物理轮式控制。
