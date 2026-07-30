---
title: sim.checkVisionSensor
type: api
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/中级
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simCheckVisionSensor.htm
related:
  - "[[wiki/01-API参考/api/api-simHandleVisionSensor]]"
  - "[[wiki/01-API参考/api/api-simGetVisionSensorImage]]"
  - "[[wiki/01-API参考/api/api-simSetVisionSensorImage]]"
  - "[[wiki/01-API参考/api/api-simGetVisionSensorDepthBuffer]]"
  - "[[wiki/04-感知与视觉/04-02-视觉传感器]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
sim_version: "4.10"
---

# sim.checkVisionSensor

> 检查视觉传感器是否检测到指定实体。相比 `sim.handleVisionSensor`，检测是**静默**的（无视觉反馈），但仍会调用视觉回调函数。若实体为对象，其可见性状态会被覆盖。

## 签名

```python
int result, list auxPacket1, list auxPacket2, ... = sim.checkVisionSensor(int sensorHandle,
                                                                          int entityHandle)
```

## 参数

- `sensorHandle`：视觉传感器句柄。
- `entityHandle`：要检测的实体句柄（对象或集合），或 `sim.handle_all` 检测所有可检测对象。

## 返回值

- `result`：`0`（未检测到）或 `1`（检测到）。
- `auxPacket1`：15 个辅助值的包：
  - `[intensity, red, green, blue, depth]` 的**最小值**
  - `[intensity, red, green, blue, depth]` 的**最大值**
  - `[intensity, red, green, blue, depth]` 的**平均值**
- `auxPacket2, auxPacket3, ...`：附加辅助值包（如图像处理组件输出）。返回包数与辅助值包数量一致。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

sensor = sim.getObject('/VisionSensor')
target = sim.getObject('/Cuboid')   # 须为可检测属性

# 静默检测：不刷新场景中的检测线/颜色反馈
result, aux1, *_ = sim.checkVisionSensor(sensor, target)
print('detected:', result == 1)
if result == 1:
    # aux1: [min_i,min_r,min_g,min_b,min_d, max_*, avg_*]
    print('avg depth =', aux1[12])
```

## 附录：`sim.checkVisionSensorEx`

扩展版本，支持更多参数（如自定义射线数、返回更多数据包）。详见 `regularApi/simCheckVisionSensorEx.htm`。

## 参见

- [[wiki/01-API参考/api/api-simHandleVisionSensor]] — 触发渲染与检测（带视觉反馈）
- [[wiki/01-API参考/api/api-simGetVisionSensorImage]] — 读取 RGB 图像
- [[wiki/01-API参考/api/api-simSetVisionSensorImage]] — 写入图像
- [[wiki/01-API参考/api/api-simGetVisionSensorDepthBuffer]] — 读取深度缓冲
- [[wiki/04-感知与视觉/04-02-视觉传感器]] — 视觉传感器概念（待建）
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
