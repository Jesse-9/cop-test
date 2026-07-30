---
title: sim.getJointPosition
type: api
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/初级
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simGetJointPosition.htm
related:
  - "[[wiki/01-API参考/api/api-simSetJointPosition]]"
  - "[[wiki/01-API参考/api/api-simGetJointTargetPosition]]"
  - "[[wiki/01-API参考/api/api-simGetJointMode]]"
  - "[[wiki/02-仿真建模/02-03-关节与关节模式]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
sim_version: "4.10"
---

# sim.getJointPosition

> 读取关节的线性/角度位置（非球关节）。回转关节返回弧度，移动关节返回米。

## 签名

```python
float position = sim.getJointPosition(int objectHandle)
```

## 参数

- `objectHandle`：关节对象句柄。

## 返回值

- `position`：关节位置。**回转关节（revolute）单位为弧度，移动关节（prismatic）单位为米**。球关节（spherical）不支持，应改用 `sim.getObjectChildPose`。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

joint = sim.getObject('/robot/joint1')
pos = sim.getJointPosition(joint)
print(f'关节位置: {pos:.4f} rad')   # 回转关节
```

## 参见

- [[wiki/01-API参考/api/api-simSetJointPosition]] — 直接设置关节位置（kinematic 模式瞬时设位）
- [[wiki/01-API参考/api/api-simGetJointTargetPosition]] — 读取目标位置
- [[wiki/01-API参考/api/api-simGetJointMode]] — 读取关节模式
- [[wiki/02-仿真建模/02-03-关节与关节模式]] — 关节与关节模式概念
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
