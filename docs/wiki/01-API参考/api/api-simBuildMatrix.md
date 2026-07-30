---
title: sim.buildMatrix
type: api
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/中级
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simBuildMatrix.htm
related:
  - "[[wiki/01-API参考/api/api-simMultiplyMatrices]]"
  - "[[wiki/01-API参考/api/api-simMultiplyPoses]]"
  - "[[wiki/01-API参考/api/api-simInterpolatePoses]]"
  - "[[wiki/01-API参考/api/api-simGetObjectPose]]"
  - "[[wiki/01-API参考/api/api-simGetObjectPosition]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
sim_version: "4.10"
---

# sim.buildMatrix

> 由位置向量和欧拉角构造 3×4 变换矩阵（12 元素，纯数学例程，不依赖场景对象）。

CoppeliaSim 内部用 12 元素的行优先矩阵表示刚体变换，相当于 4×4 齐次矩阵去掉末行 `[0 0 0 1]`。12 元素按列排列为三列基向量加一列平移：

```
[Vx0 Vy0 Vz0 P0   Vx1 Vy1 Vz1 P1   Vx2 Vy2 Vz2 P2]
 └─ 列0：X 轴 + px ┘  └─ 列1：Y 轴 + py ┘  └─ 列2：Z 轴 + pz ┘
```

即 `[ax ay az px  bx by bz py  cx cy cz pz]`。

## 签名

```python
list matrix = sim.buildMatrix(list position, list eulerAngles)
float[12] matrix = sim.buildMatrix(float[3] position, float[3] eulerAngles)
```

## 参数

- `position`：3 元素位置 `[x, y, z]`，单位**米**。
- `eulerAngles`：3 元素欧拉角 `[alpha, beta, gamma]`，单位**弧度**，按 CoppeliaSim 默认欧拉角类型（`sim.getEulerAnglesFromMatrix` 对应同一约定，通常为 X-Y-Z 固定角）。

## 返回值

- `matrix`：12 元素数组 `[Vx0 Vy0 Vz0 P0  Vx1 Vy1 Vz1 P1  Vx2 Vy2 Vz2 P2]`，对应 4×4 齐次矩阵的前三行（末行隐含为 `[0 0 0 1]`）。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

# 位置 (0.5, 0.0, 0.2)，绕 Z 轴 90°
import math
pos = [0.5, 0.0, 0.2]
euler = [0.0, 0.0, math.pi / 2]

m = sim.buildMatrix(pos, euler)
print(m)
# [Vx0 Vy0 Vz0 P0  Vx1 Vy1 Vz1 P1  Vx2 Vy2 Vz2 P2]
# 对应矩阵：
#   R = [[0,-1,0],[1,0,0],[0,0,1]]   T = [0.5,0.0,0.2]

# 用 sim.multiplyVector 把一个世界系向量变换到该矩阵的局部系
v_world = [1.0, 0.0, 0.0]
v_local = sim.multiplyVector(m, v_world)
print(v_local)
```

## 参见

- [[wiki/01-API参考/api/api-simMultiplyMatrices]] — 矩阵乘法
- [[wiki/01-API参考/api/api-simMultiplyPoses]] — pose 乘法
- [[wiki/01-API参考/api/api-simInterpolatePoses]] — pose 插值
- [[wiki/01-API参考/api/api-simGetObjectPose]] — 对象 pose（7 元素）
- [[wiki/01-API参考/api/api-simGetObjectPosition]] — 对象位置
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览

### 附录：sim.buildPose

同类小函数 `sim.buildPose`（`regularApi/simBuildPose.htm`）由位置+欧拉角（或参考系坐标轴）构造 7 元素 **pose**，比 `buildMatrix` 更适合直接喂给 `sim.setObjectPose` 等 API。

```python
list pose = sim.buildPose(list position, list eulerAnglesOrAxis, int mode = 0, list axis2 = None)
```

- `position`：3 元素位置（米）。
- `eulerAnglesOrAxis1`：3 元素欧拉角（弧度）或参考系某一轴。
- `mode`：姿态指定方式：
  - `0` —— `eulerAnglesOrAxis1` 为欧拉角，`axis2` 忽略
  - `1/2/3` —— 分别表示该轴为新参考系的 **X / Y / Z** 轴
  - `4..9` —— `eulerAnglesOrAxis1` 与 `axis2` 联合指定两条轴（如 `4`：X 轴 + Y 轴）
- `axis2`：第二条轴向量（`mode >= 4` 时使用）。
- 返回 `pose`：7 元素 `[x y z qx qy qz qw]`。

```python
# 由位置+欧拉角构造 pose
pose = sim.buildPose([0.5, 0.0, 0.2], [0.0, 0.0, math.pi / 2])
# 等价于先 buildMatrix 再 matrixToPose
```
