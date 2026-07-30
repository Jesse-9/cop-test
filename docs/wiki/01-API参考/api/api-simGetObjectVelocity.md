---
title: sim.getObjectVelocity
type: api
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/高级
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simGetObjectVelocity.htm
related:
  - "[[wiki/03-运动规划与控制/03-01-逆运动学-simIK]]"
  - "[[wiki/03-运动规划与控制/03-03-运动学基础-FK-IK]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
  - "[[wiki/01-API参考/api/api-simCheckIkGroup]]"
sim_version: "4.10"
---

# sim.getObjectVelocity

> 获取对象在绝对坐标系下的线速度与角速度。该速度为**测量速度**（由当前仿真步与上一步的位姿差分得到），场景中所有对象均可查询。

## 签名

```python
list linearVelocity, list angularVelocity = sim.getObjectVelocity(int objectHandle)
```

## 参数

- `objectHandle`：场景对象句柄。
  ⚠ **强烈建议**将 `sim.handleflag_axis` 与对象句柄按位或（`objectHandle | sim.handleflag_axis`）传入，以获得**规则的角速度**——即以旋转轴方向表示角速度，轴长度代表转速。
  若省略该标志，返回的是相对于上一仿真帧欧拉角的导数，通常不是期望的物理角速度。

## 返回值

- `linearVelocity`：3 元素数组 `[vx, vy, vz]`，**线速度，单位 m/s**，绝对坐标系。
- `angularVelocity`：3 元素数组 `[wx, wy, wz]`，**角速度，单位 rad/s**，绝对坐标系（传入 `sim.handleflag_axis` 时为旋转轴表示）。

## 说明

- 该函数返回的是**数值差分测量值**，而非物理引擎内部的速度状态，因此对场景中所有对象（含静态对象，其速度应为 0）都可用。
- 与 legacy 的 `sim.getVelocity`（返回基于雅可比/关节速度的瞬时速度）不同，`sim.getObjectVelocity` 反映的是仿真步间实际位姿变化，适合做闭环监控、碰撞前速度检查、AGV/机械臂节拍分析。
- 单位遵循 CoppeliaSim 全局约定：米、千克、秒、弧度。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

sim.startSimulation()

agv = sim.getObject('/Mobile/AGV_Shared')

# 推进若干步让 AGV 运动起来
for _ in range(10):
    sim.step()

# 取规则角速度：务必 | sim.handleflag_axis
lin_vel, ang_vel = sim.getObjectVelocity(agv | sim.handleflag_axis)
print(f'线速度 [m/s]: {lin_vel}')   # e.g. [0.12, 0.0, 0.0]
print(f'角速度 [rad/s]: {ang_vel}')  # e.g. [0.0, 0.0, 0.01]

sim.stopSimulation()
```

本仓库 `factory_agv.py` 在 stepping 模式下按位移差分估计 AGV 航向；若需要更直接的物理量，可改用 `sim.getObjectVelocity` 读取角速度 `wz` 作为航向角速率。

## 参见

- [[wiki/03-运动规划与控制/03-01-逆运动学-simIK]] — IK 与速度层面的运动学
- [[wiki/03-运动规划与控制/03-03-运动学基础-FK-IK]] — FK/IK 概念
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
- [[wiki/01-API参考/api/api-simCheckIkGroup]] — 旧 IK group 求解（已弃用，改用 simIK）
