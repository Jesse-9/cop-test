---
title: sim.getObjectPosition
type: api
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/初级
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simGetObjectPosition.htm
related:
  - "[[wiki/01-API参考/api/api-simSetObjectPosition]]"
  - "[[wiki/01-API参考/api/api-simGetObjectPose]]"
  - "[[wiki/01-API参考/api/api-simGetObjectOrientation]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
sim_version: "4.10"
---

# sim.getObjectPosition

> 获取对象相对于指定参考系的位置（3 元素 `[x y z]`，单位米）。

## 签名

```python
list position = sim.getObjectPosition(int objectHandle,
                                      int relativeToObjectHandle = sim.handle_world)
```

## 参数

- `objectHandle`：对象句柄。可与 `sim.handleflag_reljointbaseframe` 组合（见下条说明）。
- `relativeToObjectHandle`：参考系，取值：
  - `sim.handle_world` —— 绝对（世界）位置
  - `sim.handle_parent` —— 相对父对象参考系
  - 任意对象句柄 —— 相对该对象参考系
  - 若为 **joint 句柄**：返回相对该关节**运动坐标系**（moving frame）的位置；若 `objectHandle` 同时组合了 `sim.handleflag_reljointbaseframe`，则改为相对该关节**基坐标系**（base frame）。

## 返回值

- `position`：`[x, y, z]`，单位**米**。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

h = sim.getObject('/robot/base')
# 绝对位置
pos_world = sim.getObjectPosition(h, sim.handle_world)
# 相对父对象位置
pos_parent = sim.getObjectPosition(h, sim.handle_parent)
print(pos_world, pos_parent)
```

## 参见

- [[wiki/01-API参考/api/api-simSetObjectPosition]] — 设置位置
- [[wiki/01-API参考/api/api-simGetObjectPose]] — 获取位姿（位置 + 四元数）
- [[wiki/01-API参考/api/api-simGetObjectOrientation]] — 获取朝向（欧拉角）
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
