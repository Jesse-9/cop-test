---
title: 自建订单 DAG 阶段 9 — 场景 profile 感知的订单组合
type: plan
status: implemented
created: 2026-11-21
updated: 2026-11-21
scope: v1 UFLOW / full_flow / assembly_mvp / order workbench / planner
related:
  - v1-self-built-order-dag-orchestration.md
  - v1-order-dag-phase-04-auto-planner.md
  - v1-order-dag-phase-05-frontend.md
  - v1-order-dag-phase-06-simulation-acceptance.md
  - ../../reports/order-orchestration/v1-order-dag-phase-09-scene-profile-order-catalog.md
  - ../../evidence/order-orchestration/v1_order_dag_phase9_scene_profile_20261121_153000.json
  - ../production-flow/v1-neo4j-assembly-mixed-line-mvp.md
  - ../../reports/production-flow/v1-neo4j-assembly-mixed-line-mvp.md
---

# 自建订单 DAG 阶段 9：场景 profile 感知的订单组合

## 目标

把当前已登记且有运行依据的四种订单蓝图纳入同一订单工作台：用户可以手工编排并保存订单，也可以让后端 planner 生成订单 DAG，随后通过现有订单运行入口在匹配的 CoppeliaSim 工件中执行。

四种蓝图为：

| 场景能力 | 投料 | 目标 | 规划形式 |
|---|---|---|---|
| `full_flow` 壳体直达 | `RAW_A + RAW_D` | `FINAL` | `housing_direct + bracket_route + kit_join` |
| `full_flow` 壳体绕行 | `RAW_B + RAW_D` | `FINAL` | `housing_alt + bracket_route + kit_join` |
| `assembly_mvp` 压缩机装配 | `COMPRESSOR + BASEPLATE` | `COMPRESSOR_ASSY` | `mvp_compressor_assembly` 直接来料合流 |
| `assembly_mvp` 法兰装配 | `FRONT_FLANGE + REAR_FLANGE` | `FLANGE_ASSY` | `mvp_flange_assembly` 直接来料合流 |

路线/蓝图是可选工艺，不是四种必须拼在一个订单里的步骤。订单仍保持单一目标物料类型；混合产品组合以多笔独立订单表达。双车仍是 run 的车队配置，不进入订单 DAG 或路线组合定义。目标数量需受物料供给、区域容量、批量配置及 planner 搜索上限约束，不能据路线存在推断数量无限。

## 当前边界

- full-flow 两个 `FINAL` 组合已由 planner 正例覆盖；planner 使用已登记 `RouteVariant` 与 `kit_join` 搜索。
- MVP 两种订单已能通过订单 validator/compiler，且独立 `assembly_mvp` 场景工件有真实 CoppeliaSim 混线序列验收记录。
- MVP API 路径已有订单模板、预览、创建和启动测试，但该 API 测试使用记录型 runner；真实仿真验收与“从前端工作台发起的真实仿真”是不同证据。
- 前端 `ITEM_TYPES` 目前只包含 full-flow 八种物料，MVP 的目标/投料选择无法完整表达；`/api/templates` 返回目录全部可编排工序，需按当前工件能力过滤。
- planner 的反向搜索通过 route/join 宏为 join 输入递归寻找生产路线。MVP 输入是订单直接投料，没有静态 route/entry，因此当前 planner 无法到达两个 MVP 目标。
- `OrderSpec.scene_id` 识别仿真场景，不等于场景工件的 `scene_profile`。能力过滤应读取实际加载工件/会话的 profile 或等价能力清单，不要仅靠模板 ID 猜测，也不要把 profile 与车队模式混为一谈。

## 范围与明确不做

本阶段覆盖场景能力发现、订单工作台可表达性、planner 对直接来料 join 的支持和端到端验收。沿用现有 OrderSpec、compiler、订单持久化、run API、coordinator 和失败语义。

本阶段不增加通用 recipe 图编译器，不允许任意物料/模板/脚本，不改写 MVP 两个固定 handler，不把不同目标产品合并为多目标 OrderSpec，不承诺多订单同时运行，不改变 single/dual 运行派车，也不把 planner 的静态判断当成物理可行性保证。

## 分阶段实施

### 9A. 场景能力目录与订单编辑器（中）

**目的：**让手工订单界面准确表达当前所选工件可运行的目标物料、投料类型和工序模板。

1. 明确 `full_flow` 与 `assembly_mvp` 工件的 profile/能力清单读取来源。以已加载/将用于启动的 artifact manifest 或服务端已解析会话为权威来源；API 响应同时返回 `scene_id`、`scene_profile` 和该 profile 可用的目标类型、输入类型、模板及必要 zone/port 元数据。
2. 为模板/能力查询增加 profile 过滤。full-flow 工件返回 full-flow 模板；assembly_mvp 工件按照其实际包含的基础布局与扩展岛能力返回可运行模板。若 MVP 工件确实包含 full-flow 能力，应允许两类订单；若构建工件并不包含，则按工件验证结果调整映射，不在 UI 猜测。
3. 前端以服务端目录数据驱动物料和模板选择，移除订单编辑器、Inspector、PlannerPage 对固定 `ITEM_TYPES` 集合的运行时假设。类型声明可保留编译期联合类型，但服务端返回值必须在解析边界校验，避免不认识的物料值静默进入订单。
4. 场景/profile 切换后清理不兼容节点、边和投料，或者阻止保存并给出具体诊断；不得把一个 profile 的订单静默保存为另一个 profile。
5. 保持 manual DAG 的端口绑定、订单版本、preview/compiler 和运行入口不变；检查 MVP 直接来料节点的目标绑定、数量和两个端口输入在编辑器中都可操作。

**验收：**full-flow 与 assembly_mvp 能力 fixture 各一份；前端目录加载后可创建对应手工订单并通过 preview；跨 profile 错误订单被拒绝；模板与物料下拉选项不出现当前 profile 不支持项。

### 9B. Planner 支持直接来料合流（中）

**目的：**让现有 planner 在保持有界、确定性搜索的前提下，能够生成两种 MVP 单节点装配订单。

1. 扩展 planner 的领域表达，显式区分两类合法输入根：由 route 生产的中间物料，以及由已登记 `DIRECT_INPUT_JOIN_HANDLERS` 明确接纳的订单直接投料。只允许登记的 MVP handler 使用直接输入规则。
2. 在反向搜索遇到 `mvp_pair_assembly` 时，将该模板的固定输入端口转成订单投料需求，而不是继续要求每种源物料存在 route。完整检查模板/profile、输入物料类型与 zone、端口 ID、目标输出、允许模板和数量。
3. 物化 OrderSpec 时为 MVP join 输出两条带 `node_id`/`port_id` 的 `OrderInput`，无虚构 route、无伪造 predecessor；同一 compiler/validator 继续负责最终结构校验。
4. 对不匹配的配对、缺料、跨 profile 目标、数量超限和不允许模板返回可解释诊断。保留 full-flow planner 行为、候选排序、single/dual fleet assumptions 和现有 digest 语义。
5. 限定搜索空间：只增加这两种已登记直接来料 recipe 的规划支持，不实现任意直接输入 join 配方语言。

**验收：**planner 对 `COMPRESSOR_ASSY` 与 `FLANGE_ASSY` 各有一个黄金正例，输出订单经 validator/compiler/preview 通过；每个产品至少有缺料及错误物料配对负例；full-flow 两条路线与双车相关规划回归不变。API 和 UI 的 planner 结果可保存为普通 planner 来源订单。

### 9C. 工作台到 CoppeliaSim 端到端验收（中，依赖场景工件）

**目的：**区分逻辑可编译、API 可启动和真实场景可运行三种证据，确认四种蓝图都能从订单工作台的正式路径落地。

1. 在 full_flow 工件上各运行一次手工或 planner 生成的 `RAW_A + RAW_D → FINAL` 与 `RAW_B + RAW_D → FINAL`，覆盖两个 housing 路线；双车只选一例验证既有 run 配置透传，不把它计为新工艺组合。
2. 在 assembly_mvp 工件上分别从工作台创建/规划并启动压缩机和法兰订单；检查所选 artifact profile、场景对象/zone、两项来料、join 输出与目标类型一致。
3. 核对每个 run 的订单版本、DAG digest、profile/artifact、输入 UID、完成 UID、父子谱系、节点/订单终态及报告；不得只凭 API 返回 `STARTING` 或记录型 runner 的 `SUCCEEDED` 判定真实仿真通过。
4. 在一条路径做 `full_flow` 回归，并只选择一条 MVP 路径做已知负例/运行拒绝验证；不得为每个组合重复相同结构测试。已有 MVP 仿真报告可复用作场景基线，但新增验收至少要覆盖“从目标工作台版本实际触发正式订单 run”的路径。
5. 对未运行的数量、profile 交叉组合、真实运动故障或路线并行能力明确标为未验收；运行失败依现有节点级/全局故障语义处理，不在本阶段扩展失败模型。

**完成条件：**四种蓝图均能在对应 profile 下由 UI 手工编排或 planner 生成，并进入同一个订单版本/预览/启动/报告链路；planner 覆盖其负责的四种组合；真实仿真验收至少各覆盖 full-flow 两路线和两个 MVP 产品；运行报告能追溯实际输入、成品和 profile。

## 精简验证矩阵

| 层 | 必要验证 | 控制规模 |
|---|---|---|
| Domain/API | 两个 MVP planner 正例、代表性缺料/错配负例、profile 能力过滤、订单 preview/compile | 小型参数化用例；复用现有 validator/compiler 测试 |
| Frontend | 两种 profile 加载对应物料/模板；可完成一个手工 MVP DAG 并保存/预览 | 1 条组件/API 集成路径，不为每个模板复制用例 |
| Regression | 既有 full-flow direct/alt planner golden cases 与 phase 8 fleet cases | 运行现有聚焦测试，不重写同义用例 |
| CoppeliaSim | 四蓝图各一条正式订单运行；谱系/终态断言；一次代表性错误路径 | 4 个组合各一次，避免矩阵膨胀 |

## 关键决策

- “全部规划到订单工作台”指四种单目标蓝图都能被目录发现、保存为普通订单、预览、启动和追溯；不表示一个订单能同时产出 `FINAL`、`COMPRESSOR_ASSY`、`FLANGE_ASSY`。
- `assembly_mvp` 的真实场景证据已存在，但 UI 和自动规划仍须分别验收；不把过去的 CLI/独立仿真证据等同于工作台端到端。
- 订单 profile 能力来源必须对应实际运行工件；`scene_id=L5_UFLOW` 本身不足以区分 `full_flow` 与 `assembly_mvp` 工件契约。
- 每种目标数量的最大值取决于供料、区域与批次约束，先验证 quantity=1 的蓝图端到端，再扩展数量边界。

## 代码落点建议

- 服务端能力目录：`application/orders.py`、`api/server.py`、场景工件/manifest 解析入口。
- Planner：`application/planner.py`、`domain/assembly_mvp.py`、`domain/process_model.py`。
- 前端目录消费：`frontend/src/api/client.ts`、`frontend/src/api/types.ts`、`OrderEditorPage.tsx`、`Inspector.tsx`、`PlannerPage.tsx`。
- 订单测试：`tests/test_v1_order_planner.py`、`tests/test_v1_assembly_mvp.py` 及前端对应测试。
