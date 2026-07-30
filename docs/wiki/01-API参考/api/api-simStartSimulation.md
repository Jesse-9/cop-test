---
title: sim.startSimulation
type: api
tags: [coppeliasim, 版本/v4.10, 类别/API, 难度/初级]
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simStartSimulation.htm
related:
  - "[[wiki/01-API参考/api/api-simStopSimulation]]"
  - "[[wiki/01-API参考/api/api-simPauseSimulation]]"
  - "[[wiki/01-API参考/api/api-simGetSimulationState]]"
  - "[[wiki/01-API参考/01-02-Remote-API-ZMQ]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
  - "[[wiki/06-工业案例/06-02-岛式车间柔性生产案例]]"
sim_version: "4.10"
---

# sim.startSimulation

> 请求启动仿真，或恢复一个已暂停的仿真。是仿真生命周期的入口函数。

## 签名

```python
sim.startSimulation()
```

## 参数

无。

## 返回值

无。函数仅向引擎投递一个「启动/恢复」请求，状态切换在后续主循环推进中完成。

## 说明

- 启动后仿真进入 `sim.simulation_advancing_firstafterstop`（首帧）再进入 `sim.simulation_advancing_running`（稳态运行）。
- 若此前处于 `sim.simulation_paused`，调用本函数等价于「恢复运行」，不会重置仿真时间。
- 调用时引擎不会清除暂停态——如果上次仿真停在 paused，直接 `startSimulation` 后再 `sim.step()` 可能永久挂起。本仓库 `FactoryController.run()` 的做法是先 `stopSimulation` 让引擎回到 `stopped` 态、轮询 `getSimulationState()==0` 确认后再 `startSimulation`，以规避该陷阱。
- 配合 stepping 模式时：先 `sim.setStepping(True)` 再 `startSimulation`，之后由外部循环用 `sim.step()` 逐帧推进，Python 端成为 clock master（见 [[wiki/01-API参考/01-02-Remote-API-ZMQ]]）。

## 示例

```python
import time
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

# 1. 普通（自动推进）模式启动 + 运行 3 秒后停止
sim.startSimulation()
time.sleep(3)
sim.stopSimulation()

# 2. stepping 模式：Python 端逐帧推进，精确控制节拍
sim.setStepping(True)
sim.startSimulation()
while (t := sim.getSimulationTime()) < 3:
    state = sim.getSimulationState()
    print(f't={t:.2f}s  state={state}')
    sim.step()
sim.stopSimulation()
```

## 参见

- [[wiki/01-API参考/api/api-simStopSimulation]] — 停止仿真
- [[wiki/01-API参考/api/api-simPauseSimulation]] — 暂停仿真
- [[wiki/01-API参考/api/api-simGetSimulationState]] — 查询状态码
- [[wiki/01-API参考/01-02-Remote-API-ZMQ]] — ZMQ + stepping 模式
- [[wiki/06-工业案例/06-02-岛式车间柔性生产案例]] — 本仓库主线的生命周期编排
- 官方手册 `regularApi/simStartSimulation.htm`、`simulation.htm`（状态图）
