---
title: 柔性工业车间 v1 收敛与迁移方案
type: plan
status: review
created: 2026-11-03
updated: 2026-11-03
scope: v1 L5_UFLOW
implementation: V1_REPORT.md
---

# 柔性工业车间 v1 收敛与迁移方案

## 1. 目的与决策

本方案把现有阶段一至阶段四的分阶段实现收敛为一个可独立安装、构建/加载、校验和运行的 v1 产品。v1 只交付阶段四五岛 `L5_UFLOW` 场景；阶段一至三代码仅作为 v4 当前实现所依赖的基础能力来源，不再作为独立产品场景或用户运行入口。

本文中的“v4 场景”指阶段四五岛场景，不指某个 CoppeliaSim 文件格式版本。产品版本、Python 包版本、工件 schema 版本、场景契约版本分别编号，不共用一个版本号。

产品运行模式收敛为 `full_flow`。`no_pair`、`agv_priority`、`agv_blocked`、`conveyor_fault`、`staggered_batch` 作为内部单测/集成回归或一次性验收场景保留，不要求成为用户可选的正式运行模式。v1 不包含混线生产阶段 0–5 规划的后续功能，也不实现统一故障自动恢复。

## 2. 当前基线与约束

- 迁移来源场景工件：`stage4/L5_UFLOW/v004_20261029_133249`；验收事实和已知问题见 [V1 报告](V1_REPORT.md) 与 [M0 基线](V1_BASELINE.md)。
- 重排前 `stage4/L4_COLUMN/v008_20261028_225634` 只作为历史对照，未保留，也不是 v1 默认场景。
- 迁移前 `src/stage4/` 依赖 `stage1`、`stage2`、`stage3`：包括物理、传送带、IK、吸附、安全/碰撞规划、场景构造、AGV 路线及资源逻辑；迁移完成后这些包已删除。
- 迁移前阶段四 manifest 校验 stage2/stage3 配置摘要及 stage3 父工件来源；v1 已调整为独立运行契约，同时保留来源追溯信息。
- 场景工件受摘要、生命周期和只读校验保护。不得覆盖现有工件，不得手工修改 manifest 或用 GUI 覆盖工件内的 `scene.ttt`。
- 工作区中与本方案无关的用户日志、配置和未提交改动必须保持不变。

## 3. v1 产品边界

### 3.1 纳入范围

- 五岛 A–E、S1/S2 仓库、四条传送带/交接物流、共享 pioneer p3dx AGV、E 岛双输入缓冲及配对输出。
- 当前 `L5_UFLOW` 坐标、高度 datum、AGV 三段路网、已验证的运动/碰撞/物理行为和物料 UID/谱系规则。
- v1 场景构建、工件保存/派生、加载、只读验证、启动、停止、结构化运行报告。
- `full_flow` 用户运行流程及必要的状态、故障冻结、诊断和验收能力。
- 保证 v1 能在干净 Python 环境安装，且有自动化测试和可追溯仿真证据。

### 3.2 不纳入范围

- 阶段一单工位、阶段二单臂规划基准、阶段三双工位作为独立可运行产品。
- `src/reference/` 中的旧五岛控制器及其 CLI。MCP 已在 M0 决定不纳入 v1 产品范围；相关注册与实现已删除。
- 06-18 至 06-23 的混线生产演进功能、额外设备/AGV、动态避障、真实装配、Bezier 默认路径及统一故障自动恢复。
- 未经对照实验验证的布局、物理参数和控制策略调整。

## 4. 目标代码结构与模块映射

建议将产品代码归并到 `src/flexible_factory/`（最终包名应与 `pyproject.toml` 项目名统一）。按职责迁移，不要求为追求目录统一而重写已验证算法。

```text
src/flexible_factory/
  application/       # 运行用例、依赖组装、生命周期编排
  domain/            # 物料、任务、资源、状态转换、指标
  simulation/        # 场景、布局、设备、物理、运动和仿真适配
    motion/          # IK、接触、碰撞、规划、轨迹、安全锁存
  artifacts/         # v1 manifest、摘要、验证、迁移
  cli.py             # 唯一 v1 命令入口
```

| 现有来源 | 目标位置/职责 | 迁移要求 |
|---|---|---|
| `stage4/materials.py` | `domain/materials.py` | 保留 UID、类型、谱系、区域容量、工序转换和 E 原子配对；明确非法状态转换和重复配对策略。 |
| `stage4/tasks.py` | `domain/tasks.py` | 保留任务状态、优先级、等待升级、去重、重试上限和失败原因；终态定义集中维护。 |
| `stage3/resources.py`、`stage4/resources.py` | `domain/resources.py` | 提取 v1 使用的资源表；全有或全无申请、固定顺序、释放行为有单元测试，不依赖阶段三类名。 |
| `stage4/coordinator.py` | `domain/coordinator.py` + application 编排 | 业务决策与 sim API 调用分开；显式表示等待、阻塞、失败、冻结和完成，不将非 DONE 终态计入完成。 |
| `stage4/metrics.py` | `domain/metrics.py` | 保留由事件重算指标的方式和版本字段；指标定义与报告字段保持一致。 |
| `stage4/layout.py`、`agv_map.py` | `simulation/layout.py`、`simulation/agv.py` | 保留 L5_UFLOW datum、路线节点/边、dock 和布局验证；解开 `stage1.layout`、`stage3.layout` 依赖。 |
| `stage4/scene.py` | `simulation/scene.py` | 将 v1 场景构建、加载后 adopt、句柄缓存、结构校验收敛为单一场景 API。 |
| `stage4/scene_contract.py` | `artifacts/manifest.py` + `simulation` 校验器 | v1 自身校验配置、布局、拓扑、模型及场景对象；stage1–3 来源作为 provenance，不作为运行时依赖。 |
| `stage4/runtime.py`、`unit_controller.py` | `simulation/` | 保留五臂独立 IK/影子臂/碰撞集合/控制器能力；用组合替代对 `Stage2UnitController`/`Stage3UnitController` 的继承耦合。 |
| `stage1.physics.py`、`conveyor_controller.py`、`pioneer_controller.py`、`ur5_ik_controller.py`、`material_manager.py`、`scene.py` | `simulation/` | 仅迁移 v1 调用的物理、输送、AGV、IK、句柄及物料物理状态能力。 |
| `stage2.contact.py`、`collisions.py`、`path_planning.py`、`safety.py`、`shadow.py`、`trajectory.py`、`suction.py` | `simulation/motion/` | 保留 v1 实际使用的接触、路径检查、轨迹、安全锁存和吸附行为；保持现有容差与安全判据。 |
| `stage3.agv_route.py`、`layout.py`、`runtime.py` | `simulation/` | 提取 v1 使用的底层实现；不迁移双工位场景入口和 stage3 专属契约。 |
| `stage1.lifecycle.py`、`scene_artifact.py` | `application/lifecycle.py`、`artifacts/` | 将多阶段路由收敛为 v1 工件操作；保持 load 不隐式 build/clear、validate 失败关闭、save 原子写入/current 更新。 |
| `stage4.run.py`、`src/run_stage4.py` | `application/run.py`、`cli.py` | 形成唯一 v1 runner/CLI，维持 stepping 单步主控和完整收尾/证据生成。 |
| `reference.factory_mcp_server.py` | 已删除 | M0 决定 MCP 不纳入 v1；不再保留 adapter，也不允许调用 legacy/reference 后端。 |

依赖方向固定为：`cli → application → domain / simulation / artifacts`。领域层不得导入 CoppeliaSim 客户端、文件系统实现或 CLI；仿真适配层不得决定业务流程；场景构造、工件生命周期和仿真执行不得藏在同一个 runner 函数中。

## 5. v1 场景工件和版本策略

### 5.1 工件布局

在既有工件根下新增独立命名空间，不改变原始 stage4 工件：

```text
docs/scene_artifacts/v1/L5_UFLOW/v001_<timestamp>/
  scene.ttt
  manifest.json
  validation.json
  lifecycle.jsonl
docs/scene_artifacts/v1/current.json
```

### 5.2 Manifest 内容

manifest 至少记录：

- `schema_version`：manifest 结构版本，独立于产品版本；
- `product_version`：如 `1.0.0`；
- `scene_contract_version`、scene builder、layout、task flow、resource policy、AGV route、material model 和 metrics 版本；
- 稳定配置摘要、布局摘要、场景文件 SHA-256、父工件/来源工件 provenance；
- 设备 alias 清单、五台机械臂/传感器/物流线/仓库/缓冲/输出的结构信息；
- 物料流程、配对策略、资源互斥、路线与验收定义；
- 构建器与运行代码版本，以及保存/验证时间和验证器版本。

### 5.3 迁移行为

新增显式迁移命令或脚本，执行“读取 stage4 L5_UFLOW → 校验来源和场景 → 生成新 v1 manifest → 复制/保存新场景 → 重载 → 全量验证 → 原子更新 v1/current”。迁移工具必须幂等或对已存在目标拒绝覆盖；任何一步失败都保留旧 current 和源工件。stage1–3 工件 ID、摘要和报告链接保留在 provenance 中，但 v1 启动不再导入其代码或比较其当前配置摘要。

## 6. 文档调整结果

v1 发布后按“规范描述当前行为、使用指南描述操作、实施报告记录证据”的职责
划分整理文档；旧阶段报告、wiki 阶段文章、测量资料和 MCP 说明已删除。

| 文档 | 最终状态 |
|---|---|
| `README.md`、`CLAUDE.md`、`PROJECT_ENGINEERING_REFERENCE.md` | 只描述 v1 入口、产品范围、工件 ID、测试命令、文档权威源和已知限制。 |
| `docs/README.md`、`docs/implementation/README.md`、`wiki/index.md` | 只索引 v1 方案/基线/报告、运行指南、工件说明与通用 CoppeliaSim 知识。 |
| `USAGE.md` | v1 命令、工件、迁移/重建和排查；历史阶段入口已移除。 |
| `docs/scene_artifacts/README.md` | v1 工件章节与迁移规则；仅保留 stage3/stage4 只读迁移来源。 |
| `docs/implementation/evidence/` | 只保留被 V1 报告引用的 `full_flow` 与 `conveyor_fault` 摘要。 |
| 旧阶段报告、06-04/06-05 等 wiki 文章、`docs/measurement/`、`MCP_USAGE.md` | 已删除，不在仓库中保留历史副本。 |

发布前应运行 Markdown 相对链接和仓库内路径检查；文档状态、日期、工件 ID、命令和场景契约版本以实际验证结果为准，禁止先把计划改写成已完成事实。

## 7. 测试与工程质量要求

| 层级 | 最低覆盖 | 通过条件 |
|---|---|---|
| Domain 单测 | 物料 UID/谱系/状态、工序转换、E 配对、任务终态/去重/优先级、资源锁、背压、指标重算 | 不依赖 CoppeliaSim；成功、拒绝和异常路径都有断言。 |
| 仿真适配单测 | layout 几何、AGV 路径/停靠、输送带停机和补偿边界、接触与目标判据、物理状态恢复 | 对关键边界值及拒绝条件有测试；容差不因重构而放宽。 |
| Manifest/生命周期测试 | schema、摘要稳定性、provenance、current 原子更新、加载/校验/启动拒绝条件、Shadow 对象隔离 | 配置或场景漂移明确失败；失败不破坏既有工件/current。 |
| 集成仿真测试 | v1 `full_flow`、代表性停带/阻塞故障；需要时覆盖 `staggered_batch` | 核对 UID 流、物料最终状态、AGV/资源释放、故障冻结和 JSON 报告。 |
| 安装/入口测试 | 干净环境安装、唯一 v1 CLI、资源文件可定位、MCP 已移除 | 不需要导入 stage1/2/3/reference 即可启动 v1。 |
| 文档/静态检查 | 格式、lint、类型检查、导入边界、Markdown 链接 | CI 必需项均通过；仿真器不可用时集成测试明确标为未执行，不报告为通过。 |

建立迁移前测试基线，并记录测试命令、结果、解释器/依赖版本及 CoppeliaSim 版本。测试从旧 `tests/test_stage4_*` 中迁入有效回归；旧阶段测试只在覆盖被保留公共能力时迁移。`tests/conftest.py` 对 `reference.factory_mcp_server` 的模块级依赖已拆除。

## 8. 分批实施及退出门槛

### M0：冻结范围和行为基线

- 确认 v1 场景是 `L5_UFLOW`，`full_flow` 是唯一用户运行模式；内部回归场景已固定，MCP 已决定不属于产品范围。
- 记录当前 Git 状态、完整测试结果、当前工件摘要、迁移来源验收结论及已知故障/限制。
- 形成 stage4 对 stage1–3 的模块、符号、配置和工件契约依赖清单。

**退出门槛：**范围、基线、迁移映射、回归矩阵和 MCP 决策均有记录并可审查。

### M1：建立 v1 包骨架并抽取 domain

- 创建目标包、公共类型和稳定导入路径；先迁移 materials/tasks/resources/metrics。
- 将 `coordinator` 拆分为业务决策与仿真动作端口；明确状态转换、取消/冻结和错误语义。
- 迁移对应单测，避免改变场景和运行策略。

**退出门槛：**domain 单测通过；domain 不导入旧 stage 包、CoppeliaSim 或 CLI。

### M2：抽取 simulation 公共能力

- 迁移 v4 使用的场景、layout、句柄、物理、传送带、AGV、吸附、IK、碰撞、接触、路径和安全模块。
- 将 stage3 基类依赖改为 v1 直接组合底层控制能力；共享逻辑仅在确有复用时抽象。
- 在迁移过程中保持参数、容差、步进顺序、碰撞集合和状态机行为不变。

**退出门槛：**静态/适配层测试通过，v1 runner 能在既有 stage4 工件上运行代表性仿真；无未解释的行为变化。

### M3：独立工件契约与迁移

- 实现 v1 manifest/schema、摘要计算、只读校验和显式工件迁移。
- 从旧 `L5_UFLOW` 工件生成 v1 副本；确认旧工件摘要未变化。
- 验证保存、重载、场景结构、启动校验及失败原子性。

**退出门槛：**v1 工件可独立 load/validate/start；不导入旧阶段契约；旧工件和 current 未被修改；新 current 只指向完整校验通过的 v1 工件。

### M4：统一入口、打包与接入

- 更新 `pyproject.toml` 包清单、console script 和依赖声明，建立唯一 v1 命令。
- 更新 `.mcp.json` 和 MCP 实现：已按 M0 决议从 v1 产品中移除。
- 从干净环境安装并测试 build/load/validate/run/status/stop 的实际支持命令。

**退出门槛：**无 CoppeliaSim 运行时导入旧 `stage1`–`stage3`/`reference`；CLI 只操作 v1 工件，MCP 已移除。

### M5：文档与验收

- 新建 v1 权威设计/运行说明及 `V1_REPORT.md`；按 §6 更新索引和历史文档状态。
- 执行全量 Python 测试、干净安装测试、v1 工件生命周期验证、`full_flow` 仿真以及规定的故障回归。
- 保存被报告引用的 JSON 证据和场景校验结果；确认引用、命令、工件 ID 与实测一致。

**退出门槛：**产品范围和文档一致；全量必需检查通过；所有未执行项和已知限制明示。

### M6：移除旧入口并发布 v1

- 扫描全仓 Python imports、console scripts、测试 fixture、文档链接和工件契约中的旧阶段引用；已确认无 MCP 配置和旧阶段导入。
- 删除已迁移且无 v1 调用方的阶段 runner、包装入口、stage1_1 实验入口和 reference 产品入口；历史报告、测量脚本和证据按归档规则处理。
- 更新版本号、变更记录、安装说明和 v1 发布报告。

**退出门槛：**仓库安装包只包含 v1 产品代码；全量测试和端到端验收通过；不存在失效入口或必需文档链接；v1 工件可从文档步骤复现。

## 9. 具体扫描和清理清单

清理前须用 `rg` 和 Python import 检查逐项确认调用方，不得仅凭文件名删除：

- 源码：`src/stage1/`、`src/stage1_1/`、`src/stage2/`、`src/stage3/`、`src/reference/`、`src/run_stage1.py`、`run_stage2.py`、`run_stage3.py`、`run_step1_1.py`、`run_scene_lifecycle.py`。
- 配置：`pyproject.toml` 的 packages、py-modules、project scripts；`.mcp.json` 已删除；环境变量与默认工件根路径。
- 测试：`tests/conftest.py`、`test_v1_*`；`test_mcp_*` 与旧阶段测试已删除，模块导入 patch 路径已改为 v1。
- 文档：README、CLAUDE、工程参考、USAGE、`docs/README.md`、实施索引、wiki 索引；`MCP_USAGE`、06-04/06-05、06-09 至 06-23、各阶段报告和证据索引已删除。
- 数据工件：stage1–4 的 `current.json`、manifest、validation、`scene.ttt`、runtime state。历史数据不随源码清理删除；迁移生成新 v1 命名空间。

## 10. 风险与控制

| 风险 | 控制办法 |
|---|---|
| 阶段四继承多个历史阶段控制器，迁移造成运动行为变化 | 分层迁移，每批跑单测和代表性仿真；参数/容差/控制顺序在重构期间冻结。 |
| 删除旧阶段后场景父链校验或 current 指针失效 | 先设计 v1 schema 和迁移副本；旧 namespace 保持只读；迁移失败不更新 current。 |
| MCP 或 CLI 仍暗中调用 reference/stage1 默认后端 | 全仓扫描已确认无 MCP 入口；入口测试和导入边界测试覆盖 v1 CLI。 |
| 历史文档和证据被误删，或规划被表述为已实现 | 只归档/改索引，不批量删除；新 v1 报告严格区分计划、实测和遗留事项。 |
| 迁移同时引入故障自动恢复等新能力，导致范围扩大 | 本轮限定行为等价迁移；新增行为另立设计、测试和验收。 |
| v1 版本、场景契约和工件 schema 混为一谈 | 分别维护 `product_version`、`scene_contract_version`、`schema_version` 和算法/策略版本。 |

## 11. 暂定决策及完成定义

启动实施前需在 M0 固定以下决定：

1. Python 包正式名称为 `flexible-factory`；MCP 不纳入 v1，相关注册与实现已删除。
2. `full_flow` 是唯一用户入口，其他 scenario 仅作为测试/诊断入口；是否对用户隐藏 `--scenario`。
3. v1 scene artifact 的最终根目录和产品版本命名规则。
4. 自动化 CI 是否具备可运行 CoppeliaSim 的 Windows agent；若没有，哪些集成验收由受控本地流程执行并归档证据。

v1 完成定义：用户可从干净环境安装一个产品包，使用一套文档化命令加载并验证独立的 `L5_UFLOW` v1 工件，运行 `full_flow` 并获得可追溯报告；自动化测试覆盖承诺的领域规则和工件生命周期；源码运行路径不依赖 `stage1`–`stage3` 或 `reference`；历史场景、报告和证据仍可追溯且未被覆盖。

## 12. 参考资料

- [V1 实施报告](V1_REPORT.md)
- [M0 基线](V1_BASELINE.md)
- [场景工件说明](../scene_artifacts/README.md)
- [工程参考](../../PROJECT_ENGINEERING_REFERENCE.md)
- [项目使用指南](../wiki/98-ProjectUsage/USAGE.md)
