---
title: sim.multiplyPoses
type: api
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/中级
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simMultiplyPoses.htm
related:
  - "[[wiki/01-API参考/api/api-simBuildMatrix]]"
  - "[[wiki/01-API参考/api/api-simMultiplyMatrices]]"
  - "[[wiki/01-API参考/api/api-simInterpolatePoses]]"
  - "[[wiki/01-API参考/api/api-simGetObjectPose]]"
  - "[[wiki/01-API参考/api/api-simGetObjectPosition]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
sim_version: "4.10"
---

# sim.multiplyPoses

> 两个 7 元素 pose 相乘（纯数学例程，不依赖场景对象）。

CoppeliaSim 的 **pose** 是 7 元素紧凑表示 `[x y z qx qy qz qw]`：前 3 个为位置（米），后 4 个为单位四元数。`multiplyPoses` 等价于两个齐次变换的级联：`result = poseIn1 * poseIn2`，即先施加 `poseIn2`、再施加 `poseIn1`。与矩阵形式相比，pose 更省内存且无奇异，是 `sim.getObjectPose`/`sim.setObjectPose` 等对象 API 的原生数据格式。

## 签名

```python
list resultPose = sim.multiplyPoses(list poseIn1, list poseIn2)
float[7] resultPose = sim.multiplyPoses(float[7] poseIn1, float[7] poseIn2)
```

## 参数

- `poseIn1`：第一个输入 pose，7 元素 `[x y z qx qy qz qw]`。
- `poseIn2`：第二个输入 pose，同上。

## 返回值

- `resultPose`：乘积 pose，7 元素 `[x y z qx qy qz qw]`。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient
import math

client = RemoteAPIClient()
sim = client.require('sim')

# P1：平移 (1,0,0) + 单位四元数；P2：原点 + 绕 Z 轴 90°
P1 = [1.0, 0.0, 0.0,  0.0, 0.0, 0.0, 1.0]            # 无旋转
P2 = sim.buildPose([0.0, 0.0, 0.0], [0.0, 0.0, math.pi / 2])

# 级联：先旋转 90° 再平移到 (1,0,0) → 终态位置应在 (0,1,0) 附近
P = sim.multiplyPoses(P1, P2)
print(P)  # [≈0, ≈1, 0, qx, qy, qz, qw]

# pose 与矩阵互转
m = sim.poseToMatrix(P)
P_back = sim.matrixToPose(m)
```

## 参见

- [[wiki/01-API参考/api/api-simBuildMatrix]] — 构造矩阵（附录 buildPose）
- [[wiki/01-API参考/api/api-simMultiplyMatrices]] — 矩阵乘法（附录 multiplyVector）
- [[wiki/01-API参考/api/api-simInterpolatePoses]] — pose 插值
- [[wiki/01-API参考/api/api-simGetObjectPose]] — 对象 pose
- [[wiki/01-API参考/api/api-simGetObjectPosition]] — 对象位置
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览

### 附录：pose ↔ matrix 互转

CoppeliaSim 4.10 没有 `sim.getPoseFromMatrix` / `sim.getMatrixFromPose` 这两个旧名，统一用 `sim.matrixToPose` 与 `sim.poseToMatrix`（`regularApi/simMatrixToPose.htm`、`regularApi/simPoseToMatrix.htm`）：

| 函数 | 输入 | 输出 |
|------|------|------|
| `sim.matrixToPose(matrix)` | 12 元素矩阵 | 7 元素 pose `[x y z qx qy qz qw]` |
| `sim.poseToMatrix(pose)` | 7 元素 pose | 12 元素矩阵 `[Vx0 Vy0 Vz0 P0 ...]` |

```python
pose = sim.matrixToPose(m)       # 矩阵 → pose
matrix = sim.poseToMatrix(pose)  # pose → 矩阵
```

> 旧版手册中的 `sim.getQuaternionFromMatrix` 已标注 **Deprecated**，请改用 `sim.matrixToPose` 再取后 4 位四元数。
