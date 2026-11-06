---
title: 仿真模块代码优化实施计划
type: plan
tags:
  - 类别/实施计划
  - 类别/仿真
created: 2026-11-06
updated: 2026-11-06
status: partially-implemented
scope: src/flexible_factory/simulation
---

# 仿真模块代码优化实施计划

## 0. 实施状态 (2026-11-06)

- **P1 已实施**：运动层新增窄的 `ConveyorPickContext`，v1 只覆盖线别名、几何、带速、抓取区、传感器与日志标签；删除 Stage4 中与 Stage2 重复的停带/稳定/传感器实现。阶段一基线的独立停带算法保留。
- **P2 已实施**：单臂会话运行时构建、共享容器和查询属性辅助函数迁入 `runtime_common.py`；`build_stage3_runtime()` 与 `build_v1_runtime()` 保留各自入口和障碍策略，启动摘要字段与顺序不变。
- **P3 5.1 已实施**：`Stage3UnitController`/`TrayCheck` 并入 `unit_controller.py`；`build_stage3_runtime()` 并入 `runtime.py`，Stage3 错误类型独立为 `Stage3RuntimeBuildError`。旧模块保留 re-export 兼容层。
- **P3 5.3 已实施**：`path_planning.py` 拆为 `plan_types`、`plan_checks`、`plan_search`、`plan_postprocess`；原模块保留 re-export，算法、容差和预算未改。
- **P3 §6 已实施**：补充包级架构说明和模块清单；核实后删除无生产消费者的 `device_handle_map.py`，相关关节模式测试已迁出保留。
- **P3 5.2 未实施**：运行时异常路径资源回滚仍为可选维护项。
- 实施记录与验证结果见 [实施报告](../reports/v1-simulation-code-optimization.md)。

## 1. 目标与结论

本计划依据 `src/flexible_factory/simulation/` 当前调用关系制定，目标是优先减少控制器和运行时装配中有证据的重复实现，再视维护收益整理规划器边界、命名和资源生命周期。

现有代码不是一组可以整体合并的重复模块。`scene_base → scene_dual → scene`、`layout_base → layout_dual → layout`、`unit_controller_base → unit_controller_motion → unit_controller`（原 Stage3/Stage4 已并入，`unit_controller_dual` 仅为兼容层）反映阶段能力的复用链；其中若干基线实现仍被当前 v1 装配或继承。P1/P2 后仍有少数相邻阶段入口文件可评估合并，但验收看导航和维护是否更直接，不以减少文件数本身作为目标。

优先级如下：

1. **P1：梳理控制器中的停带、稳定和取放流程，按真实共性减少重复。**
2. **P2：评估并收敛 Stage3/v1 的单臂运行时装配公共部分。**
3. **P3：可选维护项，优先评估控制器/runtime 文件合并，再考虑资源回滚、规划器边界和命名。**

每个阶段单独提交和验证。P1/P2 若不能形成窄而稳定的共享边界，应保留现有实现并记录原因。P3 不作为前置条件，可根据实际维护需求决定是否安排。

## 2. 当前调用与职责边界

### 2.1 主要运行路径

v1 启动由 application runner 组装场景、控制器依赖与运行时资源；Stage4 控制器经 `ArmExecutor` 执行，协调器负责统一步进、轮询执行状态、业务验证和提交。运行时构建为五台臂创建影子臂、碰撞集合、检查器、规划器与安全锁存。

与本计划直接相关的代码：

| 职责 | 代码落点 | 当前情况 |
|---|---|---|
| 基础与运动配置 | `simulation/config_base.py`、`config_motion.py` | 配置摘要参与场景契约校验，不能随意移动或新增常量 |
| 双工位层配置/布局/场景/运行时 | `config_dual.py`、`layout_dual.py`、`scene_dual.py`、`runtime_dual.py` | Stage3 构建入口与公共类型已迁入 `runtime.py` / `runtime_common.py`；`runtime_dual.py` 仅为兼容导出 |
| v1 五岛配置/布局/场景/运行时 | `config.py`、`layout.py`、`scene.py`、`runtime.py` | v1 正式运行路径；`runtime.py` 现同时包含 Stage3/v1 两个 builder |
| 控制器阶段链 | `unit_controller_base.py`、`unit_controller_motion.py`、`unit_controller_dual.py`、`unit_controller.py` | 多层继承；Stage3/Stage4 实现已并入 `unit_controller.py`，`unit_controller_dual.py` 仅为兼容导出 |
| 机械臂/AGV 执行适配 | `execution/` | 由 `application/coordinator.py` 调用，负责执行生命周期，不负责业务提交 |
| 路径规划 | `motion/path_planning.py`（兼容导出）及 `motion/plan_types.py`、`plan_checks.py`、`plan_search.py`、`plan_postprocess.py` | 5.3 已按职责拆分；检查/规划/后处理分模块，旧路径保留 re-export |

### 2.2 需要保留的边界

- 仿真 stepping 仍由协调器统一拥有；executor 不得调用 `sim.step()`。
- 执行器报告表示设备执行结果；应用层仍负责验证物料位置、路线结果、资源释放和业务提交。
- 配置和布局摘要是场景工件契约的一部分。代码重排不得意外改变 `config_digest()`、`layout_digest()` 或 manifest 语义。
- Stage1/Stage3 文件名不等同于废弃状态。处置文件前必须查生产调用、继承、测试和文档承诺。
- 安全停止、碰撞检查、接触判定和场景校验是既有行为，不得因“去重”而降低检查范围或放宽阈值。

## 3. P1：收敛控制器中重复的输送带取料行为

### 3.1 现状与风险

Stage1、Stage2 与 Stage4 控制器分别实现输送带更新、取料传感器处理、主动停带、停带确认和物料稳定检测。可比对的方法包括：

- `unit_controller_base.py`：`_update_conveyor()`、`_read_pick_sensor()`、`_command_pick_stop()`、`_advance_stopping()`、`_advance_material_settling()`。
- `unit_controller_motion.py`：同名运动层实现及 pre/post tick 联锁。
- `unit_controller.py`：Stage4 对应实现，并处理线选择、来源/类型检查及站间流程。

名称相同并不代表逻辑等价。不同层级在合法物料状态、传送带对象、状态迁移和后续目标上可能有意不同。直接把方法移到基类，或统一成一个配置繁多的状态机，会把阶段差异隐藏为不易审查的条件分支。

### 3.2 实施步骤

1. 对照现有方法和测试，简要记录输入状态、关键判定、输出状态及阶段特有分支；只记录提取所需的差异，不另建完整状态机规格文档。
2. 复用最相关的现有测试，优先覆盖正在迁移的方法及其调用路径；不要求为每个类和每种异常组合新增重复测试。
3. 仅当对照表证明同一算法被重复实现时，提取一个窄的停带/稳定测量协作者。它负责测量 pad 速度/位移、物料速度/位移和稳定步数，并返回明确结果；各控制器继续负责状态切换、物料登记、失败归因和目标生成。
4. 第一次只迁移一层的行为并保持其余层原样。对照相关现有测试；只有发现覆盖缺口时才补最少测试，再决定是否迁移下一层。如迁移需要很多 stage/type/line 条件参数，停止抽取，保留分开的策略实现。
5. 用现有运动联锁测试确认 `pre_tick()`/`post_tick()` 顺序未变，避免因流程提取而移位或重复调用。
6. 删除重复实现前，检索继承类是否依赖被覆盖方法的副作用和私有状态字段；避免只搬函数体却漏掉 `_stop_command_time`、稳定计数、失败报告等状态生命周期。

### 3.3 验收

- 目标 UID 判定、停带时机和稳定后的抓取目标保持现有行为。
- pad 停止确认和物料稳定确认满足原有容差、步数与超时规则。
- 物料姿态、允许抓取区、目标物料类型和 Stage4 来源/去向判断不变。
- 运动控制的 pre/post 联锁、锁存和轨迹取消时序不变。
- 同一测试集在提取前后通过；不以新增通用状态机或更多配置开关作为完成标志。

## 4. P2：提取 Stage3/v1 运行时装配公共逻辑

### 4.1 候选公共段

`build_stage3_runtime()` 与 `build_v1_runtime()` 均包含停止态检查、影子臂创建、真实/影子 FK 核对、杯口半径检查、碰撞运行时创建、`ConfigurationChecker`/`LocalPlanner`/`Stage2Safety` 装配和部分失败清理。v1 对应五台臂、四条相关物流线和其余四台真实臂障碍；Stage3 对应双臂及其专属障碍策略。

v1 原先复用 `runtime_dual.py` 中的 `Stage3ArmRuntime`、`Stage3Runtime`、`UnitSceneView` 和辅助函数。这有复用价值，也造成“Stage3”模块承担 v1 公共实现的命名耦合。P2 已将这些类型和单臂 builder 迁入 `runtime_common.py`，`runtime_dual.py` 保留兼容导出；类名与 `Stage3` 控制器 ID 仍属冻结契约，不随模块迁移改名。

### 4.2 实施步骤

1. 针对两种 builder 简要列出相同操作和关键差异，确认是否存在完整的单臂装配边界。
2. 若边界稳定，提取私有的单臂构建函数；输入限定为单元视图、影子别名、障碍句柄、station/conveyor aliases、IK controller、日志上下文等真实差异，不设计任意插件注册机制。
3. 保留 `build_stage3_runtime()` 与 `build_v1_runtime()` 两个公开入口和各自场景级障碍策略。不要把两个运行时合成一个带大量布尔开关的 builder。
4. 将公共运行时类型迁入语义中立的模块（例如 `runtime_common.py`）仅在迁移确实减少反向命名依赖时实施；更新导入后保留旧导入兼容层，直到全仓库调用方迁移完毕。
5. 将启动摘要生成与碰撞对象创建分开审查。摘要字段是诊断和验收接口，不应因内部类移动或局部变量重命名而变化。
6. 运行现有 Stage3 与 v1 runtime 相关测试，检查启动摘要、碰撞集合范围和正常销毁结果。

### 4.3 验收

- Stage3 双臂与 v1 五臂运行时各自启动摘要、碰撞集合范围、关节/工具配置及销毁报告保持一致。
- 其他机械臂碰撞障碍物策略没有被错误共享；Stage3 不能意外包含 v1 全部工作单元，v1 不能退化为只检查相邻臂。
- 若抽取引入了比原来更多的条件参数或让场景差异更难读，则不合并该部分。

## 5. P3：可选维护项

### 5.1 合并相邻阶段模块

**实施结果 (2026-11-06)**：A/B 两组均已迁移。`Stage3UnitController`/`TrayCheck` 位于 `unit_controller.py`，`build_stage3_runtime()` 位于 `runtime.py`；旧模块保留只做 re-export 的兼容层，Stage3 错误类型独立为 `Stage3RuntimeBuildError`（错误码仍为 `STAGE3_RUNTIME`），v1 错误类型仍为 `RuntimeBuildError`（`STAGE4_RUNTIME`）。

本项只评估 `unit_controller_dual.py` 与 `unit_controller.py`、`runtime_dual.py` 与 `runtime.py` 两组相邻阶段模块。目标是让继承链和公开入口更容易浏览，减少跳转；不以减少文件数量为验收标准。两组分开实施，任一组收益不足都可保留原文件。

#### A. 合并控制器模块

迁移前 `Stage3UnitController` 位于 `unit_controller_dual.py`，只有 `unit_controller.py` 导入它；`application/coordinator.py` 另有一条未使用的导入（本次已删除）。`TrayCheck` 是 Stage3 控制器的局部结果类型。

1. 实施前重新检索全仓库对 `unit_controller_dual`、`Stage3UnitController` 和 `TrayCheck` 的导入与符号引用，确认没有遗漏的动态导入、命令行入口或文档约定；确认 coordinator 中的 Stage3 导入没有其他引用后，随迁移删除该未使用导入。
2. 将 `TrayCheck` 和 `Stage3UnitController` 移入 `unit_controller.py`，放在 Stage4 控制器之前；保持类名、继承关系、构造参数、状态字段、日志及对外行为不变。不得借迁移合并 Stage2/Stage3/Stage4 的状态机或改写业务流程。
3. 更新模块文档和导入位置。若全仓库与文档检查确认没有外部路径承诺，可删除 `unit_controller_dual.py`；若需要兼容旧模块路径，则让该文件仅 re-export `Stage3UnitController` 与 `TrayCheck`，不复制实现，并单独记录未来移除条件。
4. 复用现有 Stage3/Stage4 控制器测试及直接覆盖受影响导入路径的测试。只有现有测试不能证明迁移后的导入或继承仍有效时，补一个最小测试；不新增重复状态组合测试。

#### B. 合并 runtime 入口模块

迁移前 `runtime.py` 与 `runtime_dual.py` 都是场景级入口，并已共享 `runtime_common.py` 的单臂构建；差异集中于场景类型、障碍物策略、启动摘要及错误类型。本次已合并入口文件的导航边界，两个 builder 仍分别保留，没有改成由大量模式开关驱动的通用 builder。

1. 实施前完整检索 `runtime_dual`、`build_stage3_runtime`、`RuntimeBuildError` 的导入和动态引用。检索时测试直接通过 `runtime_dual` 路径访问 Stage3 builder 和错误类型；本次已同步迁移测试导入，并保留兼容导出。
2. 将 Stage3 的场景专属障碍函数和 `build_stage3_runtime()` 移入 `runtime.py`，保留 `build_v1_runtime()`；分别保留 Stage3/v1 的停止态错误语义、场景别名、碰撞集合范围、障碍元数据和启动摘要。
3. 处理同名 `RuntimeBuildError` 的模块差异：在统一模块内为 Stage3 使用语义明确的独立异常名（例如 `Stage3RuntimeBuildError`），保留 v1 的 `RuntimeBuildError`；迁移时保持 Stage3 的 `STAGE3_RUNTIME` 与 v1 的 `STAGE4_RUNTIME` 错误码及现有异常消息/包装行为。不得仅为共享错误类型而弱化错误归属。
4. 保持 `runtime_common.py` 作为单臂装配边界。若 `runtime_dual.py` 没有外部兼容承诺，且代码、测试与文档调用方都已迁移，可删除该入口文件；否则将其收敛为从 `runtime.py` 导入 Stage3 builder 和类型，并将统一模块的 `Stage3RuntimeBuildError` 以旧名 `RuntimeBuildError` 导出的兼容 shim，避免重复实现。
5. 运行现有 Stage3 与 v1 runtime 聚焦测试，验证停止态拒绝、构建错误类型、启动摘要和各自障碍策略。仅当这些测试无法覆盖具体搬迁行为时补最少测试，不扩展到故障注入或资源回滚场景。

#### 明确暂不合并

- `config_base.py`、`config_motion.py`、`config_dual.py` 与 `config.py`：配置按层承载不同摘要/场景契约，不因共享常量而合并。
- `scene_base.py`、`scene_dual.py` 与 `scene.py`：派生层和场景规模不同，合并会扩大单文件职责并隐藏阶段差异。
- `layout_base.py`、`layout_dual.py` 与 `layout.py`：schema、消费者和场景布局不同，需维持各自契约边界。
- `unit_controller_base.py` 与 `unit_controller_motion.py`：分别承载基础业务流程与运动联锁能力，是否提取共性须由具体重复算法证据驱动，不纳入文件合并。
- `agv_route.py` 与 `agv.py`：前者的直线路段控制器由后者组合使用，职责并非重复。
- `runtime_common.py` 与场景级 runtime 入口：公共单臂装配和场景障碍策略边界清楚，继续合并会重新造成跨场景条件分支。

### 5.2 运行时资源回滚

`runtime_common.py` 的 `_ensure_queryable()`（P2 前位于 `runtime_dual.py`）为真实的其他机械臂连杆和吸盘记录原始 `collidable`/`measurable` 值，并把属性设置为可查询。Stage3 与 v1 的 runtime builder 将这些记录保存在 `query_restore` 中；正常销毁时由 `Stage3Runtime.destroy()` 尝试恢复原值。

如后续维护需要覆盖构建中断后的资源状态，可单独补充异常路径恢复。本项暂缓，不作为 P1/P2 的前置工作，也不扩展为故障恢复体系。落点为 `runtime_common.py` 的单臂 builder 与两个公开入口的清理路径。

触发实施时，复用已有快照恢复逻辑，在两个 builder 的清理路径恢复已记录属性，并保留原始构建异常。测试只需一个聚焦用例验证属性恢复，再运行相关 runtime 测试；如两个 builder 的行为确实不同，再拆分测试。

### 5.3 规划器模块边界与性能优化

**实施结果 (2026-11-06)**：已按职责拆分为 `motion/plan_types.py`（计划状态/预算/结果）、`motion/plan_checks.py`（`ConfigurationChecker`）、`motion/plan_search.py`（`LocalPlanner`）、`motion/plan_postprocess.py`（平滑/压缩）。`path_planning.py` 保留 re-export；检查顺序、容差、预算和算法未改。性能优化未实施，本次没有性能证据支持调整采样或缓存。

拆分前 `motion/path_planning.py` 集中保存计划数据结构、`ConfigurationChecker`、`LocalPlanner` 和路径点平滑/压缩等逻辑，文件较大。当前已有明确的类和函数边界，但文件大小本身不足以证明需要重写。

实施前先用导入关系和测试确定模块公共接口。若拆分可行，可按以下职责迁移：计划类型/状态、配置与路径检查、候选路径搜索、成功计划后处理。原 `path_planning` 模块暂时保留 re-export，降低调用方迁移风险。一次只迁移一个职责，不同时改变算法和模块路径。

不凭文件大小或“远程 API 看起来慢”调整规划采样、容差或预算。先使用已有规划统计与 `motion/metrics.py` 证据定位耗时，再决定是否优化缓存、碰撞查询或仿真 API 调用。

每个性能改动必须记录相同场景、起点/目标、碰撞模型和运动配置下的前后数据，至少比较规划墙钟、采样数/碰撞查询数、路径质量与最终执行结果。只缩短计算时间但扩大碰撞漏检、改变成功路径语义或破坏预算失败报告的改动不接受。

## 6. 命名、目录与疑似遗留代码

### 6.1 命名和归类

**实施结果 (2026-11-06)**：已补充包级架构说明（`simulation/__init__.py`）和 [仿真模块清单](../reference/simulation-module-inventory.md)。本阶段不重命名 v1 模块；清单记录了公开入口、v1 生产调用、阶段基础复用、兼容层及未引用模块的判定。

当前 `scene.py`、`layout.py`、`unit_controller.py` 实际为 v1 五岛实现；`scene_base.py`、`scene_dual.py` 等名称记录了实现来源或继承层级。目录已将 `motion/` 和 `execution/` 单独分包，职责清晰；没有证据要求继续按每个文件新建子目录。

整理步骤：

1. 建立模块清单，标注公开入口、v1 生产调用、阶段基础复用、测试专用和未引用五类。
2. 优先补充包级架构说明，解释 Stage1/2/3 命名表示能力来源，不自动表示已废弃。
3. 新代码使用产品职责命名；只有现有名称持续导致误导且调用方可完整迁移时，才分批重命名 v1 模块。
4. 重命名后更新 `src/`、测试、文档、CLI/工件契约涉及的字符串与导入。类名、控制器 ID、manifest 字段不得因文件改名而意外改变。

### 6.2 过时模块判定

**实施结果 (2026-11-06)**：`device_handle_map.py` 经全仓库导入扫描确认无 `src/`、`tools/` 或应用层消费者，也无外部 API/文档承诺；已删除该模块及仅覆盖它的用例。同测试文件中的 `scene_base.arm_joint_modes_ok` 契约测试已迁至 `tests/test_v1_base_arm_joint_modes.py`，覆盖保留。模块清单记录了删除依据。

`device_handle_map.py` 在本计划制定时未见生产调用，引用主要来自该模块测试；其能力是动态句柄表，与 `device_binding.py` 的静态设备/alias 绑定不同，不能直接合并。本次删除前的核实项：

1. 检查公开 API、历史调用和文档承诺，确认是否仍供外部工具或工件脚本使用。
2. 确认其测试是否只验证独立旧工具，还是保护当前场景契约。
3. 若确认无消费者，删除模块及仅服务于它的测试；若仍有消费者，则迁至清晰的场景句柄缓存边界并更新名称。

`ur5_ik.py`、`component_builder.py`、`unit_controller_base.py` 等虽然名称带历史语义，但存在 runner、测试或继承链调用，不按本计划删除。

## 7. 验证要求与稳定性约束

只运行与修改直接相关的现有测试：控制器改动覆盖对应取料/运动路径；runtime 改动覆盖对应 Stage3/v1 构建路径；planner 或导入路径改动覆盖被迁移模块的测试。现有测试覆盖不足时，仅增加能证明新行为的最小用例。

下列约束贯穿所有阶段：

- 不修改受保护场景工件，不手工改写 manifest 或 `scene.ttt`。
- 不无意改变 `contract.config_digest()`、`layout_digest()`、稳定 alias、控制器/runner ID、场景契约字段。
- 不增加第二个 `sim.step()` owner，不让 `simulation/execution/` 反向依赖 `application/`。
- 不改变碰撞、运动和物料安全判据。
- 每个阶段只运行与改动直接相关的测试；若直接测试通过且没有新增影响面，不继续扩展验证范围。

## 8. 非目标

- 不为统一命名而一次性迁移整个 `simulation/` 包。
- 不把 Stage1/Stage3 代码整体移入 `legacy/`；当前存在真实复用链。
- 不将多层控制器强行合并为一个配置驱动的“大状态机”。
- 不把 Stage3 与 v1 runtime 合并为一个带大量模式开关的通用框架。
- 不因 `path_planning.py` 文件大而改规划算法、阈值、采样密度或时间预算。
- 不删除只有测试导入的模块，除非完成消费者与兼容承诺核实。
- 不在本计划中引入新的抽象注册系统、插件系统或与当前问题无关的防御机制。

## 9. 执行完成标准

计划可按阶段推进，不要求一次完成所有 P3 项目。P1/P2 只有在共享边界明确且对应测试证明无回归时才实施；否则记录对照结论并保留原结构。P3 项目均为可选维护事项，不作为 P1/P2 的前置条件；文件合并应逐组评估导航和维护收益，其中资源回滚完善暂不优先安排，待出现相关维护需求时再处理。
