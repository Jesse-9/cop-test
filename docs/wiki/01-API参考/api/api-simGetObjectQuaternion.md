---
title: sim.getObjectQuaternion
type: api
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/初级
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simGetObjectQuaternion.htm
related:
  - "[[wiki/01-API参考/api/api-simSetObjectQuaternion]]"
  - "[[wiki/01-API参考/api/api-simGetObjectOrientation]]"
  - "[[wiki/01-API参考/api/api-simGetObjectPose]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
sim_version: "4.10"
---

# sim.getObjectQuaternion

> 获取对象相对于指定参考系的朝向四元数（4 元素 `[qx qy qz qw]`，单位四元数无单位）。

## 签名

```python
list quaternion = sim.getObjectQuaternion(int objectHandle,
                                          int relativeToObjectHandle = sim.handle_world)
```

## 参数

- `objectHandle`：对象句柄。可与以下标志组合（按位或）：
  - `sim.handleflag_wxyzquat` —— 将四元数以 `[qw qx qy qz]` 顺序返回（默认是 `[qx qy qz qw]`）。
  - `sim.handleflag_reljointbaseframe` — 见下条说明。
- `relativeToObjectHandle`：参考系，取值：
  - `sim.handle_world` —— 绝对朝向
  - `sim.handle_parent` —— 相对父对象参考系
  - 任意对象句柄 —— 相对该对象参考系
  - 若为 **joint 句柄**：返回相对该关节**运动坐标系**的四元数；若 `objectHandle` 同时组合了 `sim.handleflag_reljointbaseframe`，则改为相对该关节**基坐标系**。

## 返回值

- `quaternion`：`[qx, qy, qz, qw]`（默认顺序，单位四元数）。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

h = sim.getObject('/robot/base')
# 默认 [qx qy qz qw]
q = sim.getObjectQuaternion(h, sim.handle_world)
print(q)

# 以 [qw qx qy qz] 顺序取
q_wxyz = sim.getObjectQuaternion(h | sim.handleflag_wxyzquat, sim.handle_world)
```

## 参见

- [[wiki/01-API参考/api/api-simSetObjectQuaternion]] — 设置四元数
- [[wiki/01-API参考/api/api-simGetObjectOrientation]] — 以欧拉角获取朝向
- [[wiki/01-API参考/api/api-simGetObjectPose]] — 获取完整位姿（位置 + 四元数）
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
