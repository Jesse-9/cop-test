---
title: sim.setFloatSignal / sim.getFloatSignal
type: api
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/初级
created: 2026-07-24
updated: 2026-07-24
status: published
source:
  - regularApi/simSetFloatSignal.htm
  - regularApi/simGetFloatSignal.htm
related:
  - "[[wiki/01-API参考/api/api-simSetIntegerSignal]]"
  - "[[wiki/01-API参考/api/api-simSetStringSignal]]"
  - "[[wiki/01-API参考/api/api-simSetDoubleSignal]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
  - "[[wiki/06-工业案例/06-02-岛式车间柔性生产案例]]"
sim_version: "4.10"
---

# sim.setFloatSignal / sim.getFloatSignal

> 浮点信号的读写。信号是 CoppeliaSim 中脚本间、脚本与外部程序共享简单状态的本机制：信号名全局唯一，写入即对整个场景广播。**已弃用**——手册建议改用 Properties 体系（`signal.` 前缀）。

## 签名

```python
sim.setFloatSignal(string signalName, float signalValue)
float signalValue = sim.getFloatSignal(string signalName)
```

## 参数

- `signalName`：信号名（字符串，全局）。
- `signalValue`（set）：浮点值。
- `signalValue`（get 返回）：浮点值；信号不存在时返回 `None`。

## 返回值

- `setFloatSignal`：无。
- `getFloatSignal`：信号值，或 `None`（信号不存在）。

## 备注

### 弃用说明
手册 `simSetFloatSignal.htm` / `simGetFloatSignal.htm` 顶部标注：

> Deprecated. See properties instead

即 4.10 推荐迁移到 [[wiki/02-仿真建模/properties|Properties]] 体系。注意 `setDoubleSignal`（见 [[wiki/01-API参考/api/api-simSetDoubleSignal]]）在更早版本就被标注弃用、改用 `setFloatSignal`，因为 CoppeliaSim 内部统一用 32 位浮点；两步迁移路径为 `Double → Float → Properties`。

### 信号语义
- 信号名**全局**于场景，无命名空间隔离。
- 写入即广播：任一脚本或外部 API 客户端调用 `set*Signal` 后，其他脚本通过同名 `get*Signal` 立即可读。
- **易失性**：创建/修改该信号的脚本状态被销毁时，信号自动清除（与 custom data 相反，custom data 持久化到磁盘）。
- 浮点信号常用于传递节拍时间、温度、力矩阈值等连续量。

### 现代替代：Properties 体系（4.10）
4.10 起信号以 `signal.` 前缀的属性形式存在，浮点信号等价写法：

```python
# 写入场景级浮点信号 'conveyorSpeed'
sim.setFloatProperty(sim.handle_scene, "signal.conveyorSpeed", 0.25)
# 读取（信号不存在时返回 None，不报错）
speed = sim.getFloatProperty(sim.handle_scene, "signal.conveyorSpeed", {'noError': True})
```

Properties 优势：作用域可控（`sim.handle_app` 全局 / `sim.handle_scene` 场景 / 对象句柄局部）、Property Explorer 可视化、修改可触发事件回调。新代码优先用 Properties。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

# 写入浮点信号
sim.setFloatSignal('conveyorSpeed', 0.25)

# 读取
speed = sim.getFloatSignal('conveyorSpeed')
print(speed)  # 0.25

# 不存在的信号返回 None
missing = sim.getFloatSignal('absentSignal')
print(missing)  # None
```

## 参见

- [[wiki/01-API参考/api/api-simSetIntegerSignal]] — 整数信号（同样已弃用）
- [[wiki/01-API参考/api/api-simSetStringSignal]] — 字符串信号
- [[wiki/01-API参考/api/api-simSetDoubleSignal]] — 双精度信号（已弃用，改用 Float）
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
- [[wiki/06-工业案例/06-02-岛式车间柔性生产案例]] — 信号是状态机核心
