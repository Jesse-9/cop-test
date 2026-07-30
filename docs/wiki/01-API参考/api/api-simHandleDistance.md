---
title: sim.handleDistance
type: api
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/中级
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simHandleDistance.htm
related:
  - "[[wiki/02-仿真建模/02-07-接近传感器]]"
  - "[[wiki/02-仿真建模/02-12-碰撞检测与距离计算]]"
  - "[[wiki/02-仿真建模/02-10-集合-Collections]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
sim_version: "4.10"
---

# sim.handleDistance

> 处理已注册的距离对象对（distance object），返回最小距离。**已弃用**，官方手册推荐改用 `sim.checkDistance`；后者无需预先注册距离对象，直接对两个实体（对象或集合）做几何查询，并返回最小距离线段的端点坐标。保留本函数及 `sim.getDistanceHandle` 是为了兼容旧场景中通过距离集合注册的对象对。

## 签名

```python
int result, float smallestDistance = sim.handleDistance(
    int distanceObjectHandle)
```

## 参数

- `distanceObjectHandle`：距离对象句柄，或：
  - `sim.handle_all` —— 处理所有已注册的距离对象。
  - `sim.handle_all_except_explicit` —— 处理所有未标记为 "explicit handling" 的距离对象。

## 返回值

- `result`：`1` 表示至少测到一个距离，`0` 表示未测到，`-1` 表示出错。
- `smallestDistance`：本次测得的最小距离，单位**米**。

## 典型用法（handle 模式）

旧式用法需先在场景中注册距离对象对（集合-集合或集合-对象），再用 `sim.getDistanceHandle` 取句柄，最后每帧 `sim.handleDistance`：

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

# 取已注册距离对象 'Distance_robot_vs_wall' 的句柄
dist_handle = sim.getDistanceHandle('Distance_robot_vs_wall')

# 仿真循环中每帧处理
sim.setStepping(True)
sim.startSimulation()
for _ in range(100):
    result, d = sim.handleDistance(dist_handle)
    if result == 1:
        print(f'最小距离：{d:.4f} m')
    sim.step()
sim.stopSimulation()
```

## 附录：同族函数

| 函数 | 说明 |
|------|------|
| `sim.getDistanceHandle(distanceObjectName)` | 按名称取距离对象句柄；名后缀 `@silentError` 在不存在时不报错。**已弃用**。 |
| `sim.checkDistance(entity1Handle, entity2Handle, threshold=0)` | 直接计算两实体间最小距离，返回 `result`、`distanceData`（7 元素 `[x1 y1 z1 x2 y2 z2 dist]`，单位米）、`objectHandlePair`；**推荐用法**，无需注册。 |

`sim.checkDistance` 推荐示例（无需 getDistanceHandle）：

```python
robot = sim.getObject('/robot')
wall = sim.getObject('/wall')
# threshold=0 或负表示不设阈值
result, data, pair = sim.checkDistance(robot, wall, 0.0)
if result == 1:
    # data = [x1,y1,z1, x2,y2,z2, dist]，均为绝对坐标，dist 单位米
    print(f'最近点对：{data[:3]} ↔ {data[3:6]}，距离 {data[6]:.4f} m')
```

## 参见

- [[wiki/02-仿真建模/02-12-碰撞检测与距离计算]] — 碰撞/距离检测概览（待建）
- [[wiki/02-仿真建模/02-10-集合-Collections]] — 集合作为检测实体的用法
- [[wiki/01-API参考/api/api-simHandleCollision]] — 碰撞对象对处理（同族弃用模式）
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
