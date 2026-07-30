---
title: simGeom 几何例程插件
type: reference
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/运动规划
  - 难度/中级
created: 2026-07-24
updated: 2026-07-24
status: published
source:
  - simGeom.htm
  - geometricPlugin.htm
  - geometricCalculations.htm
  - coppeliaGeometricRoutines.htm
related:
  - "[[wiki/02-仿真建模/02-12-碰撞检测与距离计算]]"
  - "[[wiki/02-仿真建模/02-07-接近传感器]]"
  - "[[wiki/01-API参考/01-04-插件API总览]]"
  - "[[wiki/01-API参考/01-05-几何与运动学例程]]"
  - "[[wiki/01-API参考/api/api-simCheckProximitySensor]]"
sim_version: "4.10"
---

# simGeom 几何例程插件

> 独立于场景对象的几何计算插件：直接对 mesh / OC tree / point cloud 做碰撞检测、最小距离计算与近点查询，无需创建任何场景实体。

## 概述

CoppeliaSim 的核心几何能力来自 **Coppelia Geometric Routines**——一套可独立嵌入到外部 C++ 应用的几何函数库（源码与 CoppeliaSim 分离，独立授权）。在仿真内部，这套例程由 **geometric plugin** 封装，对外暴露 `simGeom` 命名空间。

CoppeliaSim 场景层的 `sim.checkCollision` / `sim.checkDistance` / 接近传感器仿真，**底层调用的就是这套几何例程**。`simGeom` 插件则把同样的计算能力以**不依赖场景对象**的方式暴露出来：用户可在脚本中直接构造 mesh / OC tree / point cloud 数据结构，对其执行碰撞检测或最小距离计算，而无需在场景里放置 shape、点云或 OC tree 对象。

### 与场景对象检测的关系

| 维度 | 场景对象 API（`sim.checkCollision` 等） | `simGeom` 插件 |
|------|----------------------------------------|----------------|
| 数据来源 | 场景中的 collidable/measurable 实体（shape、collection、OC tree、点云对象） | 插件内部数据结构（`meshHandle` / `octreeHandle` / `ptcloudHandle`），与场景对象无直接关联 |
| 位姿输入 | 自动读取场景对象当前位姿 | 调用时显式传入 `pos` + `quat` |
| 典型用途 | 仿真步内在线检测（机器人 vs 环境） | 离线批量计算、自定义几何、不建场景的纯算法管线 |
| 可视化 | 有场景可视化、可染色高亮 | 纯数值结果，无场景可视化 |
| 缓存加速 | 内部管理 | 显式 `caching` 参数透传，跨帧复用 |

### 何时用 simGeom

- **批量/离线计算**：对大量几何组合做距离/碰撞扫描，不想为每组数据建场景对象。
- **自定义几何**：几何数据来自外部（如点云采集、STL 解析），直接喂给 `simGeom.createMesh` / `createOctreeFromPoints` 即可计算。
- **脱离场景的算法原型**：路径规划前做自相交/碰撞预筛、点云到网格的最小间隙评估等。
- **需要精确控制位姿和缓存**：想在多组位姿间复用三角形缓存以加速重复查询。

> 若只是仿真运行时检测机器人与环境的碰撞/距离，优先用 `sim.checkCollision` / `sim.checkDistance`——更简洁、有可视化。`simGeom` 适合上述脱离场景的场景。

## 内部数据结构

`simGeom` 的 mesh / OC tree / point cloud 句柄**与场景对象句柄无关**，必须用下列函数创建、操作和销毁：

| 类别 | 主要函数 |
|------|---------|
| Mesh 创建/销毁/操作 | `simGeom.createMesh`、`simGeom.destroyMesh`、`simGeom.copyMesh`、`simGeom.scaleMesh`、`simGeom.getMeshSerializationData`、`simGeom.createMeshFromSerializationData` |
| OC tree 创建/销毁/操作 | `simGeom.createOctreeFromPoints`、`simGeom.createOctreeFromColorPoints`、`simGeom.createOctreeFromMesh`、`simGeom.createOctreeFromOctree`、`simGeom.destroyOctree`、`simGeom.copyOctree`、`simGeom.scaleOctree`、`simGeom.getOctreeSerializationData`、`simGeom.createOctreeFromSerializationData`、`simGeom.getOctreeVoxels` |
| 点云创建/销毁/操作 | `simGeom.createPtcloudFromPoints`、`simGeom.createPtcloudFromColorPoints`、`simGeom.destroyPtcloud`、`simGeom.copyPtcloud`、`simGeom.scalePtcloud`、`simGeom.getPtcloudSerializationData`、`simGeom.createPtcloudFromSerializationData`、`simGeom.getPtcloudPoints` |

> 单位：除特别说明外，所有长度单位为米，角度单位为弧度。

## 碰撞检测 API

返回 `bool collisionResult`，并可返回用于跨帧加速的 `caching` 值与干涉线段 `intersections`。

| 函数 | 说明 |
|------|------|
| `simGeom.getMeshMeshCollision` | 两个 mesh 之间碰撞 |
| `simGeom.getMeshOctreeCollision` | mesh 与 OC tree 之间碰撞 |
| `simGeom.getMeshTriangleCollision` | mesh 与三角形碰撞 |
| `simGeom.getMeshSegmentCollision` | mesh 与线段碰撞 |
| `simGeom.getOctreeOctreeCollision` | 两个 OC tree 之间碰撞 |
| `simGeom.getOctreePtcloudCollision` | OC tree 与点云之间碰撞 |
| `simGeom.getOctreeTriangleCollision` | OC tree 与三角形碰撞 |
| `simGeom.getOctreeSegmentCollision` | OC tree 与线段碰撞 |
| `simGeom.getOctreePointCollision` | OC tree 与点碰撞 |

## 最小距离计算 API

返回 `float distance`（若不小于阈值 `thresholdDist` 则返回阈值本身，且近点为 `None`），同时返回两个实体上的最近点与 `caching`。利用 `thresholdDist` 可做提前剪枝。

**Mesh 最小距离**

| 函数 | 对手实体 |
|------|---------|
| `simGeom.getMeshMeshDistance` | mesh |
| `simGeom.getMeshOctreeDistance` | OC tree |
| `simGeom.getMeshPtcloudDistance` | 点云 |
| `simGeom.getMeshTriangleDistance` | 三角形 |
| `simGeom.getMeshSegmentDistance` | 线段 |
| `simGeom.getMeshPointDistance` | 点 |

**OC tree 最小距离**

| 函数 | 对手实体 |
|------|---------|
| `simGeom.getOctreeOctreeDistance` | OC tree |
| `simGeom.getOctreePtcloudDistance` | 点云 |
| `simGeom.getOctreeTriangleDistance` | 三角形 |
| `simGeom.getOctreeSegmentDistance` | 线段 |
| `simGeom.getOctreePointDistance` | 点 |

**点云最小距离**

| 函数 | 对手实体 |
|------|---------|
| `simGeom.getPtcloudPtcloudDistance` | 点云 |
| `simGeom.getPtcloudTriangleDistance` | 三角形 |
| `simGeom.getPtcloudSegmentDistance` | 线段 |
| `simGeom.getPtcloudPointDistance` | 点 |

**图元最小距离**（无需 mesh/OC tree/点云句柄，直接用几何参数）

| 函数 | 说明 |
|------|------|
| `simGeom.getBoxBoxDistance` | 两个长方体（半尺寸 + 位姿） |
| `simGeom.getBoxTriangleDistance` | 长方体 vs 三角形 |
| `simGeom.getBoxSegmentDistance` | 长方体 vs 线段 |
| `simGeom.getBoxPointDistance` | 长方体 vs 点 |
| `simGeom.getTriangleTriangleDistance` | 两个三角形 |
| `simGeom.getTriangleSegmentDistance` | 三角形 vs 线段 |
| `simGeom.getTrianglePointDistance` | 三角形 vs 点 |
| `simGeom.getSegmentSegmentDistance` | 两条线段 |
| `simGeom.getSegmentPointDistance` | 线段 vs 点 |

> `boxesAreSolid` / `boxIsSolid` 为真时，被完全包络的实体间距离记为 0。

## 其他

- `simGeom.getTransformedPoints`：按 4×4 矩阵（12 值）、`pos+quat` 或 `pos+Euler` 变换一组点。

## Caching 加速机制

碰撞/距离函数大多带 `caching` 入参和返回值。首次调用传 `None`/`-1`，函数返回新的 `caching`；下次对**同一对实体**再次查询时把上次的 `caching` 传回，插件会复用上次定位到的三角形/体素，显著加速相邻帧间的重复计算。位姿变化不大时加速效果最明显。若本次距离未小于阈值，返回的 `caching` 为 `None`，下次需重新开始。

## Python 示例（ZMQ Remote API）

`simGeom` 是插件命名空间，ZMQ Remote API 客户端可用 `client.require('simGeom')` 取到（与 `sim`、`simIK` 同样的方式）。下面构造两个单三角形 mesh，计算它们在不同位姿下的最小距离：

```python
# 适用 CoppeliaSim 4.10 + coppeliasim_zmqremoteapi_client
from coppeliasim_zmqremoteapi_client import RemoteAPIClient
import math

client = RemoteAPIClient()
simGeom = client.require('simGeom')

# 两个单三角形 mesh（顶点 + 索引）
verts1 = [0.0, 0.0, 0.0,  0.5, 0.5, 0.0,  0.5, 0.0, 0.5]
inds1  = [0, 1, 2]
verts2 = [0.5, 0.0, 0.0,  0.0, 0.5, 0.5,  0.0, 0.0, 0.5]
inds2  = [0, 1, 2]

mesh1 = simGeom.createMesh(verts1, inds1)
mesh2 = simGeom.createMesh(verts2, inds2)

identity_quat = [0.0, 0.0, 0.0, 1.0]   # (x, y, z, w)

# 在不同位姿下计算最小距离，复用 caching 加速
caching = None
for i in range(10):
    t = i * 0.05
    pos1 = [0.5 * math.cos(0.1 * t), 0.0, 0.0]
    pos2 = [0.0, 0.4 * math.cos(0.2 * t), 0.3 * math.cos(0.1 * t)]
    dist, pt1, pt2, caching = simGeom.getMeshMeshDistance(
        mesh1, pos1, identity_quat,
        mesh2, pos2, identity_quat,
        thresholdDist=2.0, caching=caching)
    print(f"step {i}: min dist = {dist:.6f}  pt1={pt1}  pt2={pt2}")

# 用完务必销毁，避免内存泄漏
simGeom.destroyMesh(mesh1)
simGeom.destroyMesh(mesh2)
```

下面的例子从一组点构造 OC tree，查询某点到该 OC tree 是否碰撞以及最小距离：

```python
simGeom = client.require('simGeom')

points = [0.0, 0.0, 0.0,
          0.1, 0.0, 0.0,
          0.0, 0.1, 0.0,
          0.0, 0.0, 0.1,
          0.05, 0.05, 0.05]
octree = simGeom.createOctreeFromPoints(points, cellS=0.02)

pos_oc = [0.0, 0.0, 0.0]
quat_oc = [0.0, 0.0, 0.0, 1.0]
query_pt = [0.03, 0.03, 0.03]

collided, _ = simGeom.getOctreePointCollision(octree, pos_oc, quat_oc, query_pt)
dist, nearest, _ = simGeom.getOctreePointDistance(octree, pos_oc, quat_oc, query_pt)
print(f"collision={collided}  min_dist={dist:.6f}  nearest={nearest}")

simGeom.destroyOctree(octree)
```

## 嵌入到外部 C++ 应用

`coppeliaGeometricRoutines.htm` 指出，Coppelia Geometric Routines 可作为 C++ 库独立嵌入到不含仿真器的应用中（源码独立授权）。手册给出的最小示例即用 `geom_createMesh` + `geom_getMeshMeshDistanceIfSmaller` 在 `while` 循环里逐帧计算两 mesh 最小距离。该 C++ API 与本文 `simGeom.*` 脚本 API 一一对应（手册注明 `simGeom` 是 C++ API 的子集）。需要脱离 CoppeliaSim 做离线几何计算时，可考虑直接使用该 C++ 库。

## 参见

- [[wiki/02-仿真建模/02-12-碰撞检测与距离计算]] — 场景层 `sim.checkCollision` / `sim.checkDistance` 用法（待建）
- [[wiki/02-仿真建模/02-07-接近传感器]] — 接近传感器同样底层依赖几何例程
- [[wiki/01-API参考/01-04-插件API总览]] — 各插件命名空间总览（待建）
- [[wiki/01-API参考/01-05-几何与运动学例程]] — simGeom / simIK / simConvex / simOpenMesh / simIGL 等例程插件对比（待建）
- [[wiki/01-API参考/api/api-simCheckProximitySensor]] — 场景层接近传感器检测
