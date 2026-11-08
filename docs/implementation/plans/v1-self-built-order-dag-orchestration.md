---
title: v1 自建订单与工序 DAG 自主编排总体方案
type: plan
status: proposed
created: 2026-11-06
updated: 2026-11-07
scope: v1 UFLOW / L5_UFLOW / 自建订单
related:
  - ../reports/v1-stage-03-operation-dag.md
  - ../reports/v1-stage-04-capability-scheduling.md
  - ../../wiki/06-工业案例/柔性车间/当前规划/06-24-混线生产术语与对象模型.md
  - ../../wiki/06-工业案例/柔性车间/当前规划/06-25-混线生产工艺流程与设备映射.md
---

# v1 自建订单与工序 DAG 自主编排总体方案

## 1. 目标与结论

目标是让用户通过前端编排，或由后端规划器根据订单目标自主选择工序，构造订单专属 DAG；系统将其编译为运行实例，在 CoppeliaSim 中按依赖、物料和资源约束执行，并记录逐工序结果。可隔离的局部失败阻断其依赖后继、允许无关分支继续；安全或基础设施故障停止整个仿真并保留现场与证据。

该目标可在当前工程上实现。v1 已有静态工序模板、运行时 `OperationGraph`、ready 节点派工、设备调度、执行适配器、物料提交和失败冻结机制；缺少的是自建订单领域对象、任意合规 DAG 的编译入口、对外服务接口与前端。现有图运行时主要实例化固定路线，E 岛合流也由专用逻辑处理，因此不能只增加一个 DAG 编辑器就认为端到端能力已具备。

首期限定在现有 `L5_UFLOW` 场景、已登记工序模板、已绑定设备/资源/区域和已有完成校验器内。允许用户或规划器组合这些能力；新增物理布局、未经验证的设备替代、任意脚本和循环返工不属于首期范围。

## 2. 业务对象与使用流程

**订单**是一次待执行的生产意图，包含订单 ID、产品/目标产物、投料或批次数量、所选工序图、优先级、目标场景、编排版本和运行状态。**工序模板**是可复用的已登记能力；**订单 DAG** 是订单级工序选择与依赖关系；**工序实例**是运行时绑定了具体物料 UID、输入输出和执行状态的节点。订单图不直接保存 CoppeliaSim 句柄或可执行代码。

系统支持两种编排入口，并共用同一个订单草稿和编译器：

- **前端编排：**用户新建订单，从允许的工序模板中添加节点、配置投料/产物和依赖关系；界面显示节点状态、输入输出端口、绑定设备/资源摘要以及校验诊断。
- **后端自主编排：**调用方提交目标产物、数量、允许的场景/能力和可选偏好；规划器在已登记模板、连接规则和场景能力范围内选择节点并生成 DAG。规划结果可保存为订单草稿，再启动仿真。

典型流程：`创建订单 → 手工或自动生成 DAG → 结构校验与编译 → 运行仿真 → 查看逐节点结果和订单谱系`。执行一旦开始，该订单使用的 DAG、模板和场景绑定版本固定；修改编排产生新版本，不回写正在运行的订单。

## 3. 总体架构

```text
前端 DAG 编辑器 ─┐
                 ├─► Order API / Planner ─► OrderSpec + DAG Compiler
后端自动规划器 ──┘                              │
                                                ▼
                                  OperationRuntime / OperationGraph
                                                │
                                  Stage4Coordinator / Scheduler
                                                │
                                  Execution adapters / CoppeliaSim
                                                │
                       MaterialRegistry + run events + order report
```

- **前端：**订单草稿、模板节点选择、依赖连线、校验反馈、运行控制和事件/报告查看。前端只编辑声明式数据，不直接生成任务状态或调用仿真对象。
- **Order API 与 Planner：**校验输入格式，创建/版本化订单；手工 DAG 进入编译，自动模式调用受约束规划器输出 DAG。两种模式共用 API、编译器和 Runner，并记录编排来源/版本。
- **工序目录与执行能力注册：**复用 `ProcessCatalog`、`WORK_STEP_TEMPLATES`、`COMPLETION_VERIFIERS`、`DeviceBinding` 和资源/区域定义作为允许执行集合。当前 DB 目录要求与代码目录等价，不应直接作为用户自定义订单存储；订单及其图需独立、版本化持久化。
- **DAG Compiler：**把订单级模板图绑定为运行时实例图，生成入口物料实例和稳定的 operation instance ID；建立前驱关系，解析端口和 UID 谱系，并验证图有可执行入口及目标产物路径。
- **Coordinator 与仿真执行：**继续由 coordinator 唯一推进 `sim.step()`；ready 节点经过现有调度策略和全量资源申请后，交给既有 arm/AGV executor。工序只有在设备执行、应用层完成校验及物料账本提交均成功后才能解锁后继。
- **订单运行记录：**在现有 run record / event / evidence 基础上关联 `order_id`、order version、DAG digest、模板/场景版本、实例状态、节点输入输出 UID、执行决策、失败节点及停止原因。

## 4. 订单 DAG 数据契约

订单持久化至少拆分为订单元数据、不可变订单版本、节点、边、输入/输出约束和运行实例关联。建议首版声明式结构如下，最终字段需与现有 `OperationTemplate`、`MaterialPort` 和 `OperationInstance` 收敛：

```json
{
  "order_id": "order-2026-0001",
  "version": 1,
  "scene_id": "L5_UFLOW",
  "target": {"item_type": "FINAL", "quantity": 1},
  "inputs": [
    {"item_type": "RAW_A", "quantity": 1},
    {"item_type": "RAW_D", "quantity": 1}
  ],
  "nodes": [
    {"node_id": "housing-start", "template_id": "housing_direct_A"},
    {"node_id": "housing-finish", "template_id": "housing_finish"},
    {"node_id": "housing-to-buffer", "template_id": "housing_to_E_buffer"},
    {"node_id": "bracket-start", "template_id": "bracket_process"},
    {"node_id": "bracket-transport", "template_id": "transport_DE"},
    {"node_id": "bracket-to-buffer", "template_id": "bracket_to_E"},
    {"node_id": "kit-join", "template_id": "kit_join"}
  ],
  "edges": [
    {"from": "housing-start", "to": "housing-finish", "port": "input-0"},
    {"from": "housing-finish", "to": "housing-to-buffer", "port": "input-0"},
    {"from": "bracket-start", "to": "bracket-transport", "port": "input-0"},
    {"from": "bracket-transport", "to": "bracket-to-buffer", "port": "input-0"},
    {"from": "housing-to-buffer", "to": "kit-join", "port": "housing-input"},
    {"from": "bracket-to-buffer", "to": "kit-join", "port": "bracket-input"}
  ]
}
```

示例目标为 `FINAL`、输入为 `RAW_A + RAW_D`：两条物料支路分别到达 E 缓冲后，由已登记 `kit_join` handler 绑定父 UID 并提交成品。稳定 `node_id` 与运行时 `instance_id` 分离；instance ID 必须包含订单运行、输入物料 UID 和订单节点 ID，允许同一订单重复使用同一工序模板。边还要说明输出如何满足后继输入端口；AND 合流必须绑定必需端口和物料配对规则，不能仅因有两个前驱就假定可合并。首期不泛化为用户可编程的聚合算子。

## 5. 校验与失败语义

校验分为两个时点，兼顾早期可理解的结构诊断和运行期真实物理验证。

**编排/编译时最低结构检查：**订单和节点 ID 唯一；模板、端口、资源、区域及场景存在；所有边端点有效；DAG 无环；入口投料与工序输入匹配；输出与后继端口的物料类型/区域可连接；目标产物可从入口到达；节点执行器、工步和完成校验器均在 allowlist 中。这些检查不运行 CoppeliaSim，但可避免循环图、无入口图和永远不 ready 的图进入仿真。

**运行到节点时的动态检查：**实际 UID/物料状态、区域容量、资源互斥、AGV dock/路线、设备绑定状态及执行器结果；运动规划、IK、碰撞、抓取/放置和物理完成条件继续由现有控制器与 coordinator 校验。诊断应包含 `order_id`、版本、`node_id`、模板 ID、instance ID、仿真时间和具体判据。

**局部工序失败：**只有阶段 2 明确列入 NODE allowlist 且已验证可隔离的具体 `failure_code` 才按局部失败处理。执行器必须报告已停止且无安全锁存；协调器必须确认物料处于已知安全区域/状态、账本未部分提交、该节点资源已完整释放，且不会影响共享设备或物流。满足条件时将节点标为 `FAILED`，依赖后继标为 `BLOCKED`，无依赖路径的其他分支继续。物理状态不明、账本提交不确定或共享资源影响无法排除时升级为全局故障。局部失败不得调用全局 `_freeze()`；不自动重试、不重新选路、不改写为 DONE。

**全局故障：**安全锁存、仿真会话/stepping/远程连接故障、场景完整性丢失，以及首期尚不能证明可局部隔离的共享执行资源故障，停止整个仿真运行。协调器停止设备与物流动作、释放普通调度资源并保留安全锁存/现场；未知故障默认按全局故障处理。运行终止后停止仿真并完成会话资源清理。

订单状态由目标完成情况和局部失败共同决定：无失败且目标达成时为 `SUCCEEDED`；存在局部失败但独立分支仍完成或订单目标仍达成时为 `PARTIAL_FAILED`；失败分支使目标无法达成时为 `FAILED`；全局故障导致整次运行被终止时为 `ABORTED`。报告记录故障范围、失败节点、受阻后继及仍继续运行的分支。前端按节点显示状态与诊断，同时显示订单级终态。

## 6. 分阶段实施工作包

按依赖顺序推进以下六个阶段。工作量为相对估计，用于拆分审查和排期，不代表日历工期；阶段 2 与阶段 6 的物理风险最高。详细步骤、代码落点和验收见各阶段文档。

| 顺序 | 阶段与工作量 | 主要交付 | 进入下一阶段的条件 | 详细方案 |
|---|---|---|---|---|
| 1 | 契约、目录与编译器（中） | OrderSpec、端口/连接规则、validator、compiler、确定性 digest | 模板订单可编译；关键结构负例有定位诊断 | [阶段 1](v1-order-dag-phase-01-foundation.md) |
| 2 | 局部失败隔离（大，最高风险） | NODE/GLOBAL 分类、依赖后继阻断、订单终态与协调器分流 | 记录型 executor 证明图语义；首个 NODE 错误有 focused CoppeliaSim 安全证据 | [阶段 2](v1-order-dag-phase-02-failure-isolation.md) |
| 3 | API 与持久化（中到大） | 订单版本仓储、统一 service/API、运行关联 | 版本可保存/预览/启动/追踪，step 热路径无 SQL | [阶段 3](v1-order-dag-phase-03-api-persistence.md) |
| 4 | 后端自主规划（中） | 有界确定性 planner、选择解释、无解诊断 | planner 输出经同一 compiler 验证并保存为普通订单 | [阶段 4](v1-order-dag-phase-04-auto-planner.md) |
| 5 | 前端编排（大） | 订单工作台、DAG 编辑、planner 结果预览和运行 trace | 手工与自动订单共享 API，运行状态与服务端一致 | [阶段 5](v1-order-dag-phase-05-frontend.md) |
| 6 | 仿真验收（中，依赖 CoppeliaSim） | 默认回归、自建订单、planner 订单、局部/全局故障证据 | 真实运行通过；未验证故障保持 GLOBAL | [阶段 6](v1-order-dag-phase-06-simulation-acceptance.md) |

阶段 1–2 的稳定契约和失败语义是 API/UI 的前置条件；阶段 3 是 planner 和前端共用的服务边界；阶段 4、5 可在 API 冻结后并行实现，但发布验收必须覆盖两者。测试保持聚焦：每阶段运行受影响测试；新增测试只覆盖关键正例、核心负例和新失败边界；最后跑一次全量纯测试及上述有限的实机验收矩阵，不重复生成同义测试。

实施状态（2026-11-08）：阶段 1–6 均已实施。阶段 1–5 通过纯逻辑/API/记录型执行与前端 E2E 测试，见[阶段 1–3 实施报告](../reports/v1-order-dag-phase-01-03.md)、[阶段 4 实施报告](../reports/v1-order-dag-phase-04-auto-planner.md) 与[阶段 5 实施报告](../reports/v1-order-dag-phase-05-frontend.md)；阶段 6 已在受保护 `L5_UFLOW` 工件上完成手工/planner UI 订单、NODE 前置拒绝和安全锁存全局停止验收，见[阶段 6 实施报告](../reports/v1-order-dag-phase-06-simulation-acceptance.md)。未验证的真实运动/连接故障仍为 GLOBAL；本文其余部分保留原始总体设计。

## 7. 首期验收场景

- **模板回归订单：**用订单 DAG 表达当前 `full_flow` 的输入、路线、共享后续与 E 合流；物料数量、FINAL 父 UID、关键任务、资源释放和完成报告与现有基线一致。
- **用户自建订单：**在首期模板中选择/组合节点，构造一个物理已验证的较小订单，完成 DAG 预览、保存版本、仿真运行和谱系核验。
- **后端自主订单：**给定目标产物和投料约束，planner 产生可执行 DAG，记录规则/策略版本，并经相同编译器和运行入口完成仿真。
- **局部节点失败：**触发一个明确且经分类可隔离的节点执行失败；报告定位 node/template/instance，依赖后继被阻断，无关分支继续；订单按目标完成情况标记 `PARTIAL_FAILED` 或 `FAILED`。
- **全局故障停止：**触发安全锁存或仿真基础设施故障；所有活动执行停止，订单运行标记 `ABORTED`，保留故障与现场证据。
- **结构拒绝：**环路、未知模板、端口不兼容或缺少目标路径在编译阶段拒绝并给出可定位诊断。

## 8. 关键限制与风险

- DAG 拓扑正确不代表物理可行。当前场景只有已绑定设备、固定输入输出区域和已验证控制器路径；运行期仍可能因运动或物理条件失败。
- 模板必须引用已有执行器与完成校验逻辑；新增工艺能力还要实现物料转换、设备绑定、资源语义和仿真验收。
- E join 是基于 `SEMI_CE + PART_DE` 的原子谱系提交，不能简化为纯图 AND 边；其他 join 类型需显式定义并验证。
- 当前 catalog DB 只能加载与代码静态目录逐字段等价的数据。订单版本应独立建模持久化，不能绕过目录校验把用户定义当作生产模板。
- 仓库目前为 Python CLI/应用层结构，没有前端和 HTTP API；需新增服务端 API、前端、访问控制、部署和端到端测试。
- 首期建议一个 CoppeliaSim 场景同一时间运行一个订单；多订单并行仿真与跨场景资源调度另行规划。

## 9. 首期不做

- 任意 Python/Lua 脚本、用户自定义执行器或动态导入代码。
- 未经 focused 仿真验证的跨机械臂替代、新设备和场景布局自动生成。
- 循环 DAG、返工、暂停后恢复、故障自动重派/自动重试及运行中修改订单图。
- MES/ERP 集成、跨订单全局最优排程、复杂时间/成本优化和多仿真实例并行。
- 把图结构静态可达性或能力声明等同于实际运动/物理可行性保证。

## 10. 现有代码复用映射

| 现有能力 | 建议复用方式 |
|---|---|
| `domain/process_model.py` 的 `OperationTemplate` / `ProcessCatalog` | 作为可编排节点与执行路径 allowlist；增量补足端口、目标和场景约束 |
| `domain/operation_runtime.py` 的 `OperationGraph` / `OperationRuntime` | 作为订单 DAG 编译后的运行实例图；扩展通用实例化入口，保留受控状态迁移 |
| `application/coordinator.py` | 从固定 `_poll_sources()` / `instantiate_entry()` 接入编译后的订单输入；继续唯一拥有 stepping 和提交边界 |
| `domain/scheduling.py` 与 `domain/resources.py` | 复用 ready 节点设备选择、可解释决策及原子资源申请 |
| `simulation/execution/` 与现有 controller | 保持已验收的 arm/AGV 执行路径，不在 DAG 层复刻运动控制 |
| `MaterialRegistry` 与 E join handler | 复用 UID、区域、类型和原子合流账本；关联输入/输出工序实例 |
| `persistence/runs.py` / run records | 关联订单版本与运行终态；订单版本仓储独立于静态 catalog release |

此方案是总体计划，不表示所有边界已经完成。阶段 1–6 已实施（见[阶段 1–3 报告](../reports/v1-order-dag-phase-01-03.md)、[阶段 4 报告](../reports/v1-order-dag-phase-04-auto-planner.md)、[阶段 5 报告](../reports/v1-order-dag-phase-05-frontend.md)与[阶段 6 报告](../reports/v1-order-dag-phase-06-simulation-acceptance.md)）；未验证的真实 Arm/AGV 运动错误、连接故障和其他场景仍保持 GLOBAL/未覆盖。各工作包应独立实施，并在报告中记录纯逻辑测试、仿真证据和未覆盖限制。
