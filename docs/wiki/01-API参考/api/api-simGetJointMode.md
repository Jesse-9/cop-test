---
title: sim.getJointMode
type: api
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/中级
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simGetJointMode.htm
related:
  - "[[wiki/01-API参考/api/api-simSetJointMode]]"
  - "[[wiki/01-API参考/api/api-simSetJointTargetPosition]]"
  - "[[wiki/01-API参考/api/api-simSetJointTargetVelocity]]"
  - "[[wiki/02-仿真建模/02-03-关节与关节模式]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
sim_version: "4.10"
---

# sim.getJointMode

> 读取关节的操作模式（joint mode）。模式决定关节如何响应 target position/velocity/force。

## 签名

```python
int jointMode = sim.getJointMode(int jointHandle)
```

## 参数

- `jointHandle`：关节对象句柄。

## 返回值

- `jointMode`：关节模式，取值为以下常量之一：

| 常量 | 模式 | 说明 |
|------|------|------|
| `sim.jointmode_kinematic` | kinematic | 运动学模式。关节按运动 profile 直接设位/速度，不参与物理引擎动力学计算。 |
| `sim.jointmode_dependent` | dependent | 依赖模式。被动跟随另一主关节（见 `sim.setJointDependency`）。 |
| `sim.jointmode_dynamic` | dynamic | 动力学模式。由物理引擎驱动，需配合控制器（position/velocity/force/custom）。 |

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

joint = sim.getObject('/robot/joint1')
mode = sim.getJointMode(joint)

mode_names = {
    sim.jointmode_kinematic: 'kinematic',
    sim.jointmode_dependent: 'dependent',
    sim.jointmode_dynamic: 'dynamic',
}
print(f'关节模式: {mode_names.get(mode, "unknown")}')
```

## 附录：关节类型与矩阵辅助函数

以下辅助函数读取关节的类型、位姿矩阵等属性，不单独成篇，归并于此：

| 函数 | 签名 | 说明 |
|------|------|------|
| `sim.getJointType` | `int jointType = sim.getJointType(int objectHandle)` | 读取关节类型，返回 `sim.joint_revolute`（回转）/ `sim.joint_prismatic`（移动）/ `sim.joint_spherical`（球）。 |
| `sim.getJointMatrix` | `list matrix = sim.getJointMatrix(int objectHandle)` | ⚠ **已弃用**，改用 `sim.getObjectChildPose`。返回 12 元素矩阵（4×4 去末行），描述关节运动坐标系。 |
| `sim.getJointVelocity` | `float velocity = sim.getJointVelocity(int jointHandle)` | 读取关节的测量速度（从一个仿真步到下一步），适用于场景中所有关节。回转关节 rad/s，移动关节 m/s。 |

矩阵布局（`getJointMatrix`，列优先）：x 轴 `(m[0],m[4],m[8])`，y 轴 `(m[1],m[5],m[9])`，z 轴 `(m[2],m[6],m[10])`，平移 `(m[3],m[7],m[11])`。

## 参见

- [[wiki/01-API参考/api/api-simSetJointMode]] — 设置关节模式
- [[wiki/01-API参考/api/api-simSetJointTargetPosition]] — 目标位置（行为随模式而异）
- [[wiki/01-API参考/api/api-simSetJointTargetVelocity]] — 目标速度（行为随模式而异）
- [[wiki/02-仿真建模/02-03-关节与关节模式]] — 关节与关节模式概念
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
