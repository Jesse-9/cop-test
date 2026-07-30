---
title: 集合 Collections
type: concept
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/建模
  - 难度/中级
created: 2026-07-24
updated: 2026-07-24
status: published
source:
  - collections.htm
  - entities.htm
  - collidableObjects.htm
  - measurableObjects.htm
  - detectableObjects.htm
related:
  - "[[wiki/00-基础知识/00-04-场景与模型管理]]"
  - "[[wiki/02-仿真建模/02-12-碰撞检测与距离计算]]"
  - "[[wiki/02-仿真建模/02-07-接近传感器]]"
  - "[[wiki/01-API参考/api/api-simCreateCollection]]"
  - "[[wiki/01-API参考/api/api-simAddItemToCollection]]"
sim_version: "4.10"
---

# 集合 Collections

> 集合（collection）是用户定义的场景对象集合，用于把多个对象当作一个整体引用；集合与场景对象同属实体（entity）。

## 概念

集合是用户定义的场景对象集合。集合与场景对象都称为**实体**（entity）——实体即"场景对象或集合"的统称。三者关系为：

```
entity ──┬── scene object
         └── collection（由若干 scene object 组成）
```

集合的典型用途是把一台机器人的所有构件打包成整体。例如检测两机器人碰撞时，把碰撞检测对定义为 `(collectionRobot1; collectionRobot2)`，算法就会检查第一个集合中任一对象是否与第二个集合中任一对象发生干涉，而不必逐对列举构件。

## 可碰撞 / 可测量 / 可检测

集合本身是**可碰撞（collidable）、可测量（measurable）、可检测（detectable）**实体：

- 可用于与其他可碰撞实体做碰撞检测；
- 可用于与其他可测量实体做最小距离计算；
- 可被接近传感器检测。

但集合具备这些属性并不代表其包含的对象都具备：

| 操作 | 实际测试范围 |
|------|--------------|
| 碰撞检测 | 仅集合中**可碰撞对象**子集与另一可碰撞实体测试 |
| 距离测量 | 仅集合中**可测量对象**子集与另一可测量实体测量 |
| 传感器检测 | 仅集合中**可检测对象**子集能被接近传感器检测 |

集合可以**覆盖**其对象的这些属性（见下文 `createCollection` 的 `options` bit 0），从而统一强制开启或关闭集合内对象的可碰撞/可测量/可检测及可见性。

> 个体对象的这三项属性默认对非基本体形状、OC 树、点云开启，可在场景对象属性对话框中或通过 `sim.setObjectSpecialProperty` 单独设置；若对象属于某个模型，还可能被模型属性覆盖。点式对象（Dummy、点云）只能与体式对象（OC 树）碰撞，且不能被射线型/随机型接近传感器检测。

## 条目（item）与组合方式

集合由一个或多个**条目**以加法或减法组合而成。支持的条目类型：

- **单个对象**（`sim.handle_single`）：仅该对象本身。
- **对象树**（`sim.handle_tree`）：某对象的所有后代；树生长或收缩时条目动态跟随。
- **对象链**（`sim.handle_chain`）：沿某对象向上追溯所有祖先；链变化时同样动态跟随。
- **全部场景对象**（`sim.handle_all`）：场景中所有对象。

集合不是静态的，会不断被重新求值以确定其当前包含的对象。因此同一组目标对象往往有多种等价定义方式，按需选择最简洁的即可。

## 创建 / 添加 / 销毁

相关 Regular API（Python / Lua / C 三栏一致，函数名以 `sim.` 前缀小驼峰调用）：

| 函数 | 作用 |
|------|------|
| `sim.createCollection(options)` | 新建集合，返回句柄。`options` bit 0=1 时集合覆盖其对象的可碰撞/可测量/可检测属性及可见性。 |
| `sim.addItemToCollection(collectionHandle, what, objectHandle, options)` | 向集合添加/移除条目。`what` 取 `sim.handle_single` / `sim.handle_all` / `sim.handle_tree` / `sim.handle_chain`；`options` bit 0=1 表示移除该条目，bit 1=2 表示树/链不含基准对象本身。 |
| `sim.getCollectionObjects(collectionHandle)` | 取集合当前包含的对象句柄数组。 |
| `sim.destroyCollection(collectionHandle)` | 销毁集合。 |

> 在仿真脚本、定制脚本或 main script 中创建的集合，脚本结束时自动销毁。

## 示例

下图所示集合包含 4 个对象（着色的 object2/4/6/7），其外还有 object1 作为树根。可用两种等价方式定义。

### 方式一：逐个对象相加

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

# Collection = object2 + object4 + object6 + object7
object2 = sim.getObject('/object2')
object4 = sim.getObject('/object4')
object6 = sim.getObject('/object6')
object7 = sim.getObject('/object7')

collection_handle = sim.createCollection(0)
sim.addItemToCollection(collection_handle, sim.handle_single, object2, 0)
sim.addItemToCollection(collection_handle, sim.handle_single, object4, 0)
sim.addItemToCollection(collection_handle, sim.handle_single, object6, 0)
sim.addItemToCollection(collection_handle, sim.handle_single, object7, 0)

# 查询当前包含的对象
handles = sim.getCollectionObjects(collection_handle)
print(len(handles))   # 4
```

### 方式二：全场景对象减去 object1 子树再加 object6

```python
# Collection = <all objects> - <tree at object1> + <object6>
object1 = sim.getObject('/object1')
object6 = sim.getObject('/object6')

collection_handle = sim.createCollection(0)
sim.addItemToCollection(collection_handle, sim.handle_all, -1, 0)        # + 全部对象
sim.addItemToCollection(collection_handle, sim.handle_tree, object1, 1)  # - object1 子树（bit0=1 移除）
sim.addItemToCollection(collection_handle, sim.handle_single, object6, 0)  # + object6

# 用完销毁
sim.destroyCollection(collection_handle)
```

## 覆盖属性

`sim.createCollection(1)`（`options` bit 0 置位）使集合覆盖其对象的可碰撞/可测量/可检测属性及可见性状态，便于对整组对象统一开关这些特性，而不必逐个修改。

## 参见

- [[wiki/00-基础知识/00-04-场景与模型管理]] — 场景对象与模型组织
- [[wiki/02-仿真建模/02-12-碰撞检测与距离计算]] — 碰撞检测对与距离计算使用集合（待建）
- [[wiki/02-仿真建模/02-07-接近传感器]] — 接近传感器检测集合中可检测对象（待建）
- [[wiki/01-API参考/api/api-simCreateCollection]] — 新建集合（待建）
- [[wiki/01-API参考/api/api-simAddItemToCollection]] — 向集合添加条目（待建）
