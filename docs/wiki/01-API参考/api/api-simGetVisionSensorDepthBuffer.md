---
title: sim.getVisionSensorDepthBuffer
type: api
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/中级
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simGetVisionSensorDepthBuffer.htm
related:
  - "[[wiki/01-API参考/api/api-simGetVisionSensorImage]]"
  - "[[wiki/01-API参考/api/api-simSetVisionSensorImage]]"
  - "[[wiki/01-API参考/api/api-simHandleVisionSensor]]"
  - "[[wiki/01-API参考/api/api-simCheckVisionSensor]]"
  - "[[wiki/04-感知与视觉/04-02-视觉传感器]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
sim_version: "4.10"
---

# sim.getVisionSensorDepthBuffer

> 读取视觉传感器的深度缓冲。**已弃用**，4.10 推荐改用 `sim.getVisionSensorDepth`（返回 `bytes` + 分辨率）。

## 签名

```python
list/bytes depthBuffer = sim.getVisionSensorDepthBuffer(int sensorHandle,
                                                        int posX=0, int posY=0,
                                                        int sizeX=0, int sizeY=0)
```

## 参数

- `sensorHandle`：视觉传感器句柄。可与 flag 组合（直接加到句柄上）：
  - `sim.handleflag_depthbuffermeters` —— 返回值改为**以米为单位的距离**
  - `sim.handleflag_codedstring`（Lua）—— 返回 `sim.buffer_float` 字符串缓冲
- `posX / posY`：子图左上角位置，默认 `0`。
- `sizeX / sizeY`：子图尺寸，默认 `0` 表示整图。

## 返回值

- `depthBuffer`：长度 `sizeX*sizeY` 的深度缓冲。
  - 默认值域 **0–1**：`0`=最近（近裁剪面），`1`=最远（远裁剪面）
  - 组合 `sim.handleflag_depthbuffermeters` 后，值为到传感器的**距离（米）**

> ⚠ 须先经 [[wiki/01-API参考/api/api-simHandleVisionSensor|sim.handleVisionSensor]] 处理，否则返回无意义。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient
import numpy as np

client = RemoteAPIClient()
sim = client.require('sim')

sensor = sim.getObject('/VisionSensor')
sim.handleVisionSensor(sensor)

# 新式 API：options bit0=1 表示返回以米为单位的距离
depth_bytes, [w, h] = sim.getVisionSensorDepth(sensor, 1, [0, 0], [0, 0])
# bytes 为 packed float32，用 sim.unpackFloatTable 解包
depth = np.array(sim.unpackFloatTable(depth_bytes, [w * h, 0, 0, 0, 0, 0]),
                 dtype=np.float32).reshape((h, w))
print('near=%.3f m, far=%.3f m' % (depth.min(), depth.max()))

# 旧式等价（已弃用）：
# depth = sim.getVisionSensorDepthBuffer(sensor + sim.handleflag_depthbuffermeters)
```

## 附录：`sim.getVisionSensorDepth`（推荐替代）

```python
bytes depth, list resolution = sim.getVisionSensorDepth(int sensorHandle,
                                                         int options=0,
                                                         list pos=[0, 0],
                                                         list size=[0, 0])
```

`options` 位编码：`bit0` 置位 (1) 返回值为以米为单位的距离，否则为 0.0–1.0 归一化深度。返回 `bytes` 用 `sim.unpackFloatTable` 解包。同时返回 `[w, h]` 分辨率。

## 参见

- [[wiki/01-API参考/api/api-simGetVisionSensorImage]] — 读取 RGB 图像
- [[wiki/01-API参考/api/api-simSetVisionSensorImage]] — 写入图像/深度缓冲
- [[wiki/01-API参考/api/api-simHandleVisionSensor]] — 触发渲染与检测
- [[wiki/01-API参考/api/api-simCheckVisionSensor]] — 静默检测指定实体
- [[wiki/04-感知与视觉/04-02-视觉传感器]] — 视觉传感器概念（待建）
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
