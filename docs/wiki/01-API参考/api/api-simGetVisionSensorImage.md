---
title: sim.getVisionSensorImage
type: api
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/中级
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simGetVisionSensorImage.htm
related:
  - "[[wiki/01-API参考/api/api-simSetVisionSensorImage]]"
  - "[[wiki/01-API参考/api/api-simGetVisionSensorDepthBuffer]]"
  - "[[wiki/01-API参考/api/api-simHandleVisionSensor]]"
  - "[[wiki/01-API参考/api/api-simCheckVisionSensor]]"
  - "[[wiki/04-感知与视觉/04-02-视觉传感器]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
sim_version: "4.10"
---

# sim.getVisionSensorImage

> 读取视觉传感器的 RGB 图像缓冲（值域 0–1 或 0–255）。**已弃用**，4.10 推荐改用 `sim.getVisionSensorImg`（返回 `bytes` + 分辨率，配合 `numpy` 更高效）。

## 签名

```python
list/bytes imageBuffer = sim.getVisionSensorImage(int sensorHandle,
                                                  int posX=0, int posY=0,
                                                  int sizeX=0, int sizeY=0,
                                                  int returnType=0)
```

## 参数

- `sensorHandle`：视觉传感器句柄。可与 `sim.handleflag_greyscale` 组合（直接加到句柄上）以取灰度图。
- `posX / posY`：要读取的子图左上角位置，默认 `0`。
- `sizeX / sizeY`：子图尺寸，默认 `0` 表示整图。
- `returnType`：返回缓冲类型。
  - `0` —— 返回 `list`，RGB 值域 0–1
  - `1` —— 返回 `bytes`，RGB 值域 0–255

## 返回值

- `imageBuffer`：RGB 缓冲。`returnType=0` 时为长度 `sizeX*sizeY*3` 的 list（0–1）；`returnType=1` 时为 `bytes`（0–255）。灰度图则对应 `sizeX*sizeY`。

> ⚠ 传感器须先经 [[wiki/01-API参考/api/api-simHandleVisionSensor|sim.handleVisionSensor]] 处理过，否则返回数据无意义。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient
import numpy as np

client = RemoteAPIClient()
sim = client.require('sim')

sensor = sim.getObject('/VisionSensor')
# 先触发渲染与图像处理
sim.handleVisionSensor(sensor)

# 取 bytes（0-255），便于 numpy 直接 reshape
img_bytes, [w, h] = sim.getVisionSensorImg(sensor, 0, 0.0, [0, 0], [0, 0])
arr = np.frombuffer(img_bytes, dtype=np.uint8).reshape((h, w, 3))
# 注意 CoppeliaSim 图像为底起式（bottom-up），需上下翻转得到常规视图
arr = np.flipud(arr)

# 旧式 sim.getVisionSensorImage 等价写法（已弃用，仅作参考）：
# img = sim.getVisionSensorImage(sensor, 0, 0, 0, 0, 1)  # returnType=1 -> bytes
# arr = np.frombuffer(img, dtype=np.uint8).reshape((-1, 3))

print(arr.shape)   # (h, w, 3)
print(arr[0, 0])   # 顶部首像素 RGB
```

## 附录：辅助函数 `sim.getVisionSensorRes`

```python
list resolution = sim.getVisionSensorRes(int sensorHandle)
```

返回 `[resolutionX, resolutionY]`。常在 `reshape` 前用于获取尺寸。

## 参见

- [[wiki/01-API参考/api/api-simSetVisionSensorImage]] — 写入图像（旧式）
- [[wiki/01-API参考/api/api-simGetVisionSensorDepthBuffer]] — 读取深度缓冲
- [[wiki/01-API参考/api/api-simHandleVisionSensor]] — 触发渲染与检测（调用本函数前必须先调用）
- [[wiki/01-API参考/api/api-simCheckVisionSensor]] — 静默检测指定实体
- [[wiki/04-感知与视觉/04-02-视觉传感器]] — 视觉传感器概念（待建）
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
