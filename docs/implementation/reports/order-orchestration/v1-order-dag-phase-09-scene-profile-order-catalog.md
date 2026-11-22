---
title: 自建订单 DAG 阶段 9 实施报告 — 场景 profile 感知的订单组合
type: report
status: review
created: 2026-11-21
updated: 2026-11-21
scope: v1 UFLOW / full_flow / assembly_mvp / order workbench / planner
related:
  - ../../plans/order-orchestration/v1-order-dag-phase-09-scene-profile-order-catalog.md
  - ../../plans/order-orchestration/v1-order-dag-phase-08-fleet-aware-planner.md
  - ../../reports/production-flow/v1-neo4j-assembly-mixed-line-mvp.md
  - ../../evidence/order-orchestration/v1_order_dag_phase9_scene_profile_20261121_153000.json
---

# 自建订单 DAG 阶段 9 实施报告

本报告记录
[阶段 9 方案](../../plans/order-orchestration/v1-order-dag-phase-09-scene-profile-order-catalog.md)
的实施结果。改动把 `full_flow` 与 `assembly_mvp` 两种工件能力纳入同一订单
工作台和 planner：目录查询按实际工件 manifest 过滤，手工订单可表达两种
MVP 直接来料装配，planner 可生成两种单节点 MVP 订单，运行启动前按工件
profile 拒绝跨 profile 订单。

结论：**9A（能力目录与编辑器）、9B（planner 直接来料合流）已实施**，
`OrderSpec` 契约、DAG digest、validator/compiler、运行 API 和 coordinator
失败语义保持不变。9C 已在 CoppeliaSim 4.10 上完成四个订单蓝图的正式
run：两条 full_flow 路线（手工直达/planner 绕行）各生成 1 个 `FINAL`，
MVP 压缩机/法兰各生成 1 个装配体，父子 UID 均为 `[1,2]`；两条
full_flow 用例使用 `dual`（`cart_2` D→E），原因是本次 single fleet 在
E 臂 AGV 托盘对位出现既有 `GOAL_CONFIG_INVALID`，详见第 7 节。MVP 实验
工件因旧 config digest 与当前代码不匹配，已按当前配置重建为 v002 后再
执行。未执行项逐项列出，不把纯逻辑测试等同于物理验收。

## 1. 场景 profile 能力来源

新增 `domain/scene_profile.py`，只读静态目录与工件 manifest：

- profile 名称：`full_flow`、`assembly_mvp`，缺省 `full_flow`；
- 权威来源：manifest `extra.v1.scene_profile`；
- 旧工件没有该字段时，按 `extra.v1.assembly_mvp` 扩展块或
  `material_model.extension_types` 判定 `assembly_mvp`，否则回落
  `full_flow`；
- `extra.v1.material_model.types` 在解析边界校验：未知物料类型、重复类型、
  目录模板所需类型未声明、`full_flow` 工件声明 MVP 扩展类型均抛
  `SceneProfileError`（API 映射 422）。

能力清单由 `ProcessCatalog` 与 profile 规则计算：

| profile | 模板 | 目标物料 | 入口物料 | 区域 |
|---|---|---|---|---|
| `full_flow` | v1 五岛模板（不含 MVP 直接来料） | `FINAL`、`SEMI_*`、`PART_DE` | `RAW_A/B/D` | v1 九区域 |
| `assembly_mvp` | v1 五岛 + `mvp_compressor_assembly`/`mvp_flange_assembly` | 上述 + `COMPRESSOR_ASSY`/`FLANGE_ASSY` | 上述 + 4 个 MVP 来料 | v1 + 8 个 MVP 区域 |

区域元数据包含 `id`、容量、描述、`spawnable` 和该区域可接收的物料类型。
未绑定工件时返回显式 `profile_source=unbound`（全部目录模板），不代表任何
具体工件可运行。

## 2. 服务与 API

`OrderService` 新增 `artifact_manifest_loader`（CLI 由 `ArtifactStore` 注入）
和 `scene_capabilities(artifact_id)`：

- `GET /api/templates?artifact_id={ref}` 返回
  `scene_id`、`scene_profile`、`profile_source`、`profile_version`、
  `item_types.{targets,inputs}`、`zones`、过滤后的 `templates`/`routes`；
- `POST /api/orders/preview?artifact_id={ref}` 在编译预览之外追加
  profile 兼容性诊断；`POST /api/orders/{order_id}/versions/{version}/preview`
  同样接受 `artifact_id`，供运行详情页在启动前按所选工件重新校验；跨
  profile 订单 `ok=false`；
- `OrderService.start()`/`run_sync()` 在创建 run 之前按实际工件 manifest
  校验订单模板、目标、投料和区域；不兼容时抛 `OrderRunConfigError`
  （HTTP 422），不写 `order_run` 记录。校验通过的 profile 写入
  `OrderRunCommand`、订单运行报告和运行结果摘要，便于从报告直接追溯
  “订单版本 + DAG digest + artifact + scene_profile”；
- 未配置 manifest loader 时，`start` 保持既有行为（不做 profile 过滤），
  目录查询返回 `unbound`；显式指定 artifact 的能力查询会被拒绝，避免把
  未读取的工件描述成已绑定 profile；
- `/api/health` 额外返回 `scene_profile`、`profile_source`、
  `scene_profiles` 和 `scene_profile_version`，工件缺失时只记录错误状态，
  不让健康检查失败抛出未处理异常。

诊断码：

| code | 语义 |
|---|---|
| `ORDER_TEMPLATE_PROFILE_MISMATCH` | 节点模板不属于工件 profile |
| `ORDER_TARGET_PROFILE_MISMATCH` | 目标物料不在 profile 可生产类型中 |
| `ORDER_INPUT_PROFILE_MISMATCH` | 投料物料不在 profile 入口类型中 |
| `ORDER_INPUT_ZONE_PROFILE_MISMATCH` | 投料区域不属于工件 profile |

## 3. Planner 直接来料合流

`PlannerInput` 新增 `scene_profile`（缺省 `full_flow`），输入边界拒绝未知
值；planner 只按 profile 过滤 route/join 模板：

- `full_flow` 下跳过 MVP 直接来料模板并输出
  `PLANNER_TEMPLATE_PROFILE_EXCLUDED` warning；目标为目录能生产但当前
  profile 不允许时输出 `PLANNER_TARGET_PROFILE_MISMATCH`；
- `assembly_mvp` 下，`DIRECT_INPUT_JOIN_HANDLERS` 中已登记的
  `mvp_pair_assembly` join 不再递归寻找 route，而是把固定输入端口转为
  订单投料需求；
- 物化时每个目标单位生成 1 个 join 节点和 2 条带 `node_id`/`port_id` 的
  `OrderInput`，不生成虚构 route、predecessor 或边；同一
  validator/compiler/preview 继续负责结构校验；
- 缺料/错误配对继续输出 `PLANNER_INPUT_INSUFFICIENT`，数量超过
  `max_nodes` 输出 `PLANNER_NODE_LIMIT`；没有实现任意直接输入 join 配方
  语言；
- explain 的 `join_bindings` 为直接投料端口标记 `source=input`、区域和
  端口；`scene_profile`、`profile_templates/targets/inputs` 写入 explain；
  `OrderSpec`/DAG digest 与 full-flow planner 行为不变。

## 4. 前端工作台

- `frontend/src/api/types.ts` 保留编译期已知物料联合类型
  （`KNOWN_ITEM_TYPES`），但运行时可选项全部来自 `/api/templates`；
  `parseTemplateCatalog()` 在 API 解析边界校验 profile/物料/端口/区域，
  未知物料直接抛 `INVALID_RESPONSE`；
- 订单编辑器与 planner 页增加“场景工件”输入和“加载能力”动作；目录响应
  显示 `scene_profile`、`profile_source`、`artifact_id` 和版本；
- 编辑器目标、投料物料、区域和模板 palette 由服务端目录驱动；区域下拉
  只显示 `spawnable` 区域，并按其 `item_types` 约束物料选择；新增投料
  表单和每笔投料行都支持显式 `node_id`/`port_id` 绑定（MVP 直接来料
  必须显式绑定逻辑节点端口）；
- 切换工件/profile 后，`draftProfileIssues()` 计算不兼容目标/节点/投料/
  区域；存在诊断时禁止保存并展示明细，可点击“清理不兼容项”移除不兼容
  节点、边、投料和画布位置；
- MVP 直接来料节点在 Inspector 中可设置目标数量、两个显式入口端口和
  区域/物料组合；保存/预览/版本化路径不变；
- 订单详情页的启动对话框与预览共用所选 `artifact_id`：用户切换启动工件后，
  预览按新工件 profile 重算，未通过时启动按钮保持禁用；启动仍调用同一
  `POST /versions/{version}/start`。

## 5. 版本与兼容性

- 新增 `spec.SCENE_PROFILE_VERSION = "v1_scene_profile_20261121"`；
- `ORDER_PLANNER_POLICY_VERSION` 升至 `v1_planner_20261121b`（搜索空间与
  explain 增加 profile 和直接来料 join）；旧 planner 记录仍按缺省
  `full_flow` 读取；
- `ORDER_CONTRACT_VERSION`、`OrderSpec` 字段、DAG digest 算法、
  `config_digest`/`layout_digest` 和工件保护不变；
- 数据库 schema 无新增迁移；planner 输入/explain 继续存放在既有
  `order_version` planner 元数据列；
- 未配置 manifest loader 的既有服务/测试保持 `unbound` 目录行为。

## 6. 自动化测试

执行日期：2026-11-21。

| 命令 | 结果 |
|---|---|
| `uv run pytest -q` | **773 passed, 8 skipped**（跳过项为需 CoppeliaSim 的 focused 用例） |
| `cd frontend && npm test` | **16 passed** |
| `cd frontend && npm run e2e` | **4 passed**（mock 旅程） |
| `cd frontend && npm run typecheck` | 通过 |
| `cd frontend && npm run build` | 通过 |

新增/更新测试：

- `tests/test_v1_scene_profile.py`：manifest profile 判定（含旧工件回落和
  真实仓库工件）、full_flow/assembly_mvp 能力过滤、物料声明校验、跨
  profile 订单诊断、service `templates/preview/start` 拒绝、version
  preview 的 `artifact_id` 查询、HTTP `/api/templates`/`/api/health` 返回；
- `tests/test_v1_order_planner.py`：两种 MVP 目标黄金正例、直接投料端口、
  数量扩展、缺料/错配负例、profile 目标错配、输入边界、explicit
  `full_flow` 回归、planner 草稿保存；
- `frontend/src/api/client.test.ts`：目录 artifact 查询、未知物料解析拒绝；
- `frontend/src/domain/orderDraft.test.ts`：跨 profile 诊断与清理；
- `frontend/e2e/mock.ts`/`journeys.spec.ts`：目录新字段与区域下拉适配。

未新增每模板/每区域的重复同义测试；full-flow 既有 golden cases、阶段 8
fleet cases 继续通过全量回归。

## 7. CoppeliaSim 验收

### 7.1 环境

执行日期：2026-11-21。CoppeliaSim 4.10（ZeroMQ 23000），订单 API 与
Vite 分别使用 `127.0.0.1:8081`/`127.0.0.1:5174`，避免与已有的 8080/5173
进程冲突。UI 验收脚本为 `frontend/e2e/real_acceptance.mjs`，不使用 route
mock；脚本通过真实 API 创建/保存订单并从页面启动。

| 工件 | artifact_id | manifest profile | manifest digest | validation |
|---|---|---|---|---|
| full_flow | `v1/L5_UFLOW/v005_20261117_110924` | `full_flow`（旧 manifest 未写字段，按 `material_model` 回落） | `70457326…f4f1` | ok |
| assembly_mvp | `v1/L5_UFLOW/v002_20261121_152334`（root: `docs/scene_artifacts/experiments/assembly-mvp`） | `assembly_mvp` | `c86311c9…dfce` | ok |

MVP 旧实验工件 `v001_20261114_120734` 保留为历史记录，但其
`v1_process_20261114a`/config `e292c37f…` 与当前代码不一致，启动前只读
校验会拒绝；本次验收使用按当前配置重建的 `v002`（详见 7.2）。

版本：订单契约 `v1_order_20261106`、planner 策略
`v1_planner_20261121b`、场景 profile 策略
`v1_scene_profile_20261121`、目录 `v1_process_20261117a`、车队策略
`v1_fleet_20261119`。

### 7.2 执行结果

| 用例 | 工作台路径 | 工件/profile | 订单版本 | run | 结果 |
|---|---|---|---|---|---|
| full_flow 壳体直达 + 支架 | 手工 DAG（UI） | `v005` / `full_flow` | `accept-ui-manual-1795242936443@1`，digest `f43be3e2…5413` | `order_accept-ui-manual-1795242936443_20261121_063539_a31a53` | `SUCCEEDED`；sim 136.45 s；7 节点；`FINAL` UID 3，父 UID `[1,2]`；`transport_DE → cart_2`（dual） |
| full_flow 壳体绕行 + 支架 | planner（UI，`RAW_B + RAW_D`） | `v005` / `full_flow` | `planner-draft@9`，digest `8d797e94…7252` | `order_planner-draft_20261121_065851_8483a9` | `SUCCEEDED`；sim 293.85 s；9 节点；`FINAL` UID 3，父 UID `[1,2]`；`transport_BA → agv_1`、`transport_DE → cart_2`（dual） |
| assembly_mvp 压缩机 | 手工 DAG（UI） | `v002` / `assembly_mvp` | `accept-ui-mvp-compressor_assy-1795245941174@1`，digest `2d25e0b7…8995` | `order_accept-ui-mvp-compressor_assy-17_20261121_072543_17d3d6` | `SUCCEEDED`；sim 0.05 s；1 节点；`COMPRESSOR_ASSY` UID 3，父 UID `[1,2]`；`mvp_pair_assembly`，端口 `compressor-in/baseplate-in`（single） |
| assembly_mvp 法兰 | planner（UI） | `v002` / `assembly_mvp` | `planner-draft@10`，digest `c7d68f2a…333b` | `order_planner-draft_20261121_072721_3bc7f3` | `SUCCEEDED`；sim 0.05 s；1 节点；`FLANGE_ASSY` UID 3，父 UID `[1,2]`；`mvp_pair_assembly`，端口 `front-in/rear-in`（single） |

四个 run 均通过真实 UI API 启动，并在运行页刷新前后显示“成功”；节点/订单
终态、报告和谱系一致。手工直达用例的 `run` 节点输入/输出 UID 为：
`housing_direct_A`/`housing_finish`/`housing_to_E_buffer` 的 UID 1，
`bracket_process`/`transport_DE`/`bracket_to_E` 的 UID 2，`kit_join` 输入
`[1,2]`、输出 UID 3；`report_run` 记录 `final_uids=[3]`，
`report_genealogy` 显示 UID 3 的父件为 UID 1（`SEMI_CE`）和 UID 2
（`PART_DE`），成品状态 `DELIVERED`、区域 `output_e`。绕行用例的父件来源
为 `line_s1b`（UID 1）和 `line_s2d`（UID 2），同样在 `output_e` 提交。
MVP 两例的 `report_run.counts.material_types` 分别为
`{BASEPLATE, COMPRESSOR, COMPRESSOR_ASSY}` 和
`{FRONT_FLANGE, REAR_FLANGE, FLANGE_ASSY}`；谱系父件 UID 均为 `[1,2]`，
成品区域分别为 `mvp_output_compressor_assy`/`mvp_output_flange_assy`。

**MVP 工件重建。** 旧实验工件
`v1/L5_UFLOW/v001_20261114_120734` 的 manifest 仍使用
`v1_process_20261114a`/config `e292c37f…`，与当前
`v1_process_20261117a`/config `c132340c…` 不一致；`start` 前只读校验返回
`v1_config_digest` 与 `v1_process_model_version` 失败。为完成当前代码的
真实验收，使用当前配置从 stage3 `L2_MIRROR/v008_20261029_112305` 重新
build/save 了实验工件
`v1/L5_UFLOW/v002_20261121_152334`（manifest
`c86311c9…dfce`，validation ok，`protected=true`）；旧 v001 保留未覆盖。
该工件不包含 `Cart2`，因此 MVP 验收使用 `single`。

**单 fleet 运行注记。** 本机本次对相同 full_flow 手工单在 `single` 下尝试
两次，均在 `bracket_to_E` 的 E 臂 AGV 托盘取料后 `pre_place` 法向段返回
`GOAL_CONFIG_INVALID`（lateral≈25 mm），`horizontal_direct`/`detour_±0.30`
回退均未通过，按既有故障语义升级为 GLOBAL `ABORTED`（其中一次运行由
操作者主动 stop，另一次为全局失败）；planner 绕行单也在相同 E 臂 plan q
出现前主动停止。该现象属于运动层既有的 AGV 托盘对位余量问题，不是阶段 9
新增的订单/profile 语义；阶段 7 的 single 回归证据仍作为 single 基线。
为完成两条 full_flow 路线与 profile 验收，两条 full_flow 用例都使用
`dual`（`cart_2` D→E）；这超出了“只选一例双车”的规模建议，报告明确记录，
且不把它计为新工艺组合或并行提速证据。MVP 用例为无运动逻辑装配，不受
此问题影响。

证据：[v1_order_dag_phase9_scene_profile_20261121_153000.json](../../evidence/order-orchestration/v1_order_dag_phase9_scene_profile_20261121_153000.json)。

### 7.3 负例：MVP 订单在 full_flow 工件上启动

通过 full-flow API 创建 `phase9-mvp-negative@1`（DAG digest
`3c0e1f9d…70e9`，目标 `COMPRESSOR_ASSY`，模板
`mvp_compressor_assembly`），再以 `artifact_id=v1/L5_UFLOW/v005_20261117_110924`
启动：

- HTTP `422`，错误码 `ORDER_RUN_CONFIG`；
- 消息列出 `ORDER_TEMPLATE_PROFILE_MISMATCH`、
  `ORDER_TARGET_PROFILE_MISMATCH`、`ORDER_INPUT_PROFILE_MISMATCH` 和
  `ORDER_INPUT_ZONE_PROFILE_MISMATCH`；
- `GET /api/orders/phase9-mvp-negative/runs` 返回 `[]`，未创建 run 记录。

该负例在 no-run 路径验证了“保存的订单不静默跨 profile 运行”；MVP 订单在
匹配的 `assembly_mvp` 工件上的正常创建/规划/启动路径见 7.2 的两个用例，
本负例只覆盖拒绝路径。

### 7.4 未覆盖

- `quantity>1` 的 FINAL/MVP 实机运行；数量边界仅由 planner/compiler 单测
  覆盖；
- 四种订单蓝图的交叉 profile 组合、真实运动故障、ZMQ/step 断连和路线
  并行；
- 本轮没有获得 full_flow 的 single-fleet 成功路径：相同 E 臂托盘对位在
  single 下复现 `GOAL_CONFIG_INVALID`，因此两条 full_flow 都使用 `dual`
  完成 profile/路线验收；single 基线以阶段 7 回归为准。双车只作为运行配置
  透传证据（`transport_DE → cart_2`），不证明路线并行或吞吐提升。

## 8. 边界与未覆盖

- profile 能力只说明静态目录/工件声明，不保证几何可达、碰撞、节拍或运动
  可行性；
- 未实现通用 recipe 图编译、任意直接输入 join、同一订单多目标产品、多
  订单并发或跨订单优化；
- `assembly_mvp` 工件包含 v1 五岛底图能力，因此允许两类订单；若未来工件
  构建不再包含底图，必须通过 manifest/profile 校验调整能力，不能沿用本
  报告的假设；
- 目标数量只验证到 `quantity=1` 的蓝图；更大数量受供料、区域容量和批次
  配置约束，未据路线存在推断无限数量；
- 跨 profile 拒绝发生在保存后的启动路径；已保存的订单版本本身仍可读取，
  不会被静默改写；
- 本轮不扩展节点级/全局失败模型；真实 Arm/AGV 运动故障、ZMQ/step 断连
  等未验证类型仍按 GLOBAL 处理。