# 场景工件

本目录保存版本化场景工件。v1 只使用 `v1/` 命名空间；旧阶段工件仅保留
v1 迁移来源，作为只读追溯材料。

```text
v1/
  current.json
  L5_UFLOW/v001_20261103_004317/
    scene.ttt
    manifest.json          # schema 2.0
    validation.json        # 迁移时 452 项只读校验
    lifecycle.jsonl
```

## 当前 v1 工件

- 工件 ID：`v1/L5_UFLOW/v001_20261103_004317`
- 来源：`stage4/L5_UFLOW/v004_20261029_133249`（只读迁移来源）
- 上游追溯：`stage3/L2_MIRROR/v008_20261029_112305`
- schema：`2.0`；契约：`v1_contract_20261103`；产品版本 `1.0.0`
- scene SHA-256：`bf7e4df6681abf8027e3f04e3bb214fc5a1f707d1be8922a733fce424a2a352c`
  （与来源 stage4 场景逐字节一致）
- 校验：迁移时保存的 `validation.json` 为 452 项通过、0 差异；最终代码
  独立 `flexible-factory validate` 为 454 项通过、0 差异。
- 迁移命令：

      flexible-factory migrate \
        --source docs/scene_artifacts/stage4/L5_UFLOW/v004_20261029_133249

  迁移幂等；current 已指向同一来源且摘要一致时不创建新版本。

## 保留的迁移来源

为保持 v1 manifest `provenance` 可追溯，仅保留以下只读来源工件：

- `stage3/L2_MIRROR/v008_20261029_112305`
- `stage4/L5_UFLOW/v004_20261029_133249`

其他 stage1–stage4 工件和历史布局已清理。来源工件禁止覆盖或手工修改；
v1 运行期不读取它们，只读取 `v1/current.json`。

## current 指针

```text
docs/scene_artifacts/v1/current.json
  {"artifact_id": "v1/L5_UFLOW/v001_20261103_004317",
   "path": "L5_UFLOW/v001_20261103_004317",
   "manifest_digest": "...", "scene_sha256": "...", "updated_at": "..."}
```

`current.json` 只在新工件写入、重新加载并只读校验通过后原子更新。

## 覆盖与派生规则

1. 每次 `save`/`migrate` 创建新的 `v<NNN>_<stamp>` 目录，不覆盖已有版本。
2. v1 工件 `protected: true`，拒绝覆盖；文件只读。
3. `migrate` 失败不更新 current，也不修改来源工件。
4. `build` 只执行构建契约检查；`build → save` 才生成新的 v1 版本。
5. 不得用 CoppeliaSim GUI 的 **File > Save Scene** 覆盖工件内的 `scene.ttt`。

## 命令

```bash
flexible-factory load
flexible-factory validate        # 可选诊断
flexible-factory run --scenario full_flow --time 700
flexible-factory stop
flexible-factory status
flexible-factory list
```

运行说明见 [项目使用指南](../wiki/98-ProjectUsage/USAGE.md)。
