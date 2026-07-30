---
title: sim.interpolatePoses
type: api
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/中级
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simInterpolatePoses.htm
related:
  - "[[wiki/01-API参考/api/api-simBuildMatrix]]"
  - "[[wiki/01-API参考/api/api-simMultiplyMatrices]]"
  - "[[wiki/01-API参考/api/api-simMultiplyPoses]]"
  - "[[wiki/01-API参考/api/api-simGetObjectPose]]"
  - "[[wiki/01-API参考/api/api-simGetObjectPosition]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
sim_version: "4.10"
---

# sim.interpolatePoses

> 在两个 7 元素 pose 之间做插值（纯数学例程，不依赖场景对象）。

内部对位置分量做线性插值，对四元数分量做球面线性插值（slerp），保证输出仍为单位四元数。常用于在两个位姿关键帧之间生成平滑过渡。

## 签名

```python
list resultPose = sim.interpolatePoses(list poseIn1, list poseIn2, float interpolFactor)
float[7] resultPose = sim.interpolatePoses(float[7] poseIn1, float[7] poseIn2, float interpolFactor)
```

## 参数

- `poseIn1`：起始 pose，7 元素 `[x y z qx qy qz qw]`。
- `poseIn2`：终点 pose，同上。
- `interpolFactor`：插值系数，范围 `[0.0, 1.0]`。`0.0` → 返回 `poseIn1`，`1.0` → 返回 `poseIn2`。

## 返回值

- `resultPose`：插值后的 pose，7 元素 `[x y z qx qy qz qw]`。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient
import math

client = RemoteAPIClient()
sim = client.require('sim')

# 起点：原点、无旋转；终点：平移 (1,0,0) + 绕 Z 轴 90°
P_start = [0.0, 0.0, 0.0,  0.0, 0.0, 0.0, 1.0]
P_end = sim.buildPose([1.0, 0.0, 0.0], [0.0, 0.0, math.pi / 2])

# 在 0%、50%、100% 处取 3 个采样
for t in (0.0, 0.5, 1.0):
    p = sim.interpolatePoses(P_start, P_end, t)
    print(f"t={t}: {p}")
# t=0.0  -> [0, 0, 0, 0, 0, 0, 1]
# t=0.5  -> [0.5, 0, 0, 0, 0, ≈0.707, ≈0.707]  (绕 Z 45°)
# t=1.0  -> [1.0, 0, 0, 0, 0, ≈0.707, ≈0.707]  (绕 Z 90°)

# 对矩阵或 pose 做绕任意轴旋转（见附录）
P_rot = sim.rotateAroundAxis(P_start, [0, 0, 1], [0, 0, 0], math.pi / 4)
```

## 参见

- [[wiki/01-API参考/api/api-simBuildMatrix]] — 构造矩阵（附录 buildPose）
- [[wiki/01-API参考/api/api-simMultiplyMatrices]] — 矩阵乘法（附录 multiplyVector）
- [[wiki/01-API参考/api/api-simMultiplyPoses]] — pose 乘法
- [[wiki/01-API参考/api/api-simGetObjectPose]] — 对象 pose
- [[wiki/01-API参考/api/api-simGetObjectPosition]] — 对象位置
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览

### 附录：围绕任意轴旋转与从矩阵反推姿态

`sim.rotateAroundAxis`（`regularApi/simRotateAroundAxis.htm`）把一个 pose **或** 12 元素矩阵绕空间中一条任意轴旋转给定角度，常与 `sim.getRotationAxis` 配合构造 pose/矩阵之间的插值（比 `interpolatePoses` 更底层，可控制旋转轴）：

```python
list poseOut_matrixOut = sim.rotateAroundAxis(list poseIn_matrixIn,
                                             list axis, list axisPos, float angle)
```

- `poseIn/matrixIn`：7 元素 pose 或 12 元素矩阵。
- `axis`：旋转轴向量（绝对系方向）。
- `axisPos`：旋转轴经过的位置（绝对系）。
- `angle`：旋转角（弧度）。
- 返回与输入同类型（7 或 12 元素）。

```python
# 把原点 pose 绕过原点的 Z 轴转 45°
P = sim.rotateAroundAxis([0,0,0, 0,0,0,1], [0,0,1], [0,0,0], math.pi/4)
```

`sim.getEulerAnglesFromMatrix`（`regularApi/simGetEulerAnglesFromMatrix.htm`）从 12 元素矩阵反推 3 元素欧拉角：

```python
list eulerAngles = sim.getEulerAnglesFromMatrix(list matrix)
# 输入 [Vx0 Vy0 Vz0 P0 ...]，返回 [alpha, beta, gamma]（弧度）
```

`sim.getQuaternionFromMatrix`（`regularApi/simGetQuaternionFromMatrix.htm`）已 **Deprecated**，手册明确建议改用 `sim.matrixToPose` 后取后 4 位四元数。参考 [[wiki/01-API参考/api/api-simMultiplyPoses]] 的附录表。

```python
# 推荐做法：矩阵 → pose → 取四元数
pose = sim.matrixToPose(matrix)
qx, qy, qz, qw = pose[3:7]
```
