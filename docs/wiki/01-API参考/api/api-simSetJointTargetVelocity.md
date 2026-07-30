---
title: sim.setJointTargetVelocity
type: api
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/初级
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simSetJointTargetVelocity.htm
related:
  - "[[wiki/01-API参考/api/api-simGetJointTargetVelocity]]"
  - "[[wiki/01-API参考/api/api-simSetJointTargetPosition]]"
  - "[[wiki/01-API参考/api/api-simGetJointMode]]"
  - "[[wiki/02-仿真建模/02-03-关节与关节模式]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
sim_version: "4.10"
---

# sim.setJointTargetVelocity

> 设置非球关节的目标速度。kinematic 模式按运动 profile 运动到对应速度；dynamic velocity-control 模式通知控制器期望速度。

## 签名

```python
sim.setJointTargetVelocity(int objectHandle, float targetVelocity, float[] motionParams = [])
```

## 参数

- `objectHandle`：关节对象句柄。非球关节。
- `targetVelocity`：目标速度。**回转关节单位为弧度/秒，移动关节单位为米/秒**。
- `motionParams`（可选）：运动参数数组。
  - **kinematic 模式**：`[maxAccel, maxJerk]`，限制 profile 的最大加速度/jerk。
  - **dynamic 模式 + motion profile control 启用**：同上两项。
  - 传 `None`/`nil`/空数组使用默认值（见关节 `maxVelAccelJerk` 属性）。

## 返回值

无。

## 模式行为说明

| 关节模式 | `setJointTargetVelocity` 行为 |
|---------|------------------------------|
| **kinematic** | 关节按运动 profile（受 `motionParams` 中的 maxAccel/Jerk 约束）平滑到达目标速度。 |
| **dynamic + velocity control** | 通知控制器期望速度，由动力学引擎驱动关节。若启用了 motion profile，同样受 `motionParams` 约束。 |
| **dynamic + position control** | 不适用（应使用 [[wiki/01-API参考/api/api-simSetJointTargetPosition]]）。 |
| **dependent** | 不适用。 |

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

joint = sim.getObject('/robot/joint1')
# kinematic 模式：平滑加速到 0.5 rad/s，限加速度 2 rad/s²、jerk 10 rad/s³
sim.setJointTargetVelocity(joint, 0.5, [2.0, 10.0])

# 使用默认 motionParams
sim.setJointTargetVelocity(joint, 0.0)
```

## 参见

- [[wiki/01-API参考/api/api-simGetJointTargetVelocity]] — 读取目标速度
- [[wiki/01-API参考/api/api-simSetJointTargetPosition]] — 设置目标位置（position 控制模式）
- [[wiki/01-API参考/api/api-simGetJointMode]] — 读取关节模式
- [[wiki/02-仿真建模/02-03-关节与关节模式]] — 关节与关节模式概念
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
