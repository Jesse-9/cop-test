---
title: 自建订单 DAG 阶段 6 — CoppeliaSim 集成验收与发布
type: plan
status: implemented
created: 2026-11-06
updated: 2026-11-08
scope: v1 UFLOW / CoppeliaSim 4.10
related:
  - v1-self-built-order-dag-orchestration.md
  - v1-order-dag-phase-01-foundation.md
  - v1-order-dag-phase-02-failure-isolation.md
  - v1-order-dag-phase-03-api-persistence.md
  - v1-order-dag-phase-04-auto-planner.md
  - v1-order-dag-phase-05-frontend.md
  - ../reports/v1-order-dag-phase-06-simulation-acceptance.md
  - ../reports/v1-stage-04-capability-scheduling.md
---

# 自建订单 DAG 阶段 6：CoppeliaSim 集成验收与发布

## 目标和验收边界

证明手工构造订单和 planner 订单使用同一个 `OrderSpec → compiler → coordinator → executor` 路径，在受保护的 `L5_UFLOW` 工件中实际运行，并能通过订单版本、DAG digest、节点事件、物料谱系和仿真报告复核。还要验收一个物理动作前的 NODE 局部拒绝，以及一个真实安全/基础设施 GLOBAL 停止路径。

本阶段验证当前登记模板与绑定设备的组合，不证明任意 DAG、未登记设备替换、所有机械臂运动故障或其他场景都可运行。拓扑和物料合法性由阶段 1/4 的领域测试覆盖；本阶段只为实际运动、物理状态、生命周期、持久化和前端一致性补真实 CoppeliaSim 证据。

重要分类边界：当前仅 `NODE_PRECHECK_REJECTED` 可在物理动作前、状态可确认时按 NODE 处理；真实 ArmExecutor/AgvExecutor 尚无经准入的 NODE 运动错误。阶段 6 的局部失败用例只验证该前置拒绝和独立支路继续，不得把碰撞、抓取/放置失败、AGV 故障或账本不确定性提升为局部错误。真实运动故障仍按 GLOBAL，直到针对具体 failure code 完成单独的 focused 物理验收并显式加入 allowlist。

## 进入条件

开始实机验收前全部满足：

1. 阶段 1–5 的实施报告、受影响纯测试/API/UI contract 测试通过；全量非仿真测试在本次待验代码版本上通过。
2. 已检查阶段 2 分类表及 `NODE_FAILURE_ALLOWLIST`。无 focused 证据的真实运动类故障仍是 GLOBAL；禁止用本阶段验收绕过 allowlist。
3. `L5_UFLOW` 当前 artifact 可被 lifecycle 校验；记录 `artifact_id`、scene/config/layout digest、catalog version、ORDER contract version、planner policy version 和源代码 revision。
4. CoppeliaSim 4.10 已启动，ZeroMQ Remote API add-on 可在 `127.0.0.1:23000` 访问；没有其他进程占用项目运行锁或控制该仿真会话。
5. 验收使用独立临时 SQLite DB 或已备份的验收 DB，报告写到 `docs/implementation/runs/`；不改写保护工件、不覆盖正式证据、不使用生产数据目录。
6. 明确操作员负责在真实安全故障后确认仿真停止态、现场物料/设备状态；不能仅依据 API 的 `ABORTED` 就开始下一次运行。

若任一条件不满足，停止在静态检查阶段，记录阻塞原因；不得把 skip、超时或无法连接记成通过。

## 分阶段执行步骤

### 6.1 固定测试对象和证据目录

1. 建立本次验收记录目录/批次 ID，保存 commit/revision、命令、启动参数、环境版本和每次 run ID。
2. 复制并校验当前 artifact manifest 与 digest；确认 manifest 指向保护场景，不执行 build/migrate/覆盖操作。
3. 选用验收专用 DB，运行必要迁移并写入本次订单/运行记录；在实机运行前保存初始 DB 备份或使用新建临时 DB。
4. 选择以下两份固定订单定义作为正向样例：
   - 手工自建订单：以 `tests/order_fixtures.py::full_flow_order()` 为起点，从 UI 创建并保存为 `source=manual` 的订单版本；输入 `RAW_A × 1 + RAW_D × 1`，目标 `FINAL × 1`。
   - planner 订单：目标同为 `FINAL × 1`，可用投料同为 `RAW_A × 1 + RAW_D × 1`，让阶段 4 planner 生成并保存 `source=planner` 版本。
5. 记录每个版本的规范化 spec、version、digest、catalog/scene/artifact 引用。不得直接把测试 fixture 当作“用户完成 UI 编排”的证据；UI 旅程需由实际前端操作完成并导出其保存结果。

### 6.2 启动前静态闸门

1. 运行受影响的订单领域、失败隔离、service/API、planner、前端 contract 测试，然后运行一遍全量非仿真测试；保存命令、revision 和通过/失败/skip 数。
2. 对两份订单分别执行 `order preview`，核对 diagnostic 为空、目标可达、入口与模板端口匹配、join handler 合法、编译 digest 稳定。
3. 使用阶段 1–4 已有记录型 executor 测试作为逻辑证据，不在阶段 6 重跑同义的 mock 图状态矩阵。核对代表性正例证据覆盖 ready/commit、UID 谱系、订单终态和资源释放。
4. 用 `flexible-factory status --offline` 核对生命周期无活动运行/残留锁；启动前核对 CoppeliaSim 版本、ZMQ 地址、工件引用和 DB 路径。
5. 若启动前校验、digest 或工件不匹配，修复后从 6.1 重新建立证据批次，不能沿用旧订单/旧证据混写。

### 6.3 CoppeliaSim 基线回归

1. 使用现有 `tests/test_v1_stage4_scheduler_sim.py` 入口执行已发布 v1 artifact 下的 `full_flow` 基线；启用 `FLEXIBLE_FACTORY_SIM=1`，本机测试预计可能需 20–40 分钟墙钟时间。
2. 将本次基线与 `docs/implementation/evidence/v1_stage0_1_full_flow_acceptance_20261103_132919.json` 及阶段 4 基线对比：订单外的既有 full_flow 路径没有意外回归；检查 11 项物理调度决策、两条路线、1 个 FINAL、父 UID 关系、资源释放、无安全锁存和 runtime destroy。
3. 对 UID 不要求固定数值；要求 FINAL 的两个父 UID 对应本次 `RAW_A` 与 `RAW_D` 输入谱系，且物料类型/位置符合模板预期。
4. 若基线失败，停止后续订单验收；分类是代码回归、工件/环境偏差或既有不稳定性，重新运行前保留本次失败报告，不把重复运行中最好的一次单独当作唯一结果。

### 6.4 实际使用用例 A：工艺工程师手工编排并运行订单

**目标：**工程师从允许模板创建订单 DAG，保存不可变版本，经仿真生产一件 `FINAL` 并追踪物料来源。

前置：UI 与本机订单 API 已启动；API health 返回的 contract/catalog/scene 信息与本批次记录一致；CoppeliaSim 使用已校验 artifact。

操作旅程：

1. 打开订单工作台，新建空白订单，填写订单 ID/备注（若 UI 支持）、scene `L5_UFLOW`、目标 `FINAL × 1` 和投料 `RAW_A × 1`、`RAW_D × 1`。
2. 从 palette 添加 `housing_direct_A`、`housing_finish`、`housing_to_E_buffer`、`bracket_process`、`transport_DE`、`bracket_to_E`、`kit_join`；用端口建立两条合法支路并连接到 `kit_join` 的两个必需输入。
3. 执行服务端 preview；确认拓扑、端口、物料路径和目标通过。保存为新订单 v1，离开并重新打开，核对节点/边和 digest 未变。
4. 在启动确认中核对订单版本、DAG digest、目录版本、`L5_UFLOW` artifact ID 与目标，确认后启动。
5. 观察运行页中输入投放、并行 ready 分支、已选设备/资源、`kit_join` 等待两个输入后执行、`FINAL` 提交和订单终态；刷新 run URL，确认从服务端恢复同一运行状态。
6. 打开物料谱系，确认生成 1 个 `FINAL`，父 UID 为本次 RAW_A/RAW_D 两个 UID；检查每个操作实例状态、输入/输出 UID、设备执行记录、资源 owner 清空和报告完整。

通过标准：run 终态 `SUCCEEDED`；compiler digest 与保存版本一致；只产生目标数量的 FINAL；物料父子关系正确；没有 `FAILED/BLOCKED` 节点和安全锁存；资源释放、仿真停止和 runtime destroy 均确认。记录截图/导出的 UI 状态只作辅助，结构化 run report 和 DB 记录才是主要证据。

### 6.5 实际使用用例 B：规划员自主生成并运行订单

**目标：**规划员只输入产品目标与可用投料，由后端确定性 planner 生成普通订单草稿，经人工确认后运行。

操作旅程：

1. 打开自主规划页，提交目标 `FINAL × 1`、`RAW_A × 1`、`RAW_D × 1` 和 `L5_UFLOW`；不额外限定未经验证的设备替代。
2. 查看 explain：选择的模板/边、join 绑定、投料消耗、策略版本、候选排除原因和未建模维度；确认无解/编译诊断为空。
3. 将候选保存为 `source=planner` 的订单草稿；检查保存响应中的 order version、plan digest 与该版本 DAG digest，不混淆两种 digest。
4. 在订单详情 preview 并启动明确版本；按用例 A 的运行页检查关键节点状态、设备、物料和谱系。
5. 查询订单历史与 run report，核对 planner policy version、输入摘要、来源字段、订单版本、DAG digest、artifact/catalog 版本和 run ID 均可追溯。

通过标准：planner 输出经同一 compiler；保存版本与启动版本一致；运行终态 `SUCCEEDED`、FINAL 数量和父 UID 关系正确；运行记录可反查到 planner 输入/策略。planner 生成解释正确但实机执行失败时，结论是“planner 逻辑通过、物理运行失败”，不得合并成通过。

### 6.6 故障用例 C：可隔离的节点前置拒绝与独立支路继续

**目标：**验证首个 NODE failure code 仅在物理动作前生效，只阻断其依赖后继，不停止无依赖支路。

1. 先检查阶段 2 已完成首个 `NODE_PRECHECK_REJECTED` 的 focused CoppeliaSim 安全准入证据；没有该证据时，本用例保持未执行，所有真实执行器错误继续 GLOBAL。
2. 构造最小订单/受控注入，使 A 节点因确定性前置条件拒绝，在 executor 尚未移动、抓取或释放物料前报告 `NODE_PRECHECK_REJECTED`；同一运行安排独立支路 C 可运行，且 A 的下游 B 明确依赖 A。
3. 运行前保存物料初态、支路图、注入条件和使用模板/工件；注入点必须只影响本次验证，不修改保护 scene artifact。
4. 检查 A 标记 FAILED，B 及所有依赖后继标记 BLOCKED/CANCELLED 且无 dispatch/commit；C 正常执行、提交并释放资源；A 绑定物料的位置可核实且账本无部分提交。
5. 核对订单终态按目标可达性为 `PARTIAL_FAILED` 或 `FAILED`，失败事件包含 node/template/instance/failure code/scope/sim time、安全状态、阻断原因和继续分支。
6. 若 executor 实际发生过运动、物料位置/账本不确定、资源未释放、共享 AGV/输送带受影响或状态确认失败，立即升级 GLOBAL；该次不能作为 NODE 准入证据。

该用例只给 `NODE_PRECHECK_REJECTED` 准入，不开放 Arm/AGV 真实运动失败码。为“成功完成目标但另有支路失败”的 `PARTIAL_FAILED` 收敛语义，阶段 2 记录型测试负责；不额外构造另一条耗时实机矩阵。

### 6.7 故障用例 D：GLOBAL 安全/基础设施停止

**目标：**确认真实场景发生安全或仿真基础设施故障时，全运行停止并保持故障语义。

1. 先用现有受控 conveyor stop/安全故障 harness 选择一个代表性 GLOBAL 故障。优先采用能确定性触发安全锁存且可以读取其最终状态的注入；step/连接断开故障由可控 harness 触发一次，避免破坏共享运行环境。
2. 在多个节点处于 READY/RUNNING 或现场有活动设备时注入故障；记录注入时刻、活动节点、仿真时间、安全 latch 和故障码。
3. 确认 coordinator 停止所有活动 executor/输送动作并进入全局 freeze；运行终态为 `ABORTED`（不是普通节点 FAILED），普通调度资源释放，安全锁存仍保持，未完成节点不报 DONE。
4. 确认最后已知物料 UID/位置/节点阶段可读取；如连接故障使状态无法确认，证据应标为状态未知并阻止自动重启，不得假设设备已安全静止。
5. 由操作员按现场/仿真安全规程确认会话停止、清理条件和新一轮启动前置条件；再次启动前验证工件仍完整、无遗留运行锁，旧 run 保持 ABORTED。

连接故障与安全锁存应各自保留一条 focused 证据；它们属于不同全局边界。若现有测试 harness 已证明某一子类在相同真实边界被完整覆盖，可复用证据，不重复打断仿真会话。已有 `conveyor_stop` evidence 若语义仍相同可作为回归参考，但须以新 coordinator/订单运行状态契约重新验证，不能直接推断新路径通过。

### 6.8 运行记录、UI 与生命周期交叉核验

1. 对用例 A/B/C/D 逐一以 `run_id` 查询 CLI `status/nodes/events` 和 API/UI 对应页面；关键字段要和最终 JSON report、订单 DB 记录一致。
2. 验证每个 run 都关联 `order_id/version/dag_digest/artifact_id/catalog_version`；事件顺序包含节点启动/完成或失败、物料提交、资源释放、故障传播及终态。
3. 对局部失败检查 BLOCKED 原因链和继续执行分支；对 GLOBAL 检查全部活动节点最终态与全局 stop reason；对成功运行检查 FINAL UID 谱系和 target quantity。
4. 页面刷新、重新打开 run URL 后仍可读终态；UI 不得把 worker/API 中断渲染成成功，或把运行失败原因替换为泛化提示。
5. 确认运行生命周期完成后 CoppeliaSim 已按 runner 契约停止；查询 lifecycle 状态并确认 runtime 清理，无残留活动控制器、资源 owner 或 run lock。

### 6.9 汇总、复跑和发布判断

1. 每次尝试各自产生独立 run ID 和报告；失败后修复产生新 attempt，不覆盖原始失败证据。
2. 对可重复的订单正例允许一次确认性复跑；只有当失败有可解释且修复后的全新 attempt 全部通过，才给出通过结论。相同输入 digest/策略版本的 planner 订单应保持相同 DAG digest。
3. 对局部失败按 failure code 逐项列出准入范围；未跑、未能确认安全或仅有 mock 证据的类型仍列为 GLOBAL/未准入。
4. 形成报告：环境/工件清单、用例矩阵、每次 attempt 结果、物料/节点关键证据、资源/生命周期清理、UI/API/DB 一致性、偏差和发布结论。
5. 发布前执行一次全量非仿真回归；将正式 evidence 收敛为可审查 JSON/摘要及报告链接，不提交大型逐 tick 控制器轨迹或临时日志。

## 实机验收矩阵

| 用例 | 是否需要 CoppeliaSim | 核心证明 | 通过判据 |
|---|---:|---|---|
| 固定 `full_flow` 回归 | 是 | 已发布场景和既有控制器/调度基线未回归 | 与基线关键工序、路线、最终物料和资源判据一致 |
| 手工自建订单 `RAW_A + RAW_D → FINAL` | 是 | UI 保存的自建 DAG 进入实际执行链 | `SUCCEEDED`、FINAL 父 UID 对应输入、资源/生命周期干净 |
| planner 订单同目标 | 是 | 后端生成版本复用同一 compiler/runner | planner 来源和策略可追溯，物理结果同目标约束 |
| `NODE_PRECHECK_REJECTED` 局部拒绝 | 是，需阶段 2 准入证据 | 物理动作前拒绝、依赖后继阻断、独立支路继续 | 物料/账本已知、A 未运动、B 不派工、C 提交、资源释放 |
| 安全锁存 GLOBAL | 是 | 安全故障导致全运行停止 | `ABORTED`、全活动执行停止、安全锁存保留 |
| step/连接 GLOBAL | 有界 harness/受控故障 | 仿真基础设施异常不被局部吞掉 | `ABORTED` 或按服务契约记录 crash；不伪报 DONE，可确认边界内状态 |
| 刷新恢复和报告一致性 | 是，复用上述 run | UI/API/DB/report 一致且可追踪 | 相同 run/version/digest/status/events，无前端推断终态 |

测试预算为 3 个正向实机运行（full_flow 回归、手工订单、planner 订单）和 2 个故障边界（NODE 前置拒绝、GLOBAL 停止）；连接故障若安全锁存 case 未覆盖其基础设施边界，再加 1 个有界 harness case。UI/API 刷新复用这些 run，不另启动仿真。

## 常用命令模板

命令须按当前 CLI `--help` 和验收 DB/artifact 参数确认后执行；占位符不得原样复制运行。

```powershell
# 启动前检查已有 lifecycle（不连接 CoppeliaSim）
uv run flexible-factory status --offline

# 校验订单定义与已保存版本
uv run flexible-factory order validate --file <manual-order.json> --db <acceptance.db>
uv run flexible-factory order preview --order-id <order-id> --version <version> --db <acceptance.db>

# 运行指定不可变版本；当前 CLI 同步执行
uv run flexible-factory order start --order-id <order-id> --version <version> `
  --artifact-id <artifact-id> --time 1200 --run-records on --db <acceptance.db>

# 汇总证据
uv run flexible-factory order status --run-id <run-id> --db <acceptance.db>
uv run flexible-factory order nodes --run-id <run-id> --db <acceptance.db>
uv run flexible-factory order events --run-id <run-id> --db <acceptance.db>

# PowerShell: 启用真实仿真门控后运行 full_flow 回归
$env:FLEXIBLE_FACTORY_SIM = "1"
uv run pytest -q tests/test_v1_stage4_scheduler_sim.py -s
```

HTTP 前端用户旅程使用 API 的 `start`、run status/nodes/events 查询和 stop；实机验收不得同时用 CLI runner 和 API worker 控制同一 CoppeliaSim 实例。

## 证据字段和留存

每个用例至少记录：`source_revision`、UTC 时间、CoppeliaSim 版本、ZMQ host/port、`artifact_id` 和场景/config/layout digest、catalog/contract/planner 版本、订单来源、`order_id/version`、规范化 `dag_digest`、planner 输入摘要（如适用）、`run_id`、命令/运行入口、初末仿真时间、墙钟耗时、节点状态、事件、失败 scope/code、输入输出 UID/父 UID、资源清理、lifecycle 清理、报告路径和结论。

截断 `controller_history` 中逐 tick 的 `motion/plan/events`、重复 `task.evidence` 和大型原始轨迹；保留诊断所需的失败摘要与关键状态转换。原始失败 attempt 仍应保存足够判断原因的摘要，不能只保留最终成功运行。

## 禁止的验收替代

- 记录型 executor、preview 通过或纯逻辑 dry-run 不能替代设备运动、碰撞、抓取/放置、物理位置和实际物料谱系验收。
- `full_flow` 默认订单通过不能替代手工自建订单和 planner 订单实际运行。
- `NODE_PRECHECK_REJECTED` 不代表 Arm/AGV 运动故障可以局部隔离；未准入运动错误保持 GLOBAL。
- conveyor stop 或 coordinator mock 不能替代安全锁存和 stepping/连接异常的边界验证；但也不得为了证明故障而损坏/覆盖保护场景工件。
- UI 截图、运行页面显示和 CLI 返回码不能单独替代 JSON report、数据库状态、节点事件和物理谱系的交叉证据。
- 超时、skip、连接失败或未执行用例都不是通过。
- 不得放宽 IK、碰撞、接触、停靠、安全阈值或 artifact contract 来制造通过结果。

## 验收与发布门槛

- 阶段 1–5 测试、基线 `full_flow`、手工订单、planner 订单、局部前置拒绝及 GLOBAL 停止均有各自独立结果；不可拼接多次运行形成单一通过证据。
- 手工/planner 正向订单绑定固定 artifact 与不可变订单版本，完成目标并通过 UID 谱系、调度/资源和 lifecycle 核验。
- 经过物理验证的 NODE failure code 才可加入 allowlist；所有未验证运动、共享资源及基础设施故障保持 GLOBAL。
- API、CLI、UI、DB 和 run report 的终态与失败范围一致；保护工件未被修改，未留活动控制器、运行锁和资源 owner。
- 发布说明包含安装/启动 UI/API、CoppeliaSim/ZMQ 前置条件、实际订单操作流程、终态和停止语义、已验证 failure code、未覆盖风险与恢复步骤。
- 正式报告分别记录纯逻辑/API/UI 既有证据引用、仿真基线、手工订单、planner 订单、NODE/GLOBAL 故障、失败尝试及限制。
