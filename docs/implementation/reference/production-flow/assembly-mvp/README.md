# assembly_mvp 混线装配 MVP 输入与复现资料

本目录保存 [Neo4j 图谱混线装配 MVP 方案](../../../archive-plans/production-flow/v1-neo4j-assembly-mixed-line-mvp.md)
WP0–WP3 的订单 fixture 和复现说明。图谱来源见
[Neo4j 图数据导出](../../data-platform/neo4j-graph-export/README.md) 的
`fmpmm_graph.json`；本目录不复制图谱全文，只记录被采用的最小子图事实。

## 1. 图谱事实与 MVP 映射 (WP0)

| 图谱节点/关系 | 节点 ID | MVP 解释 |
|---|---|---|
| `冰箱压缩机装配体` `include` `冰箱压缩机`、`冰箱压缩机底板` | `…:23` → `…:17`、`…:18` | `COMPRESSOR + BASEPLATE → COMPRESSOR_ASSY` |
| `前后连接法兰装配体` `include` `前后连接法兰组件` | `…:21` → `…:19` | `FRONT_FLANGE + REAR_FLANGE → FLANGE_ASSY` |
| `前后连接法兰组件` `include` `前连接法兰`、`后连接法兰` | `…:19` → `…:12`、`…:13` | 法兰组件的两个输入件 |
| `冰箱压缩机`/`冰箱压缩机底板` `achieve_goal` `实现轴孔装配` | `…:17`、`…:18` → Goal | 两个来料在压缩机岛齐套后执行逻辑轴孔装配 |
| `前后连接法兰组件` `achieve_goal` `实现螺纹装配` | `…:19` → Goal | 法兰岛逻辑组件装配的业务解释 |
| 轴孔装配目标关联抓取基准件、提起/翻转、抓取配合件、对齐、装配、释放及 `after` | Goal/Operation 子图 | 首版只保留为工序说明/事件阶段; 执行器折叠为 `mvp_pair_assembly` 一个逻辑提交 |

节点 ID 前缀均为 `4:845a2a8e-fc6c-4c47-8432-913fc44ff075`。产品编码分别为
`TYJA0001`（压缩机装配体）、`TFLA0001`（前后连接法兰装配体）、
`ZYJA0001`（压缩机）、`ZDBB0001`（压缩机底板）、`LFLQ0001`（前法兰）、
`LFLH0001`（后法兰）。

**显式业务假设**（不从空字段推导）：

1. MVP 只取两个装配体的两个命名组件作为输入；图谱中
   `前后连接法兰装配体 include 螺栓` 不在首版四槽两岛范围内，螺栓装配和
   螺纹运动不实现。
2. 图谱的文本型设备、环境、异常、注意事项字段不作为派工或运动约束；
   节拍、可达性、健康、公差没有可靠数值来源，不进入仿真事实。
3. 两个产品共用同一 `L5_UFLOW` 底图上的**两个独立岛**；订单切换决定运行
   哪座岛，不表示两条路线可任意混配或同时派工。
4. “逻辑装配”只做物料账本原子消费和成品登记，不声明真实抓取、轴孔插装
   或螺纹质量。

## 2. 订单 fixture

| 文件 | 目标 | 输入 (区域 → 端口) | 成品区域 |
|---|---|---|---|
| `order_compressor.json` | `COMPRESSOR_ASSY` | `mvp_source_compressor → compressor-in`、`mvp_source_baseplate → baseplate-in` | `mvp_output_compressor_assy` |
| `order_flange.json` | `FLANGE_ASSY` | `mvp_source_front_flange → front-in`、`mvp_source_rear_flange → rear-in` | `mvp_output_flange_assy` |

两份 fixture 都只有一个逻辑节点 `assemble`，没有边。订单输入直接绑定
`mvp_pair_assembly` 模板端口；这是首版**唯一**允许来料直接进入逻辑合流的
处理器（`DIRECT_INPUT_JOIN_HANDLERS`）。v1 的 `kit_join`/`e_material_pair`
仍要求前置工序供给。

## 3. 场景工件与运行命令

实验工件根与受保护 v1 基线分离：

```text
docs/scene_artifacts/experiments/assembly-mvp/
  v1/current.json
  v1/L5_UFLOW/v001_20261114_120734/   # 历史工件, 保留
  v1/L5_UFLOW/v002_20261121_152334/   # 当前工件 (当前 config)
```

`v001` 的 manifest 使用 `v1_process_20261114a`/config `e292c37f…`，与
2026-11-21 的当前代码 `v1_process_20261117a`/config `c132340c…` 不一致，
`order start` 会在启动前只读校验拒绝（`v1_config_digest`、
`v1_process_model_version`）。2026-11-21 已按当前配置从 stage3
`L2_MIRROR/v008_20261029_112305` 重新 build/save 为 `v002`；它不包含
`Cart2`，MVP 订单仍使用 `single`。旧 `v001` 未被覆盖。

构建（需要 CoppeliaSim + ZMQ；先确认当前会话没有未保存的 GUI 改动）：

```powershell
flexible-factory build `
  --parent docs/scene_artifacts/stage3/L2_MIRROR/v008_20261029_112305 `
  --profile assembly_mvp `
  --artifact-root docs/scene_artifacts/experiments/assembly-mvp `
  --save --note "assembly_mvp WP1"
```

订单（纯校验不需要 CoppeliaSim）：

```powershell
flexible-factory order validate --file docs/implementation/reference/production-flow/assembly-mvp/order_compressor.json --db var/catalog/catalog.db
flexible-factory order create   --file docs/implementation/reference/production-flow/assembly-mvp/order_compressor.json --source fixture --db var/catalog/catalog.db
flexible-factory order preview  --order-id mvp-compressor-001 --version 1 --db var/catalog/catalog.db
```

运行（`--artifact-root` 必须指向实验工件根；每次 `order start` 都重新加载
工件，因此上一笔的输入/成品不会复用）：

```powershell
flexible-factory order start --order-id mvp-compressor-001 --version 1 `
  --artifact-id current `
  --artifact-root docs/scene_artifacts/experiments/assembly-mvp `
  --time 300 --run-records on
```

混线演示固定顺序为“压缩机 → 法兰 → 压缩机”，使用三个独立订单或独立
订单版本，不并发、不共享在制物料。每次运行的报告在
`docs/implementation/runs/v1_order_run_<stamp>.json`，订单状态在
`var/catalog/catalog.db`。

## 4. 预期结果

| 订单 | 输入 UID（按编译顺序） | 成品 UID | 父 UID（端口顺序） | 事件 |
|---|---|---|---|---|
| 压缩机 | `1=BASEPLATE`、`2=COMPRESSOR` | `3` | `(2, 1)` | `FINAL_CREATED` 含 `compressor-in/baseplate-in`、`handler_id=mvp_pair_assembly` |
| 法兰 | `1=FRONT_FLANGE`、`2=REAR_FLANGE` | `3` | `(1, 2)` | `FINAL_CREATED` 含 `front-in/rear-in`、`handler_id=mvp_pair_assembly` |

UID 由编译器按订单输入 `input_id` 排序分配；成品父 UID 顺序由模板端口顺序
决定。两者不可混为“先到先配”。

## 5. 明确不做

- 不读取 Neo4j 查询服务；不实现通用图谱→配方编译器或用户可编辑工艺。
- 不实现真实轴孔/螺纹装配、抓取姿态、视觉、公差或节拍标定。
- 不实现多订单并发、工位互斥、AGV 新路线或真实装配岛机器人。
- 不覆盖受保护 v1 场景包；`--artifact-root` 必须显式指向实验目录。