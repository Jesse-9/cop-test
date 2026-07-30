---
title: sim.getObjectParent
type: api
tags: [coppeliasim, 版本/v4.10, 类别/API, 难度/初级]
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simGetObjectParent.htm
related:
  - "[[wiki/01-API参考/api/api-simGetObject]]"
  - "[[wiki/01-API参考/api/api-simGetObjects]]"
  - "[[wiki/01-API参考/api/api-simGetObjectsInTree]]"
  - "[[wiki/01-API参考/api/api-simGetObjectChild]]"
  - "[[wiki/01-API参考/api/api-simGetObjectAlias]]"
  - "[[wiki/01-API参考/api/api-simGetObjectUid]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
  - "[[wiki/01-API参考/api/api-simGetObjectPosition]]"
sim_version: "4.10"
---

# sim.getObjectParent

> 获取对象的父对象句柄。

## 签名

```python
int parentHandle = sim.getObjectParent(int objectHandle)
```

## 参数

- `objectHandle`：对象句柄。

## 返回值

- `parentHandle`：父对象句柄；无父对象时返回 -1。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

h = sim.getObject('/Robot/base')
parent = sim.getObjectParent(h)
if parent < 0:
    print('no parent')
else:
    print('parent:', sim.getObjectAlias(parent, 0))

# 沿父链向上回溯至根
cur = sim.getObject('/Robot/joint6')
chain = []
while cur >= 0:
    chain.append(sim.getObjectAlias(cur, 0))
    cur = sim.getObjectParent(cur)
print(' <- '.join(chain))
```

## 参见

- [[wiki/01-API参考/api/api-simGetObjectChild]] — 取子对象
- [[wiki/01-API参考/api/api-simGetObjectsInTree]] — 枚举整棵子树
- `sim.setObjectParent` — 设置父对象（手册同组函数）
