---
title: sim.checkIkGroup
type: api
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/中级
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simCheckIkGroup.htm
related:
  - "[[wiki/01-API参考/api/api-simGetJointMode]]"
  - "[[wiki/01-API参考/api/api-simSetJointTargetPosition]]"
  - "[[wiki/03-运动规划与控制/03-01-逆运动学-simIK]]"
  - "[[wiki/03-运动规划与控制/03-03-运动学基础-FK-IK]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
sim_version: "4.10"
---

# sim.checkIkGroup

> 求解一个已注册的 IK group，但**不应用**计算出的关节值——而是将结果作为数组返回。与 `sim.handleIkGroup`（会应用结果）相对。

⚠ **已弃用**：官方建议改用 kinematics 插件功能（见 [[wiki/03-运动规划与控制/03-01-逆运动学-simIK]]）。本条目仅用于理解旧代码与 legacy Regular API。

## 签名

```python
int ikCalculationResult, list jointValues = sim.checkIkGroup(int ikGroupHandle,
                                                              int[] jointHandles,
                                                              int[] jointOptions = None)
```

## 参数

- `ikGroupHandle`：IK group 句柄。仅"explicitly handled"标志的 IK group 有效。可通过 `sim.getIkGroupHandle(name)` 获取句柄。
- `jointHandles`：需要回读 IK 计算结果的关节句柄数组。
- `jointOptions`（可选）：位编码数组，对应 `jointHandles` 中每个关节。可传 `None`/`nil`。Bit 0（`1`）表示对应关节依赖于另一关节（dependent）。

## 返回值

- `ikCalculationResult`：IK 求解结果码。`-1` 表示错误；否则为 IK 计算结果值（表征是否收敛、是否被 manipulable 等，详见 IK 文档）。
- `jointValues`：长度等于 `len(jointHandles)` 的数组，包含 IK 计算出的关节值（**回转关节弧度，移动关节米**），顺序与 `jointHandles` 一致。

## 说明

`sim.checkIkGroup` 与 `sim.handleIkGroup` 的区别：

| 函数 | 是否应用结果 | 用途 |
|------|-------------|------|
| `sim.checkIkGroup` | 否，仅返回数组 | 试探性求解、多次比较、自定义应用逻辑 |
| `sim.handleIkGroup` | 是，直接写入关节 | 直接驱动关节运动 |

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

# 获取 IK group 句柄（需场景中已注册该 IK group）
ik_group = sim.getIkGroupHandle('ik_tip')
# 需要回读的关节
joints = [
    sim.getObject('/robot/joint1'),
    sim.getObject('/robot/joint2'),
    sim.getObject('/robot/joint3'),
]
# 求解但不应用
result, values = sim.checkIkGroup(ik_group, joints, None)
print(f'IK 结果码: {result}')   # -1 表示错误
print(f'关节值: {values}')      # [rad, rad, rad]
# 由调用方决定是否应用
# for j, v in zip(joints, values):
#     sim.setJointTargetPosition(j, v)
```

## 附录：相关 IK 函数（legacy Regular API）

以下 Regular API 函数在 4.10 中均标记为 **deprecated**，建议改用 kinematics 插件（`simIK`）：

| 函数 | 作用 | 替代方案 |
|------|------|---------|
| `sim.checkIkGroup` | 求解 IK group 但不应用（本条目） | `simIK.solve` / `simIK.computeJacobian` |
| `sim.handleIkGroup` | 求解 IK group 并应用结果 | `simIK.solve` + 手动应用 |
| `sim.generateIkPath` | 基于 IK 生成路径 | `simIK.computeJacobian` + 采样 |
| `sim.getIkGroupHandle` | 按 name 获取 IK group 句柄 | 直接使用 simIK 句柄 |

迁移指南与 `simIK` 插件 API 详见 [[wiki/03-运动规划与控制/03-01-逆运动学-simIK]]。

## 参见

- [[wiki/03-运动规划与控制/03-01-逆运动学-simIK]] — simIK 插件（推荐替代）
- [[wiki/03-运动规划与控制/03-03-运动学基础-FK-IK]] — FK/IK 概念
- [[wiki/01-API参考/api/api-simGetJointMode]] — 关节模式（kinematic/dependent/dynamic）
- [[wiki/01-API参考/api/api-simSetJointTargetPosition]] — 应用 IK 结果时常用
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
