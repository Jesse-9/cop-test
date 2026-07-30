---
title: sim.getSimulationState
type: api
tags: [coppeliasim, 版本/v4.10, 类别/API, 难度/初级]
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simGetSimulationState.htm
related:
  - "[[wiki/01-API参考/api/api-simStartSimulation]]"
  - "[[wiki/01-API参考/api/api-simStopSimulation]]"
  - "[[wiki/01-API参考/api/api-simPauseSimulation]]"
  - "[[wiki/01-API参考/01-02-Remote-API-ZMQ]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
  - "[[wiki/06-工业案例/06-02-岛式车间柔性生产案例]]"
sim_version: "4.10"
---

# sim.getSimulationState

> 查询当前仿真状态码。是 start/pause/stop 生命周期的观测接口，也是 ZMQ 探活与冻结诊断的关键探针。

## 签名

```python
int simulationState = sim.getSimulationState()
```

## 参数

无。

## 返回值

- `simulationState`（int）：当前状态码。对应 `sim.simulation_*` 常量。

## 状态常量

依据 `apiConstants.htm` 与 `simulation.htm` 状态图，主要状态常量如下：

| 常量 | 含义 |
|------|------|
| `sim.simulation_stopped` | 仿真已停止，时间已重置 |
| `sim.simulation_advancing_firstafterstop` | 停止后启动的首帧 |
| `sim.simulation_advancing_running` | 稳态运行中 |
| `sim.simulation_advancing_lastbeforepause` | 暂停前的最后一帧 |
| `sim.simulation_advancing_firstafterpause` | 暂停后恢复的首帧 |
| `sim.simulation_paused` | 已暂停 |
| `sim.simulation_advancing_abouttostop` | 即将停止 |
| `sim.simulation_advancing_lastbeforestop` | 停止前的最后一帧 |

> 码值在不同 4.10.x 子版本可能微调。本机实测（2026-07-20）：`stopped`=0、`advancing_first`=16、稳态运行=17（brief 标 17 为 lastbeforestop，此版实为 running）、`paused`=19，另观察到 brief 未列的过渡码 22（`stopSimulation` 后 0.3–0.5s 内回到 0）。生产代码应以常量名判定而非硬编码数值。

## 附录：相关时间查询函数

| 函数 | 签名 | 返回 |
|------|------|------|
| `sim.getSimulationTime()` | `float t = sim.getSimulationTime()` | 仿真时间（秒），停止后重置为 0 |
| `sim.getSimulationTimeStep()` | `float dt = sim.getSimulationTimeStep()` | 每个主脚本 pass 推进的仿真时间步长（可能非恒定） |
| `sim.getSystemTime()` | `float sys = sim.getSystemTime()` | 系统时间（秒） |

## 说明

- 该函数调用成功即证明 ZMQ 通路可达，是 `CoppeliaConnector.check_alive()` 的回退探针（`coppeliaSimVersion` 信号可能为 None 时改用本函数）。
- 在 stepping 模式主循环中，用它判断是否进入稳态运行、是否被 `stopSimulation` 触发退出。
- 双 ZMQ 连接场景下，只读诊断工具走 `ro_connector` 调本函数不受主循环占用影响。

## 示例

```python
import time
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

# stepping 模式：启动 → 逐帧推进并观测状态 → 停止
sim.setStepping(True)
sim.startSimulation()

try:
    while (t := sim.getSimulationTime()) < 2:
        st = sim.getSimulationState()
        dt = sim.getSimulationTimeStep()
        print(f't={t:.3f}s  dt={dt:.4f}  state={st}')
        sim.step()
except Exception as e:
    print('step loop exited:', e)
finally:
    sim.stopSimulation()

# 轮询确认回到 stopped
while sim.getSimulationState() != sim.simulation_stopped:
    time.sleep(0.02)
print('final state =', sim.getSimulationState(), '(should be stopped/0)')
```

## 参见

- [[wiki/01-API参考/api/api-simStartSimulation]] — 启动/恢复
- [[wiki/01-API参考/api/api-simStopSimulation]] — 停止（过渡态观测）
- [[wiki/01-API参考/api/api-simPauseSimulation]] — 暂停
- [[wiki/01-API参考/01-02-Remote-API-ZMQ]] — stepping 模式与探活
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
- [[wiki/06-工业案例/06-02-岛式车间柔性生产案例]] — 状态码在本仓库生命周期中的应用
- 官方手册 `regularApi/simGetSimulationState.htm`、`apiConstants.htm`、`simulation.htm`（状态图）
