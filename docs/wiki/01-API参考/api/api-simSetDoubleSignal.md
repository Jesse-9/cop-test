---
title: sim.setDoubleSignal / sim.getDoubleSignal
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
  - regularApi/simSetDoubleSignal.htm
  - regularApi/simGetDoubleSignal.htm
related:
  - "[[wiki/01-API参考/api/api-simSetIntegerSignal]]"
  - "[[wiki/01-API参考/api/api-simSetFloatSignal]]"
  - "[[wiki/01-API参考/api/api-simSetStringSignal]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
  - "[[wiki/06-工业案例/06-02-岛式车间柔性生产案例]]"
sim_version: "4.10"
---

# sim.setDoubleSignal / sim.getDoubleSignal

> 双精度浮点信号的读写。**已弃用**——手册建议改用 [[wiki/01-API参考/api/api-simSetFloatSignal|sim.setFloatSignal]]（CoppeliaSim 内部统一 32 位浮点），Float 又进一步被 Properties 取代。信号是脚本间共享简单状态的本机制：信号名全局，写入即广播。

## 签名

```python
sim.setDoubleSignal(string signalName, float signalValue)
float signalValue = sim.getDoubleSignal(string signalName)
```

## 参数

- `signalName`：信号名（字符串，全局）。
- `signalValue`（set）：浮点值（手册 Lua/Python 签名记为 `float`，因内部即 32 位浮点）。
- `signalValue`（get 返回）：浮点值；信号不存在或操作失败时返回 `None`。

## 返回值

- `setDoubleSignal`：无。C 接口返回 `-1` 表示失败。
- `getDoubleSignal`：信号值，或 `None`（信号不存在/操作不成功）。
  - C 接口返回状态码：`-1` 失败、`0` 信号不存在、`1` 成功取值。

## 备注

### 弃用链
手册 `simSetDoubleSignal.htm` / `simGetDoubleSignal.htm` 顶部标注：

> Deprecated. Use sim.setFloatSignal instead / sim.getFloatSignal instead

`Double` 是历史遗留的 64 位接口名，但 CoppeliaSim 物理与脚本层内部统一用 32 位浮点，故 Double 与 Float 实际等价。迁移路径：**Double → Float → Properties**。新代码不应再用 Double。

### 信号语义
- 信号名**全局**于场景，无命名空间隔离。
- 写入即广播：任一脚本或外部 API 客户端调用 `set*Signal` 后，其他脚本通过同名 `get*Signal` 立即可读。
- **易失性**：创建/修改该信号的脚本状态被销毁时，信号自动清除。

### 现代替代：Properties 体系（4.10）
4.10 起信号以 `signal.` 前缀的属性形式存在，浮点信号等价写法：

```python
# 写入场景级浮点信号 'loadFactor'
sim.setFloatProperty(sim.handle_scene, "signal.loadFactor", 0.75)
# 读取（信号不存在时返回 None，不报错）
val = sim.getFloatProperty(sim.handle_scene, "signal.loadFactor", {'noError': True})
```

Properties 优势：作用域可控（`sim.handle_app` 全局 / `sim.handle_scene` 场景 / 对象句柄局部）、Property Explorer 可视化、修改可触发事件回调。新代码优先用 Properties。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

# 写入（内部按 32 位浮点存储）
sim.setDoubleSignal('loadFactor', 0.75)

# 读取
val = sim.getDoubleSignal('loadFactor')
print(val)  # 0.75

# 不存在的信号返回 None
missing = sim.getDoubleSignal('absentSignal')
print(missing)  # None
```

## 参见

- [[wiki/01-API参考/api/api-simSetIntegerSignal]] — 整数信号（同样已弃用）
- [[wiki/01-API参考/api/api-simSetFloatSignal]] — 浮点信号（Double 的推荐替代，同样已弃用）
- [[wiki/01-API参考/api/api-simSetStringSignal]] — 字符串信号
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
- [[wiki/06-工业案例/06-02-岛式车间柔性生产案例]] — 信号是状态机核心
