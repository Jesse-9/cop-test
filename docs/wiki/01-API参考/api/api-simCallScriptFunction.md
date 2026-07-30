---
title: sim.callScriptFunction
type: api
tags: [coppeliasim, 版本/v4.10, 类别/API, 难度/中级]
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simCallScriptFunction.htm
related:
  - "[[wiki/07-API脚本示例/07-01-Lua基本脚本模式]]"
  - "[[wiki/01-API参考/01-06-脚本回调函数清单]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
sim_version: "4.10"
---

# sim.callScriptFunction

> 跨脚本调用用户定义的函数（user callback）。可由插件、主客户端应用或另一个脚本发起，调用目标脚本中定义的函数。目标脚本必须已初始化——调用 simulation script 时仿真须处于运行态。

## 签名

```python
... = sim.callScriptFunction(string functionName, int scriptHandle, ...)
```

## 参数

- `functionName`：要调用的函数名（目标脚本中已定义）。
- `scriptHandle`：目标脚本的句柄。可传 `sim.handle_self` 指向当前脚本自身。脚本句柄通过 `sim.getScript` 获取；对于 script objects（4.10 的 simulation script / customization script）则应使用 `sim.getObject` 按路径/别名查询。
- `...`：任意数量参数，原样转交给被调函数。

## 返回值

- `...`：被调函数返回的任意数量值。

## 脚本间函数调用机制

4.10 的脚本体系由 **script objects**（simulation script、customization script）+ main script + add-on + sandbox 构成（见 `scripts.htm`/`scriptObjects.htm`）。各脚本是独立的执行单元，无法直接访问彼此的局部符号；`sim.callScriptFunction` 提供了受控的跨脚本调用入口：

1. **目标定位**：`scriptHandle` 决定调用发往哪个脚本。仿真运行时，simulation script 才被初始化，此期间对其调用才会成功；非运行态调用 simulation script 会失败。
2. **函数名约定**：`functionName` 必须是目标脚本全局作用域中已存在的函数名；不存在或非 callable 会抛错。
3. **参数传递**：调用方与被调方按位置传参/返回，不经过共享全局表，避免脚本间状态污染。
4. **C/C++ 视角**：从插件调用时，数据经栈（stack）交换，可传递复杂数据结构；建议使用 `programming/include/simStack` 中的 helper 类降低出错率（见 `sim.executeScriptString` 说明）。

## 示例

下列 Lua 脚本注册一个函数供外部调用；Python ZMQ 客户端经 `sim.callScriptFunction` 触发它。

**目标脚本（Lua，挂在某 script object 上）：**
```lua
-- function sysCall_init()
--     -- 初始化
-- end

function computePickOffset(partName, x, y)
    -- 被外部脚本/插件调用的用户函数
    local offset = x + y
    return partName, offset
end
```

**调用方（Python ZMQ Remote API）：**
```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

# 1. 仿真须处于运行态，simulation script 才被初始化
sim.startSimulation()

# 2. 定位目标脚本对象（script object 用 getObject 按路径查）
script_h = sim.getObject('/robot')  # script object 句柄

# 3. 调用脚本中定义的 computePickOffset
name, offset = sim.callScriptFunction('computePickOffset', script_h, 'gear', 0.3, 0.1)
print(name, offset)  # 'gear' 0.4

sim.stopSimulation()
```

## 参见

- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览（Python/Lua/C 三语言）
- [[wiki/07-API脚本示例/07-01-Lua基本脚本模式]] — Lua 脚本回调基本模式
- [[wiki/01-API参考/01-06-脚本回调函数清单]] — 脚本回调函数清单（待建）
- 附录函数：
  - `sim.getScriptFunctions(scriptHandle)` — 取目标脚本可调函数的元表对象，可 `obj:fn()` 调用（`regularApi/simGetScriptFunctions.htm`）
  - `sim.executeScriptString(stringToExecute, scriptHandle)` — 在指定脚本内执行任意代码串，可选 `@python`/`@lua` 前缀强制语言；返回 `result, executionResult`（`regularApi/simExecuteScriptString.htm`）
  - `sim.getApiFunc(scriptHandle, apiWord)` — 按词匹配 API 函数/变量，用于自动补全；`+` 前缀只列函数、`-` 只列变量（`regularApi/simGetApiFunc.htm`）
  - `sim.getApiInfo(scriptHandle, apiWord)` — 返回某 API 函数/变量的 call tip / info 文本（`regularApi/simGetApiInfo.htm`）
- 官方手册 `regularApi/simCallScriptFunction.htm`
