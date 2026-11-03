---
title: sim.setStringSignal / sim.getStringSignal
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
  - regularApi/simSetStringSignal.htm
  - regularApi/simGetStringSignal.htm
related:
  - "[[wiki/01-API参考/api/api-simSetIntegerSignal]]"
  - "[[wiki/01-API参考/api/api-simSetFloatSignal]]"
  - "[[wiki/01-API参考/api/api-simSetDoubleSignal]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
  - "[[wiki/06-工业案例/06-02-岛式车间柔性生产案例]]"
sim_version: "4.10"
---

# sim.setStringSignal / sim.getStringSignal

> 字符串信号的读写。信号是 CoppeliaSim 中脚本间、脚本与外部程序共享简单状态的本机制：信号名全局唯一，写入即对整个场景广播。**已弃用**——手册建议改用 Properties 体系（`signal.` 前缀）或 `setBufferSignal`。

## 签名

```python
sim.setStringSignal(string signalName, string/bytes signalValue)
bytes signalValue = sim.getStringSignal(string signalName)
```

## 参数

- `signalName`：信号名（字符串，全局）。
- `signalValue`（set）：字符串或字节串，**可包含嵌入的零字节**（即不按 C 字符串截断，可用于二进制数据）。
- `signalValue`（get 返回）：字节串（`bytes`）；信号不存在时返回 `None`。

## 返回值

- `setStringSignal`：无。
- `getStringSignal`：字节串，或 `None`（信号不存在）。

> 注意 Python 端 `get` 返回 `bytes` 而非 `str`，读取后若需文本需 `val.decode('utf-8')`。

## 备注

### 弃用说明
手册 `simSetStringSignal.htm` / `simGetStringSignal.htm` 顶部标注：

> Deprecated. See properties instead

字符串信号可携带任意字节（含 `\0`），与 `setBufferSignal` 等价；4.10 推荐迁移到 [[wiki/02-仿真建模/properties|Properties]] 的 `setBufferProperty`（同样支持二进制）。

### 信号语义
- 信号名**全局**于场景，无命名空间隔离。
- 写入即广播：任一脚本或外部 API 客户端调用 `set*Signal` 后，其他脚本通过同名 `get*Signal` 立即可读。
- **易失性**：创建/修改该信号的脚本状态被销毁时，信号自动清除。
- 字符串/缓冲信号常用于传递结构化数据（如序列化表、JSON、配置串）——本仓库早期的 `check_alive()` 实现曾用 `coppeliasimVersion` 字符串信号探测版本，即典型用法（部分实例该信号未设置，返回 `None`，代码已加 `getSimulationState()` 回退探针）。

### 现代替代：Properties 体系（4.10）
4.10 起信号以 `signal.` 前缀的属性形式存在，字符串/缓冲信号等价写法：

```python
import json
# 写入场景级缓冲信号 'config'（可携带任意字节）
sim.setBufferProperty(sim.handle_scene, "signal.config", json.dumps({'rate': 0.05}).encode('utf-8'))
# 读取（信号不存在时返回 None，不报错）
raw = sim.getBufferProperty(sim.handle_scene, "signal.config", {'noError': True})
if raw:
    cfg = json.loads(raw.decode('utf-8'))
```

Properties 优势：作用域可控（`sim.handle_app` 全局 / `sim.handle_scene` 场景 / 对象句柄局部）、Property Explorer 可视化、修改可触发事件回调。新代码优先用 Properties。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

# 写入字符串信号（可含二进制，这里演示文本）
sim.setStringSignal('currentTask', 'pick-place-A')

# 读取（返回 bytes）
val = sim.getStringSignal('currentTask')
print(val)           # b'pick-place-A'
print(val.decode())  # 'pick-place-A'

# 不存在的信号返回 None
missing = sim.getStringSignal('absentSignal')
print(missing)  # None
```

## 参见

- [[wiki/01-API参考/api/api-simSetIntegerSignal]] — 整数信号（同样已弃用）
- [[wiki/01-API参考/api/api-simSetFloatSignal]] — 浮点信号（同样已弃用）
- [[wiki/01-API参考/api/api-simSetDoubleSignal]] — 双精度信号（已弃用，改用 Float）
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
- [[wiki/06-工业案例/06-02-岛式车间柔性生产案例]] — 信号是状态机核心
