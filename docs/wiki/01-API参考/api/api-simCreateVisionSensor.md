---
title: sim.createVisionSensor
type: api
tags: [coppeliasim, 版本/v4.10, 类别/API, 类别/感知, 难度/中级]
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simCreateVisionSensor.htm
related:
  - "[[wiki/04-感知与视觉/04-02-视觉传感器]]"
  - "[[wiki/02-仿真建模/02-01-分层命名场景构建]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
  - "[[wiki/01-API参考/api/api-simHandleVisionSensor]]"
  - "[[wiki/01-API参考/api/api-simGetVisionSensorImage]]"
sim_version: "4.10"
---

# sim.createVisionSensor

> 创建一个视觉传感器（Vision Sensor）。视觉传感器以相机视角渲染场景，输出 RGB 图像与深度缓冲，是机器人感知、视觉伺服、数字孪生图像采集的核心。创建时通过 `intParams`/`floatParams` 一次性指定分辨率、裁剪面、视角等。

## 签名

```python
int sensorHandle = sim.createVisionSensor(int options, list intParams, list floatParams)
```

## 参数

- `options`：位编码选项：
  - bit0 (1)：显式处理模式（需手动调 `sim.handleVisionSensor`，否则不渲染）
  - bit1 (2)：透视投影（否则正交）
  - bit2 (4)：不显示视锥
  - bit4 (16)：被动模式（使用外部图像，不自行渲染）
  - bit5 (32)：使用局部光照
  - bit6 (64)：不渲染雾
  - bit7 (128)：使用指定颜色作为默认背景（空像素）
- `intParams`：4 个整数参数（Python 下标从 0 起）：
  - `intParams[0]`：分辨率 x（像素）
  - `intParams[1]`：分辨率 y（像素）
  - `intParams[2]`：保留，设 0
  - `intParams[3]`：保留，设 0
- `floatParams`：11 个浮点参数（Python 下标从 0 起）：
  - `[0]` 近裁剪面、`[1]` 远裁剪面、`[2]` 视角/正交尺寸、`[3]` 传感器尺寸 x
  - `[6..8]` 空像素的 R/G/B 值
  - 其余 `[4][5][9][10]` 保留，设 `0.0`

## 返回值

- `sensorHandle`：新建视觉传感器的对象句柄。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

# 1. 创建 640x480 透视视觉传感器
import math
options = 2 | 4                      # 透视 + 不显示视锥
intParams = [640, 480, 0, 0]         # 分辨率 640x480
# API 单位为弧度（见 apiFunctions.htm），视角 60° ≈ 1.0472 rad
floatParams = [0.01, 3.0,            # near/far 裁剪面
               math.radians(60), 0.1,  # 视角(弧度) / 传感器尺寸
               0, 0,                 # 保留
               0.5, 0.5, 0.5,        # 空像素灰背景 RGB
               0, 0]                 # 保留
cam = sim.createVisionSensor(options, intParams, floatParams)
sim.setObjectAlias(cam, 'Overhead_Cam')

# 2. 定位到工件台上方俯视
sim.setObjectPosition(cam, [1.0, 1.0, 1.5], sim.handle_world)
sim.setObjectOrientation(cam, [3.14159, 0, 3.14159], sim.handle_world)  # 朝下

# 3. 运行时改分辨率（附录：sim.setObjectInt32Param）
#    常量 sim.visionintparam_resolution_x/y 见 objectParameterIDs.htm
sim.setObjectInt32Param(cam, sim.visionintparam_resolution_x, 320)
sim.setObjectInt32Param(cam, sim.visionintparam_resolution_y, 240)

print(sim.getObject('/Overhead_Cam') == cam)  # True
```

## 附录：运行时改分辨率（sim.setObjectInt32Param）

视觉传感器的分辨率等属性也可在创建后通过对象参数接口修改，常量见 `objectParameterIDs.htm`：

| 常量 | 类型 | 含义 |
|------|------|------|
| `sim.visionintparam_resolution_x` | int32 | 分辨率 x（像素） |
| `sim.visionintparam_resolution_y` | int32 | 分辨率 y（像素） |
| `sim.visionintparam_perspective_operation` | int32 | 1=透视 / 0=正交 |
| `sim.visionintparam_entity_to_render` | int32 | 仅渲染指定实体（-1 渲染全部） |

签名：`sim.setObjectInt32Param(int objectHandle, int parameterID, int parameter)`。改分辨率后下一帧渲染生效。

> `sim.setObjectInt32Parameter`（旧驼峰拼法）已弃用，手册建议改用 `sim.setObjectInt32Param`；进一步地，手册标注 `sim.setObjectInt32Param` 本身也已弃用，**新代码推荐用 `sim.setObjectProperty` / 属性接口**。本仓库仍沿用 `setObjectInt32Param` 以兼容 4.10 现有脚本。

## 参见

- [[wiki/04-感知与视觉/04-02-视觉传感器]] — 视觉传感器原理与使用
- [[wiki/02-仿真建模/02-01-分层命名场景构建]] — 本仓库场景构建模式
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
- [[wiki/01-API参考/api/api-simHandleVisionSensor]] — 显式触发视觉传感器渲染
- [[wiki/01-API参考/api/api-simGetVisionSensorImage]] — 读取 RGB 图像
- [[wiki/01-API参考/api/api-simGetVisionSensorDepthBuffer]] — 读取深度缓冲
- 官方手册 `regularApi/simCreateVisionSensor.htm`、`objectParameterIDs.htm`、`apiConstants.htm`
