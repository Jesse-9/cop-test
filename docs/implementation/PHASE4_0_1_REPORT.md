# 阶段四 0-1 实施报告：基线与静态工艺目录

日期：2026-11-02
方案：[06-19-混线生产阶段0-1基线与静态工艺目录](../wiki/06-工业案例/柔性车间/当前规划/06-19-混线生产阶段0-1基线与静态工艺目录.md)
上游规划：[06-18-柔性车间混线生产工序与设备调度演进规划](../wiki/06-工业案例/柔性车间/当前规划/06-18-柔性车间混线生产工序与设备调度演进规划.md)
术语与对象模型：[06-24-混线生产术语与对象模型](../wiki/06-工业案例/柔性车间/当前规划/06-24-混线生产术语与对象模型.md)
工艺流程与设备映射：[06-25-混线生产工艺流程与设备映射](../wiki/06-工业案例/柔性车间/当前规划/06-25-混线生产工艺流程与设备映射.md)
工件：`stage4/L5_UFLOW/v004_20261029_133249`（父 `stage3/L2_MIRROR/v008_20261029_112305`，protected）

> 本报告记录阶段 0-1 的行为基线冻结、静态工艺目录和渐进接入。阶段仍
> 只运行 `full_flow` 端到端仿真；`no_pair`、`agv_priority`、
> `agv_blocked`、`conveyor_fault`、`staggered_batch` 不在本轮重跑范围。
> 未实现 YAML/JSON 用户配方、设备执行适配器、工序 DAG 实例化和动态
> 设备选型，这些属于阶段 2 及以后。

## 1. 结论

- 新增 `src/stage4/process_model.py` 静态工艺模型与目录校验，新增
  `src/stage4/default_process.py` 保存当前唯一默认 `RecipeSpec`，新增
  `src/stage4/operation_runtime.py` 作为工序实例的单一运行时状态。
- `src/stage4/coordinator.py` 删除 `UNIT_TASKS`、`TASK_RESOURCES`、
  `TASK_TYPES`、`TASK_SOURCE/SINK/DOCK`、`UNIT_LINE`、`FOLLOWUP`、
  `SOURCE_*` 等固定映射，改为从默认目录读取任务字段；执行仍调用原
  `Stage4UnitController`、`AGVRouteController4`、物料注册表和资源表。
- `config.py` 的旧 task kind、流程摘要、优先级、工序转换和 join 常量
  保留为**冻结兼容值**：它们参与场景工件 config digest 和旧报告
  schema。启动时 `validate_default_against_config()` 核对目录与冻结值，
  两者不一致即拒绝运行。
- 场景工件、config digest、layout digest 均未修改：
  `45e46fa0…a6c` / `ed7759fd…def`，与 `v004` 工件契约一致。
- 纯测试通过，`full_flow` 第四次运行通过并与阶段 0 基线归一化对照
  `0` 差异（13/13 DONE、1 个 FINAL 父件 1+3、1 个未配对 SEMI_CE、
  两条载货路线、物料终态/位置与 per-UID 事件序列一致）。首次运行
  暴露并修复了 `kit_join` 派工集成缺陷；第二、三次运行触发了既有
  吸盘释放余量导致的 A 臂失败，第四次改选负肩部解并通过；详见 §7。

## 2. 阶段 0：行为基线冻结

基线只取 `full_flow`：批次 `RAW_A=1, RAW_B=1, RAW_D=1`。正式验收摘要
为 `phase4_41_analysis_full_flow_20261029_173632.json`：

| 项 | 基线值 |
|---|---|
| 仿真时间 | 343.90 s |
| 任务 | 13/13 DONE |
| FINAL | 1 个，父 UID `1 + 3` |
| 未配对 SEMI_CE | 1 个 |
| 载货路线 | `B_to_A_loaded`、`D_to_E_loaded` 各 1 次，`route_ok=true` |
| 安全锁存 | 无；资源全部释放 |
| 停靠/滑移 | 停靠 ≤0.0199 m，滑移 ≤0.0090 m |

为保留可逐事件对照的归一化签名，本轮从同工件、同结果的原始运行
`phase4_run_full_flow_20261030_093231.json`（337.55 s、13/13 DONE、
FINAL 父件 1+3）提取基线文件：

- `evidence/phase0_1_baseline_full_flow_20261030_093231.json`。

签名只保留输入批次、验收字段、任务 `(kind, uid, status)`、物料终态
（类型/状态/区域）、可读物料位置（run-to-run 0.06 m 容差）、实际
执行路线和每个 UID 的关键事件序列；不保留仿真时间、句柄、
停靠误差等允许波动的运行指标。

代表性 UID 轨迹（与阶段 0 报告一致）：

1. **直达壳体 UID 1**：`line_s1a RAW_A → A → handoff_ac SEMI_AC →
   C → line_ce SEMI_CE → E → buffer_e_c`。
2. **B→AGV→A 绕行壳体 UID 2**：`line_s1b RAW_B → B → agv SEMI_AB →
   dock_B → dock_A → A → handoff_ac SEMI_AC → C → line_ce SEMI_CE →
   E → buffer_e_c`。
3. **D→AGV→E 支架 UID 3**：`line_s2d RAW_D → D → agv PART_DE →
   dock_D → dock_E → E → buffer_e_d`。
4. **E 两输入合流**：`buffer_e_c SEMI_CE (uid1) + buffer_e_d PART_DE
   (uid3) → FINAL uid4 @output_e`，父 UID 记录为 `1+3`。

业务语义仅为仿真抽象：壳体/支架的"加工"是抓取、转运和物料类型转换；
E 岛是逻辑配对，不是真实物理装配。

## 3. 阶段 1A：静态工艺模型

### 3.1 数据模型

`process_model.py` 提供不可变数据类：

| 类型 | 职责 |
|---|---|
| `MaterialPort` | 稳定区域 ID + `ItemType` 输入/输出端口 |
| `WorkStepTemplateRef` | 只引用已存在执行路径：`conveyor_pickup`、`arm_pick_place`、`agv_route`、`logical_join`；不携带任意 Python |
| `OperationTemplate` | id、旧 task kind 兼容标识、工序语义、执行器类型、能力、候选设备、输入/输出、资源请求、工步模板、完成验证器、优先级、前置/后继 |
| `RouteVariant` | 命名路线的有序工序选择、进入/离开区域；同组路线互斥 |
| `OrderEntry` | 来源线、来料类型与初始工序 |
| `RecipeSpec` | recipe id/版本/产品类型/工序模板集合/路线变体/订单入口/合流工序 |
| `CapabilitySpec`、`DeviceSpec`、`ZoneSpec` | 能力、设备与区域唯一索引；`logic_E` 标记为非物理设备 |
| `ProcessCatalog` | 唯一索引与完整性校验入口 |

默认配方 `mixed_line_v1`（版本 `stage4_process_20261102`，产品
`assembled_module`）包含 10 个工序模板：

| 工序 | task kind | 输入 → 输出 | 设备/资源 |
|---|---|---|---|
| `housing_direct_A` | `a_conveyor` | `line_s1a:RAW_A → handoff_ac:SEMI_AC` | `arm_A` |
| `housing_alt_B` | `b_conveyor` | `line_s1b:RAW_B → agv:SEMI_AB` | `arm_B` |
| `transport_BA` | `transport_ba` | `agv:SEMI_AB → agv:SEMI_AB`（dock B→A） | `agv_1` |
| `housing_alt_A` | `a_agv` | `agv:SEMI_AB → handoff_ac:SEMI_AC` | `arm_A` |
| `housing_finish` | `c_handoff` | `handoff_ac:SEMI_AC → line_ce:SEMI_CE` | `arm_C` |
| `housing_to_E_buffer` | `e_conveyor` | `line_ce:SEMI_CE → buffer_e_c:SEMI_CE` | `arm_E` |
| `bracket_process` | `d_conveyor` | `line_s2d:RAW_D → agv:PART_DE` | `arm_D` |
| `transport_DE` | `transport_de` | `agv:PART_DE → agv:PART_DE`（dock D→E） | `agv_1` |
| `bracket_to_E` | `e_agv` | `agv:PART_DE → buffer_e_d:PART_DE` | `arm_E` |
| `kit_join` | `logical_join` | `buffer_e_c:SEMI_CE + buffer_e_d:PART_DE → output_e:FINAL` | `logic_E`（逻辑） |

路线变体：

```text
housing_supply/housing_direct: housing_direct_A → housing_finish → housing_to_E_buffer
housing_supply/housing_alt:    housing_alt_B → transport_BA → housing_alt_A
                               → housing_finish → housing_to_E_buffer
bracket_supply/bracket_route:  bracket_process → transport_DE → bracket_to_E
mixed_line_v1/join:            buffer_e_c:SEMI_CE + buffer_e_d:PART_DE
                               → output_e:FINAL
```

`housing_direct` 与 `housing_alt` 同组互斥，不是同时执行的两个并行分支；
两条路线的终点 `housing_to_E_buffer` 在 C 之后汇入同一条 E 缓冲输入。
`kit_join` 的 `depends_on` 同时包含两条路线终点，`predecessor_policy="all"`
表达 AND 合流。

### 3.2 目录校验

`ProcessCatalog.validate()` 在 coordinator 构造时执行，错误信息带
`recipe=<id>` / `operation=<id>` 定位。校验覆盖：

- 重复 recipe/capability/device/zone/operation id 与重复 task kind；
- 未知设备、能力、zone、资源、dock 资源；
- 缺失输入/输出端口、缺失工步模板或完成验证器；
- 无效前置/后继、依赖关系不互逆；
- 循环依赖（后继图 DFS）；
- 前置输出与后继输入物料类型/区域不连续；
- 路线 entry/exit、工序链连续性、未被路线覆盖的工序；
- 路线终点未接入合流、合流输入不足或合流带后继；
- 订单入口 line/来料类型与首工序不一致。

`sources`/`resources` 的兼容值仍由 `config.py` 提供，目录通过
`validate_default_against_config()` 核对优先级、`PROCESS_TRANSITIONS`、
`JOIN_INPUTS/JOIN_OUTPUT/JOIN_BUFFER_OF` 和 `SOURCE_ITEMS`。

## 4. 阶段 1B：渐进接入与单一运行时状态

- `Stage4Coordinator` 构造时创建/校验目录，`_poll_sources` 从
  `RecipeSpec.entries` 取来源和初始工序；`_poll_unit` 按候选设备取工序；
  `_start_unit_task` 从模板读取 source/sink/line/dock/expected_types；
  `_commit_unit_result` 按输出类型是否变化决定是否调用
  `MaterialRegistry.process`，按 sink kind 提交传送带状态和后继。
- AGV 调度从运输模板读取起点/终点 dock；B/D 装载请求和 A/E 卸载请求
  由 `source_kind/sink_kind` 派生，不再按固定 task kind 分支。
- `Stage4Task` 持有 `OperationInstance`；`status`、`phase`、时间、证据、
  资源、`upgraded` 为受控代理，`kind/uid/source/destination/dock/device`
  只读。`as_dict()` 输出旧字段和 `operation_instance_id`/
  `operation_template_id`，序列化边界不维护第二份状态。
- E join 的输入类型、缓冲区和输出类型已在 `kit_join` 模板声明；
  本轮仍调用原 `_check_join`/`MaterialRegistry.commit_join`，不改变
  事务语义。
- 空载调机 `reposition` 仍是动态任务，不进入 recipe；其 priority 继续
  读取 `config.TASK_PRIORITY`，因为它属于 AGV 调度参数而非产品工艺。

## 5. 兼容性与版本

- 场景工件与契约版本不变：`stage4_contract_20261029_v2`。
- `stage4_config_digest` 仍为
  `45e46fa09a6d4f5557df0a23d3eb3568a208156ac56b6460df5527d2ace50a6c`；
  `stage4_layout_digest` 仍为
  `ed7759fddfc55eb546fd29363cea003898236cac6a85b32f2cf9717b72afddef`。
- `config.STAGE4_TASK_FLOW_VERSION` 保持 `stage4_taskflow_20261029`，
  因为该值参与工件契约；新增运行时目录版本
  `PROCESS_MODEL_VERSION = "stage4_process_20261102"`，写入
  `summary.process_model` 和任务报告的 `process_model_version`。
- 旧报告字段 `flow`、`flow_version` 保留；新增 `process_routes`、
  `process_model_version` 和 `process_model.catalog`。既有证据读取
  不受影响。
- `build_stage4_extra()` 的 `task_flow` 新增
  `process_model_version`、`recipe_id`、`process_routes`，后续重新派生
  的工件会记录目录版本；当前 `v004` 工件没有这些字段，只读校验按
  "存在才校验" 处理，仍可通过。

## 6. 测试

新增/更新测试：

- `tests/test_stage4_process_model.py`：默认目录正向契约、三条路线与
  AND 合流、旧优先级/类型转换/join 兼容、原 coordinator 映射迁移等价、
  资源集合、任务字段只读投影、运输任务路线字段，以及重复 ID、未知
  设备/能力/zone/资源、缺失输入/输出/工步、无效后继、循环依赖、物料
  类型不匹配、路线不连续、重复设备/能力/zone ID 的负向校验。
- `tests/test_stage4_coordinator_process.py`：协调器从目录读取
  owner、source/destination zone 和 sink spec，并验证逻辑合流后继
  `kit_join` 不会创建 `Stage4Task`（不需要 CoppeliaSim）。
- `tests/test_stage4_config.py`：资源顺序测试改读默认目录，不再依赖
  coordinator 的 `TASK_RESOURCES`。

测试结果：

```text
513 passed, 3 skipped
```

（3 个 skip 为测试套件既有条件跳过的集成测试；其余全部通过。
运行阶段四仿真期间不得执行依赖真实仿真的 MCP 测试，测试套件应在
仿真停止后运行。）

## 7. 端到端 full_flow 对照

### 7.1 端到端运行暴露的问题

#### 7.1.1 逻辑合流 successor 被误当作可派工任务（已修复）

首次完整运行在 `t=123.85` 完成 `e_conveyor_uid1` 后中止：

```text
stage4.tasks.TaskError: 逻辑合流工序 'kit_join' 不创建 Stage4Task
```

原因是静态目录把 `kit_join` 声明为 `housing_to_E_buffer` 和
`bracket_to_E` 的 successor（用于表达工序 DAG 的合流依赖），而
`_commit_unit_result` 把每个 successor 都当作可派工任务调用
`make_task_from_operation`。逻辑合流没有物理设备，本来就不应进入
任务队列；E join 仍由 `_check_join` 在两条输入就绪时调用
`MaterialRegistry.commit_join`。

修正：`Stage4Coordinator._submit_followup` 遇到
`executor_kind == "logic"` 时直接返回，不创建 `Stage4Task`；新增回归
测试 `test_logic_join_successor_does_not_create_stage4_task`。失败运行
报告归档为 `evidence/phase4_0_1_full_flow_failed_20261102_182219.json`。

#### 7.1.2 既有吸盘释放修正边界（非本阶段改动）

第二、三次运行都在 `a_agv_uid2`（A 臂从 AGV 取料后放到
`handoff_ac`）释放阶段中止：

```text
第二次 t=142.45: RELEASE_CORRECTION_EXCEEDED pos=0.0113 m ang=0.0180 rad
第三次 t=142.20: RELEASE_CORRECTION_EXCEEDED pos=0.0101 m ang=0.0038 rad
```

`stage2.suction` 的释放检查阈值为 `pos=0.010 m`、`ang=0.05 rad`；两次
位置修正分别超出 1.3 mm 和 0.1 mm，物料保持 `HELD_BY_SUCTION`，任务
FAILED、整线冻结，未误报 DONE。该路径复用既有
`Stage2SuctionController`、`Stage4UnitController` 的运动与释放逻辑；
本阶段的工艺目录改造没有修改位姿目标、IK/路径、吸盘或释放阈值。两次
运行都落在 A 臂 `pre_pick` 的正肩部解（`q3≈0.319`），AGV 托盘取料点
与基线差约 1–2 mm，属于既有控制器对托盘毫米级滑移/腕部分支的余量
问题，是 06-19 范围外的物理/控制优化项。分析摘要归档为
`evidence/phase4_0_1_full_flow_retry_release_failed_analysis_20261102_184431.json`
和
`evidence/phase4_0_1_full_flow_retry2_release_failed_analysis_20261102_190305.json`。

第四次运行（系统空闲、同一工件）在相近取料点下改选了负肩部解
（`q0=-4.288`、`q3=0.365`，与基线 20261030 的 `q0=-4.289`、
`q3=0.363` 一致），释放修正落到阈值内并通过，结果见 §7.2；这也说明
该失败是既有 `pre_pick` 分支选择对托盘毫米级滑移的余量问题，而不是
工艺目录接入破坏了 A 臂执行路径。

### 7.2 通过后的基线对照

第四次运行（同一工件、同一输入批次）通过场景验收：

```powershell
.venv/Scripts/python.exe src/run_stage4.py --scenario full_flow --time 700
```

- 运行报告：`docs/implementation/runs/phase4_run_full_flow_20261102_191700.json`
  （仿真时间 337.30 s，`run_id` 见报告）；
- 对照证据：
  `evidence/phase0_1_comparison_full_flow_20261102_191700.json`；
- 归一化对照脚本：`docs/measurement/compare_stage4_process_baseline.py`；
- 本次 `a_agv_uid2` 的 `pre_pick` 选择负肩部解
  （`q0=-4.288`、`q3=0.365`），与基线 20261030 一致，释放修正未超限。

| 判据 | 阶段 0 基线 | 本次 | 结果 |
|---|---|---|---|
| 输入批次 | RAW_A/B/D = 1/1/1 | 1/1/1 | 一致 |
| 任务 | 13/13 DONE | 13/13 DONE | 一致 |
| FINAL | 1，父 UID 1+3 | 1，父 UID 1+3 | 一致 |
| 未配对 SEMI_CE | 1 | 1 | 一致 |
| 载货路线 | `B_to_A_loaded`、`D_to_E_loaded` | 同两条，`route_ok=true` | 一致 |
| 物料终态 | uid1/2 `SEMI_CE@buffer_e_c`，uid3 `PART_DE@buffer_e_d`，uid4 `FINAL@output_e` | 同 | 一致 |
| 物料位置 | 可读项 uid2/uid4 | 偏差 0.000386 m / 0.0 m（容差 0.06 m） | 一致 |
| 每 UID 关键事件序列 | 基线签名 | 0 差异 | 一致 |
| 安全/资源 | 无锁存、资源释放 | 无锁存、资源释放 | 一致 |

对照脚本输出 `ok=true`、`differences=[]`。仿真时间 337.30 s 与基线
337.55 s / 343.90 s 的差异属于运行时间波动，不参与等价判据；任务、
FINAL/父 UID、物料终态/位置、路线和 per-UID 事件序列全部一致。

因此阶段 0-1 的端到端验收项通过：默认工艺目录驱动下的 `full_flow`
与阶段 0 基线等价；执行层仍是已验收的单元/AGV/物料/资源控制器。

## 8. 不在此阶段完成

- 未引入 YAML/JSON 用户配方、配方编辑器或运行时自定义；
- 未实现设备执行适配器/`DeviceExecutor`（阶段 2）；
- 未实现工序实例 DAG、依赖推进和 ready 集合（阶段 3）；
- 未实现产品变体、能力匹配调度和自动恢复/优化（阶段 4/5）；
- 未改变场景、布局、验收批次、故障注入或安全锁存行为；
- 未重跑其它 stage4 场景；不能由本轮 `full_flow` 推断其等价性。
