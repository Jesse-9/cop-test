---
title: sim.multiplyMatrices
type: api
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/中级
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simMultiplyMatrices.htm
related:
  - "[[wiki/01-API参考/api/api-simBuildMatrix]]"
  - "[[wiki/01-API参考/api/api-simMultiplyPoses]]"
  - "[[wiki/01-API参考/api/api-simInterpolatePoses]]"
  - "[[wiki/01-API参考/api/api-simGetObjectPose]]"
  - "[[wiki/01-API参考/api/api-simGetObjectPosition]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
sim_version: "4.10"
---

# sim.multiplyMatrices

> 两个 12 元素变换矩阵相乘（纯数学例程，不依赖场景对象）。

矩阵乘法语义同齐次变换的级联：`result = matrixIn1 * matrixIn2`，等价于先施加 `matrixIn2`、再施加 `matrixIn1` 的变换。

## 签名

```python
list resultMatrix = sim.multiplyMatrices(list matrixIn1, list matrixIn2)
float[12] resultMatrix = sim.multiplyMatrices(float[12] matrixIn1, float[12] matrixIn2)
```

## 参数

- `matrixIn1`：第一个输入矩阵，12 元素 `[Vx0 Vy0 Vz0 P0  Vx1 Vy1 Vz1 P1  Vx2 Vy2 Vz2 P2]`。
- `matrixIn2`：第二个输入矩阵，同上布局。

## 返回值

- `resultMatrix`：乘积矩阵，12 元素，布局同输入。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient
import math

client = RemoteAPIClient()
sim = client.require('sim')

# 两个变换：T1 = 平移 (1,0,0)，T2 = 绕 Z 轴 90°
T1 = sim.buildMatrix([1.0, 0.0, 0.0], [0.0, 0.0, 0.0])
T2 = sim.buildMatrix([0.0, 0.0, 0.0], [0.0, 0.0, math.pi / 2])

# 先旋转 90° 再平移 (1,0,0)：点 (0,0,0) → (1,0,0)，再随坐标系旋转 → (0,1,0) 方向
T = sim.multiplyMatrices(T1, T2)
print(T)

# 验证：把世界系 (1,0,0) 用 T 变换
v = sim.multiplyVector(T, [1.0, 0.0, 0.0])
print(v)

# 矩阵 → pose / pose → 矩阵
pose = sim.matrixToPose(T)
m2 = sim.poseToMatrix(pose)
```

## 参见

- [[wiki/01-API参考/api/api-simBuildMatrix]] — 由位置+欧拉角构造矩阵
- [[wiki/01-API参考/api/api-simMultiplyPoses]] — pose 乘法
- [[wiki/01-API参考/api/api-simInterpolatePoses]] — pose 插值
- [[wiki/01-API参考/api/api-simGetObjectPose]] — 对象 pose
- [[wiki/01-API参考/api/api-simGetObjectPosition]] — 对象位置
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览

### 附录：sim.multiplyVector

同类小函数 `sim.multiplyVector`（`regularApi/simMultiplyVector.htm`）把四元数、pose 或矩阵作用到一个或多个向量上：`v_out = m * v_in`。

```python
list resultVector = sim.multiplyVector(list quaternion_pose_or_matrix, list vector)
```

- 第一个参数可以是：
  - 四元数：4 元素 `[qx qy qz qw]`
  - pose：7 元素 `[x y z qx qy qz qw]`
  - 变换矩阵：12 元素 `[Vx0 Vy0 Vz0 P0 ...]`
- `vector`：`3 * n` 个值，每 3 个为一组待变换向量。
- 返回 `resultVector`：`3 * n` 个值，与输入分组对应。

```python
# 用矩阵把 2 个向量一次变换
out = sim.multiplyVector(T, [1.0, 0.0, 0.0,  0.0, 1.0, 0.0])
print(out)  # 6 个值
```
