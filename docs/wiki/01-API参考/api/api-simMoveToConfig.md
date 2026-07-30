---
title: sim.moveToConfig
type: api
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/高级
created: 2026-07-24
updated: 2026-07-24
status: published
source:
  - regularApi/simMoveToConfig.htm
  - regularApi/simMoveToConfig_init.htm
  - regularApi/simMoveToConfig_step.htm
  - regularApi/simMoveToConfig_cleanup.htm
related:
  - "[[wiki/03-运动规划与控制/03-01-逆运动学-simIK]]"
  - "[[wiki/03-运动规划与控制/03-03-运动学基础-FK-IK]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
  - "[[wiki/01-API参考/api/api-simCheckIkGroup]]"
sim_version: "4.10"
---

# sim.moveToConfig

> 基于 **Ruckig 在线轨迹生成器**执行 n-DoF 关节空间平滑运动。支持笛卡尔/关节空间运动规划，在速度、加速度、急动度限制下生成时间最优轨迹并逐帧推进。本仓库机械臂运动可参考本函数实现。

## 签名

一体式（阻塞，仅可在 threaded 脚本中调用）：

```python
dict data = sim.moveToConfig(dict params)
```

三段式（推荐用于 ZMQ Remote API / 非 threaded 环境）：

```python
dict motionObject = sim.moveToConfig_init(dict params)
int res, dict data = sim.moveToConfig_step(dict motionObject)
sim.moveToConfig_cleanup(dict motionObject)
```

## 参数（params 字典）

- `pos`：当前构型，每自由度一个值（与 `joints` 二选一）。
- `joints`：关节句柄数组（与 `pos` 二选一；提供时函数自行读取当前构型）。
- `targetPos`：目标构型，每自由度一个值。
- `flags`（可选）：Ruckig 标志，`-1` 为默认。
- `vel` / `accel`（可选）：当前速度 / 加速度，缺省为 `[0, 0, ...]`。
- `maxVel` / `minVel`（可选）：每自由度最大 / 最小速度，缺省 `±inf`。
- `maxAccel` / `minAccel`（可选）：每自由度最大 / 最小加速度，缺省 `±inf`。
- `maxJerk`（可选）：每自由度最大急动度，缺省 `inf`。
- `targetVel`（可选）：目标速度，缺省 `[0, 0, ...]`。
- `callback`（可选）：每步回调，入参为当前运动状态。
- `auxData`（可选）：透传给回调的数据。
- `cyclicJoints`（可选）：布尔数组标记哪些关节为周期关节（取最短路径方向运动），缺省全 `false`。
- `timeStep`（可选）：期望步长，`0` 表示用当前仿真步长，缺省 `0`。

> `maxVel`/`maxAccel`/`maxJerk` 三者中至少需提供一组限制，否则轨迹无界。

## 返回值

- 一体式 `data` / 三段式 `_step` 的 `data`：当前运动状态字典（含 `pos`、`vel`、`accel`、`targetPos` 等）。
- `_step` 的 `res`：
  - 负数 → 出错
  - `0` → 本步成功，运动进行中
  - `1` → 本步成功，运动结束
  - `2` → 运动被中止

## 说明

- **适用场景**：`sim.moveToConfig` 在**关节空间**做平滑运动；`sim.moveToPose`（姊妹函数）在**笛卡尔空间**做平滑运动。两者底层都是 Ruckig，生成时间最优、加速度/急动度受限的轨迹，适合工业机械臂的点到点运动。
- **阻塞 vs 三段式**：一体式 `sim.moveToConfig` 是阻塞调用，**只能在 thread 脚本里使用**。ZMQ Remote API 客户端（Python 外部控制，如本仓库）应使用 `_init`/`_step`/`_cleanup` 三段式，配合 `sim.setStepping(True)` 的 stepping 模式逐帧驱动，这正是本仓库 `FactoryController` 单步循环的做法。
- **单位**：关节位置 revolute 为 rad、prismatic 为 m；速度 rad/s 与 m/s；加速度、急动度为对应导数单位。整体遵循米、千克、秒、弧度全局约定。

## 示例（三段式 stepping 完整示例）

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

# 6 轴 UR5 的关节句柄
joints = [sim.getObject(f'/robot/joint{i}') for i in range(1, 7)]

sim.startSimulation()
sim.setStepping(True)

# 初始化一个到目标构型的运动对象
motion = sim.moveToConfig_init({
    'joints': joints,                 # 由函数自行读取当前构型作为 pos
    'targetPos': [0, -1.57, 1.57, 0, 1.57, 0],   # rad
    'maxVel': [0.5]*6,                # rad/s
    'maxAccel': [0.5]*6,              # rad/s^2
    'maxJerk': [0.5]*6,               # rad/s^3
    'timeStep': 0.05,                 # 与 SIM_STEP 一致
})

# 三段式循环：推进物理引擎 + 轨迹步进，直到运动结束
while True:
    res, data = sim.moveToConfig_step(motion)
    # data['pos'] 为本步应设置的关节构型（rad）
    for j, v in zip(joints, data['pos']):
        sim.setJointTargetPosition(j, v)
    sim.step()                        # 推进物理引擎
    if res != 0:                      # 1=完成, 2=中止, <0=错误
        break

sim.moveToConfig_cleanup(motion)
sim.setStepping(False)
sim.stopSimulation()
print('运动结束' if res == 1 else f'异常退出 res={res}')
```

## 附录：相关运动函数

| 函数 | 作用 |
|------|------|
| `sim.moveToConfig` | 关节空间平滑运动（本条目，阻塞一体式） |
| `sim.moveToConfig_init` / `_step` / `_cleanup` | 三段式拆分（推荐外部 Python 用） |
| `sim.moveToPose` / `sim.moveToPose_init` / `_step` / `_cleanup` | 笛卡尔空间平滑运动姊妹函数 |
| `sim.ruckigPos` | 底层 Ruckig 单步位置轨迹原语 |

## 参见

- [[wiki/03-运动规划与控制/03-01-逆运动学-simIK]] — IK 求解（笛卡尔目标→关节构型，常与 moveToConfig 配合：先 IK 解目标构型，再 moveToConfig 平滑到达）
- [[wiki/03-运动规划与控制/03-03-运动学基础-FK-IK]] — FK/IK 概念
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
- [[wiki/01-API参考/api/api-simCheckIkGroup]] — 旧 IK group 求解（已弃用）
