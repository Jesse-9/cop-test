---
title: sim.getObjectChild
type: api
tags: [coppeliasim, 版本/v4.10, 类别/API, 难度/初级]
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simGetObjectChild.htm
related:
  - "[[wiki/01-API参考/api/api-simGetObject]]"
  - "[[wiki/01-API参考/api/api-simGetObjects]]"
  - "[[wiki/01-API参考/api/api-simGetObjectsInTree]]"
  - "[[wiki/01-API参考/api/api-simGetObjectParent]]"
  - "[[wiki/01-API参考/api/api-simGetObjectAlias]]"
  - "[[wiki/01-API参考/api/api-simGetObjectUid]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
  - "[[wiki/01-API参考/api/api-simGetObjectPosition]]"
sim_version: "4.10"
---

# sim.getObjectChild

> 按索引获取对象的某个子对象句柄。通过递增 index 直到返回 -1 可遍历所有子对象。

## 签名

```python
int childHandle = sim.getObjectChild(int objectHandle, int index)
```

## 参数

- `objectHandle`：父对象句柄；或 `sim.handle_scene` 取孤儿对象（无父对象的对象）。
- `index`：子对象位置索引，从 0 起。

## 返回值

- `childHandle`：子对象句柄；该索引无子对象时返回 -1。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

# 遍历某对象的所有子对象
parent = sim.getObject('/Robot')
i = 0
while True:
    child = sim.getObjectChild(parent, i)
    if child < 0:
        break
    print(i, sim.getObjectAlias(child, 0))
    i += 1

# 枚举场景顶层（无父）对象
i = 0
while True:
    h = sim.getObjectChild(sim.handle_scene, i)
    if h < 0:
        break
    print('top-level:', sim.getObjectAlias(h, 0))
    i += 1
```

## 参见

- [[wiki/01-API参考/api/api-simGetObjectParent]] — 反向取父对象
- [[wiki/01-API参考/api/api-simGetObjectsInTree]] — 一次性获取整棵子树
