---
title: 逆运动学 (simIK) 详解
type: reference
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/运动学
  - 难度/中级
created: 2024-07-13
updated: 2024-07-13
status: published
source:
  - src-doc-CoppeliaSim-kinematics-plugin.html
  - src-doc-CoppeliaSim-solving-IK-FK.html
related:
  - [[wiki/01-API参考/01-01-Regular-API-overview]]
  - [[wiki/03-运动规划与控制/03-02-OMPL运动规划]]
  - [[wiki/06-工业案例/06-01-UR5-抓取与放置案例]]
sim_version: "4.10"
---

# 逆运动学 (simIK) 详解

## 概述

CoppeliaSim 的 **simIK 插件** 提供正向运动学（FK）和逆运动学（IK）求解功能。它是所有工业机器人仿真的核心模块，支持任何类型的运动链（冗余、非冗余、闭环等）。

> 详细文档见 [[raw/src-doc-CoppeliaSim-kinematics-plugin.html]]

## IK 核心概念

### IK 元素 (IK Element)
定义一条运动链：基座 → 关节序列 → 末端。

### IK 组 (IK Group)
包含一个或多个 IK 元素，同步求解。

### IK 环境 (IK Environment)
独立的 IK 求解空间，与仿真场景隔离。

## 初始化

```lua
simIK = require('simIK')
```

## 快速设置（从场景对象自动解析）

最简单的方式：使用场景中已有的对象自动构建 IK 链。

```lua
function sysCall_init()
    simIK = require('simIK')
    
    -- 从场景自动创建 IK 元素
    local base = sim.getObject('/UR5')
    local tip = sim.getObject('/UR5/tip')
    local target = sim.getObject('/UR5/target')
    
    -- 创建 IK 环境
    ikEnv = simIK.createEnvironment()
    
    -- 创建 IK 组
    ikGroup = simIK.createGroup(ikEnv)
    
    -- 从场景自动解析运动链
    ikElement, simToIkMap, ikToSimMap = simIK.addElementFromScene(
        ikEnv, ikGroup, base, tip, target,
        simIK.constraint_position)  -- 位置约束
    
    -- 设置求解算法（可选）
    simIK.setGroupCalculation(ikEnv, ikGroup,
        simIK.method_pseudo_inverse,
        simIK.constraint_position + simIK.constraint_orientation)
end
```

## 手动设置（完全编程控制）

适用于需要精细控制 IK 链的场景：

```lua
function sysCall_init()
    simIK = require('simIK')
    ikEnv = simIK.createEnvironment()
    ikGroup = simIK.createGroup(ikEnv)
    
    -- 获取场景中的关节
    local simJoints = {}
    for i = 1, 6 do
        simJoints[i] = sim.getObject('/UR5/joint' .. i)
    end
    
    -- 在 IK 环境中创建对应的 IK 关节
    local ikJoints = {}
    local parent = simIK.handle_world
    
    for i = 1, 6 do
        ikJoints[i] = simIK.createJoint(ikEnv, simIK.jointtype_revolute)
        simIK.setJointMode(ikEnv, ikJoints[i], simIK.jointmode_ik)
        
        -- 设置关节限位
        local cyclic = false
        local interval = {-3.14159, 3.14159}
        simIK.setJointInterval(ikEnv, ikJoints[i], cyclic, interval)
        
        -- 建立父子关系
        simIK.setObjectParent(ikEnv, ikJoints[i], parent, true)
        parent = ikJoints[i]
    end
    
    -- 创建末端和 target
    ikTip = simIK.createDummy(ikEnv)
    ikTarget = simIK.createDummy(ikEnv)
    simIK.setObjectParent(ikEnv, ikTip, ikJoints[6], true)
    
    -- 添加 IK 元素
    local element = simIK.addElement(ikEnv, ikGroup, ikJoints[1], ikTip, ikTarget)
end
```

## 求解与同步

```lua
function sysCall_actuation()
    -- 移动 target 到期望位置
    local targetPos = {0.5, 0.3, 0.8}
    local targetOrient = {math.pi/2, 0, 0}
    sim.setObjectPosition(targetDummy, targetPos)
    sim.setObjectOrientation(targetDummy, targetOrient)
    
    -- 将场景中 target 的位置同步到 IK 环境
    simIK.setObjectPose(ikEnv, ikTarget, simIK.handle_world,
        sim.getObjectPose(targetDummy, simIK.handle_world))
    
    -- 求解 IK
    local result = simIK.handleGroup(ikEnv, ikGroup, {
        syncWorlds = true,
        syncStep = true
    })
    
    if result == simIK.result_success then
        print("IK 求解成功")
    elseif result == simIK.result_fail then
        print("IK 求解失败 - 目标不可达")
    end
end
```

## 约束类型

```lua
-- 基本约束
simIK.constraint_position         -- 位置约束（3 自由度）
simIK.constraint_orientation      -- 姿态约束（3 自由度）
simIK.constraint_pose             -- 完整位姿约束（6 自由度）
simIK.constraint_position_x       -- 仅 X 轴位置
simIK.constraint_position_y       -- 仅 Y 轴位置
simIK.constraint_position_z       -- 仅 Z 轴位置
```

## 求解算法

```lua
-- 伪逆法（通用推荐）
simIK.method_pseudo_inverse

-- 阻尼最小二乘法（在奇异性附近更稳定）
simIK.method_damped_pseudo_inverse

-- 转置法（计算快但精度较低）
simIK.method_jacobian_transpose

-- 循环坐标下降法（适合冗余机器人）
simIK.method_cyclic_coordinate_descent  -- 4.3+ 支持
```

## Python + ZMQ API 调用 simIK

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.getObject('sim')
simIK = client.getObject('simIK')

# 创建 IK 环境
ik_env = simIK.createEnvironment()
ik_group = simIK.createGroup(ik_env)

# 获取场景对象
base = sim.getObject('/UR5')
tip = sim.getObject('/UR5/tip')
target = sim.getObject('/UR5/target')

# 添加 IK 元素
ik_element, sim_to_ik, ik_to_sim = simIK.addElementFromScene(
    ik_env, ik_group, base, tip, target,
    simIK.constraint_pose
)

# 设置 target 位置并求解
sim.setObjectPosition(target, [-1], [0.5, 0.0, 0.5])
sim.setObjectOrientation(target, [-1], [0, math.pi/2, 0])

result = simIK.handleGroup(ik_env, ik_group, {'syncWorlds': True})
print(f"IK Result: {result}")
```

## 常见问题排查

### IK 求解失败
- **目标位置超出工作空间** → 检查 target 是否在机器人的可达范围内
- **关节限位冲突** → 检查 `simIK.setJointInterval()` 的设置
- **奇异性问题** → 使用阻尼最小二乘法 (`simIK.method_damped_pseudo_inverse`)
- **初始位姿远离目标** → 分步逼近而非一步到位

### 性能优化
- 减少 IK 组中元素的数量
- 适当降低求解精度
- 在非线程脚本中避免每步都求解（如每 5 步求解一次）

---

**关联条目：**
- [[wiki/03-运动规划与控制/03-02-OMPL运动规划]] — 运动规划（OMPL）
- [[wiki/06-工业案例/06-01-UR5-抓取与放置案例]] — UR5 案例
- [[wiki/07-API脚本示例/07-01-Lua基本脚本模式]] — 脚本模式
