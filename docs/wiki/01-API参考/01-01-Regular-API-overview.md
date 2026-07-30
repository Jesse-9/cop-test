---
title: CoppeliaSim API 框架与 Regular API 总览
type: reference
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/中级
created: 2024-07-13
updated: 2026-07-24
status: published
source:
  - apisOverview.htm
  - apiFunctions.htm
  - apiConstants.htm
  - properties.htm
  - zmqRemoteApiOverview.htm
related:
  - "[[wiki/00-基础知识/00-01-CoppeliaSim-overview]]"
  - "[[wiki/01-API参考/01-02-Remote-API-ZMQ]]"
  - "[[wiki/01-API参考/01-03-常量与参数ID]]"
  - "[[wiki/01-API参考/01-04-插件API总览]]"
  - "[[wiki/01-API参考/api/]]"
sim_version: "4.10"
---

# CoppeliaSim API 框架与 Regular API 总览

## 引言：一个可运行的例子

外部程序经 ZeroMQ Remote API 调得的 `sim.*` 函数，正是本条目所述的 Regular API。下面这段 Python 代码在 CoppeliaSim 4.10 上可直接运行：它取对象句柄、读位姿、再以单步模式推进仿真 3 秒。

```python
# pip install coppeliasim-zmqremoteapi-client
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')                       # 取得 sim 命名空间

handle = sim.getObject('/robot')                  # Regular API: 按路径取句柄
pos = sim.getObjectPosition(handle, sim.handle_world)   # 读世界系位置 [x,y,z] 米
print('robot position [m]:', pos)

sim.setStepping(True)                             # 启用单步模式（外部程序作时钟主导）
sim.startSimulation()
while (t := sim.getSimulationTime()) < 3:
    print(f'sim time: {t:.2f} [s]')
    sim.step()                                    # 推进一个仿真步
sim.stopSimulation()
```

> `sim.getObject` / `sim.getObjectPosition` / `sim.setStepping` / `sim.step` 都是 Regular API 函数；它们在脚本内部（Lua/Python）与在外部 Remote API 客户端上签名一致。这正是 Regular API 的核心特征——**一套函数，脚本内外通用**。

## API 框架：两大类函数

手册 `apisOverview.htm` 将 CoppeliaSim 的 API 划分为**两大组**：

| API 组 | 命名空间 | 提供者 | 典型函数 |
|--------|----------|--------|----------|
| **Regular API**（主函数） | `sim.*` | CoppeliaSim 本体 | `sim.getObject`、`sim.setJointPosition`、`sim.startSimulation` |
| **插件 API**（其他函数） | `simXxx.*`（各插件独立命名空间） | 各功能插件 | `simOMPL.*`、`simIK.*`、`simUI.*`、`simROS2.*` 等 |

Regular API 是 CoppeliaSim 自身提供的主函数集合，统一收敛在 `sim` 命名空间下；**属性（Properties）及其相关 API 函数是 Regular API 的重要组成部分**。插件 API 由具体插件提供，每个插件把函数挂在自己的独立命名空间下（如 `simOMPL`、`simUI`、`simIK`），其说明与 API 参考位于各插件的功能章节。

> 详见 [[01-04-插件API总览]]。本条目只讲 Regular API。

## Regular API 的三语言支持

> ⚠ **纠正旧条目错误**：旧版 wiki 曾把 Regular API 说成"只有 Lua"。实际上手册 `apiFunctions.htm` 对**每个**函数都给出两栏：一栏是 `language-python-lua`（Python 与 Lua 共享同一 `sim.*` 驼峰命名），另一栏是 `language-c++`（C/C++ 端的 `simXxx` 驼峰命名）。因此 Regular API 在**脚本侧支持 Python 和 Lua**，在**插件/C++ 侧支持 C/C++**。

| 语言 | 函数命名风格 | 调用位置 | 示例 |
|------|--------------|----------|------|
| **Python** | `sim.camelCase` | 脚本内 / ZMQ Remote API 客户端 | `sim.getObjectPosition(h, sim.handle_world)` |
| **Lua** | `sim.camelCase` | 脚本内（与 Python 同名同签名） | `sim.getObjectPosition(h, sim.handle_world)` |
| **C/C++** | `simCamelCase` | 插件 / 嵌入式 C++ 客户端 | `simGetObjectPosition(h, sim_handle_world)` |

Python 与 Lua 共享完全相同的 `sim.*` 函数名与签名（细微语法差异见 `luaPythonDifferences.htm`）；C++ 接口函数名首字母大写且无 `sim.` 前缀点号。本仓库及外部程序均走 Python ZMQ Remote API 客户端，故后续示例以 Python 为主。

## 单位约定

手册 `apiFunctions.htm` 开篇即声明：

> All units going to, or coming from the API are in **meters, kilograms, seconds and radians** or a combination of those (unless otherwise explicitly indicated)

即进出 Regular API 的所有数值默认使用**米、千克、秒、弧度**（或其组合），除非另行显式说明。位置是米、角度是弧度、力是牛顿、时间是秒。

## 属性（Properties）与 Regular API 的关系

属性是 Regular API 的重要组成。手册 `properties.htm` 指出：CoppeliaSim 中**所有内部变量或状态**都可作为属性被查询/修改，通过一组专用 API 函数访问。属性隶属于某个 **target**：

| Target | 含义 | 常量 |
|--------|------|------|
| `sim.handle_app` | CoppeliaSim 全局 | 应用级设置、命名参数 |
| `sim.handle_scene` | 当前场景 | 场景级重力、信号 |
| 对象句柄 | 具体场景对象 | 关节区间、网格顶点、颜色等 |

属性访问函数按类型成对出现（`set*Property` / `get*Property`）：`Bool/Int/Long/Float/String/Buffer`、`IntArray/FloatArray/IntArray2`、`Vector2/Vector3/Quaternion/Pose/Color`，以及泛化的 `getProperty`/`setProperty`/`getProperties` 等。

属性有四类**特殊前缀**（同属 Regular API，但语义分层）：

| 前缀 | 类别 | 挥发性 | 示例 |
|------|------|--------|------|
| `customData.` | 自定义数据 | **持久化**（存盘，不随脚本状态销毁） | `sim.setBufferProperty(h, 'customData.myTag', sim.packTable(...))` |
| `signal.` | 信号 | **易失**（创建它的脚本状态销毁时自动清除） | `sim.setIntProperty(sim.handle_scene, 'signal.myInt', 37)` |
| `namedParam.` | 命名参数 | 易失，仅 `sim.handle_app` | 命令行 `-G` 传入的用户参数 |
| `bullet.`/`ode.`/`newton.`/`vortex.`/`mujoco.` | 物理引擎相关 | 引擎参数 | 引擎开关、步长等 |

```python
# 属性访问示例（Python ZMQ Remote API）
sim.setVector3Property(sim.handle_scene, 'gravity', [0.0, 0.0, 0.0])  # 关闭重力
low, high = sim.getFloatArrayProperty(jointHandle, 'interval')        # 关节上下限
meshes = sim.getIntArrayProperty(shapeHandle, 'meshes')               # 组成 shape 的 mesh 句柄
```

> ⚠ **4.10 信号迁移**：旧式 `sim.setIntegerSignal`/`sim.setFloatSignal`/`sim.setStringSignal` 等仍可用，但 4.10 起推荐经 `signal.*` 属性访问（见上表）。自定义数据同理，推荐经 `customData.*` 属性而非旧式 `sim.setBufferProperty` 裸调用。

## 按类别的函数清单总览

手册 `apiFunctions.htm` 将 Regular API 按对象/领域分类，每类均给出 Python/Lua 与 C++ 两栏。下表汇总主要类别与代表性函数（完整清单以手册为准）：

| 类别 | 代表性函数（Python/Lua） |
|------|--------------------------|
| 文件操作 | `sim.loadScene`/`saveScene`/`loadModel`/`saveModel`/`importShape`/`importMesh`/`exportMesh`/`createTexture` |
| 句柄获取 | `sim.getObject`/`getObjectFromUid`/`isHandle`/`getObjects`/`getObjectsInTree`/`getCollectionObjects`/`getReferencedHandles` |
| 场景对象 | `getObject`/`getObjectUid`/`getObjectAlias`/`setObjectAlias`/`getObjectParent`/`setObjectParent`/`getObjectChild`/`copyPasteObjects`/`scaleObject`/`getObjectType`/`getObjectSel`/`setObjectSel` |
| 仿真控制 | `startSimulation`/`pauseSimulation`/`stopSimulation`/`getSimulationState`/`getSimulationTime`/`getSimulationTimeStep`/`getRealTimeSimulation` |
| 关节 | `setJointPosition`/`getJointPosition`/`setJointTargetPosition`/`setJointTargetVelocity`/`getJointVelocity`/`setJointTargetForce`/`getJointForce`/`setJointMode`/`getJointType`/`setJointInterval`/`setJointDependency`/`createJoint` |
| 动力学 | `handleDynamics`/`readForceSensor`/`addForce`/`addForceAndTorque`/`getVelocity`/`setLinkDummy`/`getLinkDummy`/`getShapeMass`/`setShapeMass`/`computeMassAndInertia`/`createForceSensor`/`isDynamicallyEnabled` |
| 形状 | `createShape`/`createPrimitiveShape`/`createHeightfieldShape`/`getShapeMesh`/`getShapeViz`/`groupShapes`/`ungroupShape`/`alignShapeBB`/`relocateShapeFrame`/`getShapeColor`/`setShapeColor`/`generateTextShape` |
| Dummy | `createDummy`/`getLinkDummy`/`setLinkDummy` |
| 脚本 | `createScript`/`initScript`/`callScriptFunction`/`getScriptFunctions`/`executeScriptString`/`getApiFunc`/`getApiInfo`/`registerScriptFuncHook`/`getStackTraceback` |
| 变换/位姿 | `buildMatrix`/`buildPose`/`matrixToPose`/`poseToMatrix`/`multiplyMatrices`/`multiplyPoses`/`interpolateMatrices`/`getMatrixInverse`/`getPoseInverse`/`getObjectPosition`/`setObjectPosition`/`getObjectOrientation`/`getObjectQuaternion`/`getObjectPose`/`setObjectPose`/`rotateAroundAxis`/`getRotationAxis` |
| 碰撞/距离 | `checkCollision`/`checkCollisionEx`/`checkDistance` |
| 接近传感器 | `handleProximitySensor`/`readProximitySensor`/`resetProximitySensor`/`checkProximitySensor`/`checkProximitySensorEx`/`checkProximitySensorEx2`/`createProximitySensor` |
| 视觉传感器 | `handleVisionSensor`/`readVisionSensor`/`getVisionSensorImg`/`setVisionSensorImg`/`getVisionSensorDepth`/`getVisionSensorRes`/`adjustView`/`createVisionSensor` |
| 力传感器 | `readForceSensor`/`createForceSensor` |
| 图 | `addGraphStream`/`setGraphStreamValue`/`addGraphCurve`/`handleGraph`/`resetGraph` |
| 八叉树 | `createOctree`/`insertVoxelsIntoOctree`/`removeVoxelsFromOctree`/`getOctreeVoxels`/`checkOctreePointOccupancy` |
| 点云 | `createPointCloud`/`insertPointsIntoPointCloud`/`removePointsFromPointCloud`/`getPointCloudPoints` |
| 路径/轨迹 | `createPath`/`getPathLengths`/`getPathInterpolatedConfig`/`resamplePath`/`generateTimeOptimalTrajectory`/`generateShapeFromPath` |
| 属性 | `setBoolProperty`/`getBoolProperty`、`setIntProperty`/`getIntProperty`、`setFloatProperty`/`getFloatProperty`、`setStringProperty`/`getStringProperty`、`setBufferProperty`/`getBufferProperty`、`setIntArrayProperty`/`setFloatArrayProperty`/`setVector2Property`/`setVector3Property`/`setQuaternionProperty`/`setPoseProperty`/`setColorProperty`、`getProperty`/`setProperty`/`getProperties`/`removeProperty`/`getPropertyName`/`getPropertyInfo` |
| 集合 | `createCollection`/`destroyCollection`/`addItemToCollection`/`getCollectionObjects` |
| 信号 | 4.10 推荐经 `signal.*` 属性访问；旧式 `setIntegerSignal`/`getIntegerSignal`/`setFloatSignal`/`setStringSignal`/`clearIntegerSignal`/`clearAllSignals` 仍兼容 |
| 自定义数据 | 经 `customData.*` 属性访问 |
| 渲染/绘图 | `addDrawingObject`/`addDrawingObjectItem`/`removeDrawingObject`/`getObjectColor`/`setObjectColor`/`changeEntityColor`/`restoreEntityColor` |
| 线程/单步 | `setStepping`/`step`/`setAutoYieldDelay`/`acquireLock`/`releaseLock`/`switchThread` |
| 消息通信 | `broadcastMsg`（→ 各脚本 `sysCall_msg`）/`getSimulatorMessage`/`pushUserEvent` |
| 运动功能 | `moveToConfig`/`moveToPose` 及 `_init`/`_step`/`_cleanup` 变体、`ruckigPos`/`ruckigVel`/`ruckigStep` |
| 打包/解包 | `packTable`/`unpackTable`/`packUInt8Table`/`unpackFloatTable`/`copyTable`/`transformBuffer` |
| 纹理/图像 | `getTextureId`/`readTexture`/`writeTexture`/`loadImage`/`saveImage`/`transformImage` |
| 辅助控制台 | `auxiliaryConsoleOpen`/`auxiliaryConsoleClose`/`auxiliaryConsolePrint`/`auxiliaryConsoleShow` |
| 模型 | `saveModel`/`loadModel` |
| 其他 | `addLog`/`getPluginName`/`getPluginInfo`/`getSystemTime`/`launchExecutable`/`quitSimulator`/`getExplicitHandling`/`setExplicitHandling` |

> 每个类别的完整三栏清单（Python/Lua + C++）见 `apiFunctions.htm`；各函数的签名、参数、返回值见 `regularApi/{funcName}.htm` 单页（本 wiki 在 [[01-API参考/api/]] 子目录逐条精修）。

## 常量体系

Regular API 的常量集中在 `apiConstants.htm`，按用途分组：

- **General object types**：`sim.objecttype_sceneobject`、`sim.objecttype_collection`、`sim.objecttype_mesh`、`sim.objecttype_texture`
- **Scene object types**：`sim.sceneobject_shape`、`sim.sceneobject_joint`、`sim.sceneobject_graph`、`sim.sceneobject_camera`、`sim.sceneobject_light`、`sim.sceneobject_dummy`、`sim.sceneobject_proximitysensor`、`sim.sceneobject_octree`、`sim.sceneobject_pointcloud`、`sim.sceneobject_visionsensor`、`sim.sceneobject_forcesensor`、`sim.sceneobject_script`（配合 `sim.getObjectType`、`sim.getObjectsInTree` 使用）
- **Scene object sub-types**：`sim.light_omnidirectional`/`light_spot`/`light_directional`；`sim.joint_revolute`/`joint_prismatic`/`joint_spherical`；`sim.shape_simple`/`shape_compound`；`sim.proximitysensor_ray`/`pyramid`/`cylinder`/`disc`/`cone`
- **Simulator messages**：`sim.message_model_loaded`、`sim.message_scene_loaded`、`sim.message_keypress`（`auxData[0]`=按键，`auxData[1]`=Ctrl/Shift 状态）等，经 `sim.getSimulatorMessage` 查询
- **事件回调消息**：`sim_message_eventcallback_instancepass`（每个主循环触发，`auxData[0]` 含事件位码：bit0 对象删除、bit1 对象创建、bit2 模型加载、bit3 场景加载、bit11 仿真开始、bit12 仿真结束 等）、`..._instanceabouttoswitch`、`..._scenesave`、`..._abouttoundo` 等

> 常量与参数 ID 的完整对照见 [[01-03-常量与参数ID]]。

## 与远程 API 的关系

手册 `zmqRemoteApiOverview.htm` 明确：

> The ZeroMQ remote API ... offers **all API functions also available via a CoppeliaSim script**: this includes all regular API functions (i.e. `sim.*`-type functions), but also all API functions provided by plugins (e.g. `simOMPL.*`, `simUI.*`, `simIK.*`, etc.), if enabled.

即 **ZMQ Remote API = Regular API + 插件 API 的远程代理**。外部程序经 ZeroMQ + CBOR 调用，获得的 `sim.*` 与脚本内调用的 `sim.*` 是同一套函数，签名一致；插件命名空间同样可经 `client.require('simIK')` 等取得。这意味着：

- 本条目所述的全部 Regular API 函数，**都能从外部 Python/C++/Java 客户端调用**，无需写脚本。
- 单步模式（`setStepping(True)` + `step()`）让外部程序成为**时钟主导**，每个仿真步由外部触发——这是本仓库精确工业节拍时序的基础。
- Remote API 支持 stepping（与每步同步）与 non-stepping（常规运行）两种模式，甚至支持远程加载场景、启停仿真等对仿真器本身的控制。

```python
# 取得多个命名空间（Regular API + 插件 API）
client = RemoteAPIClient()
sim = client.require('sim')        # Regular API
simIK = client.require('simIK')    # 插件 API: 逆运动学
simOMPL = client.require('simOMPL')  # 插件 API: 运动规划
```

> Remote API 的协议版本、客户端语言、与本仓库双连接设计详见 [[01-02-Remote-API-ZMQ]]。

## 关键函数签名速查

### sim.getObject（按路径取句柄）
```python
int objectHandle = sim.getObject(string objectPath, dict options = {})
# options: noError(bool)、index(int)、proxy(int)
# 返回: 对象句柄；找不到时默认抛错，options.noError=True 时返回 -1
```
路径支持绝对（`/Robot`）、相对（`./`、`../`、`:`、`::`）、通配（`/Mobile*`）、同层索引（`/Robot[0]`）、全局索引（`/Robot{4}`）。⚠ `setObjectAlias` 设别名时**别名本身不应含 `/`**（非法字符会被替换为下划线），`/` 仅在 `getObject` 查询时作路径前缀。

### sim.getObjectPosition（读位置）
```python
list position = sim.getObjectPosition(int objectHandle, int relativeToObjectHandle = sim.handle_world)
# relativeToObjectHandle: sim.handle_world(绝对)、sim.handle_parent(相对父)、或任意对象句柄
# 返回: [x, y, z]，单位米
```

### sim.setStepping / sim.step（单步模式）
```python
int prevStepLevel = sim.setStepping(bool enable)   # 累加生效：启用 n 次需禁用 n 次才回初态
sim.step()                                          # 推进一个仿真步
```

### sim.getSimulationState
```python
int state = sim.getSimulationState()   # 返回 simulationStatus 常量
```

## 参见

- [[01-02-Remote-API-ZMQ]] — ZeroMQ Remote API（同一套 `sim.*` 的远程代理，含协议版本表）
- [[01-03-常量与参数ID]] — 常量与参数 ID 完整对照
- [[01-04-插件API总览]] — 插件 API（`simIK.*`/`simOMPL.*`/`simUI.*` 等）
- [[wiki/01-API参考/api/]] — Regular API 单函数精修条目子目录
- [[00-基础知识/00-01-CoppeliaSim-overview]] — CoppeliaSim 总览
