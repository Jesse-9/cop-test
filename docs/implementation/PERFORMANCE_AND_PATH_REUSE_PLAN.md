# 仿真与运动规划效率提升方案

## 1. 结论

当前系统可以把“已验证路径直接执行”做成现有规划器外部的执行策略。为了保持可复用性，路径复用不修改 `LocalPlanner` 的搜索、IK、碰撞检查职责，只负责选择“使用缓存路径”还是“调用现有规划器”。

建议只保留两种正式模式：

1. **`CACHE_THEN_PLAN`**：精确匹配缓存路径时直接执行；没有匹配或回放前置条件不满足时，调用现有 `LocalPlanner`。
2. **`PLAN_ONLY`**：始终调用现有 `LocalPlanner`，用于规划基准和缓存正确性对照。

固定回归测试可以额外提供 **`CACHE_ONLY`**，缓存失效就失败，不自动规划。暂不引入“相似路径检索”“自动局部修复”或“路径经验图”。

这会把“规划计算”和“路径执行”解耦：重复测试时优先复用已通过的关节路径，只有环境或任务条件发生变化时才重新规划。

## 2. 当前瓶颈与可观测性

仓库已有几个重要基础：

- `LocalPlanner.plan()` 已有墙钟和采样预算，默认 `PLAN_WALL_BUDGET_S=60 s`、`PLAN_MAX_TOTAL_SAMPLES=6000`。
- 带载转运使用独立的 `TRANSFER_PLAN_WALL_BUDGET_S=150 s`。
- `ConfigurationChecker.check_segment()` 对每个样本执行工作区、载荷、接触和分支约束；显式自碰撞检查按 `SELF_CHECK_STRIDE=4` 跳采样。
- 规划成功后仍会执行最终整段复核，随后可能执行路径点压缩；因此“找到一条路径”和“获得可执行路径”不是同一耗时阶段。
- `JointStreamExecutor` 从实际关节状态逐步逼近 waypoint，并对实际状态到下一点再次检查；这保证了安全，但会使执行测试耗时明显高于纯路径计算。

现有验收证据中，`full_flow` 约为 337.45 仿真秒、6732 步。这个数字包含传送带、等待、AGV、机械臂执行、稳定窗口和规划，不应直接当作规划耗时。第一步应新增分阶段剖析字段：

| 阶段 | 必须记录的指标 |
|---|---|
| 规划入口 | `request_id`、动作阶段、起点/目标摘要、规划模式 |
| IK | seed 数、候选数、IK 调用次数、IK 墙钟 |
| 粗筛 | 配置检查次数、碰撞查询次数、粗筛墙钟 |
| 候选搜索 | DFS/beam 节点、候选策略、失败原因、墙钟 |
| 最终复核 | 采样数、复核墙钟、最小净距 |
| 路径处理 | 压缩删除点数、平滑是否执行、处理墙钟 |
| 执行 | waypoint 数、命令数、仿真步数、实际墙钟、等待/稳定时间 |

没有这组数据时，不建议直接把 `PLAN_WALL_BUDGET_S` 调大或把采样步长调粗，因为可能只是执行步数或 CoppeliaSim 远程调用占据主要时间。

## 3. 路径复用的边界

### 3.1 可以直接回放的路径

以下动作最适合缓存：

- 固定工位的 `pick`、`pre_place`、`place`、`retreat`；
- 固定载荷几何和固定工具姿态下的法向接近/撤离；
- 固定布局中的空载 `home -> station` 或站间转移；
- 固定 `TRANSFER_HEIGHT`、工具姿态和载荷包络的水平转运。

路径记录应保存**关节 waypoint**，而不是只保存笛卡尔位姿。当前执行器直接消费关节路径，且笛卡尔路径需要重新 IK；缓存关节路径可以省掉 IK 和候选分支搜索。

### 3.2 不能无条件直接回放的路径

- 物料在传送带上的实时位置发生变化；
- AGV、托盘、工件或障碍物的位姿发生变化；
- 携带物料后碰撞包络发生变化；
- 起点关节与缓存起点偏差超过执行容差；
- 腕部分支锚点、锁定关节策略、工具 yaw 或目标接触语义不同；
- 故障注入、运行时障碍、场景模型/碰撞集合更新后。

这些情况应使用回放后复核或重新规划。特别是带载路径不能只用“机器人关节路径相同”判断有效，因为载荷会改变碰撞几何和接触约束。

### 3.3 路径缓存的完整键

建议使用以下键对路径做精确匹配：

```text
robot_model_hash
scene_artifact_id + scene_hash
collision_model_hash
tool_model_hash
payload_geometry_hash
motion_config_hash
phase / semantic_segment
start_q_rounded
goal_pose_or_goal_q_rounded
branch_anchor
lock_policy / lock_joint
transfer_height / tool_yaw
planner_contract_version
```

`scene_hash` 应覆盖障碍物几何、布局和碰撞集合；`motion_config_hash` 应覆盖采样、容差、速度、加速度、jerk 和接触阈值。只要这些字段变化，就不能进入严格回放。

## 4. 推荐的路径库设计

新增 `simulation/motion/path_library.py`，保持纯 Python，不直接依赖 CoppeliaSim。建议数据结构如下：

```python
@dataclass
class PathRecord:
    path_id: str
    key: dict
    waypoints: list[list[float]]
    segments: list[dict]
    start_q: list[float]
    goal_q: list[float]
    goal_pose: list[float] | None
    branch_anchor: str | None
    min_distance: float | None
    planner_version: str
    validation: dict
    execution_stats: dict
    created_at: str
    use_count: int = 0
    failure_count: int = 0
```

路径文件建议使用 JSON，保存到独立的可替换目录，例如：

```text
docs/path_library/v1/<scene_id>/<phase>/<path_id>.json
```

不要把路径缓存直接写进场景 `scene.ttt`。场景工件可以升级，路径库可以按版本失效；两者分离能避免缓存污染验收工件。

第一版路径库只需要提供：

- `put(plan, validation, execution_stats)`：只保存最终复核通过的计划；
- `exact_match(request)`：按完整键查找；
- `record_result(path_id, status, wall_s, deviation)`：记录成功与回放失败；
- `export/import()`：用于回归测试和跨机器复制。

缓存失效不需要另做一套失效管理器。只要场景、碰撞模型、载荷、运动参数或契约版本发生变化，完整键就不会匹配；旧文件可以保留，后续再做人工清理。

## 5. 路径复用阶段与现有规划阶段的边界

现有运动语义阶段（例如抓取、抬升、转运、放置、撤离）保持原名，不在本方案中重命名。现有 `LocalPlanner` 内部的 `direct`、中转、绕行、IK 候选和最终复核也保持不变。

新增阶段统一使用 `PR`（Path Reuse）前缀，避免与项目已有 `stage0`–`stage5` 或运动语义混淆：

| 阶段 | 含义 | 负责模块 |
|---|---|---|
| `PR_LOOKUP` | 查找精确匹配的已验证路径 | `PathReusePolicy` |
| `PR_REPLAY` | 将缓存 waypoint 交给现有执行器 | `PathReusePolicy` + `JointStreamExecutor` |
| `PR_PLAN_FALLBACK` | 缓存不可用时调用现有规划器 | `LocalPlanner` |
| `PR_DONE` | 路径执行完成 | 控制器 |

`PR_PLAN_FALLBACK` 是策略阶段名称，不是新的规划算法；真正的规划仍然只发生在 `LocalPlanner.plan()` 中。

## 6. 回放状态机

建议在 `LocalPlanner.plan()` 外增加一个策略层，不把缓存逻辑塞进碰撞检查器：

```text
REQUEST
  -> PR_LOOKUP
       -> exact hit + preflight pass -> PR_REPLAY
       -> miss / preflight fail -> PR_PLAN_FALLBACK

PR_REPLAY
  -> existing JointStreamExecutor guards pass -> PR_DONE
  -> execution failure -> fail or explicit fallback, according to mode
```

建议新增配置：

```python
PATH_EXECUTION_MODE = "cache_then_plan"
# "plan_only" | "cache_only" | "cache_then_plan"
PATH_REPLAY_STRICT = True
PATH_REPLAY_START_TOL = 0.02
PATH_CACHE_DIR = "docs/path_library/v1"
```

`cache_only` 只适合固定场景回归；如果缓存失效应明确失败，不要偷偷改成规划成功。`cache_then_plan` 适合日常测试，能减少重复规划，同时保留现有规划器作为明确兜底。

## 7. 最小必要检查

为避免重复实现现有安全逻辑，回放只增加一次轻量前置检查：

1. 当前场景/碰撞模型/载荷快照与记录匹配；
2. 当前关节与记录 `start_q` 的差值在 `PATH_REPLAY_START_TOL` 内；
3. 当前动作的语义段和缓存记录一致。

通过后直接交给现有 `JointStreamExecutor`。执行器已有的偏差、超时、分支和安全联锁继续生效，不再为路径复用新增一套 `check_segment` 全路径复核。

因此，路径复用节省的是重复的 IK、候选搜索、碰撞规划和最终计划生成；它不绕过执行期已有保护。

## 8. 对现有规划器的提速顺序

### 7.1 先做低风险、确定性优化

1. **先做路径回放**：固定工位重复测试直接复用完整关节路径。
2. **再做耗时统计**：只测 IK、候选搜索、最终复核和执行四类时间，确认是否值得做更细缓存。
3. **最后考虑 IK 缓存或批量远程调用**：只有数据证明规划仍是瓶颈时再增加。

### 7.2 适度减少不必要的搜索

- 已知固定工位优先尝试 `direct_only=True`；失败后再启用中转/绕行策略。
- 对每个语义段保存历史成功策略，例如 `direct`、`horizontal_detour_+0.30`，优先尝试最近成功策略。
- 自由目标的 IK 候选按“同腕部分支 + 关节距离 + 预计下一段代价”排序，减少无效 DFS 节点。
- 保持当前在线规划逻辑不变；路径复用只在规划器外部选择执行来源。

### 7.3 需要基准测试后再启用的优化

- 将 `CARTESIAN_CHAIN_SEARCH` 从 `greedy` 切为有限宽度 beam，只在多解明显时启用；beam 会增加候选检查，可能提升路径质量但不一定缩短规划时间。
- 使用粗到细检查：先用简化碰撞包络和较稀采样筛选，再对候选路径使用当前完整检查；必须证明简化模型不会漏掉验收碰撞。
- 将路径时间参数化交给 Ruckig/TOPPRA 类工具，但只作用于已验证几何路径，不能替代碰撞验证。
- 只有在本机存在 GPU、部署链路允许且能保持 CoppeliaSim 场景一致时，评估 cuRobo/cuMotion；当前项目是 Python + CoppeliaSim 4.10 + ZMQ，直接引入 GPU 规划器的集成成本和模型一致性风险都较高。

## 9. 暂不纳入第一版的方案

OMPL PRM/LazyPRM、相似路径检索、局部修复、经验图和 GPU 规划器都可以作为后续实验，但不属于当前提速闭环。它们会引入新的状态管理、失效规则和测试维度；在精确路径回放已经能满足重复测试之前，不建议加入。

## 10. 测试加速分层

建议把测试分成四类，而不是所有测试都跑完整物理闭环：

| 测试层 | 规划模式 | 物理仿真 | 目的 |
|---|---|---|---|
| 单元/契约 | 不启动 CoppeliaSim，fake checker/IK | 无 | 验证状态机、缓存键、失效规则 |
| 运动回归 | `cache_only` | 可关闭视觉和非必要动力学 | 验证已验证路径和执行器 |
| 规划基准 | `plan_only` | 影子臂/简化场景 | 测量 IK、碰撞、搜索耗时 |
| 集成验收 | `cache_then_plan` | 完整物理、传送带、AGV、故障 | 验证端到端安全与业务结果 |

重复的 `full_flow` 测试使用 `cache_only`；规划器回归单独使用 `plan_only`。不增加“每 N 次强制重规划”的隐式测试规则，避免把两类测试混在一起。

## 11. 建议的实施顺序

### PR0：路径导出

- 给 `LocalPlanner`、`check_segment`、IK、最终复核和 `JointStreamExecutor` 增加 monotonic timer；
- 在 `MotionPlan.as_dict()` 中保存完整 waypoint、段语义、场景/配置摘要；
- 增加命令或测试工具：`dump-plan`、`replay-plan`；
- 建立 3 个固定动作的基准：pick、place、transfer。

验收：能回答一次动作耗时中 IK、碰撞检查、最终复核和执行各占多少。

### PR1：缓存回放

- 实现 `PathRecord` 和完整键；
- 在控制器入口加入 `plan_only/cache_only/cache_then_plan`；
- 回放前做快照匹配和起点检查，执行中保持现有联锁；
- 对 `cache_only` 的缓存失效提供明确失败码。

验收：固定场景重复运行时规划调用为 0，任务结果与在线规划基线一致；故障注入或键不匹配时缓存被拒绝。

### PR2：可选兜底

- 在 `cache_then_plan` 模式下，缓存不匹配时调用现有 `LocalPlanner`；
- 不做 waypoint 平移，不做局部修复，不做相似路径推断。

验收：缓存命中时不规划，缓存未命中时行为与当前在线规划完全一致。

## 12. 量化验收指标

建议把下面指标加入每次运行证据：

- `planning_wall_s`: p50/p95/max；
- `ik_wall_s`、`collision_wall_s`、`final_verify_wall_s`；
- `cache_hit_rate`、`cache_reject_rate`、`fallback_plan_rate`；
- `plan_success_rate`、`path_min_clearance`、`max_deviation`；
- `sim_steps`、`execution_commands`、`wait_steps`；
- 端到端墙钟和仿真时间的比值。

目标值应从现有基线测出后再定。一个合理的第一阶段目标是：固定场景回归的规划调用减少 80% 以上，端到端墙钟减少 30% 以上，同时所有安全/任务断言与在线规划基线一致；这只是工程目标，不应替代实测结果。

## 13. 相关资料与项目

- CoppeliaSim Path and Motion Planning / `simOMPL`：说明 OMPL 任务建立、`compute`、路径简化和 PRM 多查询复用。
- OMPL PRM 文档：说明 roadmap 可跨查询复用，`clearQuery()` 保留 roadmap 并清除查询状态。
- OMPL Planner 文档：区分多查询 PRM、LazyPRM 和单查询树型规划器。
- MoveIt 2 Benchmarking：提供规划时间、路径长度、路径时间和成功率等统一基准指标。
- Lightning：路径库检索/修复与从零规划并行，面向重复机械臂任务。
- Thunder / Experience Graphs：把历史路径组织成图，提高重复查询的复用率并降低响应时间波动。
- NVIDIA cuRobo/cuMotion：GPU 并行 IK、碰撞检查和轨迹优化，适合作为后续硬件条件允许时的独立规划后端评估。

## 14. 最终建议

近期只实现 **PR0 + PR1**：路径导出、精确键、缓存回放和明确失败码；日常运行再启用 **PR2** 作为现有在线规划兜底。

这样新增代码只有一个外层 `PathReusePolicy` 和一个纯数据 `PathRecord`，现有 `LocalPlanner`、运动语义阶段和执行器接口保持稳定。路径回放不重复实现规划检查，也不引入自动修复链路。
