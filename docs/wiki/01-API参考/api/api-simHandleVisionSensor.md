---
title: sim.handleVisionSensor
type: api
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/中级
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simHandleVisionSensor.htm
related:
  - "[[wiki/01-API参考/api/api-simGetVisionSensorImage]]"
  - "[[wiki/01-API参考/api/api-simSetVisionSensorImage]]"
  - "[[wiki/01-API参考/api/api-simGetVisionSensorDepthBuffer]]"
  - "[[wiki/01-API参考/api/api-simCheckVisionSensor]]"
  - "[[wiki/04-感知与视觉/04-02-视觉传感器]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
sim_version: "4.10"
---

# sim.handleVisionSensor

> 触发视觉传感器的渲染与检测：清除上一帧图像处理数据 → 读取新图像 → 执行视觉回调函数。若传感器使用 external input，则仅执行清除步骤。每个仿真步对每个视觉传感器调用一次。

## 签名

```python
int detectionCount, list packet1, list packet2, ... = sim.handleVisionSensor(int visionSensorHandle)
```

## 参数

- `visionSensorHandle`：视觉传感器句柄，或 `sim.handle_all` / `sim.handle_all_except_explicit` 批量处理。

## 返回值

- `detectionCount`：触发检测的传感器数量。
- `packet1`：默认辅助值包，15 个值：
  - `[intensity, red, green, blue, depth]` 的**最小值**
  - `[intensity, red, green, blue, depth]` 的**最大值**
  - `[intensity, red, green, blue, depth]` 的**平均值**
- `packet2, packet3, ...`：附加辅助值包（如图像处理组件输出）。返回包数与辅助值包数量一致。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

sensor = sim.getObject('/VisionSensor')

# 每个仿真步调用一次，驱动渲染与图像处理
sim.setStepping(True)
sim.startSimulation()
for _ in range(10):
    sim.step()
    det_count, *packets = sim.handleVisionSensor(sensor)
    print('detections:', det_count, 'aux packet 1:', packets[0] if packets else None)
sim.stopSimulation()
```

## 附录：`sim.readVisionSensor`

```python
int result, list packet1, list packet2, ... = sim.readVisionSensor(int visionSensorHandle)
```

仅读取上一次 `sim.handleVisionSensor` 的结果，**不执行检测**。

- `result`：检测状态 `0`/`1`；`-1` 表示从未调用 `handleVisionSensor` 或已调用 `resetVisionSensor`。
- `packet1, packet2, ...`：同 `handleVisionSensor` 的辅助值包。

典型分工：主脚本用 `handleVisionSensor` 驱动，从脚本用 `readVisionSensor` 取结果，避免重复渲染。

## 参见

- [[wiki/01-API参考/api/api-simCheckVisionSensor]] — 静默检测指定实体（无视觉反馈）
- [[wiki/01-API参考/api/api-simGetVisionSensorImage]] — 读取 RGB 图像（须先 handle）
- [[wiki/01-API参考/api/api-simGetVisionSensorDepthBuffer]] — 读取深度缓冲（须先 handle）
- [[wiki/01-API参考/api/api-simSetVisionSensorImage]] — 写入图像
- [[wiki/04-感知与视觉/04-02-视觉传感器]] — 视觉传感器概念（待建）
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
