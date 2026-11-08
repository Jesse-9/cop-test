---
title: 自建订单 DAG 阶段 1–3 实施报告 — 契约、失败隔离与服务化
type: report
status: review
created: 2026-11-06
updated: 2026-11-06
scope: v1 UFLOW / order DAG / failure policy / order service
related:
  - ../plans/v1-self-built-order-dag-orchestration.md
  - ../plans/v1-order-dag-phase-01-foundation.md
  - ../plans/v1-order-dag-phase-02-failure-isolation.md
  - ../plans/v1-order-dag-phase-03-api-persistence.md
  - v1-stage-03-operation-dag.md
  - v1-stage-04-capability-scheduling.md
  - v1-run-record-persistence.md
---

# 自建订单 DAG 阶段 1–3 实施报告

本报告记录
[自建订单与工序 DAG 总体方案](../plans/v1-self-built-order-dag-orchestration.md)
阶段 1–3 在 `src/flexible_factory/` 上的实施结果。范围是声明式订单契约、
结构校验与编译、局部失败隔离、版本化持久化、订单应用服务和本地 HTTP API。

结论：阶段 1–3 的纯逻辑/API/记录型执行测试已完成，全量测试
**576 passed / 3 skipped**（2026-11-06，`uv run pytest -q`）。订单路径**尚未**
在真实 CoppeliaSim 上验收；阶段 4 planner、阶段 5 前端和阶段 6 实机/故障
验收未实施。本文不把记录型 executor 结果当作物理可行性或 NODE 故障准入依据。

> 后续状态（2026-11-07）：阶段 4 planner 已另行实施，见
> [阶段 4 实施报告](v1-order-dag-phase-04-auto-planner.md)；本报告结论仍只
> 覆盖阶段 1–3，阶段 5–6 仍未实施。

## 1. 交付概览

| 阶段 | 交付 | 状态 |
|---|---|---|
| 1 契约/目录/编译器 | `OrderSpec`、端口 identity、validator、compiler、preview、确定性 digest、`full_flow` 等价模板订单 | 完成（纯逻辑） |
| 2 失败隔离 | NODE/GLOBAL 分类、依赖后继阻断、`CANCELLED`、订单收敛状态、协调器分流、未知错误默认 GLOBAL | 完成（纯逻辑/记录型 executor） |
| 3 API/持久化 | 不可变订单版本、订单运行关联、`OrderService`、单实例 worker、本地 HTTP API、CLI 命令、订单图 coordinator 执行 | 完成（纯逻辑/API/记录型执行） |
| 4–6 | 自主 planner、前端、真实 CoppeliaSim 订单/故障验收 | 未实施 |

## 2. 阶段 1：契约、目录与编译器

### 2.1 目录元数据

`domain/process_model.py` 增量增加订单编译所需信息，不复制目录：

- `MaterialPort.port_id`：输入端口稳定 identity；留空按 `input-{ordinal}`
  派生。`kit_join` 使用 `housing-input`/`bracket-input`。
- `OperationTemplate.orchestratable/scene_id/output_port_id/join_handler_id`：
  编排 allowlist、场景范围、输出端口和已登记 join handler。
- `ZoneSpec.spawnable`：订单入口可生成来料的区域；默认目录仅
  `line_s1a/line_s1b/line_s2d` 为真。
- `JOIN_HANDLERS` 登记 `e_material_pair`；`kit_join` 绑定该 ID。
- `PROCESS_MODEL_VERSION` → `v1_process_20261106a`。

目录 schema 由 Alembic `0004_order_catalog_metadata` 扩展；importer/
repository 已同步写入/读取这些字段，`catalog diff` 仍要求代码/DB 逐字段
等价。旧 release 必须在目录版本升级后重新导入/发布。

### 2.2 订单契约

`domain/orders.py`：

- `OrderSpec/OrderTarget/OrderInput/OrderNode/OrderEdge/OrderRunContext`；
- `OrderSpec.from_dict()` 严格拒绝未知字段、代码字段和句柄字段；
- `canonical_dict()/canonical_json()/dag_digest()` 使用稳定排序和 SHA-256，
  与输入容器顺序无关；
- 稳定 `node_id` 与运行期 `instance_id` 分离。

`domain/order_validation.py` 只做订单层校验：唯一 ID、模板 allowlist、
执行器/工步/完成校验/join handler、边端点/端口/物料连续性、每端口唯一
供给、多前驱 AND 或已登记 join、环、入口可达、目标终端与悬空节点。诊断
带 `ORDER_*` code 和 node/edge/port/input 定位；目录规则不在此复制。

`domain/order_compiler.py`：

- 输入按 `input_id` 排序分配 UID；多 UID 生成多条实例链；
- 非逻辑节点 `instance_id = run_id:node_id:uid`；逻辑 join 使用输入 UID
  元组；`task_id` 含 `node_id`，允许同一模板重复使用；
- join 按端口 ordinal 配对，各端口实例数不等时编译诊断；
- 逻辑 join 输出分配新 UID，绑定真实父实例和物料 UID；
- 目标终端实例数必须等于 `target.quantity`；
- `preview()` 返回拓扑序、入口/终点、节点端口/设备/资源摘要、预期产物
  路径、digest 和 diagnostics，不启动仿真/不申请资源。

### 2.3 运行时订单模式

`domain/operation_runtime.py`：

- `OperationGraph(order=...)` 与 recipe 模式互斥；订单模式校验模板
  allowlist、`node_id`、端口绑定和环，不要求/不伪造 `selected_route`；
- `OperationInstance` 增加 `node_id/order_id/order_version/
  predecessor_ports/failure_code/blocked_by`；
- `OperationRuntime.load_order_instances()` 装载编译器实例；
- 两种模式共用 ready/commit 生命周期和调度接口。

`application/coordinator.py` 增加订单模式：按 `material_plans` 生成入口
来料、不调用固定批次 `_poll_sources`、不调用 recipe `_check_join`；逻辑
join 由 `_check_order_joins()` 按编译父 UID 调
`MaterialRegistry.commit_join_uids()`，不重新配对。

## 3. 阶段 2：局部失败隔离

`domain/failures.py` 定义 `FailureScope/FailureRecord` 和保守分类表。首版
NODE allowlist 只有 `NODE_PRECHECK_REJECTED`，且必须同时满足：物理动作前、
执行器已停止、无安全锁存、物料仍在输入区域且类型/账本未变、资源已完整
释放。任何不确定输入、安全锁存、仿真 step/连接、场景完整性、物料/账本
不一致、共享 AGV/资源、未知异常和未准入 executor 均 GLOBAL。

运行时新增：

- `OperationRuntime.propagate_failure()`：源节点 FAILED，沿真实后继把依赖
  节点置 BLOCKED，保留 `blocked_by` 证据；独立分支不变；
- `cancel_unreachable()`：收敛时把输入不可达的失败阻断节点置 CANCELLED；
- `progress_state()`：`RUNNING/SUCCEEDED/PARTIAL_FAILED/FAILED/ABORTED`；
- `failure_summary()`：失败节点、阻断节点、继续分支。

协调器新增：

- `ArmExecutor/AgvExecutor` 报告结构化 `failure_code`；
- `_handle_unit_failure()` 按 code + 安全确认分流；
- NODE 路径停止本执行、释放资源、阻断后继、记录事件并继续 tick；
- GLOBAL 路径记录结构化失败并沿用全局 freeze/abort；
- `sim.step()`、poll、未知协调器异常均收敛为 GLOBAL；
- 事件/故障记录包含 `failure_scope`、`node_id`、阻断节点、继续分支、
  资源释放结果和订单终态。

CLI/pytest 不包含真实运动故障；NODE allowlist 的物理准入仍需阶段 6
focused CoppeliaSim 证据。

### 3.1 失败入口与范围

| 入口 | 结构化 code | 首版范围 | 说明 |
|---|---|---|---|
| 臂执行器 | `SAFETY_LATCH` | GLOBAL | 安全锁存优先于其他 code；保留现场 |
| 臂执行器 | `UNIT_CONTROLLER_FAILURE` | GLOBAL | 动作后失败未证明可隔离 |
| 单元完成校验 | `NODE_VERIFICATION_FAILED` | GLOBAL | 物理动作已发生 |
| AGV 航线/阻塞超时 | `AGV_ROUTE_FAILED` | GLOBAL | 共享 AGV/通道影响未排除 |
| 物料/账本提交 | `PROCESS_FAILED`/`ZONE_COMMIT_FAILED`/`LEDGER_INCONSISTENT` | GLOBAL | 账本可能部分提交 |
| 资源申请/释放 | `RESOURCE_RELEASE_DENIED` | GLOBAL | 共享资源所有权不确定 |
| 仿真 step/连接/场景 | `SIM_STEP_FAILED`/`SIM_CONNECTION_LOST`/`SCENE_INTEGRITY_LOST` | GLOBAL | 基础设施故障 |
| 超时 | `TASK_TIMEOUT`/`BACKPRESSURE_TIMEOUT` | GLOBAL | 现场状态需人工确认 |
| 未分类执行器异常 | `UNKNOWN_EXECUTOR_FAILURE` | GLOBAL | 默认保守 |
| 未分类协调器异常 | `UNKNOWN_FAILURE` | GLOBAL | 默认保守 |
| 节点前置条件拒绝 | `NODE_PRECHECK_REJECTED` | NODE（条件） | 仅物理动作前 + 安全确认 |

未列入上表的旧 recipe/AGV 冻结入口（`SOURCE_SPAWN_FAILED`、
`OPERATION_GRAPH_FAILED`、`SCHEDULE_REJECTED`、`JOIN_BIND_FAILED`、
`FINAL_SPAWN_FAILED`、控制器/航线构建失败和注入停带等）仍直接调用
`_freeze()`，按 GLOBAL 处理，不进入 NODE allowlist；它们尚未全部改写为
结构化 `FailureRecord`，但不会被误判为局部失败。

### 3.2 状态迁移

| 对象 | 迁移 | 触发 |
|---|---|---|
| 工序实例 | PENDING/WAITING/RUNNING → FAILED | NODE 源节点或 GLOBAL 失败 |
| 工序实例 | PENDING/WAITING/RUNNING/BLOCKED → BLOCKED | 失败源节点的传递后继 |
| 工序实例 | BLOCKED → CANCELLED | 收敛时输入不可达且 `blocked_by` 非空 |
| 工序实例 | RUNNING → DONE | 执行 + 应用校验 + 物料提交成功 |
| 订单运行 | STARTING → RUNNING → SUCCEEDED/PARTIAL_FAILED/FAILED/ABORTED | worker 启动/终态 |
| 订单运行 | STARTING/RUNNING → ABORTED | 外部停止/worker 缺失/拒绝 |

### 3.3 失败事件字段

`failure_scope`、`node_id`、`failure_code`、`message`、`safe_state`
（执行器停止/物料区域与状态/账本未变/资源释放）、`blocked_nodes`、
`continued_nodes`、`order_status`。`FAILURE`/`FAILURE_ISOLATED`/`TASK_FAILED`
和 `FREEZE` 事件都带范围与定位；`RunRecorder` 将 `detail` 写入
`run_event.detail_json`，订单服务另写 `order_node_run` 快照。

## 4. 阶段 3：订单 API 与版本化持久化

### 4.1 持久化

- `persistence/order_models.py`：`order_header`、`order_version`、
  `order_run`、`order_node_run`。
- `persistence/orders.py`：创建订单/追加版本、读取并校验 digest、创建/
  更新运行关联、节点快照、运行事件查询。
- Alembic `0005_orders`；`order_version` 有 `BEFORE UPDATE` 触发器，
  版本只能追加。
- 订单定义与静态目录 release 分表；`order_run` 通过
  `(order_id, order_version)` 绑定不可变版本，并保存 run_id、artifact、
  digest、终态/失败范围/停止原因。
- 运行终态和节点快照只在 worker 完成时批量写入；`sim.step()` 热路径不写
  SQL。
- 既有 `operation_instance.state` CHECK 仍为 7 状态；`CANCELLED` 在
  `RunRecorder` 中映射为 `BLOCKED`，并在 `evidence_json.cancelled_state`
  标注；订单专用精确状态保存在 `order_node_run.state`。

### 4.2 应用服务与 worker

`application/orders.py`：

- `OrderService` 提供模板目录、预览、创建订单/版本、启动、状态、节点、
  事件、停止、运行列表；
- 启动前重新编译并校验；运行绑定显式版本、目录版本和具体 artifact；
- `current` 引用必须由 artifact resolver 解析为具体 ID；
- `OrderSimulationWorker` 单实例队列，同一时刻只接受一个运行；runner 异常
  收敛为 FAILED 并持久化；
- worker 提供 live snapshot；终态回调批量写订单运行/节点。

`application/order_runner.py`：

- 只在 worker 线程内 connect ZMQ、load/prepare_start、build components、
  start stepping、构造 `Stage4Coordinator(order_plan=...)`、循环 step；
- stop_event 在 stepping 边界转成受控 `STOP_REQUESTED` freeze；
- 结束时 stop simulation、destroy runtime，可选写 `RunRecorder` 和 JSON
  报告；
- HTTP handler 不持有 sim/scene/controller/handle。

### 4.3 HTTP API 与 CLI

`api/server.py` 使用标准库 `ThreadingHTTPServer`，默认绑定 `127.0.0.1`，
只做 JSON/路由/错误映射；领域诊断 code 直接进入错误响应。接口覆盖模板、
订单/版本、预览、启动、运行状态/节点/事件、停止。首期没有认证/TLS，文档
明确不得直接暴露到非本机网络。

CLI 新增 `order validate/create/version/list/show/preview/start/status/
nodes/events/stop/runs` 和 `api serve`。`order start` 在当前进程同步运行；
`api serve` 使用 worker 异步运行。

## 5. 测试与验证

全量纯逻辑回归：`uv run pytest -q` → **576 passed, 3 skipped**。

新增测试：

| 文件 | 覆盖 |
|---|---|
| `tests/test_v1_order_foundation.py` | 契约严格解析、digest 顺序无关、端口/allowlist、编译正例/多 UID/join、preview、参数化结构负例、订单 runtime 模式 |
| `tests/test_v1_order_execution.py` | 编译订单在记录型 coordinator 上完成输入→派工→AGV→E join→FINAL，资源清零 |
| `tests/test_v1_order_failure_isolation.py` | NODE 仅阻断依赖后继、独立分支继续、join 阻断、PARTIAL_FAILED/FAILED、安全锁存/step/poll/未知异常 GLOBAL |
| `tests/test_v1_order_persistence.py` | 版本追加、数据库触发器拒绝 UPDATE、digest 读取校验、运行/节点关联 |
| `tests/test_v1_order_service.py` | service/worker 成功/冲突/停止/失败持久化、HTTP API 契约、未知订单/版本 404、非法 JSON 422 |
| `tests/order_fixtures.py` | `full_flow` 直达 A + 支架 D 的可编译模板订单 |

已有目录/运行记录测试同步更新：目录版本、迁移 head `0005_orders`、新增
表集合。目录持久化往返测试仍要求代码/DB 目录逐字段相等。

未执行（阶段 1–3 时点；阶段 6 已补充真实仿真验收，见
[阶段 6 报告](v1-order-dag-phase-06-simulation-acceptance.md)）：

- 真实 CoppeliaSim 订单运行、局部失败 focused case、全局故障 focused case；
- 阶段 4 planner、阶段 5 前端；
- 任何 NODE 真实 executor/controller 失败的物理准入。

## 6. 已知限制与后续门槛

- 订单创建/版本保存只做 JSON 字段校验；DAG 结构在 preview/start 时拒绝。
  这是为了允许 planner/前端保存不完整草稿；运行入口不会跳过编译校验。
- 旧 recipe/AGV/冻结路径中仍有直接 `_freeze(reason)` 的 GLOBAL 入口，未全部
  改为结构化 `FailureRecord`；它们仍按 GLOBAL 处理，不会被误判为 NODE。
- NODE allowlist 只有 `NODE_PRECHECK_REJECTED`；真实 ArmExecutor/
  AgvExecutor 目前不会产生该 code，因此该路径只由纯逻辑/记录型 executor
  验证。阶段 6 必须先让具体执行器产生结构化 code，并完成 focused
  CoppeliaSim 安全证据，才可把真实失败纳入 NODE；在此之前真实 executor
  失败仍全部 GLOBAL。
- API 无认证/TLS、单 worker/单活动运行；多订单并行、跨进程队列和访问控制
  未实现。
- 订单版本已记录目录版本；切换目录版本启动会被拒绝。旧订单版本目录字段
  为空时允许当前目录启动（兼容首次迁移）。
- 目录 `v1_process_20261106a` 与旧 release 不兼容；需重新导入/发布。
- `full_flow` 的 RAW_B 绕行分支是同一 `housing_supply` 互斥组中的另一选择；
  当前 fixture 只表达直达 A + 支架 D 的可编译订单，不把未配对 SEMI_CE
  建模为悬空分支。

## 7. 主要代码落点

| 模块 | 内容 |
|---|---|
| `domain/orders.py` | 订单契约、严格解析、规范化序列化、digest、运行上下文 |
| `domain/order_validation.py` | 订单结构校验与定位诊断 |
| `domain/order_compiler.py` | 实例化、UID/端口/join 绑定、preview |
| `domain/process_model.py` / `default_process.py` | 端口 identity、编排元数据、join handler、可生成来料区域 |
| `domain/operation_runtime.py` | 订单图模式、失败传播、CANCELLED、收敛状态 |
| `domain/failures.py` | NODE/GLOBAL 分类表与结构化 FailureRecord |
| `domain/materials.py` | `commit_join_uids()` 按显式父 UID 配对 |
| `application/coordinator.py` | 订单源生成/join、NODE 局部隔离、GLOBAL 分流 |
| `application/orders.py` | OrderService、OrderSimulationWorker、终态持久化 |
| `application/order_runner.py` | 真实 CoppeliaSim worker runner |
| `api/server.py` | 本地 HTTP 适配器 |
| `persistence/order_models.py` / `orders.py` | 订单/版本/运行/节点仓储 |
| `persistence/migrations/versions/0004_order_catalog_metadata.py` | 目录编排元数据 |
| `persistence/migrations/versions/0005_orders.py` | 订单与运行关联 schema |
| `tests/test_v1_order_*.py` | 纯逻辑/API/记录型执行测试 |
