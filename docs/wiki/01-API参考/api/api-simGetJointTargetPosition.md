---
title: sim.getJointTargetPosition
type: api
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 难度/初级
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simGetJointTargetPosition.htm
related:
  - "[[wiki/01-API参考/api/api-simSetJointTargetPosition]]"
  - "[[wiki/01-API参考/api/api-simGetJointPosition]]"
  - "[[wiki/01-API参考/api/api-simGetJointMode]]"
  - "[[wiki/02-仿真建模/02-03-关节与关节模式]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
sim_version: "4.10"
---

# sim.getJointTargetPosition

> 读取关节的目标位置（target position）。回转关节返回弧度，移动关节返回米。

## 签名

```python
float targetPosition = sim.getJointTargetPosition(int objectHandle)
```

## 参数

- `objectHandle`：关节对象句柄。

## 返回值

- `targetPosition`：关节目标位置。**回转关节单位为弧度，移动关节单位为米**。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

joint = sim.getObject('/robot/joint1')
# 设置目标位置后读取
sim.setJointTargetPosition(joint, 1.0)
tgt = sim.getJointTargetPosition(joint)
print(f'目标位置: {tgt:.4f} rad')  # 1.0000 rad
```

## 参见

- [[wiki/01-API参考/api/api-simSetJointTargetPosition]] — 设置目标位置（含 profile 行为说明）
- [[wiki/01-API参考/api/api-simGetJointPosition]] — 读取当前实际位置
- [[wiki/01-API参考/api/api-simGetJointMode]] — 读取关节模式
- [[wiki/02-仿真建模/02-03-关节与关节模式]] — 关节与关节模式概念
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
