---
title: sim.setObjectQuaternion
type: api
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/初级
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simSetObjectQuaternion.htm
related:
  - "[[wiki/01-API参考/api/api-simGetObjectQuaternion]]"
  - "[[wiki/01-API参考/api/api-simSetObjectOrientation]]"
  - "[[wiki/01-API参考/api/api-simSetObjectPose]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
sim_version: "4.10"
---

# sim.setObjectQuaternion

> 设置对象的朝向四元数（4 元素 `[qx qy qz qw]`）。对动态仿真对象连同其层级子树一并做动力学重置（静态 shape 除外）。

## 签名

```python
sim.setObjectQuaternion(int objectHandle, list quaternion,
                        int relativeToObjectHandle = sim.handle_world)
```

## 参数

- `objectHandle`：对象句柄。可与以下标志组合（按位或）：
  - `sim.handleflag_wxyzquat` —— 表示传入的四元数为 `[qw qx qy qz]` 顺序（默认 `[qx qy qz qw]`）。
  - `sim.handleflag_reljointbaseframe` — 见下条说明。
- `quaternion`：`[qx, qy, qz, qw]`（默认顺序，须为单位四元数）。
- `relativeToObjectHandle`：参考系，取值：
  - `sim.handle_world` —— 设置绝对朝向
  - `sim.handle_parent` —— 相对父对象参考系
  - 任意对象句柄 —— 相对该对象参考系
  - 若为 **joint 句柄**：四元数相对该关节**运动坐标系**施加；若 `objectHandle` 同时组合了 `sim.handleflag_reljointbaseframe`，则相对该关节**基坐标系**施加。

## 返回值

无。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

h = sim.getObject('/robot/base')
# 绕 Z 转 90°，默认 [qx qy qz qw]
qz = 0.70710678
qw = 0.70710678
sim.setObjectQuaternion(h, [0.0, 0.0, qz, qw], sim.handle_world)

# 传入 [qw qx qy qz] 顺序
sim.setObjectQuaternion(h | sim.handleflag_wxyzquat,
                        [qw, 0.0, 0.0, qz], sim.handle_world)
```

## 备注

- 对动态仿真对象，设置四元数会触发其层级子树的动力学重置；静态 shape 不受此影响。
- 四元数须为单位四元数。

## 参见

- [[wiki/01-API参考/api/api-simGetObjectQuaternion]] — 获取四元数
- [[wiki/01-API参考/api/api-simSetObjectOrientation]] — 以欧拉角设置朝向
- [[wiki/01-API参考/api/api-simSetObjectPose]] — 设置完整位姿（位置 + 四元数）
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
