---
title: sim.setJointTargetPosition
type: api
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/初级
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simSetJointTargetPosition.htm
related:
  - "[[wiki/01-API参考/api/api-simGetJointTargetPosition]]"
  - "[[wiki/01-API参考/api/api-simSetJointPosition]]"
  - "[[wiki/01-API参考/api/api-simSetJointTargetVelocity]]"
  - "[[wiki/01-API参考/api/api-simGetJointMode]]"
  - "[[wiki/02-仿真建模/02-03-关节与关节模式]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
sim_version: "4.10"
---

# sim.setJointTargetPosition

> 设置关节的目标位置。行为随关节模式而异：kinematic 模式按运动 profile 平滑运动；dynamic position-control 模式通知控制器目标位置。

## 签名

```python
sim.setJointTargetPosition(int objectHandle, float targetPosition, float[] motionParams = [])
```

## 参数

- `objectHandle`：关节对象句柄。
- `targetPosition`：目标位置。**回转关节单位为弧度，移动关节单位为米**。
- `motionParams`（可选）：运动参数数组。
  - **kinematic 模式**：`[maxVel, maxAccel, maxJerk]`，限制运动 profile 的最大速度/加速度/jerk。
  - **dynamic 模式 + position control + motion profile 启用**：同上三项。
  - 传 `None`/`nil`/空数组使用默认值（见关节 `maxVelAccelJerk` 属性）。

## 返回值

无。

## 模式行为说明

| 关节模式 | `setJointTargetPosition` 行为 |
|---------|------------------------------|
| **kinematic** | 关节按运动 profile（受 `motionParams` 中的 maxVel/Accel/Jerk 约束）平滑运动到目标位置。与 [[wiki/01-API参考/api/api-simSetJointPosition]] 的瞬时设位不同。 |
| **dynamic + position/custom control** | 通知控制器期望位置，由动力学引擎驱动关节。若启用了 motion profile，同样受 `motionParams` 约束。 |
| **dynamic + velocity control** | 不适用（应使用 [[wiki/01-API参考/api/api-simSetJointTargetVelocity]]）。 |
| **dependent** | 不适用（被动跟随主关节）。 |

kinematic profile 是梯形/SCURVE 风格的运动规划，保证速度/加速度/jerk 连续，适合需要平滑关节运动的场景。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

joint = sim.getObject('/robot/joint1')
# kinematic 模式：按 profile 运动到 1.0 rad，限速 0.5 rad/s、加速度 2 rad/s²、jerk 10 rad/s³
sim.setJointTargetPosition(joint, 1.0, [0.5, 2.0, 10.0])

# 使用默认 motionParams
sim.setJointTargetPosition(joint, 0.0)
```

## 附录：关节力/力矩辅助函数

dynamic 模式下，关节的目标力/力矩通过以下函数设置或读取。这些函数不单独成篇，归并于此：

| 函数 | 签名 | 说明 |
|------|------|------|
| `sim.setJointTargetForce` | `sim.setJointTargetForce(int objectHandle, float forceOrTorque, bool signedValue = true)` | 设置关节可施加的最大力/力矩。`signedValue=True` 时符号表示期望运动方向。适用于 dynamic force-control 模式。 |
| `sim.getJointTargetForce` | `float forceOrTorque = sim.getJointTargetForce(int jointHandle)` | 读取关节的目标最大力/力矩，符号表期望运动方向。 |
| `sim.getJointForce` | `float forceOrTorque = sim.getJointForce(int jointHandle)` | 读取关节沿/绕其 z 轴实际施加的力/力矩。**仅当关节为 prismatic/revolute 且动态启用时有意义**。Bullet/MuJoCo/Newton 引擎返回电机力/力矩（不含限位力矩）；ODE/Vortex 返回总力/力矩。句柄可组合 `sim.handleflag_rawvalue` 读取每个物理子步的原始值（仅在物理引擎回调中可用）。 |
| `sim.setJointMaxForce` | `sim.setJointMaxForce(int objectHandle, float forceOrTorque)` | ⚠ **已弃用**，改用 `sim.setJointTargetForce`。 |

力/力矩单位为**牛顿（N）**（prismatic 关节）或**牛顿·米（N·m）**（revolute 关节）。

## 参见

- [[wiki/01-API参考/api/api-simGetJointTargetPosition]] — 读取目标位置
- [[wiki/01-API参考/api/api-simSetJointPosition]] — 瞬时设位（不走 profile）
- [[wiki/01-API参考/api/api-simSetJointTargetVelocity]] — 设置目标速度（velocity 控制模式）
- [[wiki/01-API参考/api/api-simGetJointMode]] — 读取关节模式
- [[wiki/02-仿真建模/02-03-关节与关节模式]] — 关节与关节模式概念
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
