# Wiki 重写规范（2026-07-24 重构）

本次重构以官方手册 `docs/raw/manual/en/` 为唯一权威来源。所有 wiki 条目重写时遵循本规范。

## 权威来源
- 概念/教程：`docs/raw/manual/en/*.htm`（200+ 篇）
- Regular API 函数页：`docs/raw/manual/en/regularApi/{funcName}.htm`（939 篇）
- API 索引（JSON）：`docs/raw/manual/index/sim.json`（主）、各插件 `simXxx.json`
- API 分类清单：`docs/raw/manual/en/apiFunctions.htm`（按类列出所有函数名，Python/Lua/C 三栏）
- 常量：`docs/raw/manual/en/apiConstants.htm`

## 读取官方 htm 的方法
htm 含大量标签，用以下命令提取纯文本：
```bash
cd "e:\Work\paper\cop-test" && sed -e 's/<[^>]*>//g' "docs/raw/manual/en/某文件.htm" | grep -v '^[[:space:]]*$'
```
函数页同理：`docs/raw/manual/en/regularApi/simGetObjectPose.htm`。

## Frontmatter 规范（必填，严格遵守）
```yaml
---
title: 中文标题
type: concept            # concept | api | tutorial | case | reference | glossary
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/XXX              # 见 docs/CLAUDE.md 标签体系：基础知识|API|运动规划|运动学|控制|感知|协同仿真|数字孪生|建模|案例
  - 难度/初级             # 初级|中级|高级
created: 2024-07-13       # 保留原条目的 created；新增条目用 2026-07-24
updated: 2026-07-24
status: published         # 重写后统一 published；不确定的局部标 draft
source:                   # 必填：真实 .htm 文件名（去掉 docs/raw/manual/en/ 前缀），多个用列表
  - welcome.htm
  - coppeliaSimFeatures.htm
related:                  # 相关条目 wikilink
  - "[[wiki/00-基础知识/00-01-CoppeliaSim-overview]]"
sim_version: "4.10"
---
```

⚠ 关键纠正：
- `source` 字段**必须**是 `docs/raw/manual/en/` 下真实存在的 .htm 文件名。**禁止**再用 `src-doc-CoppeliaSim-*.html` 这种旧式二手名（仓库 raw 里已不存在）。
- api 条目 source 用 `regularApi/simXxx.htm` 形式。

## 内容质量准则
1. **以手册为准**：技术描述、API 签名、参数、返回值、示例代码均以官方手册原文为依据。不得沿用现有条目中与手册冲突的旧描述。
2. **中文精炼**：用中文重述手册要点，不逐句翻译、不堆砌原文。表格/列表保留，叙述精炼。
3. **代码可运行**：所有 Python/Lua 代码须是手册中给出的或可直接跑的完整片段，标注适用版本 4.10。优先 Python（ZMQ Remote API 客户端视角，契合本仓库用法）。
4. **纠正旧错误**：现有条目中猜测性的版本历史、错误的 API 描述、"待验证"占位内容，一律按手册重写或删除。
5. **wikilink 交叉引用**：相关概念用 `[[wiki/路径/文件名]]` 链接（注意 docs/CLAUDE.md 规范是 `[[wiki/...]]` 前缀）。指向将要新建的条目也可先链接（标记为待建无妨）。
6. **单一职责**：一条笔记一个主题。API 独立条目只讲该函数（+ get/set 合并 + 同类小函数附录）。

## API 独立条目模板（api-{name}.md）
```markdown
---
title: sim.getObjectPosition
type: api
tags: [coppeliasim, 版本/v4.10, 类别/API, 难度/初级]
created: 2026-07-24
updated: 2026-07-24
status: published
source: regularApi/simGetObjectPosition.htm
related:
  - "[[wiki/01-API参考/api/api-simSetObjectPosition]]"
  - "[[wiki/01-API参考/api/api-simGetObjectPose]]"
sim_version: "4.10"
---

# sim.getObjectPosition

> 获取对象相对于指定参考系的位置（3 元素 [x y z]，单位米）。

## 签名
\`\`\`python
list position = sim.getObjectPosition(int objectHandle, int relativeToObjectHandle = sim.handle_world)
\`\`\`

## 参数
- `objectHandle`：对象句柄。
- `relativeToObjectHandle`：参考系。`sim.handle_world`=绝对，`sim.handle_parent`=相对父对象，或任意对象句柄。

## 返回值
- `position`：`[x, y, z]`，单位米。

## 示例
\`\`\`python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient
client = RemoteAPIClient()
sim = client.require('sim')
h = sim.getObject('/robot/base')
pos = sim.getObjectPosition(h, sim.handle_world)
print(pos)  # [0.5, 0.0, 0.2]
\`\`\`

## 参见
- [[wiki/01-API参考/api/api-simSetObjectPosition]] — 设置位置
- [[wiki/01-API参考/api/api-simGetObjectPose]] — 获取位姿（位置+四元数）
```

## 命名约定
- 概念条目：`{编号}-{中文名}.md`（如 `02-04-虚拟点-Dummy.md`）
- API 条目：`api-{functionName}.md`，functionName 与 `regularApi/` 目录下文件名完全一致（含 sim 前缀，首字母小写，如 `api-simGetObjectPosition.md`）。
- API 条目统一放 `docs/wiki/01-API参考/api/` 子目录。

## 关键技术纠正清单（现有 wiki 常见错误，重写时务必修正）
1. **别名/路径机制**：依据 `accessingSceneObjects.htm`——`sim.getObject(path)` 用斜杠路径查询；路径可绝对（`/Robot`）或相对（`./`、`../`、`:`、`::`）；支持通配符 `*`、同层索引 `[n]`、全局索引 `{n}`；`setObjectAlias` 设置别名时别名本身**不应含斜杠**（手册建议别名不含 `/`，路径是查询时构造的）。现有 [[wiki/02-仿真建模/02-01-分层命名场景构建]] 的描述需与此一致。
2. **Regular API 语言**：Regular API 支持 **Python、Lua、C/C++** 三种（见 `apiFunctions.htm`，每函数三栏）。不要再说"只有 Lua"。
3. **脚本体系**：4.10 用 **script objects**（simulation script / customization script）+ main script + add-on + sandbox，不再是旧版 child script/main script 二分。见 `scriptObjects.htm`/`scripts.htm`。
4. **版本历史**：不要编造各版本特性。4.10 新特性以 `compatibility.htm`/`versionInfo.htm` 实际内容为准。
5. **Regular API 单位**：所有 API 单位为米、千克、秒、弧度（`apiFunctions.htm` 开头声明）。
