---
title: 柔性工业车间仿真项目使用指南
type: tutorial
tags:
  - coppeliasim
  - 类别/基础知识
  - 难度/初级
created: 2026-10-26
updated: 2026-10-26
status: published
sim_version: "4.10+"
---

# 柔性工业车间仿真项目使用指南

本指南说明如何启动 CoppeliaSim、连接项目、操作版本化场景工件，以及运行当前可用的阶段一至三。命令以 Windows PowerShell、仓库根目录为例。

## 启动仿真软件

1. 安装并启动 CoppeliaSim 4.10 或更新版本。项目所需内置模型包括 UR5、suction pad、generic conveyor 和 pioneer p3dx。
2. 在仓库根目录准备 Python 环境：

       uv sync

3. 在 CoppeliaSim 中启动 ZeroMQ Remote API server add-on，确认监听 localhost:23000。菜单通常为 **Add-ons → ZMQ remote API server**。
4. 保持 CoppeliaSim 运行，再从仓库根目录打开终端执行下面的阶段命令。远程仿真时使用入口支持的 --host 和 --port 参数。

查看各命令参数：

    .venv/Scripts/python.exe src/run_scene_lifecycle.py --help
    .venv/Scripts/python.exe src/run_stage1.py --help
    .venv/Scripts/python.exe src/run_stage2.py --help
    .venv/Scripts/python.exe src/run_stage3.py --help

## 运行约定

场景作为版本化工件保存在 docs/scene_artifacts/。load 只加载已保存的 .ttt 工件，不会构建或清空场景；加载后可直接 start。start 会先取得运行锁，再执行完整只读工件校验，校验失败则拒绝启动并释放锁。validate 是可选诊断入口，用于主动检查并查看详细差异报告，不是 start 的前置条件。阶段入口会按阶段选取默认 current 工件并在启动点校验。运行期间禁止构建、加载、保存、校验或启动第二个控制任务。

    .venv/Scripts/python.exe src/run_scene_lifecycle.py status
    .venv/Scripts/python.exe src/run_scene_lifecycle.py list
    .venv/Scripts/python.exe src/run_scene_lifecycle.py stop

## 用法变化（2026-10-26）

本次调整只改变“启动前是否必须手动 validate”，命令名和参数不变：

| 场景 | 调整前 | 调整后 |
|---|---|---|
| 已保存工件日常运行 | load current → validate → start | **load current → start**（validate 可选） |
| 主动检查/查看差异报告 | validate | validate（保留为可选诊断；start 仍会重新校验） |
| 首次构建并运行 | build → save → load → validate → start | **build → save → load → start** |
| stage2/stage3 派生保存 | load → validate → save | 不变（save 仍要求已校验场景） |
| 阶段入口 run_stage1/2/3.py（默认路径） | 内部 load → validate → start | 内部 load → start（启动点在锁内校验；命令参数不变） |

- `start` 的 CLI 参数（`--target`/`--scenario`/`--fault`/`--time`/`--repeat`/`--keep-running`）没有变化。
- `load` 的 JSON 结果字段由 `requires_validation` 改为 `validation_at_start`。
- MCP 对应变化见 [MCP 使用说明](MCP_USAGE.md)：`load_scene` 后可直接 `start_simulation`。

## 阶段一：单工位闭环

当前基线为 stage1/L1_S1/v002_20261020_234403。日常运行：

    .venv/Scripts/python.exe src/run_stage1.py --target station --time 45
    .venv/Scripts/python.exe src/run_stage1.py --target agv --time 45
    .venv/Scripts/python.exe src/run_stage1.py --target both --time 90
    .venv/Scripts/python.exe src/run_stage1.py --target station --repeat 6 --time 30
    .venv/Scripts/python.exe src/run_stage1.py --target station --fault ik_unreachable

只有确实要显式重建场景时才加 --build：

    .venv/Scripts/python.exe src/run_stage1.py --build --target station --time 45

## 阶段二：物理约束与运动规划

当前基线为 stage2/L1_S1/v013_20261024_232558。运行入口默认加载 stage2 的 current 工件：

    .venv/Scripts/python.exe src/run_stage2.py --target station --time 150
    .venv/Scripts/python.exe src/run_stage2.py --target agv --time 150
    .venv/Scripts/python.exe src/run_stage2.py --target both --time 180
    .venv/Scripts/python.exe src/run_stage2.py --target station --fault runtime_obstacle --time 150

如需重新派生，从已验收阶段一工件构建 stage2 新版本（派生保存前必须 validate，因为 save 只接受已校验场景）：

    .venv/Scripts/python.exe src/run_scene_lifecycle.py load stage1/L1_S1/v002_20261020_234403
    .venv/Scripts/python.exe src/run_scene_lifecycle.py validate
    .venv/Scripts/python.exe src/run_scene_lifecycle.py save --phase stage2 --parent stage1/L1_S1/v002_20261020_234403 --note "阶段二派生"

## 阶段三：双工位柔性搬运

当前基线为 stage3/L2_MIRROR/v004_20261025_131837。五种运行场景：

    .venv/Scripts/python.exe src/run_stage3.py --scenario closed_loop --time 300
    .venv/Scripts/python.exe src/run_stage3.py --scenario contention --time 300
    .venv/Scripts/python.exe src/run_stage3.py --scenario blocked --time 300
    .venv/Scripts/python.exe src/run_stage3.py --scenario unstable_unload --time 300
    .venv/Scripts/python.exe src/run_stage3.py --scenario route_baseline --time 300

如需重建 stage3，从已验收 stage2 工件派生（build 成功后状态已是已校验，可直接 save）：

    .venv/Scripts/python.exe src/run_scene_lifecycle.py load stage2/L1_S1/v013_20261024_232558
    .venv/Scripts/python.exe src/run_scene_lifecycle.py build --phase stage3 --parent stage2/L1_S1/v013_20261024_232558
    .venv/Scripts/python.exe src/run_scene_lifecycle.py save --phase stage3 --parent stage2/L1_S1/v013_20261024_232558 --note "阶段三派生"

## 手动操作生命周期

首次显式构建和保存阶段一工件：

    .venv/Scripts/python.exe src/run_scene_lifecycle.py build
    .venv/Scripts/python.exe src/run_scene_lifecycle.py save --phase stage1 --note "阶段一场景"

build 只执行构建契约检查，不产生工件 manifest；build → save 才会保存、回读并完成工件级完整只读校验。临时构建后不 save 的场景也可以 start，但启动点只复核构建契约，不能视为工件级全量校验。

加载并启动工件（启动点自动校验）：

    .venv/Scripts/python.exe src/run_scene_lifecycle.py load current --phase stage1
    .venv/Scripts/python.exe src/run_scene_lifecycle.py start --target station --time 45

如需先主动检查并查看详细差异报告，可在 load 后单独运行可选的 validate；start 仍会重新执行完整校验：

    .venv/Scripts/python.exe src/run_scene_lifecycle.py load current --phase stage1
    .venv/Scripts/python.exe src/run_scene_lifecycle.py validate
    .venv/Scripts/python.exe src/run_scene_lifecycle.py start --target station --time 45

save 每次创建新版本，不覆盖旧工件。stage2/stage3 保存必须提供正确的 --parent；stage1 正式基线受保护。保存时会回读并校验，只有通过才更新 current.json。派生保存前仍需 validate（save 只允许已校验场景；validate 允许直接从加载态转入）。详细格式与保护规则见 [场景工件说明](../../scene_artifacts/README.md)。

## 排查

- 连接失败：检查 CoppeliaSim 是否运行、ZMQ add-on 是否启动，并确认主机和端口（默认 localhost:23000）。
- 找不到工件或阶段不匹配：运行 src/run_scene_lifecycle.py list，确认对应阶段的 current 指针。
- 场景未通过校验：start 会拒绝并给出差异数；先加载对应阶段工件，再运行可选的 validate 查看详细报告；不要通过编辑受保护工件绕过摘要检查。
- 日志默认在仓库根目录的 factory_sim.log、stage2_sim.log、stage3_sim.log 等文件；运行报告在 docs/implementation/runs/。

阶段验收和未实现功能见根目录 [README](../../../README.md) 及 docs/implementation/。旧五岛 src/reference/ 是历史兼容流程。

## 维护约定

本文件是仿真启动、场景生命周期和阶段运行方法的操作手册。以后改动启动/连接方式、CLI 参数、阶段工作流或工件派生规则时，必须同步更新本文件，并核对 README、场景工件说明与实现保持一致。新增可运行阶段时，补充其前置工件、派生/加载步骤、运行命令和输出位置。
