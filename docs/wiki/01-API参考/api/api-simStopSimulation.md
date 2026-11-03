---
title: sim.stopSimulation
type: api
tags: [coppeliasim, 版本/v4.10, 类别/API, 难度/初级]
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simStopSimulation.htm
related:
  - "[[wiki/01-API参考/api/api-simStartSimulation]]"
  - "[[wiki/01-API参考/api/api-simPauseSimulation]]"
  - "[[wiki/01-API参考/api/api-simGetSimulationState]]"
  - "[[wiki/01-API参考/01-02-Remote-API-ZMQ]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
  - "[[wiki/06-工业案例/06-02-岛式车间柔性生产案例]]"
sim_version: "4.10"
---

# sim.stopSimulation

> 请求停止正在运行的仿真。触发引擎回到 `sim.simulation_stopped` 态并重置仿真时间。

## 签名

```python
sim.stopSimulation(bool wait = False)
```

## 参数

- `wait`（bool，默认 `False`）：
  - `False`：仅投递停止请求后立即返回，状态切换异步完成。
  - `True`：阻塞直到状态变为 `sim.simulation_stopped` 再返回。使用此值时调用方不能是 main script 或 simulation script，且须运行在线程化脚本中。Remote API 客户端一般不传此参数，而是自行轮询 `getSimulationState()` 确认回到 0。

## 返回值

无。

## 说明

- 停止是异步的：调用返回后引擎会经过 `sim.simulation_advancing_abouttostop` → `sim.simulation_advancing_lastbeforestop` 等过渡态，最终落到 `sim.simulation_stopped`（码值 0）。
- 在 stepping 模式下，`stopSimulation` 会让阻塞中的 `sim.step()` 抛出异常从而退出推进循环——本仓库 v1 运行器和 `flexible-factory stop` 都靠这一机制让主循环退出（最多等待 5s）。
- 实测 4.10.x 在 `stopSimulation` 后还可能出现一个 brief 文档未列的过渡码 22（约 0.3–0.5s 内回到 0）。若轮询到该码，属正常现象。

## 示例

```python
import time
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

sim.startSimulation()
time.sleep(1)

# 1. 异步停止：投递请求后轮询确认
sim.stopSimulation()
while sim.getSimulationState() != sim.simulation_stopped:
    time.sleep(0.05)
print('stopped, sim time =', sim.getSimulationTime())  # 停止后仿真时间已重置

# 2. stepping 模式：stop 触发 step() 异常退出循环
sim.setStepping(True)
sim.startSimulation()
try:
    while (t := sim.getSimulationTime()) < 5:
        sim.step()
except Exception as e:
    print('step loop exited:', e)
sim.stopSimulation()
```

## 参见

- [[wiki/01-API参考/api/api-simStartSimulation]] — 启动/恢复仿真
- [[wiki/01-API参考/api/api-simPauseSimulation]] — 暂停仿真
- [[wiki/01-API参考/api/api-simGetSimulationState]] — 查询状态码与过渡态
- [[wiki/01-API参考/01-02-Remote-API-ZMQ]] — stepping 模式与 `sim.step()`
- [[wiki/06-工业案例/06-02-岛式车间柔性生产案例]] — 停止信号在本仓库主循环中的应用
- 官方手册 `regularApi/simStopSimulation.htm`
