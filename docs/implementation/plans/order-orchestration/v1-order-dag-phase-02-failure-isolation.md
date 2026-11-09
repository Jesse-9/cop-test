---
title: 自建订单 DAG 阶段 2 — 局部失败隔离与全局故障停止
type: plan
status: implemented
created: 2026-11-06
updated: 2026-11-06
scope: v1 UFLOW / coordinator failure policy
related:
  - v1-self-built-order-dag-orchestration.md
  - v1-order-dag-phase-01-foundation.md
  - ../../reports/production-flow/v1-stage-04-capability-scheduling.md
---

# 自建订单 DAG 阶段 2：局部失败隔离与全局故障停止

## 目标与边界

在订单模型和 DAG runtime 已稳定后，为失败建立明确的影响范围。经验证可安全隔离的工序失败只阻断其依赖后继；其他分支继续。安全、仿真基础设施和未分类故障停止整个仿真。该阶段先完成纯逻辑与记录型 executor 验证，不开放 API、planner 或 UI。

当前 `Stage4Coordinator._freeze()` 会停止 AGV、传送带及所有活动控制器；不能直接复用它处理局部工序失败。首版采用保守 allowlist：只有明确列入、可停止到已知安全状态、物料状态可核实且资源已释放的错误才是 NODE；未知错误一律 GLOBAL。逻辑测试只证明图状态传播，不能单独授权真实运动错误进入 NODE allowlist。

## 具体步骤

1. 盘点 `coordinator.py` 的所有失败入口：arm executor、AGV executor、单元完成校验、物料账本提交、资源申请/释放、超时、停带/阻塞故障、sim step/连接，以及安全锁存。为每类记录来源、当前行为、是否可能影响共享状态和目标范围。
2. 定义结构化故障对象，至少含 `failure_code`、`scope`（`NODE`/`GLOBAL`）、`node_id`、`instance_id`、`device_id`、`sim_time`、安全状态和诊断。错误字符串不作为分类依据。
3. 冻结首版分类表。安全锁存、sim/session/stepping/remote API 故障、场景完整性丢失、物料物理位置/账本状态不一致或不确定、共享执行资源故障和未知异常为 GLOBAL。只有可证明发生在物理动作前的节点前置条件拒绝，或经 focused 仿真证明能停止并核实物料/设备安全状态的具体失败码，才允许 NODE。AGV/shared-resource 失败先按 GLOBAL。
4. 在 `OperationRuntime` 增加失败传播窄接口：设置源节点 `FAILED`，计算并标记所有依赖后继为 `BLOCKED`，阻止其进入 ready/dispatch；保留与该失败节点无依赖路径的实例状态。若 blocked 节点的所有未决输入已不可达，允许运行收敛时标记 `CANCELLED`，并保留原因链。
5. 在 coordinator 建立两个独立处理路径。NODE 路径只停止对应 executor；确认物料位置/账本、执行器安全状态和共享资源影响均满足分类条件后，释放该实例完整资源集，记录 failure event 和 blocked descendants，然后继续 tick/ready dispatch。任一确认失败或证据不足立即升级 GLOBAL。GLOBAL 路径沿用并强化全局 freeze/abort，停止全部活动执行并释放普通资源。
6. 定义订单收敛状态：没有失败且目标满足为 `SUCCEEDED`；存在局部失败且运行仍有有效节点或目标由其他分支满足为 `PARTIAL_FAILED`；失败导致目标不可能满足为 `FAILED`；全局故障中止为 `ABORTED`。终态判断不能只看是否仍有 ready 节点。
7. 补事件字段：`failure_scope`、失败节点、阻断节点列表/原因、继续执行的分支、资源释放结果和订单终态；保持现有运行记录只在边界批量写入，step 热路径不做 SQL。
8. 用记录型 executor 构造并发小图：A→B 与独立 C 并发；A 失败后 B 不启动、C 完成。另测 A 与 B/C 的 AND join，A 失败导致 join blocked，但独立 D 仍执行。
9. 覆盖 global 负例：模拟安全锁存与 step/连接异常，断言全部活动 executor 收到停止/hold，资源无泄漏，未完成节点不误报 DONE。增加未知异常默认 global 的回归。

## 代码与测试落点

- `domain/operation_runtime.py`：失败传播、block 原因和图收敛查询。
- `application/coordinator.py`：失败分类、node-local stop/release、global freeze 分流和终态汇总。
- `simulation/execution/arm.py`、`agv.py`：仅在 coordinator 需要时补充可判别的失败 code/安全状态；执行器仍不调用 `sim.step()`。
- 扩展 `tests/test_v1_operation_runtime.py`、`tests/test_v1_coordinator_dag.py`、`tests/test_v1_coordinator_executors.py` 与必要的安全/资源测试。

测试保持在两组：一张含“失败节点→后继”和“独立并发分支”的图验证 NODE 传播/继续；一组参数化 GLOBAL 输入（安全锁存、step/连接、未知错误）验证全停。资源释放与终态在这两组断言中一起核对，不为每个状态字段另建用例。

物理准入门槛：阶段 2 实现后，先对首个拟开放的 NODE failure code 做一次 CoppeliaSim focused 验收，证明执行停止、物料可定位、账本一致，且独立分支继续时没有干扰。该证据通过前，所有真实 executor/controller 失败仍按 GLOBAL 处理；阶段 6 再验收完整订单链路。

## 验收与交付

- NODE 故障仅影响节点及依赖后继；无依赖的 ready/running 分支正常推进并提交。
- blocked/cancelled 节点不会派工或产生物料提交；订单终态符合目标可达性。
- local 失败执行器停止并达到已知安全状态，普通资源释放；不能确认安全时升级为 GLOBAL。
- 首个 NODE failure code 必须有 focused CoppeliaSim 证据；后续 NODE 类型逐项经过准入，不因纯逻辑测试通过而自动开放。
- GLOBAL、安全锁存、step/连接与未知错误停止所有活动执行；安全锁存不被普通资源释放清除。
- 交付故障分类表、状态迁移表、事件字段、纯逻辑和记录型 executor 测试。

## 进入下一阶段

局部/全局分类和 coordinator 行为通过纯逻辑验收后，订单 API 才可承诺稳定状态语义。阶段 2 的 focused CoppeliaSim 验收只准入首个拟开放的 NODE failure code；未验证的错误保持 global。阶段 6 再对完整订单链路及其故障行为做端到端验收。
