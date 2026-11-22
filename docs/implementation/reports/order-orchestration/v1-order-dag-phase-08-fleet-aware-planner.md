---
title: 自建订单 DAG 阶段 8 实施报告 — 车队感知的自主规划
type: report
status: review
created: 2026-11-21
updated: 2026-11-21
scope: v1 UFLOW / planner / fleet context
related:
  - ../../plans/order-orchestration/v1-order-dag-phase-08-fleet-aware-planner.md
  - ../../plans/order-orchestration/v1-order-dag-phase-07-dual-fleet.md
  - ../../plans/order-orchestration/v1-order-dag-phase-04-auto-planner.md
  - ../../evidence/order-orchestration/v1_order_dag_phase8_fleet_planner_20261121_074317.json
---

# 自建订单 DAG 阶段 8 实施报告

本报告记录
[阶段 8 方案](../../plans/order-orchestration/v1-order-dag-phase-08-fleet-aware-planner.md)
的实施结果。改动让 planner 在**规划阶段**使用 `single/dual` 车队假设、
固定分工和静态车辆可用性做路线可行性、候选降权/排序与 explain，同时保持
订单 DAG、`OrderSpec` 和运行时派工边界不变。

结论：**子阶段 A–D 已实施**。planner 车队假设只影响候选评估和解释，不写入
订单或 DAG digest；planner 生成的订单在 `dual` 与 `single` 下仍由
coordinator 按实际运行模式派工。真实 UI/CoppeliaSim 验收完成两项：
dual 假设下运行成功、`dual` 假设与 `single` 运行不一致时正确标记并仍按
实际模式执行。

## 1. 规划契约

`PlannerInput` 新增字段：

```json
{
  "transport_fleet": "dual",
  "fleet_assumptions": {"available_vehicle_ids": ["agv_1", "cart_2"]},
  "strict_fleet": false,
  "fleet_eta_ranking": false
}
```

- `transport_fleet`：`single` 缺省 / `dual`；非法值在输入边界抛
  `PLANNER_INPUT`（HTTP 422）。
- `fleet_assumptions.available_vehicle_ids`：静态规划假设，空表示使用该
  编制全部车辆；不是实时车辆状态。未知/非 AGV 车辆被视为致命输入错误。
- `strict_fleet`：false 时车队假设不满足只降权并记录 warning；true 时直接
  拒绝该路线。
- `fleet_eta_ranking`：可选，用离散事件运输模型对同优先级候选做
  tie-breaker；不是物理/并行验收。
- 输入写入 `order_version.planner_input_json`；explain 写入
  `planner_explain_json`，因此规划假设可追溯，无需修改订单 schema。

## 2. 纯车队策略抽取

新增 `domain/fleet_policy.py`，集中维护：

- `FLEET_SINGLE/DUAL`、`FLEET_MODES`、`DEFAULT_FLEET_MODE`；
- `FLEET_POLICY_VERSION`（唯一来源仍是 `spec.py`）；
- `normalize_fleet_mode()`；
- `FleetConfig`（`vehicle_for()`、`vehicles()`、`verified_pairs()`、
  `as_dict()`）；
- `DUAL_VERIFIED_PAIRS`。

`simulation/transport_fleet.py` 从该模块导入并重导出，coordinator、订单服务、
API/CLI 的既有导入路径保持兼容；planner 直接依赖纯 domain 模块，不导入
仿真执行器或 CoppeliaSim。

## 3. 路线评估与解释

### 3.1 候选可行性

- 对每条 route macro 的运输模板计算固定分工车辆；`executor_kind=agv` 的
  车辆必须在该模板候选内，且（若给出静态可用集合）必须在可用集合内。
- 臂工序的 `source_kind/sink_kind=agv` 只记录其依赖车辆，不改变模板候选。
- `strict_fleet=false`：问题记录为 warning，路线保留并加 `fleet_penalty`；
  `strict_fleet=true`：路线直接跳过并记录 `error`。
- planner 不修改 `OrderSpec`、compiler 或 `OrderInstance`；DAG digest 在
  single/dual 假设下保持一致（测试与实机均验证）。

### 3.2 诊断码

| code | 语义 |
|---|---|
| `PLANNER_FLEET_MODE_INVALID` | 未知车队模式（致命） |
| `PLANNER_FLEET_ASSIGNMENT_UNAVAILABLE` | 分工车辆不在候选/静态可用集合，或假设含未知车辆 |
| `PLANNER_FLEET_ASSUMPTION_ONLY` | `available_vehicle_ids` 为静态假设，不读取实时状态 |
| `PLANNER_FLEET_ROUTE_PENALIZED` | 路线因车队假设降权但保留 |

### 3.3 explain

`explanation.fleet_context` 包含：

```json
{
  "mode": "dual",
  "policy_version": "v1_fleet_20261119",
  "assignment": {"transport_de": "cart_2"},
  "available_vehicle_ids": ["agv_1", "cart_2"],
  "strict": false,
  "eta_ranking": false,
  "route_vehicles": [
    {"route_id": "bracket_route", "template_id": "transport_DE",
     "kind": "transport_de", "vehicle_id": "cart_2"}
  ],
  "assumptions": ["static_catalog_assignment", "no_realtime_vehicle_state"]
}
```

候选 JSON 同时带 `fleet` 证据（是否降权、问题列表、ETA 结果），便于前端/
报告核对。

### 3.4 静态 ETA（可选）

启用 `fleet_eta_ranking` 时，planner 把候选中的 AGV 模板映射为
`transport_schedule.TransportJob`，按编制选择 `single` 或 `fixed` 策略运行
`DualCartSimulator`，将 `makespan_s` 作为同优先级候选的 tie-breaker。
explain 记录模型、车辆、作业、makespan 和
`assumption=discrete_event_not_physics`。ETA 证据同时带
`completed/pending/available`；没有可用车辆承接全部作业时 `available=false`。
该结果不进入运行资源锁、碰撞或运动学验收。

## 4. 运行期对照

- planner 的 `transport_fleet` 只是规划假设；订单启动仍独立选择
  `single/dual`。
- 订单启动时读取 planner 输入中的车队假设并写入 `OrderRunCommand`
  （`planner_transport_fleet`）；运行报告/状态输出
  `fleet.planner_assumption` 与 `fleet.planner_assumption_mismatch`。
- 假设与实际模式不一致时仍按实际模式派工并标记，不自动改单、不拒绝运行。
- 运行模式/设备选择/资源 owner 语义与阶段 7 一致。

## 5. 入口变化

| 入口 | 变化 |
|---|---|
| `POST /api/planner/plan`、`/api/planner/drafts` | 接受 `transport_fleet`、`fleet_assumptions`、`strict_fleet`、`fleet_eta_ranking` |
| CLI `order plan` | 新增 `--transport-fleet`、`--strict-fleet`、`--fleet-eta-ranking`；CLI 值覆盖输入文件 |
| 前端 planner 页 | 新增“规划车队假设”单选与 strict 复选框；结果显示车队模式、策略、分工、降权标记 |
| API health | planner 策略版本随 `spec.ORDER_PLANNER_POLICY_VERSION` 返回 |

`ORDER_PLANNER_POLICY_VERSION` 升至 `v1_planner_20261121`；旧输入缺省
`transport_fleet=single`，行为与阶段 4 一致。显式携带旧
`policy_version` 的调用方会得到 `PLANNER_POLICY_VERSION` 错误，需要省略该
字段或升级版本；订单运行不因 planner 版本变化拒绝已保存订单。旧 planner
记录缺少 `fleet_context` 时按空块读取。

## 6. 测试与验收

### 6.1 自动化测试

执行日期：2026-11-21。

| 命令 | 结果 |
|---|---|
| `uv run pytest -q` | **737 passed, 8 skipped**（跳过项为需 CoppeliaSim 的 focused 用例） |
| `cd frontend && npm test` | **13 passed** |
| `cd frontend && npm run e2e` | **4 passed**（mock 旅程） |
| `cd frontend && npm run typecheck` | 通过 |
| `cd frontend && npm run build` | 通过 |

新增/更新测试：

- `tests/test_v1_order_planner.py`：默认 single、dual 假设、DAG digest 不变、
  静态不可用降权、strict 拒绝、非法模式、ETA 证据、planner 输入元数据持久化、
  HTTP API 双车假设与 422。
- `tests/test_v1_order_service.py`：planner 假设与实际运行模式不一致时
  `planner_assumption_mismatch=true`。
- `tests/test_v1_transport_fleet.py`：domain 纯策略与仿真重导出同一
  `FleetConfig`，`vehicles()` 覆盖 single/dual。

### 6.2 CoppeliaSim 验收

工件：`v1/L5_UFLOW/v005_20261117_110924`（protected，validation ok）。
同一 planner 订单 `phase8-dual-planner`（保存时 `transport_fleet=dual`，
`fleet_eta_ranking=true`，DAG digest `219c97b6…`）：

| 用例 | 运行模式 | 结果 |
|---|---|---|
| dual 假设 + dual 运行 | `dual` | `SUCCEEDED`；sim 136.4 s；1 个 `FINAL`（父 UID `[1,2]`）；`transport_DE → cart_2`；`planner_assumption_mismatch=false` |
| dual 假设 + single 运行 | `single` | `SUCCEEDED`；sim 163.1 s；1 个 `FINAL`（父 UID `[1,2]`）；Cart2 停车、`transport_DE → agv_1`；`planner_assumption=dual`、`planner_assumption_mismatch=true`，未改单 |

证据：[v1_order_dag_phase8_fleet_planner_20261121_074317.json](../../evidence/order-orchestration/v1_order_dag_phase8_fleet_planner_20261121_074317.json)。

## 7. 边界与未覆盖

- planner 不读取实时车辆位置、队列、健康、故障或 ETA；静态 ETA 只用于
  候选排序，不是运行派工依据。
- 未实现运行中换车、重试、故障降级或按 ETA 动态分配。
- 路线资源仍按全部 dock + 直道锁定；没有并行路线/吞吐证明。
- `cart_2` 仍是运动学执行器，不是轮式物理控制。
- 真实 UI planner 启动未在本次验收中执行；planner 假设通过 CLI/API 与
  前端 mock E2E 覆盖，阶段 7 已验证真实 UI 启动路径。
- DAG digest 不随车队假设变化；实际运行设备仍以运行报告为准。