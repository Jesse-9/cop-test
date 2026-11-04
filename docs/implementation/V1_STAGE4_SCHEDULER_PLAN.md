---
title: v1 混线阶段 4 实施方案 — 产品变体与能力匹配调度
type: plan
status: proposed
created: 2026-11-04
updated: 2026-11-04
scope: v1 UFLOW / 阶段 4
related:
  - ../wiki/06-工业案例/柔性车间/当前规划/06-22-混线生产阶段4产品变体与能力匹配调度.md
  - ../wiki/06-工业案例/柔性车间/当前规划/06-21-混线生产阶段3配方实例化与工序DAG.md
  - V1_STAGE3_OPERATION_DAG_REPORT.md
---

# v1 混线阶段 4 实施方案：产品变体与能力匹配调度

本方案把 [06-22 阶段 4 规划](../wiki/06-工业案例/柔性车间/当前规划/06-22-混线生产阶段4产品变体与能力匹配调度.md) 拆成可逐步合并和验收的工作包。按门槛顺序实施：每个工作包先通过纯测试，再进入下一包；任何时候都保留阶段 3 的默认 `full_flow` 路径作为回归基线。

## 1. 范围与完成定义

阶段 4 把已有 ready 工序、静态 capability/device 目录、资源表和执行适配器接入确定性派工。默认流程继续按现有 `full_flow` 输入和路线运行；调度器只在 recipe 明确声明的候选中选择设备，不推断路线等价性，不在执行开始后抢占或重派。

阶段完成需同时满足：

- recipe-backed `Stage4Task` 的 legacy 生命周期字段不能绕过 `OperationRuntime` 写入；TaskQueue/coordinator 合法状态迁移仍作用于唯一实例。
- ready 工序可形成可序列化的候选/拒绝/选择记录；静态能力、设备占用、场景绑定、物料条件和资源约束均纳入决策。
- coordinator 将选择结果用于执行器启动；每个工序最多占用一个设备，完整资源集原子申请，执行器仍不调用 `sim.step()`。
- 默认 `full_flow` 的工序、物料、FINAL 父 UID、资源互斥和路线结果与阶段 0/3 基线一致；决策顺序在固定输入下可复现。
- 只对实际候选设备/目标组合声明动态几何验收。若没有经过运动验证的等价替代设备，阶段仍可按固定候选完成，不得把静态 capability 匹配表述成跨设备替代能力。
- AGV 停靠/载荷/接触余量调查单独记录；阶段 4 不放宽停靠、碰撞、接触或安全阈值。

不包含订单/MES 实体、用户可编辑 recipe、任意表达式、全局最优求解、运行中抢占、故障重派、自动重试、路线自动变更或新的仿真 scenario。

## 2. 当前代码边界

实施前以当前代码为准复核，以下落点是起始位置，不构成要求新增同名抽象：

| 现有职责 | 当前落点 | 阶段 4 用法 |
|---|---|---|
| `OperationTemplate.candidate_devices`、`capability`、资源和输入输出 | `domain/process_model.py` | 保持 recipe 允许集合为候选上限；只在缺少真实约束字段时增量扩展静态模型 |
| `CapabilitySpec`、`DeviceSpec` 与目录校验 | `domain/process_model.py`、`domain/default_process.py` | 静态能力/设备元数据来源；禁止复制一套并行目录 |
| ready DAG、实例候选和运行时状态 | `domain/operation_runtime.py` | ready 快照输入与已选 `device_id` 结果落点；保持依赖/提交语义 |
| legacy task 投影、任务队列 | `domain/tasks.py` | 首个工作包修复旧字段任意 setter；兼容字段继续用于序列化和报告 |
| 调度、资源申请、executor 装配与事件循环 | `application/coordinator.py` | 保留单一 `sim.step()` 和物料提交边界；把选择逻辑接入既有启动路径 |
| 设备到场景对象的绑定 | `simulation/device_binding.py` | 只读运行时可用绑定信息；不由 domain 调用 CoppeliaSim |
| 臂/AGV 执行 | `simulation/execution/arm.py`、`agv.py` | 使用调度器选定设备；继续包装既有控制器 |
| 资源全量申请 | `domain/resources.py` | 继续使用 `ResourceTable.acquire_all()` / `release_all()`；避免部分持锁 |
| 当前验收基线 | `docs/implementation/evidence/v1_stage3_full_flow_20261104_153954.json` | 比对阶段 3 图、工序、物料、FINAL 和航线字段；阶段 0 证据用于时间线/结果参照 |

## 3. 工作包与停止门槛

### WP0：冻结基线并修复 runtime-backed 写入口

**修改范围：**`domain/tasks.py`、`domain/operation_runtime.py`、必要时 `application/coordinator.py`；扩展 `tests/test_v1_domain_tasks_resources.py`、`tests/test_v1_operation_runtime.py` 和协调器相关纯测试。

1. 盘点当前 coordinator/TaskQueue 对 task 生命周期字段的所有赋值点，列出状态、时间、资源、证据和失败原因分别由哪个 runtime 方法负责。
2. 为 recipe-backed task 去除可任意改写 runtime 实例的 legacy setter；把必要迁移收敛到 `OperationRuntime` 窄方法。只读字段仍能被旧报告/序列化读取。
3. 保留非 DAG reposition 等兼容任务所需行为，但通过明确的类型/构造路径隔离，不以通用 setter 让 runtime-backed task 可变。
4. 加一组边界回归测试：runtime-backed task 直接赋值被拒绝；通过现有合法生命周期入口完成一次状态迁移后，实例与序列化视图一致。复用既有队列/协调器测试覆盖其余生命周期，不为每个代理字段复制用例。

**门槛：**受影响纯测试和现有任务/资源/coordinator 测试通过；`full_flow` 旧实现代码路径不因此次迁移改变。如需更新多个生命周期调用点，先完成本包再进入调度逻辑。

### WP1：定义可审计的纯调度输入输出

**修改范围：**优先在 `domain/` 定义纯数据结构；若现有模块不适合，放入 `application/` 的纯策略模块。不得从该策略模块导入 CoppeliaSim、scene handle、执行器或协调器。

1. 输入快照至少包含 ready operation 的稳定 ID、recipe 允许候选设备、能力要求、优先级/ready 时间、物料与 zone 条件，以及当前活动执行和资源 owner 的只读视图。
2. 输出一条 decision：operation instance ID、按确定性规则排序的候选、每个候选的通过/拒绝原因、最终选中设备或暂不派工原因、策略版本。字段必须能 JSON 序列化；不放对象句柄或 Python 对象 repr。
3. 首版排序固定为：业务/配套优先级（当前无订单优先级时使用现有 operation priority）→等待时间→ready 时间→稳定 operation instance ID。候选设备按 recipe 声明顺序或显式稳定键排序，不依赖 dict/set 遍历顺序。
4. 此包只计算结果，不申请资源、不更改 operation 状态、不推进仿真。

**测试：**少量纯单元测试覆盖稳定排序、能力/allowlist 拒绝理由和无可用候选；其中一个测试改变输入顺序验证确定性。序列化只验证一次 decision 可 JSON 编码，不为每个字段做 round-trip 测试。

**门槛：**纯策略测试全过，且模块依赖边界测试确认不导入 `simulation` / `application.coordinator`。

### WP2：补齐静态能力和运行时可用性过滤

**修改范围：**`domain/process_model.py`、默认目录，仅在实际缺约束时增量扩展；纯筛选继续由 WP1 策略消费。

按以下次序对每个 recipe 声明候选做筛选，并保留每一步理由：

1. **Recipe allowlist：**设备必须属于该 operation 的 `candidate_devices`；不得从同能力目录自动扩展集合。
2. **静态能力：**设备能力包含 operation 要求；只有目录已有且有明确量纲/语义的字段才参与负载、工具或输入输出类型比较。当前未建模的姿态/尺寸等不得猜默认值。
3. **绑定与目标：**设备有有效 `DeviceBinding`，operation 所需 source/sink/route 在该设备绑定覆盖内。绑定信息可由 application 预先整理成纯快照，domain 不查 scene handle。
4. **设备占用：**BUSY 从活动 execution 和资源 owner 推导；不建立可独立漂移的可变 BUSY 镜像。只有目录外存在权威健康/禁用数据源时才加状态过滤，本阶段没有就记录 `not_modeled`，不可伪造健康状态。
5. **工序/物料前提：**实例仍为 ready，输入 UID 在期望区域且目标容量/互斥条件符合现有 runtime 与资源模型。
6. **物理可行性：**底层 arm planner 仍为最终 IK/碰撞/路径判据。候选若尚无针对该目标组合的动态验证，不自动做跨设备重派；将其标为未验收候选并保持现有固定设备选择。

**测试：**WP1 的候选过滤测试覆盖 recipe 外设备和能力不匹配；再用一个目录/绑定负例确认设备未绑定时不可选。其余 readiness 与资源行为由 WP3 的集成测试覆盖，避免跨层重复验证。

**门槛：**默认 recipe 的每个 operation 都能解释当前固定设备为何可行；所有未建模维度在 decision 中明确标注，不静默当作通过。

### WP3：连接 coordinator 与原子资源申请

**修改范围：**`application/coordinator.py`，必要时扩展 executor request/result 数据结构；不更改 `simulation/execution/` 控制算法。

1. 在 `_dispatch_ready()` 读取 ready instances 与当前只读运行快照，向纯策略请求 decision。
2. 对选定候选以完整工序资源集调用 `ResourceTable.acquire_all()`；失败时不保留任何资源，记录 busy owner/拒绝原因并让 operation 保持可等待状态，不把资源等待标成 FAILED。
3. 资源申请成功后再将选定 `device_id` 和对应 operation/sink/route 上下文传给现有 `ArmExecutor` 或 `AgvExecutor`。单个 instance 只启动一个 execution；execution ID 回写经 runtime 受控接口完成。
4. 若启动前检查失败，按既有统一释放路径归还刚申请资源，记录明确拒绝原因。已启动的物理执行不在运行中换设备；失败/安全锁存继续沿用现有冻结语义。
5. 保持 E `kit_join` 的逻辑账本 handler，不伪装为 `logic_E` 物理 executor；保持传送带现行设备命令和线资源语义。
6. 调度事件至少记录 run/instance/template、ready 时间、候选全集、逐项判断、资源结果/冲突 owner、选择、策略版本、仿真时间和 execution ID。

**测试：**新增一个 coordinator 集成测试，以记录型 executor 驱动 ready operation，验证选中设备被启动、资源冲突时不启动且无部分持锁、成功/失败后资源正确释放。复用现有 stepping-boundary 测试，不再复制 `sim.step()` 次数断言。

**门槛：**全部 coordinator/executor/runtime/resource 纯测试通过；已有 `full_flow` 路径仍可由调度决策得到与原来相同的设备分配。

### WP4：用 full_flow 验证决策和结果等价

**仿真前检查：**运行全量纯测试；确认当前工件 `config_digest` / `layout_digest` 未变；静态绑定校验通过；证据输出路径和运行命令记录在实施报告草稿中。

**仿真验收：**只运行 `full_flow`，命令沿用项目入口：

```powershell
FLEXIBLE_FACTORY_SIM=1 python -m pytest -q tests/test_v1_stage4_scheduler_sim.py -s
```

按阶段 2 已验证的方式保持 CoppeliaSim 运行；需要多次运行时每次使用独立进程/报告名，不能把分次结果拼成一次测试轮次。若无仿真环境，可完成纯测试但不得宣称端到端通过。

逐项核对：

- 所有预期 operation instance 完成，图 `graph_complete=true`，无重复派工/资源泄漏/安全锁存。
- `FINAL` 数、父 UID、输出 UID/zone、未配对 `SEMI_CE`、任务类型/数量和 AGV 路线与阶段 3 evidence 一致。
- 默认路线选择的设备符合 recipe allowlist 与 capability 结果；每个派工 decision 能由事件重放/解释。
- 资源表无部分持锁，line/dock/lane/相邻 arm zone 原互锁成立。
- decision 序列符合纯策略确定性测试；集成验收只需核对本次 trace 与策略选择一致，不额外重复跑仿真来证明排序稳定。
- 从事件重算 throughput、WIP、等待与并发工序数，并与 summary 对照；若重算不一致，修复指标/事件证据后再验收。

**产物：**`docs/implementation/V1_STAGE4_REPORT.md`、裁剪后的正式 evidence JSON；大体积逐 tick run 留在被忽略的 `runs/`。报告分别写明纯测试、每次仿真结果、基线差异、未覆盖设备组合和任何失败尝试。

### WP5：等价候选设备的受控扩展（条件工作包）

默认阶段验收不依赖本包。仅当业务确认两设备执行的是同规格等价工序时实施：

1. 选一个 operation 和一个额外候选，不批量开放五臂互换。
2. 先为候选目标组合做一次 focused 仿真验证：实际 target 的 IK、碰撞、路径、负载/吸附、完成验证和物料提交全部通过。
3. 纯策略测试候选选择和稳定 tie-break；coordinator 测试选择后资源冲突/失败处理。
4. focused 仿真证据通过后，才把该设备加入该 operation 的 recipe allowlist；再运行 `full_flow`。
5. 若候选几何失败或业务规格不等价，保留候选拒绝证据并撤回 allowlist 变更；阶段 4 主路径仍采用原固定映射。

不做“能力相同即自动替换”，也不做已开始 operation 的运行时重派。

### WP6：运动容差余量复核（独立跟踪，不与调度验收混算）

以阶段 2/3 已有记录为待调查问题：AGV stop error 曾为约 0.0195–0.0199 m，距 `0.02 m` 阈值很近；重复 `full_flow` 有托盘偏移后 pre-place 横向误差超限记录。

1. 针对已记录的 D→E 载货停靠与 E 放置失败链，做一次 focused 复核，记录 stop error、material slip、pre-place lateral error 和结果。
2. 只有 focused 结果不能区分偶发波动与稳定问题时，再增加重复运行；本阶段不要求样本分布或分位数报告。
3. 保留原容差、安全锁存和失败停止行为，不在调查中静默调松阈值。若要修改控制器/工件几何/接触规划参数，另开实现变更与针对性回归。

该工作包可以与 WP1–WP4 并行排期，但物理调查结果不能代替调度逻辑验收，也不应阻塞纯策略开发。若评估结果影响 `full_flow` 稳定通过，应在阶段报告中作为未关闭风险，不宣称稳定性达标。

## 4. 精简测试集

新增测试控制在三组，优先扩展现有测试文件，不为每个筛选字段或状态属性建立独立用例：

| 测试组 | 必须证明的行为 |
|---|---|
| runtime/task 边界 | runtime-backed legacy 写入被拒绝；合法生命周期更新仍反映到同一实例与兼容序列化 |
| 纯调度策略 | 候选过滤与拒绝原因正确；排序稳定；无可行候选时不派工 |
| coordinator 接线 | 所选设备收到执行请求；资源申请失败无部分持锁且不启动；完成/失败释放资源 |

其余使用现有保证：运行相关现有纯测试全套，复用 stepping-boundary 与设备绑定校验；最后只跑一次 `full_flow` 并对照阶段 3 evidence。只有出现失败或实际新增等价设备时，才增加对应的 focused 仿真用例。运动余量复核独立记录，不作为新增调度单测矩阵。

### 现有目录负例的去重记录（2026-11-04）

在 `tests/test_v1_process_model.py` 中，将“同一 operation 的未知设备/能力/区域/资源引用”和“缺少输入/输出/工步模板引用”改为 pytest 参数化场景。每个非法输入仍独立执行、独立显示失败参数，覆盖数量和校验断言不变；仅合并重复的目录构造与断言代码。入口路线负例保留原结构，因为每种错误的上下文与预期诊断不同；重复 capability/zone ID 也暂不抽象，两个用例共用结构不足以抵消额外 helper 的复杂度。

## 5. 建议执行命令与报告规则

纯测试按仓库现有环境运行：

```powershell
python -m pytest -q -p no:cacheprovider
```

若 pytest 临时目录受执行环境限制，按仓库已有方式将 `TEMP`/`TMP` 和 `--basetemp` 指向工作区内临时目录；运行后只清理本次创建的临时目录。

每个实施报告区分通过、失败、跳过和未执行；静态绑定、动态目标几何和完整端到端证据分别陈述。仿真失败如发生在已知运动余量链路，保留并说明该次结果。

## 6. 变更边界与回退

- 每个工作包单独提交/审查；WP0 未通过不得开始 WP3 运行时状态写入迁移。
- 调度器不导入 CoppeliaSim，不负责物料提交，不推进时钟；executor 不反向调用调度器。
- 默认路径的选中设备保持原分配，直到新增候选具备独立 focused 证据。
- 若接入导致 `full_flow` 结果与阶段 3 基线不符，先保留 decision trace 和事件证据，修复选择/资源语义；不得通过改变物料配方、FINAL 配对规则或安全阈值使汇总数字相等。
- 不修改受保护工件或 scene contract/config digest，除非单独确认确需改变并建立迁移/验收方案。
