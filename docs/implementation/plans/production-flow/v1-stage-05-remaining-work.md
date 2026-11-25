---
title: 阶段 5 剩余工作 — 设备状态、未启动工序改派与有限恢复
type: plan
status: proposed
created: 2026-11-24
updated: 2026-11-25
scope: v1 UFLOW / 业务阶段 5 / WP2–WP6 / 未实施
related:
  - ../../reports/production-flow/v1-stage-05-finite-recovery.md
  - ../../archive-plans/production-flow/v1-stage-05-finite-recovery-implementation.md
  - ../../../wiki/06-工业案例/柔性车间/当前规划/06-23-混线生产阶段5调度优化与有限恢复.md
---

# 阶段 5 剩余工作 — 设备状态、未启动工序改派与有限恢复

本方案只保留业务阶段 5 **未实施**的 WP2–WP6。WP0（`cart_2` 不可用业务
用例与冻结基线）和 WP1（订单级调度观测）已实施，见
[阶段 5 实施报告](../../reports/production-flow/v1-stage-05-finite-recovery.md)；
完整原始方案已归档到
[阶段 5 完整方案](../../archive-plans/production-flow/v1-stage-05-finite-recovery-implementation.md)。

业务用例：dual 固定分工下，`cart_2` 在 `transport_DE` 启动前不可用；目标
是只对尚未启动的 `transport_DE` 撤销 `cart_2` 选择并改派给已验收的
`agv_1`。执行中故障、自动恢复、轮式物理控制不在首轮范围。

## WP2：引入有证据来源的设备状态

**前置：**首个用例的设备状态源已明确；未明确时只允许补充需求/基线。

1. 只定义首个用例需要的状态：可接单、忙、故障/停用；维护状态仅在有实际
   维护数据源时加入。忙状态继续从活动 execution/resource owner 派生，不复制
   成可漂移的可变镜像。
2. 状态变更携带来源、仿真时间和诊断证据；未知、过期或来源不明的状态不得
   视为可接单，并生成可追踪的拒绝原因。
3. 静态能力仍不能替代目标姿态 IK、碰撞和路径验证；只使用已通过 focused
   仿真的候选组合。

**落点：** `domain/process_model.py`、`domain/scheduling.py` 的
`DeviceView`、`application/coordinator.py` 的设备快照/事件接线；
测试在 `tests/test_v1_scheduling.py`、`tests/test_v1_coordinator_scheduler.py`。

**通过门槛：**覆盖可接单→故障→恢复可接单、未知状态阻止派工、过期证据
拒绝；候选过滤不改变正常 `full_flow` 固定映射。

## WP3：只对尚未启动的工序局部改派

**前置：**WP2 通过；两个候选设备都已完成 focused 运动验证且被 recipe
明确允许。

1. 定义可撤销条件：工序尚未启动、没有 execution ID、未持有物理执行资源、
   输入物料仍归该工序管理、前驱和配方候选未改变。
2. 撤销旧候选选择和 queued task projection；事件记录旧/新设备、故障证据、
   撤销结果及资源 owner。撤销不改物料账本、不增加 attempt。
3. 只对 `PENDING`/`WAITING` 且满足条件的工序重新运行候选筛选；找不到候选
   时保持可观察等待或转人工，不自动标成功/失败。
4. `RUNNING` 工序禁止通过 scheduler 改派；启动动作失败沿用明确失败/停止
   语义。

**落点：** `domain/operation_runtime.py` 增加显式 revoke/reselect 生命周期；
`domain/tasks.py`/`application/coordinator.py` 撤销未启动任务队列投影并接回
`domain/scheduling.py`；测试扩展 `tests/test_v1_operation_runtime.py`、
`tests/test_v1_coordinator_scheduler.py`。

**通过门槛：**纯测试覆盖合法改派、启动后拒绝、未知设备/不在 allowlist、
资源冲突、无可行替代、重复事件不重复 claim；focused 仿真只验证一个选定
arm/AGV 工序及其物料提交，并跑既有 `full_flow` 对照。

## WP4：单一故障场景的安全停止与人工恢复边界

**前置：**WP0 已选定故障；故障发生时 executor 能安全停下，并能读取物料
pose、attachment、速度、设备锁存和资源 owner；不能确认现场状态时只允许
停机。

1. 将故障分类为“未启动”（走 WP3）或“执行中”；执行中必须先由 executor
   停止并报告现场证据。
2. Coordinator 校验物料 UID/区域/attachment、活动执行、设备安全锁存和
   资源 owner，把核验结果写入事件和实例 evidence。
3. 任一项不一致或未知：保留物料与执行证据，标记 `BLOCKED`/`FAILED` 并
   停止相关作业；不得用通用 freeze 清理代替物理所有权判断，也不得提前
   释放仍被现场占用的资源。
4. 第一轮人工确认后仍可终止为 `FAILED` 并等待人工处置；不要求自动
   checkpoint resume、重试、返工或现场复位。

**落点：** `simulation/execution/` 报告字段、`application/coordinator.py`
故障收敛；复用 `OperationRuntime.mark_blocked/mark_failed`，必要时扩展窄
状态迁移；测试放在相应 `tests/test_v1_execution_*.py`、
`tests/test_v1_coordinator_*.py`。

**通过门槛：**覆盖安全停止成功、现场核验失败、仿真超时、重复故障报告；
证明无重复 UID 消费/产出、无重复 FINAL、资源 owner 与现场占用相符。

## WP5：有边界的恢复策略（条件包）

只有 WP4 证明某个动作可在 checkpoint 安全续作、且业务确实要求自动恢复时
才实施。每个工步单独定义 checkpoint、幂等条件、可验证现场条件、最大尝试
数和人工出口；恢复必须建立显式迁移与 execution ID 关联，不得把
`mark_resumed()` 当作未经核验的万能入口。返工必须创建新的
`OperationInstance` 并链接原失败工序。

**通过门槛：**恢复/拒绝路径均可由事件重放审计；没有这类工步或业务需求时
跳过。

## WP6：逐项引入规则调度代价

**前置：**WP1 指标在固定样本上可重算、WP2 状态数据可用、已有明确业务目标。

每轮只增加一个因素，推荐顺序：静态优先级 → waiting aging → due time →
可测的设备队列/处理时长 → 有状态定义的 setup 代价 → 已验证的路线/WIP
代价。同一订单集合、seed、工艺候选和工件上比较，报告完工数、逾期、等待、
WIP、重派次数和决策明细；每次变更提升策略版本。只有规则策略在固定样本上
持续无法满足目标，才单独评估 CP-SAT/MILP；求解器不能绕过 coordinator 校验。

**通过门槛：**`tests/test_v1_scheduling.py` 覆盖 tie-break、老化边界和不可行
候选；coordinator 测试核对事件策略版本与真实派工一致；没有实测收益时保留
原规则。

## 跨包门槛与不做项

- 每个工作包独立合并、独立验收；未通过前置门槛不进入依赖包。
- 真实 Arm/AGV 运动故障、ZMQ/step 断连仍按 GLOBAL 处理，直到逐项 focused
  验收入口建立。
- 不做自动现场复位、无上限重试、任意 recipe Python hook、在线学习、多 AGV
  或全局最优调度。
- 不通过放宽安全阈值、瞬移物料或直接改账本来“恢复”。
- 新增设备/路线先做 focused IK、碰撞、路径、负载、结果验证，再开放给
  调度器。