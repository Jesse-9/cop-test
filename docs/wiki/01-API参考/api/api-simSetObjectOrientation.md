---
title: sim.setObjectOrientation
type: api
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/初级
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simSetObjectOrientation.htm
related:
  - "[[wiki/01-API参考/api/api-simGetObjectOrientation]]"
  - "[[wiki/01-API参考/api/api-simSetObjectPose]]"
  - "[[wiki/01-API参考/api/api-simSetObjectQuaternion]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
sim_version: "4.10"
---

# sim.setObjectOrientation

> 设置对象的朝向（欧拉角 `alpha, beta, gamma`，单位弧度）。对动态仿真对象连同其层级子树一并做动力学重置（静态 shape 除外）。

## 签名

```python
sim.setObjectOrientation(int objectHandle, list eulerAngles,
                         int relativeToObjectHandle = sim.handle_world)
```

## 参数

- `objectHandle`：对象句柄。可与 `sim.handleflag_reljointbaseframe` 组合。
- `eulerAngles`：`[alpha, beta, gamma]`，单位**弧度**。
- `relativeToObjectHandle`：参考系，取值：
  - `sim.handle_world` —— 设置绝对朝向
  - `sim.handle_parent` —— 相对父对象参考系
  - 任意对象句柄 —— 相对该对象参考系
  - 若为 **joint 句柄**：朝向相对该关节**运动坐标系**施加；若 `objectHandle` 同时组合了 `sim.handleflag_reljointbaseframe`，则相对该关节**基坐标系**施加。

## 返回值

无。

## 关于欧拉角顺序

CoppeliaSim 内部采用 **alpha-beta-gamma** 欧拉角约定。从 yaw-pitch-roll 转入时可先调 `sim.yawPitchRollToAlphaBetaGamma`。详见手册 `eulerAngles.htm` 与 `orientationDialog.htm`。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

h = sim.getObject('/robot/base')
# 设置绝对朝向（绕 Z 转 90°，单位弧度）
sim.setObjectOrientation(h, [0.0, 0.0, 1.5707963], sim.handle_world)

# 从 yaw-pitch-roll 转入（如需）
alpha, beta, gamma = sim.yawPitchRollToAlphaBetaGamma(yaw, pitch, roll)
sim.setObjectOrientation(h, [alpha, beta, gamma], sim.handle_parent)
```

## 备注

- 对动态仿真对象，设置朝向会触发其层级子树的动力学重置；静态 shape 不受此影响。
- 单位弧度。

## 参见

- [[wiki/01-API参考/api/api-simGetObjectOrientation]] — 获取朝向（欧拉角）
- [[wiki/01-API参考/api/api-simSetObjectPose]] — 设置位姿（位置 + 四元数）
- [[wiki/01-API参考/api/api-simSetObjectQuaternion]] — 设置四元数
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
