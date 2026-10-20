# cop-test — 柔性工业车间仿真 (CoppeliaSim + MCP)

基于 CoppeliaSim 4.10+ ZeroMQ Remote API 的柔性工业车间仿真系统，
并附带一个本地 MCP 服务器，让 Claude Code 能像调用函数一样编排仿真生命周期。

## 三层架构

```
┌─────────────────────────────────────────────────────┐
│  MCP Tool Layer — reference/factory_mcp_server.py   │  ← Claude Code 接入点
│  (FastMCP / stdio, 20 个工具, 仿真跑在守护线程)      │
└───────────────┬─────────────────────────────────────┘
                │ 调用
┌───────────────▼─────────────────────────────────────┐
│  Python Control Layer                              │
│  stage1/ (当前) — 单元闭环: config/physics/scene    │
│                   material/conveyor/UR5/suction/AGV │
│                   unit_controller/run              │
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
| [factory_mcp_server.py](src/reference/factory_mcp_server.py) | MCP 服务器, 暴露 20 个工具给 Claude Code (A 读取/B 诊断/C 控制 + 双连接) | `FactoryMCPServer`, `mcp` |

> 全部源码平铺在 [src/](src/) 下（基础层 / 控制层 / 视觉层 / 接入层 / CLI 入口），
> 详见 [README.md](README.md) 的「项目结构」与 [docs/FILE_INDEX.md](docs/FILE_INDEX.md)。

### 阶段一单元闭环（当前实施范围，2026-10-17 通过）

阶段一严格限定为：一条内置传送带、一个内置 UR5、一个内置 suction pad、
一个内置 pioneer p3dx、一个指定物料、工位与 AGV 载台两个目标。
82 项阶段一测试通过；正常流程、60 s 连续运行与六类故障注入均有实机证据。
入口与证据见 [PHASE1_REPORT.md](docs/implementation/PHASE1_REPORT.md)。

| 文件 | 职责 |
|------|------|
| [config.py](src/stage1/config.py) | 阶段一全部位置/别名/模型路径/速度/容差/超时/故障枚举 |
| [layout.py](src/stage1/layout.py) | `LayoutSpec`、候选布局派生坐标与静态几何校验 |
| [physics.py](src/stage1/physics.py) | static/kinematic/respondable/mass/friction/mask 工具 |
| [scene.py](src/stage1/scene.py) | 单工位场景构建、脚本处理、传感器、场景契约断言 |
| [device_handle_map.py](src/stage1/device_handle_map.py) | 启动期唯一别名/句柄表 |
| [material_manager.py](src/stage1/material_manager.py) | 7 状态物料生命周期与 UID 事件历史 |
| [conveyor_controller.py](src/stage1/conveyor_controller.py) | 内置 conveyor 驱动 + HYBRID_DYNAMIC 受限补偿 |
| [ur5_ik_controller.py](src/stage1/ur5_ik_controller.py) | 独立 simIK environment、主/备组、seed 回退、关节目标 |
| [suction_controller.py](src/stage1/suction_controller.py) | UID/传感器/TCP/倾角/速度检查、属性快照与恢复 |
| [pioneer_controller.py](src/stage1/pioneer_controller.py) | 差速换算、限幅、加速度斜坡、轮速下发 |
| [unit_controller.py](src/stage1/unit_controller.py) | §2.11 单步状态机、故障状态与退避重试 |
| [run.py](src/stage1/run.py) | CLI 实现：构建 → 运行 → 证据采集 → JSON 报告 |
| [run_stage1.py](src/run_stage1.py) | 兼容入口薄包装 (`python src/run_stage1.py`) |

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

### 步骤 1.1 验证包（`src/stage1_1/`，2026-10-20）

阶段一与阶段二之间的输入收敛，**不改变阶段一默认布局和生产控制**。
`stage1.scene.build_scene(sim, layout=LayoutSpec(...))` 可选传入候选布局；
不传时严格使用阶段一基线。

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

## 运行前提

1. **CoppeliaSim 4.10+ 已启动** (教育版即可)
2. **ZMQ Remote API 服务端已激活**: 菜单 Add-ons > ZMQ remote API server (默认端口 23000)
3. 场景为空 (构建前会自动 `clear_scene` 清场)

## 入口命令 (venv)

### 阶段一单元闭环（当前）

```bash
# 构建并运行工位目标 (每次运行自动重建单工位场景)
.venv/Scripts/python src/run_stage1.py --target station --time 45

# AGV 载台目标 (含 0.5 m 轮速行驶)
.venv/Scripts/python src/run_stage1.py --target agv --time 45

# 两个目标连续运行 / 60 s 连续运行
.venv/Scripts/python src/run_stage1.py --target both --time 60
.venv/Scripts/python src/run_stage1.py --target station --repeat 6 --time 30

# 故障注入
.venv/Scripts/python src/run_stage1.py --target station --fault ik_unreachable
```

### 步骤 1.1 布局与无挡板取料验证

```bash
# 基线场景测量 / 候选布局 simIK 扫描
.venv/Scripts/python src/run_step1_1.py --phase measure
.venv/Scripts/python src/run_step1_1.py --phase layout

# 指定已选布局跑 A/B/C 试验; 省略 --selected-layout 时自动先扫描
.venv/Scripts/python src/run_step1_1.py --phase experiment --selected-layout L1_S1
.venv/Scripts/python src/run_step1_1.py --phase experiment --selected-layout L1_S1 \
  --groups B --repeats 1          # 冒烟

# 全流程 (measure → layout → A/B/C 10 次矩阵)
.venv/Scripts/python src/run_step1_1.py --phase all --repeats 10

# 从已有证据复算统计
.venv/Scripts/python docs/measurement/analyze_step1_1.py --stamp <stamp>
```

### 历史五岛完整场景层

```bash
# 1. 构建工厂场景 (幂等, 先清场)
.venv/Scripts/python src/reference/build_scene.py [--host localhost] [--port 23000]

# 2. 运行柔性生产仿真
.venv/Scripts/python src/reference/run_simulation.py [--host localhost] [--port 23000] [--time 60] [--log factory_sim.log]

# 3. 交互菜单
.venv/Scripts/python src/reference/main.py
```

或用 uv 入口脚本 (源码已装包, 路径无关):
```bash
uv run build-scene
uv run run-simulation --time 60
```

## MCP 服务器 (给 Claude Code 用)

[factory_mcp_server.py](src/reference/factory_mcp_server.py) 通过 [.mcp.json](.mcp.json) 注册，
Claude Code 在本项目会话会自动加载。详细使用见 [docs/wiki/98-ProjectUsage/MCP_USAGE.md](docs/wiki/98-ProjectUsage/MCP_USAGE.md)。

**CoppeliaSim 控制台实时输出**: CoppeliaSim 自身的控制台消息 (脚本 `print` /
`sim.addLog`、插件与 add-on 报错、冻结前最后一条消息) 不在 ZMQ API 里, 需用
[tools/conpty_relay.py](tools/conpty_relay.py) 以 ConPTY 启动 CoppeliaSim, 输出实时
落盘到 `coppelia_console.log`; MCP 工具 `tail_coppelia_console(lines, offset)` 读取
该文件 (`offset` 做增量跟随)。普通 stdout 重定向是 4 KB 块缓冲, 不能可靠捕获小消息
或冻结前消息。

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
      (0.733 → 0.85 m)。现在到站判据用**行程进度** `conv_progress >= 1.0`。

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
