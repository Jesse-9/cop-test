---
title: CoppeliaSim 平台概述与架构
type: concept
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/基础知识
  - 难度/初级
created: 2024-07-13
updated: 2026-07-24
status: published
source:
  - welcome.htm
  - coppeliaSimFeatures.htm
  - functionality.htm
  - entities.htm
  - objects.htm
related:
  - "[[wiki/00-基础知识/00-03-安装与设置]]"
  - "[[wiki/00-基础知识/00-04-场景与模型管理]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
  - "[[wiki/01-API参考/01-04-插件API总览]]"
sim_version: "4.10"
---

# CoppeliaSim 平台概述与架构

## 平台定位

**CoppeliaSim**（由 Coppelia Robotics 开发）是一款基于**分布式控制架构**的机器人仿真平台，集成开发环境（IDE）内置几何建模、运动学、动力学、视觉、路径规划等模块。其架构理念是：场景中的每个对象/模型都可被独立控制——通过嵌入式脚本、插件、ROS/ROS2 节点、Remote API 客户端或自定义方案中的任意一种，彼此可并存且协同工作。这使其在多机器人协作、复杂自动化系统与快速算法验证场景中尤为突出（见 `welcome.htm`）。

控制器可用语言：**C/C++、Python、Java、Lua、Matlab、Octave**。CoppeliaSim 既可独立运行，也可凭借小体积与精致 API 嵌入到上层主客户端应用中；内置 Lua 与 Python 脚本解释器，允许用户将低层功能组合为新的高层功能。

典型应用方向（手册列举）：工厂自动化仿真、远程监控、硬件控制、快速原型与验证、安全监控、算法快速开发、机器人教育、产品展示。

## 五种编程途径（互兼、可并用）

CoppeliaSim 提供 5 种相互兼容、可同时使用的编程途径（见 `coppeliaSimFeatures.htm`/`apisOverview.htm`）：

| 途径 | 语言支持 | 定位 |
|------|---------|------|
| **Regular API** | Python、Lua、C/C++ | 仿真器主函数集，统一在 `sim` 命名空间下（如 `sim.getObject`）。可嵌入脚本内运行，也可经 Remote API 从外部调用同一组函数 |
| **Remote API** | C/C++、Python、Java、JavaScript、Matlab、Octave、Lua、Rust | 外部进程/远程机器控制仿真或仿真器本身。4.10 下含两个框架：**ZeroMQ remote API**（Python/C++/Matlab/Octave/Java/Lua/Rust）与 **WebSocket remote API**（JavaScript）。旧的 legacy remote API 已废弃 |
| **ROS interfaces** | 经 ROS/ROS2 包（C++，Lua/Python 可调） | 发布/订阅/服务调用，支持所有标准消息并可扩展。分别由 simROS、simROS2 插件提供 |
| **B0-based remote API（BlueZero）** | C++、Python、Java、Matlab、Lua | **已废弃**，手册明确推荐改用 ZeroMQ remote API |
| **Plugins / add-ons / Python packages / Lua modules** | C/C++（插件共享库）等 | 插件以 `loadPlugin` 按需载入，为脚本注册新命令；add-ons/模块跨场景运行 |

> Regular API 并非"只有 Lua"——手册的 `apiFunctions.htm` 为每个函数同时给出 Python、Lua、C/C++ 三栏签名。Regular API 的所有单位为**米、千克、秒、弧度**或其组合（另有说明除外）。

### ZeroMQ Remote API 引子（可运行）

下面是手册给出的 Python 客户端最小示例——以步进（stepping）模式启动仿真并运行 3 秒，精确同步每个 `sim.step()`：

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

sim.setStepping(True)
sim.startSimulation()
while (t := sim.getSimulationTime()) < 3:
    print(f'Simulation time: {t:.2f} [s]')
    sim.step()
sim.stopSimulation()
```

这正是本仓库 `factory_core.py` 的时钟主导者模式（Python 端 `sim.setStepping(True)` 后逐帧 `sim.step()` 推进 `SIM_STEP`）所遵循的官方做法。

## 核心功能模块

手册将功能划分为以下主类（`functionality.htm`/`coppeliaSimFeatures.htm`）：

- **几何/网格（Geometry/Mesh）**：图元提取、凸分解、网格抽减、编辑模式等。
- **运动学（Kinematics）**：任意机构（分支、闭环、冗余、嵌套闭环）的正/逆运动学；IK/FK 算法有可嵌入版本，可跑在真实机器人上。
- **动力学（Dynamics）**：5 个物理引擎可选——**MuJoCo、Bullet Physics、ODE、Vortex、Newton Dynamics**；模拟碰撞响应、抓取等真实交互。
- **软体（Soft Bodies）**：经 MuJoCo 引擎支持软体、绳、布等。
- **碰撞检测（Collision Detection）**：mesh、OC tree、点云或其集合间的快速干涉检测。
- **最小距离计算（Minimum Distance Calculation）**：凸/凹、开/闭 mesh、OC tree、点云间的精确最小距离。
- **接近传感器仿真**：可定制检测体积内的精确最小距离检测，比离散射线传感器更连续；作用于 mesh、OC tree、点云。
- **视觉传感器仿真**：可扩展的图像处理（可经插件）。
- **路径/运动规划（Path/Motion Planning）**：通过 OMPL 库（以插件形式封装）灵活支持。
- **数据记录与可视化**：可录制数据流，显示时序图、x/y 图、3D 曲线。
- **自定义用户界面**：数量不限的可定制 UI 元素。
- **数据导入/导出**：URDF、SDF、Collada、STL、DXF、OBJ、glTF 等。
- **RRS 接口与运动库**：完整实现 RRS-1 接口规范，支持 Reflexxes Motion Library 与 Ruckig Online Trajectory Generator。
- **场景层级、模型浏览器、仿真中全交互**：场景层级直观展示别名/类型/关联脚本/闭环/选择/可见性等；模型浏览器支持拖放（仿真中也行）；仿真中可平移、旋转、复制、缩放、删除模型及其行为脚本而无需改代码。

## 场景对象类型总览

场景由 **scene objects** 与 **collections** 构成，二者统称 **entities**（`entities.htm`）。手册定义的场景对象类型如下（`objects.htm`）：

| 类型 | 说明 |
|------|------|
| Shapes | 由三角面构成的刚体网格 |
| Joints | 连接对象间提供 1 或 3 自由度；可为被动或主动（驱动） |
| Graphs | 记录并可视化仿真数据 |
| Dummies | 带朝向的点，多用途对象 |
| Proximity sensors | 在检测体积内以几何精确方式检测特定对象 |
| Vision sensors | 相机型传感器，对光照/颜色/图像反应 |
| Force sensors | 测量施加于其上的力与力矩 |
| Cameras | 从不同视点观察场景 |
| Lights | 照明对象 |
| OC trees | 体素构成的空间分区数据结构 |
| Point clouds | 含点的 OC tree 结构 |
| Paths | 空间中带朝向的点的序列；伪场景对象（由若干场景对象组成） |
| **Script objects** | 把脚本封装为场景对象，分 simulation script 与 customization script 两类 |

部分对象可带特殊属性以与功能交互：**Collidable**（可碰撞检测）、**Measurable**（可计算最小距离）、**Detectable**（可被接近传感器检测）、**Viewable**（可被观察/成像）。

### 父子关系与位姿

每个场景对象有**绝对位姿**（相对世界参考系）与**局部位姿**（相对父对象参考系）。将 A 挂到 B 下时，A 的绝对位姿不变、局部位姿被改写；移动 B 时 A 自动跟随。可通过 `[Edit > Set parent, keep pose(s)]` 或在场景层级中拖放建立父子关系，用 `[Edit > Set parent-less]` 解除。

## 脚本体系（4.10：script objects）

CoppeliaSim 4.10 以 **script objects** 为核心组织脚本（`scripts.htm`/`scriptObjects.htm`），不再是旧版 "child script/main script" 二分法。

脚本类型分两大域：

- **嵌入式脚本（embedded scripts）**：嵌入场景/模型、随场景一起保存加载。
  - **main script**：场景主脚本，每场景一个。
  - **simulation scripts**：经 **script object** 封装，仅在仿真运行时执行，负责模型/对象的仿真功能。
  - **customization scripts**：经 **script object** 封装，仿真未运行时也执行，负责场景/模型的定制化交互。
- **非嵌入式**：不绑定特定场景/模型/对象，跨场景运行。
  - **sandbox**：沙箱脚本。
  - **add-ons**：附加项。

> **script object** 是把脚本封装为场景对象的载体，本身不作 3D 对象参与渲染；通过 `[Add > Script]` 或 `sim.createScript` 创建；双击即打开脚本对话框与编辑器。脚本通过回调被调用并遵循特定执行顺序，可 threaded 或 non-threaded。

脚本解释器：内置 **Lua**，外置 **Python**，二者运行方式几乎一致。CoppeliaSim 在二者上扩展出以 `sim` 前缀标识的专属命令（如 `sim.getObjectPosition`）。

## 积木式（Building Block）架构理念

手册强调 **Building Block Concept**：从传感器、执行器到整个机器人系统，都可在 CoppeliaSim 内通过组合基本对象并用嵌入式脚本串联功能来搭建——**每个场景对象都可拥有自己的嵌入式脚本**。配合"单一可移植文件即可包含完整功能模型（含控制代码）"的便携性，构成其分布式、积木式架构的核心理念。这也解释了为何多机器人/多控制器场景可去中心化地协同运行。

## 文件类型

| 扩展名 | 类型 |
|--------|------|
| `.ttt` | 场景文件 |
| `.ttm` | 模型文件（可复用组件） |
| `.tt` | 纹理文件 |

## 学习路径

```
初级 → 界面与场景搭建（见 [[wiki/00-基础知识/00-03-安装与设置]]、[[wiki/00-基础知识/00-04-场景与模型管理]]）
  │
中级 → 脚本编程与 Regular API（见 [[wiki/01-API参考/01-01-Regular-API-overview]]）
  │
高级 → Remote API 控制、ROS 集成、插件开发（见 [[wiki/01-API参考/01-04-插件API总览]]）
  │
专家 → 数字孪生、协同仿真、性能优化
```

---

**关联条目：**
- [[wiki/00-基础知识/00-03-安装与设置]] — 安装与配置
- [[wiki/00-基础知识/00-04-场景与模型管理]] — 场景与模型组织
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 详解
- [[wiki/01-API参考/01-04-插件API总览]] — 插件 API 与命名空间
