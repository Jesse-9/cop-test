---
title: sim.setJointMode
type: api
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/中级
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simSetJointMode.htm
related:
  - "[[wiki/01-API参考/api/api-simGetJointMode]]"
  - "[[wiki/01-API参考/api/api-simSetJointTargetPosition]]"
  - "[[wiki/01-API参考/api/api-simSetJointTargetVelocity]]"
  - "[[wiki/02-仿真建模/02-03-关节与关节模式]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
sim_version: "4.10"
---

# sim.setJointMode

> 设置关节的操作模式。模式决定后续 `setJointTargetPosition/Velocity/Force` 的实际行为。

## 签名

```python
sim.setJointMode(int jointHandle, int jointMode)
```

## 参数

- `jointHandle`：关节对象句柄。
- `jointMode`：关节模式常量，取值为以下三者之一：

| 常量 | 模式 | 说明 |
|------|------|------|
| `sim.jointmode_kinematic` | kinematic | 运动学模式。关节按运动 profile 直接设位/速度，不参与动力学。 |
| `sim.jointmode_dependent` | dependent | 依赖模式。被动跟随主关节（配合 `sim.setJointDependency` 设置依赖关系）。 |
| `sim.jointmode_dynamic` | dynamic | 动力学模式。由物理引擎驱动，需通过 `dynCtrlMode` 属性指定控制器（position/velocity/force/custom）。 |

## 返回值

无。

## 说明

切换模式会影响后续目标值 API 的行为：

| 模式 | 适用目标 API | 不适用 |
|------|--------------|--------|
| kinematic | `setJointTargetPosition`（profile 平滑）/ `setJointTargetVelocity`（profile 平滑）/ `setJointPosition`（瞬时设位） | 力控类 |
| dependent | 无（被动） | 所有目标值 API |
| dynamic | `setJointTargetPosition`（通知控制器）/ `setJointTargetVelocity`（velocity 控制器）/ `setJointTargetForce`（force 控制器） | — |

dynamic 模式的具体控制器子类型由关节对象的 `dynCtrlMode` 属性决定（position / velocity / force / custom / spring 等）。详见 [[wiki/02-仿真建模/02-03-关节与关节模式]]。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

joint = sim.getObject('/robot/joint1')
# 切换为动力学模式（后续由物理引擎驱动）
sim.setJointMode(joint, sim.jointmode_dynamic)
# 切换为运动学模式（后续按 profile 设位）
sim.setJointMode(joint, sim.jointmode_kinematic)
```

## 附录：关节区间（range）辅助函数

关节的运动范围（cyclic 与区间）通过以下函数读取或设置，不单独成篇，归并于此：

| 函数 | 签名 | 说明 |
|------|------|------|
| `sim.getJointInterval` | `bool cyclic, list interval = sim.getJointInterval(int objectHandle)` | 读取关节区间。`cyclic=True` 表示关节在 -π 到 +π 间循环（此时 `interval` 无意义）。`interval[0]` 为下限，`interval[1]` 为范围（上限 = 下限 + 范围）。 |
| `sim.setJointInterval` | `sim.setJointInterval(int objectHandle, bool cyclic, list interval)` | 设置关节区间。`cyclic` 仅对 lead=0 的回转关节可设为 True。`interval = [下限, 范围]`。区间属性可能因关节类型而无效果。 |

回转关节区间单位为**弧度**，移动关节区间单位为**米**。

## 参见

- [[wiki/01-API参考/api/api-simGetJointMode]] — 读取关节模式
- [[wiki/01-API参考/api/api-simSetJointTargetPosition]] — 设置目标位置（行为随模式而异）
- [[wiki/01-API参考/api/api-simSetJointTargetVelocity]] — 设置目标速度（行为随模式而异）
- [[wiki/02-仿真建模/02-03-关节与关节模式]] — 关节与关节模式概念
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
