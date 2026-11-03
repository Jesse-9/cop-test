---
title: B0 与 Legacy 远程 API
type: reference
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/协同仿真
  - 难度/中级
created: 2026-07-24
updated: 2026-07-24
status: published
source:
  - b0RemoteApiOverview.htm
  - b0RemoteApiModusOperandi.htm
  - b0RemoteApiClientSide.htm
  - b0RemoteApiServerSide.htm
  - legacyRemoteApiOverview.htm
  - remoteApiOverview.htm
  - remoteApiModusOperandi.htm
related:
  - "[[wiki/01-API参考/01-02-Remote-API-ZMQ]]"
  - "[[wiki/05-数字孪生与协同仿真/05-03-Python客户端集成]]"
  - "[[wiki/00-基础知识/00-07-兼容性与版本]]"
  - "[[wiki/01-API参考/01-04-插件API总览]]"
sim_version: "4.10"
---

# B0 与 Legacy 远程 API

> CoppeliaSim 历史上出现过两套现已弃用的远程 API：基于 **BlueZero 中间件** 的 **b0 remote API**，以及更早的、基于 `simx*` 函数与 socket 通信的 **legacy remote API**。4.10 官方推荐统一使用 [[wiki/01-API参考/01-02-Remote-API-ZMQ|ZeroMQ remote API]]，两者仅作为向后兼容保留。

## 背景：三套远程 API 的演进

CoppeliaSim 的"远程 API"指让外部进程（甚至远程机器）控制仿真或仿真器本身的接口。它历经三代：

1. **Legacy remote API**（`simx*` 函数 + 原始 socket / 共享内存）——最早期方案，自 V4.4 起标记 **deprecated**。
2. **b0 / BlueZero remote API**（`simx*` 函数 + BlueZero 中间件，MessagePack 序列化）——曾作为高性能替代方案推出，现已 **deprecated**（BlueZero 中间件本身被弃用）。
3. **ZeroMQ remote API**（基于 ZMQ + MessagePack，支持 Python/C++/Matlab/Octave/Java/Lua/Rust；另有 WebSocket 变体支持 JS）——4.10 的现行方案，本仓库 [[../01-API参考/01-02-Remote-API-ZMQ|Remote-API-ZMQ]] 与 [[05-03-Python客户端集成|Python客户端集成]] 即基于此。

> ⚠ 重要纠正：legacy 与 b0 **都**使用 `simx` 前缀的函数名，但底层传输与调用约定不同。不要把"simx 前缀"等同于"legacy"——b0 的函数也是 `simx*`。区分它们靠中间件（socket vs BlueZero），而非函数名前缀。

## Legacy remote API

### 机制概要

- **客户端**：C/C++、Python、Java、Matlab、Octave 五种语言绑定。
- **服务端**：CoppeliaSim 插件 `simExtRemoteApi`（`simExtRemoteApi.dll` / `libsimExtRemoteApi.dylib` / `libsimExtRemoteApi.so`），默认随 CoppeliaSim 加载。
- **传输**：socket 通信（可选共享内存）；客户端至少有主线程 + 通信线程两条线程，可调用多次 `simxStart` 建立多条通信线。
- **函数集**：约一百个特定 `simx*` 函数 + 一个通用函数 `simxCallScriptFunction`。
- **单位**：米、千克、秒、弧度。

### 调用约定（与 Regular API 的两大差异）

1. 大多数函数返回一个**位编码的返回码**（bit-coded return code），需按位测试才能正确解读，而非简单的成功/失败。
2. 大多数函数需要两个额外参数：**clientID**（`simxStart` 返回的连接标识）和 **operation mode**（操作模式）。

### 操作模式

| 模式 | 阻塞? | 典型用途 |
|------|-------|----------|
| `simx_opmode_oneshot` | 非阻塞 | set 类函数，不关心返回值（如 `simxSetJointPosition`） |
| `simx_opmode_blocking` | 阻塞 | get 类函数且需立即得到回复（如 `simxGetObjectHandle`） |
| `simx_opmode_streaming` | 非阻塞 | 订阅服务端持续推送的值（如 `simxGetJointPosition`） |
| `simx_opmode_oneshot_split` | 非阻塞 | 大数据量 set（如 `simxSetVisionSensorImage`），分块发送，**不推荐** |
| `simx_opmode_streaming_split` | 非阻塞 | 大数据量 get 持续流，分块，**不推荐** |
| `simx_opmode_discontinue` | 非阻塞 | 取消某条 streaming 订阅，释放服务端/客户端缓冲 |
| `simx_opmode_buffer` | 非阻塞 | 从本地 inbox 读取 streaming 推送过来的最新值 |
| `simx_opmode_remove` | 非阻塞 | 清除本地缓冲中的某条回复，释放客户端内存 |

### Stepping（同步）模式

默认异步：仿真自顾自推进。若需客户端逐帧触发，启用 stepping 模式：

```python
# legacy remote API 示例（已弃用，仅作历史参考；4.10 请用 ZMQ）
simxSynchronous(clientID, True)          # 开启 stepping（阻塞调用）
simxStartSimulation(clientID, simx_opmode_oneshot)
simxSynchronousTrigger(clientID)         # 触发下一仿真步（阻塞）
simxSynchronousTrigger(clientID)         # 再触发一步
# ...
simxGetPingTime(clientID)               # 阻塞至当前步计算完成，确保读到本步数据
```

> 该 stepping 模式需服务端预先启用 stepping 服务（`simRemoteApi.start` 或 `remoteApiConnections.txt` 配置）。在 `simxSynchronousTrigger` 返回后，下一仿真步可能尚未算完——用一次阻塞调用（如 `simxGetPingTime`）来确保读到正确步的数据。

### 其他要点

- **暂停通信线程打包发送**：`simxPauseCommunication(clientID, 1)` 暂时挂起通信线程，期间多个 set 调用会在服务端一次性生效（同一仿真步内一起应用，例如机器人三关节同步设置），结束后 `simxPauseCommunication(clientID, 0)`。
- **streaming 必须显式停止**：启用 streaming 的命令在不用时必须用 `simx_opmode_discontinue` 取消，否则服务端会持续推送无用数据拖慢仿真。

## B0 / BlueZero remote API

### 机制概要

- **客户端**：C++、Python、Java、Matlab、Lua 五种语言绑定（其它语言可自建绑定）。
- **服务端**：基于 Lua 脚本 `lua/b0RemoteApiServer.lua` + BlueZero 插件 `simExtB0`（`simExtB0.dll` / `libsimExtB0.dylib` / `libsimExtB0.so`）。该脚本既被 add-on `simAddOnB0RemoteApiServer.lua` 使用（所有场景可用），也被模型 `Models/tools/B0 remote Api server.ttm` 的 customization script 使用（单场景可用）。
- **中间件**：BlueZero（基于 ZeroMQ + Boost 等构建的发布/订阅中间件）；序列化用 MessagePack。
- **函数集**：约一百个特定 `simx*` 函数 + 一个通用函数。
- **客户端依赖**：以 Python 为例，需 `pip install msgpack`、`b0RemoteApi.py`、`b0.py` 以及 BlueZero 库（`b0.dll`）及其依赖（`libzmq`、`boost_chrono`、`boost_system`、`boost_thread` 等）。

### 调用约定（topic 机制）

b0 的核心创新是：大多数函数需要一个额外参数 **topic / 通信通道**，决定该次调用以何种模式执行。topic 由以下五个函数之一返回：

| topic 构造函数 | 行为 | 典型用法 |
|----------------|------|----------|
| `simxServiceCall` | 阻塞：命令发到服务端执行后返回响应 | 一次性取值（如 `simxGetObjectHandle`） |
| `simxDefaultPublisher` | 非阻塞：发出即返回，不等回复 | 单次 set 命令（如 `simxSetJointPosition`） |
| `simxDefaultSubscriber` | 服务端持续执行该命令并持续推送，客户端在回调里收 | 持续取值（如 `simxGetJointForce`）；回调经 `simxSpinOnce` 触发 |
| `simxCreatePublisher` | 类似 default publisher，但开辟独占发布通道 | 重数据 set（如 `simxSetVisionSensorImage`） |
| `simxCreateSubscriber` | 类似 default subscriber，但开辟独占订阅通道 | 重数据 get（如 `simxGetVisionSensorImage`） |

### Stepping 模式（Python 示例，已弃用）

```python
# b0 remote API 示例（已弃用，仅作历史参考；4.10 请用 ZMQ）
import b0RemoteApi
import time

with b0RemoteApi.RemoteApiClient('b0RemoteApi_pythonClient', 'b0RemoteApi') as client:
    doNextStep = True

    def simulationStepStarted(msg):
        simTime = msg[1][b'simulationTime']
        print('Simulation step started. Simulation time:', simTime)

    def simulationStepDone(msg):
        simTime = msg[1][b'simulationTime']
        print('Simulation step done. Simulation time:', simTime)
        global doNextStep
        doNextStep = True

    client.simxSynchronous(True)
    client.simxGetSimulationStepStarted(client.simxDefaultSubscriber(simulationStepStarted))
    client.simxGetSimulationStepDone(client.simxDefaultSubscriber(simulationStepDone))
    client.simxStartSimulation(client.simxDefaultPublisher())

    startTime = time.time()
    while time.time() < startTime + 5:
        if doNextStep:
            doNextStep = False
            client.simxSynchronousTrigger()
        client.simxSpinOnce()

    client.simxStopSimulation(client.simxDefaultPublisher())
```

> 注意 b0 的 stepping 约定与 legacy 不同：通过订阅 `simxGetSimulationStepStarted` / `simxGetSimulationStepDone` 两个流，在回调中推进 `doNextStep`，并用 `simxSynchronousTrigger` 触发下一步、`simxSpinOnce` 处理到达的响应。

## 三者对比

| 维度 | Legacy remote API | b0 / BlueZero remote API | ZeroMQ remote API（现行） |
|------|-------------------|--------------------------|---------------------------|
| 传输 | 原始 socket（可选共享内存） | BlueZero 中间件（ZMQ + Boost 上构建） | ZeroMQ（另有 WebSocket 变体支持 JS） |
| 序列化 | 自定义二进制 | MessagePack | MessagePack（更现代的 msgpack 客户端） |
| 调用模型 | clientID + operation mode（8 种），返回位码 | topic（5 种），回调式订阅 | 同步 RPC 为主，`sim.setStepping(True)` + `sim.step()` 逐帧推进 |
| 同步方式 | `simxSynchronous` + `simxSynchronousTrigger` | `simxSynchronous` + `simxSynchronousTrigger` + 订阅 step 回调 | `sim.setStepping(True)` + `sim.step()`（见 [[../01-API参考/01-02-Remote-API-ZMQ]]） |
| 语言绑定 | C/C++、Python、Java、Matlab、Octave | C++、Python、Java、Matlab、Lua | Python、C++、Matlab、Octave、Java、Lua、Rust（+ JS via WebSocket） |
| 服务端插件 | `simExtRemoteApi`（默认加载） | `simExtB0` + `b0RemoteApiServer.lua` | ZMQ remote API add-on（菜单 Add-ons > ZMQ remote API server） |
| 4.10 现状 | **deprecated**（V4.4 起） | **deprecated**（BlueZero 弃用） | **推荐**，现行方案 |
| 客户端依赖 | 较轻 | 较重（BlueZero + Boost 全家桶 + msgpack） | 轻（`coppeliasim_zmqremoteapi_client`，纯 Python，含 msgpack） |

## 迁移要点（legacy/b0 → ZMQ）

1. **抛弃 `simx*` 与 clientID / operation mode / topic 心智模型**：ZMQ remote API 把 Regular API 直接暴露给外部客户端——函数名与脚本内的 Regular API 一致（`sim.getObject`、`sim.setJointPosition`、`sim.getSimulationTime`…），无需 clientID、无需 operation mode。
2. **同步语义改用 `setStepping` + `step`**：替代 `simxSynchronous` / `simxSynchronousTrigger`，写法更直观、与脚本内 stepping 一致。详见 [[../01-API参考/01-02-Remote-API-ZMQ]] 与本仓库 v1 运行器的 clock-master 实现。
3. **返回值不再是位码**：ZMQ 调用直接返回数据或抛异常，无需位测试；错误处理走 Python 异常，配合本仓库 [[../01-API参考/01-04-插件API总览|插件API总览]] 提到的统一异常处理层。
4. **streaming / subscriber 模式消失**：需要持续取值时，在 stepping 循环里按节拍调用对应 get 函数即可（Python 端是 clock master，节拍由 `sim.step()` 主导）。
5. **客户端依赖大幅简化**：Python 端只需 `pip install coppeliasim-zmqremoteapi-client`，不再需要 BlueZero / Boost / msgpack 单独配置。
6. **服务端启用方式**：ZMQ 不再需要加载额外 .dll 插件——通过菜单 Add-ons > ZMQ remote API server 激活即可（默认端口 23000），见 [[../05-数字孪生与协同仿真/05-03-Python客户端集成|Python客户端集成]]。
7. **大块数据传输**：legacy 的 `*_split` 模式、b0 的 dedicated publisher 在 ZMQ 中由 msgpack + ZMQ 帧自动处理，无需手动分块。

> 本仓库（cop-test）即采用 ZMQ remote API + Python 端 clock master 架构；legacy/b0 的代码仅留作历史参考，**不要在新代码中使用**。

## 参见
- [[wiki/01-API参考/01-02-Remote-API-ZMQ]] — 现行 ZMQ remote API 详解
- [[wiki/05-数字孪生与协同仿真/05-03-Python客户端集成]] — Python ZMQ 客户端集成实践
- [[wiki/01-API参考/01-04-插件API总览]] — 插件体系（legacy/b0 服务端即以插件形式实现）
- [[wiki/00-基础知识/00-07-兼容性与版本]] — 版本兼容性与弃用时间线（待建）
