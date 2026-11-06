---
title: 柔性工业车间 v1 使用指南
type: tutorial
tags:
  - coppeliasim
  - 类别/基础知识
  - 难度/初级
created: 2026-10-26
updated: 2026-11-06
status: published
sim_version: "4.10+"
---

# 柔性工业车间 v1 使用指南

本指南说明 CoppeliaSim、ZMQ 连接、v1 工件操作和 `full_flow` 运行。命令以
Windows PowerShell、仓库根目录为例。v1 不支持历史阶段入口；历史实现、报告
与证据已清理，结论见 [V1 实施报告](../../implementation/reports/v1-product-implementation.md)。

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

## v1 工件与当前基线

v1 工件位于独立命名空间：

    docs/scene_artifacts/v1/
      current.json
      L5_UFLOW/v001_<timestamp>/
        scene.ttt
        manifest.json
        validation.json
        lifecycle.jsonl

当前 v1 工件为 `v1/L5_UFLOW/v001_20261103_004317`，由已验收 stage4
`stage4/L5_UFLOW/v004_20261029_133249` 行为等价迁移。仅保留该 stage4 工件
及其 stage3 上游 `L2_MIRROR/v008_20261029_112305` 作为只读迁移来源，禁止
覆盖；其他历史工件已清理。v1 工件受摘要、生命周期和只读保护；不得手工修改
manifest 或用 GUI 覆盖 `scene.ttt`。

## 日常运行

    flexible-factory load
    flexible-factory validate      # 可选诊断
    flexible-factory run --scenario full_flow --time 700

- `load` 只加载 `v1/current.json` 指向的工件，不构建、不清场，加载后可直接运行。
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
  同时投入 2 个 `RAW_B` 可能使后续壳体等待 AGV 空载调机达到 300 s 背压
  上限；该边界记录在实施报告中。
- 批次只改变本次运行的 `RAW_A/RAW_B/RAW_D` 数量，不改变工艺路线、设备
  候选、场景工件或 `config_digest`；非 `default` profile 只用于
  `full_flow`。运行快照和报告记录实际生效的批次及
  `BATCH_INPUT_VERSION`。
- 批次配比只展示路线混批能力，不表示同一 UID 可在运行中动态选路。
  默认与两个 profile 的实机验收结果见
  [实施报告](../../implementation/reports/v1-mixed-production-flexibility-demo.md)。

其他查询/控制命令：

    flexible-factory status
    flexible-factory status --offline
    flexible-factory list
    flexible-factory stop

## 静态目录数据库（维护操作）

静态资源/工艺目录可持久化到本地 SQLite；默认路径
`var/catalog/catalog.db`（不写入受保护的场景工件目录）。该功能不影响默认
运行：`run` 仍读取代码内默认目录，只有显式 `--catalog-source db` 才切换。

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

    # 显式使用已发布数据库目录运行 full_flow
    flexible-factory run --scenario full_flow --time 700 --catalog-source db

`catalog diff` 必须输出 `"ok": true` 且差异为空；`run --catalog-source db`
也会在连接 CoppeliaSim 前执行同样的等价检查，不一致则拒绝启动。数据库不可用
或没有已发布 release 时不会回退到代码目录，错误为 `CATALOG_*`。需要
CoppeliaSim 的 `full_flow` 验收不在目录数据库维护命令范围内。

开发者也可直接使用 Alembic：

    alembic -c alembic.ini -x db_url=sqlite:///E:/path/catalog.db upgrade head
    alembic -c alembic.ini -x db_url=sqlite:///E:/path/catalog.db check

## 知识图谱导入（维护操作）

真实 Neo4j dump 的载入、只读导出、staging、审核和发布流程统一维护在
[本地 Neo4j 工具说明](../../../tools/neo4j/README.md)。导入验收结果以
[知识图谱实施报告](../../implementation/reports/v1-knowledge-graph-import.md)
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
- **工艺目录**：工序依赖 DAG（点击工序查看端口/资源/候选设备/工步）、
  路线卡片、互斥资源申请顺序、设备与能力、区域容量、物料类型、订单入口、
  场景绑定；
- 目录发布版本、知识来源与抽取状态。

工艺目录来自已发布 release；如果数据库还没有工艺数据，先执行：

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

从历史 stage4 工件生成 v1 副本（幂等；源工件只读）：

    flexible-factory migrate \
      --source docs/scene_artifacts/stage4/L5_UFLOW/v004_20261029_133249 \
      --note "v1 migration"

迁移流程：校验来源 manifest/scene 摘要 → 复制场景 → 生成 v1 manifest 与
`extra.v1` 契约 → 重载并全量校验 → 原子更新 `v1/current.json`。任一步失败
都不更新 current，也不修改源工件。

从历史 stage3 父场景显式重建 v1 场景（高级维护）：

    flexible-factory build \
      --parent docs/scene_artifacts/stage3/L2_MIRROR/v008_20261029_112305 \
      --save --note "v1 rebuild"

`build` 加载父场景并增加 C/D/E 与物流线；`--save` 保存新 v1 版本。
每次保存创建新版本，不覆盖已验收工件。

## 产物与日志

- 工件：`docs/scene_artifacts/v1/L5_UFLOW/vNNN_<stamp>/`
- current 指针：`docs/scene_artifacts/v1/current.json`
- 运行报告：`docs/implementation/runs/v1_run_<scenario>_<stamp>.json`
- 默认日志：仓库根目录 `flexible_factory.log`（`--log` 可覆盖）
- 校验报告：`docs/implementation/runs/scene_validation_<artifact>_<stamp>.json`

## 排查

- 连接失败：检查 CoppeliaSim 是否运行、ZMQ add-on 是否启动、端口是否为 23000。
- 找不到工件：运行 `flexible-factory list`，确认 `v1/current.json`。
- 场景未通过校验：`run` 会拒绝并给出差异数；先 `load`，再运行可选
  `validate` 查看详细报告。不要编辑受保护工件绕过摘要检查。
- 运行中残留锁：`flexible-factory status` 显示 lock owner；确认进程已退出后
  `stop`，生命周期会清理陈旧锁。
- 迁移来源工件不匹配：v1 运行期不比较 stage2/stage3 当前配置摘要，只把来源
  记录在 provenance；若保留的来源工件被修改，迁移时摘要校验会失败。

## 维护约定

本文件是 v1 启动、工件生命周期和运行方法的操作手册。改动启动/连接方式、
CLI 参数、运行模式或工件派生规则时，必须同步更新本文件，并核对 README、
场景工件说明与实现保持一致。新增可运行模式时，补充其前置工件、加载步骤、
运行命令和输出位置。新增目录数据库、知识图谱或运行记录命令时，同步更新
本节、[资源目录数据库化实施报告](../../implementation/reports/v1-catalog-persistence.md)、
[知识图谱导入实施报告](../../implementation/reports/v1-knowledge-graph-import.md)
与[运行记录实施报告](../../implementation/reports/v1-run-record-persistence.md)。
