# cop-test — 岛式车间柔性生产仿真 (CoppeliaSim + MCP)

基于 CoppeliaSim 4.10+ ZeroMQ Remote API 的岛式车间柔性生产仿真系统，
并附带一个本地 MCP 服务器，让 Claude Code 能像调用函数一样编排仿真生命周期。

## 三层架构

```
┌─────────────────────────────────────────────────────┐
│  MCP Tool Layer  — factory_mcp_server.py            │  ← Claude Code 接入点
│  (FastMCP / stdio, 19 个工具, 仿真跑在守护线程)      │
└───────────────┬─────────────────────────────────────┘
                │ 调用
┌───────────────▼─────────────────────────────────────┐
│  Python Control Layer                              │
│  factory_controller.py  — FactoryController (编排)   │
│  factory_island.py      — 工作岛/节拍追踪/供料门控   │
│  factory_agv.py         — 共享 AGV 柔性调度器        │
│  factory_exceptions.py  — 统一异常处理              │
│  factory_robot.py       — 机械臂动画 (纯视觉层)      │
│  factory_types.py       — 枚举/数据类/物料           │
└───────────────┬─────────────────────────────────────┘
                │ ZMQ Remote API (同步, stepping 模式)
┌───────────────▼─────────────────────────────────────┐
│  ZeroMQ Remote API Layer — factory_core.py          │
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

## 关键模块职责

| 文件 | 职责 | 核心导出 |
|------|------|---------|
| [factory_config.py](src/factory_config.py) | 所有位置/尺寸/节拍/阈值集中配置 | 常量字典 |
| [factory_core.py](src/factory_core.py) | ZMQ 连接+重连、健康监控、日志 | `CoppeliaConnector`, `ZMQMonitor`, `setup_logger` |
| [factory_types.py](src/factory_types.py) | 枚举(物料类型/AGV状态/岛状态)、数据类、物料生成 | `ItemType`, `AGVState`, `IslandState`, `Material`, `spawn_material`, `lerp_2d` |
| [factory_island.py](src/factory_island.py) | 工作岛状态机、节拍追踪、供料门控 | `IslandController`, `BeatTracker`, `SupplyController` |
| [factory_agv.py](src/factory_agv.py) | 单一共享 AGV + 优先级调度 (B→A 高优先, D→E 低优先, 饥饿可升级) | `AGVScheduler` |
| [factory_exceptions.py](src/factory_exceptions.py) | 统一异常轮询与紧急停止 | `ExceptionHandler` |
| [factory_robot.py](src/factory_robot.py) | 机械臂设备层(六轴/TCP/控制模式/反馈) + 关键帧动画, **不影响调度/节拍/异常** | `RobotArmDevice`, `RobotArmAnimator` |
| [factory_controller.py](src/factory_controller.py) | 顶层编排器, 按固定顺序驱动每个步进 | `FactoryController` |
| [build_scene.py](src/build_scene.py) | 场景构建 (分层命名 /Warehouse /Island /Conveyor /Mobile) | `build_scene()`, `clear_scene()` |
| [run_simulation.py](src/run_simulation.py) / [main.py](src/main.py) | CLI 入口 | `main()` |
| [factory_mcp_server.py](src/factory_mcp_server.py) | MCP 服务器, 暴露 19 个工具给 Claude Code (A 读取/B 诊断/C 控制 + 双连接) | `FactoryMCPServer`, `mcp` |

> 全部源码平铺在 [src/](src/) 下（基础层 / 控制层 / 视觉层 / 接入层 / CLI 入口），
> 详见 [README.md](README.md) 的「项目结构」与 [docs/FILE_INDEX.md](docs/FILE_INDEX.md)。

## 单步仿真循环顺序 (FactoryController.run)

每个 `SIM_STEP` 按此顺序执行：

1. 异常轮询 (ZMQ 通信 / 工位超时 / 物料溢出 / AGV 拥堵)
2. 若通信暂停 → 仅推进物理引擎, 跳过控制逻辑
3. 仓库按节拍产出物料 (S1→A, S1→B, S2→D)
4. 传送带移动物料 (4 条传送带)
5. 工作岛处理 (更新加工时间 + 节拍追踪)
6. AGV 任务调度 (B→A 高优先, D→E 低优先)
7. AGV 状态机推进
8. A→C 刚性传输 (不经 AGV)
9. C→E 传送带传输
10. E 岛定时装配 (SEMI_CE + PART_DE → FINAL)
11. 场景位置同步 (Python 状态 → CoppeliaSim 物体)
12. 机械臂动画更新 (视觉层)
13. `sim.step()` 推进物理引擎

## 运行前提

1. **CoppeliaSim 4.10+ 已启动** (教育版即可)
2. **ZMQ Remote API 服务端已激活**: 菜单 Add-ons > ZMQ remote API server (默认端口 23000)
3. 场景为空 (构建前会自动 `clear_scene` 清场)

## 入口命令 (venv)

```bash
# 1. 构建工厂场景 (幂等, 先清场)
.venv/Scripts/python src/build_scene.py [--host localhost] [--port 23000]

# 2. 运行柔性生产仿真
.venv/Scripts/python src/run_simulation.py [--host localhost] [--port 23000] [--time 60] [--log factory_sim.log]

# 3. 交互菜单
.venv/Scripts/python src/main.py
```

或用 uv 入口脚本 (源码已装包, 路径无关):
```bash
uv run build-scene
uv run run-simulation --time 60
```

## MCP 服务器 (给 Claude Code 用)

[factory_mcp_server.py](src/factory_mcp_server.py) 通过 [.mcp.json](.mcp.json) 注册，
Claude Code 在本项目会话会自动加载。详细使用见 [docs/wiki/98-ProjectUsage/MCP_USAGE.md](docs/wiki/98-ProjectUsage/MCP_USAGE.md)。

## 已知问题与坑 (重要)

1. **CoppeliaSim 4.10 别名路径陷阱**: `setObjectAlias` 把含 `/` 的路径转成下划线别名
   (如 `/Mobile/AGV_Shared` → `_Mobile_AGV_Shared`)，导致 `getObject(斜杠路径)` 查询失败。
   代码中用别名匹配方式定位对象 (见 [factory_controller.py:159-213](src/factory_controller.py#L159-L213))。

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
   - **修复**: [build_scene.py](src/build_scene.py) 的 `load_builtin_model()` 挂父前
     记录根 shape 的 `static`, 挂父后写回 `setObjectInt32Param(h, shapeintparam_static, 1)`
     (4.10 的 `static` properties 路径不可用, 只能走 int-param, 见
     [factory_physics.py](src/factory_physics.py))。已验证写回后 4s 内臂高恒定、
     关节正常走到 HOME 并保持。对本就是 dynamic 的模型 (pioneer p3dx) 是空操作;
     传送带 pad (挂的是 dummy 根) 与 `createPrimitiveShape` 造的岛 (kin=0) 不受影响。
   - 修复后需**重新 build 场景** —— 修复前 build 出来的场景里机械臂仍是 dynamic 状态。

8. **`sim.getJointMode` 在 4.10 返回二元组 → 模式写回校验会被静默吞掉 (已修复)**:
   该函数实测返回 `(jointMode, options)`，与官方手册
   `int jointMode = sim.getJointMode(int jointHandle)` 的签名不符
   (`getJointPosition`/`getJointTargetPosition` 仍返回标量，只有它是这个形态)。
   `int(元组)` 抛 TypeError，被 `RobotArmDevice.read_modes()` 的兜底 `except`
   吞成 `-1`，于是"设置关节模式后读回校验"恒报不一致 —— 模式是否真的切换成功
   无法验证。**修复**: [factory_robot.py](src/factory_robot.py) 的 `read_modes()`
   归一化 tuple/list 返回，并把"读回失败(-1)"与"读回不一致"分成两种告警。
   - 常量实测值: `jointmode_kinematic=0` / `jointmode_dependent=4` /
     `jointmode_dynamic=5` —— 与旧版 1/2 不同，一律取 `sim.jointmode_*`，不可写字面量。
   - 另: kinematic 模式下 `sim.setJointTargetPosition(h, target, motionParams)`
     的第三参数 `[maxVel, maxAccel, maxJerk]` 确实生效 (实测 0.5 rad/s 走 0.3 rad
     约 0.6 s)；不传则用关节自带 maxVelAccelJerk 属性。

## 日志

`factory_sim.log` — 双输出日志 (文件 DEBUG 级 / 控制台 INFO 级)。紧急停止时写
`emergency_dump_<timestamp>.json` 保存状态供事后分析。
