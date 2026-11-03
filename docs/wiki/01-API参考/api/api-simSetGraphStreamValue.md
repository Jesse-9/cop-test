---
title: sim.setGraphStreamValue
type: api
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/中级
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simSetGraphStreamValue.htm
related:
  - "[[wiki/02-仿真建模/02-08-图与数据可视化]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
  - "[[wiki/02-仿真建模/02-01-分层命名场景构建]]"
sim_version: "4.10"
---

# sim.setGraphStreamValue

> 为图对象的某个数据流（stream）设置下一次待记录的值。需在每个仿真步先调用本函数喂入各流数值，再调用一次 [`sim.handleGraph`](api-simHandleGraph.md) 把当前时间点写入图中。若某个流在对应的 `sim.handleGraph` 调用前未设值，则该时间点的数据缺失（图上出现断点）。

## 签名

```python
sim.setGraphStreamValue(int graphHandle, int streamId, float value)
```

## 参数

- `graphHandle`：图对象句柄。
- `streamId`：数据流 ID（由 `sim.addGraphStream` 返回）。
- `value`：本次要记录的数值。**必须先设值再 `sim.handleGraph`**，否则该点缺失。

## 返回值

无。

## 典型循环

图数据的录入遵循固定三段式：初始化时 `addGraphStream` 注册流 → 每步 `setGraphStreamValue` 喂数据 → 同步调用 `handleGraph` 触发记录。下面用 ZMQ Remote API 在 Python 端完整驱动一个 stepping 仿真循环，记录两条关节速度流：

```python
import math
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

# 取已存在的图对象与两个关节（场景中先 Add > Graph 添加一个 Graph）
graph = sim.getObject('/Graph')
joint1 = sim.getObject('/joint1')
joint2 = sim.getObject('/joint2')

# 1) 注册两条数据流，返回 streamId
stream1 = sim.addGraphStream(graph, 'joint1 velocity', 'deg/s', 0, [1, 0, 0])
stream2 = sim.addGraphStream(graph, 'joint2 velocity', 'deg/s', 0, [0, 1, 0])

# 2) stepping 模式逐帧喂值
sim.setStepping(True)
sim.startSimulation()
for _ in range(200):
    v1 = 180.0 * sim.getJointVelocity(joint1) / math.pi
    v2 = 180.0 * sim.getJointVelocity(joint2) / math.pi
    sim.setGraphStreamValue(graph, stream1, v1)
    sim.setGraphStreamValue(graph, stream2, v2)
    # 3) 用当前仿真时间触发一次记录
    sim.handleGraph(graph, sim.getSimulationTime())
    sim.step()
sim.stopSimulation()
```

> 说明：手册原例用脚本对象的 `sysCall_sensing` 钩子每步喂值；ZMQ 客户端视角下改为在主循环里显式调用，效果等价。图流是持久（persistent）的，停止仿真后数据仍保留在场景中，可再次运行时叠加。

## 附录：同族函数

| 函数 | 说明 |
|------|------|
| `sim.addGraphStream(graphHandle, streamName, unitStr='', options=0, color=[1,0,0], cyclicRange=0)` | 添加或更新一个数据流，返回 `streamId`。`options` 位编码：bit0=流不可见、bit1=标签不可见、bit2=点不连线。`cyclicRange` 用于周期量（如角度）的正确变换。 |
| `sim.addGraphCurve(graphHandle, curveName, dim, streamIds, defaultValues, unitStr, options=0, color=[0,1,0], curveWidth=2)` | 组合 2 或 3 条流为 x/y 或 x/y/z 曲线，返回 `curveId`。`streamIds` 长度须为 2 或 3；缺失流填 `-1` 并用 `defaultValues` 替代。 |
| `sim.destroyGraphCurve(graphHandle, curveId)` | 销毁曲线；亦用于移除由流构成的持久曲线。 |
| `sim.handleGraph(graphHandle, time)` | 触发图记录一次，将此前 `setGraphStreamValue` 设的值写入对应时间戳。 |

## 参见

- [[wiki/01-API参考/api/api-simHandleGraph]] — 触发图记录（每步必配对调用）
- [[wiki/02-仿真建模/02-08-图与数据可视化]] — 图对象与数据流/曲线概念（待建）
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
- [[wiki/02-仿真建模/02-01-分层命名场景构建]] — 场景中 `getObject('/Graph')` 的路径查询机制
