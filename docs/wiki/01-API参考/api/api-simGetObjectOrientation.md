---
title: sim.getObjectOrientation
type: api
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/初级
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simGetObjectOrientation.htm
related:
  - "[[wiki/01-API参考/api/api-simSetObjectOrientation]]"
  - "[[wiki/01-API参考/api/api-simGetObjectPose]]"
  - "[[wiki/01-API参考/api/api-simGetObjectQuaternion]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
sim_version: "4.10"
---

# sim.getObjectOrientation

> 获取对象相对于指定参考系的朝向（欧拉角 `[alpha beta gamma]`，单位弧度）。

## 签名

```python
list eulerAngles = sim.getObjectOrientation(int objectHandle,
                                            int relativeToObjectHandle = sim.handle_world)
```

## 参数

- `objectHandle`：对象句柄。可与 `sim.handleflag_reljointbaseframe` 组合（见下条说明）。
- `relativeToObjectHandle`：参考系，取值：
  - `sim.handle_world` —— 绝对朝向
  - `sim.handle_parent` —— 相对父对象参考系
  - 任意对象句柄 —— 相对该对象参考系
  - 若为 **joint 句柄**：返回相对该关节**运动坐标系**的朝向；若 `objectHandle` 同时组合了 `sim.handleflag_reljointbaseframe`，则改为相对该关节**基坐标系**。

## 返回值

- `eulerAngles`：`[alpha, beta, gamma]`，单位**弧度**。

## 关于欧拉角顺序

CoppeliaSim 内部采用 **alpha-beta-gamma** 欧拉角约定（3 个角度依次施加），与常见的 yaw-pitch-roll 不同。若需要互转，可用 `sim.alphaBetaGammaToYawPitchRoll` / `sim.yawPitchRollToAlphaBetaGamma`（详见手册 `eulerAngles.htm` → "Positions and orientations" 与 `orientationDialog.htm`）。Orientation 对话框的 Alpha/Beta/Gamma 三栏即此约定。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

h = sim.getObject('/robot/base')
# 绝对朝向（弧度）
euler = sim.getObjectOrientation(h, sim.handle_world)
print(euler)  # e.g. [0.0, 0.0, 1.5708]

# 转换为 yaw-pitch-roll（如需）
yaw, pitch, roll = sim.alphaBetaGammaToYawPitchRoll(*euler)
```

## 参见

- [[wiki/01-API参考/api/api-simSetObjectOrientation]] — 设置朝向（欧拉角）
- [[wiki/01-API参考/api/api-simGetObjectPose]] — 获取位姿（位置 + 四元数）
- [[wiki/01-API参考/api/api-simGetObjectQuaternion]] — 获取四元数
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
