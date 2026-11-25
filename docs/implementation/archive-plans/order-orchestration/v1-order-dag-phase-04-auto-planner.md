---
title: 自建订单 DAG 阶段 4 — 后端自主编排器
type: plan
status: implemented
created: 2026-11-06
updated: 2026-11-07
scope: v1 UFLOW / deterministic order planner
related:
  - v1-self-built-order-dag-orchestration.md
  - v1-order-dag-phase-01-foundation.md
  - v1-order-dag-phase-03-api-persistence.md
---

# 自建订单 DAG 阶段 4：后端自主编排器

## 目标与边界

根据目标物料、数量、可用投料、场景与用户偏好，确定性地选择已登记工序模板并生成订单 DAG。planner 只产出订单草稿，不申请资源、不操作 CoppeliaSim、不绕过 compiler，也不承诺静态图一定物理可行。首版不引入全局最优 solver、随机 LLM 生成或跨订单优化。

## 具体步骤

1. 冻结 planner 输入：目标物料/数量、可用输入及数量上限、scene ID、允许的 template/device 范围、路线偏好、是否允许局部失败风险较高的候选（首版默认否）和策略版本。
2. 将可用转换建成有限、确定性的工艺搜索空间：节点只能来自阶段 1 allowlist；边只能来自登记的端口兼容/区域连接规则；join 只能调用已登记 handler；每个候选路线保留 template 和规则来源。
3. 从目标反向求候选生产路径，再校验输入供应与多输入合流；按稳定键排序候选，设置明确的搜索深度/节点数上限，防止输入导致无界搜索。
4. 定义首版选择目标与 tie-break，例如先满足目标可达和输入限制，再最少工序/优先偏好，最后按稳定 template ID 排序。把未建模维度列入结果，不暗示设备健康、节拍或几何能力可被 planner 推断。
5. 把选中路径编译为阶段 1 的 `OrderSpec`，调用同一 validator/compiler；任何编译失败都作为 planner 缺陷或无可行解返回，不静默修补图。
6. 通过阶段 3 order service 保存为 `source=planner` 的草稿版本，记录 `planner_policy_version`、输入摘要、候选数量、选择理由和拒绝原因。用户确认后再启动仿真。
7. 提供 `explain` 结果：选中节点/边、目标物料路径、投料消耗、join 绑定、未选候选及排除原因；无解时返回最先阻断的约束类别。
8. 用固定样例建立 golden tests：当前 `full_flow` 可表达的目标、RAW 不足、目标不可达、多个等价模板稳定 tie-break、join 物料不匹配、搜索上限触发。

## 主要落点

- `application/planner.py` 作为应用层服务；纯搜索规则如无 I/O 可放 `domain/`，且不依赖 coordinator/simulation。
- 输入/输出版本与 `spec.py` 版本常量或订单记录中的策略版本绑定。
- planner 只调用 OrderValidator/Compiler 和 order service，不直接写 ORM、MaterialRegistry 或 executor。
- 新增 planner 纯逻辑测试和 API 集成测试，验证规划结果确实保存为普通订单草稿。

planner 测试收敛为三组：可行目标 golden case、参数化无解/约束 case、等价候选稳定 tie-break；compiler 规则由阶段 1 测试负责，不在 planner 层重复穷举。

## 验收与交付

- 相同 planner 版本和输入始终产生相同 DAG digest 与 explain 输出。
- 每个可行结果都通过 validator/compiler；无解原因稳定、可解释。
- planner 不使用未登记工序、设备替代、代码执行或未验证场景路径。
- 交付策略说明、有限搜索界限、版本字段、候选解释和测试用例。

## 进入下一阶段

planner 输出可保存、可预览且接口语义稳定后，前端可增加“自主生成”入口；前端不得内置另一套规划规则。
