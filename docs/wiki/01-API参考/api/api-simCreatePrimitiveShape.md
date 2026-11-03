---
title: sim.createPrimitiveShape
type: api
tags: [coppeliasim, 版本/v4.10, 类别/API, 类别/建模, 难度/中级]
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simCreatePrimitiveShape.htm
related:
  - "[[wiki/02-仿真建模/02-02-形状与几何体]]"
  - "[[wiki/02-仿真建模/02-01-分层命名场景构建]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
  - "[[wiki/01-API参考/api/api-simGetObject]]"
  - "[[wiki/01-API参考/api/api-simSetObjectPosition]]"
sim_version: "4.10"
---

# sim.createPrimitiveShape

> 创建一个基本几何体形状（Primitive Shape）。基本体是场景搭建的地板、工作台、物料块等的基础原语，CoppeliaSim 据其类型自动生成可碰撞/可响应的纯形状。本仓库 v1 场景构建用 cuboid 构造地板与工作台。

## 签名

```python
int shapeHandle = sim.createPrimitiveShape(int primitiveType, list sizes, int options = 0)
```

## 参数

- `primitiveType`：基本体类型（见 `apiConstants.htm`）：

  | 常量 | 形状 |
  |------|------|
  | `sim.primitiveshape_none` | 无（占位） |
  | `sim.primitiveshape_plane` | 平面 |
  | `sim.primitiveshape_disc` | 圆盘 |
  | `sim.primitiveshape_cuboid` | 长方体 |
  | `sim.primitiveshape_spheroid` | 球体 |
  | `sim.primitiveshape_cylinder` | 圆柱体 |
  | `sim.primitiveshape_cone` | 圆锥体 |
  | `sim.primitiveshape_capsule` | 胶囊体 |
  | `sim.primitiveshape_heightfield` | 高度图（通常用 `createHeightfieldShape` 代替） |

- `sizes`：3 个值 `[x, y, z]`，形状尺寸（米）。对不同类型含义不同：cuboid 是三边长，cylinder/spheroid 用 `[直径, 直径, 长度]`，plane/disc 用 `[x, y, _]`。
- `options`：位编码：
  - bit0 (1)：背面剔除
  - bit1 (2)：边缘显示为锐边
  - bit2 (4)：圆柱两端开放（不封口）
  - bit3 (8)：不注册为基本体，而是当作普通（可能凸）形状

## 返回值

- `shapeHandle`：新建形状的对象句柄。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

# 1. 创建地板（cuboid，锐边）
floor = sim.createPrimitiveShape(sim.primitiveshape_cuboid,
                                 [4.0, 3.0, 0.05], 2)
sim.setObjectAlias(floor, 'Workshop_Floor')
sim.setObjectPosition(floor, [0, 0, -0.025], sim.handle_world)

# 2. 创建圆柱工作台（bit2=4 开放两端不适用，此处封口）
table = sim.createPrimitiveShape(sim.primitiveshape_cylinder,
                                 [0.4, 0.4, 0.8], 2)
sim.setObjectAlias(table, 'Island_B_Table')
sim.setObjectPosition(table, [1.2, -0.6, 0.4], sim.handle_world)

# 3. 创建球状物料标记
ball = sim.createPrimitiveShape(sim.primitiveshape_spheroid,
                                [0.06, 0.06, 0.06], 0)
sim.setObjectAlias(ball, 'Material_Marker')
sim.setObjectPosition(ball, [1.2, -0.6, 0.85], sim.handle_world)

print(sim.getObject('/Workshop_Floor') == floor)  # True
```

## 附录：其他形状创建函数

| 函数 | 用途 | 签名要点 |
|------|------|----------|
| `sim.createHeightfieldShape` | 高度图形状（地形） | `(options, shadingAngle, xPointCount, yPointCount, xSize, heights)` |
| `sim.createMeshShape` | 任意网格形状（**已弃用**，改用 `sim.createShape`） | `(options, shadingAngle, vertices, indices)` |
| `sim.createShape` | 新版网格/凸包形状创建（推荐替代 `createMeshShape`） | 见 `regularApi/simCreateShape.htm` |
| `sim.createForceSensor` | 力传感器（与形状同属创建原语，5 个 int/float 参数） | `(options, intParams[5], floatParams[5])` |
| `sim.createPath` | 路径对象（控制点 + 平滑度 + 朝向模式） | `(ctrlPts, options, subdiv, smoothness, orientationMode, upVector)` |

> `sim.createMeshShape` 在手册中标注 **Deprecated. Use sim.createShape instead**，新代码应使用 `sim.createShape`。

## 参见

- [[wiki/02-仿真建模/02-02-形状与几何体]] — 形状类型与碰撞属性
- [[wiki/02-仿真建模/02-01-分层命名场景构建]] — 本仓库分层命名场景构建模式
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
- [[wiki/01-API参考/api/api-simGetObject]] — 按路径反查句柄
- [[wiki/01-API参考/api/api-simSetObjectPosition]] — 设置对象位置
- 官方手册 `regularApi/simCreatePrimitiveShape.htm`、`regularApi/simCreateHeightfieldShape.htm`、`regularApi/simCreateMeshShape.htm`、`regularApi/simCreateForceSensor.htm`、`regularApi/simCreatePath.htm`、`apiConstants.htm`
