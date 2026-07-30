---
title: sim.pauseSimulation
type: api
tags: [coppeliasim, 版本/v4.10, 类别/API, 难度/初级]
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simPauseSimulation.htm
related:
  - "[[wiki/01-API参考/api/api-simStartSimulation]]"
  - "[[wiki/01-API参考/api/api-simStopSimulation]]"
  - "[[wiki/01-API参考/api/api-simGetSimulationState]]"
  - "[[wiki/01-API参考/01-02-Remote-API-ZMQ]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
  - "[[wiki/06-工业案例/06-02-岛式车间柔性生产案例]]"
sim_version: "4.10"
---

# sim.pauseSimulation

> 请求暂停仿真。仿真时间冻结，场景保持当前状态，可用 `sim.startSimulation` 恢复。

## 签名

```python
sim.pauseSimulation()
```

## 参数

无。

## 返回值

无。

## 说明

- 暂停请求投递后，引擎会经过 `sim.simulation_advancing_lastbeforepause` → `sim.simulation_advancing_firstafterpause` 等过渡态，最终稳定在 `sim.simulation_paused`。
- 暂停态下 `sim.getSimulationTime()` 不再增长，`sim.step()` 不会推进物理引擎。
- 恢复方式：再次调用 `sim.startSimulation()`（手册明确 start 兼具 resume 语义）。注意：若想从暂停态彻底回到初始态，应先 `sim.stopSimulation()` 让引擎回到 `stopped` 再重新启动，否则可能在 paused 残留态上挂起（见 [[wiki/01-API参考/api/api-simStartSimulation]] 的陷阱说明）。

## 示例

```python
import time
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

sim.startSimulation()
time.sleep(1)
t1 = sim.getSimulationTime()

# 1. 暂停 → 确认进入 paused 态 → 停止
sim.pauseSimulation()
while sim.getSimulationState() != sim.simulation_paused:
    time.sleep(0.02)
t2 = sim.getSimulationTime()
print(f'paused at t={t2:.3f}s (delta during pause ~{t2-t1:.3f})')

# 2. 恢复运行
sim.startSimulation()
time.sleep(1)
print('after resume:', sim.getSimulationTime())

sim.stopSimulation()
```

## 参见

- [[wiki/01-API参考/api/api-simStartSimulation]] — 启动/恢复（resume）仿真
- [[wiki/01-API参考/api/api-simStopSimulation]] — 停止并重置
- [[wiki/01-API参考/api/api-simGetSimulationState]] — 查询 paused/过渡态
- [[wiki/01-API参考/01-02-Remote-API-ZMQ]] — ZMQ 远程控制
- [[wiki/06-工业案例/06-02-岛式车间柔性生产案例]] — 生命周期编排
- 官方手册 `regularApi/simPauseSimulation.htm`、`simulation.htm`（状态图）
