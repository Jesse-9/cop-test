---
title: 柔性工业车间 v1 使用指南
type: tutorial
tags:
  - coppeliasim
  - 类别/基础知识
  - 难度/初级
created: 2026-10-26
updated: 2026-11-03
status: published
sim_version: "4.10+"
---

# 柔性工业车间 v1 使用指南

本指南说明 CoppeliaSim、ZMQ 连接、v1 工件操作和 `full_flow` 运行。命令以
Windows PowerShell、仓库根目录为例。v1 不支持历史阶段入口；历史实现、报告
与证据已清理，结论见 [V1 实施报告](../../implementation/V1_REPORT.md)。

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

`--scenario` 的 `full_flow` 是唯一用户运行模式；`no_pair`、`agv_priority`、
`agv_blocked`、`conveyor_fault`、`staggered_batch` 仅用于内部回归和诊断。

其他查询/控制命令：

    flexible-factory status
    flexible-factory status --offline
    flexible-factory list
    flexible-factory stop

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
运行命令和输出位置。
