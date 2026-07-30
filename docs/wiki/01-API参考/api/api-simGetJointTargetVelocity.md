---
title: sim.getJointTargetVelocity
type: api
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/初级
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simGetJointTargetVelocity.htm
related:
  - "[[wiki/01-API参考/api/api-simSetJointTargetVelocity]]"
  - "[[wiki/01-API参考/api/api-simGetJointTargetPosition]]"
  - "[[wiki/01-API参考/api/api-simGetJointMode]]"
  - "[[wiki/02-仿真建模/02-03-关节与关节模式]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
sim_version: "4.10"
---

# sim.getJointTargetVelocity

> 读取非球关节的目标速度（target velocity）。回转关节返回弧度/秒，移动关节返回米/秒。

## 签名

```python
float targetVelocity = sim.getJointTargetVelocity(int objectHandle)
```

## 参数

- `objectHandle`：关节对象句柄。非球关节。

## 返回值

- `targetVelocity`：关节目标速度。**回转关节单位为弧度/秒，移动关节单位为米/秒**。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

joint = sim.getObject('/robot/joint1')
# 设置目标速度后读取
sim.setJointTargetVelocity(joint, 0.5)
vel = sim.getJointTargetVelocity(joint)
print(f'目标速度: {vel:.4f} rad/s')  # 0.5000 rad/s
```

## 参见

- [[wiki/01-API参考/api/api-simSetJointTargetVelocity]] — 设置目标速度（含 mode 行为说明）
- [[wiki/01-API参考/api/api-simGetJointTargetPosition]] — 读取目标位置
- [[wiki/01-API参考/api/api-simGetJointMode]] — 读取关节模式
- [[wiki/02-仿真建模/02-03-关节与关节模式]] — 关节与关节模式概念
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
