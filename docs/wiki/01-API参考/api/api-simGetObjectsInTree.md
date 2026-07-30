---
title: sim.getObjectsInTree
type: api
tags: [coppeliasim, 版本/v4.10, 类别/API, 难度/初级]
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simGetObjectsInTree.htm
related:
  - "[[wiki/01-API参考/api/api-simGetObject]]"
  - "[[wiki/01-API参考/api/api-simGetObjects]]"
  - "[[wiki/01-API参考/api/api-simGetObjectChild]]"
  - "[[wiki/01-API参考/api/api-simGetObjectParent]]"
  - "[[wiki/01-API参考/api/api-simGetObjectAlias]]"
  - "[[wiki/01-API参考/api/api-simGetObjectUid]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
  - "[[wiki/01-API参考/api/api-simGetObjectPosition]]"
sim_version: "4.10"
---

# sim.getObjectsInTree

> 获取指定层级子树内的对象句柄列表。可按类型过滤、按 options 位控输出范围。

## 签名

```python
list objects = sim.getObjectsInTree(int treeBaseHandle, int objectType = sim.handle_all, int options = 0)
```

## 参数

- `treeBaseHandle`：子树根对象句柄；或 `sim.handle_scene` 表示场景中全部对象。
- `objectType`：要获取的对象类型；`sim.handle_all` 表示任意类型。
- `options`：位编码选项：
  - bit0 (1)：从返回数组中排除子树根本身。
  - bit1 (2)：只返回第一层子对象。若 `treeBaseHandle` 为 `sim.handle_scene`，则只返回无父对象的对象。

## 返回值

- `objects`：对象句柄数组。

## 注意事项

仿真未启动或子树中含不可查询对象时，本函数可能抛 `"Operation cannot be accomplished in current state"`。本仓库 `RobotArmAnimator._collect_arm_joints()`（`src/factory_robot.py`）即用 try/except 捕获此异常，回退到全场景关节扫描按别名匹配机械臂关节。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

# 1. 获取机械臂子树全部对象
base = sim.getObject('/Robot')
tree = sim.getObjectsInTree(base, sim.handle_all, 0)
print('tree size:', len(tree))

# 2. 只取关节，排除根本身
joints = sim.getObjectsInTree(base, sim.sceneobject_joint, 1)
print('joints:', len(joints))

# 3. 容错：仿真未启动时回退全场景扫描
try:
    tree = sim.getObjectsInTree(base, sim.handle_all, 0)
except Exception as e:
    print('getObjectsInTree failed:', e)
    tree = []
    i = 0
    while True:
        h = sim.getObjects(i, sim.sceneobject_joint)
        if h < 0:
            break
        tree.append(h)
        i += 1

# 4. 枚举场景中所有无父对象（顶层对象）
top_level = sim.getObjectsInTree(sim.handle_scene, sim.handle_all, 2)
```

## 参见

- [[wiki/01-API参考/api/api-simGetObjects]] — 按类型枚举场景全部对象
- [[wiki/01-API参考/api/api-simGetObjectChild]] — 按索引取单个子对象
- 本仓库 `src/factory_robot.py` `_collect_arm_joints` 的回退用法
