---
title: sim.getConfigForTipPose
type: api
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/高级
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simGetConfigForTipPose.htm
related:
  - "[[wiki/03-运动规划与控制/03-01-逆运动学-simIK]]"
  - "[[wiki/03-运动规划与控制/03-03-运动学基础-FK-IK]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
  - "[[wiki/01-API参考/api/api-simCheckIkGroup]]"
sim_version: "4.10"
---

# sim.getConfigForTipPose

> 给定末端（tip）目标位姿，通过随机搜索 + IK 反解出一组匹配的关节构型。**已弃用**，官方建议改用 kinematics 插件（`simIK`）。

## 签名

```python
list jointPositions = sim.getConfigForTipPose(int ikGroupHandle,
                                              list jointHandles,
                                              float distanceThreshold,
                                              int maxTimeInMs,
                                              list[4] metric = None,
                                              list collisionPairs = None,
                                              list jointOptions = None,
                                              list lowLimits = None,
                                              list ranges = None)
```

## 参数

- `ikGroupHandle`：负责将 tip 拉到 target 的 IK group 句柄（可标记为 explicit handling）。可用 `sim.getIkGroupHandle(name)` 获取。
- `jointHandles`：需要回读 IK 结果的关节句柄数组。
- `distanceThreshold`：位姿距离阈值。随机生成的构型若 tip 与目标距离超过此值则不触发 IK，避免无效求解。值大→计算慢、解多；值小→解少。
- `maxTimeInMs`：搜索超时（毫秒），超时后放弃。
- `metric`（可选）：4 元素位姿距离度量 `[mx, my, mz, m_angle]`，距离公式为
  `sqrt((dx*mx)^2 + (dy*my)^2 + (dz*mz)^2 + (angle*m_angle)^2)`。默认 `{1,1,1,0.1}`。
- `collisionPairs`（可选）：碰撞对列表，每对 2 个实体句柄（collider, collidee）。`collidee` 可为 `sim.handle_all`。用于在搜索中剔除碰撞构型。`None` 表示不做碰撞检查。
- `jointOptions`（可选）：按位编码，Bit 0（`1`）表示对应关节依赖（dependent）于另一关节。
- `lowLimits` / `ranges`（可选）：为每个关节指定搜索子区间的下限与范围，便于只探索关节空间子集。两者须同时提供或同时为 `None`。

## 返回值

- `jointPositions`：找到解时为长度 `len(jointHandles)` 的数组（**revolute 关节为 rad，prismatic 关节为 m**）；未找到返回 `None`/空。

## 说明

- **随机化搜索**：算法反复在关节空间随机采样构型，将 tip 投影到目标附近后再跑 IK 精修，因此每次调用可能返回不同的解。
- **多解与等效构型**：若某些 revolute 关节范围 > 360°，应对每个返回构型再调 `sim.getAlternateConfigs` 生成等价位姿下的备选构型。
- **与 IK 的关系**：本函数本质是"给定 tip 目标位姿 → 调 IK group 反解关节值"的封装，IK group 的定义（tip/target 对、雅可比、阻尼等）决定了能否收敛。
- **已弃用**：4.10 起官方推荐使用 kinematics 插件 `simIK`（见 [[wiki/03-运动规划与控制/03-01-逆运动学-simIK]]），本条目仅用于理解 legacy 代码与旧 IK Regular API。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

# 场景中需已配置 IK group（tip-target 对）并注册
ik_group = sim.getIkGroupHandle('ik_tip_target')
joints = [sim.getObject(f'/robot/joint{i}') for i in range(1, 7)]

# 在 200ms 内随机搜索一个使 tip 达到当前 target 位姿的关节构型
config = sim.getConfigForTipPose(
    ik_group,
    joints,
    0.1,        # distanceThreshold (m)
    200,        # maxTimeInMs
    None,       # metric 默认 {1,1,1,0.1}
    None,       # 不做碰撞检查
    None,       # jointOptions
    None, None  # lowLimits/ranges 用默认关节区间
)

if config:
    print(f'关节构型 [rad]: {config}')
    # 如需应用：
    # for j, v in zip(joints, config):
    #     sim.setJointTargetPosition(j, v)
else:
    print('未找到满足条件的构型')
```

## 参见

- [[wiki/03-运动规划与控制/03-01-逆运动学-simIK]] — 推荐替代（simIK 插件）
- [[wiki/03-运动规划与控制/03-03-运动学基础-FK-IK]] — FK/IK 概念
- [[wiki/01-API参考/api/api-simCheckIkGroup]] — 旧 IK group 求解（同样已弃用）
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
