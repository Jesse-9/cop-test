# 场景版本包

本目录保存可版本化、可校验和可追溯的 CoppeliaSim 场景包。每个场景包以
`scene.ttt` 为主体，并附带 `manifest.json`、校验结果和生命周期记录。代码和
磁盘路径中的 `artifact` / `scene_artifacts` 是历史内部名称；这里的“场景包”
不是生产流程中的在制品或零部件。v1 只使用 `v1/` 命名空间；旧阶段场景包
仅作为 v1 迁移来源保留，只读追溯。

```text
v1/
  current.json
  L5_UFLOW/v001_20261103_004317/
    scene.ttt
    manifest.json          # schema 2.0
    validation.json        # 迁移时 452 项只读校验
    lifecycle.jsonl
experiments/
  assembly-mvp/v1/         # 混线装配 MVP 实验工件, 与 v1 current 独立
    current.json
    L5_UFLOW/v001_20261114_120734/
```

## 当前 v1 场景包

- 场景包 ID（内部字段 `artifact_id`）：`v1/L5_UFLOW/v001_20261103_004317`
- 来源：`stage4/L5_UFLOW/v004_20261029_133249`（只读迁移来源场景包）
- 上游追溯：`stage3/L2_MIRROR/v008_20261029_112305`
- schema：`2.0`；契约：`v1_contract_20261103`；产品版本 `1.0.0`
- scene SHA-256：`bf7e4df6681abf8027e3f04e3bb214fc5a1f707d1be8922a733fce424a2a352c`
  （与来源 stage4 场景逐字节一致）
- 校验：迁移时保存的 `validation.json` 记录该场景包的只读校验结果；后续代码校验
  结果以对应实施报告和当前 `flexible-factory validate` 输出为准。
- 迁移命令：

      flexible-factory migrate \
        --source docs/scene_artifacts/stage4/L5_UFLOW/v004_20261029_133249

  迁移幂等；current 已指向同一来源且摘要一致时不创建新版本。

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
  L5_UFLOW/v001_20261114_120734/
    scene.ttt
    manifest.json          # extra.v1.scene_profile=assembly_mvp
    validation.json
    lifecycle.jsonl
```

- 场景包 ID：`v1/L5_UFLOW/v001_20261114_120734`
- 构建来源：`stage3/L2_MIRROR/v008_20261029_112305`
- profile：`assembly_mvp`；扩展契约 `v1_assembly_mvp_20261114`
- 内容：v1 五岛底图 + 2 个独立岛、4 个来料槽、2 个逻辑装配位、2 个成品位
- 校验：保存后重载 `validation.json` `ok=true`；manifest 构建契约含 308 项
  MVP 扩展检查
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
