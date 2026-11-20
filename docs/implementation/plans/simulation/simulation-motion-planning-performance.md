---
title: 仿真与运动规划效率提升方案
type: plan
tags:
  - 类别/实施计划
  - 类别/运动规划
created: 2026-11-05
updated: 2026-11-20
status: partially-implemented
scope: v1 跨阶段技术提案
---

# 仿真与运动规划效率提升方案

## 1. 目标与范围

目标是缩短重复仿真测试的等待时间，同时保留现有机械臂碰撞、接触、腕部分支和执行期安全约束。本方案先区分启动、规划、执行和业务等待时间，再为固定测试动作增加已验证关节路径回放。

本方案不替换现有规划算法，不改碰撞阈值、采样密度、速度参数或安全契约。路径回放是规划器外部的一种执行来源；缓存不能匹配时，继续使用现有 `LocalPlanner`。

## 2. 当前机械臂规划方式

当前机械臂规划由项目内的 `LocalPlanner`、`ConfigurationChecker` 和 `JointStreamExecutor` 完成，未使用 OMPL 作为当前生产规划器。

一次抓取/放置动作大致分为以下过程：

1. 控制器按动作阶段生成末端目标位姿，例如抓取前位、接触位、抬升位、放置前位和放置位。
2. IK（逆运动学）把末端目标位姿换算为关节角。根据目标自由度和当前关节状态，规划器使用一个或多个初始猜测，筛选并排序有限个候选解。
3. `LocalPlanner` 在候选关节构型间尝试允许的局部策略。直线路径、指定的笛卡尔段或受限中转路径会按关节/笛卡尔采样展开。
4. `ConfigurationChecker` 检查构型与路径段，包括碰撞、净空、接触几何、腕部分支和载荷条件。候选路径通过后，规划器对最终路径再次完整复核。
5. `JointStreamExecutor` 根据实测关节位置逐步发出命令。它限制单次关节步幅，并在执行期间检查小段路径、关节偏差和超时。

主要代码入口：

| 模块 | 职责 |
|---|---|
| `simulation/unit_controller_motion.py` | 按抓取、抬升、转运、放置、撤离等业务动作生成目标并调用规划器 |
| `simulation/motion/plan_search.py` | IK 候选组织、局部搜索、预算管理和最终计划复核 |
| `simulation/motion/plan_checks.py` | 单构型与路径段安全检查 |
| `simulation/motion/trajectory.py` | 按实测状态逐步执行已生成的关节路径 |
| `simulation/config_motion.py` | 采样、候选、预算和执行限制 |

关键现状参数包括：关节路径检查最大采样间隔 `2°`，候选筛选间隔 `8°`，末端笛卡尔采样间隔 `2 mm`，法向接近段 IK 步长 `10 mm`，每次执行关节步幅上限 `0.20 rad`。这些参数用途不同，不应仅为提速而统一调大。

### 2.1 当前插值和平滑

- `motion/trajectory.py::cartesian_poses()` 对笛卡尔位置做直线插值，对姿态四元数做 SLERP；抓取/放置的法向接近段因此保持直线语义。
- 规划得到的是关节 waypoint。`JointStreamExecutor` 逐段向 waypoint 推进，并把 `[velocity, acceleration, jerk]` 交给 `sim.setJointTargetPosition`；当前臂执行器没有调用 `sim.moveToConfig`。
- `motion/plan_postprocess.py::smooth_plan_waypoints()` 是内部点三点平均平滑，不是贝塞尔曲线，且 `TRAJECTORY_SMOOTHING_ENABLED=False`，默认关闭。
- `compact_plan_waypoints()` 删除通过 FK 偏差检查的冗余点，默认开启；压缩只减少路径点，不改变语义段端点。

### 2.2 贝塞尔曲线判断

贝塞尔曲线可以让自由空间段的几何转角更圆滑，但**不等于规划更快**：它不省略 IK，也不自动避障；曲线改变后仍要检查实际曲线路径上的机械臂、吸盘和载荷。关节空间曲线可能让 TCP 偏离预期，笛卡尔曲线则可能破坏抓取/放置段必须保持的法向直线。

因此当前结论是：

- 不把贝塞尔曲线作为路径规划算法或路径回放的替代品；
- 不用于 `pick`/`place` 接触段、法向接近/撤离段以及有固定高度/姿态约束的转运段；
- 只有计时和执行指标显示自由空间路径存在明显折角、方向突变或过多停顿时，才在独立实验分支评估自由空间段的曲线后处理；
- 曲线几何验证通过后，还需确认速度、加速度、jerk 和实际执行步数没有恶化。若只是需要满足运动学速度约束，先评估现有关节目标运动参数，不因此引入完整曲线规划体系。

## 3. 先区分耗时

| 耗时 | 包含内容 | 适合的改进方向 |
|---|---|---|
| 启动耗时 | 场景准备、工件校验、影子机械臂与碰撞集合构建 | 复用同一仿真器会话；按启动阶段计时 |
| 规划耗时 | IK、候选搜索、碰撞采样、最终计划复核 | 精确路径回放；根据剖析结果减少重复计算 |
| 动作仿真耗时 | 机械臂移动、传送带/AGV运行、物料稳定等待 | 减少测试中不必要的物理步骤；区分动作和等待 |
| 墙钟耗时 | 启动、Python 与 ZMQ 通信、仿真推进及报告写入的总等待时间 | 与仿真时间、步骤数和规划耗时一起分析 |

现有启动计时证据 `v1_startup_timing_noload_20261113_215332.json` 报告总启动墙钟约 `88.6 s`；其中场景准备、启动校验和影子运行时构建是明显阶段。阶段计时有包含关系，不能把它们简单相加。此数据说明端到端等待不能全部归因于路径规划。

现有规划器记录单次总规划时间、检查配置数和采样数，但缺少稳定的 IK、候选搜索、最终复核分段计时。当前没有足够证据判断全流程的主要瓶颈一定是规划计算。

## 4. 路径回放适用范围

### 适合

- 固定场景、固定机器人和工具；
- 固定抓取/放置目标；
- 固定起始关节状态或可重复复位到同一状态；
- 固定载荷几何和动作语义段；
- 主要用于重复测试控制器执行或测试业务流程，而不是测试规划算法。

这类路径保存已通过现有规划器复核的**关节 waypoint**，回放时可省去重复 IK 和候选搜索。

### 暂不适合

- 传送带上位置变化的物料；
- 位姿变化的 AGV、托盘、工件或障碍物；
- 载荷、工具、抓取点或动作目标变化；
- 要专门验证在线规划是否还能找到路径的测试。

这些情况继续走现有在线规划。支持移动目标路径变换或相似路径修复需要额外算法和验证，当前不纳入第一版。

## 5. 复用边界与阶段命名

现有业务运动阶段名称和 `LocalPlanner` 内的规划策略名称保持不变。新增复用阶段使用 `PR`（Path Replay）前缀：

| 阶段 | 含义 |
|---|---|
| `PR_LOOKUP` | 查找适用于当前固定测试动作的路径记录 |
| `PR_REPLAY` | 将路径记录交给现有执行器 |
| `PR_PLAN_FALLBACK` | 无匹配路径时调用现有 `LocalPlanner` |
| `PR_DONE` | 动作执行完成 |

第一版只提供三种模式：

| 模式 | 行为 | 用途 |
|---|---|---|
| `PLAN_ONLY` | 始终运行现有规划器 | 规划器回归和性能基准 |
| `CACHE_ONLY` | 只回放匹配路径；未命中即失败 | 固定动作回归，确保测试确实走缓存 |
| `CACHE_THEN_PLAN` | 先尝试缓存，未命中时运行现有规划器 | 日常重复测试 |

路径策略放在规划器调用点外层，不加入 `ConfigurationChecker`，不修改 `LocalPlanner.plan()` 的搜索行为，也不复制业务控制器状态机。

## 6. 路径记录和匹配

首版使用 JSON 记录完整关节 waypoint、动作阶段、起点/终点、场景工件版本、机器人/工具/载荷标识、运动参数版本、腕部分支及生成时的规划结果。运行期 `MotionContext` 要从当前动作请求重建，不能把之前运行的 Python/CoppeliaSim handle 写入文件。路径文件独立于 `.ttt` 场景工件保存，例如：

```text
docs/path_library/<scene-version>/<motion-phase>/<path-id>.json
```

只实现精确匹配，不做最近邻或相似路径搜索。匹配至少要求场景版本、机器人/工具、载荷、动作阶段、起点和目标一致。运动参数版本使用已有 `MOTION_OPT_VERSION`、`TRANSFER_TRAJECTORY_VERSION` 及相关参数值形成记录，不向 `config*.py` 随意增加大写常量，以免影响现有 `config_digest()` 契约。任何一项不一致即视为未命中：`CACHE_ONLY` 返回清晰失败；`CACHE_THEN_PLAN` 调用原有规划器。

第一版数据接口控制在：

- `PathRecord`：路径及其匹配信息；
- `PathLibrary.get_exact(key)`：精确读取；
- `PathLibrary.put(record)`：保存规划成功并已通过最终复核的路径。

暂不建设数据库、缓存清理服务、使用次数排名或跨场景迁移机制。文件版本不匹配自然失效，旧文件可以人工清理。

## 7. 回放时保留的检查

回放前仅检查：

1. 路径键与当前场景/机器人/工具/载荷/动作阶段一致；
2. 当前关节与记录起点在既有执行容差内。

通过后交给现有 `JointStreamExecutor`。执行器已有的关节偏差、每步快速碰撞检查、腕部分支与超时行为继续生效，不再增加第二套全路径复核或路径修复流程。

注意：回放只跳过重新求解与重新搜索，不能取消现有执行期联锁。故障注入、动态物料和动态障碍测试不应使用固定路径回放。

## 8. 实施阶段

### P0：用现有数据建立动作基线

- 先运行现有 `tests/test_v1_motion_path_planning.py`、`tests/test_v1_motion_trajectory.py` 和 `tests/test_v1_motion_agv_transfer.py`，记录基线结果；不要先改运动参数。
- 复用 `MotionPlan.wall_s`、`samples`、`configs` 和 `JointStreamExecutor.execution_metrics()` 中已有的执行步数、命令数及仿真持续时间。
- 用一个固定抓取段和一个固定放置/转运段作为样本，从报告里分别抄录规划耗时、执行时间、步数和命令数；同时标注运行是否包含完整场景启动。
- 只有 `plan.wall_s` 明显偏高、且重复动作有稳定相同的起终点时，继续做 P1；若启动或执行等待占主导，应转到启动性能方案或执行阶段分析，不扩大本方案。
- 暂不新增逐碰撞查询日志。若现有汇总字段无法区分候选搜索与最终复核，才在 `MotionPlan` 增加一个可选 `timings` 汇总字段，仅记录 `search_wall_s`、`verify_wall_s` 和 `postprocess_wall_s`；不在每个控制 tick 输出日志。

验收：用一条动作报告能判断规划与执行哪一项耗时较高；至少记录固定抓取和固定转运各一次。若计时结果显示启动主导，不把结论写成规划器慢。

### P1：固定路径导出与回放

- **P1.1 导出器**：新增纯数据模块 `simulation/motion/path_library.py`，只序列化已成功且最终复核通过的 `MotionPlan`。首版只保存 JSON waypoint、语义段名称/点数、起终点、场景工件 ID、机器人/工具/载荷类型和运动配置版本；不保存 CoppeliaSim 临时 handle。
- **P1.2 回放请求**：回放仍由当前控制器构造实时 `MotionContext`（目标物料、接触标志、载荷状态等），缓存只提供关节 waypoint 和静态段信息。若无法从当前动作上下文重建所需 context，则当作缓存未命中，不猜测或恢复旧 handle。
- **P1.3 策略接入**：先只选一个固定工位、固定目标的抓取或放置段接入 `PLAN_ONLY` / `CACHE_ONLY` / `CACHE_THEN_PLAN`。调用顺序放在 `unit_controller_motion.py` 的该段 `_build_plan()` 分支内，或提取一个窄的 `PathReusePolicy`；不要一次性改写所有控制器和 `LocalPlanner`。
- **P1.4 执行**：命中后用当前实时 `MotionContext` 组装可执行计划并交给原 `JointStreamExecutor`。保留现有执行期快速碰撞检查、偏差、超时和分支联锁；不新增完整曲线或全路径复查。
- **P1.5 失效处理**：匹配键至少包含场景工件 ID、机器人/工具/载荷类型、运动阶段、起始关节、目标摘要和运动配置版本。`CACHE_ONLY` 不匹配就返回明确状态；`CACHE_THEN_PLAN` 不匹配就只调用一次现有 `LocalPlanner`。
- **P1.6 后续扩展门槛**：首个固定动作命中/回退闭环通过后，再考虑第二个动作；不要为了“通用”提前支持最近邻匹配、自动变换、局部修复或数据库。

建议按以下工作区顺序落地，每步只改必要文件：

| 顺序 | 工作区文件/入口 | 具体操作 | 完成条件 |
|---|---|---|---|
| 1 | `simulation/motion/plan_types.py::MotionPlan.as_dict()` | 确认 waypoint、段名/点数、终点和计划标记足以回放；只补缺字段，不序列化动态 `contexts` | 成功计划可写入并重新读取 JSON，精度满足现有容差 |
| 2 | `simulation/motion/path_library.py`（新增） | 实现 `PathRecord`、`get_exact(key)`、`put(record)`；使用标准库 JSON/文件 API，不引入数据库 | 读写、缺文件、损坏 JSON、版本不匹配都有确定结果 |
| 3 | `simulation/unit_controller_motion.py::_build_plan()` | 选一个固定工位、固定目标的动作段接入三种模式；先查缓存，命中时用当前请求生成的 context 与缓存 waypoint 组合计划 | 只有试点动作进入复用逻辑，其他动作保持原调用路径 |
| 4 | `simulation/unit_controller_motion.py::_run_motion()` | 保持原 `JointStreamExecutor` 初始化、逐步检查、报告和失败联锁；只增加 `plan_source=cache|online` 来源字段 | 两种路径来源使用同一执行器，运行报告可区分来源 |
| 5 | `tests/test_v1_motion_path_reuse.py`（新增） | 用 fake plan/planner 覆盖命中、键不匹配、`CACHE_ONLY` 失败和 `CACHE_THEN_PLAN` 单次回退 | 离线测试，不启动 CoppeliaSim、不复制碰撞算法 |
| 6 | `tests/test_v1_stage2_arm_sim.py` 或现有固定场景 harness | 同一固定目标分别运行 `PLAN_ONLY`、`CACHE_ONLY`，比较目标误差、任务结果与执行联锁 | 两种来源通过相同业务/安全断言，报告规划调用数和耗时 |

策略通过运行请求或函数参数传入，不新增全局配置常量，不改 `config_digest()`、`layout_digest()`、场景工件或 `sim.step()` 所有权。

验收：固定场景回放和在线规划到达同一目标容差；缓存命中时 `LocalPlanner.plan()` 调用次数为 0；键不匹配时 `CACHE_ONLY` 明确失败、`CACHE_THEN_PLAN` 与当前规划结果一致。执行期检查仍由原执行器完成。

### P2：按计时证据选择后续优化

只有在 P0 显示在线规划仍是主要耗时时，才选择一个具体热点：

1. `search_wall_s` 占主导：检查该固定动作失败前尝试了哪些策略，再评估优先尝试已知成功策略；不删掉失败兜底。
2. `verify_wall_s` 占主导：先确认是否对同一路径重复执行了多次最终复核；不通过减小碰撞采样密度解决。
3. IK 占主导：只针对固定目标测量候选 seed 是否反复求出相同解，再评估有限范围的 IK 结果缓存；动态目标不共用缓存。
4. 执行步数或仿真等待占主导：去运动执行方案评估控制周期和测试等待；这不是贝塞尔曲线能直接解决的问题。

每轮只改一个因素，用相同场景、起点、目标、故障设置重复对照，报告规划成功率、路径终点误差、最小净距、规划墙钟、命令数和仿真执行时间。若收益不稳定或安全/终点指标退化，则不合入。

若 P0 显示需要改善自由空间轨迹平滑度，再进行 P3；否则结束在 P1/P2。

### P3：贝塞尔后处理小范围验证（可选，不属于提速基线）

仅针对一个静态、无接触、无载荷的自由空间运动段，作为 `plan_postprocess.py` 中独立的实验后处理函数，不改变 `LocalPlanner` 搜索和默认参数：

1. 从一条已通过在线规划的固定段导出 waypoint，保留段首、段尾和所有语义边界；不跨 `pick`、`lift`、`transfer`、`place`、`retreat` 等段拟合一条曲线。
2. 用三次贝塞尔曲线拟合这段关节 waypoint 的中间形状，首版不做自动调控制点，也不添加多种曲线阶数配置。
3. 按不大于现有关节采样间隔对曲线取样；将相邻曲线采样点逐段交给现有 `ConfigurationChecker.check_segment()`，检查机械臂、载荷/吸盘、腕部分支和净空。不得只检查原始 waypoint 或曲线端点。
4. 检查采样 FK 的 TCP 偏差、终点精度、关节限位以及现有速度/加速度/jerk 指标。任何检查失败就放弃曲线，使用原始路径；第一版不做自动局部修复。
5. 只比较同一条基准路径的原始和贝塞尔版本：路径长度/关节总变差、最大速度/加速度/jerk、实际执行时间、命令数及最小净距。
6. 用一个离线单元测试覆盖端点保持和碰撞拒绝；再用一个固定场景实机动作做视觉/指标确认。若仅视觉更圆滑但执行时间、平滑指标或净距无改善，不启用默认开关。

结论门槛：贝塞尔是可选的路径后处理，不是“更快规划”的承诺。第一版维持 `TRAJECTORY_SMOOTHING_ENABLED=False`；只有 P3 的单段实验通过全部几何和执行指标后，才单独讨论是否把贝塞尔作为配置选项。

参考依据：贝塞尔在这里仅作为“已有路径的平滑后处理”评估，而不是避障算法；曲线生成后仍需沿实际曲线做碰撞和运动约束检查。CoppeliaSim 的 `sim.moveToConfig` 使用 Ruckig 在线轨迹生成，但当前项目的机械臂执行器通过 `setJointTargetPosition` 控制，因此不能假定换成该 API 与现有分步控制等价。

## 9. 测试范围

采用三个必要测试层，不让每次测试重复跑完整系统：

| 测试 | 模式 | 目的 |
|---|---|---|
| 缓存逻辑单元测试 | fake 记录与 key | 验证命中、版本不匹配、起点不符和未命中行为 |
| 固定动作回归 | `CACHE_ONLY` | 验证路径执行结果和现有执行期联锁 |
| 规划器/端到端验收 | `PLAN_ONLY` 或 `CACHE_THEN_PLAN` | 覆盖在线规划及完整业务行为 |

首版不增加大量排列组合测试。缓存新增测试集中在一个文件 `tests/test_v1_motion_path_reuse.py`，覆盖缓存命中、键不匹配和一次原规划兜底；复用 `tests/test_v1_motion_trajectory.py`、`tests/test_v1_motion_safety.py` 的既有检查，不复制其测试矩阵。贝塞尔实验只在 P3 被触发时，向 `tests/test_v1_motion_optimization.py` 增加端点/边界和碰撞拒绝用例。

## 10. 不纳入本方案的优化

- 不降低碰撞采样密度、接触阈值或安全容差；
- 不对移动目标做路径平移、相似匹配或局部修复；
- 不引入 OMPL PRM/经验图或 GPU 规划器；
- 不在没有剖析证据时增加 IK/碰撞缓存；
- 不通过关闭安全检查来换取测试速度。

这些方向只有在固定路径回放和计时基线不足以解决实际瓶颈时，才单独立项评估。CoppeliaSim 的 `simOMPL` 插件和 OMPL PRM 可作为不同场景下的候选技术，但当前项目实现的是自有 `LocalPlanner`，引入新的规划器不等于自动提速。

## 11. 决策

按 **P0 → P1 → P2** 实施。第一目标是让固定测试动作不重复规划，同时保留在线规划的独立回归覆盖。路径复用只有在固定场景与目标匹配时才命中；动态目标继续使用当前规划器。测量确认瓶颈后再做后续优化，不先改采样与安全参数。

贝塞尔曲线不进入 P0/P1 的路径复用提速实现；仅在现有数据明确显示自由空间折角或平滑指标有问题时，按 P3 做单段实验。

## 12. 工作区内参考

- [仿真模块代码优化报告](../../reports/simulation/v1-simulation-code-optimization.md)：规划器已按 `plan_types`、`plan_checks`、`plan_search`、`plan_postprocess` 拆分；性能参数未随模块拆分改变。
- [v1 启动性能优化方案](v1-startup-performance-optimization.md)：启动开销单独计时；不得跳过 `prepare_start` 全量只读校验或改变 `config_digest()` / `layout_digest()`。
- [轨迹与路径规划模块知识页](../../../wiki/03-运动规划与控制/03-05-轨迹与路径规划模块.md)：CoppeliaSim 路径、Ruckig、`moveToConfig` 和 TOPPRA 概念参考；其中 API 示例不代表当前 Python 控制器已使用这些 API。
- [运动规划模块拆分回归](../../../../tests/test_v1_motion_module_layout.py)：约束公共 `path_planning` re-export 与内部模块身份。
- [CoppeliaSim `sim.moveToConfig` 手册](https://manual.coppeliarobotics.com/en/sim/simMoveToConfig.htm)：说明该 API 使用 Ruckig 且是线程脚本中的阻塞调用；不是当前执行器使用的接口。
- [机械臂 RRT*-Connect 与 Bézier 路径平滑研究](https://pmc.ncbi.nlm.nih.gov/articles/PMC11754615/)：将三次 Bézier 用作已有规划路径的分段平滑，支持把它定位为后处理实验，而非替代避障规划。
- [安全走廊内的 Bézier 曲线优化研究](https://arxiv.org/abs/2310.01190)：说明曲线平滑需要同时定义优化目标、端点连续条件和安全走廊约束。

## 13. 实施状态（2026-11-20）

P0 计时字段与 P1 固定路径回放已代码落地，实施事实见
[运动规划性能实施报告](../../reports/simulation/v1-motion-planning-performance.md)：

- `MotionPlan` 新增 `timings`（`search_wall_s` / `verify_wall_s` /
  `postprocess_wall_s`）、`plan_source` 与 `replay`；总墙钟仍由
  `wall_s` 表示，且成功计划的 `wall_s` 现在覆盖后处理与最终复核。
- 新增 `simulation/motion/path_library.py`（记录版本
  `v1_path_record_20261120`）与 `simulation/motion/path_reuse.py`
  （策略版本 `v1_path_reuse_20261120`），实现 `PLAN_ONLY` /
  `CACHE_ONLY` / `CACHE_THEN_PLAN`；首版只接入 `PLACING` 单目标段。
- 策略默认不注入，运行入口不传时保持在线规划；不新增
  `config*.py` 常量，不改变 `config_digest()` / `layout_digest()`。
- 离线测试已覆盖命中、键不匹配、损坏记录、版本不符、起点/分支容差、
  `CACHE_ONLY` 失败与 `CACHE_THEN_PLAN` 单次回退。
- 2026-11-20 focused CoppeliaSim 对照已执行：同一 `housing_direct_A`
  任务的固定 A 臂 `PLACING` 段先 `PLAN_ONLY` 导出，再重载场景
  `CACHE_ONLY` 回放；两次任务均 `DONE`、无安全锁存，回放规划器调用为 0，
  目标误差均在同一容差内。证据见实施报告和
  `docs/implementation/evidence/simulation/v1_motion_path_reuse_20261120.json`。
- 仍**未执行**：P0 的固定抓取段与抬升/转运段计时、第二个固定动作、
  移动/动态目标回放、故障注入，以及 P2/P3。现有证据不能证明全流程
  规划是主要瓶颈，也不能宣称全流程提速或完整验收。
