---
title: 柔性工业车间 v1 使用指南
type: tutorial
tags:
  - coppeliasim
  - 类别/基础知识
  - 难度/初级
created: 2026-10-26
updated: 2026-11-12
status: published
sim_version: "4.10+"
---

# 柔性工业车间 v1 使用指南

本指南说明 CoppeliaSim、ZMQ 连接、v1 场景包操作和 `full_flow` 运行。完整命令、
参数、默认值和可选项见 [CLI 参数手册](CLI_REFERENCE.md)。命令以
Windows PowerShell、仓库根目录为例。v1 不支持历史阶段入口；历史实现、报告
与证据已清理，结论见 [V1 实施报告](../../implementation/reports/product/v1-product-implementation.md)。

## 启动仿真软件

1. 安装并启动 CoppeliaSim 4.10 或更新版本。项目所需内置模型包括 UR5、
   suction pad、generic conveyor 和 pioneer p3dx。
2. 在仓库根目录准备 Python 环境：

        uv sync

3. 在 CoppeliaSim 中启动 ZeroMQ Remote API server add-on，确认监听
   `localhost:23000`。菜单通常为 **Add-ons → ZMQ remote API server**。
4. 保持 CoppeliaSim 运行，从仓库根目录执行 v1 命令。远程仿真时使用
   `--host` 和 `--port`。

查看命令：

    flexible-factory --help
    flexible-factory run --help

## v1 场景包与当前基线

v1 场景包位于独立命名空间：

    docs/scene_artifacts/v1/
      current.json
      L5_UFLOW/v001_<timestamp>/
        scene.ttt
        manifest.json
        validation.json
        lifecycle.jsonl

当前 v1 场景包为 `v1/L5_UFLOW/v001_20261103_004317`，由已验收 stage4
`stage4/L5_UFLOW/v004_20261029_133249` 行为等价迁移。仅保留该 stage4 场景包
及其 stage3 上游 `L2_MIRROR/v008_20261029_112305` 作为只读迁移来源，禁止
覆盖；其他历史场景包已清理。v1 场景包受摘要、生命周期和只读保护；不得手工修改
manifest 或用 GUI 覆盖 `scene.ttt`。

## 日常运行

    flexible-factory load
    flexible-factory validate      # 可选诊断
    flexible-factory run --scenario full_flow --time 700

- `load` 只加载 `v1/current.json` 指向的场景包，不构建、不清场，加载后可直接运行。
- `run` 在取得运行锁后执行完整只读校验；校验失败拒绝启动并释放锁。
- `validate` 是可选诊断，不是 `run` 的前置条件。
- 运行期间禁止 build/load/save/validate 或启动第二个控制任务。
- 报告写入 `docs/implementation/runs/v1_run_<scenario>_<stamp>.json`。
- 运行记录默认写入 `var/catalog/catalog.db`；`--run-records off` 可关闭，
  写库失败不会中止运行。

`--scenario` 的 `full_flow` 是唯一用户运行模式；`no_pair`、`agv_priority`、
`agv_blocked`、`conveyor_fault`、`staggered_batch` 仅用于内部回归和诊断。

本次运行的原料数量可用命名批次配比调整：

    flexible-factory run --scenario full_flow --batch-profile direct_heavy

- `default`（缺省）严格使用 `SCENARIO_BATCH[scenario]`，保持现有运行。
- `direct_heavy`：`RAW_A=2, RAW_B=0, RAW_D=2`，壳体直达路线占比提高。
- `detour_heavy`：`RAW_A=0, RAW_B=1, RAW_D=1`，壳体 100% 走 AGV 绕行。
  同时投入 2 个 `RAW_B` 可能使后续壳体等待 AGV 空载移位达到 300 s 背压
  上限；该边界记录在实施报告中。
- 批次只改变本次运行的 `RAW_A/RAW_B/RAW_D` 数量，不改变工艺路线、设备
  候选、场景包或 `config_digest`；非 `default` profile 只用于
  `full_flow`。运行快照和报告记录实际生效的批次及
  `BATCH_INPUT_VERSION`。
- 批次配比只展示路线混批能力，不表示同一 UID 可在运行中动态选路。
  默认与两个 profile 的实机验收结果见
  [实施报告](../../implementation/reports/production-flow/v1-mixed-production-flexibility-demo.md)。

其他查询/控制命令：

    flexible-factory status
    flexible-factory status --offline
    flexible-factory list
    flexible-factory stop

## 订单 DAG（阶段 1–6）

自建订单把工序模板组合成声明式 DAG，经同一 validator/compiler 编译后在
`L5_UFLOW` 中执行。阶段 1–5 已实现契约、编译、局部失败隔离、版本化持久化、
本地 HTTP 服务、后端自主规划器和前端工作台；阶段 6 已在受保护 v1 场景包上
完成手工 UI 订单、planner UI 订单、物理动作前 NODE 拒绝和真实安全锁存
GLOBAL 停止验收，见
[阶段 6 实施报告](../../implementation/reports/order-orchestration/v1-order-dag-phase-06-simulation-acceptance.md)。
本轮只准入 `NODE_PRECHECK_REJECTED`；真实 Arm/AGV 运动错误、连接断开等
未验证类型仍是 GLOBAL，不能把纯逻辑或本报告范围外结果当作实机能力。

订单 JSON 只允许 `order_id/version/scene_id/target/inputs/nodes/edges/source`
字段；节点只引用已登记 `template_id`，边只引用端口 identity，不接受 Python/
Lua 代码、CoppeliaSim 句柄或未登记执行器。示例见
`tests/order_fixtures.py` 的 `full_flow_order()`。

CLI（不需要 CoppeliaSim 的校验/预览/版本命令）：

    flexible-factory order validate --file order.json --db var/catalog/catalog.db
    flexible-factory order create  --file order.json --db var/catalog/catalog.db
    flexible-factory order preview --order-id <id> --version 1 --db var/catalog/catalog.db
    flexible-factory order show    --order-id <id> --version 1 --db var/catalog/catalog.db
    flexible-factory order list    --db var/catalog/catalog.db

运行（需要 CoppeliaSim + ZMQ 23000；`start` 在当前进程同步执行）：

    flexible-factory order start --order-id <id> --version 1 \
        --artifact-id current --time 700 --run-records on
    flexible-factory order status --run-id <run_id> --db var/catalog/catalog.db
    flexible-factory order nodes  --run-id <run_id> --db var/catalog/catalog.db
    flexible-factory order events --run-id <run_id> --db var/catalog/catalog.db
    flexible-factory order stop   --run-id <run_id> --db var/catalog/catalog.db

HTTP 服务（worker 独占 CoppeliaSim stepping；默认只绑定 127.0.0.1）：

    flexible-factory api serve --host 127.0.0.1 --port 8080 \
        --sim-host localhost --sim-port 23000 \
        --artifact-id current --run-records on

    GET  /api/health
    GET  /api/templates
    GET/POST /api/orders
    GET  /api/orders/{order_id}
    GET/POST /api/orders/{order_id}/versions
    GET  /api/orders/{order_id}/versions/{version}
    POST /api/planner/plan
    POST /api/planner/drafts
    POST /api/orders/preview
    POST /api/orders/{order_id}/versions/{version}/preview
    POST /api/orders/{order_id}/versions/{version}/start
    GET  /api/orders/{order_id}/runs
    GET  /api/runs/{run_id}
    GET  /api/runs/{run_id}/nodes
    GET  /api/runs/{run_id}/events
    POST /api/runs/{run_id}/stop

- 创建/保存版本只做 JSON 字段校验；DAG 结构在预览和启动时由后端 validator/
  compiler 拒绝并返回定位诊断。`start` 绑定不可变版本、静态定义版本和具体
  场景包；`current` 会在服务端解析为具体的 `artifact_id`。
- 订单版本只能追加；数据库触发器禁止原地 UPDATE。运行开始后修改编排必须
  创建新版本。
- `api serve --host/--port` 只绑定本地 HTTP API；仿真 worker 通过
  `--sim-host/--sim-port` 连接 ZMQ（默认 `localhost:23000`），两者不要混用。
- 阶段 6 已验证：手工订单和 planner 订单各生成 1 个 `FINAL`，父 UID 对应
  本次两个输入；NODE 前置拒绝只阻断依赖后继；真实安全锁存使运行 `ABORTED`
  并保留锁存。证据和未覆盖边界见阶段 6 实施报告；真实连接断开、Arm/AGV
  运动类故障仍未获 NODE 准入。
- 首期只有 `NODE_PRECHECK_REJECTED` 在物理动作前且安全状态可核实时按 NODE
  隔离；真实 ArmExecutor/AgvExecutor 目前不产生该 code，纯逻辑测试不构成
  物理准入。安全锁存、仿真基础设施、共享资源和未知异常一律 GLOBAL。真实
  运动错误的 NODE 准入需 focused 证据，未验证类型保持 GLOBAL。
- 静态定义版本已升至 `v1_process_20261106a`。旧 release 需重新
  `catalog import/publish` 后才能作为 `--catalog-source db` 使用；订单表由
  `0005_orders` 迁移创建。
- API 首期没有认证/TLS，不得直接暴露到非本机网络。

### 自主规划（阶段 4）

planner 根据目标物料/数量和可用投料，确定性地选择已登记路线与合流，生成
普通订单草稿。它只读静态工艺与资源定义，不启动仿真、不申请资源；结果仍需预览/确认
后启动。

```json
{
  "target_item_type": "FINAL",
  "target_quantity": 1,
  "inputs": [
    {"item_type": "RAW_A", "quantity": 1},
    {"item_type": "RAW_D", "quantity": 1}
  ],
  "order_id": "planner-draft",
  "order_version": 1
}
```

    # 只规划并输出 explain；无解也返回可读诊断
    flexible-factory order plan --file planner.json

    # 规划并保存为 source=planner 的普通不可变订单版本
    flexible-factory order plan --file planner.json --save --note "golden"

可选字段：`scene_id`、`allowed_template_ids`、`allowed_device_ids`、
`preferred_route_ids`、`allow_high_risk`、`max_candidates`、`max_depth`、
`max_nodes`、`policy_version`。首期 `allow_high_risk` 没有目录风险元数据，
不改变候选集合。

HTTP：

    POST /api/planner/plan      只规划，返回 candidate/explain
    POST /api/planner/drafts    规划并保存草稿；无解返回 422

planner 只建模静态工艺图和投料数量；设备健康、节拍、实时资源、AGV 排队、
几何可达性、碰撞和运动可行性仍需运行期/阶段 6 验收。无解时
`first_blocking_category` 给出 policy/scene/target/template/device/join/
input/search_limit/compile 中最先阻断的类别。

### 前端订单工作台（阶段 5）

独立前端位于 `frontend/`（React + TypeScript + Vite + React Flow）。先启动
本机订单 API，再启动前端；浏览器不直接控制 CoppeliaSim。

    # 终端 1：订单 API（默认 127.0.0.1:8080）
    flexible-factory api serve --host 127.0.0.1 --port 8080

    # 终端 2：前端开发服务器（默认 127.0.0.1:5173，/api 代理到 8080）
    cd frontend
    npm ci
    npm run dev

打开 `http://127.0.0.1:5173/orders`。页面包括订单列表、订单编辑器、自动规划
和运行详情；编辑器的“服务端预览”是校验权威，保存总是创建新的不可变版本。
运行页只读显示服务端节点/事件快照，stop 按钮只发送受控停止请求。

    npm test          # Vitest 单元测试
    npm run e2e       # Playwright 三条旅程（首次先 npm run e2e:install）
    npm run build     # 生产静态产物 frontend/dist/

真实 CoppeliaSim 的 UI 验收入口见
[frontend/README.md](../../../frontend/README.md) 的
`e2e/real_acceptance.mjs`（`manual`/`planner`/`start`/`verify`）；该脚本不使用
route mock，会实际启动订单运行。

开发 API 目标可用 `VITE_DEV_API_TARGET` 覆盖；生产 API 基址可用
`VITE_API_BASE_URL` 覆盖（默认 `/api`）。前端与 API 不同源部署时需使用
同源反向代理或显式 CORS allowlist；首期 API 无认证/TLS，只作为本机工程
工具使用。详细说明见 [frontend/README.md](../../../frontend/README.md)
（仓库内路径：`frontend/README.md`）。

## 静态工艺与资源定义的数据库存储（维护操作）

产品工艺定义（代码对象 `RecipeSpec`）、工序模板、路线、设备、能力和区域等静态
工艺与资源定义（代码/API 名称为 `ProcessCatalog`/`catalog`，下文简称“目录”）
可持久化到本地 SQLite；默认路径
`var/catalog/catalog.db`（不写入受保护的场景包目录）。该功能不影响默认
运行：`run` 仍读取代码内定义，只有显式 `--catalog-source db` 才读取已发布的
数据库版本。目录是可复用的静态定义，不是文件夹，也不是 UID、实时物料位置、
运行资源占用或设备状态；后几类状态由运行时账本和控制器管理。
数据库查看方式汇总见
[本地数据库查看指南](../../implementation/reference/data-platform/database-inspection-guide.md)。

    # 初始化或升级 schema（幂等）
    flexible-factory catalog init

    # 导入代码默认目录为 staging release（--release-id 可省略）
    flexible-factory catalog import --release-id rel_v1_process_20261103b_01

    # 校验后原子发布
    flexible-factory catalog publish --release-id rel_v1_process_20261103b_01

    # 状态、代码/数据库影子对比、完整性与往返校验
    flexible-factory catalog status
    flexible-factory catalog diff
    flexible-factory catalog verify

    # 显式使用数据库中已发布的定义运行 full_flow
    flexible-factory run --scenario full_flow --time 700 --catalog-source db

`catalog diff` 必须输出 `"ok": true` 且差异为空；`run --catalog-source db`
也会在连接 CoppeliaSim 前执行同样的等价检查，不一致则拒绝启动。数据库不可用
或没有已发布 release 时不会回退到代码内定义，错误为 `CATALOG_*`。需要
CoppeliaSim 的 `full_flow` 验收不在静态定义数据库维护命令范围内。

开发者也可直接使用 Alembic：

    alembic -c alembic.ini -x db_url=sqlite:///E:/path/catalog.db upgrade head
    alembic -c alembic.ini -x db_url=sqlite:///E:/path/catalog.db check

## 知识图谱导入（维护操作）

真实 Neo4j dump 的载入、只读导出、staging、审核和发布流程统一维护在
[本地 Neo4j 工具说明](../../../tools/neo4j/README.md)。导入验收结果以
[知识图谱实施报告](../../implementation/reports/data-platform/v1-knowledge-graph-import.md)
为准。`knowledge import` 只写 staging；校验失败时不得发布。

## 运行记录与查询（维护操作）

`run` 默认在开始/结束边界批量写运行记录到同一数据库
（`var/catalog/catalog.db`）；仿真循环内不写库。写库失败只会在运行报告
标记持久化失败，不影响仿真结果。可用 `--run-records off` 关闭。

    flexible-factory report runs
    flexible-factory report run --run-id <run_id>
    flexible-factory report genealogy --run-id <run_id> --uid <final_uid>
    flexible-factory report waits --run-id <run_id> --resource arm_A
    flexible-factory report timeline --run-id <run_id> --instance-id <id>

不传 `--run-id` 时使用最近一次运行。四个命令分别对应：

- `run`：运行汇总与验收（Q1）；
- `genealogy`：FINAL 物料谱系（Q2）；
- `waits`：资源等待/竞争（Q3）；
- `timeline`：工序时间线与失败取证（Q4）。

数据库可直接用 SQLite 查询；记录全部保留，当前没有自动清理命令。需要
CoppeliaSim 的 `full_flow` 与运行记录联合验收不在本机维护命令范围内。

## 数据库可视化（维护操作）

生成一个自包含 HTML 仪表盘（不依赖外部 CDN 或服务端）：

```powershell
uv run python .\tools\db_dashboard.py `
  --db .\var\catalog\catalog.db `
  --out .\docs\implementation\runs\db_dashboard.html `
  --open
```

仪表盘显示：

- 知识图谱（SQLite 知识表的按类型分组视图、节点/关系表格）；
- 实体类型与关系谓词计数；
- 运行记录列表、Q4 工序时间线、Q3 资源等待、Q2 物料谱系；
- **工艺与资源定义（目录）**：工序依赖 DAG（点击工序查看端口/资源/候选设备/工步）、
  路线卡片、互斥资源申请顺序、设备与能力、区域容量、物料类型、订单入口、
  场景绑定；
- 定义发布版本、知识来源与抽取状态。

工艺与资源定义来自已发布 release；如果数据库还没有相关数据，先执行：

```powershell
uv run flexible-factory catalog import --db .\var\catalog\catalog.db
uv run flexible-factory catalog publish --db .\var\catalog\catalog.db --release-id <release_id>
```

页面是只读快照，数据变化后重新运行生成命令。图谱也可直接在 Neo4j
Browser（`http://localhost:7474`）查看，例如：

```cypher
MATCH p=()-[]->() RETURN p LIMIT 100;
MATCH (i:Instance)-[:achieve_goal]->(g:Goal) RETURN i, g LIMIT 50;
```

Browser 使用本地 Neo4j 容器的用户名/密码；SQLite 仪表盘不需要 Docker。

## 迁移与重建（维护操作）

从历史 stage4 场景包生成 v1 副本（幂等；来源只读）：

    flexible-factory migrate \
      --source docs/scene_artifacts/stage4/L5_UFLOW/v004_20261029_133249 \
      --note "v1 migration"

迁移流程：校验来源 manifest/scene 摘要 → 复制场景 → 生成 v1 manifest 与
`extra.v1` 契约 → 重载并全量校验 → 原子更新 `v1/current.json`。任一步失败
都不更新 current，也不修改来源场景包。

从历史 stage3 父场景显式重建 v1 场景（高级维护）：

    flexible-factory build \
      --parent docs/scene_artifacts/stage3/L2_MIRROR/v008_20261029_112305 \
      --save --note "v1 rebuild"

`build` 加载父场景并增加 C/D/E 与物流线；`--save` 保存新 v1 版本。
每次保存创建新版本，不覆盖已验收场景包。

## 启动阶段计时（性能诊断）

`run` 从 ZMQ 连接、加载工件、启动点校验、组件构建到 `start_stepping` 的耗时
可用仓库内脚本单独测量。脚本复现 `run` 的启动路径，在仿真刚启动时立即
停止，不执行场景循环：

    uv run python tools/measure_startup_stages.py

常用选项：

    # 使用当前已加载场景, 不重新 load
    uv run python tools/measure_startup_stages.py --no-load

    # 不写运行记录, 避免在 catalog.db 留下 ABORTED 记录
    uv run python tools/measure_startup_stages.py --run-records off

    # 启动后额外运行传送带驱动探测 (会步进仿真约 35 次)
    uv run python tools/measure_startup_stages.py --probe

- 计时阶段：`process_import`、`batch_resolve`、`catalog_resolve`（仅
  `--catalog-source db`）、`logger_setup`、`zmq_connect`、`lifecycle_init`、
  `artifact_load`、`prepare_start_validation`、`run_record_init`、
  `build_components`、`snapshot_write`、`simulation_start`；`build_components`
  另给出 `build.base_components`、`build.ik_initialize_total`、
  `build.shadow_runtime`、`build.suction_controllers` 子项（与主项重叠）。
  `--probe` 时追加 `post_start.conveyor_drive_probe`。
- 结果输出到 stdout 表格和
  `docs/implementation/runs/startup_timing_<stamp>.json`。停仿真、销毁影子臂、
  结束运行记录/生命周期等清理动作单独计时，不计入“进程开始 → 仿真启动”总计。
- 脚本与 `run` 一样会加载 `v1/current.json` 指向的工件（除非 `--no-load`），
  会替换 CoppeliaSim 当前场景；先确认没有未保存的 GUI 改动。
- 默认 `--run-records on` 会写一条 RUNNING 记录，并在清理时标记为 `ABORTED`；
  不需要该记录时使用 `--run-records off`。
- 脚本不是 pytest 钩子，也不会被 `flexible-factory` 自动调用；运行前需启动
  CoppeliaSim 和 ZMQ 23000，并先从仓库根目录执行 `uv sync`。

## 产物与日志

- 场景包：`docs/scene_artifacts/v1/L5_UFLOW/vNNN_<stamp>/`
- current 指针：`docs/scene_artifacts/v1/current.json`
- 运行报告：`docs/implementation/runs/v1_run_<scenario>_<stamp>.json`
- 默认日志：仓库根目录 `flexible_factory.log`（`--log` 可覆盖）
- 校验报告：`docs/implementation/runs/scene_validation_<artifact>_<stamp>.json`
- 启动计时报告：`docs/implementation/runs/startup_timing_<stamp>.json`
  （由 `tools/measure_startup_stages.py` 生成）

## 排查

- 连接失败：检查 CoppeliaSim 是否运行、ZMQ add-on 是否启动、端口是否为 23000。
- 找不到场景包：运行 `flexible-factory list`，确认 `v1/current.json`。
- 场景未通过校验：`run` 会拒绝并给出差异数；先 `load`，再运行可选
  `validate` 查看详细报告。不要编辑受保护场景包绕过摘要检查。
- 运行中残留锁：`flexible-factory status` 显示 lock owner；确认进程已退出后
  `stop`，生命周期会清理陈旧锁。
- 迁移来源场景包不匹配：v1 运行期不比较 stage2/stage3 当前配置摘要，只把来源
  记录在 provenance；若保留的来源场景包被修改，迁移时摘要校验会失败。

## 维护约定

本文件是 v1 启动、场景包生命周期和运行方法的操作手册。改动启动/连接方式、
常见 CLI 操作、运行模式或场景包派生规则时，必须同步更新本文件，并核对 README、
场景包说明与实现保持一致。新增、删除或修改 CLI 命令/参数时，还必须同步更新
[CLI 参数手册](CLI_REFERENCE.md)，并用受影响命令的 `--help` 核对参数表。新增
可运行模式时，补充其前置场景包、加载步骤、
运行命令和输出位置。新增 `catalog` 定义数据库、知识图谱或运行记录命令时，同步更新
本节、[资源目录数据库化实施报告](../../implementation/reports/data-platform/v1-catalog-persistence.md)、
[知识图谱导入实施报告](../../implementation/reports/data-platform/v1-knowledge-graph-import.md)
与[运行记录实施报告](../../implementation/reports/data-platform/v1-run-record-persistence.md)。
修改 `tools/measure_startup_stages.py` 的启动路径、计时阶段名或输出字段时，
同步更新本文件的“启动阶段计时”一节。
