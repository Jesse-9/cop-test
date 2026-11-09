---
title: v1 混线阶段 4 实施报告 — 产品变体与能力匹配调度
type: report
status: review
created: 2026-11-04
updated: 2026-11-04
scope: v1 UFLOW / 阶段 4
related:
  - docs/implementation/plans/production-flow/v1-stage-04-capability-scheduling-implementation.md
  - docs/implementation/reports/production-flow/v1-stage-03-operation-dag.md
  - ../../../wiki/06-工业案例/柔性车间/当前规划/06-22-混线生产阶段4产品变体与能力匹配调度.md
---

# v1 混线阶段 4 实施报告：产品变体与能力匹配调度

本报告记录
[阶段 4 实施方案](../../plans/production-flow/v1-stage-04-capability-scheduling-implementation.md) 与
[06-22 阶段 4 规划](../../../wiki/06-工业案例/柔性车间/当前规划/06-22-混线生产阶段4产品变体与能力匹配调度.md)
在 v1 代码（`src/flexible_factory/`）上的实施结果。阶段目标是：把已有
ready 工序、静态 capability/device 目录、资源表和执行适配器接入确定性
派工；默认 `full_flow` 仍按阶段 3 的固定路线与固定设备运行。

结论：WP0–WP4 已实现；WP5（等价候选扩展）未实施，WP6（AGV 停靠/接触
余量复核）未执行。纯逻辑测试 **450 passed / 3 skipped**；`full_flow`
实机验收**通过**：FINAL 1 件（UID 4，父 UID `[1,3]`）、12 个工序实例全部
完成、11 条物理调度决策全部选定 recipe 候选并取得资源、资源表清零，详见
§6。运动余量仍贴近阈值，不宣称稳定性已达标。

## 1. 代码落点

| 模块                                | 内容                                                                                                                                                                                                                      |
| --------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `domain/scheduling.py`（新增）        | `DeviceView`/`MaterialView`/`OperationView`/`SchedulingSnapshot`、`CandidateDecision`/`DispatchDecision`/`SchedulingResult`、`plan_ready()`；纯函数，不导入 simulation/application，不申请资源、不改工序状态                                   |
| `domain/operation_runtime.py`（扩展） | `OperationInstance.selected_device_id`；`OperationRuntime` 新增 `claim_instance`/`select_device`/`record_phase`/`record_resources`/`record_upgraded`/`mark_waiting`/`mark_blocked`/`mark_resumed`，作为 coordinator 唯一生命周期写入口 |
| `domain/tasks.py`（修改）             | recipe-backed `Stage4Task` 的生命周期 setter 抛 `TaskError`；`TaskQueue.start/wait/set_phase/block/unblock/done/fail` 拒绝 runtime-backed 任务；`as_dict()` 追加只读 `selected_device_id`                                               |
| `application/coordinator.py`（修改）  | `_dispatch_ready()` 构造快照、调用 `plan_ready()`、记录 `SCHEDULE_DECISION`/`SCHEDULE_WAIT`、认领实例并登记选定设备；`_poll_unit()` 只启动本单元被选中的任务；资源申请仍用 `ResourceTable.acquire_all()`；所有运行时状态写入改走 runtime 窄接口                                  |
| `spec.py`（版本）                     | 新增 `SCHEDULER_POLICY_VERSION = "v1_scheduler_20261104"`；不修改 `simulation/config.py`，`config_digest` 与受保护工件不变                                                                                                             |
| `tests/`（新增/扩展）                   | `test_v1_scheduling.py`（纯策略）、`test_v1_coordinator_scheduler.py`（接线/资源/全批次 dry-run）、`test_v1_stage4_scheduler_sim.py`（`full_flow` 实机）、既有 runtime/task/process/import 测试扩展                                                |

未新增：订单/MES 实体、用户可编辑 recipe、表达式/DSL、全局优化求解、
运行中抢占、故障重派、自动重试、第二个仿真 scenario 或 MCP 能力。

## 2. WP0：runtime-backed 写入口收敛

阶段 3 的 `Stage4Task` 同名字段虽然代理 `OperationInstance`，但 setter 仍可
任意改写状态、时间、资源与证据，绕开 `OperationRuntime` 的提交/失败语义。
本阶段：

- 生命周期 setter（`status`/`phase`/`priority`/`attempts`/时间字段/
  `failure_reason`/`evidence`/`resources`/`upgraded`）对
  `runtime_backed=True` 的任务抛 `TaskError`，信息明确指向
  `OperationRuntime`；
- `TaskQueue` 的状态方法只服务非配方任务（空载调机/兼容测试）；对配方任务
  抛 `TaskError`，不产生“队列改了、运行图没改”的双写；
- coordinator 的必要写入改为窄接口：等待/阻塞/恢复/资源/阶段/升级/选定
  设备分别对应独立方法；`mark_started`/`commit_success`/`mark_failed`
  保持原有语义；
- 只读字段与 `as_dict()` 兼容读取不变，既有证据键名未改，仅追加
  `selected_device_id`；
- 空载 `reposition`、测试注入的非配方任务继续走 `TaskQueue`，通过
  `runtime_backed` 明确隔离，未引入通用 setter。

边界测试覆盖：13 个生命周期字段直接赋值被拒绝；7 个队列状态方法对配方
任务被拒绝；合法 runtime 迁移后任务视图与序列化保持同一实例；等待/阻塞/
恢复证据仍落在实例上。

## 3. WP1/WP2：纯调度输入输出与候选筛选

`domain/scheduling.py` 只消费只读快照：

- 输入：ready 工序的稳定实例 ID、recipe 候选（按声明顺序）、能力要求、
  优先级、ready 时间、输入/输出物料与区域、所需资源、source/sink/dock、
  当前活动执行与资源 owner 只读视图、物料状态、目标区域容量、AGV 停靠
  状态、已验收的 `(device, operation)` 组合；
- 输出：每个工序一条 `DispatchDecision`，包含候选全集、逐项
  `reasons`（阻断）与 `conditions`（不阻断的执行前条件）、最终选定设备或
  `defer_reason`、策略版本、仿真时间与 `not_modeled` 维度（设备健康、
  工具载荷、姿态包络）；字段全部可 JSON 序列化，不含句柄、对象 repr 或
  字典遍历依赖；
- 排序：业务/配套优先级（当前为 operation priority）→等待时间降序→ready
  时间→稳定 instance ID；候选按 recipe 声明顺序；
- 筛选顺序：recipe allowlist → 静态执行器类型/能力 → 设备绑定与资源/区域
  覆盖 → 物料/区域前提 → 未验收动态几何组合 → 设备占用/资源 owner 条件。
  设备忙时优先选空闲替代候选；全部忙时仍选择排序第一的可用候选并记录
  `DEVICE_BUSY`，工序保持可等待，不丢派工；资源 owner 冲突和 AGV 停靠
  记录为 `conditions`，真正拿锁由 coordinator 的 `acquire_all()` 决定。

实现中发现并规避了一个死锁：若把“AGV 不在目标 dock”当作候选拒绝，B/D
装载任务不会入队，`_poll_agv()` 也就没有调机请求，AGV 永远不会到位。
因此 AGV 停靠属于入队后的执行前条件，不是候选拒绝；这与阶段 3 的任务
队列/AGV 调机依赖一致。

默认 recipe 的 10 个工序（含逻辑合流）都能在纯策略测试中解释固定设备：
`test_every_default_operation_explains_its_fixed_device()` 逐工序核对
allowlist、能力、绑定资源/区域与基线设备选择。

## 4. WP3：coordinator 接线与原子资源

- `_dispatch_ready()` 对 ready 物理实例构造快照并调用策略；逻辑 `kit_join`
  不参与派工，仍由 `_check_join` 提交账本。
- 选中后先 `claim_instance()`（仍要求 PENDING 且未派发、前驱已提交），再
  `select_device()`（必须属于 recipe 候选；同实例启动后不得改派），然后
  投影 `Stage4Task` 入队。
- 无候选时不认领、不改状态，实例留在 ready 集合下一 tick 重试；等待原因
  只在变化时记录一次，避免逐 tick 刷事件。
- `_poll_unit()` 只启动 `selected_device_id == arm_X` 的任务；资源仍以
  `template.resources` 完整集合调用 `acquire_all()`，失败时只记录等待，
  不保留部分锁，不标 FAILED。`_start_unit_task()` 使用选定设备而非
  `candidate_devices[0]`，并校验设备与单元一致。
- AGV 载货/空载航线沿用 `_start_transport()`/`_try_start_reposition()` 的
  资源集与 `AgvExecutor`；载货成功仍走同一 `_commit_operation()` 边界。
- `_freeze()`、超时、AGV 受阻/恢复、停带注入等路径统一经
  `_fail_task()`/`_block_task()` 分支到 `OperationRuntime`，避免队列与
  运行图双写。
- 证据：每条选定决策发 `SCHEDULE_DECISION`（含 run/instance/template、
  ready 时间、候选全集、逐项理由/条件、资源 owner 条件、选定设备、策略
  版本、仿真时间）；`acquire_all()` 后回填 `resource_result`（`ACQUIRED`/
  `BUSY`）、`acquired_resources`、冲突资源与 owner；execution ID 在启动后
  回填。`SCHEDULE_WAIT` 记录无候选原因；`summary["scheduler"]` 与
  `acceptance.scheduler_ok` 汇总核对。

## 5. 测试

| 测试 | 用例数 | 覆盖 |
|---|---|---|
| `tests/test_v1_scheduling.py`（新增） | 9 | 能力/allowlist/绑定/物料/区域/未验收几何拒绝；空闲替代优先；全忙仍派工；输入顺序不变；JSON 序列化与 `not_modeled` 标注；默认目录逐工序可解释 |
| `tests/test_v1_coordinator_scheduler.py`（新增） | 4 | 决策→设备启动与 execution ID 回填；物料未就绪不认领；资源冲突无部分持锁不标 FAILED；全批次 dry-run 收敛、11 条物理决策、FINAL [1,3]、资源清零 |
| `tests/test_v1_operation_runtime.py`（扩展） | +2 | `claim_instance` 单次认领/前驱校验；`select_device` 候选与启动后改派拒绝；阶段/资源窄写入 |
| `tests/test_v1_domain_tasks_resources.py`（扩展） | +3 | 13 个 legacy 字段直接赋值拒绝；TaskQueue 拒绝 runtime-backed；runtime 迁移唯一实例与等待/阻塞/恢复证据 |
| `tests/test_v1_coordinator_process.py`（更新） | — | 调度后仍只提交入口工序；重复派发幂等；逻辑合流不入队 |
| `tests/test_v1_import_boundary.py`（扩展） | +1 | `domain.scheduling` 不导入 simulation/application |
| `tests/test_v1_stage4_scheduler_sim.py`（新增） | 1 | `full_flow` 实机：验收、决策、最终配对、资源释放；未启用时 skip |

纯逻辑全量（无仿真环境）：

```text
python -m pytest -q -p no:cacheprovider
450 passed, 3 skipped
```

阶段 3 报告记录 426 passed / 2 skipped；其后目录负例参数化为 431 passed /
2 skipped。本阶段新增 19 个纯逻辑用例与 1 个实机用例（未启用时 skip），
合计 450 passed / 3 skipped。3 个 skip 是三个需要
`FLEXIBLE_FACTORY_SIM=1` 的 focused/实机用例。

## 6. `full_flow` 实机验收

环境：CoppeliaSim 4.10，ZeroMQ Remote API `localhost:23000`，当前 v1 工件
`v1/L5_UFLOW/v001_20261103_004317`；`config_digest=e292c37f…b6c7c`、
`layout_digest=ed7759fd…fddef` 与工件 manifest 一致，未执行
`build`/`migrate`。

仿真前检查：纯测试 450 passed / 3 skipped；`config_digest` / `layout_digest`
与当前工件 manifest 一致；`validate_bindings(default_catalog())` 返回
`ok=True`（6 项覆盖检查、无错误）。未执行 `build`/`migrate`，未修改受保护
工件。

```text
FLEXIBLE_FACTORY_SIM=1 python -m pytest -q tests/test_v1_stage4_scheduler_sim.py -s
```

### 6.1 结果：通过

本机单次运行 `wall_s=1514.6`（约 25 min；pytest 统计 27:29，含场景/
组件装配与销毁），`finished=true`，`frozen=false`，`acceptance.ok=true`。

| 判据 | 阶段 3 基线 | 阶段 4 |
|---|---|---|
| `ok` / `all_ok` | true | **true** |
| 仿真时间 / 步数 | 337.85 s / 6740 | 336.35 s / 6727 |
| `tasks_done` / 任务数 | true / 13 | true / 13 |
| 工序实例 / `graph_complete` | 12 / true | 12 / true |
| `final_count` / `final_parents` | 1 / `[[1, 3]]` | 1 / `[[1, 3]]` |
| FINAL UID / 类型 / 区域 | 4 / `FINAL` / `output_e` | 4 / `FINAL` / `output_e` |
| `unpaired_semi_ce` | 1（UID 2 在 `buffer_e_c`） | 1（UID 2 在 `buffer_e_c`） |
| `loaded_routes` / `route_ok` | 2 / true | 2 / true |
| `priority_ok` | true | true |
| `scheduler_ok` / 决策数 | 无该字段 | **true / 11** |
| 事件数 | 92 | 103（+11 `SCHEDULE_DECISION`、+1 `SCHEDULE_WAIT`、−1 `TASK_WAIT`：E2 改由调度器暂缓入队） |
| `resources_released` | true | true |

调度决策核对（`scheduler_detail.checked_instances=11`、`errors=[]`）：

- 11 条物理工序全部选定 recipe 候选设备（A→`arm_A`、B→`arm_B`、
  C→`arm_C`、D→`arm_D`、E→`arm_E`、运输→`agv_1`），
  `resource_result=ACQUIRED`，execution ID 分别为
  `arm_A#1`/`arm_B#1`/`arm_D#1`/`arm_C#1`/`agv_1#2`/`arm_E#1`/
  `arm_A#2`/`arm_C#2`/`agv_1#4`/`arm_E#2`/`arm_E#3`。
- 1 条 `SCHEDULE_WAIT`：`e_conveyor_uid2` 在 t=190.55 ready 时目标
  `buffer_e_c` 被 UID 1 占用，候选拒绝理由为
  `DEST_ZONE_OCCUPIED: buffer_e_c`；它保持 ready 而未提前入队，t=304.95
  合流释放缓冲后才被选择并启动。该行为解释了阶段 3 中该任务的长时间
  `TASK_WAIT`，但不改变任务数量、最终配对或路线结果。
- 每条 decision 均带 `not_modeled=[device_health, tool_payload,
  pose_envelope]`，未把未建模维度当作通过。

路线指标（判据：到站 ≤0.02 m、航向 ≤0.08 rad、滑移 ≤0.02 m）：

| 航线 | 阶段 3 stop/heading/slip | 阶段 4 stop/heading/slip |
|---|---|---|
| `A_to_B_empty` | 0.01979 / 0.00017 / — | 0.019694 / −0.00050 / — |
| `B_to_A_loaded` | 0.01956 / 0.00036 / 0.00201 | 0.019718 / 0.000167 / 0.005469 |
| `A_to_D_empty` | 0.01963 / −0.00011 / — | 0.019600 / −0.000408 / — |
| `D_to_E_loaded` | 0.01973 / −0.00012 / 0.00795 | 0.019673 / −0.00020 / 0.008789 |

全部在阈值内，但到站误差仍为 0.0196–0.0197 m，距 0.02 m 阈值不足
0.4 mm；不把本次通过表述为余量充足（见 WP6）。

任务时长与阶段 3 对照：13 个任务中 11 个物理任务的 processing 时长差异
≤0.4 s；`reposition` 两条航线时长完全一致（12.25 s / 61.40 s）。
绝对开始时间整体早约 0.85 s，来源是测试 harness 从 t=0 开始步进，而
阶段 3 报告的时间线从场景启动后的首个可派工时刻计；这不构成行为差异。

指标重算：`metrics.throughput.finals=1` 与 `final_count` 一致，
`metrics.tasks.total=13` 与任务数一致，`metrics.queues.active_at_end=0`、
`peak_active=2`、`wip.total=1`（`SEMI_CE` UID 2 在 `buffer_e_c`），
与 `acceptance/materials` 对照一致。

证据：[`v1_stage4_full_flow_20261104_210605.json`](../../evidence/production-flow/v1_stage4_full_flow_20261104_210605.json)
（237 KB）。`runs/` 中同名文件是同一裁剪版本的运行输出，未另外保存逐 tick
全量报告。

### 6.2 证据裁剪与运动层观察

证据裁剪：用例在写盘前移除 `controller_history` 的逐 tick
`motion`/`plan`/`events` 与 `task.evidence`，保留 acceptance、scheduler、
工序实例、任务/航线/事件与指标。

运动层观察：本次运行中 A/C/D 的水平直达转运变体分别在 `pre_place`
第 48/51/52 点报 `IK 无解` 或 `GOAL_CONFIG_INVALID`（C2 的
`lateral=0.0057 m`），既有 planner 自动改选 `horizontal_detour_+0.30`
变体后完成；这是规划器的候选变体策略，不是本阶段调度或任务状态失败。
该记录不改变 WP6 的结论：停靠/接触余量仍需独立 focused 复核（本轮 A→D
空载 reposition `stop_error=0.0196 m`，仍贴近 0.02 m 阈值）。

### 6.3 验收过程与重跑说明

最终验收是在代码冻结后用干净场景、独立 pytest 进程完成的。之前的三次
准备性运行均**未作为验收证据**，也没有与最终结果拼接：

1. **首次启动失败**：`tools/conpty_relay.py` 用 `shutil.which` 解析
   `coppeliaSim.exe`，该程序不在 PATH；改为传入完整路径后启动成功。
2. **第一次预运行主动终止**：测试的 `max_wall_s=900` 小于本机约 25–27 min
   的实际用时，按当时进度会在 15 min 被截断；将预算提高到 3600 s 后重跑。
3. **第二次预运行主动终止**：运行中补充了 `resource_result`/
   `acquired_resources`/冲突 owner 的审计回填（方案 WP3 要求），已加载的
   Python 进程不会热更新，只能停止并用新进程重跑。
4. **第三次预运行主动终止**：补充 `not_modeled` 维度标注（方案 WP2 要求
   未建模维度显式记录），同理停止并重跑。

每次停止测试后重启 CoppeliaSim，是为了避免被杀进程留下运行/步进中的仿真
状态：`LifecycleManager.load("current")` 要求在停止态加载，重启也同时清掉
影子臂/碰撞集合等会话资源，保证最终证据来自全新场景。该过程反映的是验收
准备与代码修订节奏，不是阶段 4 调度逻辑的不稳定；前三次准备运行没有写入
`docs/implementation/evidence/`。

## 7. 版本处置

| 版本项 | 结果 |
|---|---|
| `PRODUCT_VERSION` / `pyproject` | 1.0.0 未变（入口、运行模式与报告路径未变） |
| `SCHEMA_VERSION`、`SCENE_CONTRACT_VERSION`、`SCENE_BUILDER_VERSION` | 未变（未修改场景对象/别名/契约字段） |
| `TASK_FLOW_VERSION`、`MATERIAL_MODEL_VERSION`、`RESOURCE_POLICY_VERSION`、`METRICS_VERSION` | 未变（任务类型、物料契约、资源顺序与指标定义未改） |
| `PROCESS_MODEL_VERSION` | 未变（未增删 recipe/工序/路线/设备/能力） |
| `SCHEDULER_POLICY_VERSION` | 新增 `v1_scheduler_20261104`（`spec.py`；不进入 `simulation/config.py` 冻结摘要） |
| `simulation/config*.py` 常量 | 未新增/未修改，`config_digest` 与 `layout_digest` 不变，受保护工件无需重建 |
| `CONTROLLER_ID` / `COORDINATOR_ID` / `RUNNER_ID` | 未变 |

## 8. 边界与未完成项

- **未建模维度**：设备健康/禁用状态、工具载荷、姿态包络没有权威目录
  数据源，本阶段不参与筛选；每条 decision 以 `not_modeled` 显式列出，
  不静默当作通过。动态几何只接受基线设备或 `verified_pairs` 中已验收的
  组合。
- **WP5 未实施（条件工作包）**：方案 §3 WP5 明确要求先有业务确认的
  同规格等价工序，并对第二候选做实际 target 的 IK/碰撞/路径/负载/完成验证，
  才允许加入 recipe allowlist。本阶段这两个前提都不具备：每个工序仍只有
  一个候选设备，也没有 focused 几何证据。纯策略与接线已支持
  `verified_pairs` 门控，但未向任何 recipe allowlist 增加设备。多候选共享
  的设备专属资源（如 `arm_X`）尚未从模板资源中解耦；启用 WP5 前必须单独
  设计，不能仅靠“能力相同”直接替换。因此保持固定设备是符合验收口径的
  选择，不是遗漏。
- **WP6 未执行**：阶段 2–3 记录的 AGV 停靠误差约 0.0195–0.0199 m（阈值
  0.02 m）与 pre-place 横向超限链未复核；本阶段未放宽任何停靠、接触或
  安全阈值，也未通过调整配方/配对规则改变汇总结果。§6 的 `full_flow`
  航线指标是常规验收记录，不替代 WP6 的 focused 复核。
- 不包含：运行中抢占、故障重派、自动重试、路线自动变更、全局最优、
  订单/MES 实体、用户可编辑 recipe。
- `reposition` 等非配方任务仍由 `TaskQueue` 管理；它们不属于运行图，
  不产生调度决策。
- 安全锁存/停带/航线失败仍冻结现场，不自动恢复。

## 9. 后续

1. 若业务确认等价工序，按 WP5 先做 focused 几何验证，再增加一个
   `(device, operation)` 已验证组合与对应资源模型。
2. 按 WP6 复核 D→E 停靠/E 放置链；只记录证据，不静默调松阈值。
3. 阶段 5（调度优化/有限恢复）仍需单独规划；本阶段不声称具备这些能力。
