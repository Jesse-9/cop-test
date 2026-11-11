---
title: Flexible Factory CLI 参数参考
type: reference
tags:
  - 类别/案例
  - 难度/中级
created: 2026-11-10
updated: 2026-11-11
status: published
---

# Flexible Factory CLI 参数参考

本文逐项说明 `flexible-factory` 命令、选项、默认值和可选值。常见业务流程、
CoppeliaSim 准备和运行产物说明见[项目使用指南](USAGE.md)。

参数定义以 [`src/flexible_factory/cli.py`](../../../src/flexible_factory/cli.py)
中的 `build_parser()` 为准；动态选项的值以对应实现为准。运行中的 `--help`
由同一 argparse 定义生成，可用来确认当前安装版本：

```powershell
flexible-factory --help
flexible-factory run --help
flexible-factory order --help
flexible-factory order start --help
```

任何参数表与当前 `--help` 不一致时，先核对安装包版本和代码版本；不要据此
猜测参数行为。

## 语法与通用规则

```text
flexible-factory [--version] <command> [options]
flexible-factory <command> <subcommand> [options]
```

- `<command>` 和 `<subcommand>` 是下文列出的命令名，不带尖括号输入。
- 方括号表示可省略；`必填` 表示必须提供；选项取值用 `a | b` 表示。
- 路径相对当前工作目录解析。带空格的路径请用引号包住。
- `--help` 可用于根命令及各级子命令，例如 `flexible-factory report timeline --help`。
- JSON 文件参数通常从文件读取；`order validate` 和 `order plan` 的 `--file -`
  表示从标准输入读取 JSON。
- 命令成功时通常输出 JSON；错误写入标准错误。退出码由命令结果决定，不能只
  根据是否打印 JSON 判断成功。

## 根命令

| 命令/选项 | 说明 |
|---|---|
| `--help` | 显示帮助并退出。 |
| `--version` | 显示产品名和 CLI 版本并退出。当前版本来自包元数据。 |

示例：

```powershell
flexible-factory --help
flexible-factory --version
```

## 场景包生命周期与运行

`list`、`status`、`load`、`validate`、`stop`、`migrate`、`build`、`run` 使用
以下共享选项：

| 选项                     | 默认值           | 说明                   |
| ---------------------- | ------------- | -------------------- |
| `--host TEXT`          | `localhost`   | CoppeliaSim ZMQ 主机。  |
| `--port INT`           | `23000`       | CoppeliaSim ZMQ 端口。  |
| `--artifact-root PATH` | 程序默认 v1 场景包根目录 | 覆盖 v1 场景包根目录。         |
| `--actor TEXT`         | `cli`         | 记录在生命周期/迁移操作中的操作者标识。 |

### `list`

列出 v1 场景包。仅接受上述共享选项。

```powershell
flexible-factory list
flexible-factory list --artifact-root .\docs\scene_artifacts\v1
```

### `status`

查看生命周期状态。默认连接 CoppeliaSim；`--offline` 只读取本地会话状态。

| 选项 | 默认值 | 说明 |
|---|---|---|
| `--offline` | 关闭 | 不连接 CoppeliaSim。 |

```powershell
flexible-factory status
flexible-factory status --offline
```

### `load`

加载指定场景包，不执行 build 或清场。

| 选项 | 默认值 | 说明 |
|---|---|---|
| `--artifact TEXT` | `current` | 场景包引用；`current` 表示 current 指针指向的场景包，也可使用场景包标识/引用。 |

```powershell
flexible-factory load
flexible-factory load --artifact current
```

### `validate`

只读校验场景。默认先加载 `--artifact` 指定的场景包；`--no-load` 校验当前已加载
场景。

| 选项 | 默认值 | 说明 |
|---|---|---|
| `--artifact TEXT` | `current` | 默认加载并校验此场景包。 |
| `--no-load` | 关闭 | 跳过加载，校验当前已加载场景。 |

```powershell
flexible-factory validate
flexible-factory validate --no-load
```

### `stop`

停止仿真并确认停止态。

| 选项 | 默认值 | 说明 |
|---|---|---|
| `--timeout FLOAT` | `10.0` 秒 | 等待停止确认的超时。 |

```powershell
flexible-factory stop --timeout 20
```

### `run`

运行场景。正式用户入口为 `full_flow`；其他场景仅用于内部回归/诊断。

| 选项 | 默认值 | 可选值/说明 |
|---|---|---|
| `--artifact TEXT` | `current` | 要加载的 v1 场景包引用。 |
| `--no-load` | 关闭 | 使用当前已加载场景，不重新加载。 |
| `--scenario TEXT` | `full_flow` | `full_flow`、`no_pair`、`agv_priority`、`agv_blocked`、`conveyor_fault`、`staggered_batch`。正式入口仅 `full_flow`。 |
| `--fault TEXT` | `none` | `none`、`agv_blocked`、`conveyor_stop`。 |
| `--batch-profile TEXT` | `default` | `default`、`direct_heavy`、`detour_heavy`。非默认配比仅可用于 `full_flow`。 |
| `--time FLOAT` | `1200.0` 秒 | 本次运行的仿真时间上限。 |
| `--log PATH` | `flexible_factory.log` | 日志文件路径。 |
| `--out-dir PATH` | `docs/implementation/runs` | 运行报告输出目录。 |
| `--catalog-source TEXT` | `code` | `code` 使用代码内静态工艺与资源定义（`ProcessCatalog`）；`db` 使用 SQLite 已发布定义，并要求与代码定义等价。 |
| `--catalog-db PATH` | `var/catalog/catalog.db` | 目录/运行记录数据库路径。 |
| `--run-records TEXT` | `on` | `on` 或 `off`；控制是否批量写入运行记录。 |

```powershell
flexible-factory run --scenario full_flow --time 700
flexible-factory run --scenario full_flow --batch-profile direct_heavy
flexible-factory run --scenario full_flow --catalog-source db `
  --catalog-db .\var\catalog\catalog.db --run-records off
```

批次 profile 的物料数量及限制见[项目使用指南](USAGE.md)的“命名批次配比”部分。

### `migrate`

从历史 stage4 `L5_UFLOW` 场景包生成 v1 场景包。来源只读。

| 选项 | 默认值 | 说明 |
|---|---|---|
| `--source PATH` | 必填 | stage4 来源场景包目录。 |
| `--note TEXT` | 空字符串 | 写入迁移记录的备注。 |
| `--force` | 关闭 | 即使 current 已指向同一来源也创建新版本。 |

```powershell
flexible-factory migrate `
  --source .\docs\scene_artifacts\stage4\L5_UFLOW\v004 `
  --note "v1 migration" --actor engineer
```

### `build`

从历史 stage3 父场景显式构建 v1 场景。

| 选项 | 默认值 | 说明 |
|---|---|---|
| `--parent PATH` | 必填 | 历史 stage3 场景包目录，或该场景包内受支持的文件。 |
| `--layout PATH` | 不使用覆盖布局 | 可选布局 JSON 文件。 |
| `--save` | 关闭 | 构建后立即保存为新的 v1 场景包。 |
| `--note TEXT` | 空字符串 | 保存时使用的备注。 |

```powershell
flexible-factory build --parent .\docs\scene_artifacts\stage3\L2_MIRROR\v008
flexible-factory build --parent .\path\to\parent --save --note "v1 rebuild"
```

## 静态工艺与资源定义数据库（CLI 名称：`catalog`）

本节所说的静态工艺与资源定义（下文简称“目录”）包括代码中可复用的配方、工序、
路线、设备、能力和区域定义；它不是文件系统目录，也不是运行中的物料与设备状态。
`catalog` 是对应的 CLI 命令名。所有 `catalog` 子命令都接受 `--db PATH`，默认
`var/catalog/catalog.db`。

| 命令 | 选项 | 默认值/可选值 | 说明 |
|---|---|---|---|
| `catalog init` | `--db PATH` | 默认路径 | 创建或升级数据库 schema。 |
| `catalog import` | `--release-id TEXT` | 自动生成 | 导入代码默认定义为 staging release。 |
|  | `--catalog-version TEXT` | 当前代码定义版本 | 留空时使用代码定义版本；指定值必须与代码定义版本一致。 |
| `catalog publish` | `--release-id TEXT` | 必填 | 校验并发布指定 staging release。 |
| `catalog status` | `--release-id TEXT` | 当前发布版本 | 查看指定 release 或当前发布版本。 |
| `catalog diff` | `--release-id TEXT` | 当前发布版本 | 对比代码默认定义与数据库已发布定义。 |
| `catalog verify` | `--release-id TEXT` | 当前发布版本 | 校验数据库结构、外键和定义往返。 |

除表中选项外，每条命令都接受 `--db PATH`。

```powershell
flexible-factory catalog init
flexible-factory catalog import --db .\var\catalog\catalog.db
flexible-factory catalog publish --db .\var\catalog\catalog.db `
  --release-id rel_v1_process_20261106a
flexible-factory catalog diff
```

## 知识图谱导入

所有 `knowledge` 子命令接受 `--db PATH`，默认 `var/catalog/catalog.db`。

| 命令                  | 选项                         | 默认值/可选值 | 说明                                |
| ------------------- | -------------------------- | ------- | --------------------------------- |
| `knowledge import`  | `--export PATH`            | 必填      | `tools/neo4j_export.py` 生成的 JSON。 |
|                     | `--source-dump PATH`       | 必填      | 原始 Neo4j dump，仅计算摘要，不解析。          |
|                     | `--extractor-version TEXT` | 当前导出器版本 | 导出器版本；相同来源/版本的重跑需保持幂等。            |
| `knowledge publish` | `--extraction-id TEXT`     | 必填      | 审核后发布指定 staging extraction。       |
| `knowledge status`  | `--extraction-id TEXT`     | 最近一次    | 查看指定或最近一次抽取及计数。                   |
| `knowledge verify`  | `--extraction-id TEXT`     | 最近一次    | 校验指定或最近一次抽取。                      |

所有子命令另接受 `--db PATH`。

```powershell
flexible-factory knowledge import `
  --export .\var\knowledge\export.json `
  --source-dump .\var\knowledge\source.dump `
  --db .\var\catalog\catalog.db
flexible-factory knowledge status
```

## 运行报告查询

所有 `report` 子命令接受 `--db PATH`，默认 `var/catalog/catalog.db`。

| 命令 | 选项 | 默认值/可选值 | 说明 |
|---|---|---|---|
| `report runs` | `--limit INT` | `50` | 返回的运行记录数量上限。 |
| `report run` | `--run-id TEXT` | 最近一次运行 | 查询指定运行或最近一次运行。 |
| `report genealogy` | `--run-id TEXT` | 最近一次运行 | `--uid INT` 必填；查询成品物料谱系。 |
| `report waits` | `--run-id TEXT` | 最近一次运行 | `--resource TEXT` 可选；限制到指定资源。 |
| `report timeline` | `--run-id TEXT` | 最近一次运行 | `--instance-id TEXT` 可选；限制到指定工序实例。 |

示例：

```powershell
flexible-factory report runs --limit 20
flexible-factory report genealogy --run-id run-123 --uid 42
flexible-factory report waits --resource arm_A
flexible-factory report timeline --run-id run-123 --instance-id op-1
```

## 订单 DAG

所有 `order` 子命令接受以下共享选项：

| 选项 | 默认值 | 可选值/说明 |
|---|---|---|
| `--db PATH` | `var/catalog/catalog.db` | 订单、目录及运行记录所用 SQLite 数据库。 |
| `--catalog-source TEXT` | `code` | `code` 使用代码内静态工艺与资源定义；`db` 使用已发布数据库定义，且必须与代码定义等价。 |

| 命令 | 参数 | 默认值/可选值 | 说明 |
|---|---|---|---|
| `order validate` | `--file PATH` | 必填 | 订单 JSON 文件；`-` 从 stdin 读取。 |
| `order create` | `--file PATH` | 必填 | 创建首版本订单。 |
|  | `--source TEXT` | `manual`；`manual`、`planner`、`fixture` | 订单来源标识。 |
|  | `--note TEXT` | 空字符串 | 版本备注。 |
| `order version` | `--order-id TEXT`、`--file PATH` | 均必填 | 为订单创建新版本。 |
|  | `--note TEXT` | 空字符串 | 版本备注。 |
| `order list` | `--limit INT` | `50` | 订单数量上限。 |
| `order show` | `--order-id TEXT` | 必填 | `--version INT` 默认 `0`；`0` 表示查看订单，否则查看指定版本。 |
| `order preview` | `--order-id TEXT`、`--version INT` | 均必填 | 预览已保存版本的编译结果。 |
| `order plan` | `--file PATH` | 必填 | Planner 输入 JSON；`-` 从 stdin 读取。 |
|  | `--save` | 关闭 | 保存为 `source=planner` 的订单版本；省略时只规划并输出结果。 |
|  | `--note TEXT` | 空字符串 | 保存草稿时的版本备注。 |
| `order start` | `--order-id TEXT`、`--version INT` | 均必填 | 同步运行指定订单版本。 |
|  | `--artifact-id TEXT` | `current` | 运行绑定的场景包；`current` 在服务端解析为具体 `artifact_id`。 |
|  | `--time FLOAT` | `1200.0` 秒 | 仿真时间上限。 |
|  | `--run-records TEXT` | `on`；`on`、`off` | 是否保存运行记录。 |
|  | `--log PATH` | `flexible_factory.log` | 日志路径。 |
|  | `--out-dir PATH` | `docs/implementation/runs` | 报告输出目录。 |
|  | 共享仿真选项 | 见上文 | `--host`、`--port`、`--artifact-root`、`--actor` 仅此子命令额外支持。 |
| `order status` | `--run-id TEXT` | 必填 | 查询运行状态。 |
| `order nodes` | `--run-id TEXT` | 必填 | 查询运行节点快照。 |
| `order events` | `--run-id TEXT` | 必填 | `--limit INT` 默认 `2000`；事件数量上限。 |
| `order stop` | `--run-id TEXT` | 必填 | 停止活动订单运行。 |
| `order runs` | `--order-id TEXT` | 空字符串 | 可选订单过滤条件；默认列出全部。 |
|  | `--limit INT` | `50` | 运行记录数量上限。 |

订单查询示例：

```powershell
flexible-factory order validate --file .\order.json
flexible-factory order create --file .\order.json --source manual --note "first version"
flexible-factory order show --order-id order-1 --version 1
flexible-factory order start --order-id order-1 --version 1 --time 700
```

### `api serve`

启动本地订单 HTTP 服务。该命令使用自身的 `--host`/`--port` 作为 HTTP 监听地址；
`--sim-host`/`--sim-port` 单独设置 CoppeliaSim ZMQ 地址。

| 选项 | 默认值 | 可选值/说明 |
|---|---|---|
| `--host TEXT` | `127.0.0.1` | HTTP 监听主机。 |
| `--port INT` | `8080` | HTTP 监听端口。 |
| `--sim-host TEXT` | `localhost` | CoppeliaSim ZMQ 主机。 |
| `--sim-port INT` | `23000` | CoppeliaSim ZMQ 端口。 |
| `--db PATH` | `var/catalog/catalog.db` | SQLite 数据库路径。 |
| `--catalog-source TEXT` | `code`；`code`、`db` | 订单模板定义来源；`db` 要求数据库发布定义与代码定义等价。 |
| `--artifact-root PATH` | 程序默认 v1 场景包根目录 | v1 场景包根目录。 |
| `--artifact-id TEXT` | `current` | 运行绑定的场景包。 |
| `--run-records TEXT` | `on`；`on`、`off` | 是否保存运行记录。 |
| `--log PATH` | `flexible_factory.log` | 日志路径。 |
| `--out-dir PATH` | `docs/implementation/runs` | 报告输出目录。 |

```powershell
flexible-factory api serve --host 127.0.0.1 --port 8080 `
  --sim-host localhost --sim-port 23000
```

## 参数维护约定

`cli.py` 的 argparse 定义是参数名、类型、必填性、默认值和 choices 的唯一权威
来源；使用指南维护流程级说明和常用示例，本手册维护完整参数清单。每次新增、
删除或修改 CLI 命令/参数时，必须：

1. 同步更新本手册对应命令的参数表、默认值、可选值和示例。
2. 若改变常见操作流程或用户可见运行行为，同步更新
   [项目使用指南](USAGE.md)；不要把完整参数表复制进操作指南。
3. 更新 [`docs/README.md`](../../README.md) 和仓库根目录 `README.md` 中的入口
   链接（若 CLI 文档入口路径变化）。
4. 执行受影响命令的 `--help`，核对参数名和必填/可选关系；choices 或默认值
   来自动态常量时，同时检查对应实现，例如 `batch_profile_names()`。

命令树发生变化时，也应更新本手册中的命令总览和适用范围，确保继承/共享选项
没有被遗漏。
