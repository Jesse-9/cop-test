---
title: 阶段 5 有限恢复实施方案 — 业务用例、基线与调度观测
type: plan
status: partially-implemented
created: 2026-11-24
updated: 2026-11-24
scope: v1 UFLOW / 业务阶段 5 / 双车运输 / 调度观测
related:
  - ../../../wiki/06-工业案例/柔性车间/当前规划/06-23-混线生产阶段5调度优化与设备状态.md
  - ../../reports/production-flow/v1-stage-05-finite-recovery.md
  - ../../reports/order-orchestration/v1-order-dag-phase-07-dual-fleet.md
  - ../../reports/production-flow/v1-stage-04-capability-scheduling.md
  - ../../evidence/production-flow/v1_stage5_baseline_20261124.json
---

# 阶段 5 有限恢复实施方案 — 业务用例、基线与调度观测

本方案把业务阶段 5 的首个业务用例、冻结基线和调度观测拆成可独立验证的
工作包。当前已实施 **WP0（业务用例与基线）** 和 **WP1（订单级调度观测）**；
WP2–WP6 仍未实施，不表示设备健康、故障改派或自动恢复已具备。

## 1. 首个业务用例

**业务问题：**双车运行中，`transport_DE` 按固定分工由 `cart_2` 承运。
若 `cart_2` 在 `transport_DE` 启动前不可用，当前策略没有替代候选，支架
分支无法启动，订单目标无法达成。

**目标行为（尚未实现）：**只对尚未启动、未持有物理执行资源的
`transport_DE`，撤销 `cart_2` 选择并改派给 recipe 已允许且已验收的
`agv_1`；不改物料账本、不增加 attempt、不触碰 `RUNNING` 工序。

**为什么选择这个用例：**

- `transport_DE` 是默认 recipe 中唯一具有两个候选设备的运输工序：
  `("agv_1", "cart_2")`；
- `agv_1` 是 recipe 首个候选，已在 single/`full_flow` 基线验收；
- `cart_2` 已在双车合并提交和阶段 7 的 dual 运行中验收
  （`verified_pairs={("cart_2", "transport_DE")}`）；
- 不涉及机械臂替代、新布局或未验收设备，符合阶段 5“单一故障类型、
  单一执行器”的准入要求。

**本轮故障分类：**设备不可用/停用，且只发生在 `transport_DE` 未启动时。
执行中故障、自动续作、现场复位、轮式物理控制不在本用例内。

**恢复责任与人工边界：**设备不可用由固定试验输入提供（当前没有运行时
设备健康源）；改派后的运行仍需操作员确认设备可接单。第一轮不做自动
恢复、重试、换车或 `mark_resumed()` 复用。

## 2. 冻结基线

### 2.1 工件与环境

| 项 | 固定值 |
|---|---|
| 场景工件 | `v1/L5_UFLOW/v005_20261117_110924` |
| `config_digest` | `c132340c0cdbbbfcb8ebf10a0f42a73d260d14d2aa267801c676e8ba30a5ae9d` |
| `layout_digest` | `ed7759fddfc55eb546fd29363cea003898236cac6a85b32f2cf9717b72afddef` |
| 场景文件 SHA-256 | `15be9723ccc8a49554218af1f516427655239e7dc871d7d237a1b7c5202ef7d7` |
| CoppeliaSim | 4.10.0.0 |
| 车队 | `dual` |
| 订单 | `phase7-dual-manual`，version 1，DAG digest `3fd961b4…c04cd85` |
| 固定输入 | `raw-b-1`（`RAW_B`）×1、`raw-d-1`（`RAW_D`）×1；无额外批次 |
| 随机源 | 无；固定 `SIM_STEP`、确定性调度与控制器，不使用随机 seed |
| 运行 | `order_phase7-dual-manual_20261119_162821_96004c` |
| 归档 summary | `docs/implementation/runs/phase7/v1_order_run_20261120_004442.json`（Git 忽略） |
| 裁剪事件 fixture | `tests/fixtures/stage5_baseline_events.json`（39 条任务/决策/合流事件，可复算同一基线） |
| 仿真时间 / 步数 | 293.75 s / 5875 |

本轮**没有重新启动 CoppeliaSim**；基线数值由上述 2026-11-20 归档运行事件
重算，不是新一轮实机结果。`v005` 与 `config_digest` 已由冻结 manifest
核对；`layout_digest` 由 `manifest.layout` 重算，与阶段 4 基线一致。

### 2.2 基线指标

统计口径：`recompute_order_metrics(events, order_id="phase7-dual-manual",
due_time=300.0, target_quantity=1)`；事件包含协调器事件与物料事件。

| 指标 | 基线值 |
|---|---:|
| 目标 / FINAL 数 | 1 / 1，父 UID `[1, 2]` |
| 完成仿真时刻 | 293.70 s |
| `due_time` / 逾期 | 300.0 s / 0.0 s |
| 仿真窗口 | 0.0–293.75 s |
| 任务数 / DONE / FAILED / BLOCKED | 9 / 9 / 0 / 0 |
| 累计等待 / 阻塞 | 177.75 s / 0.0 s |
| 处理时间合计 / 周期合计 | 293.75 s / 471.50 s |
| WIP 峰值 / 结束活动数 | 1 / 0 |
| 调度决策 / 调度等待 | 8 / 0 |
| 选中设备 | `arm_A×1, arm_B×1, arm_C×1, arm_D×1, arm_E×2, agv_1×1, cart_2×1` |
| 失败 / 安全锁存 / 资源未释放 | 无 / 无 / 无 |
| 重派次数 | 0（当前无改派能力） |

### 2.3 故障基线（纯策略，非实机）

`transport_DE` 在 dual 固定分工下被收窄为 `("cart_2",)`；把 `cart_2`
标记为无有效绑定时，`plan_ready` 返回：

- `selected_device_id=""`，`defer_reason="NO_FEASIBLE_CANDIDATE"`；
- 候选原因 `DEVICE_NOT_BOUND`；
- 支架分支保持 ready/等待，`kit_join` 无法就绪，目标不能达成。

`bound=False` 是当前目录模型中最接近“设备不可用”的静态代理；本阶段没有
设备健康事实源，因此不能把它描述为运行时健康状态或实机故障注入。

若把候选恢复为 recipe 全集 `("agv_1", "cart_2")`，纯调度器可在 `cart_2`
不可用时选择基线设备 `agv_1`。这只证明策略输入具备可行备选，**不证明**
运行时改派、车辆调度或物理执行已实现。

### 2.4 后续比较口径

WP2/WP3 实现后，在同一工件、订单、车队和仿真时长下比较：

- 保持 `finals=1`、`due_met=true`、父 UID `[1,2]`、无失败/锁存、
  资源清零；
- 等待、阻塞、WIP 峰值不劣于基线 177.75 s / 0 / 1；
- 新增 `SCHEDULE_REASSIGN` 事件记录旧/新设备、故障证据、撤销结果、
  资源 owner 和重派原因；
- 任一项无法重算或与 summary 不一致时，先修事件，不进入策略变更。

## 3. WP0：业务用例与基线（已实施）

交付：

- 本文冻结业务用例、故障范围、工件/digest、订单/车队、基线指标和比较口径；
- 正式证据
  [v1_stage5_baseline_20261124.json](../../evidence/production-flow/v1_stage5_baseline_20261124.json)
  记录归档运行重算值与故障基线；
- 不修改生产运行时能力，不新增设备健康字段或改派路径。

通过门槛：另一位维护者能按本方案和证据复现基线数值；新增场景只增加
`cart_2` 不可用这一故障变量。本轮未做真实现场复现，门槛的实机部分
保持未通过。

## 4. WP1：订单级调度观测（已实施）

### 4.1 事件字段

协调器在订单模式下为所有事件 `detail` 写入：

- `order_id`、`order_version`：稳定订单归属；
- `snapshot_digest`、`snapshot_summary`：本次调度快照的稳定摘要，以及
  ready 工序/设备/物料/区域/资源 owner/忙设备/已验证组合的数量；
- `wait_s`、`sort_key`：当前排序键（priority、等待、ready 时刻、实例 ID）；
- `selected_candidate_index`：选中候选在可行候选中的序号；
- 既有 `candidates`、`reasons`、`conditions`、`selected_device_id`、
  `defer_reason`、`policy_version`、`resource_result` 保持不变。

`not_modeled` 仍显式列出 `device_health`、`tool_payload`、`pose_envelope`；
本轮没有把未建模维度写成已通过。

### 4.2 订单级指标

`domain/metrics.py` 新增：

```python
recompute_order_metrics(
    events, *, order_id="", due_time=None, target_quantity=1,
    policy_version="", window_start=None, window_end=None, wip=None)
```

返回完成/逾期、任务数与等待/阻塞、处理/周期、WIP 峰值、派工/等待次数、
选中设备、延后原因、重派次数/原因、策略版本和事件数。规则：

- 订单过滤保留 `detail.order_id` 匹配或未标记的事件；v1 一次运行只有一个
  订单，物料事件（`MATERIAL_SPAWN`/`JOIN_COMMIT`）未带订单字段时仍计入；
- 完成时刻取第 `target_quantity` 个 FINAL 的时刻；未完成时逾期按窗口
  结束时刻计算；
- 相同事件重复重算结果必须一致；`recomputed_from_events=true`；
- 指标版本 `v1_order_metrics_20261124`，不进入 `config_digest()` 或
  `layout_digest()`。

协调器 `summary()["metrics"]["order"]` 在订单模式下自动写入该结果；
无订单的 `full_flow` 运行不增加该字段。

### 4.3 测试

| 测试 | 覆盖 |
|---|---|
| `tests/test_v1_metrics_contract.py` | 订单过滤、完成/逾期、等待/阻塞、WIP、派工统计、确定性重算、失败任务 |
| `tests/test_v1_stage5_baseline.py` | dual 固定分工、`cart_2` 不可用时无候选、recipe 全集下 `agv_1` 可选、single 基线、归档裁剪事件复算基线 |
| `tests/test_v1_order_execution.py` | 记录型订单端到端后 `metrics["order"]`、事件 `order_id`/`snapshot_digest`/`sort_key` |

## 5. 未实施工作包

| 工作包 | 状态 | 进入条件 |
|---|---|---|
| WP2 设备状态 | 未实施 | 需要首个用例的权威状态源；未知/过期状态必须阻止派工 |
| WP3 未启动工序改派 | 未实施 | WP2 通过；两个候选都已完成 focused 运动验证；纯测试覆盖撤销/重选/重复事件 |
| WP4 单一故障安全停止与人工恢复 | 未实施 | executor 能安全停止并报告物料/attachment/速度/锁存/资源 owner |
| WP5 有界恢复策略 | 未实施 | WP4 证明某工步可 checkpoint 续作，且业务明确要求 |
| WP6 规则调度代价 | 未实施 | WP1 指标可重算、WP2 状态可用、有明确业务目标 |

本轮没有实现设备健康字段、候选故障过滤、`SCHEDULE_REASSIGN`、运行时
改派、自动恢复、重试、换车或路线并行。真实 Arm/AGV 运动故障与 ZMQ/step
断连仍按 GLOBAL 处理。

## 6. 复现命令

```bash
.venv/Scripts/python.exe -m pytest -q \
  tests/test_v1_metrics_contract.py \
  tests/test_v1_stage5_baseline.py \
  tests/test_v1_order_execution.py
.venv/Scripts/python.exe -m pytest -q
```

基线重算使用归档 summary 的 `summary.events`；不要从 `runs/` 手工改写
事件，也不要把旧运行输出当作新实机证据。

## 7. 边界

- 本轮未启动 CoppeliaSim；基线是归档运行事件的重算，不是新一轮验收；
- 没有设备健康事实源，`bound=False` 只是静态代理；
- 不改 `OrderSpec`、DAG digest、`OrderRunContext`、场景工件或冻结配置；
- 指标只覆盖单订单事件流；多订单并发、跨订单对比和 Q5/Q6 查询未实现；
- 真实故障、改派、恢复和并行仍需逐项 focused 仿真与人工确认。