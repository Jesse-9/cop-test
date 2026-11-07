---
title: 自建订单 DAG 阶段 1 — 契约、工序目录与编译器
type: plan
status: implemented
created: 2026-11-06
updated: 2026-11-06
scope: v1 UFLOW / order domain / DAG compiler
related:
  - v1-self-built-order-dag-orchestration.md
  - ../reports/v1-stage-03-operation-dag.md
  - ../reports/v1-stage-04-capability-scheduling.md
---

# 自建订单 DAG 阶段 1：契约、工序目录与编译器

## 目标与边界

建立前端手工编排和后端自主规划共同使用的订单数据契约，并把声明式订单 DAG 编译为现有运行时可消费的图。本阶段不实现 HTTP API、数据库迁移、planner、前端或 CoppeliaSim 控制改造。

入口为现有 `ProcessCatalog` 与已验证 `L5_UFLOW` 执行能力；输出为不可变订单版本数据、编译诊断和 `OperationGraph`。不允许订单数据携带 Python/Lua 代码、CoppeliaSim handle 或未登记执行器。

## 具体步骤

1. 以当前 `full_flow` evidence 为基线，列出首期允许的目标物料、输入物料、工序模板、来源区域、目标区域、执行器、工步和完成校验器。为每个可编排模板建立明确 allowlist。
2. 定义 `OrderSpec`、订单版本、输入批次、目标产物、节点、边、端口连接和编排来源的数据类。稳定的 `node_id` 与运行期 `instance_id` 分离；instance ID 使用 `run_id + node_id + material_uid`（逻辑节点使用其输入 UID 元组）构造，允许同一物料在订单中重复使用同一 template；定义规范化序列化和确定性 `dag_digest`。
3. 定义状态与语义：订单版本在运行启动后不可变；边表达真实的物料/工艺前置关系；每条输入端口有明确供给者；多前驱节点必须声明 AND/已登记 join 语义；不从无依赖关系推导隐式依赖。
4. 增补工序目录元数据，只扩展编译所需信息，例如可编排标志、场景范围、端口身份、允许连接关系、目标物料转换与 join handler ID。沿用 `WORK_STEP_TEMPLATES`、`COMPLETION_VERIFIERS`、设备、资源、区域目录，不复制目录到新模块。
5. 明确并实现 `OperationRuntime` 的订单图模式：保留现有 recipe/route 校验供旧入口使用；订单模式按编译后的节点和边校验模板 allowlist、端口/物料连续性、环和前驱状态，不伪造 `selected_route` 或把节点塞入静态 `RouteVariant`。两种模式共用 `OperationGraph`、ready/commit 生命周期和调度接口。
6. 实现纯 Python `OrderValidator`：检查版本字段、唯一 node ID、节点和模板存在性、边端点、端口方向、环路、入口投料、输出到输入的类型/区域兼容、必需前驱、目标可达及执行器/完成校验 allowlist。目录级静态校验继续由 `ProcessCatalog` 负责；订单校验不复制目录规则。诊断返回稳定 code 和 node/edge/port 定位。
7. 实现纯 Python `OrderDagCompiler`：按订单输入数量实例化物料 UID 上下文，将节点/边解析为运行时实例及前驱索引；同一输入的多 UID 规则明确；逻辑 join 绑定真实父实例和物料 UID，不把单纯 AND 边误当物料合流。
8. 增加 compile preview：返回拓扑序、入口和终点、节点模板/端口/设备资源摘要、预期产物路径、digest 和 diagnostics；不启动仿真、不申请资源、不改变生命周期状态。
9. 用当前 `full_flow` 构造等价模板订单 fixture，证明它能编译出等价工序依赖和物料谱系；保留当前运行入口作为迁移期间基线。

## 代码与测试落点

- 优先新增 `domain/orders.py` 或同层领域模块；如职责更适合拆分，可将 validator/compiler 分文件，但保持 `domain` 不依赖 application、simulation、persistence 或 SQLAlchemy。
- 在 `domain/process_model.py` 与 `domain/default_process.py` 增量补目录字段和默认模板声明。
- 在 `domain/operation_runtime.py` 增加订单图装载/验证窄接口；recipe route 校验只用于旧入口，订单节点身份包含 `node_id` 以避免同 UID 重复模板冲突；编译器不直接篡改生命周期状态。
- 扩展 `tests/test_v1_process_model.py`、`tests/test_v1_operation_runtime.py`，新增订单模型/compiler 测试。

测试只保留三类：一个模板订单编译正例（含多 UID 和 join）、一个确定性/digest 检查、一个参数化结构负例集合。已有目录校验覆盖的行为不在 compiler 测试中重复镜像。

## 验收与交付

- 正例：当前模板订单能生成确定性 digest 和可解释拓扑；不同数量输入能生成正确 UID 数量及实例映射；E join 使用显式 handler。
- 参数化负例：未知模板/端口、重复 ID、孤立或悬空节点、环、物料类型/区域不兼容、入口不足、目标不可达及未登记 executor 均返回定位诊断。
- 同一订单版本在不同输入容器顺序下编译结果一致；compiler 不导入 CoppeliaSim/application/persistence。
- 交付领域契约、validator/compiler、序列化/digest 规则、纯逻辑测试和更新后的模板订单 fixture。

## 进入下一阶段

本阶段纯逻辑测试通过、`full_flow` 模板图语义对照完成，订单结构/端口/join 规则冻结后，才进入阶段 2 的失败隔离。持久化 schema 在阶段 3 根据冻结契约落地。
