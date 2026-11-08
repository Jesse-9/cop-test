---
title: 自建订单 DAG 阶段 6 实施报告 — CoppeliaSim 集成验收
type: report
status: review
created: 2026-11-08
updated: 2026-11-08
scope: v1 UFLOW / CoppeliaSim 4.10
related:
  - ../plans/v1-order-dag-phase-06-simulation-acceptance.md
  - ../plans/v1-self-built-order-dag-orchestration.md
  - v1-order-dag-phase-05-frontend.md
  - ../evidence/v1_order_dag_phase6_acceptance_20261108.json
  - ../plans/v1-order-dag-phase-02-failure-isolation.md
---

# 自建订单 DAG 阶段 6 实施报告

本报告记录
[阶段 6 仿真验收方案](../plans/v1-order-dag-phase-06-simulation-acceptance.md)
的执行结果。验收对象是受保护的 `L5_UFLOW` v1 工件
`v1/L5_UFLOW/v001_20261103_004317`，验证手工订单、planner 订单、物理动作
前 NODE 局部拒绝和真实安全锁存 GLOBAL 停止。

结论：**阶段 6 已执行并通过本报告列出的验收矩阵**。手工 UI 订单和 planner
UI 订单均生成 1 个 `FINAL`，父 UID 对应本次两个输入；NODE 前置拒绝只阻断
依赖后继，独立支架支路完成；真实安全锁存使整次运行 `ABORTED` 并保留锁存。
未执行项和边界见第 11 节，不能把本报告解释为任意 DAG、任意设备替换或
Arm/AGV 真实运动故障已获 NODE 准入。

## 1. 验收环境与工件

| 项 | 值 |
|---|---|
| 时间 | 2026-11-08（本地时间 UTC+08:00） |
| 源代码 | `4ad01ffce6f6da47e336f8197014b2479d951254` + 阶段 6 改动；验收后提交为 `7702f03`（代码内容相同） |
| 操作系统 | Windows 11 Home 10.0.26200 |
| Python / 工具 | Python 3.11，`uv`；pytest 9.1.1 |
| CoppeliaSim | 4.10.0.0，Bullet，`simulation_step=0.05` |
| ZMQ Remote API | `localhost:23000` |
| 工件 | `v1/L5_UFLOW/v001_20261103_004317`（`protected=true`） |
| 场景 SHA-256 | `bf7e4df6681abf8027e3f04e3bb214fc5a1f707d1be8922a733fce424a2a352c` |
| manifest 校验摘要 | `1bc46e1bf036578e6da4ae2dec800778781b54855f0771b837cec5076cb7d38e` |
| config digest | `e292c37f6d2a611763a9d76f508ccb219e16c089e3ce6e6e67f9c700495b6c7c` |
| layout | `L5_UFLOW`；canonical layout SHA-256 `0b75e7bfa2ce7dbdf05b4736ba03d6ea64c31b73841a8a569f9d11f154486ae8` |
| 工件校验 | `validation.json` `ok=true`，`validated_at=2026-11-03T00:46:23+08:00` |
| 订单契约 | `v1_order_20261106` |
| 静态目录 | `v1_process_20261106a` |
| planner 策略 | `v1_planner_20261107` |
| 失败策略 | `v1_failure_20261106` |

验收 DB 为独立临时库 `.tmp/order_accept/ui.db`，未写入受保护工件目录。正式
证据收敛在
[`v1_order_dag_phase6_acceptance_20261108.json`](../evidence/v1_order_dag_phase6_acceptance_20261108.json)；
完整运行报告保存在被 Git 忽略的 `docs/implementation/runs/`。

## 2. 本阶段代码改动

阶段 6 只修正实机验收暴露的接线/验收判据问题，不改变订单模型、调度策略、
执行器运动参数、场景工件或 IK/碰撞/安全阈值：

| 文件 | 改动 | 原因 |
|---|---|---|
| `application/order_runner.py` | `connect()` 返回值改为 `_client, sim, simIK` | 修复 runner 启动时解包错误 |
| `application/order_runner.py` | `OrderRunnerConfig.catalog_source`；运行记录写实际来源 | 目录来源为代码目录时不再误记为 `database` |
| `application/coordinator.py` | 订单验收新增 `order_target_count`，以目标实例的实际输出 UID 数量判定 | 目标为中间物料（如 `SEMI_CE`）时不再用 `full_flow` 的 FINAL 数量误判 |
| `cli.py` | `api serve` 新增 `--sim-host`（默认 `localhost`）、`--sim-port`（默认 `23000`）；`_order_runner` 分离 HTTP 绑定与 ZMQ 端点 | 修复 API 进程把 HTTP 8080 当作 ZMQ 端口导致的 `NO_ACTIVE_WORKER` |
| `tests/test_v1_order_sim.py` | 新增真实仿真门控用例：手工/planner service 运行、NODE 前置拒绝、GLOBAL 安全锁存 | 阶段 6 focused 证据与可重复入口 |
| `frontend/e2e/real_acceptance.mjs` | 新增真实 UI 验收脚本：`manual`/`planner`/`start`/`verify`；终态后刷新核验 | UI 旅程不使用 route mock；验证刷新恢复 |

`order_target_count` 只统计订单目标实例的输出 UID；`final_count` 仍表示
`MaterialRegistry` 中 `FINAL` 记录数。两者对 `FINAL` 目标一致，对中间目标
分别表达目标实例完成和最终产物数量，避免把 `SEMI_CE` 订单误判为失败。

## 3. 静态闸门与基线回归

### 3.1 启动前静态闸门

- 阶段 1–5 的订单契约、编译、失败隔离、service/API、planner 和前端测试在
  阶段 5 报告中通过；本阶段新增测试见第 12 节。
- 两份订单分别在 UI 的服务端 preview 通过后才保存/启动；compiler digest
  与保存版本的 `dag_digest` 一致。
- 启动前 `status --offline` 显示 lifecycle `SCENE_VALIDATED`、无运行锁；
  工件 manifest/validation 未被修改。
- 未对场景 artifact 执行 build、migrate、覆盖或放宽阈值操作。

### 3.2 `full_flow` 基线回归

使用 `FLEXIBLE_FACTORY_SIM=1 uv run pytest -q tests/test_v1_stage4_scheduler_sim.py -s`
在阶段 6 前一版本执行基线：

```text
1 passed in 1664.26s (0:27:44)
```

证据：`docs/implementation/runs/v1_stage4_full_flow_20261107_130958.json`。
关键判据：`all_ok=true`；仿真 335.85 s、6717 步；1 个 FINAL，父 UID
`[[1, 3]]`；2 条载货路线；11 次调度决策；资源释放；无安全锁存/冻结。
与阶段 4 基线和 `v1_stage0_1_full_flow_acceptance_20261103_132919.json`
的既有工序、路线和物料判据一致，未发现订单改动导致的默认路径回归。该基线
在阶段 6 源码改动前执行；阶段 6 的 `acceptance()` 改动只增加
`order_target_count`，未改 `full_flow` 计数逻辑。

## 4. 用例 A：UI 手工订单

### 4.1 订单定义

通过真实前端 `manual` 模式在订单编辑器创建并保存 7 节点订单：

- `order_id=accept-ui-manual-1794026712760`，`version=1`，
  `source=manual`，`scene_id=L5_UFLOW`；
- 目标 `FINAL × 1`；输入 `RAW_A × 1 @ line_s1a`、`RAW_D × 1 @ line_s2d`；
- 节点：`housing_direct_A-01`、`housing_finish-01`、
  `housing_to_E_buffer-01`、`bracket_process-01`、`transport_DE-01`、
  `bracket_to_E-01`、`kit_join-01`；
- 6 条端口边连接两条支路和 `kit_join` 的 `housing-input` /
  `bracket-input`。

`dag_digest=a231a53b9b92bb3aea144f6bee329b589ba11db5342a5b66420ae9e2a00c190f`，
catalog `v1_process_20261106a`，contract `v1_order_20261106`。UI 输入的
`node_id/port_id` 为空，compiler 将其确定性绑定到 `housing_direct_A-01:input-0`
和 `bracket_process-01:input-0`；编译结果
`order_plan.material_plans` 明确记录 `uid=1` / `uid=2` 的绑定。

### 4.2 运行结果

| 项 | 结果 |
|---|---|
| run_id | `order_accept-ui-manual-1794026712760_20261108_005154_7f2d91` |
| 启动方式 | UI 订单详情“确认启动”；API worker 独占 ZMQ |
| 起止 | `2026-11-08T00:51:54Z` → `2026-11-08T01:06:56Z`（墙钟约 15 min） |
| 仿真时间 | 163.7 s |
| 订单终态 | `SUCCEEDED`（API、DB、UI 一致） |
| 验收 | `acceptance.ok=true`；`order_status=SUCCEEDED`；`order_target_count=1`；`final_count=1` |
| FINAL 谱系 | `FINAL(uid=3)`，父 UID `[1, 2]`，`pair_key=1+2`，`kit_join` 实例 `...:kit_join-01:1+2` |
| 节点 | 7/7 `DONE`；`operations_failed=0`；`any_task_failed=false` |
| 资源 | `resources_released=true`；`controllers` 为空；`resource_checks` 通过 |
| 安全 | `any_safety_latch=false`；`aborted=false`；`frozen=false` |
| 路线 | `A_to_D_empty`、`D_to_E_loaded` 均 `DONE`；`route_ok=true`；载货路线 1 条 |
| 调度 | `scheduler_ok=true`；6 次决策（6 个运行实例）；错误为空 |
| 运行记录 | `run_record.ok=true`，状态 `DONE`；3 个物料、7 个工序实例、8 条输入、7 条输出、2 条谱系边、60 条资源事件、57 条运行事件 |
| 生命周期 | `SCENE_VALIDATED`，`run_id=null`，无 lock owner；runner 已停止仿真并 `runtime.destroy()` |
| UI 刷新 | 刷新前后徽标均为“成功”，节点表 7 行；“0 失败 · 0 取消” |

节点 UID 链（API/DB 一致）：

```text
housing_direct_A-01   in [1]   out [1]   DONE
housing_finish-01     in [1]   out [1]   DONE
housing_to_E_buffer-01 in [1]  out [1]   DONE
bracket_process-01    in [2]   out [2]   DONE
transport_DE-01       in [2]   out [2]   DONE
bracket_to_E-01       in [2]   out [2]   DONE
kit_join-01           in [1,2] out [3]   DONE
```

UI 页面、`GET /api/runs/{run_id}`、`GET /api/runs/{run_id}/nodes`、
`GET /api/runs/{run_id}/events`、`order status/nodes/events`（同一 `ui.db`）
和完整 JSON 报告字段一致；刷新后按 run URL 恢复终态和节点表。

## 5. 用例 B：planner 订单

### 5.1 planner 输入与保存

在真实前端 PlannerPage 使用默认目标/投料生成候选，再通过“保存草稿并编辑”
保存为普通不可变订单：

- `order_id=planner-draft`，`version=1`，`source=planner`，
  `scene_id=L5_UFLOW`；目标 `FINAL × 1`，投料 `RAW_A × 1`、`RAW_D × 1`；
- `planner_policy_version=v1_planner_20261107`，`planner_candidate_count=2`；
- `dag_digest=c053e1259692e4ddec14f5f15b149d88a8933290f1f050fa359f3d49d79bc269`，
  与 planner explain `digest` 一致；
- 选中 `cand-000`：`housing_direct` + `bracket_route` →
  `kit_join`；解释中 `join_bindings` 明确 `housing-input` /
  `bracket-input` 的路线和模板；
- 候选 `cand-001` 因 `PLANNER_INPUT_INSUFFICIENT`（`RAW_B@line_s1b 缺 1`）
  被拒绝；搜索统计 `expansions=3`、`branches=5`、`candidates=2`、
  `feasible=1`、`truncated=false`；
- 未建模维度仍为设备健康/节拍、实时资源与 AGV 排队、几何可达性/碰撞/运动
  可行性、局部失败风险和跨订单优化。

订单节点为 planner 生成的
`housing_direct_A-00`、`housing_finish-01`、`housing_to_E_buffer-02`、
`bracket_process-03`、`transport_DE-04`、`bracket_to_E-05`、
`kit_join-06`，输入节点/端口由 planner 显式写出，随后仍经同一
validator/compiler/preview 保存。

### 5.2 运行结果

| 项 | 结果 |
|---|---|
| run_id | `order_planner-draft_20261108_010853_cfaf39` |
| 启动方式 | 保存后进入订单详情，UI“确认启动”；同一 API worker/runner |
| 起止 | `2026-11-08T01:08:53Z` → `2026-11-08T01:24:11Z`（墙钟约 15 min） |
| 仿真时间 | 163.8 s |
| 订单终态 | `SUCCEEDED`（API、DB、UI 一致） |
| 验收 | `acceptance.ok=true`；`order_target_count=1`；`final_count=1`；父 UID `[[1, 2]]` |
| 节点 | 7/7 `DONE`；无失败/阻断 |
| 资源/安全 | `resources_released=true`；`any_safety_latch=false`；无冻结/中止 |
| 路线/调度 | `route_ok=true`；1 条载货路线；`scheduler_ok=true`（6 次决策） |
| 运行记录 | `run_record.ok=true`；7 个工序实例、3 个物料、57 条事件 |
| UI 刷新 | `RUN_RELOAD_STATUS=成功`，刷新后节点表 7 行；与手工订单同页恢复行为 |
| 来源追溯 | `planner_policy_version`、输入摘要、explain、候选数、`dag_digest` 均写入不可变订单版本；启动版本与保存版本一致 |

planner 生成的 DAG 只决定静态工艺路径；上述真实运行额外证明了本次
`FINAL × 1` 组合的物理执行成功，不证明 planner 未建模的节拍、设备健康、
碰撞或任意候选可行。

## 6. 用例 C：NODE 前置拒绝

### 6.1 注入与边界

`tests/test_v1_order_sim.py::test_node_precheck_rejects_only_dependent_branch`
在真实场景中构造 `accept-node-precheck` 订单（7 节点，目标 `FINAL × 1`），
用只报告 `NODE_PRECHECK_REJECTED` 的受控 executor 替换 `arm_A`：

- 不创建真实 arm 控制器，不执行任何运动；
- `ExecutionReport.finished=True`、`physical_action_started=False`、
  `failure_code=NODE_PRECHECK_REJECTED`；
- 不修改 scene artifact、配置、IK/碰撞/安全阈值。

同一运行中支架支路 `bracket-start → bracket-transport → bracket-buffer` 与
壳体支路无依赖，用来验证局部拒绝不停止独立分支。

### 6.2 结果

| 项 | 结果 |
|---|---|
| order / run | `accept-node-precheck` v1 / `sim-node-precheck` |
| 驱动 | 3170 步，墙钟 414.6 s，仿真 158.5 s，`finished=true` |
| 全局状态 | `frozen=false`，`aborted=false`，无安全锁存 |
| 失败事件 | `scope=NODE`、`failure_code=NODE_PRECHECK_REJECTED`、`node_id=housing-start`、`instance_id=sim-node-precheck:housing-start:1`、`device_id=arm_A`、`sim_time=0.0` |
| 安全前置 | `physical_action_started=false`、`material_uid=1`、`material_zone=line_s1a`、`material_type=RAW_A`、`material_state=CREATED`、`ledger_unchanged=true`、`resources_released=true`、`held_resources=[]` |
| 阻断 | `blocked_nodes=[housing-finish, housing-buffer, kit-join]`，实例均为 `CANCELLED`，`blocked_by=sim-node-precheck:housing-start:1` |
| 继续支路 | 支架 3 节点 `DONE`，`continued_nodes=[bracket-start, bracket-transport, bracket-buffer]` |
| 订单终态 | 失败记录时 `PARTIAL_FAILED`（支架分支尚可完成）；收敛后 `order_status=FAILED`；`acceptance.ok=false`，`order_target_count=0` |
| 资源 | `resources.owners` 全部为空；`controllers` 为空 |

该用例只准入 `NODE_PRECHECK_REJECTED`；没有让真实 Arm/AGV 产生运动失败，
也没有把 `IK_FAILED`、抓取/放置失败或账本不确定提升为 NODE。失败记录中的
`PARTIAL_FAILED` 是失败瞬间的状态投影，最终收敛以 `order_status=FAILED`
为准；测试显式断言最终状态和物料位置。

证据：
`docs/implementation/runs/v1_order_node_precheck_20261108_093342.json`。

## 7. 用例 D：GLOBAL 安全锁存

`tests/test_v1_order_sim.py::test_global_safety_latch_aborts_order` 在真实
`_short_order` 运行中，待 `arm_A` 控制器建立且已步进至少 2 步后，调用真实
`UnitController._safety_fail("ACCEPTANCE_INJECTED_SAFETY")`。这不是 mock
异常，而是场景安全锁存路径；注入只影响本次会话，不修改工件。

| 项 | 结果 |
|---|---|
| order / run | `accept-global-safety` v1 / `sim-global-safety` |
| 注入 | `injected=true`，仿真时间约 0.05 s；锁存 `SAFETY_LATCH`，原因 `ACCEPTANCE_INJECTED_SAFETY` |
| 驱动 | 22 步；`aborted=true`，`frozen=true` |
| 订单终态 | `order_status=ABORTED`；`acceptance.order_status=ABORTED`；`ok=false`；目标/成品数量 0 |
| 安全锁存 | `engaged=true`；`stop_commanded=true`；`stop_command_ok=true`；`stop_confirmed=false`（仅 1 次确认检查）；`stop_speed=0.09998` |
| 资源 | `resources_released=true`；全部资源 owner 为空；`controllers` 为空 |
| 节点 | `housing-start=FAILED`；`housing-finish`/`housing-buffer=PENDING`；无节点报 `DONE` |
| 失败范围 | 失败记录 `scope=GLOBAL`、`failure_code=SAFETY_LATCH`；存在 `FREEZE` 事件 |

`stop_confirmed=false` 表示安全停止确认只完成了 1 次检查，不能据此声称设备
已完全静止；操作员仍需按现场/仿真安全规程确认停止态、物料和共享设备状态，
然后才能开始下一次运行。测试证明了全局停止、资源释放、锁存保留和无节点
误报完成，没有证明自动恢复或安全确认闭环。

证据：
`docs/implementation/runs/v1_order_global_safety_20261108_093607.json`。

## 8. step/连接故障边界

本阶段没有切断真实 ZMQ 会话，也没有在共享仿真上破坏 stepping；按方案允许的
“有界 harness/受控故障”复用记录型协调器测试：

- `tests/test_v1_order_failure_isolation.py::test_coordinator_sim_step_failure_stops_all_and_marks_global`
  让注入的 `sim.step()` 抛错，断言协调器进入 `frozen/aborted`、记录
  `SIM_STEP_FAILED` GLOBAL 失败并释放资源；
- 该测试不建立真实 CoppeliaSim 连接，不能证明真实连接断开后的重连/清理
  行为。安全锁存用例（第 7 节）覆盖安全停止边界，但不等价于连接故障；
- 因此本报告不声称真实 ZMQ 断开/stepping 故障已实机验证；该边界仍按
  GLOBAL 处理，恢复需操作员确认仿真停止态和现场状态。

现有 `full_flow` 的 `conveyor_stop`/fault harness 不属于订单链路，未被当作
本阶段订单 GLOBAL 证据。

## 9. 失败尝试与修复

- 第一次 UI 手工订单运行
  `order_accept-ui-manual-1794026712760_20261108_004819_2c8038` 在启动前
  将 API HTTP 端口 `8080` 误当作 ZMQ 端口；worker 未能连接仿真，运行被标记
  `ABORTED`、`stop_reason=NO_ACTIVE_WORKER`。该失败 attempt 保留在验收 DB，
  未覆盖或删除。修复为 `api serve --sim-host/--sim-port` 后，以新的 run ID
  重跑，成功结果见第 4 节。
- 阶段 6 还修复了 `order_runner` 的 `connect()` 返回值解包错误和
  `catalog_source` 误记问题；两者均在首次实机尝试中暴露，修复后重跑。
- 手工运行中出现 `IK_FAILED` 直线转移，控制器按既有策略选择
  `horizontal_detour_+0.30` 并完成；未放宽 IK/碰撞阈值，也未把该运行时
  重规划计为订单失败。该行为与既有 `full_flow` 基线一致。

## 10. UI/API/DB/报告一致性核验

| 核验 | 手工订单 | planner 订单 |
|---|---|---|
| API run status | `SUCCEEDED` | `SUCCEEDED` |
| `order status`（同一 `ui.db`） | `SUCCEEDED` | `SUCCEEDED` |
| `order nodes` | 7 行，全部 `DONE`，UID 链与 API 相同 | 7 行，全部 `DONE`，UID 链与 API 相同 |
| 事件 | 57 条，`COORDINATOR_START` → … → `RUN_END` | 57 条，起始/终止一致 |
| UI 刷新 | 徽标“成功”，节点表 7 行 | 徽标“成功”，节点表 7 行 |
| 运行报告 | 完整 JSON 报告、`run_record.ok=true` | 完整 JSON 报告、`run_record.ok=true` |
| digest/版本 | 保存版本 `dag_digest` = 运行 digest | planner explain digest = 保存/运行 digest |
| 资源/生命周期 | 资源清零，`controllers` 空，lifecycle 无锁 | 资源清零，`controllers` 空，lifecycle 无锁 |

UI、HTTP API、SQLite 订单运行记录和 `docs/implementation/runs/` 完整报告使用
同一 `run_id`、订单版本、DAG digest 和 artifact；页面刷新前后状态与节点表
一致，未把 worker/API 中断渲染成成功。首次 UI 尝试的 `NO_ACTIVE_WORKER`
`ABORTED` 记录保留为失败 attempt，见第 9 节。

故障测试结束后直接 ZMQ 探测 `sim_state=0`；lifecycle 为 `SCENE_LOADED`，
`run_id=null`，无 lock owner/alive。测试 harness 未调用 `prepare_start`，因此
不产生订单运行记录；这符合 focused 故障用例的边界。

## 11. 未执行项与边界

- `tests/test_v1_order_sim.py` 中的
  `test_manual_order_service_run_succeeds` 和
  `test_planner_order_service_run_succeeds` 是 service 级入口；本阶段的正向
  实机预算由第 3.2 节基线、第 4 节手工订单和第 5 节 planner 订单覆盖，这两
  个用例未单独执行，不能按通过报告。
- 真实 ArmExecutor/AgvExecutor 的运动类 failure code 未做 focused 物理
  验收，仍是 GLOBAL；本阶段只准入 `NODE_PRECHECK_REJECTED`。
- 连接断开/stepping 故障未切断真实 ZMQ 会话；只引用记录型 coordinator
  harness 的 `SIM_STEP_FAILED` 证据，不能声称真实连接故障已实机验证。
- 一次性 UI 订单只验证当前登记模板和绑定设备组合，不证明任意 DAG、未登记
  设备替换、其他场景或任意运动路径可行。
- 本阶段不覆盖多订单并行、跨场景调度、故障自动重试/自动恢复、运行中修改
  DAG 或 MES/ERP 集成。

## 12. 回归与测试结果

| 测试 | 结果 |
|---|---|
| 阶段 6 故障用例 | `FLEXIBLE_FACTORY_SIM=1 uv run pytest -q -s tests/test_v1_order_sim.py::test_node_precheck_rejects_only_dependent_branch tests/test_v1_order_sim.py::test_global_safety_latch_aborts_order` → **2 passed in 706.59s (0:11:46)** |
| full_flow 基线 | `tests/test_v1_stage4_scheduler_sim.py` → 1 passed in 1664.26s，`all_ok=true` |
| 全量非仿真回归 | `uv run pytest -q` → **598 passed, 7 skipped in 61.01s**（4 个为 `tests/test_v1_order_sim.py` 的仿真门控用例） |
| 前端单元 | `npm test` → 3 files / **12 passed**，Duration 26.50s |
| 前端 Playwright mock journeys | `npm run e2e` → **3 passed** (8.3s) |
| 前端构建 | `npm run build` → `tsc --noEmit` + Vite 构建通过（131 modules，3.79s） |

`test_v1_order_sim.py` 的 service 级手工/planner 用例未单独执行；其正向路径
已由第 4/5 节 UI 运行覆盖。全量非仿真回归中该文件 4 个 `require_sim` 用例
按 skip 记录，不能按通过报告。

## 13. 发布结论

阶段 6 验收矩阵在本机 CoppeliaSim 4.10 和受保护
`v1/L5_UFLOW/v001_20261103_004317` 上执行完毕：

1. `full_flow` 基线保持 1 个 FINAL、父 UID `[[1, 3]]`、2 条载货路线、
   11 次调度决策、资源清零、无安全锁存；未发现默认路径回归。
2. UI 手工订单 v1 和 planner 订单 v1 均通过同一 compiler/runner 完成 1 个
   FINAL，父 UID `[1, 2]`，目标数量和资源/生命周期判据通过；保存版本、
   planner 元数据、DAG digest、run ID 可追溯。
3. `NODE_PRECHECK_REJECTED` 在物理动作前、物料/账本可核实时只阻断依赖后继，
   独立支架支路完成；订单最终 `FAILED`，无冻结/全局中止。
4. 真实安全锁存注入使运行 `ABORTED`、全部资源释放、锁存保留、没有节点
   误报 DONE；`stop_confirmed=false` 仍需操作员完成现场确认。
5. 全量非仿真回归 598 passed / 7 skipped；前端单元 12 passed、mock E2E
   3 passed、构建通过。

**发布判断：**阶段 6 范围内可发布，使用入口为 `flexible-factory` 的订单
CLI、`api serve` 和 `frontend/` 工作台；API 的 HTTP 端点与 worker 的
`--sim-host/--sim-port` 必须分开配置。

**发布不表示：**任意 DAG/设备替换已物理可行；真实 Arm/AGV 运动类失败已获
NODE 准入；真实 ZMQ 断开/stepping 故障已实机验证；自动恢复、故障重试、
多订单并行、MES/ERP 或跨场景调度已完成。上述边界仍按 GLOBAL 或未实现处理，
恢复/重跑前由操作员确认仿真停止态和现场状态。

## 14. 证据清单

| 用例 | 正式证据 | 说明 |
|---|---|---|
| 工件/环境 | 本节第 1 节；`validation.json` | 受保护工件只读校验 |
| `full_flow` 基线 | `docs/implementation/runs/v1_stage4_full_flow_20261107_130958.json` | 完整运行报告（Git 忽略），摘要进正式 evidence |
| 手工订单 | 正式 evidence `manual_ui` 段；`docs/implementation/runs/v1_order_run_20261108_090612.json` | UI 创建/启动，run/节点/digest/谱系 |
| planner 订单 | 正式 evidence `planner_ui` 段；`docs/implementation/runs/v1_order_run_20261108_092329.json` | planner 元数据 + 同一 compiler/runner |
| NODE 前置拒绝 | 正式 evidence `node_precheck` 段 | `NODE_PRECHECK_REJECTED` 注入证据 |
| GLOBAL 安全锁存 | 正式 evidence `global_safety` 段 | 真实 `safety._safety_fail` 注入 |
| step 故障 harness | `tests/test_v1_order_failure_isolation.py::test_coordinator_sim_step_failure_stops_all_and_marks_global` | 记录型边界，不是真实断连 |
| UI 刷新 | `frontend/e2e/real_acceptance.mjs verify` 输出；正式 evidence `consistency` 段 | 同 run URL 刷新恢复 |

大型逐 tick `controller_history`、重复控制器 evidence 和原始日志不复制到
`docs/implementation/evidence/`；完整输出保留在被 Git 忽略的
`docs/implementation/runs/`。
