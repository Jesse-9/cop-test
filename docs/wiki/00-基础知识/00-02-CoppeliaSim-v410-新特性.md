---
title: CoppeliaSim 4.10 新特性与版本兼容
type: concept
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/基础知识
  - 难度/中级
created: 2024-07-13
updated: 2026-07-24
status: published
source:
  - compatibility.htm
  - versionInfo.htm
  - coppeliaSimFeatures.htm
  - scriptObjects.htm
  - scripts.htm
  - zmqRemoteApiOverview.htm
  - wsRemoteApiOverview.htm
related:
  - "[[wiki/00-基础知识/00-01-CoppeliaSim-overview]]"
  - "[[wiki/00-基础知识/00-03-安装与设置]]"
  - "[[wiki/00-基础知识/00-07-兼容性与版本]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
  - "[[wiki/07-API脚本示例/07-01-Lua基本脚本模式]]"
sim_version: "4.10"
---

# CoppeliaSim 4.10 新特性与版本兼容

> 本条目只记录官方手册 `docs/raw/manual/en/` 中可实际查到的 4.x 关键变化与 4.10 版本要点。不再臆造逐版本特性对照表——版本细节以 `versionInfo.htm` 原文为准。

## 版本号规则

手册（`versionInfo.htm`）规定版本号格式为：

```
Version X.Y.Z (rev. W)
```

- **X**：特别版本（数年一次）
- **Y.Z**：常规发布
- **W**：修订号（主要用于 bug 修复）

> 仓库实测版本为 **V4.10.0**（2025-05-14 发布）。手册首页 `welcome.htm` 标注 "Version 4.10"。

## 4.10.0 官方记录的要点

以下摘自 `versionInfo.htm`，仅列与本仓库用法相关的关键条目：

1. **Eigen 线性代数插件**：新增基于 Eigen C++ 的高速向量/数学运算库，状态栏矩阵运算改由 Eigen 插件承担。
2. **WebSockets 插件**：现在同时支持 client 与 server 两种工作模式（见 `wsRemoteApiOverview.htm`）。
3. **事件过滤插件 `simEvents`**：新增事件过滤插件，可在脚本中通过 `simEvents.addProbe(...)` 注册基于逻辑表达式（`and`/`or`/`has`/`eq` 等）的高级事件过滤器。
4. **MIDI I/O 插件**：新增 MIDI 输入/输出插件。
5. **`sysCall_afterLoad` 回调**：新增场景加载完成后触发的回调函数。
6. **MuJoCo armature 默认值修正**：默认 armature 值改回 `0.0`（V4.9 rev.0 曾误设为 `2.0`，V4.9 rev.6 已修正）；用户需自行调整该值以保证仿真稳定。
7. **新增/改进示例场景**：`inverseDynamics.ttt`（串联机械臂逆动力学多种控制方式）、改进的 `motorControllerExamples.ttt`（自定义力/速/位控制器）。

> 4.10 并未引入破坏性脚本/API 大改；真正影响兼容性的结构性变化发生在 **V4.7（script objects）** 和 **V4.8（properties 体系）**，下文单独说明——它们是 4.10 体系的基线。

## V4.7 起的关键结构性变化（4.10 基线）

### 1. Script Objects 体系（取代旧 child/main 二分）

`scriptObjects.htm` / `scripts.htm` 明确：

- **Child scripts 已更名为 simulation scripts（仿真脚本）**。
- **Simulation scripts 与 customization scripts（定制脚本）本身成为场景对象**（script objects），双击即可打开脚本编辑器，可用 `sim.createScript` 创建，或菜单 `[Add > Script]` 添加。
- 脚本类型体系（`scripts.htm`）：
  - **Sandbox**：不绑定任何场景/模型，跨场景常驻。
  - **Add-ons**：位于 `addOns/` 目录，程序启动时自动加载，常驻后台。
  - **Embedded scripts（嵌入脚本）**：随场景保存/加载，包含 main script、simulation scripts、customization scripts。
- 回调函数驱动，可线程化或非线程化执行，遵循固定执行顺序。

### 2. Buffers（缓冲区类型）

V4.7 引入 buffer 以区分二进制数据。兼容性影响（`compatibility.htm`）：

- `sim.getVisionSensorImg` 等返回二进制数据的 API 现返回 **buffer** 而非 string。
- `sim.packTable` 返回的 buffer 长度与旧版不同。
- buffer 与 string 比较恒为 false；需用 `tostring`/`tobuffer` 转换后再比较。
- 可在用户设置文件中用 `useBuffers` 变量关闭 buffer 以兼容旧代码。
- 向后兼容回退：在 `lua/base.lua` 中定义 `tobuffer(a)=a`、`isbuffer(a)=false` 可让旧 Lua 不报错。

### 3. 弃用函数的告警机制

- 旧函数被调用时输出一次告警；`notifyDeprecated` 设置项控制冗长度（0=关闭，1=默认，2=详尽）。

## V4.8 起的关键变化（4.10 基线）

`versionInfo.htm`：

1. **Properties 体系**：所有内部变量/状态统一为 property，可通过专用 API 查询/修改；多数 property 修改会触发可监听事件。**Signals、custom data、引擎参数、named parameters 均已并入 properties 框架。**
2. **Property explorer add-on**：`[Modules > Developer tools > Property explorer]` 可检视/修改所有已知 property。
3. **事件协议变更**：转发至 `sysCall_event` 的事件改用新消息协议，依赖事件的旧代码需调整。
4. **TOPPRA 集成**：时间最优路径参数化（`sim.generateTimeOptimalTrajectory`）内置，不再需要 Docker 容器。
5. 新增 `sim.systemSemaphore`、`sim.scheduleExecution`/`sim.cancelScheduledExecution`、app properties `processId`/`processCnt`。

## 4.10 体系基线：5 大编程方式与 7 语言

`coppeliaSimFeatures.htm` 列出 5 种相互兼容的编程方式：

| 编程方式 | 支持语言 | 说明 |
|---------|---------|------|
| **Regular API** | Python、Lua、C/C++ | 脚本内直接调用，单位：米/千克/秒/弧度（见 `apiFunctions.htm`） |
| **Remote API** | C/C++、Python、Java、JavaScript、Matlab & Octave | 外部进程远程控制仿真/仿真器本身 |
| **ROS interfaces** | — | 发布/订阅/服务调用，支持标准消息，可扩展 |
| 插件 | C/C++ | 底层扩展 |
| 嵌入脚本 | Lua/Python | 场景对象级控制 |

> ⚠ 重要纠正：旧条目称 Regular API "只有 Lua" 是错的。`apiFunctions.htm` 每个函数均给出 Python/Lua/C 三栏签名。

### 5 个物理引擎

`coppeliaSimFeatures.htm` 明确支持 5 个引擎：**MuJoCo、Bullet Physics、ODE、Vortex、Newton Dynamics**。软体（soft body/string/rope/cloth）通过 MuJoCo 支持。

## ZeroMQ / WebSocket Remote API（本仓库核心）

`zmqRemoteApiOverview.htm`：

- ZMQ Remote API 暴露脚本可用的全部 API（`sim.*` 及插件命名空间 `simOMPL.*`/`simUI.*`/`simIK.*` 等），通过 ZMQ + 接口插件 + ZMQ remote API **add-on** 实现。
- 支持 **stepping（与每步同步）** 与 **non-stepping（常规模式）**，亦支持远程控制仿真器本身（加载场景、启停仿真）。
- Python 客户端安装：
  ```bash
  python3 -m pip install coppeliasim-zmqremoteapi-client
  ```
- 最小 stepping 示例（本仓库 `factory_core.py` 的时钟主控模式即基于此）：
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
- 支持客户端：Python、C++、Java、Matlab/Octave、Lua（仅脚本内，用于多实例互联）、Rust。
- WebSocket Remote API（`wsRemoteApiOverview.htm`）面向 JavaScript/远程 client/server，默认端口 23050，是 ZMQ API 的薄封装。

> 本仓库运行前提之一即 ZMQ remote API server add-on 已激活（菜单 `Add-ons > ZMQ remote API server`，默认端口 23000）。详见 [[wiki/00-基础知识/00-03-安装与设置]] 与 [[wiki/98-ProjectUsage/MCP_USAGE]]。

## 兼容性与迁移要点

`compatibility.htm` 给出的官方迁移指引：

### Script objects 的前向/后向兼容
- **前向**：旧 associated scripts 仍可运行，但建议转为 script objects；自动转换不完美，可能需手动调整。
- **后向**：含 script objects 的场景/模型可通过设置 `scriptConversion = -1` 转回仅含 associated scripts 的形式，但脚本代码本身不会被自动转换，需手动调整。
- **严重异常回退**：若 script objects 出严重问题，可设 `useSceneObjectScripts = false` 退回旧 associated scripts。

### Buffers 的前向/后向兼容
- 见上文 V4.7 部分；旧 Lua 可用 `tostring`/`tobuffer` 或在 `lua/base.lua` 补回退函数。

## 已废弃（deprecated）的旧特性

依手册与 `versionInfo.htm` 记载：

- **`sim.setThreadAutomaticSwitch`** → 改用 `sim.setStepping`（V4.6 起）。
- **`sim.switchThread`** → 改用 `sim.step`（V4.6 起）。
- **旧版 Remote API（legacy XML-RPC 风格）** → 仍存在（`legacyRemoteApiOverview.htm`），但官方推荐迁移到 ZMQ Remote API。
- **V4.7 起 child scripts** → 更名为 simulation scripts。
- **MuJoCo composite 类型（rope/cube/cylinder/ellipsoid）** → V4.9 起废弃（上游 MuJoCo 支持不确定），改用 `simMujoco.addFlexcomp()` 的 grid-type flexcomp。

## 参见

- [[wiki/00-基础知识/00-01-CoppeliaSim-overview]] — 平台总览
- [[wiki/00-基础知识/00-03-安装与设置]] — 安装、命令行、add-on 激活
- [[wiki/00-基础知识/00-07-兼容性与版本]] — 兼容性专题（待建）
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 概览与三语言签名
- [[wiki/01-API参考/01-02-Remote-API-ZMQ]] — ZMQ Remote API 详解
- [[wiki/07-API脚本示例/07-01-Lua基本脚本模式]] — 脚本回调模式
