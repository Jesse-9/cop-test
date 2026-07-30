---
title: 逆运动学 (simIK) 插件 API 详解
type: reference
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/运动学
  - 难度/高级
created: 2024-07-13
updated: 2026-07-24
status: published
source:
  - simIK.htm
  - basicsOnIkGroupsAndIkElements.htm
  - solvingIkAndFk.htm
  - inverseKinematicsTutorial.htm
related:
  - "[[wiki/03-运动规划与控制/03-03-运动学基础-FK-IK]]"
  - "[[wiki/02-仿真建模/02-03-关节与关节模式]]"
  - "[[wiki/01-API参考/api/api-simCheckIkGroup]]"
  - "[[wiki/01-API参考/api/api-simGetConfigForTipPose]]"
  - "[[wiki/01-API参考/01-04-插件API总览]]"
  - "[[wiki/06-工业案例/06-01-UR5-抓取与放置案例]]"
sim_version: "4.10"
---

# 逆运动学 (simIK) 插件 API 详解

## 定位

simIK 是 CoppeliaSim 内置的**运动学插件**，提供独立的 IK/FK 求解环境与 API，**与仿真场景（scene world）隔离**：

- 在 IK 环境中构建一条等价运动链（关节/dummy/父子关系），求解后将结果回写到场景；
- 不依赖物理引擎，也不依赖场景中对象的 `dynamic` 属性，是纯运动学计算；
- 单位为**米与弧度**（与 Regular API 一致）。

> 与 [[wiki/01-API参考/api/api-simCheckIkGroup|sim.checkIkGroup]] 的区别：`sim.checkIkGroup` 是已废弃的 Regular API 侧函数，仅"检查"已在场景中注册的 IK group（不写回关节值）；simIK 插件 API 则是当前推荐方式，可在独立 IK 环境中创建、求解并同步，灵活度更高。

## 核心对象层级

| 对象 | 说明 | 创建 API |
|------|------|---------|
| **IK environment**（IK 环境） | 独立的运动学求解空间，与场景隔离。一切对象、group 都存活在某个 environment 内 | `simIK.createEnvironment()` |
| **IK group**（IK 组） | 求解单元，含 1 个或多个 IK element。group 决定求解方法、阻尼、最大迭代数、group flags | `simIK.createGroup(ikEnv)` |
| **IK element**（IK 元素） | 一条简单运动链：base → links/joints → tip，tip 追随 target，约束可位姿组合 | `simIK.addElement(...)` / `simIK.addElementFromScene(...)` |
| joint / dummy | IK 环境内的对象，由插件管理句柄，与场景对象通过 mapping 对应 | `simIK.createJoint/createDummy` |

> 多个共享关节的链需放入同一 IK group 才能联立求解；不共享关节但相互依赖的链可分多个 group，但求解顺序重要。多个 group 同时求解时支持 null-space 投影（优先级）。

## 约束与求解方法

### 约束位（bit-combine）

| 常量 | 含义 |
|------|------|
| `simIK.constraint_x` / `_y` / `_z` | 单轴位置约束 |
| `simIK.constraint_alpha_beta` | 偏航+俯仰（2 自由度，z 轴对齐） |
| `simIK.constraint_gamma` | 绕 z 自转（仅当 alpha_beta 已设时有效） |
| `simIK.constraint_position` | `x|y|z` 三轴位置 |
| `simIK.constraint_orientation` | `alpha_beta|gamma` 完整姿态 |
| `simIK.constraint_pose` | 位置+姿态（6 自由度） |

### 求解方法（`simIK.setGroupCalculation` 的 `method`）

| 常量 | 特性 |
|------|------|
| `simIK.method_pseudo_inverse` | 伪逆（含少量硬编码阻尼），通用推荐 |
| `simIK.method_undamped_pseudo_inverse` | 无阻尼伪逆 |
| `simIK.method_damped_least_squares` | 阻尼最小二乘，奇异点附近更稳定，需配 `damping` |
| `simIK.method_jacobian_transpose` | 雅可比转置，快但精度较低 |

### group flags（`simIK.setGroupFlags`，位或）

| 常量 | 含义 |
|------|------|
| `simIK.group_enabled` | 启用该 group |
| `simIK.group_ignoremaxsteps` | 忽略关节最大步长 |
| `simIK.group_restoreonbadlintol` | 线性精度未达时不写回（防抖） |
| `simIK.group_restoreonbadangtol` | 角度精度未达时不写回 |
| `simIK.group_avoidlimits` | 主动避关节限位 |
| `simIK.group_stoponlimithit` | 碰限位即停 |

## 主要 API 速览（以 simIK.json 实际函数名为准）

### 环境 / 持久化

| 函数 | 作用 |
|------|------|
| `simIK.createEnvironment(flags=0)` | 创建 IK 环境 |
| `simIK.eraseEnvironment(env)` | 销毁环境 |
| `simIK.duplicateEnvironment(env)` | 克隆环境（搜索/规划时保留原件） |
| `simIK.save(env)` / `simIK.load(env, data)` | 导出/导入运动学内容（buffer） |

### IK group

| 函数 | 作用 |
|------|------|
| `simIK.createGroup(env, name='')` | 创建 group |
| `simIK.getGroupHandle(env, name)` / `simIK.doesGroupExist(env, name)` | 按名查 group |
| `simIK.setGroupCalculation(env, group, method, damping, maxIter)` | 设求解方法 |
| `simIK.getGroupCalculation(env, group)` | 取求解方法 |
| `simIK.setGroupFlags(env, group, flags)` / `simIK.getGroupFlags` | 设/取 group flags |
| `simIK.getGroupJoints(env, group)` | 取该 group 涉及的关节句柄 |
| `simIK.getGroupJointLimitHits(env, group)` | 上次求解碰限位的关节 |

### IK element

| 函数 | 作用 |
|------|------|
| `simIK.addElement(env, group, tipHandle)` | 手动添加 element（返回 element handle） |
| `simIK.addElementFromScene(env, group, base, tip, target, constraints)` | 从场景运动链自动解析（推荐入口） |
| `simIK.setElementBase(env, group, elem, base, constrBase=-1)` | 设 base 与约束参考系 |
| `simIK.setElementConstraints(env, group, elem, constraints)` | 设约束 |
| `simIK.setElementPrecision(env, group, elem, [lin, ang])` | 设线性/角度精度 |
| `simIK.setElementWeights(env, group, elem, [lin, ang, overall])` | 设求解权重 |
| `simIK.setElementFlags(env, group, elem, flags)` | 设 element flags（bit0=启用） |

### 对象 / 关节

| 函数 | 作用 |
|------|------|
| `simIK.createJoint(env, name='')` / `simIK.createDummy(env, name='')` | 在环境内创建关节/dummy |
| `simIK.setJointMode(env, j, mode)` | `simIK.jointmode_passive` 或 `simIK.jointmode_ik` |
| `simIK.setJointInterval(env, j, cyclic, [low, range])` | 设关节限位 |
| `simIK.setJointPosition(env, j, pos)` / `simIK.getJointPosition` | 设/取关节位置 |
| `simIK.setJointWeight(env, j, w)` | 设 IK 求解权重 |
| `simIK.setJointMaxStepSize(env, j, step)` | 设最大步长 |
| `simIK.setJointDependency(env, j, depJ, off=0, mult=1, cb=None)` | 关节联动 |
| `simIK.setJointScrewLead(env, j, lead)` | 螺旋副导程（0=纯旋转） |
| `simIK.setObjectParent(env, obj, parent, keepInPlace=True)` | 设父子 |
| `simIK.setObjectMatrix/setObjectPose/setObjectTransformation` | 设位姿 |
| `simIK.setTargetDummy(env, tip, target)` | 关联 tip ↔ target |

### 求解与同步

| 函数 | 作用 |
|------|------|
| `simIK.handleGroup(env, group, options)` | 求解单个 group |
| `simIK.handleGroups(env, [g1, g2, ...], options)` | 多 group 联立（null-space 投影） |
| `simIK.syncFromSim(env, [groups])` | 场景状态 → IK 环境 |
| `simIK.syncToSim(env, [groups])` | IK 结果 → 场景 |
| `simIK.computeGroupJacobian(env, group)` | 计算 group 雅可比与误差向量 |
| `simIK.computeJacobian(env, base, lastJoint, constraints, tipM, targetM=None, constrBaseM=None)` | 单链雅可比 |
| `simIK.findConfigs(env, group, joints, params={}, configs=[])` | 随机搜索满足 target 的多组解 |
| `simIK.getAlternateConfigs(env, joints, lowLim=None, ranges=None)` | 同位姿的等价配置 |
| `simIK.generatePath(env, group, joints, tip, pathPointCount, cb=None, aux=None)` | 生成直线路径（configuration space） |

### `handleGroup` 的 options

- `syncWorlds=True`：求解前 `syncFromSim`、求解后 `syncToSim`（与 `addElementFromScene` 搭配最常用）；
- `allowError=True`：即使未达精度也写回场景；
- `debug` bit0：可视化 IK group；
- `callback`：可注入自定义雅可比/误差/dq，跳过内部计算。

### 返回值

`handleGroup`/`handleGroups` 返回 `(result, reason, precision)`：

- `result`：`simIK.result_success` 或失败；
- `reason`：位或 `simIK.calc_notperformed` / `calc_cannotinvert`（雅可比不可逆）/ `calc_notwithintolerance` / `calc_stepstoobig` / `calc_limithit`；
- `precision`：所有 tip-target 对的最大线性和角度距离 `[lin, ang]`。

## 典型流程：从场景自动解析 IK 链

最常用入口是 `addElementFromScene`：传入场景中的 base / tip / target dummy 与约束，插件自动遍历运动链、在 IK 环境内创建等价关节与 dummy，并返回 sim↔IK 对象映射。后续只需移动场景中的 target，再 `handleGroup(..., syncWorlds=True)` 即可。

```python
# Python (ZMQ Remote API 客户端，适配 4.10)
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')
simIK = client.require('simIK')

# 场景对象（路径查询，见 accessingSceneObjects.htm）
sim_base = sim.getObject('/redundantRobot')
sim_tip = sim.getObject('/redundantRobot/tip')
sim_target = sim.getObject('/redundantRobot/target')

# 1. 创建 IK 环境与 group
ik_env = simIK.createEnvironment()
ik_group = simIK.createGroup(ik_env)
# 2. 求解方法：伪逆，无阻尼，最多 6 次迭代
simIK.setGroupCalculation(ik_env, ik_group,
                          simIK.method_pseudo_inverse, 0.0, 6)
# 3. 从场景自动解析运动链，约束为完整位姿
simIK.addElementFromScene(ik_env, ik_group,
                          sim_base, sim_tip, sim_target,
                          simIK.constraint_pose)

# 4. 移动 target 到期望位姿（米/弧度）
sim.setObjectPosition(sim_target, sim.handle_world, [0.5, 0.0, 0.5])
sim.setObjectOrientation(sim_target, sim.handle_world, [0, 1.5708, 0])

# 5. 求解并写回场景
res, reason, precision = simIK.handleGroup(ik_env, ik_group,
                                            {'syncWorlds': True})
if res != simIK.result_success:
    # 6. 奇异/不可达时回退到阻尼最小二乘
    simIK.setGroupCalculation(ik_env, ik_group,
                              simIK.method_damped_least_squares, 1.0, 99)
    simIK.handleGroup(ik_env, ik_group, {'syncWorlds': True})
```

对应 Lua（非线程化脚本内）：

```lua
function sysCall_init()
    sim = require('sim')
    simIK = require('simIK')
    local simBase = sim.getObject('/redundantRobot')
    local simTip = sim.getObject('/redundantRobot/tip')
    local simTarget = sim.getObject('/redundantRobot/target')
    ikEnv = simIK.createEnvironment()
    ikGroup = simIK.createGroup(ikEnv)
    simIK.setGroupCalculation(ikEnv, ikGroup, simIK.method_pseudo_inverse, 0, 6)
    simIK.addElementFromScene(ikEnv, ikGroup, simBase, simTip, simTarget,
                              simIK.constraint_pose)
end

function sysCall_actuation()
    res, _, _ = simIK.handleGroup(ikEnv, ikGroup, {syncWorlds=true})
    if res ~= simIK.result_success then
        simIK.setGroupCalculation(ikEnv, ikGroup,
            simIK.method_damped_least_squares, 1, 99)
        simIK.handleGroup(ikEnv, ikGroup, {syncWorlds=true})
    end
end
```

## 手动构建 IK 链（精细控制）

当 `addElementFromScene` 不够灵活（如要自定义 base 参考系、约束参考系、关节联动等），可在 IK 环境内逐个创建关节/dummy 并建立父子关系。手册教程给出完整模板：

```python
# 摘自 inverseKinematicsTutorial.htm（精简）
sim_base = sim.getObject('/redundantRobot')
sim_tip = sim.getObject('/redundantRobot/tip')
sim_target = sim.getObject('/redundantRobot/target')
sim_joints = [sim.getObject(f'/redundantRobot/joint{i}') for i in range(1, 8)]

ik_env = simIK.createEnvironment()
ik_base = simIK.createDummy(ik_env)
simIK.setObjectMatrix(ik_env, ik_base, -1, sim.getObjectMatrix(sim_base))

parent = ik_base
ik_joints = []
for sj in sim_joints:
    ij = simIK.createJoint(ik_env, simIK.jointtype_revolute)
    simIK.setJointMode(ik_env, ij, simIK.jointmode_ik)
    cyclic, interv = sim.getJointInterval(sj)
    simIK.setJointInterval(ik_env, ij, cyclic, interv)
    simIK.setJointPosition(ik_env, ij, sim.getJointPosition(sj))
    simIK.setObjectMatrix(ik_env, ij, -1, sim.getObjectMatrix(sj))
    simIK.setObjectParent(ik_env, ij, parent, True)
    parent = ij

ik_tip = simIK.createDummy(ik_env)
simIK.setObjectMatrix(ik_env, ik_tip, -1, sim.getObjectMatrix(sim_tip))
simIK.setObjectParent(ik_env, ik_tip, parent, True)

ik_target = simIK.createDummy(ik_env)
simIK.setObjectMatrix(ik_env, ik_target, -1, sim.getObjectMatrix(sim_target))
simIK.setTargetDummy(ik_env, ik_tip, ik_target)

ik_group = simIK.createGroup(ik_env)
simIK.setGroupCalculation(ik_env, ik_group, simIK.method_pseudo_inverse, 0, 6)
elem = simIK.addElement(ik_env, ik_group, ik_tip)
simIK.setElementBase(ik_env, ik_group, elem, ik_base)
simIK.setElementConstraints(ik_env, ik_group, elem, simIK.constraint_pose)
```

手动模式下需自行同步：求解前把场景 target 位姿写入 `ik_target`（相对 `ik_base`），求解后把 `ik_joints` 的位置写回 `sim_joints`。`addElementFromScene` 模式则用 `syncWorlds=True` 自动完成这两步。

## generatePath：生成直线路径

`simIK.generatePath` 让 tip 沿"当前位置 → target 位置"的**笛卡尔直线**插值，逐点反解关节，返回 configuration space 路径（行优先，每点为一组关节值）。环境状态不变。常配合 `duplicateEnvironment` 在副本上规划。

```python
# 生成 20 点直线路径（需先建好 ik_env/ik_group/ik_tip，并设好 target）
ik_joints = simIK.getGroupJoints(ik_env, ik_group)
path = simIK.generatePath(ik_env, ik_group, ik_joints, ik_tip, 20)
# path: [j1,j2,...,jN, j1,j2,...,jN, ...] 共 20*N 个值
```

失败原因：路径上存在不可达点、关节限位冲突、或验证回调拒绝。可传 `validationCallback`（如碰撞检测）过滤。

## 多 group 联立与优先级

`simIK.handleGroups(env, [g1, g2, ...], options)` 对多个 group 做**null-space 投影优先级求解**：

```
dq = dq0 + NP0*(dq1 + NP1*(dq2 + NP2*(...)))
```

其中 `dqi` 是第 i 个任务的解，`NPi = I - J_i# * J_i`。高优先级任务先解，低优先级在其零空间内尽量满足。适用于冗余机器人主任务+副任务（如保持末端位姿的同时避奇异/避限位）。

## 与 `sim.checkIkGroup`（Regular API）的关系

| 维度 | `sim.checkIkGroup`（Regular API，已废弃） | simIK 插件 API |
|------|------------------------------------------|----------------|
| 求解对象 | 场景中已注册的 IK group（需 UI 或场景文件预置） | 独立 IK 环境内创建的 group |
| 是否写回 | 不写回关节值，只返回计算结果 | `syncToSim`/`syncWorlds` 可写回 |
| 灵活性 | 低，依赖场景注册 | 高，纯编程构建 |
| 推荐度 | 已废弃，仅遗留场景兼容 | 4.10 推荐方式 |

> 见 [[wiki/01-API参考/api/api-simCheckIkGroup]] 与 [[wiki/01-API参考/api/api-simGetConfigForTipPose]]。`sim.getConfigForTipPose`/`simIK.getConfigForTipPose` 也已废弃，改用 `simIK.findConfigs`。

## 常见排错

| 现象 | 可能原因与对策 |
|------|---------------|
| `result != success` 且 `reason` 含 `calc_cannotinvert` | 雅可比奇异，切 `method_damped_least_squares` 并加阻尼 |
| `calc_notwithintolerance` | 目标超工作空间或约束过严；检查 target 是否可达、关节限位是否过紧 |
| `calc_limithit` | 碰限位；开 `group_avoidlimits` 或放宽限位 |
| `calc_stepstoobig` | 关节 max step 过小；开 `group_ignoremaxsteps` 或调大 `setJointMaxStepSize` |
| 求解抖动 | 开 `group_restoreonbadlintol` + `group_restoreonbadangtol`，未达精度时不写回 |
| 0 关节 / 链断 | `addElementFromScene` 依赖 base→tip 的父子链完整，检查场景层级与 dummy 关联 |

## 调试可视化

- `[Tools > Visualize IK world]` 可叠加显示 IK 环境内容（与场景对照）；
- `simIK.createDebugOverlay(env, tipHandle, baseHandle=-1)` 在场景中生成可视化对象，`simIK.eraseDebugOverlay` 移除；
- `handleGroup` 的 `options.debug` bit0 也可可视化当前 group。

---

**关联条目：**
- [[wiki/03-运动规划与控制/03-03-运动学基础-FK-IK]] — IK/FK 概念
- [[wiki/02-仿真建模/02-03-关节与关节模式]] — 关节类型与模式
- [[wiki/01-API参考/api/api-simCheckIkGroup]] — 已废弃的 Regular API 侧 IK
- [[wiki/01-API参考/api/api-simGetConfigForTipPose]] — 已废弃，改用 `simIK.findConfigs`
- [[wiki/01-API参考/01-04-插件API总览]] — 插件 API 总览
- [[wiki/06-工业案例/06-01-UR5-抓取与放置案例]] — UR5 实战
