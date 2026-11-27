---
title: 阶段 5 剩余工作 — 设备状态、未启动工序改派与规则调度
type: plan
status: proposed
created: 2026-11-24
updated: 2026-11-25
scope: v1 UFLOW / 业务阶段 5 / WP2、WP3、WP6 / 未实施
related:
  - ../../reports/production-flow/v1-stage-05-finite-recovery.md
  - ../../archive-plans/production-flow/v1-stage-05-finite-recovery-implementation.md
  - ../../../wiki/06-工业案例/柔性车间/当前规划/06-23-混线生产阶段5调度优化与设备状态.md
---

# 阶段 5 剩余工作 — 设备状态、未启动工序改派与规则调度

本方案只保留业务阶段 5 **未实施**的 WP2、WP3、WP6；WP4/WP5 的异常恢复规划已移除。WP0（`cart_2` 不可用业务
用例与冻结基线）和 WP1（订单级调度观测）已实施，见
[阶段 5 实施报告](../../reports/production-flow/v1-stage-05-finite-recovery.md)；
完整原始方案已归档到
[阶段 5 完整方案](../../archive-plans/production-flow/v1-stage-05-finite-recovery-implementation.md)。

业务用例：dual 固定分工下，`cart_2` 在 `transport_DE` 启动前不可用；目标
是只对尚未启动的 `transport_DE` 撤销 `cart_2` 选择并改派给已验收的
`agv_1`。执行中设备停用的处理、轮式物理控制不在首轮范围。

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

**通过门槛：**覆盖可接单→故障/停用→可接单、未知状态阻止派工、过期证据
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
- 真实 Arm/AGV 运动故障、ZMQ/step 断连仍按 GLOBAL 处理；异常恢复不在当前
  规划范围。
- 不做自动现场复位、无上限重试、任意 recipe Python hook、在线学习、多 AGV
  或全局最优调度。
- 不通过放宽安全阈值、瞬移物料或直接改账本来伪造状态。
- 新增设备/路线先做 focused IK、碰撞、路径、负载、结果验证，再开放给
  调度器。