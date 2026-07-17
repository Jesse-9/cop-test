---
title: ZeroMQ Remote API 使用指南
type: reference
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/中级
created: 2024-07-13
updated: 2024-07-13
status: published
related:
  - [[wiki/01-API参考/01-01-Regular-API-overview]]
  - [[wiki/06-工业案例/06-01-UR5-抓取与放置案例]]
sim_version: "4.10"
---

# ZeroMQ Remote API 使用指南

## 概述

ZeroMQ Remote API 是 CoppeliaSim 推荐的**外部控制方案**，取代了旧版的 Legacy Remote API。它基于 ZeroMQ 消息队列和 CBOR 序列化，在性能、可靠性和易用性上均优于旧方案。

## 架构

```
┌──────────────────┐         ZMQ          ┌──────────────────┐
│  CoppeliaSim      │ ◄══════════════════► │  外部程序         │
│  (仿真端)          │     req/reply        │  (客户端)         │
│                    │                      │                  │
│  simZMQ 插件      │                      │  Python/C++/Java  │
└──────────────────┘                      └──────────────────┘
```

## 安装

### 服务端（CoppeliaSim 端）
CoppeliaSim 4.1+ 内置 ZMQ 支持，无需额外安装。启动时检查终端输出确认 `simZMQ` 插件已加载。

### 客户端（Python）
```bash
pip install coppeliasim-zmqremoteapi-client
```

## 基本用法 (Python)

### 连接与基础操作
```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

# 连接 CoppeliaSim（默认端口 23000）
client = RemoteAPIClient()
sim = client.getObject('sim')

# 启动仿真
sim.startSimulation()

# 获取对象句柄
motor = sim.getObject('/arm_joint')
target = sim.getObject('/target')

# 读取/写入关节
pos = sim.getJointPosition(motor)
sim.setJointTargetPosition(motor, pos + 0.1)

# 停止仿真
sim.stopSimulation()
```

### 完整控制示例
```python
import time
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

class RobotController:
    def __init__(self):
        self.client = RemoteAPIClient()
        self.sim = self.client.getObject('sim')
        self.connected = False
        
    def connect(self):
        """连接到仿真并获取对象句柄"""
        self.joints = [
            self.sim.getObject(f'/UR5/joint{i}')
            for i in range(1, 7)
        ]
        self.tip = self.sim.getObject('/UR5/tip')
        self.target = self.sim.getObject('/UR5/target')
        self.connected = True
        print("已连接到 CoppeliaSim")
        
    def move_joints(self, joint_angles):
        """设置关节角度"""
        for i, angle in enumerate(joint_angles):
            self.sim.setJointPosition(self.joints[i], angle)
            
    def get_joint_angles(self):
        """读取当前关节角度"""
        return [
            self.sim.getJointPosition(j)
            for j in self.joints
        ]
    
    def get_tip_pose(self):
        """获取末端位姿"""
        pos = self.sim.getObjectPosition(self.tip, -1)
        orient = self.sim.getObjectOrientation(self.tip, -1)
        return pos, orient

# 使用示例
if __name__ == '__main__':
    ctrl = RobotController()
    ctrl.connect()
    ctrl.sim.startSimulation()
    time.sleep(0.5)
    
    # 读取当前位置
    angles = ctrl.get_joint_angles()
    print(f"当前关节角度: {angles}")
    
    # 移动到新位置
    ctrl.move_joints([0.5, -0.8, 1.2, 0.0, 0.5, 0.0])
    time.sleep(1.0)
    
    ctrl.sim.stopSimulation()
```

## 回调函数与订阅
```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.getObject('sim')

# 注册回调 — 当仿真中某事件发生时调用
def on_simulation_step(msg):
    print(f"仿真步进: {msg}")

sim.setIntegerSignal('callback_id', 1)
```

## 与 Legacy Remote API 对比

| 特性 | Legacy Remote API | ZeroMQ Remote API |
|------|------------------|-------------------|
| 协议 | XML-RPC | ZMQ + CBOR |
| 性能 | 较慢 | 快速（~10x 提升） |
| 语言支持 | C/C++, Python, Java, Lua, MATLAB | C++, Python, Java, MATLAB |
| 连接管理 | 手动 | 自动重连 |
| 堵塞风险 | 同步调用可能堵塞 | 异步支持更好 |
| 版本 | 旧版（仍可用） | 推荐（4.1+） |
| 并发 | 有限 | 优秀 |

## 连接配置

```python
# 自定义连接参数
client = RemoteAPIClient(
    host='localhost',    # 远程时改为目标 IP
    port=23000,          # 默认端口
    timeout=10           # 超时（秒）
)
```

## 脚本端配合

仿真端脚本需要暴露被调用的函数：

```lua
-- 在子脚本中定义可被 Remote API 调用的函数
function getStatus()
    local pos = sim.getJointPosition(jointHandle)
    return {position = pos, time = sim.getSimulationTime()}
end

function moveToTarget(targetPos)
    sim.setJointTargetPosition(jointHandle, targetPos)
    return true
end
```

## 常见问题

1. **连接失败** — 检查 CoppeliaSim 是否运行且 ZMQ 端口未被占用
2. **函数找不到** — 确认函数名在 Regular API 中存在（部分函数可能在 Remote API 中不可用）
3. **性能慢** — 避免频繁调用，考虑批量处理

---

**关联条目：**
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 详解
- [[wiki/05-数字孪生与协同仿真/05-01-ROS2集成]] — ROS2 外部控制
- [[wiki/06-工业案例/06-01-UR5-抓取与放置案例]] — 应用案例
