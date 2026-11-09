---
title: 自建订单 DAG 阶段 4 实施报告 — 有界确定性自主编排器
type: report
status: review
created: 2026-11-07
updated: 2026-11-07
scope: v1 UFLOW / order planner
related:
  - ../../plans/order-orchestration/v1-self-built-order-dag-orchestration.md
  - ../../plans/order-orchestration/v1-order-dag-phase-01-foundation.md
  - ../../plans/order-orchestration/v1-order-dag-phase-03-api-persistence.md
  - ../../plans/order-orchestration/v1-order-dag-phase-04-auto-planner.md
  - v1-order-dag-phase-01-03.md
---

# 自建订单 DAG 阶段 4 实施报告

本报告记录
[阶段 4 自主编排方案](../../plans/order-orchestration/v1-order-dag-phase-04-auto-planner.md) 的实施结果。
范围是确定性、有界的订单草稿 planner，以及通过阶段 3 订单服务保存 planner
草稿、API/CLI 查询和解释。

结论：阶段 4 已实施并通过纯逻辑/API 测试。全量回归
**598 passed / 3 skipped**（2026-11-07，`uv run pytest -q`）。planner 不启动
CoppeliaSim、不申请资源、不写 ORM；草稿通过 `OrderService.plan_and_save()`
保存为普通不可变订单版本。真实订单运行和物理可行性仍由阶段 6 验收。

## 1. 交付概览

| 交付 | 内容 |
|---|---|
| 输入契约 | `PlannerInput`/`PlannerSupply`，目标物料/数量、可用投料、场景、允许模板/设备、路线偏好、搜索上限、策略版本 |
| 搜索空间 | 目录中已登记 `RouteVariant` 的 route macro + 已登记 `join_handler_id` 的 join macro；不把单工序端口兼容性当作任意拼接边 |
| 反向规划 | 从目标物料递归求生产者；route 作为基础分支，join 组合各输入端口的生产者；`max_depth`/`max_nodes`/`max_candidates` 限制 |
| 选择 | 先满足目标可达/投料充足/validator/compiler；再按路线偏好、最少工序、稳定签名排序；无解返回稳定阻断类别 |
| 编译门禁 | 每个候选都调用阶段 1 `OrderDagCompiler.preview()`；失败作为 `PLANNER_COMPILE_FAILED`，不静默修补 |
| 解释 | 选中节点/边、目标路径、投料消耗、join 绑定、候选与拒绝原因、搜索统计、未建模维度 |
| 持久化 | Alembic `0006_order_planner_metadata`；订单版本记录 `planner_policy_version`、输入摘要、explain、候选数 |
| 服务/API/CLI | `OrderService.plan/plan_and_save`；`POST /api/planner/plan`、`POST /api/planner/drafts`；`flexible-factory order plan [--save]` |
| 版本 | `spec.ORDER_PLANNER_POLICY_VERSION = "v1_planner_20261107"` |

## 2. 搜索与选择规则

### 2.1 有限搜索空间

- **route macro**：只接受目录中已存在的 `RouteVariant` 线性工序链。首工序
  必须有一个与 `entry_zone` 匹配的输入端口；末工序输出必须与 `exit_zone`
  匹配；整条链端口连续。这样不会出现“跳过 AGV 运输工序、仅靠端口兼容
  直接拼接”的物理不可信图。
- **join macro**：只接受 `executor_kind=logic`、`orchestratable=True`、
  `join_handler_id` 已登记且位于 `RUNTIME_JOIN_HANDLERS`（当前只有
  `e_material_pair`）、输出存在且至少两个输入端口的有序合流。首期默认工艺
  只有 `kit_join`（`SEMI_CE + PART_DE -> FINAL`）。
- **过滤**：`allowed_template_ids`、`allowed_device_ids`、`scene_id` 和
  template `orchestratable` 共同过滤；被排除的路线/合流不会进入候选。
- **周期防护**：递归时记录正在展开的物料类型；同一物料类型不在同一分支
  重复展开，配合 `max_depth` 防止无界递归。

### 2.2 反向生产

`_producer_options(item_type, zone, depth)`：

1. 收集所有输出物料类型/区域匹配的 route 分支；
2. 收集所有输出匹配的 join 分支；对 join 的每个输入端口递归求生产者；
3. 对输入端口的生产者列表做笛卡尔积，合并投料需求、节点数、工序数和
   稳定签名；
4. 去重、按 `(工序数, 签名)` 稳定排序，并截断到 `max_candidates`。

目标 FINAL、投料 RAW_A + RAW_D 的默认结果：

```text
kit_join
  housing-input <- housing_direct route
    housing_direct_A -> housing_finish -> housing_to_E_buffer
  bracket-input <- bracket_route
    bracket_process -> transport_DE -> bracket_to_E
```

如果投料是 RAW_B + RAW_D，壳体分支改为 `housing_alt` 路线（含
`transport_BA`）。如果目标是中间物料（如 `SEMI_CE`），planner 可以选择
route-only 草稿，不强行添加 join。

### 2.3 投料与选择

- 每条 route 分支消耗 1 个入口物料；按目标数量 N 乘以需求；join 分支合并
  所有子分支的投料需求。
- 投料按 `(item_type, zone)` 分配；`zone` 为空的 supply 可用于该物料类型
  的任意入口区域。分配顺序和输入摘要均稳定排序。
- 候选评分：
  1. `preferred_route_ids` 排名（未列出为末位）；
  2. 每单位工序数（最少优先）；
  3. 稳定签名（route/join ID 和工序 ID 元组）。
- 目标可达、投料充足且通过 validator/compiler 的候选才进入可行集合；
  默认选取第一个可行候选。

### 2.4 无解与搜索上限

无解诊断按稳定类别排序，`first_blocking_category` 返回最先阻断类别：

```text
policy > scene > target > template > device > join > input > search_limit > compile
```

如果搜索因 `max_depth`/`max_nodes`/`max_candidates` 截断，优先返回
`search_limit`，避免把“搜索不完整”误报为“目标不可达”。

## 3. 草稿生成与编译

- planner 依据选中分支和 `target_quantity` 物化 `OrderSpec`：
  - route 链生成节点和边，首工序绑定订单入口 `OrderInput`；
  - join 节点按端口绑定各子分支终端节点；
  - 节点 ID 由 template ID + 稳定序号构成，`task_id`/instance ID 由阶段 1
    编译器生成；
  - `source="planner"`。
- 每个候选都调用 `OrderDagCompiler.preview()`；preview 不通过时不返回
  `OrderSpec`，并记录 `PLANNER_COMPILE_FAILED`。
- 输出 `PlannerResult` 包含 `order`、`digest`、`preview`、可行候选、
  被拒候选、explain 和 search 统计。

## 4. 持久化与服务

### 4.1 订单版本元数据

Alembic `0006_order_planner_metadata` 给 `order_version` 增加：

- `planner_policy_version`；
- `planner_input_json`；
- `planner_explain_json`；
- `planner_candidate_count`。

这些字段只用于审计/复现，不参与运行时订单图。planner 草稿仍是普通不可变
订单版本；`source="planner"`，运行入口与手工订单完全相同。

### 4.2 OrderService

- `OrderService.plan(payload)`：解析 `PlannerInput`，只读编排，不写库。
- `OrderService.plan_and_save(payload, note="")`：
  - 无解时返回 `ok=false`，不创建订单/版本；
  - 有解时新订单写版本 1；已有订单追加下一版本；
  - 记录 planner 元数据并返回 `saved_order.order_id/version/dag_digest`；
  - 已有订单追加版本时版本号变化会改变 digest，响应同时返回
    `saved_order.plan_digest`（planner 草稿 digest）和
    `saved_order.dag_digest`（实际保存版本 digest），调用方不得混用。
- planner 不能直接写 ORM、MaterialRegistry 或 executor；所有保存通过
  order service/repository。

### 4.3 API 与 CLI

```text
POST /api/planner/plan     只规划, 返回 explain; 无解也返回 200 + plan.ok=false
POST /api/planner/drafts   规划并保存草稿; 无解返回 422 PLANNER_NO_SOLUTION
GET  /api/health           增加 planner_policy_version
```

```powershell
flexible-factory order plan --file planner.json
flexible-factory order plan --file planner.json --save --note "planner golden"
```

## 5. 测试

新增 `tests/test_v1_order_planner.py`（21 个测试）：

- 可行 golden：FINAL + RAW_A/RAW_D、RAW_B 绕行、目标数量 2、SEMI_CE
  route-only、输入容器顺序无关；
- 等价候选：同时有 RAW_A/RAW_B 时按最少工序选直达路线，候选 ID/digest
  稳定；`preferred_route_ids` 可覆盖默认；
- 参数化无解：缺 RAW_D、只有 RAW_D、目标 RAW_B 不可达、`max_depth`/
  `max_nodes`/目标数量×节点数触发搜索上限、策略版本/场景不匹配；
- 约束解释：模板排除导致 join 输入不可达、设备排除 warning；
- 严格输入：未知字段、非正整数、未知物料类型；
- 服务草稿：新订单版本 1、已有订单追加版本 2、无解不落库、草稿可被普通
  compiler 编译；
- HTTP API：plan、drafts、版本读取、无解 422、health 策略版本。

全量回归：**598 passed / 3 skipped**。3 个 skipped 为既有 CoppeliaSim
门控用例；本阶段未启动真实仿真。

## 6. 限制与未建模维度

- planner 只基于静态目录和声明式图，不承诺物理可行；设备健康、节拍、
  实时资源占用、AGV 排队、几何可达性、碰撞和运动规划可行性不在模型内。
- `allow_high_risk` 字段已预留，但首期目录没有“高风险候选”元数据，因此
  该字段当前不改变候选集合；不得把它解释为已完成风险评估。
- 首期不引入全局最优 solver、随机/LLM 生成、跨订单优化或动态重规划。
- planner 不自动启动仿真；用户必须先预览/确认保存的不可变版本。
- 目录版本变化后，旧 planner 草稿仍可读取；启动时按阶段 3 规则校验目录
  版本，不匹配则拒绝。
- `preferred_route_ids` 中不存在的路线 ID 当前被忽略；explain 保留输入
  原值，调用方应使用模板目录返回的路线 ID。

## 7. 代码落点

| 模块 | 内容 |
|---|---|
| `application/planner.py` | `PlannerInput`/`PlannerResult`、route/join macro、反向搜索、物化、explain |
| `application/orders.py` | `OrderService.plan/plan_and_save`、planner 元数据传递 |
| `persistence/order_models.py` | 订单版本 planner 元数据列 |
| `persistence/orders.py` | 版本写入/读取 planner 元数据 |
| `persistence/migrations/versions/0006_order_planner_metadata.py` | schema 迁移 |
| `api/server.py` | `/api/planner/plan`、`/api/planner/drafts`、health 策略版本 |
| `cli.py` | `order plan` / `--save` |
| `spec.py` | `ORDER_PLANNER_POLICY_VERSION` |
| `tests/test_v1_order_planner.py` | planner golden/无解/tie-break/服务/API 测试 |
