---
title: 虚拟点 Dummy
type: concept
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/建模
  - 难度/初级
created: 2026-07-24
updated: 2026-07-24
status: published
source:
  - dummies.htm
  - dummyPropertiesDialog.htm
  - assembly.htm
  - explicitHandling.htm
related:
  - "[[wiki/00-基础知识/00-04-场景与模型管理]]"
  - "[[wiki/02-仿真建模/02-01-分层命名场景构建]]"
  - "[[wiki/01-API参考/api/api-simCreateDummy]]"
  - "[[wiki/01-API参考/api/api-simSetLinkDummy]]"
  - "[[wiki/03-运动规划与控制/03-01-逆运动学-simIK]]"
sim_version: "4.10"
---

# 虚拟点 Dummy

> Dummy 是 CoppeliaSim 中**最简单的对象**：一个带姿态的点，可视作参考系。多用途辅助对象，单独使用标识点/参考系，成对使用指定 loop closure 或 tip-target 关系，也可用于模型装配对齐。

## 本质

Dummy 的全部几何就是一个点加一个朝向（即一个坐标系）。它是"参考系"概念的实体化：

- **单独使用**——在场景中标记某个点或局部参考系（如夹爪中心、IK 目标点、路径节点）。
- **成对使用**——两个 Dummy 通过 link 关系协作，用于动力学闭环（loop closure）、tip-target 运动学关系等。
- **装配用途**——父子对象各放一个 assembly 型 Dummy，装配时二者重叠，精确定位连接点。

## 对象属性

Dummy 是 **collidable / measurable / detectable** 三合一对象，但这三项属性**默认全部关闭**（见 general scene object properties 对话框）。开启后可：

| 属性 | 开启后的能力 |
|------|--------------|
| collidable | 可参与与体素类对象（如 OC tree）的碰撞检测 |
| measurable | 可参与最小距离计算 |
| detectable | 可被 proximity sensor 探测到 |

> 默认关闭使 Dummy 在多数场景中是"纯粹逻辑点"，不介入物理/感知计算；需要时按场景单独开启。

## 创建方式

1. **菜单**：`Add > Dummy` 直接添加一个场景 Dummy。
2. **从 shape 创建**：在 vertex edit 模式下，可由 shape 的顶点生成 Dummy。
3. **API 创建**：`sim.createDummy(size)`（见下文示例）。

## Dummy 类型（Dummy Dialog）

通过 `Tools > Scene object properties` 打开 Dummy Dialog（或双击层级树中的 Dummy 图标）。可在其中设置 Dummy 的专用类型：

| Dummy type | 行为 |
|------------|------|
| **Default** | 无专用行为，仅作点/参考系 |
| **Assembly** | 作为装配点；父子各一个 assembly dummy，执行 assemble 操作后二者重叠。可通过 `assemblyTag` 属性指定装配兼容性标签。也可用 `sim.setObjectParent` 在 API 层完成装配 |
| **Dynamic, overlap constraint** | 该 dummy 与其 linked dummy 尽力让位置/朝向重叠，用于**动力学闭环约束** |
| **Dynamic, tendon constraint** | 该 dummy 与其 linked dummy 表现为绳索/弹性约束，**目前仅 MuJoCo 引擎支持** |

其他参数：**Engine properties**（各物理引擎专用参数，需查阅对应引擎文档）、**Object size**（仅视觉效果，无功能含义）、**Color**。

## Linked Dummy（配对链接）

一个 Dummy 可选择性地**链接另一个 Dummy**，形成 dummy-dummy link pair。配对后可承载特殊属性或行为（如上述 overlap / tendon 约束）。链接通过 `sim.setLinkDummy` 建立：

- 设 `linkedDummyHandle` 为第二个 Dummy 的句柄——建立链接。
- 设为 `-1`——解除第一个 Dummy 的链接。

> Link dummy 的核心价值在于**动态约束的运行时构建**：可在仿真运行中即时建立/断开 dummy-dummy 链接，动态生成闭环约束或动态连接，不必在建模期固定。

## Assembly（模型装配）

父子对象通过 parent-child 关系附着。为精确控制二者连接点，在父与子对象上各放一个 **assembly 型 Dummy**，随后：

1. 选中**子对象**；
2. 选中**父 dummy**（期望的附着点）；
3. 点击工具栏 **assemble** 按钮。

装配成功后，两个 assembly dummy 完全重叠。可用 `assemblyTag` 属性为 assembly dummy 指定兼容性标签，限制可装配组合。也可经由 `sim.setObjectParent` API 完成。

## 显式处理（Explicit Handling）

Dummy 作为辅助点本身一般不涉及每帧计算，但与 Dummy 相关的传感器类对象（如 proximity sensor）有 **explicit handling flag**。该标志默认不勾选，意味着由 main script 隐式处理（每仿真步一次，`sim.handleProximitySensor(sim.handle_all_except_explicit)`）。若需更频繁/更稀疏的计算，勾选 explicit handling 后改由自定义脚本调用 `sim.handleProximitySensor(handle)`。

## Python 示例（ZMQ Remote API）

创建 Dummy 并建立 / 解除 link：

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

# 创建两个 dummy（size 仅影响视觉大小）
d1 = sim.createDummy(0.05)
d2 = sim.createDummy(0.05)

# 给定位置，便于在场景中看到
sim.setObjectPosition(d1, [0.5, 0.0, 0.1])
sim.setObjectPosition(d2, [0.5, 0.5, 0.1])

# 建立 dummy-dummy link（可承载 dynamic loop closure / tendon 等约束）
sim.setLinkDummy(d1, d2)

# 运行中动态解除链接
sim.setLinkDummy(d1, -1)
```

> 版本：CoppeliaSim 4.10+，ZMQ Remote API 客户端视角。`sim.setLinkDummy` 在仿真运行中也可调用，是动态构建闭环约束的关键 API。

## 参见
- [[wiki/00-基础知识/00-04-场景与模型管理]] — 对象层级与装配上下文
- [[wiki/02-仿真建模/02-01-分层命名场景构建]] — Dummy 在分层命名场景中的定位
- [[wiki/01-API参考/api/api-simCreateDummy]] — `sim.createDummy(size)` 创建虚拟点
- [[wiki/01-API参考/api/api-simSetLinkDummy]] — `sim.setLinkDummy(d1, d2)` 建立/解除链接
- [[wiki/03-运动规划与控制/03-01-逆运动学-simIK]] — Dummy 常作 IK tip/target 使用
