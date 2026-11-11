---
title: ZeroMQ Remote API
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
  - zmqRemoteApiOverview.htm
  - wsRemoteApiOverview.htm
  - remoteApiOverview.htm
  - b0RemoteApiOverview.htm
  - pythonClient.htm
related:
  - "[[wiki/00-基础知识/00-01-CoppeliaSim-overview]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
  - "[[wiki/01-API参考/01-04-插件API总览]]"
  - "[[wiki/05-数字孪生与协同仿真/05-03-Python客户端集成]]"
  - "[[wiki/05-数字孪生与协同仿真/05-05-B0与Legacy远程API]]"
  - "[[wiki/06-工业案例/06-02-岛式车间柔性生产案例]]"
sim_version: "4.10"
---

# ZeroMQ Remote API

## 定位

ZeroMQ Remote API 是 CoppeliaSim 与外部应用通信的方式之一，跨平台，允许**外部应用或远程硬件**（真实机器人、远程计算机等）控制仿真或仿真器本身。它对外暴露**与脚本内完全相同的 API**：

- **全部 Regular API 函数**（`sim.*`）
- **已启用的插件 API**（`simOMPL.*`、`simUI.*`、`simIK.*` 等）

通信经由 [ZeroMQ](https://zeromq.org/) 库、其接口插件（`simZMQ`）及 **ZMQ remote API add-on** 完成，对用户透明。外部应用可以 stepping（与每个仿真步同步）或 non-stepping（常规模式）方式与 CoppeliaSim 交互，并支持远程控制仿真器本身（远程加载场景、启动/暂停/停止仿真）。

> WebSocket Remote API 是 JavaScript 对应变体（见下文），与 ZMQ Remote API 并列、能力等价。

## 架构

```
┌──────────────────┐   ZeroMQ REQ/REP + CBOR   ┌──────────────────┐
│  外部程序         │ ◄════════════════════► │  CoppeliaSim     │
│  (客户端)         │   tcp://localhost:23000 │  (仿真端)        │
│  Python/C++/...  │                        │  simZMQ 插件     │
│                  │                        │  + ZMQ add-on    │
└──────────────────┘                        └──────────────────┘
```

- 传输：ZeroMQ
- 序列化：[CBOR](https://cbor.io/)（Python 客户端安装时自动带入 CBOR 依赖）
- 默认端口：`tcp://localhost:23000`（CoppeliaSim 端 ZMQ remote API add-on 监听）

> 协议字段层面的细节（请求/响应具体消息结构）官方手册未在 `zmqRemoteApiOverview.htm` 中公开描述，需要时请查阅客户端仓库源码，勿沿用网络二手描述。

## stepping 与 non-stepping 模式

| 模式 | 时钟主导 | 适用 |
|------|---------|------|
| **non-stepping** | CoppeliaSim 自行推进 | 常规操作、被动观测 |
| **stepping** | **外部应用**（clock master） | 精确节拍、工业时序、强化学习 |

启用 stepping：`sim.setStepping(True)` 后，外部应用每次调 `sim.step()` 推进一个仿真步；外部应用不调，仿真即停。这是本仓库"Python 端始终是 clock master"的依据，详见 [[06-工业案例/06-02-岛式车间柔性生产案例]]。

## Python 客户端

### 安装

```bash
python3 -m pip install coppeliasim-zmqremoteapi-client
# ZeroMQ 与 CBOR 依赖会自动安装
```

### 连接与命名空间获取

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()              # 默认 host='localhost', port=23000
sim = client.require('sim')              # 取得 sim 命名空间
```

> ⚠ 取命名空间用 `client.require('sim')`（官方手册 ZMQ 页即用此写法）。旧版 legacy remote API 示例里出现的 `client.getobject('sim')` 是 deprecated legacy 的写法，勿与 ZMQ 客户端混用。`sim.getObject(path)` 才是按路径取场景对象句柄（如 `sim.getObject('/Floor')`），二者职责不同。本仓库 v1 运行器统一使用 `require`。

### 连接参数

```python
client = RemoteAPIClient(host='localhost', port=23000)   # 远程时改 host
```

### 典型 stepping 示例（运行 3 秒，手册原文）

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

sim.setStepping(True)             # 启用单步 → 客户端成为时钟主导
sim.startSimulation()
while (t := sim.getSimulationTime()) < 3:
    print(f'Simulation time: {t:.2f} [s]')
    sim.step()                    # 推进一个仿真步
sim.stopSimulation()
```

### 视觉传感器图像往返（手册示例）

```python
from time import sleep
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')
sensor1Handle = sim.getObject('/VisionSensor')
sensor2Handle = sim.getObject('/PassiveVisionSensor')

sim.startSimulation()
while sim.getSimulationTime() < 10:
    image, resolution = sim.getVisionSensorImg(sensor1Handle)
    sim.setVisionSensorImg(sensor2Handle, image)
    sleep(0.01)
sim.stopSimulation()
```

## 多命名空间

ZMQ Remote API 暴露全部 `sim.*` 及已启用插件 API。取命名空间一律走 `require`：

```python
sim = client.require('sim')            # Regular API
simIK = client.require('simIK')        # 运动学插件
simOMPL = client.require('simOMPL')    # 运动规划插件
```

> C++/Java 客户端命名空间获取语法不同（见下文多语言表），手册原文为 `client.getObject().sim()`。

## 远程控制仿真器本身

ZMQ Remote API 不仅能控制仿真内容，还能控制仿真器进程：远程 `loadScene`、`startSimulation`、`pause`、`stopSimulation` 等。本仓库 v1 由 `flexible-factory` 从场景包加载 `scene.ttt`，再远程启停仿真。

## asyncio 客户端

官方手册 `zmqRemoteApiOverview.htm` 未涉及 asyncio 变体；`coppeliasim-zmqremoteapi-client` 包提供 `coppeliasim_zmqremoteapi_client.asyncio` 子模块，所有方法为 async，可用 `asyncio.gather` 并发批量发送。需使用时请查阅客户端包仓库的示例，本条目不展开。

## 多语言客户端

| 语言 | 取命名空间 | 备注 |
|------|-----------|------|
| **Python** | `sim = client.require('sim')` | `pip install coppeliasim-zmqremoteapi-client` |
| **C++** | `auto sim = client.getObject().sim();` | 需 `cppzmq` + `jsoncons` + boost/ZMQ，cmake 自动下载 |
| **Java** | `var sim = client.getObject().sim();` | 需 Maven；`GENERATE_INCLUDE_OBJECTS=sim,simIK` 指定命名空间 |
| **Matlab/Octave** | `sim = client.require('sim');` | Matlab 用内置 JeroMQ；Octave 6.4+ 需 communications + zeromq 包 |
| **Lua** | `simx = remoteApiClient.getObject('sim')` | 仅限从 CoppeliaSim 脚本内连接另一实例（如同步两实例） |
| **Rust** | `let sim = Sim::new(client.clone());` | 社区维护，`client.set_stepping(true)?` 等 snake_case |
| **JavaScript** | `var sim = await client.require('sim');` | 走 WebSocket Remote API（端口 23050，`json` 协议） |

C++ 单步示例（手册原文）：

```cpp
#include "RemoteAPIClient.h"
int main(int argc, char* argv[])
{
    RemoteAPIClient client;
    auto sim = client.getObject().sim();
    sim.setStepping(true);
    sim.startSimulation();
    double t = 0.0;
    do {
        t = sim.getSimulationTime();
        printf("Simulation time: %.2f [s]\n", t);
        sim.step();
    } while (t < 3.0);
    sim.stopSimulation();
    return(0);
}
```

Lua 跨实例同步示例（手册原文，同步两个 CoppeliaSim 实例的仿真步）：

```lua
function sysCall_init()
    remoteApiClient = require('luaZmqRemoteApi')
    remoteApiClient.init('127.0.0.1', 23002)
    simx = remoteApiClient.getObject('sim')
    remoteApiClient.setStepping(true)
    simx.startSimulation()
end
function sysCall_sensing()
    remoteApiClient.step()
end
function sysCall_cleanup()
    simx.stopSimulation()
    remoteApiClient.cleanup()
end
```

## WebSocket Remote API（JS 变体）

WebSocket Remote API 面向 JavaScript/浏览器客户端，经由 WebSocket、其接口插件及 WS remote API add-on 完成，能力与 ZMQ 变体等价（全部 `sim.*` + 插件 API），同样支持 stepping / non-stepping 与远程控制仿真器。默认端口 23050，消息可用 `json` 协议：

```javascript
var client = new RemoteAPIClient('localhost', 23050, 'json');
await client.websocket.open();
var sim = await client.require('sim');
var [h] = await sim.getObject('/Floor');
```

## 与 Legacy Remote API / B0 的关系

- **Legacy Remote API**（基于 XML-RPC）：**已废弃**，仅为向后兼容保留，请勿用于新项目。手册原文在 `remoteApiOverview.htm` 末尾标注 "deprecated legacy remote API"。
- **B0-based Remote API**（基于 BlueZero 中间件）：**已弃用**，手册原文 `b0RemoteApiOverview.htm` 首句即 "BlueZero is deprecated. We highly recommend using the ZeroMQ remote API instead."

详见 [[05-数字孪生与协同仿真/05-05-B0与Legacy远程API]]（待建）。

## 双连接与并发（本仓库实践）

单个 ZMQ 连接是**同步独占**的：一次请求-回复期间该连接被占用。仿真主循环持续调用 `sim.step()` 时，若诊断工具共用同一连接会被阻塞、甚至误判为断连。

v1 产品由单个 `flexible-factory` 进程持有 ZMQ 连接；`cli → application →
simulation` 分层中只有运行时层直接调用 Remote API。MCP 不在 v1 范围，旧的
双连接服务器实现已删除。运行方式见 [[wiki/98-ProjectUsage/USAGE]]。

## 常见问题

1. **连接失败** — 确认 CoppeliaSim 运行中且 23000 端口未被占用；检查 Add-ons > ZMQ remote API server 是否激活。
2. **函数找不到** — 确认函数名在 Regular API 中存在；极少数脚本专用函数不在 Remote API 暴露。
3. **性能慢** — 避免逐帧频繁往返；尽量在脚本端批量处理。
4. **通信冻结** — 多份模型自带 Lua 脚本在 4.10 ZMQ addOn 层争用会致冻结（本仓库已知问题，见仓库 CLAUDE.md "完整场景启动冻结"），剥离模型脚本可缓解。

---

**关联条目：**
- [[wiki/00-基础知识/00-01-CoppeliaSim-overview]] — CoppeliaSim 总览
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 详解（同一套 `sim.*`）
- [[wiki/01-API参考/01-04-插件API总览]] — 插件 API（simIK/simOMPL/simUI 等）
- [[wiki/05-数字孪生与协同仿真/05-03-Python客户端集成]] — Python 客户端集成（待建）
- [[wiki/05-数字孪生与协同仿真/05-05-B0与Legacy远程API]] — B0 与 Legacy 远程 API（待建）
- [[wiki/98-ProjectUsage/USAGE]] — v1 项目使用指南
- [[wiki/06-工业案例/06-02-岛式车间柔性生产案例]] — 本仓库实战（stepping + 单连接）
