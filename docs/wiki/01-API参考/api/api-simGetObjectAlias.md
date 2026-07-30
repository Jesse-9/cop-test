---
title: sim.getObjectAlias
type: api
tags: [coppeliasim, 版本/v4.10, 类别/API, 难度/初级]
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simGetObjectAlias.htm
related:
  - "[[wiki/01-API参考/api/api-simGetObject]]"
  - "[[wiki/01-API参考/api/api-simGetObjects]]"
  - "[[wiki/01-API参考/api/api-simGetObjectsInTree]]"
  - "[[wiki/01-API参考/api/api-simGetObjectChild]]"
  - "[[wiki/01-API参考/api/api-simGetObjectParent]]"
  - "[[wiki/01-API参考/api/api-simGetObjectUid]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
  - "[[wiki/02-仿真建模/02-01-分层命名场景构建]]"
  - "[[wiki/01-API参考/api/api-simGetObjectPosition]]"
sim_version: "4.10"
---

# sim.getObjectAlias

> 按句柄反查对象的别名或路径。通过 `options` 选择不同的路径表达形式，是 `sim.getObject` 的逆操作。

## 签名

```python
string alias = sim.getObjectAlias(int objectHandle, int options = -1)
```

## 参数

- `objectHandle`：对象句柄。
- `options`：别名格式化选项：

| 值 | 含义 | 示例 |
|----|------|------|
| -1 | 裸别名 | `alias` |
| 0 | 带同层序号的别名 | `alias[0]` |
| 1 | 唯一标识对象的短路径 | `/parent/alias[0]` |
| 2 | 完整路径 | `/parentA/parentB/alias[0]` |
| 3 | 裸别名，仅在别名非唯一时附句柄 | `alias` 或 `alias__42__` |
| 4 | 裸别名始终附句柄 | `alias__42__` |
| 5 | 短路径的打印版（不保证唯一） | `/parent/ ... /alias` |
| 6 | 用别名 + 全局索引的路径 | `/alias{3}` |
| 7 | 同 6，含第一个父模型 | `/parentModel{2}/alias{3}` |
| 8 | 同 6，含前两个父模型 | `/parentModelA{4}/parentModelB{2}/alias{3}` |
| 9 | 同 6，含全部父模型 | `/parentModelA{4}/.../parentModelN{2}/alias{3}` |

## 返回值

- `alias`：对象的别名或路径字符串。

## 与 setObjectAlias 的区别

- `sim.setObjectAlias` 是**设置**对象的别名（写入），别名本身不应含斜杠 `/`。
- `sim.getObjectAlias` 是**读取**对象当前的别名/路径，可选择多种表达形式。
- 本仓库遇到 4.10 的别名路径陷阱：`setObjectAlias` 把含 `/` 的字符串转成下划线别名（如 `/Mobile/AGV_Shared` → `_Mobile_AGV_Shared`），导致 `getObject(斜杠路径)` 查询失败。详见 [[wiki/02-仿真建模/02-01-分层命名场景构建]]。

## 示例

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')

h = sim.getObject('/Robot/base')

print('naked      :', sim.getObjectAlias(h, -1))
print('ordered    :', sim.getObjectAlias(h, 0))
print('short path :', sim.getObjectAlias(h, 1))
print('full path  :', sim.getObjectAlias(h, 2))
print('uid path   :', sim.getObjectAlias(h, 6))

# 用 options=2 输出完整路径，可反查是否被 setObjectAlias 污染
print('debug path :', sim.getObjectAlias(h, 5))
```

## 参见

- [[wiki/01-API参考/api/api-simGetObject]] — 按路径正向查句柄
- [[wiki/02-仿真建模/02-01-分层命名场景构建]] — 别名陷阱与分层命名约定
- `sim.setObjectAlias` — 设置别名
