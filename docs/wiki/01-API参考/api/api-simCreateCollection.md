---
title: sim.createCollection
type: api
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/中级
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simCreateCollection.htm
related:
  - "[[wiki/02-仿真建模/02-10-集合-Collections]]"
  - "[[wiki/01-API参考/api/api-simHandleCollision]]"
  - "[[wiki/01-API参考/api/api-simHandleDistance]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
sim_version: "4.10"
---

# sim.createCollection

> 创建一个新的集合（collection）。集合是一组对象的动态聚合，可作为 `sim.checkCollision`/`sim.checkDistance` 等几何查询的实体，也可用于批量设置属性。在 simulation script、customization script 或 main script 中创建的集合会在脚本结束时自动销毁。

## 签名

```python
int collectionHandle = sim.createCollection(int options = 0)
```

## 参数

- `options`：位编码选项，目前仅一位：
  - bit 0（值 1）：集合覆盖其成员对象的 collidable、measurable、detectable 属性及可见性状态。即查询该集合时以集合自身属性为准，忽略成员对象各自的设置。

## 返回值

- `collectionHandle`：新集合的句柄。后续可用于 `sim.addItemToCollection`、`sim.getCollectionObjects`、`sim.destroyCollection`，或作为碰撞/距离检测实体传入 `sim.checkCollision`/`sim.checkDistance`。

## 示例

创建集合、添加对象树，并用作碰撞检测实体：

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

# 取机器人基座与障碍物句柄
robot = sim.getObject('/robot')
obstacle = sim.getObject('/obstacle')

# 1) 创建集合（不覆盖成员属性）
coll = sim.createCollection()

# 2) 把整棵机器人树加入集合
sim.addItemToCollection(coll, sim.handle_tree, robot, 0)

# 3) 用集合作为实体做碰撞检测（无需注册碰撞对象）
sim.setStepping(True)
sim.startSimulation()
for _ in range(100):
    result, pair = sim.checkCollision(coll, obstacle)
    if result:
        print(f'机器人碰撞障碍物：{pair}')
    sim.step()
sim.stopSimulation()

# 4) 查询集合成员
members = sim.getCollectionObjects(coll)
print(f'集合成员数：{len(members)}')

# 5) 销毁集合（脚本中创建的可省略，脚本结束自动销毁）
sim.destroyCollection(coll)
```

## 附录：同族函数

| 函数 | 说明 |
|------|------|
| `sim.getCollectionHandle(collectionName)` | 按名称取集合句柄；名后缀 `@silentError` 在不存在时不报错。**已弃用**，手册建议用 `sim.createCollection` + `sim.addItemToCollection` 动态创建集合，而非按名称引用场景中预置的集合。 |
| `sim.getCollectionObjects(collectionHandle)` | 返回集合中所有对象句柄列表 `[h1, h2, ...]`。 |
| `sim.destroyCollection(collectionHandle)` | 显式销毁集合。脚本中创建的集合在脚本结束时自动销毁，无需手动调用。 |

## 参见

- [[wiki/02-仿真建模/02-10-集合-Collections]] — 集合概念与用途
- [[wiki/01-API参考/api/api-simAddItemToCollection]] — 向集合添加条目
- [[wiki/01-API参考/api/api-simHandleCollision]] — 碰撞检测（集合作为实体）
- [[wiki/01-API参考/api/api-simHandleDistance]] — 距离检测（集合作为实体）
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
