---
title: Python 客户端集成——库与 ZMQ 远程 API 对比
type: tutorial
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/协同仿真
  - 难度/中级
created: 2026-07-24
updated: 2026-07-24
status: published
source:
  - pythonClient.htm
  - externalControllerTutorial.htm
  - externalFrontEnd.htm
related:
  - "[[wiki/01-API参考/01-02-Remote-API-ZMQ]]"
  - "[[wiki/05-数字孪生与协同仿真/05-02-数字孪生概述]]"
  - "[[wiki/06-工业案例/06-02-岛式车间柔性生产案例]]"
  - "[[wiki/00-基础知识/00-06-插件与附加组件]]"
sim_version: "4.10"
---

# Python 客户端集成——库与 ZMQ 远程 API 对比

## 概述

CoppeliaSim 的图形界面覆盖了大部分常规操作，但当需要对**仿真循环做细粒度控制**（如强化学习训练中每步读传感器、写控制量），就要把 CoppeliaSim 当成一个可编程组件来驱动。官方手册给出两条 Python 集成路径：

| 路径 | 进程模型 | 同步方式 | 典型场景 |
|------|---------|---------|---------|
| **A. 把 CoppeliaSim 当库**（ctypes 加载 `libcoppeliaSim`） | 单进程：主线程跑 UI，次线程跑 SIM | 主线程内同步，无通信开销 | 强化学习、批量参数扫描、自定义启动流程 |
| **B. ZMQ Remote API 客户端** | 双进程：CoppeliaSim 独立跑 + 外部 Python 脚本 | `setStepping(True)` 逐帧同步 | 外部控制、数字孪生、跨机器部署、Sim2Real |

本仓库 [[wiki/06-工业案例/06-02-岛式车间柔性生产案例]] 采用路径 B（ZMQ + stepping 模式），Python 端作为时钟主导者逐帧推进物理引擎。

## 七种控制方式概览

官方手册（`externalControllerTutorial.htm`）列出控制 CoppeliaSim 中机器人/仿真的多种方式：

1. **仿真脚本（Simulation Script）**：Python 或 Lua，场景对象，随复制而复制，无需编译，可线程化或非线程化。Lua 下使用 regular API 无通信延迟，是应用主线程的一部分（天然同步）。
2. **插件（Plugin）**：支持回调、自定义脚本函数注册、调用外部库。与仿真脚本常配合使用（插件注册脚本函数 → 脚本调用 → 回调插件）。同样无通信延迟、属主线程同步，但需编译、编程复杂。
3. **自定义客户端应用**：用 C/C++ 或 Python 直接加载 CoppeliaSim 库（路径 A），可自定义启动流程，与仿真在全局、即时层面交互。
4. **Remote API 外部客户端**：外部应用通过 ZeroMQ 或 WebSocket Remote API 控制。便于跨进程/跨机器运行控制代码，且**控制真实机器人与虚拟机器人可共用同一份代码**（Sim2Real）。
5. **ROS 节点**：分布式多进程通信，生态丰富，但比 Remote API 更重、更复杂。
6. **外部应用 + 自定义通信**：通过管道、ZeroMQ、socket、串口等与 CoppeliaSim 插件或脚本通信。语言不限、灵活，但比 Remote API 繁琐。
7. **customization script / add-on**：不推荐用于控制，更适合处理仿真未运行时的功能。

官方提供 7 个对照场景 `scenes/controlTypeExamples/`（脚本/插件/Remote API/ROS/ROS2/ZMQ/TCP），所有场景都保留了仿真脚本作为对外接口的桥接层。

## 路径 A：把 CoppeliaSim 当库

### 原理

通过 `ctypes` 加载 `libcoppeliaSim`（Windows 为 `coppeliaSim.dll`，Linux 为 `libcoppeliaSim.so`，macOS 为 `libcoppeliaSim.dylib`），在**同一进程**内跑两个线程：

- **UI 线程（主）**：调用 `simRunGui` 驱动界面事件循环。即使 headless 也必须调用，参数 `sim_gui_headless` 即无界面模式，`sim_gui_all` 开启全部界面。
- **SIM 线程（次，必建）**：调用 `simInitialize` 初始化，之后循环调 `simLoop` 直到 `simGetExitRequest` 返回 false，最后 `simDeinitialize` 退出。

由于 SIM 线程与应用同进程、同地址空间，调用 regular API **无通信延迟**，适合需要高频次细粒度控制的强化学习等场景。

### 最小骨架（SIM 线程跑完整应用）

```python
import builtins, os, threading
from ctypes import *
# 1. 指定库路径
builtins.coppeliaSim_library = "/path/to/libcoppeliaSim.so"  # Windows: coppeliaSim.dll
# 2. 导入库函数（coppeliaSim.lib 模块来自 coppeliaSimClientPython 示例）
from coppeliaSim.lib import *

def simThreadFunc(appDir):
    simInitialize(c_char_p(appDir.encode('utf-8')), 0)
    while not simGetExitRequest():
        simLoop(None, 0)
    simDeinitialize()

appDir = os.path.dirname(builtins.coppeliaSim_library)
t = threading.Thread(target=simThreadFunc, args=(appDir,))
t.start()
simRunGui(sim_gui_all)        # 用 sim_gui_headless 跑无界面模式
t.join()
```

### 手动加载场景 + 逐帧步进

`coppeliaSim.bridge` 模块提供对脚本 regular API 的无缝访问，可手动加载场景并按指定步数推进：

```python
def simThreadFunc(appDir):
    simInitialize(c_char_p(appDir.encode('utf-8')), 0)
    import coppeliaSim.bridge
    coppeliaSim.bridge.load()
    global sim
    sim = coppeliaSim.bridge.require('sim')
    sim.loadScene('path/to/scene.ttt')

    simStart()
    for i in range(1000):
        t = sim.getSimulationTime()
        print(f'Simulation time: {t:.2f} [s] (running in stepped mode)')
        simStep()
    simStop()
    simDeinitialize()

def simStart():
    if sim.getSimulationState() == sim.simulation_stopped:
        sim.startSimulation()

def simStep():
    """推进到下一个仿真时刻（stepped 模式）。"""
    if sim.getSimulationState() != sim.simulation_stopped:
        t = sim.getSimulationTime()
        while t == sim.getSimulationTime():
            simLoop(None, 0)

def simStop():
    while sim.getSimulationState() != sim.simulation_stopped:
        sim.stopSimulation()
        simLoop(None, 0)
```

`coppeliaSim.bridge` 用法：

```python
import coppeliaSim.bridge
coppeliaSim.bridge.load()
sim = coppeliaSim.bridge.require('sim')
program_version = sim.getIntProperty(sim.handle_app, 'productVersionNb')
```

完整示例见官方仓库 `coppeliaSimClientPython/main_with_ui.py`。

## 路径 B：ZMQ Remote API 客户端（stepping 同步）

### 原理

CoppeliaSim 作为独立进程运行并启动 ZMQ remote API 服务端（菜单 Add-ons > ZMQ remote API server，默认端口 23000）。外部 Python 进程通过 `coppeliasim_zmqremoteapi_client` 连接，调用与脚本内**完全相同**的 regular API。

关键同步机制：`sim.setStepping(True)` 把仿真切换为**外部步进模式**，每次 `sim.step()` 推进一个 `SIM_STEP`（默认 0.05s）。Python 端成为**时钟主导者**，控制逻辑与物理引擎严格同步——这是精确工业节拍时序的基础。

### 完整可运行示例

```python
# pip install coppeliasim-zmqremoteapi-client
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

# 步进模式：Python 端控制时钟
sim.setStepping(True)
sim.startSimulation()

try:
    while (t := sim.getSimulationTime()) < 10.0:
        # 读取传感器/对象位姿
        # pos = sim.getObjectPosition(handle, sim.handle_world)
        # 写入控制量
        # sim.setJointTargetPosition(joint_handle, value)
        print(f't={t:.2f}s')
        sim.step()           # 推进一帧（SIM_STEP 默认 0.05s）
finally:
    sim.stopSimulation()
```

> 本仓库 [[wiki/06-工业案例/06-02-岛式车间柔性生产案例]] 即采用此模式：v1 运行器按固定顺序驱动每个 `SIM_STEP`，先执行控制逻辑（异常轮询/物料产出/AGV 调度/装配等），最后 `sim.step()` 推进物理引擎。

## 何时选哪种

| 需求 | 推荐路径 |
|------|---------|
| 机器人行为逻辑、随复制可用的模型自带控制 | 仿真脚本（方式 1） |
| 需要回调/自定义脚本函数注册/调用外部 C 库 | 插件（方式 2） |
| **强化学习**：每 episode 重启、每步高频次读写、自定义启动流程 | **路径 A（库方式）**——同进程零通信延迟 |
| 外部控制、跨机器部署、Sim2Real（同一份代码控真实/虚拟机器人） | **路径 B（ZMQ Remote API）** |
| 数字孪生（物理 → 虚拟单向同步或双向同步） | 路径 B，配合外部数据源 |
| 多进程分布式系统、需要丰富生态 | ROS / ROS2（方式 5） |
| 浏览器前端可视化 | WebSocket Remote API + Three.js front-end（见 `externalFrontEnd.htm`） |
| 非常规通信协议、语言不限 | 自定义通信 + 插件/脚本（方式 6） |

### 外部控制器范式与强化学习

强化学习等场景需要**细粒度控制循环**：每个 episode 重置场景、每个 step 读观测、计算动作、写入关节、推进一帧。路径 A 的库方式在此最合适——`sim.loadScene` 加载场景后手动 `simStep` 指定步数，同进程内调用 regular API 无通信延迟，避免 ZMQ 跨进程往返对高频训练的拖累。当控制代码需要跨机器部署或与真实机器人共用时，再切换到路径 B。

## 要点总结

- **库方式**（路径 A）的核心是 `ctypes` 加载 `libcoppeliaSim` + 双线程（UI 跑 `simRunGui`、SIM 跑 `simInitialize`/`simLoop`/`simDeinitialize`），`coppeliaSim.bridge` 提供脚本 regular API 的无缝访问，`sim_gui_headless` 支持无界面运行。
- **ZMQ 方式**（路径 B）以 `setStepping(True)` + `sim.step()` 实现外部时钟主导，外部进程跨平台跨语言，是本仓库工业柔性生产仿真的选择。
- 两条路径调用的是**同一套 regular API**；差别在进程模型与同步开销。路径 A 无通信延迟但单进程耦合，路径 B 有跨进程往返但解耦灵活、利于 Sim2Real。
