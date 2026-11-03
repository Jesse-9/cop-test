---
title: sim.setIntegerSignal / sim.getIntegerSignal
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
  - regularApi/simSetIntegerSignal.htm
  - regularApi/simGetIntegerSignal.htm
related:
  - "[[wiki/01-API参考/api/api-simSetFloatSignal]]"
  - "[[wiki/01-API参考/api/api-simSetStringSignal]]"
  - "[[wiki/01-API参考/api/api-simSetDoubleSignal]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
  - "[[wiki/06-工业案例/06-02-岛式车间柔性生产案例]]"
sim_version: "4.10"
---

# sim.setIntegerSignal / sim.getIntegerSignal

> 整数信号的读写。信号是 CoppeliaSim 中脚本间、脚本与外部程序共享简单状态的本机制：信号名全局唯一，写入即对整个场景广播。**已弃用**——官方手册建议改用 [[wiki/01-API参考/api/api-simSetInt32Signal|sim.setInt32Signal]]，最终推荐使用 Properties 体系（见下文）。

## 签名

```python
sim.setIntegerSignal(string signalName, int signalValue)
int signalValue = sim.getIntegerSignal(string signalName)
```

## 参数

- `signalName`：信号名（字符串，全局）。
- `signalValue`（set）：整数值。
- `signalValue`（get 返回）：整数值；信号不存在时返回 `None`。

## 返回值

- `setIntegerSignal`：无。
- `getIntegerSignal`：信号值，或 `None`（信号不存在）。

## 备注

### 弃用说明
手册 `simSetIntegerSignal.htm` / `simGetIntegerSignal.htm` 顶部明确标注：

> Deprecated. See sim.setInt32Signal instead.

`setIntegerSignal` 是旧的 32 位整数接口名，保留向后兼容。新代码应直接用 `sim.setInt32Signal` / `sim.getInt32Signal`（语义等价，命名更准确）。

### 信号语义
- 信号名**全局**于场景，无命名空间隔离。
- 写入即广播：任一脚本或外部 API 客户端调用 `set*Signal` 后，其他脚本通过同名 `get*Signal` 立即可读。
- **易失性**：创建/修改该信号的脚本状态被销毁时，信号自动清除（这是与 custom data 的关键区别）。
- 信号常用于工作岛状态机、AGV 节拍同步等——本仓库早期的 `check_alive()` 实现曾用 `coppeliasimVersion` 字符串信号探测，即信号机制典型用法。

### 现代替代：Properties 体系（4.10）
4.10 起，所有内部变量/状态统一为 [[wiki/02-仿真建模/properties|Properties]]，信号也以 `signal.` 前缀的属性形式存在。整数信号等价写法：

```python
# 写入场景级整数信号 'myIntSignal'
sim.setIntProperty(sim.handle_scene, "signal.myIntSignal", 37)
# 读取（信号不存在时返回 None，不报错）
val = sim.getIntProperty(sim.handle_scene, "signal.myIntSignal", {'noError': True})
```

Properties 优势：作用域可控（`sim.handle_app` 全局 / `sim.handle_scene` 场景 / 对象句柄局部）、有 Property Explorer 可视化、修改可触发事件回调。新代码优先用 Properties。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

# 写入整数信号
sim.setIntegerSignal('productionCount', 42)

# 读取（同一场景内任意脚本/客户端均可）
count = sim.getIntegerSignal('productionCount')
print(count)  # 42

# 不存在的信号返回 None
missing = sim.getIntegerSignal('absentSignal')
print(missing)  # None
```

## 参见

- [[wiki/01-API参考/api/api-simSetFloatSignal]] — 浮点信号（同样已弃用）
- [[wiki/01-API参考/api/api-simSetStringSignal]] — 字符串信号
- [[wiki/01-API参考/api/api-simSetDoubleSignal]] — 双精度信号（已弃用，改用 Float）
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
- [[wiki/06-工业案例/06-02-岛式车间柔性生产案例]] — 信号是状态机核心
