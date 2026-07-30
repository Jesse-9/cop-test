---
title: sim.getObjectUid
type: api
tags: [coppeliasim, 版本/v4.10, 类别/API, 难度/初级]
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simGetObjectUid.htm
related:
  - "[[wiki/01-API参考/api/api-simGetObject]]"
  - "[[wiki/01-API参考/api/api-simGetObjects]]"
  - "[[wiki/01-API参考/api/api-simGetObjectsInTree]]"
  - "[[wiki/01-API参考/api/api-simGetObjectChild]]"
  - "[[wiki/01-API参考/api/api-simGetObjectParent]]"
  - "[[wiki/01-API参考/api/api-simGetObjectAlias]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
  - "[[wiki/01-API参考/api/api-simGetObjectPosition]]"
sim_version: "4.10"
---

# sim.getObjectUid

> 获取对象的唯一标识符（UID）。整个 CoppeliaSim 会话内不会有重复 UID；但 UID 不持久化（不随对象保存到场景文件）。

## 签名

```python
int uid = sim.getObjectUid(int objectHandle)
```

## 参数

- `objectHandle`：对象句柄。

## 返回值

- `uid`：对象的唯一标识符。

## 用途

UID 用于在会话内稳定引用对象，比句柄更适合跨模块传递（句柄可能因场景重载而失效，UID 在会话内唯一）。可配合 `sim.getObjectFromUid` 反向恢复句柄。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

h = sim.getObject('/Robot/base')
uid = sim.getObjectUid(h)
print('uid:', uid)

# 反向：由 UID 取回句柄
h2 = sim.getObjectFromUid(uid)
print('handle match:', h == h2)
```

## 参见

- [[wiki/01-API参考/api/api-simGetObject]] — 按路径查句柄
- `sim.getObjectFromUid` — UID 反查句柄（手册同组函数）
- `sim.getObjectUniqueIdentifier` — 更底层的唯一标识（含持久化信息）
