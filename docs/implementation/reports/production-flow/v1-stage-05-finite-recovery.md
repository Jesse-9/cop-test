---
title: 阶段 5 业务用例与基线实施报告 — WP0/WP1
type: report
status: review
created: 2026-11-24
updated: 2026-11-24
scope: v1 UFLOW / 业务阶段 5 / 双车运输 / 订单级调度观测
related:
  - ../../plans/production-flow/v1-stage-05-remaining-work.md
  - ../../archive-plans/production-flow/v1-stage-05-finite-recovery-implementation.md
  - ../../../wiki/06-工业案例/柔性车间/当前规划/06-23-混线生产阶段5调度优化与有限恢复.md
  - ../../reports/order-orchestration/v1-order-dag-phase-07-dual-fleet.md
  - ../../evidence/production-flow/v1_stage5_baseline_20261124.json
---

# 阶段 5 业务用例与基线实施报告 — WP0/WP1

本报告记录
[阶段 5 完整方案（归档）](../../archive-plans/production-flow/v1-stage-05-finite-recovery-implementation.md)
WP0（业务用例与冻结基线）和 WP1（订单级调度观测）的实施结果；WP2–WP6
剩余工作见[阶段 5 剩余工作](../../plans/production-flow/v1-stage-05-remaining-work.md)。

结论：**WP0/WP1 已实施并通过纯测试；WP2–WP6 未实施**。本轮没有启动
CoppeliaSim，基线来自 2026-11-20 归档 dual 运行的事件重算；`bound=False`
只是当前目录模型下“设备不可用”的静态代理。不能把本报告解释为设备健康、
运行时改派、故障恢复或物理并行已具备。

## 1. 业务用例

选定用例：dual 固定分工下，`cart_2` 在 `transport_DE` 启动前不可用。
当前策略把 `transport_DE` 收窄到 `cart_2`，没有替代候选，支架分支不能
启动，订单目标无法达成。

目标行为（未实现）：只对尚未启动、未持有物理执行资源的 `transport_DE`
撤销 `cart_2` 选择并改派给 recipe 已允许且已验收的 `agv_1`；不触碰
`RUNNING` 工序，不自动重试/恢复，不做轮式物理控制。

选择依据：`transport_DE` 是默认 recipe 唯一具有两个候选设备的运输工序
（`agv_1`/`cart_2`）；`agv_1` 已在 single/`full_flow` 基线验收，`cart_2`
已在阶段 7 dual 运行验收（`verified_pairs={("cart_2","transport_DE")}`）。

## 2. 冻结基线

| 项 | 值 |
|---|---|
| 工件 | `v1/L5_UFLOW/v005_20261117_110924` |
| `config_digest` | `c132340c0cdbbbfcb8ebf10a0f42a73d260d14d2aa267801c676e8ba30a5ae9d` |
| `layout_digest` | `ed7759fddfc55eb546fd29363cea003898236cac6a85b32f2cf9717b72afddef` |
| 订单 / DAG digest | `phase7-dual-manual` v1 / `3fd961b4…c04cd85` |
| 固定输入 | `raw-b-1`（`RAW_B`）×1、`raw-d-1`（`RAW_D`）×1 |
| 运行 | `order_phase7-dual-manual_20261119_162821_96004c` |
| 归档 summary | `docs/implementation/runs/phase7/v1_order_run_20261120_004442.json` |
| 裁剪事件 fixture | `tests/fixtures/stage5_baseline_events.json`（39 条，可复算同一基线） |
| 车队 / 仿真 | dual / 4.10.0.0，293.75 s / 5875 步 |
| 交期口径 | 300.0 s |

事件重算结果（`recompute_order_metrics`，`due_time=300.0`）：

| 指标 | 基线 |
|---|---:|
| FINAL / 父 UID / 目标 | 1 / `[1,2]` / 1 |
| 完成时刻 / 逾期 | 293.7 s / 0.0 s |
| 任务 / DONE / FAILED / BLOCKED | 9 / 9 / 0 / 0 |
| 等待 / 阻塞 | 177.75 s / 0.0 s |
| 处理合计 / 周期合计 | 293.75 s / 471.5 s |
| WIP 峰值 / 结束活动数 | 1 / 0 |
| 调度决策 / 调度等待 | 8 / 0 |
| 选中设备 | `arm_A×1, arm_B×1, arm_C×1, arm_D×1, arm_E×2, agv_1×1, cart_2×1` |
| 失败 / 锁存 / 资源未释放 | 无 / 无 / 无 |

归档运行未带 `detail.order_id`；重算按 v1 单订单运行将未标记的物料事件
计入当前订单。该限制已写入方案。

故障基线（纯调度快照，非实机）：

- `transport_DE` 候选收窄为 `("cart_2",)`，`cart_2` 标记
  `bound=False`；
- `selected_device_id=""`，`defer_reason="NO_FEASIBLE_CANDIDATE"`，
  候选原因 `DEVICE_NOT_BOUND`；
- 候选恢复为 recipe 全集 `("agv_1","cart_2")` 时策略可选 `agv_1`，但
  运行时改派未实现。

## 3. WP1 代码改动

| 层 | 改动 |
|---|---|
| `domain/metrics.py` | 新增 `ORDER_METRICS_VERSION`、`event_order_id`、`recompute_order_metrics`；从事件重算完成/逾期、等待/阻塞、处理/周期、WIP 峰值、派工/等待、延后原因、重派次数和策略版本；相同事件重复重算一致 |
| `application/coordinator.py` | 订单模式事件 `detail` 写入 `order_id`/`order_version`；调度记录增加 `snapshot_digest`、`snapshot_summary`、`wait_s`、`sort_key`、`selected_candidate_index`；快照摘要在真正发出事件时才计算；`summary()["metrics"]["order"]` 输出订单级指标 |
| `tests/test_v1_metrics_contract.py` | 订单过滤、完成/逾期、等待/阻塞、WIP、派工统计、确定性、失败任务 |
| `tests/test_v1_stage5_baseline.py` | dual 固定分工、`cart_2` 不可用无候选、recipe 全集下 `agv_1` 可选、single 基线、归档裁剪事件复算 |
| `tests/test_v1_order_execution.py` | 记录型订单端到端后的订单指标与事件字段 |

`not_modeled` 仍为 `device_health`、`tool_payload`、`pose_envelope`；
本轮没有新增设备健康字段或策略分值模型。`sort_key` 记录的是当前实际
排序键，不是新的代价函数。指标版本 `v1_order_metrics_20261124` 不进入
`config_digest()`/`layout_digest()`。

## 4. 测试与验证

执行日期：2026-11-24。未启动 CoppeliaSim。

| 命令 | 结果 |
|---|---|
| `python -m pytest -q tests/test_v1_metrics_contract.py tests/test_v1_stage5_baseline.py tests/test_v1_order_execution.py tests/test_v1_scheduling.py tests/test_v1_coordinator_scheduler.py` | **30 passed** |
| `python -m pytest -q` | **820 passed, 8 skipped** |

8 个跳过项是需要 `FLEXIBLE_FACTORY_SIM=1` 的既有 CoppeliaSim/ZMQ 用例；
本轮没有把它们计为通过。前端未改动，未重跑。

## 5. 未覆盖与边界

- **未实机**：没有重新启动 CoppeliaSim；基线是归档运行事件重算，不是新
  实机结果；故障基线是纯调度快照，不是故障注入。
- **无设备健康源**：`bound=False` 是静态代理；未知/过期状态阻止派工、
  可接单→故障→恢复可接单序列仍未实现。
- **未改派**：没有 `SCHEDULE_REASSIGN`、撤销/重选生命周期、车辆选择变更
  或资源 owner 变更；`running` 工序改派仍禁止。
- **未恢复**：没有安全停止、现场核验、checkpoint 续作、自动重试或人工
  恢复协议；真实 Arm/AGV 运动故障与 ZMQ/step 断连仍按 GLOBAL 处理。
- **单订单口径**：指标过滤允许未标记物料事件进入当前订单；多订单并发、
  跨订单对比、Q5/Q6 查询未实现。
- **物理可行性**：`agv_1`/`cart_2` 各自验收不等于“`cart_2` 故障时改派
  `agv_1`”已验收；后续 WP3 必须补 focused 仿真和 `full_flow` 对照。

## 6. 证据

正式证据：
[v1_stage5_baseline_20261124.json](../../evidence/production-flow/v1_stage5_baseline_20261124.json)。

证据记录用例、工件/digest、归档运行、基线重算值、故障基线快照、版本和
测试结果；不包含新的 CoppeliaSim 运行输出。