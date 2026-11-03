---
title: v1 混线阶段 2 实施方案 — 设备目录与执行适配器
type: plan
status: proposed
created: 2026-11-03
updated: 2026-11-03
scope: v1 UFLOW / 阶段 2
---

# v1 混线阶段 2 实施方案：设备目录与执行适配器

本文件把
[06-20 阶段 2 规划](../wiki/06-工业案例/柔性车间/当前规划/06-20-混线生产阶段2设备目录与执行适配器.md)
落到 v1 代码（`src/flexible_factory/`）的可执行改动清单：文件级落点、接口
契约、测试矩阵、文档与版本处置。**本文件不声明任何新能力**；实施完成后由
`V1_STAGE2_REPORT.md` 给出实测结论，06-20 的 `status` 才改为 `implemented`。

范围前提（与 06-20 一致）：调度仍用默认 `mixed_line_v1` 与固定候选设备；
`full_flow` 的用户入口、运行模式、场景工件、运动算法、资源互锁均不改变。
阶段 2 不把设备能力变成动态派工输入；待阶段 3 配方 DAG 稳定后，再单独设计
“工序声明需求与候选设备、调度器结合可用性/资源/路线选择设备”的派工策略，
执行 adapter 仍只负责所选设备的完整执行生命周期。

## 1. 目标与验收映射

06-20 的每条验收要求对应到唯一交付物，避免"报告写成通过、代码没有对应用例"：

| 06-20 验收条目 | 交付物 | 证据形态 |
|---|---|---|
| 全部后续设备 ID/alias/目标/资源/区域通过静态契约核对 | `simulation/device_binding.py` + `tests/test_v1_device_binding.py` | 纯测试通过 + 报告 §2 映射表 |
| 一台代表臂（`arm_A`）完成一个正常工序 focused 验证 | `tests/test_v1_stage2_arm_sim.py` | `evidence/v1_stage2_arm_*.json` |
| 其余四臂仅做绑定/配置核对 | 同上静态核对（不跑动作） | 测试断言 + 报告声明 |
| 一条代表 AGV 路线（含载货/空载两分支）focused 验证 | `tests/test_v1_stage2_agv_sim.py` | `evidence/v1_stage2_agv_*.json` |
| conveyor 命令/观测边界；E join 沿用现有候选选择与提交边界 | `tests/test_v1_execution_conveyor.py` + 既有 join 测试 | 纯测试通过 |
| 单元测试验证 `RUNNING → SUCCEEDED` 与完成证据映射 | `tests/test_v1_execution_arm.py` | 纯测试通过 |
| harness 只有一个 stepping owner；adapter 不调 `sim.step()`、不双写 | `tests/test_v1_execution_boundary.py` + harness 断言 | 边界测试通过 |
| 本阶段不要求运行 `full_flow` | 报告中标注"未执行"，不写成通过 | 报告"未执行项" |

## 2. 现状核对（代码实证）

### 2.1 已有落点

| 现有实现 | 位置 | 阶段 2 处置 |
|---|---|---|
| 静态设备/能力/区域目录 | `domain/process_model.py`、`domain/default_process.py` | **不动**（不递增 `PROCESS_MODEL_VERSION`） |
| 场景 alias/handle 解析 | `simulation/scene.py`、`simulation/contract.py`、`simulation/device_handle_map.py` | **不动**；绑定表只引用 alias 字符串 |
| 臂控制器构造（15+ 参数内联） | `coordinator._start_unit_task()` L350–407 | 迁入 `ArmExecutor.start()` |
| 臂逐 tick 推进 | `coordinator.step()` L157–166 直调 `controller.pre_tick/post_tick` | 改为 `executor.pre_tick/post_tick` |
| 臂完成/失败判定 | `coordinator._after_step()` L678–741 | 改为读 `executor.poll()`，验证与提交留在协调器 |
| AGV 航线构造与状态机 | `coordinator._launch_route()` / `_advance_route()` / `_after_route()` | 构造与状态机迁入 `AgvExecutor`；资源申请、排队、freeze 留在协调器 |
| E 逻辑合流 | `coordinator._check_join()` L1013–1070、`MaterialRegistry.pair_candidates()` | 保留现有候选选择、校验、spawn/commit 与事件路径，不新增重复执行器 |

### 2.2 必须遵守的硬约束（实测得出，不是惯例推断）

1. **禁止在 `simulation/config*.py` 新增大写常量。**
   `contract.config_digest()` 对 `config_base`/`config_motion`/`config_dual`/
   `config` 的**全部大写名**（除 `MODEL_BASE`、`DEFAULT_LOG_FILE`）做摘要，
   而 `validate_contract()` 会逐字比对已保存的 `v1_config_digest`。新增常量
   会让受保护工件 `v1/L5_UFLOW/v001_20261103_004317` 校验失败。
   → 本阶段不向 `config*` 增加常量；只保留已有且有消费者的版本契约。
2. **不得修改 `spec.CONTROLLER_ID` / `COORDINATOR_ID` / `RUNNER_ID`。**
   manifest 只读校验逐项比对这三个字符串；`ArmExecutor` 是**包装**，
   `Stage4UnitController` 仍是被包装的实现类。
3. **不得修改 `spec.SCENE_CONTRACT_VERSION`、`SCHEMA_VERSION`、`LAYOUT_NAME`。**
   本阶段不改场景对象、别名、布局和契约字段。
4. **`sim.step()` 只允许存在于协调器步进路径。**
   现状唯一步进调用点是 `Stage4Coordinator.step()`（`unit_controller_base.step()`
   仅供单控制器路径，v1 不使用）；adapter 与 harness 均不得引入第二个。

### 2.3 设备静态映射核对表（第 1 步的目标产出）

| 目录 ID | kind | scene 绑定（起始别名） | 运行时对象 | 资源 | 区域 |
|---|---|---|---|---|---|
| `arm_A` | arm | `Robot_UR5_01`、`SuctionPad_01(_TCP/_ModelSensor)`、`IK_Target_01`、`Island_A`、`Island_A_Station` | `runtime.arm("A")`、`ik["A"]`、`suction["A"]` | `arm_A`、`zone_ab`、`zone_ac` | `line_s1a`、`handoff_ac`、`agv` |
| `arm_B` | arm | `Robot_UR5_02`、`SuctionPad_02*`、`IK_Target_02`、`Island_B(_Station)` | 同上（B） | `arm_B`、`zone_ab` | `line_s1b`、`agv` |
| `arm_C` | arm | `Robot_UR5_03`、`SuctionPad_03*`、`IK_Target_03`、`Island_C`、`Island_C_Output` | 同上（C） | `arm_C`、`zone_ac` | `handoff_ac`、`line_ce` |
| `arm_D` | arm | `Robot_UR5_04`、`SuctionPad_04*`、`IK_Target_04`、`Island_D(_Input)` | 同上（D） | `arm_D` | `line_s2d`、`agv` |
| `arm_E` | arm | `Robot_UR5_05`、`SuctionPad_05*`、`IK_Target_05`、`Island_E` | 同上（E） | `arm_E` | `line_ce`、`agv`、`buffer_e_c`、`buffer_e_d` |
| `agv_1` | agv | `Mobile_AGV_P3DX`、`AGV_LoadTray`、`AGV_TrayWall_*`、`Sensor_AGV_Dock*` | `comp["agv"]`（`PioneerController`） | `agv_lane_west/south/east`、`dock_A/B/D/E` | `agv` |
| `logic_E` | logic（`physical=False`） | 无场景对象；FINAL 生成点来自 `cfg.E_OUTPUT` | `MaterialRegistry`（配对记录） | 无（`logical_join` 策略） | `buffer_e_c` + `buffer_e_d` → `output_e` |
| `line_s1a`…`line_ce` | 线资源（非 `DeviceSpec`） | `LINE_DEFS[*]` 的 conveyor/entry/stop/sensor | `comp["conveyor"]`（`ConveyorController`） | 同名资源 | 同名取放区 |
| `handoff_ac` | 交接位（非 `DeviceSpec`） | `cfg.HANDOFF_AC` + 交接传感器 | `scene.unit("C").station` | `handoff_ac` | `handoff_ac` |
| `sensor_*`、`dock_*` | 观测设备（非 `DeviceSpec`） | `UNIT_ALIASES[*].suction_sensor/model_sensor`、`LINE_DEFS[*].sensor`、`ALIASES["dock_sensor"][unit]` | `Stage4Unit.dock_sensor` 等 | 无 | — |

覆盖原则沿用 06-20：**全部实例静态核对，代表设备动态验证**。五台臂共用
同一控制器实现，动态只跑 `arm_A`；不据此声称 B–E 的动作几何已重新验收。

### 2.4 现状差距

1. 控制器/航线的**构造参数装配**散在协调器，无法单独测试与复用。
2. 完成/失败判定与"执行证据"混在同一函数（`_after_step` 同时做状态迁移、
   验证、提交、释放、故障注入）。
3. 没有 device →（scene/runtime 对象、资源、区域）的可校验静态表，
   设备 ID 与别名的一致性只靠人工维护。

## 3. 目标结构与依赖方向

```text
src/flexible_factory/
  simulation/
    device_binding.py          新增：device_id → alias/runtime/资源/区域 静态表
    execution/                 新增包（唯一的执行适配层）
      __init__.py              执行接口导出
      base.py                  ExecutionState / ExecutionReport / 协议 / 错误
      arm.py                   ArmExecutor + sink_spec_for()
      agv.py                   AgvExecutor
  application/coordinator.py   改为经 adapter 启动/轮询；保留验证、提交、资源
```

依赖方向不变：`cli → application → domain / simulation / artifacts`；
`domain` 不导入 `simulation`；`simulation/execution/` **不导入 `application`
或 `cli`**，且不调用 `sim.step()`。`application` 依赖 `simulation.execution`
是允许方向（`coordinator` 已导入 `simulation.*`）。

## 4. 接口契约

```python
class ExecutionState(str, Enum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ExecutionReport:
    execution_id: str
    device_id: str
    operation_id: str | None       # 空载 reposition 可无工艺工序
    state: ExecutionState
    finished: bool                 # 执行端已结束（含失败）
    failure_reason: str = ""       # 仅 state == FAILED 时有意义
    detail: dict = field(default_factory=dict)    # phase/route_id/loaded/...
    evidence: dict = field(default_factory=dict)  # controller.summary()/metrics


@dataclass(frozen=True)
class ExecutionRequest:
    operation: OperationTemplate | None
    task: Stage4Task                 # 运行时任务上下文；不序列化
    device_id: str
    uid: int | None
    task_id: str
    sim_time: float
    route_spec: RouteSpec | None = None  # AGV transport/reposition 意图


class Executor(Protocol):
    kind: str                      # arm | agv
    device_id: str

    def start(self, request: ExecutionRequest) -> str: ...
    def pre_tick(self, sim_time: float) -> None: ...
    def post_tick(self, sim_time: float) -> None: ...
    def poll(self, execution_id: str) -> ExecutionReport: ...
```

`ExecutionRequest` 是应用协调器传给设备 adapter 的运行时命令，不是持久化
recipe。机械臂 adapter 从 `task`、`operation` 和设备绑定装配
`Stage4UnitController` 所需上下文；AGV adapter 使用 `route_spec` 区分载货运输
与空载 reposition。空载请求仍携带 coordinator 创建的运行时任务，避免 adapter
自行构造临时 `Stage4Task`。稳定 ID、route intent 和证据可序列化，任务对象与
CoppeliaSim 句柄只在当前运行时使用。

层级约定：工序（`OperationTemplate`）是上层调度/配方可编排的生产模块；分配到
设备后形成运行时任务（`Stage4Task` / `ExecutionRequest`）；executor 执行该任务
对应的完整控制器流程，其中可包含多个控制阶段和仿真 tick。仿真 tick 是时间
推进单位，不是工步；底层抓取、移动、吸取等动作阶段也不因此自动成为独立调度
对象。阶段 2 只封装完整设备执行生命周期，不新增通用工步编排模型。

三条语义约定，必须写进 docstring 并被测试固定：

1. **`start()` 不阻塞、不推进仿真。** 只构造底层控制器/航线并登记执行；
   任何 `while not finished: sim.step()` 都违反 06-20。
2. **`SUCCEEDED` 只表示执行端完成，不等于物料已提交。** 应用层验证
   （`_verify_unit_task` / `_verify_route`）仍可能失败，此时记账为
   `APPLY_VERIFY_FAILED`，任务状态由 `queue.fail` 决定，**不得**把验证失败
   塞进 `poll()` 的 `FAILED`。
3. **`poll()` 是只读的。** 不写任务队列、不写 `MaterialRegistry`、不申请/
   释放资源表条目、不产生事件；事件由协调器按既有文案输出。

单设备单活动执行：`arm_A`…`arm_E` 与 `agv_1` 在现行资源策略下各自最多一个
活动执行，因此 `poll(execution_id)` 对过期 ID 抛 `ExecutorError`
（`code="V1_EXECUTION"`），不做多路复用。

### 4.1 `BLOCKED` 的处理（不新增第四状态）

AGV 路线受阻是既有已验收行为（`agv_blocked` 回归：进入受阻、清障后确认
现场/资源所有权再续行）。生产代码继续保留该行为；阶段 2 不新增阻塞、清障或
恢复验收要求，沿用既有回归覆盖。接口需能承接协调器现有状态判断：

- `poll()` 仍返回 `RUNNING`，`detail["route_state"] = "BLOCKED"`；
- `AgvExecutor.blocked` 属性供协调器读取，`resume(sim_time, clearance_ok,
  reason)` 透传给 `AGVRouteController4.resume()`；
- 协调器现存的阻塞计时/清障/超时分支原样保留，仅把 `route.state` 判断换成
  `report.detail` 与 `executor.blocked`，事件文案不变。

## 5. 代码改动清单

### 5.1 新增文件

| 文件 | 主要内容 | 预估 |
|---|---|---|
| `simulation/device_binding.py` | `DeviceBinding`（frozen dataclass）；`device_bindings()`、`binding_for()`、`validate_bindings(catalog)`（纯）、`verify_scene_bindings(sim, scene)`（需实机） | ~200 行 |
| `simulation/execution/__init__.py` | 导出 arm/AGV adapter 与 `ExecutionState` | ~20 行 |
| `simulation/execution/base.py` | §4 的请求、状态、报告、协议、错误；`_jsonable_evidence()` 用 `util.jsonable()` 归一 | ~130 行 |
| `simulation/execution/arm.py` | `ArmExecutor`；`sink_spec_for(operation, unit_id, layout, lines, scene)` | ~170 行 |
| `simulation/execution/agv.py` | `AgvExecutor`：构造 `AGVRouteController4`、`pre_tick`（`route.step`）、`poll`、读取/续接既有 `blocked/resume` 状态、`summary` | ~130 行 |

`device_binding.py` 只持有 alias 字符串与资源/区域 ID，**不持有句柄**；
`verify_scene_bindings()` 才通过 `scene.handle()` 解析，供 focused harness 用。

### 5.2 修改文件

| 文件 | 改动 |
|---|---|
| `application/coordinator.py` | `__init__` 创建 `self.arm_executors`（按 `cfg.UNIT_IDS`）与 `self.agv_executor`；协调器步进路径继续拥有 `pre_tick → sim.step → post_tick → reconcile`；`_start_unit_task` / `_launch_route` 组装 `ExecutionRequest` 并委托 adapter；完成验证、提交、资源、阻塞/超时/freeze 与事件仍由协调器负责；`_check_join` 继续调用 `MaterialRegistry.pair_candidates()` |
| `application/coordinator.py` | `ControllerRecord` 改为持有 `executor`（字段名 `controller` 保留为只读属性，`on_controller` 回调仍传底层控制器，避免破坏 `run.py` 与既有测试） |
| `application/coordinator.py` | `_sink_spec()` / `_expected_material_pose()` **保留为委托方法**（既有测试直接调用），实现改为调用 `sink_spec_for()` |
| `simulation/contract.py` | **不改**：绑定表不写入 `extra.v1`，避免既有受保护工件的 manifest 语义漂移 |
| `docs/...` | 见 §8 |

### 5.3 删除的重复分支

- `_start_unit_task` 中 15+ 参数的 `Stage4UnitController(...)` 构造块；
- `_after_step` 中直接读 `controller.state`/`controller.finished`/`safety`
  的分支（改为读报告，判断顺序与文案不变）；
- `_launch_route` 中的 `AGVRouteController4(...)` 构造与 `ROUTE_BUILD_FAILED`
  处理（异常仍向上抛，由协调器维持相同事件与 freeze）；
- 不迁移 `_check_join` / `MaterialRegistry.pair_candidates()`：E join 的候选选择、
  配对校验和提交沿用现有实现，避免在 adapter 中复制选择规则。

## 6. 实施步骤

每步完成即跑对应测试并提交一次；未迁移路径保留原调用，禁止并行双写。

### 步骤 1：静态映射核对

- 实现 `device_binding.py` 纯部分 + `tests/test_v1_device_binding.py`。
- 断言：五臂全覆盖；每个 `DeviceSpec` 有 binding 且 kind 一致；binding 中
  资源 ⊆ `RESOURCE_ORDER`，区域 ⊆ `ZONE_CAPACITY`；alias 全部存在于
  `cfg.UNIT_ALIASES` / `LINE_DEFS` / `base.ALIASES`，且不含 `/`；`arm_*`
  的 `default_line_id` 与 binding 的线资源一致。
- 验证：`python -m pytest -q tests/test_v1_device_binding.py`。
- 判据：默认目录下 0 缺项、0 重复、0 未知别名。

### 步骤 2：最小验证入口（harness）

- `tests/stage2_harness.py`（模块名不以 `test_` 开头，不被收集）：
  `open_context()`（`LifecycleManager.load()` → `_ensure_scene()` →
  `build_components_v1()` → `start_stepping()`），通过测试专用 setup 注入一条
  正常任务，并抑制来源生成和 join；`drive(coordinator, ...)` 只调用
  `coordinator.step()`，由协调器唯一推进 `pre_tick → sim.step → post_tick →
  reconcile`；`close()` 负责停止仿真并释放 runtime。
- 测试不新增生产 scenario 或 CLI 参数，不调用 `sim.step()` 绕开 coordinator。
- focused 测试用 `FLEXIBLE_FACTORY_SIM=1` 门控，未设置时 `pytest.skip`
  并输出"未执行"原因（符合工程参考 §9 的"不得报告为通过"）。

### 步骤 3：机械臂 adapter（代表 `arm_A`）

- `ArmExecutor` 承载构造、`pre_tick/post_tick`、`poll`、`summary`。
- 纯测试用假控制器注入验证 `start` 立即返回、多 tick 后正常到达 `SUCCEEDED`、
  证据含 `controller`/`material` 键；focused 测试跑 `housing_direct_A`
  （S1→A→`handoff_ac`）。
- 判据：`start()` 返回耗时与本步无关（同 tick 内返回）；资源释放后
  `poll` 抛 `ExecutorError`；物料只在 `_commit_unit_result` 边界更新。

### 步骤 4：AGV adapter

- `AgvExecutor` 承载 `AGVRouteController4` 与轮速步进。
- 代表路线 `dock_B → dock_A`：分别覆盖 `loaded=True`（`transport_BA`）与
  `loaded=False`（reposition），验证到站证据（`stop_error`/`heading_error`/
  `material_slip`）与 `agv_at_dock` 更新仍由协调器记账。
- 判据：正常路线在 `DONE` 时返回 `SUCCEEDED` 且 `evidence["route"]` 完整；
  不新增 `BLOCKED/resume` 阶段验收，既有阻塞回归继续负责该行为。

### 步骤 5：辅助设备边界

- conveyor：`tests/test_v1_execution_conveyor.py` 验证 adapter **不**提供
  按 UID 输送能力（`ArmExecutor` 只在工序内启停线，`comp["conveyor"]` 仍由
  控制器调用），且线资源由资源表持有。
- E join：保留 `_check_join()` 与 `MaterialRegistry.pair_candidates()` 现有
  选择及提交职责；阶段 2 不新增逻辑执行器或复制配对规则，相关行为由既有
  测试覆盖。

### 步骤 6：协调接入与定向复验

- 协调器改为经 executor 启动/轮询；`tests/test_v1_coordinator_executors.py`
  用记录型假 executor 验证正常路径：每个任务只 start 一次、每 tick 只 poll
  一次、成功结果只在既有提交边界落账。
- 回归：`python -m pytest -q`（基线 340 passed）；阶段 2 focused 仿真只运行
  代表机械臂与代表 AGV 测试，不运行 `full_flow`。
- 判据：`tests/test_v1_execution_boundary.py` 通过（见 §7）。

## 7. 测试矩阵

| 文件 | 类型 | 覆盖 | 无 sim 时 |
|---|---|---|---|
| `test_v1_device_binding.py` | 纯 | 静态映射完备性/唯一性/闭合性 | 通过 |
| `test_v1_execution_arm.py` | 纯（假控制器） | 正常 `RUNNING→SUCCEEDED`、证据键、释放 | 通过 |
| `test_v1_execution_agv.py` | 纯（假 route） | 正常路线构造与推进、`DONE` 证据 | 通过 |
| `test_v1_execution_conveyor.py` | 纯 | 无非 UID 输送 executor、线资源归属 | 通过 |
| `test_v1_execution_boundary.py` | 纯（AST） | `simulation/execution/*` 无 `sim.step(`、无 `application` 导入、无句柄字段 | 通过 |
| `test_v1_coordinator_executors.py` | 纯（假 executor） | 协调器委托顺序、单次提交 | 通过 |
| `test_v1_stage2_arm_sim.py` | focused sim | `arm_A` 正常取放全程 | `skip`（标"未执行"） |
| `test_v1_stage2_agv_sim.py` | focused sim | `dock_B→dock_A` 载货 + 空载 | `skip`（标"未执行"） |

`test_v1_execution_boundary.py` 的两条实现要点：

1. AST 扫描 `simulation/execution/*.py`，断言不存在 `.step(` 调用属性名为
   `sim`/`self.sim` 的语句；
2. 在 `simulation.execution` 中导入后检查 `sys.modules` 不含
   `flexible_factory.application` / `flexible_factory.cli`。

## 8. 文档与版本规范

### 8.1 版本处置

| 版本项 | 定义位置 | 阶段 2 处置 | 依据 |
|---|---|---|---|
| `PRODUCT_VERSION` `1.0.0` | `spec.py`、`pyproject.toml` | **不变** | 无对外入口/运行模式/工件变化；若确需发版按 minor 升并同步 CHANGELOG |
| `SCHEMA_VERSION` `2.0` | `spec.py` | 不变 | manifest 无新增必填字段 |
| `SCENE_CONTRACT_VERSION` | `spec.py` | 不变 | 不改场景对象/别名/契约字段；改了必须新契约版本 + 新工件（本阶段禁止） |
| `CONTROLLER_ID` / `COORDINATOR_ID` / `RUNNER_ID` | `spec.py` | 不变 | manifest 只读校验逐项比对 |
| `PROCESS_MODEL_VERSION` `v1_process_20261103` | `default_process.py` | 不变 | 未增删工序/设备/能力/路线；一旦增删，递增并同步 06-19 与默认目录测试 |
| `TASK_FLOW_VERSION`、`RESOURCE_POLICY_VERSION`、`AGV_ROUTE_VERSION` | `spec.py` | 不变 | 任务流/资源策略/航线算法未改 |

本阶段不增加仅供报告记录、且没有 manifest 或 API 消费者的内部版本常量。

### 8.2 文档更新

| 文档 | 动作 | 时点 |
|---|---|---|
| 本文件 | `status: proposed → implemented`，补"实施偏差"节 | 全部步骤通过后 |
| `V1_STAGE2_REPORT.md` | 新建，`type: report`、`status: review`、`scope: v1 UFLOW / 阶段 2` | 落地 |
| `docs/implementation/README.md` | 表格新增两行（方案、报告） | 落地 |
| 06-20 规划 | 更新 `updated`、`status`；`related` 加报告；正文追加"实施记录"（写法对齐 06-19） | 落地 |
| `PROJECT_ENGINEERING_REFERENCE.md` §2 | 目录树加 `simulation/execution/`、`device_binding.py`；边界段补"execution 不导入 application、不调用 sim.step" | 落地 |
| `CLAUDE.md` "按任务读取" | 加一行指向本文件/报告（单行链接，不复制内容） | 落地 |
| `CHANGELOG.md` | 新增 `## Unreleased` 段："设备执行适配层内部重构，`full_flow` 行为不变" | 落地 |
| `docs/wiki/98-ProjectUsage/USAGE.md` | **不改**：无新命令/模式；focused sim 测试的前置条件写进报告 | — |

### 8.3 代码规范

- 模块头中文 docstring + 术语边界（沿用现有风格）；`from __future__ import
  annotations`；全量类型注解；证据类用 `dataclass(frozen=True)`。
- 错误类 `<X>Error(RuntimeError)` 带 `code`（`"V1_EXECUTION"` /
  `"V1_DEVICE_BINDING"`），消息中文且带 device/operation 定位。
- 常量仅在 `device_binding.py` / `execution/__init__.py` 内定义；行宽 ≤ 79。
- 序列化与证据只写 device/zone/alias/execution_id，**不写句柄**（对齐 06-20
  "句柄只存在启动绑定/运行时对象中"）；证据 dict 经 `util.jsonable()` 归一。
- 测试命名 `test_v1_*`，用例名描述行为（`test_arm_start_returns_without_stepping`），
  不使用 sleep/轮询等待。

## 9. 风险与保持不变项

1. **控制器构造失败路径**：异常仍从 `start()` 抛 `ExecutorError`，协调器保持
   原文案 `CONTROLLER_BUILD_FAILED` / `ROUTE_BUILD_FAILED` 与 freeze 语义。
2. **会话资源归属不变**：executor 不创建/销毁影子臂、碰撞集合、吸盘；
   仍由 `runtime` 与 runner 管理，`keep_running=False` 语义不变。
3. **既有测试兼容**：`_sink_spec`、`_operation`、`_owner` 等被
   `test_v1_coordinator_process.py` 直接调用，保留同名方法签名。
4. **focused 验证的代表性**：只证明复用实现接通，不宣称 B–E 动作几何验收。
5. **无法执行 sim 测试时**：报告必须列"未执行"，`status` 不得升为
   `implemented`。

## 10. 交付物与完成判据

- 新增 `simulation/device_binding.py`、`simulation/execution/`（`base.py`、`arm.py`、`agv.py` 三个功能模块及包导出）。
- `application/coordinator.py` 经 adapter 启动/轮询，重复分支删除。
- 6 个纯测试文件 + 2 个 focused sim 测试文件 + harness；E join 沿用现有测试。
- `python -m pytest -q` 全绿（基线 340 passed，新增用例数在报告中列出）。
- `V1_STAGE2_REPORT.md` 含：映射表核对结论、代表臂/代表 AGV 实测证据、
  未执行项、版本处置记录。
- 以上全部满足后，06-20 的 `status` 才改为 `implemented`。
