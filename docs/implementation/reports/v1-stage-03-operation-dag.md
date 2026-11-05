---
title: v1 混线阶段 3 实施报告 — 配方实例化与工序 DAG
type: report
status: review
created: 2026-11-03
updated: 2026-11-04
scope: v1 UFLOW / 阶段 3
---

# v1 混线阶段 3 实施报告：配方实例化与工序 DAG

本报告记录
[06-21 阶段 3 规划](../../wiki/06-工业案例/柔性车间/当前规划/06-21-混线生产阶段3配方实例化与工序DAG.md)
在 v1 代码（`src/flexible_factory/`）上的实施结果。阶段目标是用默认工艺数据
驱动既有流程：配方实例化、依赖 DAG、ready 集合与工序解锁；不引入订单实体、
动态选路、返工循环或异常恢复流程。

结论：代码级步骤 1–6 与步骤 7 的支路切换均已完成，纯逻辑测试
**426 passed / 2 skipped**；`full_flow` 实机验收**通过**（`all_ok=true`），
与阶段 0 基线逐项一致：FINAL 1 件（UID 4，父 UID `[1,3]`）、未配对
`SEMI_CE` 1 件、2 条载货航线、13 个任务全部 `DONE`，12 个工序实例全部提交，
仿真时间 337.85 s 对基线 337.45 s（+0.4 s），详见 §5。

## 1. 代码落点

| 模块 | 内容 |
|---|---|
| `domain/operation_runtime.py`（扩展） | `RecipeRunContext`（run/recipe 版本/入口物料/选定 route）；`OperationInstance` 增加 `task_id`/`run_id`/`selected_route`/`predecessor_instance_ids`/`input_uids`/`output_uids`/`capability`/`candidate_device_ids`/`current_work_step`/`sequence`/`dispatched`/`committed_sim_time`；`OperationGraph`（索引、ready、`link()`、图级校验）；`OperationRuntime`（`instantiate_entry`/`add_join`/`mark_started`/`commit_success`/`mark_failed`/`completed_output`）；`legacy_task_fields()` 成为模板→旧任务字段的唯一派生 |
| `domain/process_model.py`（扩展） | `OrderEntry.route_variant_id`（入口 → 固定路线）；`_check_entry_route()` 校验路线存在、首工序、group 与 `entry_zone` 自洽；未填写时要求同组唯一候选 |
| `domain/default_process.py`（扩展） | 入口固定对应 `s1a→housing_direct`、`s1b→housing_alt`、`s2d→bracket_route`；`PROCESS_MODEL_VERSION` → `v1_process_20261103b` |
| `domain/tasks.py`（扩展） | `Stage4Task.from_operation()` 直接代理既有实例；`make_task_from_operation(..., instance=...)` 只做投影，不新建第二个实例；新增 `instance_id`/`runtime_backed` 只读属性 |
| `application/coordinator.py`（修改） | `_spawn_source()` 按入口建图并发 `OPERATION_GRAPH` 事件；`_dispatch_ready()` 逐 tick 把 ready 实例投影为任务；`_mark_started`/`_commit_operation`/`_fail_operation` 统一任务↔实例状态迁移；`_commit_unit_result()` 改为「账本成功 → 确认 DONE」并返回成功标志；`_after_route()` 载货航线走同一成功边界；`_check_join()` 改为 runtime join handler；`finished`/`acceptance`/`summary` 增加图完成度与实例证据；删除 `_submit_followup()` 与静态 `successors` 推进 |
| `domain/process_model.py`、`domain/operation_runtime.py` | **未新增** `recipe_loader`、schema/DSL、独立 scheduler 或数据库；按改动边界要求仍在原模块内实现 |

关键语义落点：

- **实例 ID**：`{run_id}:{uid}:{operation_id}`（如 `run0001:1:housing_direct_A`），
  不依赖模板元组排列；任务队列/证据键仍是旧格式 `{kind}_uid{n}`，两者在
  `OperationGraph` 内一一映射，既有证据字段不改名。
- **只有选中路线**：`instantiate_entry()` 从 `entry.route_variant_id` 取路线，
  按 `route.operation_ids` 顺序建链；`housing_finish` / `housing_to_E_buffer`
  每个壳体 UID 只生成一次，前驱指向该 UID 实际走到的前一道工序，`RAW_A` 的
  实例不会把 `housing_alt_A` 写成前驱。
- **ready 语义**：入口节点初始 ready；其余实例要求**全部实际前驱** `DONE`
  且 `committed_sim_time` 已写入才解锁。排序键是
  `(priority, sequence, instance_id)`，与 recipe 节点排列无关。
- **成功边界**：executor `SUCCEEDED` 只触发 `_verify_unit_task`；验证通过后
  `_commit_unit_result()` 先提交类型/区域账本，再 `commit_success()` 把实例
  标 `DONE` 并登记 `output_uids`。重复提交、前驱未提交、已 FAILED 均抛
  `OperationRuntimeError`。失败前驱不解锁后继，不重试、不改线。
- **E 合流**：`_check_join()` 仍用 `flow.pair_candidates()` 选配对，但要求
  两件物料分别来自图中**已完成并提交**的输入工序
  （`completed_output()` 按端口 zone/type 解析），随后 `add_join()` 建绑定
  两个父实例的 `kit_join` 节点，账本 `commit_join()` 成功后才 `commit_success`。
  输入未就绪则不建节点、不生成虚假任务。

## 2. 测试落点

| 测试 | 用例数 | 覆盖 |
|---|---|---|
| `tests/test_v1_operation_runtime.py`（新增） | 20 | 入口固定选路、未选路线不实例化、共享尾段唯一、实例 ID 确定性、重复任务键拒绝、ready 逐步解锁、`DONE` 未提交不解锁、重复提交/未解锁提交拒绝、失败不解锁、ready 稳定排序、图级 cycle/闭合性/物料错配/未选路线/缺终点诊断、join 绑定与端口校验 |
| `tests/test_v1_coordinator_dag.py`（新增） | 5 | 单 UID 直达支路端到端（ready→入队→提交→解锁→收敛）、未选路线无任务、验证失败冻结且不解锁、合流只提交一次、缺少父工序时不建节点 |
| `tests/test_v1_coordinator_process.py`（更新） | 5 | ready 派工只提交入口工序、重复派工不重复入队、逻辑合流不生成 `Stage4Task` |
| `tests/test_v1_process_model.py`（更新） | 21 | 入口 → 路线固定对应；留空时同组唯一推断；未知路线/首工序不符/group 不符的负例 |
| `tests/test_v1_metrics_contract.py`（更新） | — | 契约 `process_model_version` 同步为 `v1_process_20261103b` |

`tests/test_v1_coordinator_dag.py` 用真实 `MaterialRegistry`（纯 Python 谱系）
+ 记录型执行适配器驱动，位置/物理/类型验证被替换为固定通过——几何判据由
实机 `full_flow` 负责，该文件只锁定编排语义。

无仿真环境全量：`python -m pytest -q -p no:cacheprovider`
→ **426 passed, 2 skipped**（阶段 2 基线 397+2，新增 29 个用例）。

## 3. 行为保持与差异

| 项 | 结论 |
|---|---|
| 事件顺序 | `TASK_DONE → MATERIAL_PLACE → (下一 tick) TASK_CREATED`；后继改为下一 tick 的 `_dispatch_ready()` 提交，比旧实现晚一个 step（0.05 s） |
| 任务/证据键 | `task_id`、`operation_instance_id`、`operation_template_id`、`process_model`、`materials`、`routes` 字段不变 |
| 新增证据 | 每条工序图一条 `OPERATION_GRAPH` 事件（`run_id`/`recipe`/`route`/`operations`）；`summary["operations"]`（runs + graph + 校验）；`acceptance` 增加 `graph_complete`/`operation_instances`/`operations_failed`；`FINAL_CREATED` 的 `detail` 增加 `instance` |
| 验收口径 | 非故障场景 `ok` 现在同时要求 `graph_complete`（已实例化节点全部 `DONE`）；`finished` 也要求图完成，避免队列空闲但图未走完时提前结束 |
| 失败路径 | 控制器/航线/验证失败仍冻结并按旧文案落账，只是改为经 runtime `mark_failed`；`_freeze`/超时仍直接用队列终态写入（同一实例对象，无双写） |
| 优先级老化 | 实例 `created_sim_time` 语义固定为「进入可派工队列的时间」，后继实例在 `claim_ready()` 时刷新；批次创建时间单独记在 `RecipeRunContext` |

## 4. 版本处置

| 版本项 | 结果 |
|---|---|
| `PRODUCT_VERSION` / `pyproject` | 1.0.0 未变（入口、运行模式与报告路径未变） |
| `SCHEMA_VERSION`、`SCENE_CONTRACT_VERSION`、`SCENE_BUILDER_VERSION` | 未变（未改场景对象/别名/契约字段） |
| `TASK_FLOW_VERSION`、`MATERIAL_MODEL_VERSION`、`RESOURCE_POLICY_VERSION`、`METRICS_VERSION` | 未变（任务类型、物料契约、资源顺序与指标定义未改） |
| `PROCESS_MODEL_VERSION` | `v1_process_20261103` → **`v1_process_20261103b`**（`OrderEntry` 增加路线元数据、入口固定选路） |
| `simulation/config*.py` 常量 | 未新增/未修改，`config_digest` 与 `layout_digest` 不变，受保护工件无需重建 |
| `CONTROLLER_ID` / `COORDINATOR_ID` / `RUNNER_ID` | 未变 |

## 5. 实机验收

环境：CoppeliaSim 4.10.0.0，ZeroMQ Remote API `localhost:23000`，当前 v1 工件
`v1/L5_UFLOW/v001_20261103_004317`（`config_digest` / `layout_digest` 未变，
未执行 `build`/`migrate`）。

```text
flexible-factory run --scenario full_flow --time 700
```

### 5.1 结果：通过

| 判据 | 阶段 0 基线（`v1_stage0_1_full_flow_acceptance_20261103_132919`） | 阶段 3（`v1_stage3_full_flow_20261104_153954`） |
|---|---|---|
| `ok` / `all_ok` | true | **true** |
| 仿真时间 / 步数 | 337.45 s / 6732 | 337.85 s / 6740 |
| `tasks_done` | true | true（13 个任务全部 `DONE`） |
| 工序实例 | 无该字段（列表驱动） | `graph_complete=true`，12 个实例 0 失败 |
| `final_count` | 1 | 1 |
| `final_parents` | `[[1, 3]]` | `[[1, 3]]` |
| FINAL UID / 类型 / 区域 | 4 / `FINAL` / `output_e` | 4 / `FINAL` / `output_e` |
| `unpaired_semi_ce` | 1（UID 2 在 `buffer_e_c`） | 1（UID 2 在 `buffer_e_c`） |
| `loaded_routes` / `route_ok` / `priority_ok` | 2 / true / true | 2 / true / true |
| 事件数 | 89 | 92（+3 条 `OPERATION_GRAPH`） |
| AGV 利用率 / `arm_A` 利用率 | 0.3228 / 0.2525 | 0.3228 / 0.2525 |

工序实例（`run:uid:operation`）共 12 个：`housing_direct` 3 + `housing_alt` 5 +
`bracket_route` 3 + `kit_join` 1；未选路线没有生成任何实例与任务
（`housing_alt_B`/`transport_BA` 不出现在 UID 1 的子图里）。

任务时间线对照（同一 `task_id`，单位 s）：

| 任务 | 基线 start→finish | 阶段 3 start→finish |
|---|---|---|
| `a_conveyor_uid1` | 0.85→48.90 | 0.85→48.75 |
| `b_conveyor_uid2` | 48.95→94.50 | 48.80→94.15 |
| `c_handoff_uid1` | 48.95→94.40 | 48.80→94.25 |
| `transport_ba_uid2` | 94.55→106.40 | 94.20→106.15 |
| `e_conveyor_uid1` | 94.45→124.40 | 94.30→123.85 |
| `a_agv_uid2` | 106.45→143.30 | 106.20→143.30 |
| `c_handoff_uid2` | 143.35→191.75 | 143.35→191.80 |
| `reposition_uid3_toD_leg2` | 143.35→204.70 | 143.35→204.75 |
| `d_conveyor_uid3` | 204.75→252.75 | 204.80→252.90 |
| `transport_de_uid3` | 252.80→275.80 | 252.95→275.95 |
| `e_agv_uid3` | 275.85→305.95 | 276.00→306.35 |
| `e_conveyor_uid2` | 306.00→337.40 | 306.40→337.80 |

差值 ≤ 0.4 s（约 8 个 step），来源是后继实例改由下一 tick 的
`_dispatch_ready()` 提交；没有出现跳步、重复派工或任务拒绝
（`rejections = []`）。

航线指标（判据：到站 ≤0.02 m、航向 ≤0.08 rad、滑移 ≤0.02 m）：

| 航线 | 基线 stop/heading/slip | 阶段 3 stop/heading/slip |
|---|---|---|
| `A_to_B_empty` | 0.01989 / 0.00017 / — | 0.01979 / −0.00003 / — |
| `B_to_A_loaded` | 0.01972 / 0.00023 / 0.00526 | 0.01956 / 0.00036 / 0.00201 |
| `A_to_D_empty` | 0.01965 / −0.00011 / — | 0.01963 / 0.00000 / — |
| `D_to_E_loaded` | 0.01948 / 0.00094 / 0.00880 | 0.01973 / −0.00012 / 0.00795 |

证据：
`docs/implementation/evidence/v1_stage3_full_flow_20261104_153954.json`
（71 KB，保留验收字段、12 个工序实例、13 个任务、4 条航线、92 条事件与
指标；逐 tick 控制器轨迹/规划详情被裁掉）。完整报告（17 MB）在
`docs/implementation/runs/v1_run_full_flow_20261104_153954.json`，不入版本库。

判据字段解读：

- `acceptance.graph_complete=true` 与 `operations.validation.ok=true` 说明
  全部已实例化节点都提交完成，且图级校验（唯一 ID、前驱闭合、无环、物料
  连通、选中路线终点、join 端口闭合）没有诊断。
- `kit_join` 节点 `run0001:4:kit_join` 的两个前驱分别为
  `run0001:1:housing_to_E_buffer` 与 `run0002:3:bracket_to_E`，输入 UID 为
  `[1, 3]`、输出 `[4]`，与 `final_parents=[[1,3]]` 一致；`rejections` 为空
  说明没有重复提交或重复配对。
- 未配对 `SEMI_CE`（UID 2）留在 `buffer_e_c`，与基线一致：`housing_to_E_buffer`
  完成后不会因为"没进合流"被回退或重派。

### 5.2 同一环境下的失败尝试（不是阶段 3 引入）

本次连续运行 3 次，前 2 次在运动层 `GOAL_CONFIG_INVALID` 处冻结；
阶段 2 之前（未含本阶段改动）的 `v1_run_full_flow_20261103_200427.json`
也在同一环节失败：

| 运行 | 代码 | 终止时间 | 冻结原因 |
|---|---|---|---|
| `...20261104_143918` | 阶段 3 | 158.45 s | `C 任务失败: GOAL_CONFIG_INVALID: 法向段 pre_place 第 33 点 lateral=0.0235 m` |
| `...20261104_151125` | 阶段 3 | 299.50 s | `E 任务失败: GOAL_CONFIG_INVALID: 法向段 pre_place 第 19 点 lateral=0.0243 m` |
| `...20261103_200427` | 阶段 2 之后、阶段 3 之前 | 310.25 s | `E 任务失败: RELEASE_FAILED: RELEASE_CORRECTION_EXCEEDED` |

三次都是在"AGV 停靠误差 ≈0.0195–0.0199 m（容差 0.02 m）→ 托盘物料偏移 →
pre_place 法向插值偏差超过 `CONTACT_MAX_LATERAL=0.020 m`"这一条链上失败，
与工序依赖/解锁无关：失败前的 `task_id` 序列、航线指标与成功运行一致。
成功运行的 `D_to_E_loaded` stop_error 为 0.01973 m，失败运行是 0.01985 m，
即 **0.12 mm 的停靠差异决定了成败**。这是既有运动容差余量问题（阶段 2
报告已记录 0.2 mm 余量），本阶段未放宽任何判据，也不应通过调参来"修好"
验收；建议作为独立课题处理停靠/接触余量。

## 6. 边界与后续

本阶段**不宣称**：

- 动态候选设备选路、返工/循环、基于调度代价的重派（阶段 4 起）；
- YAML/JSON 用户配方、任意用户代码、CP-SAT、数据库/MES 或订单实体；
- `no_pair`/`staggered_batch`/故障注入场景的阶段 3 逐项验收：本次只跑了
  用户入口 `full_flow`，其他 scenario 未执行，不得按"通过"报告；
- 运动容差余量治理（见 §5.2）。

后续：阶段 4 在 `OperationRuntime` 上增加候选设备/调度决策；届时 ready
集合与 `selected_route` 已经可以作为决策输入，不需要再改任务队列语义。

