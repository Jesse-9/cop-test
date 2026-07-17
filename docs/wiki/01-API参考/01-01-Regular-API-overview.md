---
title: Regular API 概述与使用
type: reference
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/中级
created: 2024-07-13
updated: 2024-07-13
status: published
source: src-doc-CoppeliaSim-API-functions.html
related:
  - [[wiki/00-基础知识/00-01-CoppeliaSim-overview]]
  - [[wiki/07-API脚本示例/07-01-Lua基本脚本模式]]
  - [[wiki/03-运动规划与控制/03-01-逆运动学-simIK]]
sim_version: "4.10"
---

# Regular API 概述与使用

## 简介

Regular API 是 CoppeliaSim 最核心的编程接口，由 `sim` 命名空间提供。所有嵌入 CoppeliaSim 中的脚本（主脚本、子脚本、定制化脚本）都通过 Regular API 与仿真环境交互。

> 详细 API 函数列表见 [[raw/src-doc-CoppeliaSim-API-functions.html]]

## 加载方式

在 Lua 脚本中调用 Regular API 前需先加载 `sim` 模块：

```lua
sim = require('sim')
```

从 CoppeliaSim 4.6 开始，`sim` 模块在脚本中已**默认预加载**，可直接使用 `sim.*` 函数。

## API 函数分类

### 1. 对象操作
| 函数 | 功能 |
|------|------|
| `sim.getObject(path)` | 获取对象句柄 |
| `sim.getObjectName(handle)` | 获取对象名称 |
| `sim.setObjectPosition(handle, relTo, pos)` | 设置位置 |
| `sim.getObjectPosition(handle, relTo)` | 获取位置 |
| `sim.setObjectOrientation(handle, relTo, euler)` | 设置欧拉角 |
| `sim.getObjectOrientation(handle, relTo)` | 获取欧拉角 |
| `sim.setObjectMatrix(handle, relTo, matrix)` | 设置变换矩阵 |
| `sim.getObjectMatrix(handle, relTo)` | 获取变换矩阵 |
| `sim.setObjectParent(handle, parent, keepInPlace)` | 设置父对象 |

### 2. 关节控制
| 函数 | 功能 |
|------|------|
| `sim.setJointPosition(handle, pos)` | 设置关节位置 |
| `sim.getJointPosition(handle)` | 获取关节位置 |
| `sim.setJointTargetPosition(handle, pos)` | 设置目标位置（位置控制模式） |
| `sim.setJointTargetVelocity(handle, vel)` | 设置目标速度（速度控制模式） |
| `sim.setJointForce(handle, force)` | 设置最大力/扭矩 |
| `sim.getJointForce(handle)` | 获取关节力/扭矩 |
| `sim.setJointMode(handle, mode)` | 设置关节控制模式 |

**关节控制模式：**
```lua
sim.jointmode_passive              -- 被动模式（外力驱动）
sim.jointmode_movement             -- 运动模式（运动学）
sim.jointmode_ik                   -- IK 模式
sim.jointmode_dependent            -- 依赖模式
sim.jointmode_dynamic              -- 动力学模式
sim.jointmode_torque               -- 力矩模式
sim.jointmode_force                -- 力模式
```

### 3. 仿真控制
| 函数 | 功能 |
|------|------|
| `sim.startSimulation()` | 启动仿真 |
| `sim.stopSimulation()` | 停止仿真 |
| `sim.getSimulationTime()` | 获取仿真时间 |
| `sim.getSimulationState()` | 获取仿真状态 |
| `sim.setStepping(true/false)` | 启用/禁用单步模式 |

### 4. 传感器
| 函数 | 功能 |
|------|------|
| `sim.readProximitySensor(handle)` | 读取接近传感器 |
| `sim.readVisionSensor(handle)` | 读取视觉传感器 |
| `sim.checkDistance(handle1, handle2)` | 距离检测 |
| `sim.checkCollision(handle1, handle2)` | 碰撞检测 |

### 5. 信号与数据交换
```lua
-- 整数信号
sim.setIntegerSignal(name, value)
val = sim.getIntegerSignal(name)

-- 浮点信号
sim.setFloatSignal(name, value)
val = sim.getFloatSignal(name)

-- 字符串信号
sim.setStringSignal(name, value)
val = sim.getStringSignal(name)

-- 清除信号
sim.clearIntegerSignal(name)
sim.clearAllSignals()
```

### 6. 脚本与函数调用
| 函数 | 功能 |
|------|------|
| `sim.callScriptFunction(func, handle, ...)` | 调用指定脚本中的函数 |
| `sim.callScriptFunctionEx(handle, funcName, ...)` | 扩展脚本调用 |
| `sim.executeScriptString(string, handle)` | 执行 Lua 代码字符串 |

### 7. 图形与可视化
| 函数 | 功能 |
|------|------|
| `sim.addDrawingObject(type, size, ...)` | 添加绘图对象 |
| `sim.addDrawingObjectItem(drawing, data)` | 绘图 |
| `sim.addGraphCurve(graph, ...)` | 添加图表曲线 |
| `sim.setGraphUserData(graph, id, val)` | 设置图表数据 |

### 8. 通信
| 函数 | 功能 |
|------|------|
| `sim.sendData(..)` | 发送数据 |
| `sim.receiveData(..)` | 接收数据 |
| `sim.publishData(..)` | 发布数据 |

## 典型用法示例

### 非线程子脚本 (每步执行一次)
```lua
function sysCall_init()
    -- 初始化
    motor = sim.getObject('/arm_joint')
    target = sim.getObject('/target')
end

function sysCall_actuation()
    -- 每一步执行的逻辑（执行阶段）
    local pos = sim.getJointPosition(motor)
    sim.setJointTargetPosition(motor, pos + 0.01)
end

function sysCall_sensing()
    -- 每一步执行的逻辑（感知阶段）
end

function sysCall_cleanup()
    -- 清理
end
```

### 线程子脚本 (独立循环)
```lua
function sysCall_init()
    motor = sim.getObject('/arm_joint')
end

function sysCall_thread()
    while sim.getSimulationState() ~= sim.simulation_advancing_abouttostop do
        local pos = sim.getJointPosition(motor)
        sim.setJointTargetPosition(motor, pos + 0.01)
        sim.switchThread()  -- 让出控制权
    end
end
```

## 常用最佳实践

### 对象句柄
- 避免在每次回调中重复调用 `sim.getObject()`，将句柄保存在全局变量中
- 使用场景层次的绝对路径（以 `/` 开头）定位对象，如 `/robot/arm_joint`

### 坐标变换
```lua
-- 将点从子坐标系变换到父坐标系
local relPos = sim.getObjectPosition(tip, base)

-- 将点从世界坐标系变换到对象本地坐标系
local localPos = sim.getObjectPosition(tip, sim.handle_world)

-- 检查对象间相对位置
local dist = sim.getDistanceBetweenEntities(handle1, handle2)
```

### 性能优化
- 减少 `sim.switchThread()` 的调用频率
- 使用信号（Signal）在脚本间高效通信
- 批量处理对象操作，而非逐帧更新

## API 版本兼容性

> ⚠️ 不同版本 CoppeliaSim 的 API 可能有细微差异。本条目面向 CoppeliaSim 4.10，如使用旧版本请查阅对应文档。

| 函数 | 4.10 | 4.6+ | 4.0+ | 注意 |
|------|------|------|------|------|
| `sim.getObject` | ✅ | ✅ | ✅ | 4.0 起推荐，替代旧版 `sim.getObjectHandle` |
| `sim.callScriptFunctionEx` | ✅ | ✅ | ❌ | 4.3+ 引入 |
| `sim.setStepping` | ✅ | ✅ | ❌ | 4.2+ 引入 |

---

**关联条目：**
- [[wiki/01-API参考/01-02-Remote-API-ZMQ]] — ZeroMQ Remote API
- [[wiki/07-API脚本示例/07-01-Lua基本脚本模式]] — Lua 脚本模式详解
- [[wiki/99-附录/99-01-术语表]] — 常用术语
