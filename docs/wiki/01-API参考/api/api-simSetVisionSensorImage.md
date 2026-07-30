---
title: sim.setVisionSensorImage
type: api
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/中级
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simSetVisionSensorImage.htm
related:
  - "[[wiki/01-API参考/api/api-simGetVisionSensorImage]]"
  - "[[wiki/01-API参考/api/api-simGetVisionSensorDepthBuffer]]"
  - "[[wiki/01-API参考/api/api-simHandleVisionSensor]]"
  - "[[wiki/01-API参考/api/api-simCheckVisionSensor]]"
  - "[[wiki/04-感知与视觉/04-02-视觉传感器]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
sim_version: "4.10"
---

# sim.setVisionSensorImage

> 写入视觉传感器的图像缓冲，并触发图像回调处理。**已弃用**，4.10 推荐改用 `sim.setVisionSensorImg`（基于 `bytes` + `options` 位编码）。

## 签名

```python
(1) int result = sim.setVisionSensorImage(int sensorHandle, list image)
(2) int result = sim.setVisionSensorImage(int sensorHandle, bytes image)
```

## 参数

- `sensorHandle`：视觉传感器句柄。可与以下 flag 组合（直接加到句柄上）：
  - `sim.handleflag_greyscale` —— 提供灰度值而非 RGB
  - `sim.handleflag_rawvalue` —— 不触发图像处理
  - `sim.handleflag_depthbuffer` —— 写入深度缓冲而非图像
- `image`：
  - 重载 (1) `list`，每个分量 0–1（RGB 或灰度，或深度值）
  - 重载 (2) `bytes`，每字节 0–255；`depthbuffer` 模式下为打包的深度值字符串

> 写入前须将传感器标记为 external input。

## 返回值

- `result`：`-1` 失败；`0` 图像处理未触发检测；`1` 图像处理触发了检测。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient
import numpy as np

client = RemoteAPIClient()
sim = client.require('sim')

sensor = sim.getObject('/VisionSensor')
w, h = sim.getVisionSensorRes(sensor)

# 生成一张 64x64 红色图（bytes, 0-255）
arr = np.zeros((h, w, 3), dtype=np.uint8)
arr[..., 0] = 255                    # R 通道
img_bytes = arr.tobytes()

# 新式 API：bit0=灰度, bit1=RGBA；RGB 默认 options=0
sim.setVisionSensorImg(sensor, img_bytes, 0, [0, 0], [0, 0])
# 旧式等价（已弃用）：
# sim.setVisionSensorImage(sensor, img_bytes)
```

## 附录：`sim.setVisionSensorImg`（推荐替代）

```python
sim.setVisionSensorImg(int sensorHandle, bytes image, int options=0,
                       list pos=[0, 0], list size=[0, 0])
```

`options` 位编码：`bit0`=灰度图（1 字节/像素），`bit1`=含 alpha 通道（RGBA 或灰度+A）。`size` 默认 `[0,0]` 表示整张分辨率。无返回值。

## 参见

- [[wiki/01-API参考/api/api-simGetVisionSensorImage]] — 读取图像（旧式）
- [[wiki/01-API参考/api/api-simGetVisionSensorDepthBuffer]] — 读取深度缓冲
- [[wiki/01-API参考/api/api-simHandleVisionSensor]] — 触发渲染与检测
- [[wiki/01-API参考/api/api-simCheckVisionSensor]] — 静默检测指定实体
- [[wiki/04-感知与视觉/04-02-视觉传感器]] — 视觉传感器概念（待建）
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
