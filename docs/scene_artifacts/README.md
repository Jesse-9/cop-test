# 场景版本包

本目录保存可版本化、可校验和可追溯的 CoppeliaSim 场景包。每个场景包以
`scene.ttt` 为主体，并附带 `manifest.json`、校验结果和生命周期记录。代码和
磁盘路径中的 `artifact` / `scene_artifacts` 是历史内部名称；这里的“场景包”
不是生产流程中的在制品或零部件。v1 只使用 `v1/` 命名空间；旧阶段场景包
仅作为 v1 迁移来源保留，只读追溯。

```text
v1/
  current.json
  L5_UFLOW/v001_20261103_004317/   # 历史迁移工件
  L5_UFLOW/v005_20261117_110924/   # 当前 (五岛 + Cart2, 双车可运行)
experiments/
  assembly-mvp/v1/         # 混线装配 MVP 实验工件, 与 v1 current 独立
    current.json
    L5_UFLOW/v001_20261114_120734/   # 历史 (旧 config)
    L5_UFLOW/v002_20261121_152334/   # 当前 (v1_process_20261117a)
```

## 当前 v1 场景包

- 场景包 ID（内部字段 `artifact_id`）：`v1/L5_UFLOW/v005_20261117_110924`
- 来源：`v1/L5_UFLOW/v004_20261116_234100`（同一五岛 + Cart2 场景；
  phase 7 重新基线以登记 `cart_2` 运输设备）
- 上游追溯：`stage4/L5_UFLOW/v004_20261029_133249` →
  `stage3/L2_MIRROR/v008_20261029_112305`
- schema：`2.0`；契约：`v1_contract_20261103`；产品版本 `1.0.0`
- scene SHA-256：`15be9723ccc8a49554218af1f516427655239e7dc871d7d237a1b7c5202ef7d7`
- 校验：`validation.json` 记录 `ok=true`；当前 `flexible-factory validate`
  输出为准。
- 历史 `v001_20261103_004317` 是首个迁移工件，保留作追溯；`current.json`
  是当前指针的唯一权威来源。

## 保留的迁移来源

为保持 v1 manifest `provenance` 可追溯，仅保留以下只读来源场景包：

- `stage3/L2_MIRROR/v008_20261029_112305`
- `stage4/L5_UFLOW/v004_20261029_133249`

其他 stage1–stage4 场景包和历史布局已清理。来源场景包禁止覆盖或手工修改；
v1 运行期不读取它们，只读取 `v1/current.json`。

## current 指针

`v1/current.json` 是当前场景包标识与摘要的唯一指针；只在新场景包写入、重新加载
并只读校验通过后原子更新。使用者应读取该文件，不要在其他文档另存一份指针副本。

## assembly_mvp 实验场景包

混线装配 MVP 使用独立的实验工件根，不进入受保护 `v1/current.json`：

```text
experiments/assembly-mvp/v1/
  current.json
  L5_UFLOW/v001_20261114_120734/   # 历史, 旧 config, 保留
  L5_UFLOW/v002_20261121_152334/   # 当前, 当前 config
    scene.ttt
    manifest.json          # extra.v1.scene_profile=assembly_mvp
    validation.json
    lifecycle.jsonl
```

- 场景包 ID：`v1/L5_UFLOW/v002_20261121_152334`（`current.json` 指针）
- 构建来源：`stage3/L2_MIRROR/v008_20261029_112305`
- profile：`assembly_mvp`；扩展契约 `v1_assembly_mvp_20261114`
- 内容：v1 五岛底图 + 2 个独立岛、4 个来料槽、2 个逻辑装配位、2 个成品位；
  不含 `Cart2`，MVP 订单使用 `single`
- 校验：保存后重载 `validation.json` `ok=true`
- 历史 `v001_20261114_120734` 使用 `v1_process_20261114a`/config
  `e292c37f…`，与当前 `v1_process_20261117a`/config `c132340c…` 不一致，
  启动前只读校验会拒绝；不覆盖该历史版本。
- 命令：

      flexible-factory build \
        --parent docs/scene_artifacts/stage3/L2_MIRROR/v008_20261029_112305 \
        --profile assembly_mvp \
        --artifact-root docs/scene_artifacts/experiments/assembly-mvp \
        --save

实验场景包与 v1 工件同样受版本目录、SHA-256 和只读保护；它只服务 MVP
订单验收，不是新的 v1 基线，也不改变 `docs/scene_artifacts/v1/current.json`。
订单 fixture 与复现步骤见
[assembly_mvp 参考](../implementation/reference/production-flow/assembly-mvp/README.md)。

## 覆盖与派生规则

1. 每次 `save`/`migrate` 创建新的 `v<NNN>_<stamp>` 目录，不覆盖已有版本。
2. v1 场景包 `protected: true`，拒绝覆盖；文件只读。
3. `migrate` 失败不更新 current，也不修改来源场景包。
4. `build` 只执行构建契约检查；`build → save` 才生成新的 v1 版本。
5. 不得用 CoppeliaSim GUI 的 **File > Save Scene** 覆盖场景包内的 `scene.ttt`。

CLI 命令与运行流程见[项目使用指南](../wiki/98-ProjectUsage/USAGE.md)。
