---
title: ZeroMQ Remote API 使用指南
type: reference
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/中级
created: 2024-07-13
updated: 2026-07-21
status: published
source: src-doc-CoppeliaSim-zmq-remote-api.md, src-doc-CoppeliaSim-zmq-remote-api-protocol.md, src-doc-CoppeliaSim-zmq-remote-api-python-client.md, src-doc-CoppeliaSim-messaging-communication.md, src-doc-CoppeliaSim-simulation.md
related:
  - [[wiki/01-API参考/01-01-Regular-API-overview]]
  - [[wiki/06-工业案例/06-01-UR5-抓取与放置案例]]
  - [[wiki/06-工业案例/06-02-岛式车间柔性生产案例]]
sim_version: "4.10"
---

# ZeroMQ Remote API 使用指南

## 概述

Remote API 是 CoppeliaSim 与外部应用（独立进程或远程机器）通信的方式之一，跨平台，且**支持与脚本内完全相同的 API 调用**。它有两个并行框架：

| 框架 | 客户端语言 | 用途 |
|------|-----------|------|
| **ZeroMQ Remote API** | Python、C++、Matlab、Octave、Java、Lua、Rust | 通用外部控制（**推荐**） |
| **WebSocket Remote API** | JavaScript | 浏览器/Web 客户端 |

> 旧版 Legacy Remote API（基于 XML-RPC）已**废弃**，仅为向后兼容保留，请勿用于新项目。

ZeroMQ Remote API 通过 [ZeroMQ](http://api.zeromq.org/) 库及其接入插件 `simZMQ`、配合 ZMQ Remote API add-on 实现，对用户完全透明。它可让一个或多个外部应用以 **stepping（与每步同步）** 或 **non-stepping（常规模式）** 方式与 CoppeliaSim 交互，并支持远程控制仿真器本身（远程加载场景、启停仿真等）。

## 架构

```
┌──────────────────┐   ZMQ REQ/REP + CBOR   ┌──────────────────┐
│  外部程序         │ ◄════════════════════► │  CoppeliaSim     │
│  (客户端)         │   tcp://localhost:23000│  (仿真端)        │
│  Python/C++/...  │                        │  simZMQ 插件     │
│                  │                        │  + ZMQ add-on    │
└──────────────────┘                        └──────────────────┘
```

## 协议（Protocol v2）

客户端连接一个 [`REQ` socket](https://zeromq.org/socket-api/#req-socket) 到端点（默认服务端监听 `tcp://*:23000`），发送请求消息、读取响应消息。消息内容用 [CBOR](https://cbor.io) 编解码（下述字段以 JSON 仅为示意）。

### 请求 (Request)
| 字段 | 类型 | 说明 |
|------|------|------|
| `func` | string | 要调用的函数名；或 `executed`（当 CoppeliaSim 回调/命令已执行） |
| `args` | array | 函数实参；或 CoppeliaSim 回调的返回值 |
| `uuid` | string | 客户端唯一标识 |
| `ver` | int | 协议版本（当前为 **2**），首次联络后可省略 |
| `lang` | string | 客户端语言（非必需） |
| `timeout` | int | 客户端超时秒数（默认十分钟），首次联络后忽略 |
| `argsL` | int | args 大小提示（因 None/NULL 被 Lua 忽略而有用） |

```json
{ "func": "sim.getObject", "args": ["/Floor"],
  "uuid": "c06b3832-...", "ver": 2, "lang": "python", "timeout": 60, "argsL": 1 }
```

### 响应 (Response)
| 字段 | 类型 | 说明 |
|------|------|------|
| `err` | string | 出错时的错误消息 |
| `func` | string | 可能的等待请求（`wait`）或要执行的回调函数名 |
| `ret` | array | 前次函数调用的返回值（当无 `err`/`func` 时） |

成功：`{ "ret": [37] }`；失败：`{ "err": "Object does not exist. (in function 'sim.getObject')" }`

### 协议版本与 CoppeliaSim 版本对应
| 协议版本 | CoppeliaSim 版本 |
|----------|-----------------|
| 1 | 4.5.1 及更早 |
| **2** | **4.6.0 或更高**（本仓库 4.10 即用 v2） |

> pip 包从 2.x 起，主版本号与协议版本一致：`coppeliasim-zmqremoteapi-client` 2.x 对应协议 v2。详见 `raw/src-doc-CoppeliaSim-zmq-remote-api-protocol.md`。

## 安装

### 服务端（CoppeliaSim 端）
CoppeliaSim 4.1+ 内置 ZMQ 支持，无需额外安装。确认 `simZMQ` 插件与 ZMQ Remote API add-on 已加载：菜单 **Add-ons > ZMQ remote API server**（默认端口 23000）。

### 客户端（Python）
```bash
python3 -m pip install coppeliasim-zmqremoteapi-client
# ZeroMQ 与 CBOR 依赖会自动安装
```

## 基本用法 (Python)

### 连接与命名空间获取
```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()        # 默认 host='localhost', port=23000
sim = client.require('sim')       # 取得 sim 命名空间
```
> ⚠ **修正**：取命名空间用 `client.require('sim')`（GitHub 官方客户端 README 与手册 ZMQ 页均用此）。旧写法 `client.getObject('sim')` 在部分版本也兼容，但 `getObject` 的正名职责是**按路径取场景对象句柄**（如 `sim.getObject('/Floor')`），二者勿混。本仓库 `factory_core.py` 已统一使用 `require`。

### 连接参数
```python
client = RemoteAPIClient(host='localhost', port=23000)   # 远程时改 host
```

### 单步（stepping）仿真——手册官方示例
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
> 本仓库的"Python 端始终是 clock master"即基于此模式，详见 [[01-01-Regular-API-overview]] 的"仿真循环与单步模式"与 [[06-02-岛式车间柔性生产案例]]。

### 视觉传感器图像往返示例（手册）
```python
from time import sleep
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')
s1 = sim.getObject('/VisionSensor')
s2 = sim.getObject('/PassiveVisionSensor')

sim.startSimulation()
while sim.getSimulationTime() < 10:
    image, res = sim.getVisionSensorImg(s1)
    sim.setVisionSensorImg(s2, image)
    sleep(0.01)
sim.stopSimulation()
```

## asyncio 客户端（并发批处理）

客户端提供 asyncio 版本，所有方法均为 async。**性能要点**：一批命令若结果稍后才用，应使用 `asyncio.gather` 并发发送以提升吞吐：

```python
from coppeliasim_zmqremoteapi_client.asyncio import *
import asyncio

async def main():
    async with RemoteAPIClient() as client:
        sim = await client.require('sim')
        h = await sim.getObject('/Floor')
        print(h)

asyncio.run(main())
```

批量取 100 个对象句柄：
```python
handles = await asyncio.gather(*[sim.getObject(f'/Object{i+1}') for i in range(100)])
```

> ⚠ **Windows**：若 `asyncio.run(...)` 不正常，在其之前调用：
> `asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())`

## 多语言客户端

| 语言 | 取命名空间 | 备注 |
|------|-----------|------|
| **Python** | `client.require('sim')` | `pip install coppeliasim-zmqremoteapi-client` |
| **C++** | `auto sim = client.getObject().sim();` | 需 `cppzmq` + `jsoncons` + boost/ZMQ，cmake 自动下载 |
| **Java** | `var sim = client.getObject().sim();` | 需 Maven；`GENERATE_INCLUDE_OBJECTS=sim,simIK` 指定命名空间 |
| **Matlab/Octave** | `sim = client.require('sim');` | Matlab 用内置 JeroMQ；Octave 6.4+ 需 communications + zeromq 包 |
| **Lua** | `simx = remoteApiClient.getObject('sim')` | 仅限从 CoppeliaSim 脚本内连接另一实例（如同步两实例） |
| **Rust** | `let sim = Sim::new(client.clone());` | 社区维护，`client.set_stepping(true)?` 等 snake_case |

C++ 单步示例：
```cpp
#include "RemoteAPIClient.h"
int main(int argc, char* argv[]) {
    RemoteAPIClient client;
    auto sim = client.getObject().sim();
    sim.setStepping(true);
    sim.startSimulation();
    double t = 0.0;
    do { t = sim.getSimulationTime(); printf("%.2f [s]\n", t); sim.step(); } while (t < 3.0);
    sim.stopSimulation();
    return 0;
}
```

Lua 跨实例同步示例：
```lua
function sysCall_init()
    remoteApiClient = require('luaZmqRemoteApi')
    remoteApiClient.init('127.0.0.1', 23002)
    simx = remoteApiClient.getObject('sim')
    remoteApiClient.setStepping(true)
    simx.startSimulation()
end
function sysCall_sensing() remoteApiClient.step() end
function sysCall_cleanup() simx.stopSimulation(); remoteApiClient.cleanup() end
```

## 可调用的函数范围

ZeroMQ Remote API 暴露**全部 Regular API 函数**（`sim.*`）以及**已启用的插件 API**（`simOMPL.*`/`simUI.*`/`simIK.*` 等）。脚本端需暴露的"被远程调用的函数"通过普通 Lua/Python 函数定义即可（外部经 `sim.callScriptFunction` 机制调用），例如：

```lua
function getStatus()
    return {position = sim.getJointPosition(jointHandle),
            time = sim.getSimulationTime()}
end
```

## 与 Legacy Remote API 对比

| 特性 | Legacy Remote API | ZeroMQ Remote API |
|------|------------------|-------------------|
| 协议 | XML-RPC | ZMQ + CBOR |
| 性能 | 较慢 | 快（约 10x 提升） |
| 语言支持 | C/C++, Python, Java, Lua, MATLAB | C++, Python, Java, MATLAB, Octave, Lua, Rust + JS(WS 变体) |
| 连接管理 | 手动 | 自动重连 |
| 堵塞风险 | 同步调用易堵 | 异步支持更好 |
| 状态 | 废弃（仅向后兼容） | **推荐**（4.1+） |

## 双连接与并发（本仓库实践）

单个 ZMQ 连接是 **同步独占** 的：一次请求-回复期间该连接被占用。仿真主循环持续调用 `sim.step()` 时，若诊断工具共用同一连接会被阻塞。本仓库 MCP 服务器维护**两个 `CoppeliaConnector`**：主连接（写/控制类工具 + 主循环）、只读 `ro_connector`（诊断工具 `get_object_pose`/`list_objects`/`read_signal`/`get_object_tree`/`inspect_scene_state`/`get_simulation_state`/`run_lua`，经 `ro_lock` 隔离）。这解决了旧版"仿真中 status 假断连"问题。详见 [[98-ProjectUsage/MCP_USAGE]]。

## 常见问题

1. **连接失败** — 确认 CoppeliaSim 运行中且 23000 端口未被占用；检查 Add-ons > ZMQ remote API server 是否激活。
2. **函数找不到** — 确认函数名在 Regular API 中存在（极少数脚本专用函数不在 Remote API 暴露）。
3. **性能慢** — 避免逐帧频繁往返；用 asyncio 客户端 + `asyncio.gather` 批量并发；尽量在脚本端批量处理。
4. **通信冻结** — 多份模型自带 Lua 脚本在 4.10 ZMQ addOn 层争用会致冻结（本仓库已知问题 #3），剥离模型脚本可缓解。

---

**关联条目：**
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 详解（同一套 `sim.*`）
- [[wiki/05-数字孪生与协同仿真/05-01-ROS2集成]] — ROS2 外部控制
- [[wiki/06-工业案例/06-01-UR5-抓取与放置案例]] — 应用案例
- [[wiki/06-工业案例/06-02-岛式车间柔性生产案例]] — 本仓库实战（stepping + 双连接）
