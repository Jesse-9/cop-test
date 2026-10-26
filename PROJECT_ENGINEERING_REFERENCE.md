# cop-test 工程参考

本文件保留工程约束、模块职责、阶段实现细节和仿真实测陷阱，供相关代码任务按需查阅。它不是项目状态页或运行手册：当前验收状态见根目录 README，操作命令见 docs/wiki/98-ProjectUsage/USAGE.md，MCP 操作见 docs/wiki/98-ProjectUsage/MCP_USAGE.md，阶段方案见 docs/wiki/06-工业案例/06-05-柔性工业车间分阶段执行步骤.md。实现变化时更新对应权威文档，避免在此重复命令清单和状态摘要。

基于 CoppeliaSim 4.10+ ZeroMQ Remote API 的柔性工业车间仿真系统，
并附带一个本地 MCP 服务器，让 Claude Code 能像调用函数一样编排仿真生命周期。

## 三层架构

```
┌─────────────────────────────────────────────────────┐
│  MCP Tool Layer — reference/factory_mcp_server.py   │  ← Claude Code 接入点
│  (FastMCP / stdio, 24 个工具, 默认 stage1 后端, 仿真跑在守护线程) │
└───────────────┬─────────────────────────────────────┘
                │ 调用
┌───────────────▼─────────────────────────────────────┐
│  Python Control Layer                              │
│  stage1/ (当前) — 单元闭环: config/physics/scene    │
│                   material/conveyor/UR5/suction/AGV │
│                   unit_controller/run              │
│  stage2/ (当前) — 影子碰撞/局部规划/流式执行/联锁   │
│                   contact/collisions/shadow/path_   │
│                   planning/trajectory/safety/run    │
│                   branch/metrics (阶段 2.01)        │
│                   diagnostics/整链/路径变体 (2.02)  │
│  stage3/ (当前) — 双工位/资源锁/AGV 两站转运        │
│                   layout/scene/resources/tasks      │
│                   agv_route/runtime/unit_controller │
│                   coordinator/scene_contract/run    │
│  reference/ (历史) — factory_controller/island/AGV  │
│  reference/factory_robot.py — 关键帧兼容层          │
│  reference/factory_types.py — 枚举/数据类/物料      │
└───────────────┬─────────────────────────────────────┘
                │ ZMQ Remote API (同步, stepping 模式)
┌───────────────▼─────────────────────────────────────┐
│  ZeroMQ Remote API Layer — reference/factory_core.py│
│  CoppeliaConnector / ZMQMonitor / setup_logger      │
└─────────────────────────────────────────────────────┘
                │ TCP localhost:23000
        ┌────────▼────────┐
        │  CoppeliaSim 4.10+ │  (3D 物理引擎 + 内置模型)
        └──────────────────┘
```

**时钟主导者**: Python 端始终是 clock master。`sim.setStepping(True)` 后，
每次 `sim.step()` 推进一个 `SIM_STEP` (默认 0.05s)，控制逻辑与物理引擎严格同步——
这是精确工业节拍时序的基础。

## 柔性车间重构工程规范 (强制)

以下规范适用于 `docs/wiki/06-工业案例/06-04-柔性工业车间重构方案.md` 和
`06-05-柔性工业车间分阶段执行步骤.md` 对应的新实现。它们是工程不变量，
不是可选的实现建议；若旧版源码、场景或文档与本节冲突，以本节和已审核方案为准。

### 1. 基线和变更边界

- 当前 `main` 可能没有旧版 `src` 控制层。新实现可以直接按审核方案重新建立源码和模块边界；
  删除前的提交仅作为模型路径、4.10 已知问题、参数测量和测试经验的参考，不是必须恢复的
  代码基线，也不应限制新架构设计。
- 实施仍应建立独立 `codex/*` 分支，并在实施记录中注明参考过的历史提交和实际采用的经验。
- 不得覆盖用户未提交的 `.ttt` 场景、日志、文档或配置；不得用 `git reset --hard`、
  `git checkout --` 等破坏性命令回退。
- 先完成阶段一单元闭环，再进入阶段二物理与运行基础能力，随后进入阶段三双工位，
  最后进入阶段四多岛车间；阶段验收未通过时不得通过临时脚本绕过接口进入下一阶段。
- 场景结构、物理参数、TCP 偏移、轮距、轮半径或传感器容差发生变化时，必须重新执行
  受影响阶段的标定和回归测试。

### 2. 内置模型和生产模式

- 传送带必须使用内置 `generic conveyor (belt).ttm`；机械臂必须使用内置六轴
  `UR5.ttm`；末端必须使用 `suction pad.ttm`；AGV 必须使用
  `pioneer p3dx.ttm`。
- 生产模式只允许 `HYBRID_DYNAMIC`。不得把“纯脚本/纯位置驱动”作为正式生产模式；
  物理输送不足时的补偿必须沿传送带切线、有速度上限并记录原因。
- 内置模型缺失时可以在诊断阶段使用 fallback primitive，但生产构建必须失败并明确报警，
  不得静默把指定内置模型替换成几何体。
- 内置模型加载后必须检查并剥离会造成 CoppeliaSim 4.10 启动冻结或与外部控制冲突的
  customization/child script；不能同时保留模型脚本和重复的 Python 控制逻辑。

### 3. 场景命名、句柄和传感器

- 实际别名必须是扁平下划线形式；禁止把 `/` 路径传给 `setObjectAlias`，禁止依赖
  `getObject("/...")` 作为运行时句柄查询。
- 设备句柄在启动时一次性建立并缓存，使用 alias-leaf 匹配；每步循环不得重复枚举全场景
  或进行无必要的 ZMQ 往返。
- 固定位置检测必须使用 proximity sensor。传感器命中只表示物理条件满足，不能直接触发
  吸附；必须同时校验当前任务的物料 UID/句柄、允许的物料类型和工位归属。
- 传感器、目标 dummy、TCP、物料和工位都必须有唯一 alias，并在启动时做唯一性检查。
- Vision Sensor 仅用于未来的颜色、型号或未知位置识别，不得作为固定位置搬运的隐含依赖。

### 4. 物料和传送带物理关系

- 物料默认是 `dynamic + respondable`，有明确质量、摩擦和碰撞掩码；不得用永久
  `setObjectPosition` 瞬移替代物理验证。
- conveyor 的尺寸只允许在构建阶段测量、缩放和定位；仿真启动后不得改变模型尺寸。
  入口、停止区和传感器必须按 pad 有效承载区计算，不得按外包围盒端点放置物料。
- 必须区分模型外包围盒长度和 pad 有效运行长度；长距离优先串联多个内置皮带，而不是
  无限制等比放大单段模型。
- HYBRID_DYNAMIC 补偿只能在传感器确认物料仍在有效区时沿带面切向进行，并限制最大
  修正速度；超过偏差阈值应进入 `CONVEYOR_FAULT`，不得继续强推。
- 生产日志不得把 4.10 中未验证的摩擦效果描述为“真实摩擦输送”；必须记录实际驱动源
  和是否发生补偿。

### 5. UR5、simIK 和 TCP

- 新方案的取料、抬升、放置必须使用 `simIK` 求解六个关节角，再通过关节目标控制执行；
  禁止用关键帧或直接设置末端位姿冒充 IK 成功。
- 每台 UR5 使用独立 IK environment/group，主求解失败时才使用阻尼备用组；两组失败
  必须返回明确错误，不得写回伪造角度。
- 吸盘接触面中心必须建立专用 `SuctionPad_*_TCP` dummy，作为 IK tip、距离检测和
  放置参考点；不能把 UR5 connection 或吸盘外壳原点未经标定地当作 TCP。
- 到位判定必须同时检查 TCP 世界坐标误差、姿态/倾角、关节限位、碰撞状态和末端速度；
  只检查关节角属于不合格验证。
- 每次改变 UR5 基座、工具偏移、取料点或放料点，都必须重新执行可达性和 TCP 世界坐标
  回归测试。

### 6. suction pad 吸附和释放

- 当前阶段不模拟真实真空压力、流量或吸力；吸附采用“TCP 到位 + suction sensor 命中
  指定 UID + 姿态/速度/碰撞检查”的逻辑流程。
- 禁止启用内置 suction 脚本的全场景自动吸附行为；它可能吸附 conveyor pad 或错误物料。
- 吸附前必须保存物料父对象、位姿、dynamic/respondable、质量、摩擦和碰撞掩码；吸附
  期间仅关闭物料动态响应，释放后必须完整恢复并 `resetDynamicObject`。
- 吸附、释放和物料状态转换必须写入同一 UID 的生命周期日志；没有状态证据不得报告任务完成。

### 7. pioneer p3dx 和载荷

- pioneer p3dx 必须保留内置车体、轮子、质量、惯量和差速结构；不得用 cuboid fallback
  作为生产 AGV。
- AGV 必须通过左右轮 `setJointTargetVelocity` 驱动；禁止在运行循环中用
  `setObjectPosition` 或 `setObjectPose` 瞬移车体。
- 载荷放到 AGV 后默认保持 `dynamic + respondable`，依靠载台接触、摩擦、低矮挡边和
  受限加速度运输；禁止默认把载荷 parent 到 AGV 后宣称具有物理运输效果。
- 装卸必须经过 dock sensor、车体速度稳定、物料位姿和载台区域检查；AGV 到站不等于
  物料已经交接。
- 任何质量、摩擦、轮速上限、轮距或载台几何变化，都必须重新进行直线、转向、停车和
  载荷滑移测试。

### 8. 控制循环、调度和验收

- Python 是 stepping clock master；控制器必须保持“异常/传感器 → conveyor → 物料 →
  IK/UR5 → 吸附/释放 → AGV 轮速 → 调度/日志 → `sim.step()`”的可追踪顺序。
- 任务必须以物料 UID、资源锁和状态机驱动，不能只根据屏幕位置或对象 alias 推断完成。
- AGV 任务执行期间不抢占；低优先级任务可等待升级，但不得打断当前物理运动。
- 每阶段必须交付场景/构建代码、配置和 alias 清单、控制模块测试、运行日志、故障注入
  结果和未解决问题；阶段退出门槛未通过不得标记完成。
- 最低验收项包括：无 `state=8` 冻结、无 0 关节、无错误吸附、无 AGV 位置瞬移、无
  物料 UID 丢失、无未记录的物理属性变更。

### 9. 文档和代码一致性

- 新实现必须同步更新方案文档、分阶段执行文档、模块参考和本文件；代码、场景和文档
  不一致时不得以“后续再补文档”作为交付理由。
- 任何“已修复/已验证”描述必须有可复现测试或仿真日志；设计目标必须明确标记为“方案”
  或“待实现”，不能把计划写成当前能力。
- 新增模块和参数必须集中配置、可追踪、可测试；不得在控制循环内散落硬编码坐标、尺寸、
  物理参数或容差。

## 关键模块职责

### 历史五岛完整场景层（旧版，位于 `src/reference/`，阶段一不调用）

| 文件 | 职责 | 核心导出 |
|------|------|---------|
| [factory_config.py](src/reference/factory_config.py) | 所有位置/尺寸/节拍/阈值集中配置 | 常量字典 |
| [factory_core.py](src/reference/factory_core.py) | ZMQ 连接+重连、健康监控、日志 | `CoppeliaConnector`, `ZMQMonitor`, `setup_logger` |
| [factory_types.py](src/reference/factory_types.py) | 枚举(物料类型/AGV状态/岛状态)、数据类、物料生成 | `ItemType`, `AGVState`, `IslandState`, `Material`, `spawn_material`, `lerp_2d` |
| [factory_island.py](src/reference/factory_island.py) | 工作岛状态机、节拍追踪、供料门控 | `IslandController`, `BeatTracker`, `SupplyController` |
| [factory_agv.py](src/reference/factory_agv.py) | 单一共享 AGV + 优先级调度 (B→A 高优先, D→E 低优先, 饥饿可升级) | `AGVScheduler` |
| [factory_exceptions.py](src/reference/factory_exceptions.py) | 统一异常轮询与紧急停止 | `ExceptionHandler` |
| [factory_robot.py](src/reference/factory_robot.py) | 历史机械臂设备/关键帧兼容层；新重构必须改用独立 UR5 + simIK + TCP 控制，不得以关键帧伪装 IK 成功 | `RobotArmDevice`, `RobotArmAnimator`（历史接口） |
| [factory_controller.py](src/reference/factory_controller.py) | 顶层编排器, 按固定顺序驱动每个步进 | `FactoryController` |
| [build_scene.py](src/reference/build_scene.py) | 场景构建 (分层命名 /Warehouse /Island /Conveyor /Mobile) | `build_scene()`, `clear_scene()` |
| [run_simulation.py](src/reference/run_simulation.py) / [main.py](src/reference/main.py) | CLI 入口 | `main()` |
| [factory_mcp_server.py](src/reference/factory_mcp_server.py) | MCP 服务器, 暴露 24 个工具给 Claude Code (默认 stage1 后端, legacy 可选; A 读取/B 诊断/C 控制 + 场景工件生命周期 + 双连接) | `FactoryMCPServer`, `mcp` |

> 全部源码平铺在 [src/](src/) 下（基础层 / 控制层 / 视觉层 / 接入层 / CLI 入口），
> 项目概览见 [README.md](README.md)；当前源码结构以源码目录为准。

### 阶段一单元闭环（当前实施范围，2026-10-17 通过）

阶段一严格限定为：一条内置传送带、一个内置 UR5、一个内置 suction pad、
一个内置 pioneer p3dx、一个指定物料、工位与 AGV 载台两个目标。
82 项阶段一测试通过；正常流程、60 s 连续运行与六类故障注入均有实机证据。
入口与证据见 [PHASE1_REPORT.md](docs/implementation/PHASE1_REPORT.md)。

**2026-10-20 起生产布局切换为步骤 1.1 选定的 `L1_S1`，取料流程切换为 B 组：**
UR5 基座 `(-0.20, 0.55, 0.30)`、置物台 `(-0.20, 1.20)`、AGV dock `(0.40, 0.75)`；
场景**不构建挡停体**。`Sensor_PickStation` 命中目标 UID 后立即下发
conveyor 零速，等待带面和物料稳定，再按仿真器实测位姿生成一次固定的
TCP 抓取目标并执行 simIK 接近/吸附。旧 `stage1_baseline` 布局与挡停体
仅作为步骤 1.1 A 组历史证据保留，不再是生产场景。

| 文件 | 职责 |
|------|------|
| [config.py](src/stage1/config.py) | 阶段一全部位置/别名/模型路径/速度/容差/超时/故障枚举 |
| [layout.py](src/stage1/layout.py) | `LayoutSpec`、候选布局派生坐标与静态几何校验 |
| [physics.py](src/stage1/physics.py) | static/kinematic/respondable/mass/friction/mask 工具 |
| [scene.py](src/stage1/scene.py) | 单工位场景构建、脚本处理、传感器、场景契约断言；`adopt_scene()` 加载后重建句柄 |
| [scene_artifact.py](src/stage1/scene_artifact.py) | 版本化场景工件、manifest schema 1.0、摘要与只读校验 |
| [lifecycle.py](src/stage1/lifecycle.py) | 生命周期状态机、跨进程会话状态/运行锁、build/save/load/validate/start/stop 与 CLI |
| [device_handle_map.py](src/stage1/device_handle_map.py) | 启动期唯一别名/句柄表 |
| [material_manager.py](src/stage1/material_manager.py) | 7 状态物料生命周期与 UID 事件历史 |
| [conveyor_controller.py](src/stage1/conveyor_controller.py) | 内置 conveyor 驱动 + HYBRID_DYNAMIC 受限补偿 |
| [ur5_ik_controller.py](src/stage1/ur5_ik_controller.py) | 独立 simIK environment、主/备组、seed 回退、关节目标 |
| [suction_controller.py](src/stage1/suction_controller.py) | UID/传感器/TCP/倾角/速度检查、属性快照与恢复 |
| [pioneer_controller.py](src/stage1/pioneer_controller.py) | 差速换算、限幅、加速度斜坡、轮速下发 |
| [unit_controller.py](src/stage1/unit_controller.py) | §2.11 单步状态机、故障状态与退避重试 |
| [run.py](src/stage1/run.py) | `run_loaded_scene()`（不 build）+ CLI；默认加载当前工件，`--build` 才重建 |
| [run_stage1.py](src/run_stage1.py) | 兼容入口薄包装 (`python src/run_stage1.py`) |
| [run_scene_lifecycle.py](src/run_scene_lifecycle.py) | 步骤 1.2 生命周期 CLI 兼容入口 |

阶段一模块集中在 `src/stage1/` 包内；安装后也可用 `uv run run-stage1`。
阶段一不变量：

- conveyor 的 customization 脚本必须保留（pad 的唯一写入者）；物料输送必须显式
  标注驱动来源。**步骤 1.1 C 组实测（2026-10-20）**表明当前 stage1 场景在关闭
  位置补偿后物料仍能从入口前进 **0.8298 m** 到达检测区（约 4.2 s 到达下游
  边界，≈0.20 m/s），即 pad→物料存在真实物理输送；因此不得再把“4.10 pad
  不传递切向速度”当作当前事实。补偿必须只在物料落后于带速时施加，并在日志中
  区分物理输送与补偿；当前“每步固定补偿 1 cm”与物理输送叠加会使物料约
  0.38 m/s 前进，是 A 组越程的直接原因，阶段二必须修正。
- UR5 与 suction pad 的所有 shape 为 `static=1, respondable=0`（运动学工具 +
  逻辑吸附）；吸附由 TCP + 吸盘接触传感器 + UID 校验完成；
- `suction pad.ttm` 的内置脚本已剥离，内置 `Sensor` 改名为
  `SuctionPad_01_ModelSensor`，另建 `Sensor_SuctionPad_01`；
- AGV 驱动关节保持 dynamic + velocity，只用 `setJointTargetVelocity`；
  装卸前必须通过 dock sensor、车体停稳与竖直检查；
- 载台/挡边是挂在动态车体上的 kinematic shape；载荷保持 dynamic + respondable，
  验收断言载台 XY/Z 区域与行驶距离。

### 步骤 1.2 场景生命周期（2026-10-20/21 通过）

阶段一场景已固化为版本化工件，`start` 不再隐式 build/clear。实现与证据见
[STEP1_2_REPORT.md](docs/implementation/STEP1_2_REPORT.md) 和
[06-08](docs/wiki/06-工业案例/06-08-柔性工业车间步骤1.2-场景生命周期与运行入口.md)。

- 工件根目录 `docs/scene_artifacts/<phase>/<layout>/vNNN_<stamp>/`，包含
  `scene.ttt` / `manifest.json`（schema 1.0）/ `validation.json` /
  `lifecycle.jsonl`；`.runtime/` 保存会话与运行锁，不入库。
- 当前只读基线：`stage1/L1_S1/v002_20261020_234403`（protected，130 项只读
  校验通过，两次 CoppeliaSim 进程重启后均可加载/校验/运行）。阶段二必须
  从该工件派生 `stage2` 副本，不得覆盖。
- 状态：`DISCONNECTED` / `CONNECTED_STOPPED` / `SCENE_LOADED` /
  `SCENE_VALIDATED` / `SIMULATION_RUNNING` / `ERROR`。运行中拒绝
  build/load/save/validate/重复 start；跨进程由
  `docs/scene_artifacts/.runtime/run.lock`（PID + run_id）拒绝第二路控制。
- `prepare_start` 先取运行锁再做只读校验；校验失败释放锁并进入 ERROR。
- manifest 不含运行时句柄；加载后由 `scene.adopt_scene()` 按 alias 重建
  句柄。配置摘要、模型 sha256、仿真器版本/引擎、对象 alias/类型/父级/
  世界坐标、43 项构建契约任一不匹配都会在 start 前失败并输出差异。
- `save` 默认拒绝“构建后已运行”的非初始位姿；受限快照用
  `--allow-after-run`，且不得作为基线。受保护工件不再追加 lifecycle 事件。
- `--keep-running` 在 CLI 退出前释放运行锁并标记 `background=true`；
  第二路 start 被拒绝，`stop` 负责停止并回收。`refresh()` 自动清理死亡
  PID 的锁文件，`status` 以 `stale_run` 报告崩溃遗留的运行。
- `Stage1Scene.verify()` 含 43 项构建契约，其中 `ur5_joint_modes` 检查
  六关节全部 kinematic（4.10 `getJointMode` 返回二元组已归一化）。

CLI：

具体可复制的场景生命周期命令统一见 [项目使用指南](docs/wiki/98-ProjectUsage/USAGE.md)。本节保留生命周期实现语义与约束。

全局参数（`--host`/`--port`/`--artifact-root`/`--out-dir`）必须写在子命令之前。
`run_stage1.py` 默认加载 `current` 工件；`--build` 才显式重建。

### 阶段二物理约束与运动规划（2026-10-21 通过）

阶段二在阶段一工件上增加：同基座影子 UR5、碰撞集合与离散路径检查、
多解 IK + 有界 DFS 局部规划、流式关节执行、外表面接触判据、保持世界
位姿的释放、速度/pad 位移双判据停带和运行期锁存。阶段一默认调用行为
不变；入口与证据见 [PHASE2_REPORT.md](docs/implementation/PHASE2_REPORT.md)
和 [06-09 §17](docs/wiki/06-工业案例/06-09-柔性工业车间阶段二-物理约束与运动规划.md)。

阶段 2.01（2026-10-21 通过）在此基础上增加腕部分支硬约束、段内固定 q[0]、
候选一步前瞻、逐段关节/腕部指标和可行性点精简；入口与证据见
[PHASE2_01_REPORT.md](docs/implementation/PHASE2_01_REPORT.md) 和
[06-10](docs/wiki/06-工业案例/06-10-柔性工业车间阶段2.01-动作轨迹优化.md)。

阶段 2.02（2026-10-25 实现，实验能力默认关闭）按 06-10 §8 增加 AGV 转运
路径诊断、路径变体、有界整链 beam 选链、分层代价、执行 vel/acc/jerk 剖面
与平滑钩子；默认仍为 `baseline` + `greedy` + 无平滑，2.01 生产行为不变。
入口与证据见 [PHASE2_02_REPORT.md](docs/implementation/PHASE2_02_REPORT.md)。

当前工件：`stage2/L1_S1/v013_20261024_232558`，父
`stage1/L1_S1/v002_20261020_234403`；保存/加载 **145 项只读校验**通过，
父差异 `[]`；`extra.stage2.contract_version = stage2_contract_20261024_v3`，
动作优化版本 `2.01`、AGV 转运优化版本 `2.02`；stage1 `current.json`、v002
scene/manifest 摘要未变化。v009 是阶段二历史工件，v010–v012 是 2.01 过渡
工件，v013 是 2.02 工件；修改 `src/stage2/config.py` 后旧工件会因 digest
失败，必须重新派生。

| 文件 | 职责 |
|------|------|
| [config.py](src/stage2/config.py) | 阶段二/2.01/2.02 全部新参数（接触/碰撞/采样/预算/执行/停带/安全/分支/锁轴/前瞻/转运路径/整链/代价/平滑/诊断） |
| [branch.py](src/stage2/branch.py) | q[4] 归一化腕部分支、奇异边界裕量、跨支拒绝 |
| [metrics.py](src/stage2/metrics.py) | 逐段 TV/最大单步/点数/q[0] 变化/锁定偏差/腕部/TCP 误差；2.02 整链分层代价与执行剖面 |
| [diagnostics.py](src/stage2/diagnostics.py) | 逐 waypoint q/TCP/载荷/分支/净空证据（只读影子探针） |
| [contact.py](src/stage2/contact.py) | 物料局部包围盒、顶面 frame、带符号间隙/横向/杯口/法向 |
| [collisions.py](src/stage2/collisions.py) | 对象角色、碰撞集合、4.10 查询适配、显式自碰撞对、载荷净空 |
| [shadow.py](src/stage2/shadow.py) | 影子 UR5/pad/TCP/载荷副本、FK 一致性、会话资源清理 |
| [path_planning.py](src/stage2/path_planning.py) | 构型/段检查、自适应细分、多解 IK、前瞻选链、分支/锁轴、有界 beam、压缩/平滑、最终复核 |
| [trajectory.py](src/stage2/trajectory.py) | Cartesian 位姿序列、流式执行器、逐段执行指标/profile/采样 trace、保持/取消 |
| [safety.py](src/stage2/safety.py) | `SafetyObservation` / `SafetyLatch` / `request_stop` |
| [conveyor_safety.py](src/stage2/conveyor_safety.py) | 停带监视器、受限补偿、物料稳定 |
| [suction.py](src/stage2/suction.py) | 外表面接触吸附、保持世界位姿释放 |
| [unit_controller.py](src/stage2/unit_controller.py) | 继承阶段一状态机，接入规划/执行/联锁/停带/锁轴/分支锚点 |
| [runtime.py](src/stage2/runtime.py) | 影子臂/集合/检查器/规划器装配与销毁 |
| [scene_contract.py](src/stage2/scene_contract.py) | `extra.stage2` 契约、稳定 manifest、只读校验 |
| [run.py](src/stage2/run.py) | 阶段二 runner、受控障碍注入、报告 |
| [run_stage2.py](src/run_stage2.py) | CLI 薄包装 |

阶段二不变量：

- 阶段二参数只写在 `stage2/config.py`；`stage1/config.py` 保持原值，
  否则父工件配置摘要校验失败。`extra.stage2.config.digest` 守卫阶段二
  参数漂移。
- 影子臂/碰撞集合/载荷副本是**会话资源**，不写入工件；保存前 manifest
  剥离会话句柄，只读校验拒绝持久化的 `Shadow_*`。
- 4.10 实测 `collection self-collision indicator` 对
  `checkCollision(collection,collection)` 无过滤效果，自碰撞使用显式
  非相邻连杆对 + 吸盘 Body 对 link1–5。
- 内置 conveyor 根下 alias `shape` 是整条带粗包围盒（顶面比真实带面高
  ≈10 cm），从查询角色排除并记录原因；真实承载面是 `pad`。
- 接触判据使用物料局部包围盒实测 min/max（不是对象原点到顶面的距离），
  检查带符号间隙、杯口覆盖、横向偏移和工具法向；接近段允许绕法向 yaw，
  最终放置点严格检查完整姿态。
- 带载转运固定为“抬升 → 高位中转（z≥0.80 m）→ 预放置 → 法向放置”；
  载荷只在抬升起点和放置终点允许与承载面接触，中间构型严格检查。
- 规划只返回完整通过粗筛和最终 2° 复核的 `MotionPlan`；预算耗尽返回
  `PLAN_BUDGET_EXCEEDED`，不回退到未检查的直线动作。OMPL 未启用（N/A）。
- 执行器每步只提交经 `check_segment` 复核的下一构型；同一目标不重复
  下发；取消后不再产生命令。真实 q/TCP/载荷偏差或查询未知立即锁存。
- 启动时与每次运动完成时比较真实/影子 7 个可见连杆与 TCP 的世界位姿
  （1e-4 m / 1e-3 rad）；不一致立即锁存。
- 锁存后停带/停车、保持最后实测构型、确认真实停止；不自动恢复、不
  重试。故障恢复属阶段 2.1。
- 生命周期 `start` 按工件 `source_phase` 显式路由 stage1/stage2/stage3
  runner；stage1 runner 拒绝 stage2/stage3 工件。MCP `start_simulation`
  复用同一生命周期/运行锁并在加载 stage2/stage3 工件时路由到对应线程。

阶段 2.01 不变量：

- 腕部分支以 q[4] 归一化角判定（`positive`/`negative`，奇异边界 0/±π），
  `WRIST_BRANCH_MARGIN=0.30 rad`；候选、路径采样、最终复核和执行步进
  共用同一检查入口。跨支/近奇异立即拒绝或锁存，不回退到另一分支。
- 段内固定 q[0] 只作用于 `lift`、`rise`、`place`、`retreat`；通过
  `simIK.setJointInterval` 临时收窄区间实现，`syncFromSim` 后应用、
  `handleGroup` 后恢复，不修改场景关节限位。`pre_place` 实测不可锁
  （station 跨支、AGV 载荷扫带），保留自由 IK。
- 自由目标候选按“本段关节距离 + 下一自由目标一步前瞻”排序；前瞻只用于
  排序，逐段碰撞检查仍是唯一放行依据。后续笛卡尔段从上一构型实际 TCP
  承接，不直接用目标位姿（IK 端点允许 10 mm 误差）。
- 规划优先从上一段已验证计划终点开始（实测偏差 ≤2×`EXEC_JOINT_TOL`），
  让 A/B 与复跑使用同一冻结起点；超限退回实测并记录 `PLAN_START_ACTUAL`。
- 语义 waypoint 永不删除；只压缩段内共线且 FK 偏差 ≤1 mm / 0.01 rad 的
  数值点，压缩后重新执行 2° 全路径复核，失败则恢复原路径。
- `TRANSFER_SPLIT_HIGH_ABOVE_PLACE` 默认关闭：A/B 实测拆分把 station
  计划 TV 提高 29.2%、AGV 提高 11.6%；能力保留，打开前必须重新验收。
- 运行证据必须包含逐段计划/执行 TV、最大单步、点数、q[0] 变化、锁定
  偏差、腕部分支/最小裕量、最小工作区距离和 TCP 误差；失败/锁存段也要
  保留部分实测，不得只报告已完成段。

阶段 2.02 不变量：

- 默认 `TRANSFER_PATH_POLICY="baseline"`、`CARTESIAN_CHAIN_SEARCH="greedy"`、
  `TRAJECTORY_SMOOTHING_ENABLED=False`。2026-10-25 的影子规划 A/B 显示
  transit 偏移 0.10/0.00 m 可把 AGV transfer TV 降低约 47%、q[3] 降低约
  79.5%，但 `rise → transit` 的 q[0] TV 从 1.510 升到 2.065–2.094 rad；
  未满足 06-10 §8.4 B 的 q[0] 下降条件，且候选偏移尚未做 3 次实机重复，
  因此不得切换默认值。
- `transfer_goal_variants` 的 baseline 在任何偏移下都保留 `transit` 语义点；
  只有 high_align 在偏移为 0 时 `transit` 与 `high_above_place` 重合才省略。
  detour 只在直线路径被碰撞/净空拒绝后引入一个高位水平绕行点，并记录引入
  原因和绕行距离；本次所有直线路径均通过，detour 只增加 TV，不默认启用。
- beam 整链搜索与 greedy 使用同一 IK、姿态/偏差判据和 `check_segment`；
  beam 宽度 8、每层候选 6、节点上限 512。预算耗尽返回
  `PLAN_BUDGET_EXCEEDED`，不得静默退化为未比较路径。当前布局 greedy 与
  beam 计划 TV/代价完全相同，不得因“能力存在”而默认打开。
- 分层代价 `J = 1.0·TV_all + 0.5·TV(q[0]) + 0.5·TV(q[3]) + 0.2·B_all +
  0.2·A_all` 只用于候选比较；`A_all` 的规划采样 `dt` 与执行仿真实时
  不可混用，有限位关节不做 wrap。
- 平滑默认关闭；一旦打开，必须对改变后的 q 路径重新执行 2° 全路径
  FK/分支/载荷/碰撞复核，失败则恢复原路径并记录 `reverted_reason`。
- 计划 trace 与执行 trace 必须写入运行证据；执行 trace 超限时等间隔
  降采样并标记 `truncated`。执行 profile 只报告实测 vel/acc/jerk，不因
  目标参数已设限就宣称平顺；2026-10-25 实测 TRANSFERRING jerk 峰值
  120.65 rad/s³ > `EXEC_JOINT_JERK=100`。
- 阶段 2.02 工件必须从 stage1 v002 重新派生；v013 的
  `extra.stage2.agv_transfer_opt` 记录路径策略、偏移变体、绕行、beam、
  分层代价、平滑与诊断字段。修改 `src/stage2/config.py` 后旧工件失败是
  预期保护。

阶段二运行命令和测量探针入口见 [项目使用指南](docs/wiki/98-ProjectUsage/USAGE.md) 与阶段报告；本节只记录规划/执行约束和实验结论。

### 阶段三双工位（当前实施范围，2026-10-25 通过）

阶段三从已验收 stage2 工件只读派生 `stage3` 副本，增加 B 单元、两个 AGV
dock、共享取料区资源锁和 A→B 任务链。A 单元、共享传送带和 AGV 模型不变；
AGV 初始位姿在构建期设为 dock A `(0.40,0.70)` 朝 −Y，运行期仍只通过左右轮
`setJointTargetVelocity` 驱动。入口与证据见 [PHASE3_REPORT.md](docs/implementation/PHASE3_REPORT.md)、
[06-13](docs/wiki/06-工业案例/06-13-柔性工业车间阶段三双工位实施记录.md)。

当前工件：`stage3/L2_MIRROR/v004_20261025_131837`，父
`stage2/L1_S1/v013_20261024_232558`；保存/加载 **229 项只读校验**通过，
父差异为新增 11 个 B/共享 alias、无 alias 移除；109 项场景构建契约通过。
`extra.stage3.contract_version = stage3_contract_20261025_v1`。修改
`src/stage3/config.py` 后旧工件会因 digest 失败，必须从 stage2 父工件重新
派生，不得手改 manifest。

| 文件 | 职责 |
|------|------|
| [config.py](src/stage3/config.py) | 双工位布局/资源表/任务流/AGV 路线/故障注入/验收参数 |
| [layout.py](src/stage3/layout.py) | `L2_MIRROR` 布局、`UnitLayoutView`、候选静态筛选、序列化 |
| [scene.py](src/stage3/scene.py) | 从 stage2 工件增加 B 单元、`Stage3Scene`、构建契约 |
| [resources.py](src/stage3/resources.py) | 6 资源固定顺序锁表、等待/释放/顺序违规日志 |
| [tasks.py](src/stage3/tasks.py) | UID 任务状态机、活动任务去重、拒绝记录 |
| [agv_route.py](src/stage3/agv_route.py) | 直线穿梭、速度剖面、停靠/航向/滑移、BLOCKED/续行 |
| [runtime.py](src/stage3/runtime.py) | 两台影子臂、独立碰撞集合/规划器/锁存、会话资源清理 |
| [unit_controller.py](src/stage3/unit_controller.py) | 传送带/AGV 托盘两种取料源、AGV 保持静止 |
| [coordinator.py](src/stage3/coordinator.py) | 任务编排、资源互斥、AGV 调度、故障注入、验收 |
| [scene_contract.py](src/stage3/scene_contract.py) | `extra.stage3` 契约、布局/配置摘要、只读校验 |
| [run.py](src/stage3/run.py) | 五个验收场景 runner、证据、CLI |
| [run_stage3.py](src/run_stage3.py) | CLI 兼容入口；安装后 `uv run run-stage3` |

阶段三不变量：

- **两臂独立资源**：每台 UR5 使用独立 simIK environment、影子臂、碰撞
  集合、`ConfigurationChecker`、`LocalPlanner` 和 `Stage2Safety`；不得
  共享句柄、IK context 或可变任务状态。另一台**真实**臂的可见连杆与吸盘
  Body 必须加入本臂工作区碰撞集合。另一台真实臂的 `collidable`/`measurable`
  只在会话期启用，`Stage3Runtime.destroy()` 必须恢复原值（不改变
  static/respondable/质量/摩擦/掩码）。
- **一次 `sim.step()`**：阶段三协调器先对两台控制器调用 `pre_tick()`，
  再下发 AGV 轮速、注入故障、`sim.step()`，最后对所有控制器调用
  `post_tick()`（包括已 FAILED 的控制器，以便确认锁存后停止）。不得让
  两台控制器各自 `sim.step()`。
- **资源顺序**：`pick_zone → agv_lane → dock_A → dock_B → arm_A → arm_B`；
  严格按序申请，禁止反向获取。`pick_zone` 在 TCP 退出 conveyor envelope
  后释放；AGV 运输任务持有 `agv_lane`/`dock_B`，B 工位在运输途中记录
  WAIT 但不提前获取，避免死锁。失败/停止释放普通调度锁，安全锁存不由
  资源释放清除。
- **任务 UID 与去重**：任务以 `(kind, uid)` 去重；活动任务重复提交必须
  REJECTED，不得产生第二次吸附/派车。`FAILED`/`BLOCKED` 不得改写成
  `DONE`；任务完成必须由物料状态、父对象/位姿、载台区域和 UID 证据确认。
- **AGV 路线**：x=0.40 直线穿梭，A→B 前进带载、B→A 倒车空载；不在 dock
  处原地旋转。停靠判据为位置 ≤0.02 m、航向 ≤0.08 rad、连续 5 步低速稳定；
  带载滑移 ≤0.02 m。BLOCKED 后必须停车、保留任务/资源；只有无安全锁存、
  障碍移除、位置/航向和资源所有权重新确认才允许 0.08 m/s 续行，不自动重试。
- **物料状态**：A 释放到 AGV 后状态为 `RELEASED`；B 在 dock B 确认同一
  UID 仍在载台后转 `ON_TRAY`，再进入 `READY_FOR_PICK`。不得跳过载台/UID
  校验直接吸附。
- **锁存后冻结**：任一臂安全锁存立即取消轨迹、停带/停车、保持实测构型、
  确认真实停止；其他任务中止并释放普通调度锁，但不得清除 `SafetyLatch`、
  不得自动复位/续作。阶段 2.1 统一恢复仍未实现。

阶段三派生和运行命令见 [项目使用指南](docs/wiki/98-ProjectUsage/USAGE.md)；阶段验收和测量结果见 [PHASE3_REPORT.md](docs/implementation/PHASE3_REPORT.md)。

阶段三实测（2026-10-25，v004）：A→B 闭环 3 任务 DONE，AGV 停靠误差
0.019706 m、航向 −0.000557 rad、载荷滑移 0.0；竞争场景 3 个重复请求被
拒绝、dock_B 等待 1 次；受控阻塞 t=44.85 BLOCKED、t=54.90 续行；不稳定
卸载 `LOAD_DRIFT(0.0400>0.02)` 锁存、unload FAILED、载荷仍
`HELD_BY_SUCTION`。A 装载总 TV 18.732 rad、`pre_place` 的 q[3] TV
4.931 rad；实测 jerk 峰值 113.9–120.7 rad/s³ > 100，MQ-01/02/03 仍未关闭。

### 步骤 1.1 验证包（`src/stage1_1/`，2026-10-20）

阶段一与阶段二之间的输入收敛。**其选定布局 `L1_S1` 与 B 组取料流程已于
2026-10-20 迁移到 `src/stage1/` 生产路径**；本包保留为试验矩阵、冻结门槛
和历史证据的复现入口，不再代表当前生产控制。
`stage1.scene.build_scene(sim, layout=LayoutSpec(...))` 仍可显式传入候选
布局；不传时使用生产布局 `L1_S1`。

| 文件 | 职责 |
|------|------|
| [config.py](src/stage1_1/config.py) | 候选布局、冻结的允许抓取区、试验矩阵与门槛 |
| [layout_eval.py](src/stage1_1/layout_eval.py) | 场景测量、31 个离散 TCP 目标 simIK 扫描、边界余量、选择规则 |
| [baseline.py](src/stage1_1/baseline.py) | 阶段一当前流程的 sensor→零速时序对照 |
| [pickup.py](src/stage1_1/pickup.py) | A/B/C 单步试验：sensor→UID→零速→pad 停止→物料稳定→实测位姿→IK |
| [run_step1_1.py](src/stage1_1/run_step1_1.py) | `measure` / `layout` / `experiment` / `all` CLI |
| [run_step1_1.py](src/run_step1_1.py) | 兼容入口薄包装 |

入口：
```bash
.venv/Scripts/python src/run_step1_1.py --phase all --repeats 10
```

不变量：

- 允许抓取区在试验前冻结，不得按观察结果放宽；判定同时检查 UID、最终位姿、
  物料稳定、pad 停止、IK 和吸附前置条件；
- “sensor 命中”与“仿真器 ground-truth 位姿”是两个不同来源，报告不得混写；
- 只声明离散 IK 可达，不声明路径无碰撞；碰撞/急停/越程验收属于阶段二；
- 最低/最高速度未冻结时只记录为待冻结参数，不自行扩大试验范围。

报告与证据：`docs/implementation/STEP1_1_REPORT.md`、
`docs/implementation/step1_1_*_{stamp}.json{,l}`。

## 单步仿真循环顺序（历史五岛层 FactoryController.run）

> 阶段一的单步顺序由 [unit_controller.py](src/stage1/unit_controller.py)
> 实现，见 06-05 §2.11；下表是旧版五岛完整场景的循环，阶段一不调用。

新重构每个 `SIM_STEP` 必须按以下顺序执行；旧版关键帧动画循环仅作为迁移参考，
不得用于新方案的取放控制：

1. 异常轮询 (ZMQ 通信 / 碰撞 / 传感器 / 工位超时 / 物料溢出 / AGV 拥堵)
2. 若通信暂停 → 仅推进物理引擎, 跳过新的控制命令
3. 读取 conveyor、取料位、吸盘和 AGV dock 传感器
4. 更新 `HYBRID_DYNAMIC` 传送带和物料生命周期
5. 更新工作岛状态、节拍、队列和资源锁
6. 为可执行任务生成 UR5 target pose，调用 simIK 并下发关节目标
7. 执行吸附、抬升、放置、释放及物料物理属性恢复
8. 调度共享 AGV，计算 pioneer p3dx 左右轮目标速度并检查到站
9. 处理 A→C、C→E、D→E 交接和 E 岛配对装配
10. 更新 UID 轨迹、诊断快照、日志和异常恢复状态
11. `sim.step()` 推进物理引擎

## 操作入口

启动要求与阶段命令统一见 [项目使用指南](docs/wiki/98-ProjectUsage/USAGE.md)；MCP 工具行为、输出和诊断方式见 [MCP 使用说明](docs/wiki/98-ProjectUsage/MCP_USAGE.md)。本参考文档只保留实现约束和技术证据，避免重复维护命令。

## 已知问题与坑 (重要)

1. **CoppeliaSim 4.10 别名路径陷阱**: `setObjectAlias` 把含 `/` 的路径转成下划线别名
   (如 `/Mobile/AGV_Shared` → `_Mobile_AGV_Shared`)，导致 `getObject(斜杠路径)` 查询失败。
   代码中用别名匹配方式定位对象 (见 [factory_controller.py:159-213](src/reference/factory_controller.py#L159-L213))。

2. **coppeliaSimVersion 信号可能为 None**: 部分实例配置下该信号未设置，
   `getStringSignal` 返回 None。`CoppeliaConnector.check_alive()` 已加回退探针
   (改用 `getSimulationState()`)，`connect()` 也已容错。

3. **完整场景启动冻结 (已缓解)**: 完整工厂场景 (6 机械臂 + 传送带 + AGV) 启动后约 3 帧处
   仿真冻结 (`state=8`, simTime 卡 0.15), `sim.step()` 不再推进, 报
   "abort execution Connectivity >> ZMQ remote API server@addOnScript"。
   - **根因**: 内置机械臂 (UR5/KUKA iiwa/Franka)、AGV (pioneer p3dx)、夹爪模型各自
     携带 Lua 定制/子脚本, 多份脚本在 4.10 ZMQ addOn 层争用导致阻塞。
   - **缓解 (已实施)**: `build_scene._strip_model_scripts()` 在加载每个模型后剥离其
     脚本子对象 (按 `object_script_type` + 别名含 "script" 匹配); 同时统一全部机械臂
     为 UR5 (6 轴), 不再混用 iiwa/Franka (避免脚本组合冲突 + 关节数不匹配)。
     动画由 Python 端关键帧插值驱动 (`factory_robot.py`), 不依赖模型脚本。
   - 若仍冻结: `stop_simulation` → `inspect_scene_state` 查残留脚本 → `step_simulation`
     手动逐帧定位剩余阻塞脚本; 或用 `list_objects filter="*cript*"` 找脚本对象。
   - MCP 的 `start_simulation` 启动探活: 启动 5s 后若 sim_time 不推进,
     `status` 的 `last_error` 给出冻结告警与排查建议。

4. **机械臂 0 关节 / 动画不播放**: `getObjectsInTree` 在仿真未启动或子树含不可查询
   对象时抛 "Operation cannot be accomplished in current state", 导致采集到 0 关节。
   `RobotArmAnimator._collect_arm_joints()` 已加全场景关节扫描回退 + HOME 姿态预置,
   0 关节时输出 WARNING 而非静默。

5. **AGV 朝向不更新 (已修复)**: 旧版 `_update_scene_positions` 只设 AGV 位置不设朝向,
   小车始终以初始朝向滑行。已加 `_agv_yaw`/`_agv_prev_pos` 跟踪, 每帧按位移向量算
   航向 `atan2(dy,dx)` 调 `setObjectOrientation`, 位移过小时保留上一朝向避免抖动。

6. **双 ZMQ 连接**: MCP 服务器维护两个 `CoppeliaConnector` —— 主连接 (写/控制类工具 +
   主仿真循环) 与 `ro_connector` (只读诊断工具 `get_object_pose`/`list_objects`/
   `read_signal`/`get_object_tree`/`inspect_scene_state`/`get_simulation_state`/`run_lua`,
   经 `ro_lock` 隔离)。仿真运行中, `status` 走主连接调 `check_alive` 可能瞬时返回
   False (连接被主循环占用), 属正常现象; 只读诊断工具走 `ro_connector` 不受影响
   (这正是双连接设计的目的, 解决旧版假断连)。详见 [docs/wiki/98-ProjectUsage/MCP_USAGE.md](docs/wiki/98-ProjectUsage/MCP_USAGE.md)。

7. **`setObjectParent` 清掉 `static` → 机械臂掉落 (已修复)**: 4.10 实测,
   `sim.setObjectParent()` 会重算被挂对象**自身**的动力学标志 —— UR5.ttm 的模型根
   是 static+kinematic 的固定基座 (`loadModel` 后 `static=1`), 挂到工作岛下之后
   `static` 被清成 0, 整条机械臂变成受重力的动力学体, 仿真开始即自由落体
   (实测 2.5s 掉到 z=-24m, 穿过工作台也穿过地板)。挂到 dummy 与挂到 shape 同样会清掉。
   - 视觉上之所以是"从平台上掉下去": 工作岛台面由 `createPrimitiveShape` 建,
     4.10 默认 `respondable=0` (不参与碰撞), 托不住任何东西。
   - **修复**: [build_scene.py](src/reference/build_scene.py) 的 `load_builtin_model()` 挂父前
     记录根 shape 的 `static`, 挂父后写回 `setObjectInt32Param(h, shapeintparam_static, 1)`
     (4.10 的 `static` properties 路径不可用, 只能走 int-param, 见
     [factory_physics.py](src/reference/factory_physics.py))。已验证写回后 4s 内臂高恒定、
     关节正常走到 HOME 并保持。对本就是 dynamic 的模型 (pioneer p3dx) 是空操作;
     传送带 pad (挂的是 dummy 根) 与 `createPrimitiveShape` 造的岛 (kin=0) 不受影响。
   - 修复后需**重新 build 场景** —— 修复前 build 出来的场景里机械臂仍是 dynamic 状态。

8. **`sim.getJointMode` 在 4.10 返回二元组 → 模式写回校验会被静默吞掉 (已修复)**:
   该函数实测返回 `(jointMode, options)`，与官方手册
   `int jointMode = sim.getJointMode(int jointHandle)` 的签名不符
   (`getJointPosition`/`getJointTargetPosition` 仍返回标量，只有它是这个形态)。
   `int(元组)` 抛 TypeError，被 `RobotArmDevice.read_modes()` 的兜底 `except`
   吞成 `-1`，于是"设置关节模式后读回校验"恒报不一致 —— 模式是否真的切换成功
   无法验证。**修复**: [factory_robot.py](src/reference/factory_robot.py) 的 `read_modes()`
   归一化 tuple/list 返回，并把"读回失败(-1)"与"读回不一致"分成两种告警。
   - 常量实测值: `jointmode_kinematic=0` / `jointmode_dependent=4` /
     `jointmode_dynamic=5` —— 与旧版 1/2 不同，一律取 `sim.jointmode_*`，不可写字面量。
   - 另: kinematic 模式下 `sim.setJointTargetPosition(h, target, motionParams)`
     的第三参数 `[maxVel, maxAccel, maxJerk]` 确实生效 (实测 0.5 rad/s 走 0.3 rad
     约 0.6 s)；不传则用关节自带 maxVelAccelJerk 属性。

9. **UR5 切 kinematic 需先把连杆转成纯运动学, 否则连杆塌陷 (已解决)**:
   内置 `UR5.ttm` 的本体连杆与夹爪碰撞体是 `static=0 respondable=1`(动态启用)。
   关节处于 `kinematic` 而连杆仍是动态时, 引擎不为这些体建立约束 →
   **连杆失去支撑直接自由落体**: TCP 世界 Z 由 2.04 m 掉到 0.13 m(贴地)。
   - ⚠ **最危险之处: 塌陷时关节角读数仍精确等于设定值**(实测读到 HOME 的
     `[0, -1.5708, 1.5708, 0, 0, 0]` 而 TCP 已贴地)。任何只检查关节角/最大误差的
     验证都会给出**假通过** —— 必须检查 TCP 或连杆的**世界坐标**。
   - **解决**: `build_scene.make_arm_kinematic()` 在挂好夹爪**之后**遍历整条机械臂
     子树, 把所有 `static=0` 的 shape 置为非动态(只改 `static`, `respondable` 不变)。
     **必须覆盖整棵子树** —— 夹爪根/手指/配重块同样是动态体, 只改 6 个连杆会让
     夹爪掉落。`ARM_CONTROL_MODE == "kinematic"` 时由 `create_robot_arm` 自动执行。
     改造后**构建期/运行期两种切换时序都正常**, 原先"必须已步进过一步"的脆弱
     时序约束消失。
   - **安全网**: `RobotArmDevice.apply_control_mode("kinematic")` 会先统计子树内
     残余的动态 shape 数, >0 则**拒绝切换**并报错(提示用当前 build_scene 重建场景)。
     旧场景(或 MCP 服务里的旧模块构建的场景)不会再静默塌陷。
   - 无效做法: 只设 `shapeintparam_kinematic=1` 而不设 `static`, 实测**无效**。
   - 与动态物料的碰撞: 对照实验显示 `static` 改造前后表现一致(自建动态方块在两种
     配置下均穿透连杆), 即该改造不改变碰撞行为。
   - 回归护栏: `tests/test_robot_integration.py::test_arm_does_not_collapse_after_animator_startup`
     (判据为 TCP 世界坐标 Z > 1.4 m)。

## 日志

`factory_sim.log` — 双输出日志 (文件 DEBUG 级 / 控制台 INFO 级)。紧急停止时写
`emergency_dump_<timestamp>.json` 保存状态供事后分析。

阶段一/步骤 1.1 的输出约定：

- 原始日志（`stage1_sim.log`、`step1_1.log`、`coppelia_console*.log`）
  每次运行重新生成，已被 `.gitignore` 排除，不提交；
- 运行脚本默认写入 `docs/implementation/runs/`（不提交）；
- 报告/验收记录实际引用的证据才复制到 `docs/implementation/evidence/`
  并随代码提交，文件名保留原始时间戳；约定见该目录 `README.md`。

10. **传送带长度曾是设计值的 3.2 倍 (已修复)**: `create_conveyor` 用硬编码
    `default_len = 1.0` 计算 `scale_x`, 而内置 `generic conveyor (belt).ttm`
    的 native 长度实测是 **1.216 m**, 于是每条带被放大了 `length/1.0` 倍
    (3.2/3.9/3.0 倍), 长度、宽度、高度一起超标。
    - **4.10 的 `sim.scaleObjects` 只能等比缩放**: 官方手册签名是
      `(list objectHandles, float scalingFactor, bool scalePositionsToo)`,
      scalingFactor 是**标量**; 实测传 `(2, 1, 1)` 结果三轴同时 ×2。
      所以传送带只能整体等比放大到设计长度, 宽度/高度会随之等比变化
      (实测 0.497~0.639 m 宽)。**一律以实测值 `CONVEYORS[*]["width"]` 为准。**
    - 修复: 读 `objfloatparam_modelbbox_*` 得到 native 长度 → 等比缩放到设计
      长度 → **重新读包围盒**确认实测长度 == 设计长度。

11. **传送带包围盒 ≠ 可承载区, 物料会从端部掉到地面 (已修复)**:
    `generic conveyor (belt).ttm` 的 native 包围盒 X 跨度 1.216 m, 但**上表面
    pad 只覆盖 [-0.497, 0.500]** (约 82%), 两端是回转滚筒。按包围盒半长放
    入口 dummy 会让物料生成在滚筒外, 直接自由落体到地面 (实测物料停在
    z=0.16 的地面上)。
    - 修复: `factory_scene.measure_belt_top_run()` 取最高一排 pad 的局部 X
      范围, 入口/停止区 dummy 由**该范围** ± 内缩推出。
    - 内缩必须 ≥ 物料半长 (`CONVEYOR_STOP_INSET = 0.08 ≥ 0.15/2`), 否则物料
      有一半压在滚筒上。

12. **4.10 历史实验：脚本驱动未形成可靠摩擦传递**: 内置 conveyor 脚本用
    `setObjectPosition` 逐个移动 pad, **Bullet 不会把切向速度传给上方
    dynamic 物料** —— 两种配置各跑 10 s 的物料位移都是 < 0.01 m:
      - pad `static=1 + kinematic=1` (项目原配置) → 0.009 m
      - pad `static=0 + kinematic=1` (传统传送带配置) → 0.008 m
    且 4.10 **既没有 `sim.setObjectVelocity`**, 也**没有 surface velocity
    属性** (`bullet.surfaceVelocity` / `ode.surfaceVelocity` 均报
    `property is unknown`)。
    - 该结论只适用于旧实验控制器。新工程对外正式模式统一称为
      **`HYBRID_DYNAMIC`**：优先依靠动态接触和摩擦；物理输送不足时才允许
      沿带面切向的限速补偿，并必须记录补偿原因。不得把旧的
      `KINEMATIC_BELT_TRANSPORT` 当作新方案模式。
    - **2026-10-20 步骤 1.1 C 组复测（重要修正）**: 在 `stage1` 当前场景中
      关闭位置补偿后，入口物料 **10/10 前进 0.8298 m 到达检测区**
      (≈0.20 m/s)，说明当前配置下 pad→物料确实存在物理输送。旧结论只适用于
      旧实验控制器/配置，不得再作为当前实现的依据。同一轮 A 组从入口到
      sensor 的平均速度约 **0.38 m/s ≈ 带速 + 补偿步率**，是补偿与物理输送
      叠加的直接证据；阶段二必须改为“仅在物料落后于带速时补偿”并重测摩擦。
    - 履带的**驱动源**仍按方案 §5.1 做一次性启动探测: 有脚本且 pad 确实在动
      → `SCRIPT_DRIVEN` (Python 只用 `__ctrl__` 调速, 不碰 pad 位姿); 否则剥离
      脚本转 `PYTHON_PAD_DRIVEN`; 没有内置模型则为 `FALLBACK_GEOMETRY`。

13. **内置 conveyor / suction 脚本没有公开函数, 只能走 buffer 属性与传感器回退**:
    - conveyor: `Script` 是 customization 脚本, 正文只有
      `require('models.conveyor_customization-3')`。外部控制接口是模型自带注释
      里的 buffer 属性 (不是 `sim.callScriptFunction`):
      `sim.setBufferProperty(h, 'customData.__ctrl__', sim.packTable({vel=v}))`,
      `sim.readCustomTableData(h, '__state__') → {vel, pos}`。
      实测默认 vel=0.1; 下发 `{vel=0}` → 实测带速 0.0000 m/s; `{vel=0.5}` → 0.427 m/s。
      封装在 `factory_physics.set_conveyor_running` / `get_conveyor_state`。
    - suction pad: 脚本是 child script, `sysCall_sensing` **每步枚举全场景
      shape**, 对**第一个** respondable 且 `checkProximitySensor` 命中的对象做
      `setObjectParent` + `setLinkDummy` —— 既没有 activate/deactivate, 又会
      无条件吸附传送带 pad 等错误对象。故 `SUCTION_USE_BUILTIN_SCRIPT = False`,
      加载后剥离脚本, 改用 `Sensor` 接近传感器静默检测 + Python 显式父子绑定
      (方案 §6.3)。`Link`(力传感器) 保留作吸附参考点。

14. **机械臂底座必须够得着取料点, 否则 IK 必然失败 (已加构建期断言)**:
    原 `offset = [0, 0.35, 0.45]` 使 A/B/D 的底座到取料点距离达 0.89~0.97 m,
    **超出 UR5 约 0.85 m 的工作半径** → `simIK.handleGroup` 主组与阻尼组双双
    返回失败 (result=2, reason=112), 日志被 IK 失败刷屏。
    - 修复: A/B/D 的 `offset.x` 由 0 改为 **-0.5** (底座靠近带尾侧), 并在
      `build_scene.verify_layout()` 里加 `arm[*].reach_to(conveyor)` 断言
      (`ARM_REACH_RADIUS - ARM_REACH_MARGIN`)。
    - ⚠ 取料点是**末端停止区 dummy**, 不是传送带末端: 早期用"距末端半径"判到站,
      物料会停在距停止点多达 0.18 m 处, 使实际抓取点刚好越过可达边界
      (0.733 → 0.85 m)。旧阶段一按**行程进度** `conv_progress >= 1.0` 判停带;
      2026-10-20 迁移后改为 `Sensor_PickStation` 命中目标 UID 即停带,
      行程末端只作为未命中故障的保护边界。

15. **simIK 在 4.10 的实际返回形态**: `simIK.handleGroup` 返回 **三元组**
    `(resultCode, reason, [linearError, angularError])`, `result_success = 1`。
    `addElementFromScene` 返回 `(0, {objHandle: ikElementHandle}, 反向映射)` ——
    不是布尔值, 直接 `if not addElementFromScene(...)` 永远为假 (三元组恒真)。
    - 带**姿态约束** (`constraint_pose`) 的 IK 在取料点常常无解
      (角误差已满足但线误差 0.14 m), 故本项目用 `constraint_position` 求解,
      再用 `ARM_TOOL_MAX_TILT` (≈20°) 在**抓取时刻**硬性拦截"吸盘翻转"的抓取。
    - IK 上下文 (`RobotArmIKContext`) 每台臂只建一次并复用; 主组伪逆、备用组
      阻尼最小二乘, 两组都失败 → 动作判为 `FAILED`, 由控制器退避重试
      (`CONVEYOR_PICKUP_RETRY_DELAY`), **绝不用关键帧姿态冒充到位**。

16. **ConPTY relay 继承 uv 的 PYTHONHOME → sandboxScript:error (已修复)**:
    用 `tools/conpty_relay.py` 从 uv 管理的 venv 启动 CoppeliaSim 时, relay 会继承
    `PYTHONHOME` / `UV_INTERNAL__PYTHONHOME` (指向 uv 的 Python 3.11)。CoppeliaSim
    的 Python 沙箱再启动 `defaultPython` (本项目为 Anaconda 3.12) 时, 3.12 解释器被
    强制加载 3.11 标准库, 报 `AssertionError: SRE module mismatch`, 随后
    `[sandboxScript:error] The Python interpreter could not handle the wrapper script`。
    - 该报错**不是** `cbor2`/`zmq` 缺失, 也不是 `startTimeout` 太短 (两者都实测正常)。
    - 修复: relay 在 spawn 子进程前剔除 `PYTHONHOME` 与 `UV_INTERNAL__PYTHONHOME`
      (`_child_env()`; 启动日志头 `env_dropped=[...]` 可核对)。手动启动 CoppeliaSim
      遇到同样报错时, 先清掉该环境变量再启动 (cmd: `set PYTHONHOME=`; bash:
      `unset PYTHONHOME`)。
    - 最小复现:
      `PYTHONHOME=<uv 3.11 目录> D:/anaconda3_envs/coopeliasim/python.exe -c "print('OK')"`。

17. **内置 conveyor 默认加速度过小, 物料到站后带面仍滑行 (已修复)**:
    `generic conveyor (belt).ttm` 的 `customData.__config__.accel` 实测为
    **0.05 m/s²** (schema 默认 0.01, ttm 存 0.05)。模型脚本用
    `sim.ruckigVel` 按该加速度做速度规划, 因此 `__ctrl__ {vel=0}` 只是
    把目标速度设为 0: 0.1 m/s 需约 2 s 停住, 0.2 m/s 接近 4 s, 视觉上
    就是“物料到了、传送带还在转”。
    - **修复**: [stage1/scene.py](src/stage1/scene.py) 在构建期 (simulation
      start 之前) 读取 `customData.__config__`, 写入
      `accel = CONVEYOR_ACCEL = 2.0 m/s²`、`targetVel = CONVEYOR_SPEED`,
      模型脚本在 init 时读取该表。
    - 实测 (0.2 m/s): **0.10 s / 2 个仿真步**停住, pad 滑行 0.010 m;
      `run_stage1` 的 `conveyor_drive_probe` 记录 `stop_time_s`、
      `stop_steps`、`stop_pad_moved_m`、`stop_ok`。
    - **不要把 accel 调到 3.0/5.0**: 虽然一帧停住, 但脚本只在
      `pos` 变化时才刷新 `__state__`, 会出现速度回读仍为旧值 (实测 3.0
      时回读 0.05、5.0 时回读 0.2)。2.0 是“快速停车 + 状态可读”的
      实测折中。
    - 若替换 conveyor 模型或覆盖 `__config__`, 必须重新构建场景并检查
      驱动探测的 `stop_ok`。

18. **`sim.intparam_engine` 不存在, 物理引擎参数名是 `intparam_dynamic_engine`
    (步骤 1.2 实测)**: 4.10 的 ZMQ 绑定没有 `intparam_engine` 常量;
    用 `sim.getInt32Param(sim.intparam_dynamic_engine)` 读取 (实测 0 = bullet)。
    `sim.getEngineInt32Param(param, -1)` 实测返回 None, 不能用于 manifest。
    若引擎/版本字段缺失会导致阶段二无法判断物理后端, 必须在工件保存时记录。

19. **`saveScene`/`loadScene` 会替换场景对象, 旧句柄全部失效 (步骤 1.2)**:
    两者只能在仿真停止态调用; `loadScene` 后必须用
    `stage1.scene.adopt_scene()` 按 alias 重新解析句柄, 不得复用构建期句柄。
    `save` 的流程是: 写 `scene.partial.ttt` → `loadScene` 回读 → 只读校验 →
    `os.replace` 为 `scene.ttt` → 更新 `current.json`; 任何一步失败都不得
    更新 current 指针。

20. **生命周期 CLI 的全局参数必须写在子命令之前 (argparse 限制)**:
    `python src/run_scene_lifecycle.py --out-dir runs start ...` 正确;
    `... start --out-dir runs` 会报未识别参数。跨进程运行锁位于
    `docs/scene_artifacts/.runtime/run.lock` (PID + run_id), 持有进程死亡后
    下一次 start 自动清理; 运行中的 `load`/`build` 返回 `RUN_CONFLICT`。

21. **4.10 的 collection self-collision indicator 对 checkCollision 无效 (阶段二实测)**:
    `sim.setObjectInt32Param(h, sim.objintparam_collection_self_collision_indicator, v)`
    后，同一 collection 与自身检查时 1/1/2、1/2/3、1/10/100 等组合
    **仍照样报碰撞**。不要依赖该编号过滤相邻连杆；阶段二使用显式非相邻
    连杆对（|i−j|≥2）+ 吸盘 Body 对 link1–5。若后续版本声称支持，必须
    先实测枚举过滤结果。

22. **内置 conveyor 的粗包围盒代理 `shape` (阶段二实测)**:
    模型根 `Conveyor_In` 下 alias `shape` 是 0.2×0.2×1.2 m 的整条带
    外包盒，顶面比真实带面高约 10 cm；真实承载面是 50 个 `pad`。阶段二
    把它从碰撞查询角色中排除并记录原因，**不修改其场景属性、不收缩任何
    代理**。`loadScene` 后 `objfloatparam_modelbbox_*` 可能不可读，粗代理
    检测回退为“厚度 > 0.02 m 的 `shape`”。

23. **kinematic 关节流式执行的正确用法 (阶段二实测)**:
    - 每步重复 `setJointTargetPosition(j, q, motion)` 会反复重置运动规划
      的加速段，实际关节追不上目标；执行器必须在上一目标未达到时保持，
      达到后再下发下一构型。
    - 0.075 rad 微步 + jerk=10 时每步约 0.45 s，大范围中转会超时；阶段二
      使用 0.20 rad/命令 + jerk=100（仍受 maxVel/maxAccel 限制），实测
      0.2 rad 约 0.35 s。
    - **停止态 `sim.setJointPosition` 对真实臂部分关节不生效**（同一
      UR5.ttm 的新加载影子臂正常）。不要用停止态 `setJointPosition` 构造
      真实臂验证位姿；规划/检查用影子 FK，真实运动用 target + `sim.step`。

24. **set/frozenset 序列化顺序导致摘要跨进程漂移 (阶段二修复)**:
    `stage2_config_summary()` 含 `CONTACT_PHASES` 等 frozenset；
    `scene_artifact._jsonable` 原先直接迭代集合，Python 哈希随机化会让
    config digest 在不同 CLI 进程间变化，表现为“同一工件时而过校验、
    时而失败”。已改为 `sorted(value, key=repr)`；新增摘要字段若含集合
    必须走同一序列化。

25. **竖直工具的腕部奇异与 yaw (阶段二实测)**:
    工具竖直时 q5≈±90°，q4/q6 耦合；关节空间插值可能在抬升/中转途中
    产生绕工具 Z 的 yaw 旋转（TCP 位置不变）。阶段二接近段按“工具 Z 轴
    对齐”检查（允许 yaw），最终放置点仍严格检查完整姿态。不要为通过
    路径而放松最终放置姿态判据。

26. **阶段二配置漂移会拒绝旧工件**:
    `extra.stage2.config.digest` 记录阶段二参数；修改
    `src/stage2/config.py` 后，已保存的 stage2 工件在 `start` 前会失败
    （`stage2_config_digest`）。这是预期保护：重新从 stage1 基线派生
    新工件，不要手工改 manifest 或关闭校验。

27. **`simIK.setJointInterval` 段内锁轴的时序 (阶段 2.01 实测)**:
    - `simIK.syncFromSim(env, [group])` 会用场景关节区间覆盖 IK 环境；
      必须在同步**之后**设置 `setJointInterval(env, ikJoint, False, [low, range])`
      （`range = high-low`），`handleGroup` 之后恢复原 `(cyclic, interval)`。
    - 锁定值取段起始 q[0]，半宽用 `Q0_LOCK_IK_TOL=0.010 rad`，候选/执行
      验收容差 `Q0_LOCK_TOL=0.02 rad`；不要用锁定值代替场景关节限位。
    - `addElementFromScene` 返回的 `{scene_joint: ik_joint}` 映射用于定位
      被锁 IK 关节；映射缺失时锁轴不可用，记录后走同分支自由 IK。

28. **first-feasible 不是最小 TV，且笛卡尔段必须承接实际 TCP (阶段 2.01)**:
    - 同一目标的多解候选中，第一个可行链可能在下一段发生 2.7 rad 基座
      翻转；仅按关节距离排序无法发现。实现用“下一自由目标一步前瞻”
      排序候选（只排序，不替代碰撞检查），station transfer TV 15.644→8.006。
    - 上一 IK 端点允许 10 mm TCP 误差；下一段笛卡尔链若从 `goal.pose`
      而不是实际 TCP 出发，会把这点误差误报为 7.4 mm 横向偏差并拒绝
      可行解。必须用 `checker.tcp_at(q_prev)` 承接。
    - 控制器规划优先从上一段已验证计划终点开始（实测偏差 ≤2×
      `EXEC_JOINT_TOL`），否则 A/B 复跑会因 0.01 rad 级差异选到不同路线。

29. **转运拆分与 pre_place 锁轴的负结果 (阶段 2.01 实测)**:
    - `transit → high_above_place → pre_place` 拆分能力已实现，但 A/B 把
      station 计划 TV 从 12.395 提高到 16.013 rad、AGV 从 11.968 提高到
      13.354 rad，因此 `TRANSFER_SPLIT_HIGH_ABOVE_PLACE=False`。不要因为
      “几何上更规整”就默认打开；必须重新做净空/碰撞/总行程验收。
    - `pre_place`（未拆分时的对角段）锁 q[0] 在 station 会迫使腕部跨支、
      在 AGV 会让载荷扫过 conveyor pad；`Q0_LOCK_SEGMENTS` 只含
      `lift/rise/place/retreat`。

30. **锁存路径必须先保留运动证据再清空执行器 (阶段 2.01 修复)**:
    `_safety_fail` 原先先 `cancel()`/清空 `_executor`、`_plan`，再调用
    `_fail` → `_capture_motion_report`，导致运行期障碍锁存时缺少
    TRANSFERRING 的失败段实测。现在在 `_safety_fail` 入口先捕获部分执行
    指标，再取消轨迹；失败段的 TV/q[0]/分支必须出现在 `motion` 中。

31. **AGV 转运 A/B 必须使用实机停止后的 pick pose (阶段 2.02 实测)**:
    用 `layout.pick_material_pose` 生成探针物料会选中另一条 IK 分支，
    transfer 计划 TV 为 7.698 rad；使用实机运行记录中的
    `pick_pose=(-0.30784, 0.00012, 0.5)` 才复现生产计划 TV 14.345 rad 与
    `pre_place` q[3]=+5.019 rad。探针必须接受并记录 pick pose 来源，
    不得把布局位姿当作“停止后实测位姿”。

32. **执行 jerk 峰值不等于目标参数 (阶段 2.02 实测)**:
    控制器下发 `EXEC_JOINT_JERK=100`，但实测采样经三阶差分得到的峰值在
    LIFTING/TRANSFERRING 为 117.8/120.65 rad/s³。原因包括离散目标切换、
    保持步和实测滞后；在完成时间参数化/平滑并对输出路径重新复核前，
    不得报告“已平顺”。`execution.totals.profile.within_limits.jerk` 为
    false 是有效证据，不是脚本错误。

33. **baseline 的 transit 在零偏移时仍是独立语义点 (阶段 2.02 修复)**:
    `transfer_goal_variants` 的 baseline 若在 `transit_offset=0` 时省略
    transit，会退化成 rise→pre_place 对角段，改变 gate B 的单变量比较；
    只有 high_align 的 transit 与 high_above_place 重合时才省略。修改
    该逻辑必须同步更新 `tests/test_stage2_agv_transfer.py` 和 06-10 §8。

34. **阶段三配置漂移会拒绝旧工件 (预期保护)**: `extra.stage3.config.digest`
    记录阶段三全部参数；修改 `src/stage3/config.py` 后，已保存的 stage3
    工件在 `start`/`validate` 前会失败。必须从 stage2 父工件重新
    `build --phase stage3` + `save`，不得手改 manifest 或关闭校验。
    本轮因 `UNSTABLE_INJECT_DELAY_STEPS` 与 `SAFETY_STOP_SETTLE_STEPS`
    调整派生到 v003，因共享对象清单补全派生到 v004；历史工件保留。

35. **阶段三两台控制器必须共享一次 `sim.step()` (阶段三实测)**:
    阶段一/二控制器的 `step()` 内部各自推进物理；阶段三不能同时调用两台
    控制器的 `step()`，否则每逻辑步推进两次物理。实现把单步拆成
    `pre_tick()`（传感器/传送带/任务/IK/吸附）和 `post_tick()`（步后联锁），
    协调器统一顺序：两臂 `pre_tick` → AGV 轮速 → 故障注入 → `sim.step()`
    → 两臂 `post_tick`。锁存后即使控制器已 FAILED 也必须调用 `post_tick`，
    否则 `SafetyLatch.stop_confirmed` 永远为 false。

36. **阶段三资源锁只解决调度互斥, 不代替碰撞检查**: 固定顺序
    `pick_zone → agv_lane → dock_A → dock_B → arm_A → arm_B`；B 工位在
    AGV 运输途中只能记录 `dock_B` WAIT，不能提前获取 dock，否则会与运输
    任务形成死锁。另一台真实臂的可见连杆/吸盘 Body 必须加入本臂工作区
    碰撞集合；资源释放不会清除 `Stage2Safety` 锁存。

37. **AGV dock 朝向改变会与 Station 台面干涉 (阶段三实测)**:
    原 L1_S1 的 AGV 在 dock A `(0.40,0.75)` 朝 +X；改为朝 −Y 后车体长轴
    沿 Y，车体端部到 `y=1.0095`，与 Station A `y>=1.0` 相撞。阶段三把
    dock A/B 内移到 `y=±0.70`，AGV 初始位姿在构建期（停止态）一次设置，
    并在场景契约中断言位置/航向。运行期仍禁止 `setObjectPosition` 瞬移。

38. **B 工位从 AGV 取料需要 `RELEASED → ON_TRAY` 重新确认 (阶段三)**:
    A 工位释放到 AGV 后物料状态是 `RELEASED`（阶段一语义），B 工位必须
    在 dock B 校验同一 UID、载台区域和稳定后才允许转 `ON_TRAY` →
    `READY_FOR_PICK`；不得跳过 UID/载台检查直接吸附。

39. **不稳定卸载注入必须早于 `pre_tick` 联锁 (阶段三实测)**:
    `PLACING` 通常只持续一个控制步，下一 `pre_tick` 就进入 `RELEASING`。
    故障注入必须在控制器 `pre_tick` **之前**执行，才能让
    `Stage2Safety` 在释放前检出 `LOAD_DRIFT`；延迟设为 1 步。锁存后
    协调器继续推进 `SAFETY_STOP_SETTLE_STEPS=20` 步，由 `post_tick`
    读取真实带速/车体速度确认停止。

40. **阶段三影子臂清理必须按别名精确清理**: `ShadowProbe.destroy()` 缺省
    只删除本臂的 4 个根别名；阶段二单臂场景仍等价于清理全部 `Shadow_*`。
    不得在另一臂仍在运行时用全局前缀清理，否则会误删另一台影子臂。

41. **阶段三工件受保护且 current 指向最新版本**: CLI `save` 默认
    `protected=true`；`stage3/L2_MIRROR/v001..v004` 均保留，current 指向
    v004。派生新版本只能 `save --phase stage3 --parent ...` 创建新目录，
    不得覆盖受保护工件。保存时 `adopt_stage3_scene` 必须解析阶段一/二
    共享对象的非必需别名（AGV 载台/挡边、出口传感器），否则父子差异会
    误报对象被移除。
