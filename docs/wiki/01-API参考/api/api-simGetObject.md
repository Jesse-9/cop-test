---
title: sim.getObject
type: api
tags: [coppeliasim, 版本/v4.10, 类别/API, 难度/初级]
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simGetObject.htm
related:
  - "[[wiki/01-API参考/api/api-simGetObjects]]"
  - "[[wiki/01-API参考/api/api-simGetObjectsInTree]]"
  - "[[wiki/01-API参考/api/api-simGetObjectChild]]"
  - "[[wiki/01-API参考/api/api-simGetObjectParent]]"
  - "[[wiki/01-API参考/api/api-simGetObjectAlias]]"
  - "[[wiki/01-API参考/api/api-simGetObjectUid]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
  - "[[wiki/02-仿真建模/02-01-分层命名场景构建]]"
  - "[[wiki/01-API参考/api/api-simGetObjectPosition]]"
sim_version: "4.10"
---

# sim.getObject

> 按路径/别名查询对象句柄。是访问场景对象最核心的入口，支持绝对路径、相对路径、通配符与索引。

## 签名

```python
int objectHandle = sim.getObject(string objectPath, dict options = {})
```

## 参数

- `objectPath`：对象路径/别名。语义见下方「路径语法」。
- `options`：可选字典，控制查询方式：
  - `noError` (bool)：对象未找到时静默返回 -1，不产生错误。
  - `index` (int)：取满足该路径的第 n 个对象（从 0 起）。
  - `proxy` (int)：限定在指定 proxy 对象的子树内搜索。

## 返回值

- `objectHandle`：对象句柄；未找到返回 -1。

## 路径语法

依据 `accessingSceneObjects.htm`，路径分绝对与相对两种，可叠加通配符与索引：

### 绝对路径（非关联代码：插件、add-on、Remote API 客户端、main script）

| 写法 | 含义 |
|------|------|
| `/Path/to/Object` | 完整路径 |
| `/Object` | 别名唯一时直接用别名 |
| `/Robot[0]` | 同层第 1 个名为 Robot 的对象 |
| `/Robot[1]` | 同层第 2 个名为 Robot 的对象 |
| `/Robot{4}` | 场景内第 5 个名为 Robot 的对象（全局索引） |
| `/Mobile*` | 通配：所有以 Mobile 开头的对象 |

### 相对路径（仅脚本对象内：simulation/customization script）

| 写法 | 含义 |
|------|------|
| `.` | 当前脚本自身句柄 |
| `..` | 当前脚本的父对象 |
| `:` | 包含当前脚本的模型 |
| `::` | 包含当前脚本的父模型 |
| `./Object*` | 当前层级下以 Object 开头的第一个对象 |
| `:/Object` | 当前模型层级内名为 Object 的对象 |
| `:/Leg[3]` | 当前模型同层第 4 个 Leg |
| `:/Leg{4}` | 当前模型内第 5 个 Leg |
| `./Object` + `{'proxy': anotherObject}` | 在 anotherObject 子树内查 Object |

> Remote API 客户端（本仓库 Python 端）属非关联代码，只能用绝对路径。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

# 1. 绝对路径
h = sim.getObject('/Robot/base')
print(h)

# 2. 别名唯一时简写
h = sim.getObject('/AGV_Shared')

# 3. 同层索引 + 全局索引
first_robot = sim.getObject('/Robot[0]')
fifth_robot = sim.getObject('/Robot{4}')

# 4. 通配符枚举（配合 noError 静默结束）
i = 0
while True:
    h = sim.getObject('/Mobile*', {'index': i, 'noError': True})
    if h < 0:
        break
    print('found', h, sim.getObjectAlias(h, 0))
    i += 1

# 5. proxy 限定子树搜索
base = sim.getObject('/Robot')
child = sim.getObject('./joint1', {'proxy': base})
```

## 参见

- [[wiki/01-API参考/api/api-simGetObjectAlias]] — 反向查询对象别名/路径
- [[wiki/01-API参考/api/api-simGetObjects]] — 按类型枚举场景对象
- [[wiki/01-API参考/api/api-simGetObjectsInTree]] — 枚举子树对象
- [[wiki/02-仿真建模/02-01-分层命名场景构建]] — 分层命名与别名陷阱
- 官方手册 `accessingSceneObjects.htm`
