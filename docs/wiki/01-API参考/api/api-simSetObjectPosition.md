---
title: sim.setObjectPosition
type: api
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/初级
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simSetObjectPosition.htm
related:
  - "[[wiki/01-API参考/api/api-simGetObjectPosition]]"
  - "[[wiki/01-API参考/api/api-simSetObjectPose]]"
  - "[[wiki/01-API参考/api/api-simSetObjectOrientation]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
sim_version: "4.10"
---

# sim.setObjectPosition

> 设置对象的位置（`x, y, z` 坐标，单位米）。对动态仿真对象连同其层级子树一并做动力学重置（静态 shape 除外）。

## 签名

```python
sim.setObjectPosition(int objectHandle, list position,
                      int relativeToObjectHandle = sim.handle_world)
```

## 参数

- `objectHandle`：对象句柄。可与 `sim.handleflag_reljointbaseframe` 组合。
- `position`：`[x, y, z]`，单位**米**。
- `relativeToObjectHandle`：参考系，取值：
  - `sim.handle_world` —— 设置绝对位置
  - `sim.handle_parent` —— 相对父对象参考系
  - 任意对象句柄 —— 相对该对象参考系
  - 若为 **joint 句柄**：位置相对该关节**运动坐标系**施加；若 `objectHandle` 同时组合了 `sim.handleflag_reljointbaseframe`，则相对该关节**基坐标系**施加。

## 返回值

无。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

h = sim.getObject('/robot/base')
# 设置绝对位置
sim.setObjectPosition(h, [0.5, 0.0, 0.2], sim.handle_world)
# 相对父对象设置位置
sim.setObjectPosition(h, [0.1, 0.0, 0.0], sim.handle_parent)
```

## 备注

- 对动态仿真对象，设置位置会触发其层级子树的动力学重置；静态 shape 不受此影响。
- 单位米（CoppeliaSim API 统一采用米、千克、秒、弧度）。

## 参见

- [[wiki/01-API参考/api/api-simGetObjectPosition]] — 获取位置
- [[wiki/01-API参考/api/api-simSetObjectPose]] — 设置位姿（位置 + 四元数）
- [[wiki/01-API参考/api/api-simSetObjectOrientation]] — 设置朝向（欧拉角）
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
