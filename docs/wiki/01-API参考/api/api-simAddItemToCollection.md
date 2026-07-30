---
title: sim.addItemToCollection
type: api
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/中级
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simAddItemToCollection.htm
related:
  - "[[wiki/02-仿真建模/02-10-集合-Collections]]"
  - "[[wiki/01-API参考/api/api-simHandleCollision]]"
  - "[[wiki/01-API参考/api/api-simHandleDistance]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
sim_version: "4.10"
---

# sim.addItemToCollection

> 向集合添加（或移除）一个对象或一组对象。通过 `what` 指定条目类型（单对象 / 全场景 / 树 / 链），通过 `options` 位编码控制是添加还是移除、以及是否包含树/链的根节点。集合需先用 `sim.createCollection` 创建。

## 签名

```python
sim.addItemToCollection(int collectionHandle, int what, int objectHandle, int options)
```

## 参数

- `collectionHandle`：目标集合的句柄（由 `sim.createCollection` 返回）。
- `what`：要添加的对象（或对象组）的类型，取以下常量之一：
  - `sim.handle_single` —— 单个对象。
  - `sim.handle_all` —— 场景中的全部对象。
  - `sim.handle_tree` —— 一棵对象树（从 `objectHandle` 向下的所有后代）。
  - `sim.handle_chain` —— 一条对象链（即倒置的树，从 `objectHandle` 向上到根）。
- `objectHandle`：对象句柄。当 `what` 为 `sim.handle_all` 时此参数被忽略，但仍需传入（可传 `0`）。
- `options`：位编码选项：
  - bit 0（值 1）：将指定对象（或对象组）从集合中**移除**；否则为**添加**。
  - bit 1（值 2）：当 `what` 为 `sim.handle_tree` 或 `sim.handle_chain` 时，**排除** `objectHandle` 自身（即树根/链端不包含在组内）。

## 返回值

无。

## 示例

创建集合，分别添加单对象、整棵树（排除根）、全场景，并用于碰撞检测：

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

robot_base = sim.getObject('/robot')
tool = sim.getObject('/robot/tool')
obstacle = sim.getObject('/obstacle')

# 创建集合
coll = sim.createCollection()

# 1) 添加单个对象
sim.addItemToCollection(coll, sim.handle_single, tool, 0)

# 2) 添加整棵机器人树，但排除树根 robot_base（bit 1 置位）
sim.addItemToCollection(coll, sim.handle_tree, robot_base, 2)

# 3) 用集合与障碍物做碰撞检测
result, pair = sim.checkCollision(coll, obstacle)
print('碰撞' if result else '无碰撞')

# 4) 从集合中移除 tool（bit 0 置位）
sim.addItemToCollection(coll, sim.handle_single, tool, 1)

# 5) 查询当前集合成员
members = sim.getCollectionObjects(coll)
print(f'移除后成员数：{len(members)}')

sim.destroyCollection(coll)
```

## 说明

- `sim.handle_tree` 是最常用的条目类型：一次加入机器人或夹爪的全部连杆，便于整组做碰撞/距离查询。
- `sim.handle_chain` 适用于从末端执行器沿父链向上收集对象（倒置树）。
- 多次调用 `sim.addItemToCollection` 可累加条目；同一对象重复添加不会产生重复成员。
- 要清空集合，可对每个已添加条目用 `options=1` 逐项移除，或直接 `sim.destroyCollection` 后重建。

## 参见

- [[wiki/02-仿真建模/02-10-集合-Collections]] — 集合概念与用途
- [[wiki/01-API参考/api/api-simCreateCollection]] — 创建集合（含同族函数附录）
- [[wiki/01-API参考/api/api-simHandleCollision]] — 碰撞检测（集合作为实体）
- [[wiki/01-API参考/api/api-simHandleDistance]] — 距离检测（集合作为实体）
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
