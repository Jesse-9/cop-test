---
title: Regular API 概述与使用
type: reference
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/中级
created: 2024-07-13
updated: 2026-07-21
status: published
source: src-doc-CoppeliaSim-API-functions.md, src-doc-CoppeliaSim-callback-functions.md, src-doc-CoppeliaSim-scripts.md, src-doc-CoppeliaSim-accessing-scene-objects.md, src-doc-CoppeliaSim-simulation.md
related:
  - [[wiki/00-基础知识/00-01-CoppeliaSim-overview]]
  - [[wiki/07-API脚本示例/07-01-Lua基本脚本模式]]
  - [[wiki/03-运动规划与控制/03-01-逆运动学-simIK]]
  - [[wiki/01-API参考/01-02-Remote-API-ZMQ]]
sim_version: "4.10"
---

# Regular API 概述与使用

## 简介

Regular API 是 CoppeliaSim 最核心的编程接口，由 `sim` 命名空间提供（所有函数以 `sim.` 为前缀，如 `sim.getObjectPosition`）。它**不是**某个脚本的私有 API——所有嵌入 CoppeliaSim 的脚本（主脚本、子脚本、定制化脚本、沙盒脚本、add-on）都通过 Regular API 与仿真环境交互，**外部程序经 [[01-02-Remote-API-ZMQ|ZeroMQ Remote API]] 调用时获得的也是同一套 `sim.*` 函数**（外加各插件命名空间如 `simIK.*`/`simOMPL.*`/`simUI.*`）。换言之，Remote API = Regular API + 插件 API 的远程代理。

> 完整函数目录见官方手册 [apiFunctions.htm](https://manual.coppeliarobotics.com/en/apiFunctions.htm)，本地存档 `raw/src-doc-CoppeliaSim-API-functions.md`。

> **单位约定**：进出 API 的所有数值均使用 **米、千克、秒、弧度**（或其组合），除非另行说明。

## 脚本体系（Regular API 的运行宿主）

CoppeliaSim 内置 Lua 与 Python 两种脚本解释器，二者用法几乎一致（细微差异见手册 `luaPythonDifferences.htm`）。脚本分三类：

| 脚本类型 | 作用域 | 典型用途 |
|----------|--------|----------|
| **沙盒脚本** (sandbox) | 不绑定任何场景/对象，跨所有场景运行 | 临时调试、原型计算 |
| **Add-on** | 不绑定场景，随 CoppeliaSim 启动常驻 | 扩展功能、菜单项、后台服务 |
| **嵌入脚本** (embedded) | 嵌入场景/模型，随场景保存加载 | 又分主脚本(main)、仿真脚本(simulation)、定制化脚本(customization) |

嵌入脚本是仿真的主力：每个对象/模型可由各自的子脚本独立控制，正是这种**分布式控制架构**让 CoppeliaSim 适合多机器人场景。

> 详见 `raw/src-doc-CoppeliaSim-scripts.md`。

## 系统回调函数（进入脚本代码的唯一入口）

脚本代码**只通过回调函数被触发**——没有自上而下的"主循环"由用户书写。CoppeliaSim 在特定事件发生时调用对应 `sysCall_*` 函数。以下是全部系统回调（Python/Lua 同名）：

### 仿真生命周期核心回调
| 回调 | 触发时机 |
|------|----------|
| `sysCall_init()` | 脚本初始化 |
| `sysCall_actuation()` | 每个仿真步的**执行阶段**（驱动机理） |
| `sysCall_sensing()` | 每个仿真步的**感知阶段**（读传感器） |
| `sysCall_cleanup()` | 脚本清理 |
| `sysCall_thread()` | 线程脚本的入口点 |
| `sysCall_beforeMainScript(inData)` | 可返回 `{doNotRunMainScript=True}` 自定义单步推进 |

### 仿真状态切换
`sysCall_nonSimulation`、`sysCall_beforeSimulation`、`sysCall_afterSimulation`、`sysCall_suspend`、`sysCall_suspended`、`sysCall_resume`、`sysCall_realTimeIdle`、`sysCall_beforeInstanceSwitch`、`sysCall_afterInstanceSwitch`（仿真脚本/主脚本不支持部分项）。

### 物理引擎回调
`sysCall_dyn(inData)`（每动力学步前后）、`sysCall_joint(inData)`（自定义关节控制）、`sysCall_contact(inData)`（每接触对）、`sysCall_vision(inData)`（新图像采集/应用时）。

### 场景/对象事件回调
`sysCall_beforeSave`/`sysCall_afterSave`、`sysCall_afterLoad`、`sysCall_selChange`、`sysCall_beforeCopy`/`sysCall_afterCopy`、`sysCall_afterCreate`、`sysCall_beforeDelete`/`sysCall_afterDelete`。

### 通信回调
- `sysCall_msg(msg, origin)`：同步调用，`sim.broadcastMsg` 或上述若干 init/cleanup 回调触发时被调用（可重入）。
- `sysCall_data(inData)`：异步，当附加到本脚本/其父对象的自定义数据属性被修改时触发（仅仿真/定制化脚本）。
- `sysCall_event(cborBuffer)`：异步，当内部变量/状态变化时触发（可重入）；主要用于把外部 GUI/查看器与 CoppeliaSim 同步，配合 `sim.setEventFilters` 做过滤。
- `sysCall_ext(funcName, inData)`：当此函数存在时，所有外部对脚本函数的调用（用户回调）都会被它拦截（原函数被遮蔽）；若只想监控应改用 `sim.registerScriptFuncHook`。

> 完整签名（Python + Lua）见 `raw/src-doc-CoppeliaSim-callback-functions.md`。大多数回调可返回 `{cmd='cleanup'}` 或 `{cmd='restart'}` 来结束/重启脚本。

### 非线程 vs 线程脚本

```lua
-- 非线程子脚本：每步由 CoppeliaSim 调用 sysCall_actuation/sysCall_sensing 一次
function sysCall_init()
    motor = sim.getObject('/arm_joint')
end
function sysCall_actuation()
    sim.setJointTargetPosition(motor, sim.getJointPosition(motor) + 0.01)
end

-- 线程子脚本：在 sysCall_thread 中写自己的循环，用 sim.switchThread() 让出
function sysCall_thread()
    while sim.getSimulationState() ~= sim.simulation_advancing_abouttostop do
        sim.setJointTargetPosition(motor, sim.getJointPosition(motor) + 0.01)
        sim.switchThread()
    end
end
```

## 仿真循环与单步（stepping）模式

CoppeliaSim 通过**按固定时间步推进仿真时间**来运行。默认每个仿真周期由两步顺序组成：**执行主脚本 → 渲染场景**。实时仿真则尽量让仿真时间与真实时间同步。

**单步模式（stepping / synchronous mode）** 允许手动触发每一个仿真步——这是检查单步行为、或与外部应用同步的基础。脚本内可用 `sysCall_beforeMainScript` 实现；外部程序则有专用函数：先 `sim.setStepping(True)` 启用，再用 `sim.step()` 逐帧推进。

```python
# 手册官方单步示例（Python ZMQ Remote API）
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

sim.setStepping(True)          # 启用单步模式 → 外部程序成为时钟主导
sim.startSimulation()
while (t := sim.getSimulationTime()) < 3:
    print(f'Simulation time: {t:.2f} [s]')
    sim.step()                  # 推进一个仿真步
sim.stopSimulation()
```

> ⚠ **本仓库正是基于此模式**：Python 端 `sim.setStepping(True)` 后每次 `sim.step()` 推进一个 `SIM_STEP`（默认 0.05s），控制逻辑与物理引擎严格同步——这是精确工业节拍时序的基础。详见 [[06-02-岛式车间柔性生产案例]]。
>
> ⚠ 注意 `sim.setStepping` / `sim.step` 在手册中归类于 **Threads** 而非 Simulation，其语义偏"线程中断/单步推进"，但在外部 Remote API 客户端上用法即上面示例。

## 访问场景对象：路径与别名系统（重要）

获取对象句柄统一通过 `sim.getObject(objectPath, options={})`，参数是**对象路径**而非单纯的别名。路径语法：

| 写法 | 含义 |
|------|------|
| `/Path/to/Object` | 完整绝对路径 |
| `/Object` | 别名唯一时，路径可简化为别名 |
| `/Robot[0]` / `/Robot[1]` | 同一层级中第 1/第 2 个别名为 Robot 的对象 |
| `/Robot{4}` | 场景中第 5 个别名为 Robot 的对象 |
| `/Mobile*` | 通配：所有别名以 Mobile 开头的对象（循环取） |

**脚本对象内**还支持相对路径：`./`（本脚本层级）、`..`（父）、`:`（本脚本所在模型）、`::`（父模型），如 `sim.getObject('./Object*')`、`sim.getObject(':/Leg[3]')`。

`options` 字典：`noError`（找不到时静默返回 -1）、`index`（取第 n 个匹配）、`proxy`（在指定代理对象的子树内搜索）。

### ⚠ 别名陷阱（本仓库已知问题 #1 的根因）

`sim.setObjectAlias(handle, alias)` 对别名有严格字符限制：

> Allowed characters include **0-9, a-z, A-Z, and underscores. Illegal characters are replaced with underscores.
> （合法字符仅为数字、字母、下划线；**非法字符（含 `/`）会被替换为下划线**。）

因此 `setObjectAlias('/Mobile/AGV_Shared', ...)` 实际把别名存成 `_Mobile_AGV_Shared`，随后 `sim.getObject('/Mobile/AGV_Shared')` 按路径查会失败。**正确做法**：别名本身只用下划线层级（如 `Mobile_AGV_Shared`），仅在 `getObject` 查询时用 `/` 作路径前缀。详见 [[02-01-分层命名场景构建]]。

## `sim` 模块加载

```lua
sim = require('sim')
```
从 CoppeliaSim 4.6 起，`sim` 模块在脚本中**默认预加载**，可直接使用 `sim.*`。Python 端经 Remote API 取得：`sim = client.require('sim')`。

## API 函数分类（完整目录）

手册将 Regular API 函数按对象/领域分为 40+ 类，下表列出主要类别与代表性函数（完整列表见 `raw/src-doc-CoppeliaSim-API-functions.md`）：

| 类别 | 代表函数 |
|------|---------|
| 文件操作 | `sim.loadScene` / `sim.saveScene` / `sim.loadModel` / `sim.importShape` / `sim.importMesh` / `sim.exportMesh` |
| 句柄获取 | `sim.getObject` / `sim.getObjectFromUid` / `sim.getObjects` / `sim.getObjectsInTree` / `sim.isHandle` |
| 场景对象 | `sim.getObjectAlias`/`setObjectAlias`、`getObjectParent`/`setObjectParent`、`getObjectChild`、`copyPasteObjects`、`scaleObject`、`getObjectType`、`getObjectSel`/`setObjectSel` |
| 仿真控制 | `sim.startSimulation` / `sim.pauseSimulation` / `sim.stopSimulation` / `sim.getSimulationState` / `sim.getSimulationTime` / `sim.getSimulationTimeStep` / `sim.getRealTimeSimulation` |
| 关节 | `sim.setJointPosition` / `getJointPosition` / `setJointTargetPosition` / `setJointTargetVelocity` / `getJointVelocity` / `setJointTargetForce` / `getJointForce` / `setJointMode` / `getJointMode` / `setJointInterval` / `getJointType` / `setJointDependency` |
| 动力学 | `sim.handleDynamics` / `sim.setJointTargetPosition` / `sim.readForceSensor` / `sim.addForce` / `sim.addForceAndTorque` / `sim.getVelocity` / `sim.setLinkDummy` / `sim.getShapeMass`/`setShapeMass` / `sim.computeMassAndInertia` |
| 形状 | `sim.createShape` / `createPrimitiveShape` / `getShapeMesh` / `getShapeViz` / `groupShapes` / `ungroupShape` / `alignShapeBB` / `relocateShapeFrame` / `getShapeColor`/`setShapeColor` |
| 变换/位姿 | `sim.buildMatrix`/`buildPose` / `matrixToPose`/`poseToMatrix` / `multiplyMatrices`/`multiplyPoses` / `interpolateMatrices`/`interpolatePoses` / `getMatrixInverse`/`getPoseInverse` / `getObject/setPosition` / `getObject/Orientation` / `getObject/Quaternion` / `getObject/ChildPose` / `rotateAroundAxis` / `getRotationAxis` / `multiplyVector` |
| 碰撞/距离 | `sim.checkCollision` / `checkCollisionEx` / `sim.checkDistance` |
| 接近传感器 | `sim.handleProximitySensor` / `readProximitySensor` / `resetProximitySensor` / `checkProximitySensor*` / `createProximitySensor` |
| 视觉传感器 | `sim.handleVisionSensor` / `readVisionSensor` / `getVisionSensorImg`/`setVisionSensorImg` / `getVisionSensorDepth` / `getVisionSensorRes` / `createVisionSensor` / `adjustView` |
| 力传感器 | `sim.readForceSensor` / `sim.createForceSensor` |
| 图 | `sim.addGraphStream` / `setGraphStreamValue` / `addGraphCurve` / `handleGraph` / `resetGraph` |
| 八叉树/点云 | `sim.createOctree` / `insertVoxelsIntoOctree` / `createPointCloud` / `insertPointsIntoPointCloud` / `getPointCloudPoints` |
| 路径/轨迹 | `sim.createPath` / `getPathLengths` / `getPathInterpolatedConfig` / `resamplePath` / `generateTimeOptimalTrajectory` |
| 属性 (Properties) | `sim.setBoolProperty`/`getBoolProperty`、`setIntProperty`/`getIntProperty`、`setFloatProperty`/`getFloatProperty`、`setStringProperty`/`getStringProperty`、`setBufferProperty`/`getBufferProperty`、`setIntArrayProperty` 等 |
| 信号 (Signals) | **4.10 起推荐经"信号属性"访问**（见 `properties.htm#signals`）；旧式 `sim.setIntegerSignal`/`getIntegerSignal`、`setFloatSignal`/`getFloatSignal`、`setStringSignal`/`getStringSignal`、`clearIntegerSignal`、`clearAllSignals` 仍可用 |
| 自定义数据 | 经"自定义数据属性"访问（`properties.htm#customData`），可在对象/场景上挂持久化数据块 |
| 脚本 | `sim.createScript` / `initScript` / `callScriptFunction` / `getScriptFunctions` / `executeScriptString` / `getApiFunc` / `getApiInfo` / `registerScriptFuncHook` / `getStackTraceback` |
| 线程 | `sim.setStepping` / `sim.step` / `sim.switchThread` 等（单步模式相关） |
| 消息通信 | `sim.broadcastMsg`（→ 各脚本 `sysCall_msg`） |
| 渲染/绘图 | `sim.addDrawingObject` / `addDrawingObjectItem` |
| 模型 | 组建/拆解模型相关 |

### 关节控制模式
```lua
sim.jointmode_passive              -- 被动（外力驱动）
sim.jointmode_movement             -- 运动（运动学）
sim.jointmode_ik                   -- IK
sim.jointmode_dependent            -- 依赖
sim.jointmode_dynamic              -- 动力学
sim.jointmode_torque               -- 力矩
sim.jointmode_force                -- 力
```

## 关键函数签名

### sim.getObject
```python
int objectHandle = sim.getObject(string objectPath, dict options = {})
# options: noError(bool)、index(int)、proxy(int)
# 返回: 句柄或 -1
```

### sim.getObjectsInTree
```python
list objects = sim.getObjectsInTree(int treeBaseHandle,
                                   int objectType = sim.handle_all,
                                   int options = 0)
# treeBaseHandle 也可传 sim.handle_scene 取场景全部对象
# options 位码: bit0=1 排除树根; bit1=2 仅含直接子对象
```
> ⚠ 仿真未启动或子树含不可查询对象时可能抛 "Operation cannot be accomplished in current state"，导致采集到 0 关节。本仓库 `RobotArmAnimator` 已加全场景关节扫描回退（已知问题 #4）。

### sim.getSimulationState
```python
int simulationState = sim.getSimulationState()   # 返回 simulationStatus 常量
```
状态码用于冻结排查（本仓库 MCP `get_simulation_state` 即封装此调用）。状态图见 `raw/src-doc-CoppeliaSim-simulation.md`。

### sim.setStepping
```python
int prevStepLevel = sim.setStepping(bool enable)
# 累加生效: 启用 n 次需禁用 n 次才回到初始态
# 返回前一级别 (0 表示之前未启用)
```

## 常用最佳实践

### 对象句柄
- 避免在每次回调中重复 `sim.getObject()`，把句柄缓存到全局变量（`sysCall_init` 中一次性获取）。
- 用绝对路径（`/` 开头）定位对象；别名唯一时可简化为 `/Object`。

### 坐标变换
```lua
local relPos  = sim.getObjectPosition(tip, base)                 -- 相对 base
local localPos = sim.getObjectPosition(tip, sim.handle_world)     -- 世界系
-- 优先用 sim.multiplyPoses / sim.interpolatePoses 做组合与插值，而非手算欧拉角
```

### 性能
- 减少 `sim.switchThread()` 调用频率。
- 脚本间通信用信号 / 自定义数据属性 / `broadcastMsg`，避免轮询。
- 被远程调用的脚本函数应短小，否则会阻塞整个仿真（长任务应改为外部触发、由系统回调在合适时机处理）。

## API 版本兼容性

> 本条目面向 CoppeliaSim 4.10。Remote API 协议版本 2 对应 4.6.0+（协议 1 对应 4.5.1 及更早），见 [[01-02-Remote-API-ZMQ]] 的协议版本表。

| 函数 | 4.10 | 4.6+ | 4.0+ | 注意 |
|------|------|------|------|------|
| `sim.getObject` | ✅ | ✅ | ✅ | 4.0 起推荐，替代旧版 `sim.getObjectHandle` |
| `sim.setStepping` | ✅ | ✅ | ❌ | 4.2+ 引入 |
| `sim.callScriptFunctionEx` | ✅ | ✅ | ❌ | 4.3+ 引入（C++ 端） |
| 信号 `setIntegerSignal` 等 | ✅(兼容) | ✅ | ✅ | 4.10 起推荐改用信号属性 |

---

**关联条目：**
- [[wiki/01-API参考/01-02-Remote-API-ZMQ]] — ZeroMQ Remote API（同一套 `sim.*` 的远程代理）
- [[wiki/07-API脚本示例/07-01-Lua基本脚本模式]] — Lua 脚本模式详解
- [[wiki/02-仿真建模/02-01-分层命名场景构建]] — 别名路径陷阱的工程化规避
- [[wiki/99-附录/99-01-术语表]] — 常用术语
