---
title: sim.getObjects
type: api
tags: [coppeliasim, 版本/v4.10, 类别/API, 难度/初级]
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simGetObjects.htm
related:
  - "[[wiki/01-API参考/api/api-simGetObject]]"
  - "[[wiki/01-API参考/api/api-simGetObjectsInTree]]"
  - "[[wiki/01-API参考/api/api-simGetObjectChild]]"
  - "[[wiki/01-API参考/api/api-simGetObjectParent]]"
  - "[[wiki/01-API参考/api/api-simGetObjectAlias]]"
  - "[[wiki/01-API参考/api/api-simGetObjectUid]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
  - "[[wiki/01-API参考/api/api-simGetObjectPosition]]"
sim_version: "4.10"
---

# sim.getObjects

> 按类型枚举场景对象句柄。在循环中从 index=0 递增直至返回 -1，可遍历场景中全部该类型对象。

## 签名

```python
int objectHandle = sim.getObjects(int index, int objectType)
```

## 参数

- `index`：对象索引，从 0 起。
- `objectType`：对象类型常量。如 `sim.sceneobject_shape`、`sim.sceneobject_joint` 等（见对象类型常量），或 `sim.handle_all` 表示任意类型。

## 返回值

- `objectHandle`：对象句柄；该索引无对象时返回 -1。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

# 枚举场景中所有关节对象
i = 0
while True:
    h = sim.getObjects(i, sim.sceneobject_joint)
    if h < 0:
        break
    print(i, sim.getObjectAlias(h, 0))
    i += 1

# 枚举所有对象（任意类型）
i = 0
handles = []
while True:
    h = sim.getObjects(i, sim.handle_all)
    if h < 0:
        break
    handles.append(h)
    i += 1
print('total objects:', len(handles))
```

## 参见

- [[wiki/01-API参考/api/api-simGetObject]] — 按路径查单个句柄
- [[wiki/01-API参考/api/api-simGetObjectsInTree]] — 按子树枚举
