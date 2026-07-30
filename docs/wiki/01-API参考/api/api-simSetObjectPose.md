---
title: sim.setObjectPose
type: api
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/初级
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simSetObjectPose.htm
related:
  - "[[wiki/01-API参考/api/api-simGetObjectPose]]"
  - "[[wiki/01-API参考/api/api-simSetObjectPosition]]"
  - "[[wiki/01-API参考/api/api-simSetObjectQuaternion]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
sim_version: "4.10"
---

# sim.setObjectPose

> 设置对象的位姿（7 元素 `[x y z qx qy qz qw]`）。对动态仿真对象连同其层级子树一并做动力学重置（静态 shape 除外）。

## 签名

```python
sim.setObjectPose(int objectHandle, list pose,
                  int relativeToObjectHandle = sim.handle_world)
```

## 参数

- `objectHandle`：对象句柄。可与以下标志组合（按位或）：
  - `sim.handleflag_wxyzquat` —— 表示传入的四元数为 `[qw qx qy qz]` 顺序（默认 `[qx qy qz qw]`）。
  - `sim.handleflag_reljointbaseframe` — 见下条说明。
- `pose`：`[x, y, z, qx, qy, qz, qw]`。前 3 个为位置（**米**），后 4 个为单位四元数。
- `relativeToObjectHandle`：参考系，取值：
  - `sim.handle_world` —— 设置绝对位姿
  - `sim.handle_inverse` —— 设置**绝对位姿的逆**
  - `sim.handle_parent` —— 相对父对象参考系
  - 任意对象句柄 —— 相对该对象参考系
  - 若为 **joint 句柄**：位姿相对该关节**运动坐标系**施加；若 `objectHandle` 同时组合了 `sim.handleflag_reljointbaseframe`，则相对该关节**基坐标系**施加。

## 返回值

无。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

h = sim.getObject('/robot/base')
# 默认 [x y z qx qy qz qw]：平移到 (0.5,0,0.2)，绕 Z 转 90°
qz = 0.70710678
qw = 0.70710678
sim.setObjectPose(h, [0.5, 0.0, 0.2, 0.0, 0.0, qz, qw], sim.handle_world)

# 传入 [qw qx qy qz] 顺序
sim.setObjectPose(h | sim.handleflag_wxyzquat,
                  [0.5, 0.0, 0.2, qw, 0.0, 0.0, qz], sim.handle_world)
```

## 备注

- 对动态仿真对象，设置位姿会触发其层级子树的动力学重置；静态 shape 不受此影响。
- 四元数须为单位四元数。

## 参见

- [[wiki/01-API参考/api/api-simGetObjectPose]] — 获取位姿
- [[wiki/01-API参考/api/api-simSetObjectPosition]] — 仅设置位置
- [[wiki/01-API参考/api/api-simSetObjectQuaternion]] — 仅设置四元数
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
