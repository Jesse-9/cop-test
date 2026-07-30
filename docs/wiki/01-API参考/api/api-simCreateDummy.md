---
title: sim.createDummy
type: api
tags: [coppeliasim, 版本/v4.10, 类别/API, 难度/中级]
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simCreateDummy.htm
related:
  - "[[wiki/02-仿真建模/02-04-虚拟点-Dummy]]"
  - "[[wiki/02-仿真建模/02-01-分层命名场景构建]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
  - "[[wiki/01-API参考/api/api-simGetObject]]"
  - "[[wiki/01-API参考/api/api-simSetObjectPosition]]"
sim_version: "4.10"
---

# sim.createDummy

> 创建一个虚拟点（Dummy）。Dummy 是无几何、无质量的标记对象，常用作路径端点、IK 目标、坐标系锚点或层级占位符。本仓库 `build_scene.py` 用它标记仓库/工位/AGV 接驳点。

## 签名

```python
int dummyHandle = sim.createDummy(float size)
```

## 参数

- `size`：Dummy 的显示尺寸（米）。仅影响场景中的可视化大小，不参与物理。

## 返回值

- `dummyHandle`：新建 Dummy 的对象句柄。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

# 1. 创建 Dummy 并设别名（别名不含斜杠，避免 4.10 别名路径陷阱）
pickup = sim.createDummy(0.05)
sim.setObjectAlias(pickup, 'Island_A_Pickup')

# 2. 定位到工作岛 A 的接驳点（绝对坐标，单位米）
sim.setObjectPosition(pickup, [0.8, 0.6, 0.05], sim.handle_world)

# 3. 通过路径查询验证
h = sim.getObject('/Island_A_Pickup')
print(h == pickup)  # True
print(sim.getObjectPosition(h, sim.handle_world))  # [0.8, 0.6, 0.05]
```

## 参见

- [[wiki/02-仿真建模/02-04-虚拟点-Dummy]] — Dummy 概念与典型用途
- [[wiki/02-仿真建模/02-01-分层命名场景构建]] — 本仓库分层命名模式（/Warehouse /Island /Conveyor /Mobile）
- [[wiki/01-API参考/api/api-simGetObject]] — 按路径/别名反查句柄
- [[wiki/01-API参考/api/api-simSetObjectPosition]] — 设置对象位置
- 官方手册 `regularApi/simCreateDummy.htm`
