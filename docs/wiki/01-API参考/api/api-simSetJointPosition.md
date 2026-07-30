---
title: sim.setJointPosition
type: api
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/初级
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simSetJointPosition.htm
related:
  - "[[wiki/01-API参考/api/api-simGetJointPosition]]"
  - "[[wiki/01-API参考/api/api-simSetJointTargetPosition]]"
  - "[[wiki/01-API参考/api/api-simGetJointMode]]"
  - "[[wiki/02-仿真建模/02-03-关节与关节模式]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
sim_version: "4.10"
---

# sim.setJointPosition

> 直接设置关节的线性/角度位置。**仅在 kinematic 模式下有意义**，瞬时设位，不走运动规划；非球关节专用。

## 签名

```python
sim.setJointPosition(int objectHandle, float position)
```

## 参数

- `objectHandle`：关节对象句柄。
- `position`：关节位置。**回转关节单位为弧度，移动关节单位为米**。球关节不支持，改用 `sim.setObjectChildPose`。

## 返回值

无。

## 说明

`setJointPosition` 与 [[wiki/01-API参考/api/api-simSetJointTargetPosition]] 的关键差异：

| 行为 | `setJointPosition` | `setJointTargetPosition` |
|------|-------------------|--------------------------|
| 适用模式 | kinematic | kinematic / dynamic（position-control）/ custom |
| 运动方式 | **瞬时设位**，物理引擎下一步即生效 | kinematic 模式按 profile（maxVel/Accel/Jerk）规划；dynamic 模式通知控制器目标位置 |
| 是否走运动规划 | 否 | 是（kinematic profile） |

kinematic 模式下若希望按速度/加速度/jerk 限制平滑运动，应使用 `setJointTargetPosition`；若需要瞬态跳位（如重定位、离线设置），用本函数。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

joint = sim.getObject('/robot/joint1')
# kinematic 模式下瞬时设位至 1.57 rad (~90°)
sim.setJointPosition(joint, 1.5708)
```

## 参见

- [[wiki/01-API参考/api/api-simGetJointPosition]] — 读取位置
- [[wiki/01-API参考/api/api-simSetJointTargetPosition]] — 设置目标位置（含 profile 运动）
- [[wiki/01-API参考/api/api-simGetJointMode]] — 读取关节模式
- [[wiki/02-仿真建模/02-03-关节与关节模式]] — 关节与关节模式概念
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
