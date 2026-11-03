---
title: v1 混线阶段 2 实施报告 — 设备目录与执行适配器
type: report
status: review
created: 2026-11-03
updated: 2026-11-03
scope: v1 UFLOW / 阶段 2
---

# v1 混线阶段 2 实施报告：设备目录与执行适配器

本报告记录
[V1_STAGE2_EXECUTOR_PLAN.md](V1_STAGE2_EXECUTOR_PLAN.md)
在 v1 代码（`src/flexible_factory/`）上的实施结果。阶段目标是**包装**现有
机械臂与 AGV 执行实现、建立稳定 ID 到场景/运行时对象的静态绑定，
不改变 `full_flow` 行为、场景工件、运动算法和资源互锁。

结论：静态绑定核对、57 个纯测试与两项 focused 仿真（代表臂 `arm_A`、
代表 AGV 空载 + 载货）**均已执行并通过**。`full_flow` 端到端仍是阶段 3
验收项，本阶段未运行。

## 1. 代码落点

| 模块 | 内容 |
|---|---|
| `simulation/device_binding.py`（新增） | `DeviceBinding` / `SupportBinding`、`device_bindings()`、`support_bindings()`、`binding_for()`、`validate_bindings(catalog)`（纯）、`verify_scene_bindings(sim, scene)`（需实机） |
| `simulation/execution/base.py`（新增） | `ExecutionState`、`ExecutionRequest`、`ExecutionReport`、`Executor` 协议、`ExecutorError`（`code="V1_EXECUTION"`） |
| `simulation/execution/arm.py`（新增） | `ArmExecutor`（构造、pre/post tick、poll、证据、释放）、`sink_spec_for()` |
| `simulation/execution/agv.py`（新增） | `AgvExecutor`（route intent 构造、轮速步进、DONE/FAILED/BLOCKED 映射、resume） |
| `application/coordinator.py`（修改） | `ControllerRecord` 持适配器与执行 ID；`_start_unit_task`/`_launch_route` 组装 `ExecutionRequest`；`_after_step`/`_after_route` 读 `poll()`；`_sink_spec` 委托 `sink_spec_for()` |
| `simulation/contract.py` | **未改**：绑定表不写入 `extra.v1` |

协调器保留：资源申请/释放、任务队列状态、应用层验证
（`_verify_unit_task` / `_verify_route`）、提交与事件、冻结与超时。
适配器只做装配与状态映射，不写队列、物料谱系或资源表。

E 逻辑配对继续由 `_check_join()` 与 `MaterialRegistry.pair_candidates()`
负责，本阶段未新增逻辑执行器（与方案修订一致）。

## 2. 静态绑定核对结果

`validate_bindings(default_catalog())` 通过，覆盖：

- 设备 7 台：`arm_A`…`arm_E`、`agv_1`、`logic_E`；每个
  `DeviceSpec` 恰好一个绑定且 `kind` 一致。
- 物理承载 8 项：4 条物流线、A→C 交接台、E 双缓冲、成品输出台。
- `RESOURCE_ORDER` 21 项与 `ZONE_CAPACITY` 9 项**全部被认领**，无缺项、
  无重复别名、无未登记别名。
- 每台臂的 `default_line_id` 与默认工艺中该设备实际使用的传送带区域一致；
  工序资源/区域/停靠位全部落在对应绑定的资源与区域集合内。

别名处理要点：`UNIT_ALIASES[*]["output_target"]` 与交接台/线入口/缓冲
是同一对象，`HANDOFF_AC["table_alias"]` 与 `arm_A` 的 station 是同一对象，
因此臂绑定只登记 8 个角色（robot/suction/tcp/suction_sensor/model_sensor/
ik_target/island/station），放置目标点由 support 绑定覆盖 —— 否则会把
"合法共享"误报成别名冲突。

## 3. 测试结果

无仿真环境：`python -m pytest -q` → **397 passed, 2 skipped**
（基线 340，新增 57 个纯测试；2 skipped 为 focused 用例）。

两项 focused 用例以**独立进程**分别执行通过：
`tests/test_v1_stage2_arm_sim.py` 6 min 53 s、`tests/test_v1_stage2_agv_sim.py`
3 min 32 s。合并成一轮整轮复跑未取得结果 —— 第二次启动时仿真器会话已结束
（`coppeliaSim.exe` 进程不存在、23000 无监听），`connect` 挂起后进程被终止；
因此本报告**不声明**"单次 399 passed"，只声明上述两次独立运行的实际结果。

| 测试 | 用例数 | 覆盖 |
|---|---|---|
| `tests/test_v1_device_binding.py` | 15 | 目录覆盖、五臂齐全、资源/区域闭合、别名唯一、工序闭合、缺设备与别名缺失/冲突的负向用例 |
| `tests/test_v1_execution_arm.py` | 12 | `start` 立即返回、pre/post 转发、`RUNNING→SUCCEEDED`、控制器失败、安全锁存优先、重复启动/过期 poll/设备不匹配拒绝、放置汇未知区域 |
| `tests/test_v1_execution_agv.py` | 12 | route intent 构造、逐 tick 推进、`DONE` 证据、`FAILED` 原因、`BLOCKED` 子状态、`resume` 透传、空载/载货同入口 |
| `tests/test_v1_execution_conveyor.py` | 5 | 无 conveyor executor、无 `conveyor_transport` capability、线资源仍由工序持有、放置汇只含几何 |
| `tests/test_v1_execution_boundary.py` | 5 | adapter 无 `sim.step()`、无 `application`/`cli` 导入、无句柄字段 |
| `tests/test_v1_coordinator_executors.py` | 8 | 五臂各一适配器、`_start_unit_task` 委托一次、每 tick 轮询一次、失败与安全锁存落账文案、AGV 委托与空闲等待 |
| `tests/test_v1_stage2_arm_sim.py` | 1 | focused：`arm_A` 真实控制器取放 |
| `tests/test_v1_stage2_agv_sim.py` | 1 | focused：AGV 空载调机 + 载货运输 |

既有测试全绿，包括 `test_v1_coordinator_process.py`、`test_v1_process_model.py`、
`test_v1_contract_validation.py`、`test_v1_import_boundary.py`。

## 4. 行为保持

- 失败/冻结文案未变：`CONTROLLER_BUILD_FAILED`、`ROUTE_BUILD_FAILED`、
  `SAFETY_LATCH: …`、`AGV_ROUTE_FAILED: …`、`AGV_BLOCKED_TIMEOUT`。
- AGV 阻塞/清障/续行分支保留，仅把 `route.state` 判断换成
  `report.detail["route_state"]` 与 `executor.resume()`；`BLOCKED` 仍是
  `RUNNING` 的子状态，没有新增第四状态。
- `_sink_spec` / `_expected_material_pose` / `_operation` / `_owner` 保留
  同名方法（既有测试直接调用），`_sink_spec` 实现委托 `sink_spec_for`。
- `on_controller` 回调与 `controllers[*].controller` 仍暴露底层
  `Stage4UnitController`，`run.py`、快照与报告字段不变。

## 5. focused 仿真结果（已执行）

环境：CoppeliaSim 4.10.0.0，ZeroMQ Remote API 23000，当前 v1 工件
`v1/L5_UFLOW/v001_20261103_004317`。执行方式：

每个 focused 文件单独一次进程（已验证可行的形式；两次运行之间仿真器保持运行）：

```text
FLEXIBLE_FACTORY_SIM=1 python -m pytest -q tests/test_v1_stage2_arm_sim.py -s
FLEXIBLE_FACTORY_SIM=1 python -m pytest -q tests/test_v1_stage2_agv_sim.py -s
```

每个用例会 `load` 当前工件并 `start_stepping`，结束时
`stopSimulation` + 销毁 `Shadow_*` 会话资源；仿真器未启动时
harness 在 3 s 内报错，不会无限等待。

| 用例 | 结果 |
|---|---|
| `arm_A` 执行 `housing_direct_A`（S1→A→`handoff_ac`） | 任务 `DONE`，964 步 / 48.20 s 仿真 / 221.9 s 墙钟，执行 `arm_A#1`，物料 `SEMI_AC` 落在 `handoff_ac`（`READY`） |
| AGV 空载 `A→B`（reposition） | 任务 `DONE`，246 步 / 12.30 s 仿真，`stop_error=0.019846 m`，`heading_error=-0.001050 rad` |
| AGV 载货 `B→A`（`transport_BA`） | 任务 `DONE`，242 步 / 12.05 s 仿真，`stop_error=0.019460 m`，`heading_error=0.000258 rad`，`material_slip=0.004085 m` |

对照判据：`AGV_ROUTE_ARRIVE_TOL=0.02 m`、`AGV_ROUTE_HEADING_TOL=0.08 rad`、
`AGV_ROUTE_SLIP_TOL=0.02 m`；两次航线 `state=DONE`、`blocked={}`、
`resumed=[]`，结束后 `agv_at_dock=A`。

证据：臂证据为 harness 输出摘要（去掉逐 tick 的
`summary.motion/plan/events` 与 `task.evidence`，其余字段原样，
完整输出在 `runs/` 不入库）；AGV 证据为完整输出（<20 KB）。

- `docs/implementation/evidence/v1_stage2_arm_20261103_190310.json`
  （507 行 / 13 KB；原始 54 k 行 / 1.9 MB 保留在 `runs/`）
- `docs/implementation/evidence/v1_stage2_agv_20261103_190713.json`

解读注意事项：

0. 上述证据由 harness 在加入"连接前 TCP 探测"之前产生；该探测与证据
   裁剪都只影响连接失败提示和存储体积，不改变工件加载、组件构建与
   步进路径，判据字段全部保留。
1. 臂证据里的 `acceptance.ok=false` 是**场景级**判定（FINAL 数、父子 UID），
   focused 用例抑制了来源批次与 E 配对，该字段不适用于单工序用例，不代表
   执行失败；判据以 `task.status=DONE`、物料区域/类型与 `controller_history`
   为准。
2. 空载与载货的 `stop_error` 分别为 0.0198 / 0.0195 m，距 0.02 m 容差仅
   0.2 mm 余量。这是既有停靠判据的真实结果，未在阶段 2 放宽或收紧；若后续
   需更大余量，应作为独立课题调整停靠参数，而不是在适配层补偿。

## 6. 版本处置

| 版本项 | 结果 |
|---|---|
| `PRODUCT_VERSION` / `pyproject` | 1.0.0 未变（无对外入口/行为变化） |
| `SCHEMA_VERSION`、`SCENE_CONTRACT_VERSION` | 未变（未改场景对象/别名/契约字段） |
| `CONTROLLER_ID` / `COORDINATOR_ID` / `RUNNER_ID` | 未变（适配器只包装） |
| `PROCESS_MODEL_VERSION` | 未变（未增删工序/设备/能力/路线） |
| `simulation/config*.py` 常量 | 未新增（避免 `config_digest` 变化导致受保护工件校验失败） |
| 新增内部版本常量 | 无（`device_binding.py` / `execution/` 不写版本字符串） |

受保护工件一致性核对（不需仿真，直接比对 manifest）：

```text
current.json → v1/L5_UFLOW/v001_20261103_004317
config_digest  保存 e292c37f…b6c7c  当前 e292c37f…b6c7c  一致
layout_digest  保存 ed7759fd…fddef  当前 ed7759fd…fddef  一致
```

两次 focused 运行也都在同一工件上完成加载与启动校验，未触发 `build`、
`migrate` 或任何场景写入。

## 7. 边界与后续

本阶段**不宣称**：

- `arm_B`…`arm_E` 的动作几何重新验收（仅通过静态绑定与配置核对）；
- 全部 dock 组合、全部航线；只跑了 `A↔B` 一条代表路线；
- `full_flow` 端到端正确性、B/D 多路线交错与 E 配对（阶段 3 验收项）；
- 阻塞清障续行在本轮重测（既有 `agv_blocked` 回归继续负责）。

后续：阶段 3 接入配方 DAG 后由 `full_flow` 覆盖端到端；设备派工选择策略
届时单独设计，适配器仍只负责所选设备的完整执行生命周期。
