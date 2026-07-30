---
title: sim.handleGraph
type: api
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/中级
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simHandleGraph.htm
related:
  - "[[wiki/02-仿真建模/02-08-图与数据可视化]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
  - "[[wiki/02-仿真建模/02-01-分层命名场景构建]]"
sim_version: "4.10"
---

# sim.handleGraph

> 处理一次图对象（graph）的记录：把当前各曲线已经通过 `sim.setGraphStreamValue` 喂入的数值，写入到指定的 `time` 时间戳上。通常每个仿真步调用一次，`time` 取当前仿真时间。

## 签名

```python
sim.handleGraph(int graphHandle, float time)
```

## 参数

- `graphHandle`：图对象句柄。
- `time`：本次记录的时间戳，通常传 `sim.getSimulationTime()`。

## 返回值

无。

## 典型循环

`handleGraph` 与 `setGraphStreamValue` 是配对使用的：前者设值，后者把值固化到图。下面给出一个完整可运行的 ZMQ 客户端循环，记录单个对象的 x/y 位置曲线（结合 `addGraphStream` + `addGraphCurve`）：

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

graph = sim.getObject('/Graph')
obj = sim.getObject('/object')

# 1) 注册两条位置流（options=1 表示流本身不可见，仅用于曲线）
pos_x = sim.addGraphStream(graph, 'object pos x', 'm', 1)
pos_y = sim.addGraphStream(graph, 'object pos y', 'm', 1)
# 2) 组合成 x/y 曲线
sim.addGraphCurve(graph, 'object pos x/y', 2, [pos_x, pos_y], [0, 0], 'm by m')

# 3) stepping 模式逐帧喂值并触发记录
sim.setStepping(True)
sim.startSimulation()
for _ in range(300):
    x, y, _ = sim.getObjectPosition(obj, sim.handle_world)
    sim.setGraphStreamValue(graph, pos_x, x)
    sim.setGraphStreamValue(graph, pos_y, y)
    sim.handleGraph(graph, sim.getSimulationTime())
    sim.step()
sim.stopSimulation()
```

> 说明：若某步未对某条流调用 `setGraphStreamValue` 而直接 `handleGraph`，则该流在此时间点的数据缺失，图上表现为断点。手册原例在脚本对象的 `sysCall_sensing` 中喂值；ZMQ 客户端在主循环里显式调用即可，二者等价。

## 附录：同族函数

| 函数 | 说明 |
|------|------|
| `sim.setGraphStreamValue(graphHandle, streamId, value)` | 为某条流设置下一个待记录值，`handleGraph` 前必须先设值。 |
| `sim.addGraphStream(graphHandle, streamName, unitStr='', options=0, color=[1,0,0], cyclicRange=0)` | 添加/更新数据流，返回 `streamId`。 |
| `sim.addGraphCurve(graphHandle, curveName, dim, streamIds, defaultValues, unitStr, options=0, color=[0,1,0], curveWidth=2)` | 由 2/3 条流组合成曲线，返回 `curveId`。 |
| `sim.destroyGraphCurve(graphHandle, curveId)` | 销毁曲线。 |

## 参见

- [[wiki/01-API参考/api/api-simSetGraphStreamValue]] — 设定流的待记录值（每步必配对调用）
- [[wiki/02-仿真建模/02-08-图与数据可视化]] — 图对象与数据流/曲线概念（待建）
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
- [[wiki/02-仿真建模/02-01-分层命名场景构建]] — 场景中 `getObject('/Graph')` 的路径查询机制
