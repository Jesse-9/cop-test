---
title: sim.addLog
type: api
tags: [coppeliasim, 版本/v4.10, 类别/API, 难度/中级]
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simAddLog.htm
related:
  - "[[wiki/07-API脚本示例/07-01-Lua基本脚本模式]]"
  - "[[wiki/01-API参考/01-06-脚本回调函数清单]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
sim_version: "4.10"
---

# sim.addLog

> 向控制台或状态栏输出一条日志消息。按 verbosity 级别决定输出通道；`sim.verbosity_scripterrors` 还会额外抛出一个错误。

## 签名

```python
sim.addLog(int verbosity, string message)
```

## 参数

- `verbosity`：日志级别。常用值：
  - `sim.verbosity_scripterrors` — 错误（输出并额外抛错）
  - `sim.verbosity_scriptwarnings` — 警告
  - `sim.verbosity_scriptinfos` — 信息
  - 与 `sim.verbosity_undecorated` 组合可输出裸文本（不带前缀装饰）。
- `message`：消息内容。传 `None`/`nil` 清空状态栏。

## 返回值

无。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

# 信息级日志（状态栏默认级别）
sim.addLog(sim.verbosity_scriptinfos, '岛 A 节拍对齐，进入供料门控')

# 警告级
sim.addLog(sim.verbosity_scriptwarnings, '工位 B 超时 2.3s')

# 裸文本：与 undecorated 组合，去掉前缀装饰
sim.addLog(sim.verbosity_scriptinfos | sim.verbosity_undecorated, 'raw payload: 0.42')

# 清空状态栏
sim.addLog(sim.verbosity_scriptinfos, None)
```

## 参见

- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
- [[wiki/07-API脚本示例/07-01-Lua基本脚本模式]] — Lua 脚本日志/回调模式
- [[wiki/01-API参考/01-06-脚本回调函数清单]] — 脚本回调函数清单（待建）
- 附录函数：
  - `sim.getStackTraceback(scriptHandle)` — **Lua only**，取回并清除该脚本最后一次生成的栈回溯，用于错误诊断（`regularApi/simGetStackTraceback.htm`）
- verbosity 常量全集见官方手册 `apiConstants.htm`（`sim.verbosity_none`/`errors`/`warnings`/`scripterrors`/`scriptwarnings`/`scriptinfos`/`debug`/`trace`/`tracelua`/`traceall` 等）
- 官方手册 `regularApi/simAddLog.htm`
