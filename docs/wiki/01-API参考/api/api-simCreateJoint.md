---
title: sim.createJoint
type: api
tags: [coppeliasim, 版本/v4.10, 类别/API, 难度/中级]
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simCreateJoint.htm
related:
  - "[[wiki/02-仿真建模/02-03-关节与关节模式]]"
  - "[[wiki/02-仿真建模/02-01-分层命名场景构建]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
  - "[[wiki/01-API参考/api/api-simSetObjectPosition]]"
  - "[[wiki/01-API参考/api/api-simSetJointPosition]]"
sim_version: "4.10"
---

# sim.createJoint

> 创建一个关节（Joint）。关节是连接两个对象并限制其相对运动的运动副，是机械臂、夹爪、传送带运动建模的核心原语。本仓库机械臂动画由 Python 关键帧插值驱动，关节对象本身用作运动副占位。

## 签名

```python
int jointHandle = sim.createJoint(int jointType, int jointMode, int options, list sizes = None)
```

## 参数

- `jointType`：关节类型（运动副），取值（见 `apiConstants.htm`）：
  - `sim.joint_revolute` — 旋转副（单轴转动）
  - `sim.joint_prismatic` — 移动副（单轴平移）
  - `sim.joint_spherical` — 球面副（三轴转动）
- `jointMode`：关节模式，决定运动学/动力学行为（见下方「关节模式」）。
- `options`：保留位，设 `0`。
- `sizes`：`[length, diameter]`，关节的长度与直径（米），用于可视化。`None` 用默认值。

### 关节模式（jointMode，见 `apiConstants.htm`）

| 常量 | 含义 |
|------|------|
| `sim.jointmode_kinematic` | 运动学模式：直接设定位姿，不参与动力学求解 |
| `sim.jointmode_dependent` | 依存模式：跟随主关节运动 |
| `sim.jointmode_dynamic` | 动力学模式：由物理引擎驱动，需配合力/速度控制 |

## 返回值

- `jointHandle`：新建关节的对象句柄。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

# 1. 创建一个旋转副关节（运动学模式），尺寸 [0.2m 长, 0.05m 直径]
joint = sim.createJoint(sim.joint_revolute, sim.jointmode_kinematic, 0, [0.2, 0.05])
sim.setObjectAlias(joint, 'Island_A_Joint1')

# 2. 定位并设为竖直绕 Z 轴
sim.setObjectPosition(joint, [0.8, 0.6, 0.3], sim.handle_world)
sim.setObjectOrientation(joint, [0, 0, 0], sim.handle_world)

# 3. 运行时改用动力学模式（附录 sim.setJointMode）
sim.setJointMode(joint, sim.jointmode_dynamic)

# 4. 验证
print(sim.getObject('/Island_A_Joint1') == joint)  # True
```

## 附录：sim.setJointMode

```python
sim.setJointMode(int jointHandle, int jointMode)
```

运行时修改关节模式，取值同上表。常用于切换「示教运动学」与「物理动力学」两种控制风格。参见 `regularApi/simSetJointMode.htm`、`sim.getJointMode`、`sim.setJointDependency`。

## 参见

- [[wiki/02-仿真建模/02-03-关节与关节模式]] — 关节类型与模式详解
- [[wiki/02-仿真建模/02-01-分层命名场景构建]] — 本仓库场景构建模式
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览（Python/Lua/C 三语言）
- [[wiki/01-API参考/api/api-simSetJointPosition]] — 设置关节位置
- 官方手册 `regularApi/simCreateJoint.htm`、`regularApi/simSetJointMode.htm`、`apiConstants.htm`
