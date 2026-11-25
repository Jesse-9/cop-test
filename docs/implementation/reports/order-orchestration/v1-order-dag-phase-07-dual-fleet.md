---
title: 自建订单 DAG 阶段 7 实施报告 — 双车订单运行接入
type: report
status: review
created: 2026-11-20
updated: 2026-11-20
scope: v1 UFLOW / dual transport fleet / order runner
related:
  - ../../archive-plans/order-orchestration/v1-order-dag-phase-07-dual-fleet.md
  - ../../archive-plans/order-orchestration/v1-self-built-order-dag-orchestration.md
  - ../../archive-plans/order-orchestration/v1-order-dag-phase-03-api-persistence.md
  - ../../archive-plans/order-orchestration/v1-order-dag-phase-06-simulation-acceptance.md
  - ../../evidence/order-orchestration/v1_order_dag_phase7_dual_20261120_004442.json
  - ../../evidence/order-orchestration/v1_order_dag_phase7_single_regression_20261120_005619.json
  - ../../evidence/order-orchestration/v1_order_dag_phase7_dual_planner_20261120_010820.json
  - ../../evidence/order-orchestration/v1_order_dag_phase7_ui_dual_20261120_084236.json
  - ../production-flow/v1-stage-04-capability-scheduling.md
---

# 自建订单 DAG 阶段 7 实施报告

本报告记录
[阶段 7 方案](../../archive-plans/order-orchestration/v1-order-dag-phase-07-dual-fleet.md)
的执行结果。改动把 `single/dual` 车队作为**订单运行选项**接入
`OrderSpec → compiler → OrderService → order runner → Stage4Coordinator`
链路，并统一运输工序的设备选择、实际执行车辆、运行事件、资源 owner 和
报告记录。

结论：**子阶段 A–D 的代码、API、迁移与启动 UI 已实施，并通过纯逻辑/API/前端测试和三项限定 CoppeliaSim 验收**。在受保护工件 `v005` 上：

- **single 回归**（不传 `transport_fleet`）：`SUCCEEDED`，1 个 `FINAL`，Cart2 停车，`transport_DE` 由 `agv_1` 执行；
- **dual 手工订单**（含 B→A 与 D→E 两支路）：`SUCCEEDED`，1 个 `FINAL`（父 UID `[1,2]`），`transport_BA` 由 `agv_1`、`transport_DE` 由 `cart_2` 实际执行；
- **dual planner 订单**：`SUCCEEDED`，保存的 DAG digest 与运行 `order_plan` digest 一致，运行模式未改变订单图，`cart_2` 实际承运 `transport_DE`；
- **真实 UI dual 启动**：Playwright 在真实 API + Vite + CoppeliaSim 上创建/预览/保存手工订单，在启动对话框选择“双车”，运行 `SUCCEEDED`，运行页显示“车队 双车”、策略版本、固定分工和 `transport_DE→cart_2`，刷新后终态一致。

未验证项：路线锁缩窄后的并行/冲突矩阵、`cart_2` 轮式物理控制。两根路线仍互斥，本报告不构成并行提速声明。

## 1. 运行契约

`POST /api/orders/{order_id}/versions/{version}/start` 新增可选字段：

```json
{
  "artifact_id": "v1/L5_UFLOW/v005_20261117_110924",
  "max_sim_time": 1200,
  "transport_fleet": "dual"
}
```

- 合法值仅 `single` / `dual`，缺省 `single`，大小写与首尾空白会被规范化。
- 非法值、`dual` 工件缺少 `Cart2`/`Cart2_Tray`、目录未登记 `cart_2` 均在
  创建 run 记录和启动 worker 之前返回 `422 ORDER_RUN_CONFIG`，不产生
  伪造的 `RUNNING` 运行。
- 模式与策略版本冻结在 `OrderRunCommand`，并写入 `order_run` 元数据、
  运行状态查询、事件和 JSON 报告；终态 `result_json` 另行记录
  `catalog_version`（`process_model.version`）。不修改不可变订单版本或
  DAG digest。
- `GET /api/fleet?artifact_id={ref}` 返回可选模式、默认值和该工件的
  dual 可用性/原因，供启动对话框禁用双车并显示后端原因。
- CLI `order start` 增加 `--transport-fleet single|dual`。

固定分工策略版本：`v1_fleet_20261119`（`FLEET_POLICY_VERSION`）。
策略首期只有 `single` 与 `dual` 固定分工，不暴露未接入 coordinator 的
`eta` 选项。

## 2. 设备选择与执行一致性

| 项目 | 实现 |
|---|---|
| 候选收窄 | `Stage4Coordinator._effective_candidates()` 对 `executor_kind=agv` 的工序只保留固定分工车辆；single 只保留 `agv_1`，dual 下 `transport_DE → cart_2` |
| 启动前校验 | `_validate_fleet_assignment()` 检查每个 AGV 工序的固定车辆在其 recipe 候选内，否则 `FLEET_ASSIGNMENT_INVALID` |
| 已验收组合 | `FleetConfig.verified_pairs()` 注入调度快照；`("cart_2", "transport_DE")` 登记自双车合并提交 `bb54cf5` 的验收 |
| 执行前一致性 | `_start_transport()` 校验任务固定分工车辆与运行时槽一致，不一致记录 `FLEET_VEHICLE_MISMATCH` 并冻结，不改派 |
| 调度快照 | `_busy_devices()` 遍历两车执行器；快照新增 `vehicle_at_dock` / `vehicle_dock_settled`，运输与 AGV 装卸工序按 `vehicle_id` 查询对应车辆，不使用 `agv_1` 状态代替 `cart_2` |
| 记录一致 | `selected_device_id`、执行器 `device_id`、`ROUTE_*`/`TASK_*` 事件 `device`、资源 owner（`agv_1/task_*` 或 `cart_2/task_*`）和 `summary.fleet.transport_selections` 使用同一车辆 |
| 托盘校验 | 保持既有 `Cart2_Tray` 别名和位姿容差判据；`cart_2` 的 `_tray_check` 读取 `Cart2_Tray` 高度，不复用 AGV 托盘 |
| 运行收敛 | `finished` 检查所有车辆槽位的活动执行，不只检查 `agv_1` |

`cart_2` 仍由 `KinematicTransportExecutor` 按仿真时间推进位姿，不写轮速；
其 `poll()` 证据已补齐与 `AgvExecutor` 同形的 `route.spec` / `route.metrics`，
使验收层不必按车辆实现分支。

## 3. 路线资源边界

首期保留 `AGV_ROUTE_REQUIRES_ALL_DOCKS=True`：路线资源列表为实际直道 +
全部 dock，由 `ResourceTable.acquire_all()` 一次性全有或全无申请。因此任意
两条路线互斥（保守串行），不会出现逐段持有并等待或死锁。运行摘要新增：

```json
"route_lock": {
  "resources": "all_docks_and_lanes",
  "parallel_routes": false,
  "policy_version": "v1_resources_20261103"
}
```

方案 7.3 的并行证明未实施；双车当前只声明车辆分工/能力可用，不声明并行
提速。缩窄锁定范围需要共享车道、相向冲突和 dock 占用的实机验收。

## 4. 代码与数据落点

| 层 | 文件 | 改动 |
|---|---|---|
| 车队契约 | `simulation/transport_fleet.py` | 策略版本、模式规范化、`verified_pairs()`、运行时槽/证据字段；更新过时的“未被生产代码导入”说明 |
| 应用服务 | `application/orders.py` | `OrderRunCommand.transport_fleet`；启动前模式/工件校验；run 元数据写入；状态投影 `fleet` |
| worker | `application/order_runner.py` | 传递 `fleet_mode` 到 coordinator；进度快照与日志记录车队 |
| 协调器 | `application/coordinator.py` | 候选收窄、分工校验、双车 dock/停稳、执行一致性、事件/owner、`fleet_summary()` |
| 调度策略 | `domain/scheduling.py` | `OperationView.vehicle_id`、快照分车辆 dock 状态、`dock_state()` |
| API | `api/server.py` | start 参数、`GET /api/fleet`、422 映射、health 车队版本 |
| CLI | `cli.py` | `order start --transport-fleet`；基于工件 manifest 的 dual 预检 |
| 场景运行 | `application/run.py` | 车队模式早期规范化并写入运行证据/摘要 |
| 持久化 | `persistence/order_models.py`、`persistence/orders.py`、`0007_order_run_fleet.py` | `order_run.transport_fleet` / `fleet_policy_version` 列与读回；历史行回填 `single` |
| 错误类型 | `persistence/errors.py` | 新增 `OrderRunConfigError`（`ORDER_RUN_CONFIG`） |
| 前端 | `api/types.ts`、`api/client.ts`、`StartRunDialog.tsx`、`RunPage.tsx`、`styles.css` | 单/双车分段选择、dual 不可用原因、运行详情车队/分工/路线锁展示 |

迁移 `0007_order_run_fleet` 通过 batch 模式增加非空 `transport_fleet`
（`server_default='single'`）与 `transport_fleet IN ('single','dual')` 检查
约束，旧运行按历史事实回填单车。

## 5. 前端行为

- 订单详情“确认启动”对话框新增单车/双车选择，默认单车；输入工件变化后
  调用 `GET /api/fleet` 刷新可用性。dual 不可用时按钮禁用并显示后端原因。
- 使用同一个订单启动 API，不为手工/planner 订单新增专属端点。
- 运行详情显示本次车队模式、策略版本、固定分工和路线锁策略；页面字段
  来自服务端运行元数据，刷新 URL 后可恢复。
- 服务端拒绝（非法模式/工件缺 Cart2）在对话框内以明确错误显示。

## 6. 测试与验收结果

执行日期：2026-11-20（本机 Windows 11，CoppeliaSim 4.10 + ZMQ 23000）。

### 6.1 自动化测试

| 命令 | 结果 |
|---|---|
| `uv run pytest -q` | **704 passed, 7 skipped**（跳过项均为需要 `FLEXIBLE_FACTORY_SIM=1` 与 CoppeliaSim 4.10 ZMQ 23000 的 focused 用例） |
| `cd frontend && npm test` | **13 passed**（3 个测试文件） |
| `cd frontend && npm run e2e` | **4 passed**（Playwright mock 旅程，不启动真实仿真） |
| `cd frontend && npm run typecheck` | 通过 |
| `cd frontend && npm run build` | 通过 |

阶段新增/更新的聚焦测试：

- `tests/test_v1_order_service.py`：默认 single、dual 透传与 run 元数据、
  非法模式拒绝、工件缺 Cart2 拒绝、`fleet_status`、HTTP `/api/fleet` 与
  start 的 dual/非法参数、`result_json.catalog_version`。
- `tests/test_v1_order_persistence.py`：车队模式/策略版本写入与读回、
  `0007_order_run_fleet` 降级/升级往返。
- `tests/test_v1_transport_fleet.py`：模式规范化、策略/验收组合投影、
  coordinator 候选收窄与固定分工校验、运动学证据形状。
- `tests/test_v1_scheduling.py`：双车按 `vehicle_id` 读取 dock/停稳条件。
- `tests/test_v1_coordinator_process.py`：资源 owner 由 `agv/task_*` 更新为
  显式车辆 `agv_1/task_*`（single 行为等价，记录更明确）。
- `tests/test_v1_db_dashboard.py`：Alembic 当前 revision 断言更新为 `0007`。

### 6.2 CoppeliaSim 验收

工件：`v1/L5_UFLOW/v005_20261117_110924`（protected，manifest
`70457326…`，validation ok）。

| 用例 | 订单/运行 | 结果 |
|---|---|---|
| single 回归 | `phase7-single-regression`，`order start`（不传模式） | `SUCCEEDED`；sim 163.8 s / 3276 步；1 个 `FINAL`（父 UID `[1,2]`）；`agv_1` 完成 A→D 调机与 D→E 载货（stop_error 0.019604 / 0.019562 m）；Cart2 停车未参与；无失败、资源清零 |
| dual 手工订单 | `phase7-dual-manual`，B→A + D→E 两支路，`--transport-fleet dual` | `SUCCEEDED`；sim 293.75 s / 5875 步；1 个 `FINAL`（父 UID `[1,2]`）；`transport_BA → agv_1`（`agv_1#2`，stop_error 0.019832 m）、`transport_de → cart_2`（`cart_2#1`）；无失败、资源清零 |
| dual planner 订单 | `order plan --save` 后同一启动路径，`--transport-fleet dual` | `SUCCEEDED`；sim 136.15 s / 2723 步；1 个 `FINAL`（父 UID `[1,2]`）；保存 DAG digest = 运行 `order_plan` digest（`dc10746e…`）；`transport_DE → cart_2`；无失败 |
| 真实 UI dual 启动 | Playwright `real_acceptance.mjs manual`（无 route mock），UI 创建订单并选择双车 | `SUCCEEDED`；sim 136.05 s；1 个 `FINAL`（父 UID `[1,2]`）；7/7 节点完成；运行页显示“车队 双车”、`v1_fleet_20261119`、固定分工与 `transport_DE→cart_2`；刷新后仍为“成功” |

证据：

- dual 手工订单：[v1_order_dag_phase7_dual_20261120_004442.json](../../evidence/order-orchestration/v1_order_dag_phase7_dual_20261120_004442.json)
- single 回归：[v1_order_dag_phase7_single_regression_20261120_005619.json](../../evidence/order-orchestration/v1_order_dag_phase7_single_regression_20261120_005619.json)
- dual planner：[v1_order_dag_phase7_dual_planner_20261120_010820.json](../../evidence/order-orchestration/v1_order_dag_phase7_dual_planner_20261120_010820.json)
- 真实 UI dual：[v1_order_dag_phase7_ui_dual_20261120_084236.json](../../evidence/order-orchestration/v1_order_dag_phase7_ui_dual_20261120_084236.json)

运行观察：

- dual 手工订单中两次 transfer 的 `horizontal_direct` 规划分别因 IK 无解和
  工作空间碰撞失败，系统按既有 `horizontal_detour_±0.30` 回退继续执行，
  未产生节点失败。这是既有运动层余量行为，不是阶段 7 新增能力。
- AGV 载货/调机 stop_error 约 0.0196–0.0198 m，仍贴近 0.02 m 容差；
  本次通过不代表余量已扩大。
- `transport_DE` 在 AGV 路线活动期间记录 `AGV 通道/dock 资源等待`，随后
  才启动，证明保守全 dock 锁下的冲突方安全等待；没有并行路线执行。
- 真实 UI 用例使用 `housing_direct_A` 分支，因此只有 `cart_2` 执行运输，
  `agv_1` 空闲；双车各自承运两类物流的联合证据来自 CLI 手工订单用例。

## 7. 方案 7.5 用例覆盖

| 用例 | 状态 | 说明 |
|---|---|---|
| 1. single 回归 | 通过 | 见 6.2；Cart2 停车，AGV 承运 D→E，终态/谱系/资源通过 |
| 2. dual 手工订单 | 通过（CLI + 真实 UI） | 见 6.2；B→A 与 D→E 各自使用正确车辆；真实 UI 手工订单选择双车并成功启动/刷新一致 |
| 3. dual planner 订单 | 通过（CLI 驱动） | 见 6.2；planner DAG digest 不因模式变化，实际选车与固定分工一致 |
| 4. 资源冲突/并行边界 | 部分验证 | 保留 `AGV_ROUTE_REQUIRES_ALL_DOCKS`，未缩窄路线锁；dual 运行观察到冲突方等待，未证明并行，也未执行专用冲突 harness |
| 5. 配置拒绝 | 通过（服务/API + CLI 预检） | 非法模式、缺 Cart2/Cart2_Tray、目录缺 `cart_2` 均在创建 run 前拒绝；另有真实 `v001` 工件 CLI dual 启动验证：退出码 2、`ORDER_RUN_CONFIG`，运行表未新增记录；未在仿真器内重复构造失败 |

仍未执行：planner 订单的真实 UI 启动（planner 用例经 CLI 验证）、
路线锁缩窄后的共享车道/相向冲突矩阵、真实故障降级。

## 8. 边界与未覆盖

- `cart_2` 是运动学执行器，不是轮式物理驱动；本报告不构成轮式控制验收。
- 未实现 planner 按实时车辆状态派车、ETA 分配、故障降级/换车或改派。
- 两条路线仍互斥；没有双车并行吞吐证据。
- planner 订单的真实 UI 启动未单独执行；planner dual 用例经 CLI 保存/启动验证。
- `verified_pairs` 目前只登记 `("cart_2", "transport_DE")`。新增双车组合
  必须先完成 focused 仿真验收再登记，不能靠候选列表默认放行。
- 工件预检基于 manifest 只读对象清单；最终场景契约仍由 worker 的
  `LifecycleManager.load/prepare_start` 校验，二者失败都显式拒绝。
- 运行状态/报告的 `fleet` 是运行投影，不是新的订单属性；订单版本与
  DAG digest 不随车队模式变化。
- AGV 停靠误差余量仍窄（≈0.0196–0.0198 m / 0.02 m），属于既有物理风险，
  不能据本次通过放宽阈值。