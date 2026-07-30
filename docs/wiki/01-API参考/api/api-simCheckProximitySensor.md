---
title: sim.checkProximitySensor
type: api
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/中级
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simCheckProximitySensor.htm
related:
  - "[[wiki/02-仿真建模/02-07-接近传感器]]"
  - "[[wiki/02-仿真建模/02-12-碰撞检测与距离计算]]"
  - "[[wiki/02-仿真建模/02-10-集合-Collections]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
sim_version: "4.10"
---

# sim.checkProximitySensor

> 静默检测：检查接近传感器能否探测到指定实体。与 `sim.handleProximitySensor` 不同，本函数不执行完整的感知流程也不产生视觉反馈（无检测颜色/线段），仅在调用时刻做一次几何查询；实体的 detectable 标志在检测时被强制覆盖。

## 签名

```python
int res, float dist, list point, int obj, list n = sim.checkProximitySensor(
    int sensorHandle, int entityHandle)
```

## 参数

- `sensorHandle`：接近传感器对象句柄。
- `entityHandle`：被探测实体句柄（对象或集合），或 `sim.handle_all` 以探测所有可探测对象。

## 返回值

- `res`：`0`（未检测到）或 `1`（检测到）。
- `dist`：传感器原点到检测点的距离，单位**米**；`res != 1` 时为 `None`。
- `point`：检测点相对传感器原点的坐标 `[x, y, z]`，单位米；`res != 1` 时为 `None`。
- `obj`：被检测到的对象句柄；`res != 1` 时为 `None`。
- `n`：检测点处表面法向量（已归一化），相对传感器参考系；`res != 1` 时为 `None`。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

sensor = sim.getObject('/ProximitySensor')
target = sim.getObject('/obstacle')

res, dist, point, obj, n = sim.checkProximitySensor(sensor, target)
if res == 1:
    print(f'检测到对象 {obj}：距离 {dist:.3f} m，'
          f'检测点 {point}，法向量 {n}')
else:
    print('未检测到')
```

用 `sim.handle_all` 一次探测场景中所有可探测对象：

```python
res, dist, point, obj, n = sim.checkProximitySensor(sensor, sim.handle_all)
```

## 附录：同族函数

| 函数 | 说明 |
|------|------|
| `sim.handleProximitySensor(sensorHandle)` | 执行完整感知（含检测颜色/线段等视觉反馈），返回值与本函数一致；支持 `sim.handle_all` / `sim.handle_all_except_explicit`。 |
| `sim.readProximitySensor(sensorHandle)` | 读取上一次 `sim.handleProximitySensor` 的结果，不重新检测。 |
| `sim.resetProximitySensor(sensorHandle)` | 清除检测状态、检测颜色、检测线段等；支持 `sim.handle_all` / `sim.handle_all_except_explicit`。 |
| `sim.checkProximitySensorEx(...)` | 扩展版，支持自定义检测模式（前/后面、快速、限角）、距离阈值与最大角度。 |

典型用法：仿真循环中每帧先 `sim.handleProximitySensor` 驱动感知，后续用 `sim.readProximitySensor` 读取；需要针对特定实体做一次性静默查询时用 `sim.checkProximitySensor`。

## 参见

- [[wiki/02-仿真建模/02-07-接近传感器]] — 接近传感器建模概念
- [[wiki/02-仿真建模/02-12-碰撞检测与距离计算]] — 碰撞/距离检测概览（待建）
- [[wiki/02-仿真建模/02-10-集合-Collections]] — 集合作为 `entityHandle` 的用法
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
