---
title: sim.getObjectPose
type: api
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/初级
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simGetObjectPose.htm
related:
  - "[[wiki/01-API参考/api/api-simSetObjectPose]]"
  - "[[wiki/01-API参考/api/api-simGetObjectPosition]]"
  - "[[wiki/01-API参考/api/api-simGetObjectQuaternion]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
sim_version: "4.10"
---

# sim.getObjectPose

> 获取对象相对于指定参考系的位姿（7 元素 `[x y z qx qy qz qw]`：位置单位米，四元数无单位）。

## 签名

```python
list pose = sim.getObjectPose(int objectHandle,
                              int relativeToObjectHandle = sim.handle_world)
```

## 参数

- `objectHandle`：对象句柄。可与以下标志组合（按位或）：
  - `sim.handleflag_wxyzquat` —— 将四元数以 `[qw qx qy qz]` 顺序返回（默认是 `[qx qy qz qw]`）。
  - `sim.handleflag_reljointbaseframe` —— 见下条说明。
- `relativeToObjectHandle`：参考系，取值：
  - `sim.handle_world` —— 绝对位姿
  - `sim.handle_inverse` —— **绝对位姿的逆**（即该对象参考系到世界系的变换）
  - `sim.handle_parent` —— 相对父对象参考系
  - 任意对象句柄 —— 相对该对象参考系
  - 若为 **joint 句柄**：返回相对该关节**运动坐标系**的位姿；若 `objectHandle` 同时组合了 `sim.handleflag_reljointbaseframe`，则改为相对该关节**基坐标系**。

## 返回值

- `pose`：`[x, y, z, qx, qy, qz, qw]`。前 3 个为位置（**米**），后 4 个为单位四元数。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

h = sim.getObject('/robot/base')
# 默认 [x y z qx qy qz qw]
pose = sim.getObjectPose(h, sim.handle_world)
print(pose)

# 以 [qw qx qy qz] 顺序取四元数
pose_wxyz = sim.getObjectPose(h | sim.handleflag_wxyzquat, sim.handle_world)

# 取绝对位姿的逆（对象→世界变换）
pose_inv = sim.getObjectPose(h, sim.handle_inverse)
```

## 参见

- [[wiki/01-API参考/api/api-simSetObjectPose]] — 设置位姿
- [[wiki/01-API参考/api/api-simGetObjectPosition]] — 仅位置
- [[wiki/01-API参考/api/api-simGetObjectQuaternion]] — 仅四元数
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览

### 附录式参见（matrix 系列）

位姿的矩阵形式可参考手册 `regularApi/` 下的矩阵函数：

- `sim.getObjectMatrix` / `sim.setObjectMatrix` — 用 3×4 或 4×4 矩阵读写对象位姿
- `sim.buildMatrix` / `sim.buildPose` — 由位姿/欧拉角构造矩阵
- `sim.getInvertMatrix` / `sim.multiplyMatrices` / `sim.interpolateMatrices` — 矩阵运算
- `sim.getEulerAnglesFromMatrix` — 从矩阵取欧拉角

> matrix 系列同样位于 Regular API 的 "positions and orientations" 分组，适合需要做多次级联变换的场景。
