---
title: sim.handleCollision
type: api
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/中级
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simHandleCollision.htm
related:
  - "[[wiki/02-仿真建模/02-07-接近传感器]]"
  - "[[wiki/02-仿真建模/02-12-碰撞检测与距离计算]]"
  - "[[wiki/02-仿真建模/02-10-集合-Collections]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
sim_version: "4.10"
---

# sim.handleCollision

> 处理已注册的碰撞对象对（collision object），返回碰撞计数。**已弃用**，官方手册推荐改用 `sim.checkCollision`；后者无需预先注册碰撞对象，直接对两个实体（对象或集合）做几何查询。保留本函数及 `sim.getCollisionHandle` 是为了兼容旧场景中通过碰撞集合注册的对象对。

## 签名

```python
int collisionCount, list collidingObjectHandles = sim.handleCollision(
    int collisionObjectHandle)
```

## 参数

- `collisionObjectHandle`：碰撞对象句柄，或：
  - `sim.handle_all` —— 处理所有已注册的碰撞对象。
  - `sim.handle_all_except_explicit` —— 处理所有未标记为 "explicit handling" 的碰撞对象。

## 返回值

- `collisionCount`：碰撞次数；`-1` 表示操作失败（如句柄无效）。
- `collidingObjectHandles`：发生碰撞的两个对象句柄 `[h1, h2]`。**仅当显式传入碰撞对象句柄时**才返回此值。

## 典型用法（handle 模式）

旧式用法需先在场景中注册碰撞对象对（集合-集合或集合-对象），再用 `sim.getCollisionHandle` 取句柄，最后每帧 `sim.handleCollision`：

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

# 取已注册碰撞对象 'Collision_robot_vs_obstacle' 的句柄
coll_handle = sim.getCollisionHandle('Collision_robot_vs_obstacle')

# 仿真循环中每帧处理
sim.setStepping(True)
sim.startSimulation()
for _ in range(100):
    cnt, pair = sim.handleCollision(coll_handle)
    if cnt > 0:
        print(f'碰撞：{pair}')
    sim.step()
sim.stopSimulation()
```

## 附录：同族函数

| 函数 | 说明 |
|------|------|
| `sim.getCollisionHandle(collisionObjectName)` | 按名称取碰撞对象句柄；名后缀 `@silentError` 在不存在时不报错。**已弃用**。 |
| `sim.checkCollision(entity1Handle, entity2Handle)` | 直接检查两个实体（对象或集合）是否碰撞，返回 `result` (0/1) 与 `collidingObjectHandles`；**推荐用法**，无需注册。 |
| `sim.checkCollisionEx(entity1Handle, entity2Handle)` | 返回两实体间所有相交线段（`intersections`），用于需要完整相交几何的场景。 |

`sim.checkCollision` 推荐示例（无需 getCollisionHandle）：

```python
robot = sim.getObject('/robot')
obstacle = sim.getObject('/obstacle')
result, pair = sim.checkCollision(robot, obstacle)
# result: 0=无碰撞, 1=碰撞
```

## 参见

- [[wiki/02-仿真建模/02-12-碰撞检测与距离计算]] — 碰撞/距离检测概览（待建）
- [[wiki/02-仿真建模/02-10-集合-Collections]] — 集合作为检测实体的用法
- [[wiki/01-API参考/api/api-simHandleDistance]] — 距离对象对处理（同族弃用模式）
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
