# 知识库 Schema

## 这是什么

一个以 **CoppeliaSim 4.10** 为底座的 LLM 知识库，专注于**工业场景仿真**的学习与实现。涵盖机器人仿真建模、运动规划、力控制、视觉感知、数字孪生、V-REP API、Remote API、插件开发、协同仿真等主题。

## 知识库结构

```
docs/                          # 本知识库根目录 (即 cop-test/docs)
├── CLAUDE.md                  # 本文件 — 知识库 Schema 和维护指南
├── README.md                  # 文档入口导航
├── MCP_USAGE.md               # MCP 服务器使用文档
├── raw/                       # 未处理的源材料（官方文档HTML/MD、论文、网页存档等）
│   ├── README.md              # 原始资料清单与来源验证
│   └── ...                    # ⚠️ 永远不要修改这些文件
├── wiki/                      # 整理后的维基，完全由 AI 维护
│   ├── index.md               # 维基导航索引
│   ├── 00-基础知识/            # CoppeliaSim 基础概念、安装、界面
│   ├── 01-API参考/             # V-REP API、Remote API、Plugin API 详解
│   ├── 02-仿真建模/            # 场景搭建、模型导入、关节/传感器配置
│   ├── 03-运动规划与控制/       # OMPL、逆运动学、轨迹规划、力/位控制
│   ├── 04-感知与视觉/          # 视觉传感器、点云、图像处理
│   ├── 05-数字孪生与协同仿真/   # 与Simulink/Python/ROS等协同
│   ├── 06-工业案例/            # 典型工业场景仿真案例
│   ├── 07-API脚本示例/          # Lua/Python 脚本代码片段与注释
│   └── 99-附录/               # 术语表、快捷键、资源链接
└── outputs/                   # 生成的报告、问答记录、分析结果
    ├── reports/               # 研究报告、调研报告
    ├── qa/                    # LLM 问答记录/QA pairs
    └── analysis/              # 技术分析、对比评测
```

## 文件命名规范

| 类型 | 格式 | 示例 |
|------|------|------|
| 知识条目 | `{编号}-{英文短名}.md` | `00-01-CoppeliaSim-overview.md` |
| API条目 | `api-{功能名}.md` | `api-simSetJointPosition.md` |
| 案例 | `case-{案例描述}.md` | `case-6DOF-pick-and-place.md` |
| 报告 | `report-{日期}-{主题}.md` | `report-20240713-coppeliasim410-research.md` |
| QA记录 | `qa-{日期}-{序号}.md` | `qa-20240713-001.md` |
| 原始材料 | `src-{类型}-{来源描述}.{ext}` | `src-doc-CoppeliaSim-user-manual.pdf` |

## 元数据规范 (YAML Frontmatter)

每条 wiki 笔记必须包含标准化的 frontmatter：

```yaml
---
title: 笔记标题
type:            # concept | api | tutorial | case | reference | glossary
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/运动规划       # 按功能领域分类
  - 难度/初级           # 初级 | 中级 | 高级
created: 2024-07-13
updated: 2024-07-13
status: draft           # draft | review | published | archived
source:                 # 信息来源（raw 中的文件名或 URL）
related:                # 相关笔记的 [[wikilink]]
  - [[wiki/00-基础知识/00-01-CoppeliaSim-overview]]
difficulty: beginner    # beginner | intermediate | advanced
sim_version: "4.10"     # 适用仿真版本
---
```

## 标签分类体系 (Tag Taxonomy)

所有笔记使用层级标签进行分类：

### 按版本
- `版本/v4.10` — CoppeliaSim 4.10 专属内容
- `版本/v4.9` — 早期版本兼容性

### 按功能领域
- `类别/基础知识` — 安装、界面、基础概念
- `类别/API` — 脚本 API、Remote API、Plugin API
- `类别/运动规划` — OMPL、路径规划、避障
- `类别/运动学` — 正/逆运动学、雅可比
- `类别/控制` — PID、力控制、阻抗控制
- `类别/感知` — 视觉传感器、激光雷达、点云
- `类别/协同仿真` — ROS、Simulink、Python 协同
- `类别/数字孪生` — Digital Twin 相关
- `类别/建模` — 场景搭建、模型制作
- `类别/案例` — 工业应用案例

### 按难度
- `难度/初级`
- `难度/中级`
- `难度/高级`

### 按文档状态
- `状态/待整理`
- `状态/已完成`
- `状态/需验证`
- `状态/归档`

## 内容质量准则

1. **每个概念一条笔记** — 单一职责原则，一条笔记只讲一个概念
2. **链接优先** — 使用 `[[wikilink]]` 链接相关知识，形成知识网络
3. **代码可运行** — 所有 Lua/Python 脚本需标注适用的 CoppeliaSim 版本
4. **图文并茂** — 关键步骤、场景截图、流程图使用 `![[image.png]]` 嵌入
5. **来源可溯** — 从 raw/ 引用的材料必须注明来源文件名
6. **API 笔记模板** — 每个 API 条目包含：函数签名、参数说明、返回值、使用示例、注意事项
7. **案例笔记模板** — 每个案例包含：场景描述、系统架构、关键步骤、关键代码、结果分析

## LLM 工作流规范（AI 维护者指南）

### 信息收集阶段
1. 使用 `curl` 或搜索工具从官方文档、论坛、GitHub 获取源材料
2. 原始资料存入 `raw/` 目录，不做修改
3. 从 raw 提取关键信息，撰写 wiki 条目

### 知识整理阶段
1. 按分类目录创建对应 wiki 条目
2. 使用 `[[wikilink]]` 建立条目之间的关联
3. 为每个条目添加适当的标签和元数据
4. 代码片段标注版本兼容性

### 质量检查阶段
- ✅ 所有 `[[wikilink]]` 指向存在的笔记
- ✅ Frontmatter 完整且符合规范
- ✅ 标签使用分类体系中的已有标签
- ✅ 技术内容经过验证或注明待验证
- ✅ 中英文术语使用一致

### 更新频率
- wiki/ 中的内容随学习进度持续更新
- outputs/ 在每次深度研究后生成报告
- 对 raw/ 中的资料定期回顾，过时的标注 `状态/归档`

## 许可证与约定

- 知识库内容仅供个人学习研究
- 引用外部资料时保留原始版权声明
- 代码片段遵循对应开源项目的许可协议

## 快速开始

首次使用：
1. 浏览 `wiki/00-基础知识/` 了解 CoppeliaSim 基本概念
2. 查阅 `wiki/01-API参考/` 学习脚本编程
3. 参考 `wiki/06-工业案例/` 进行实践
4. 学习中遇到的问题记录在 `outputs/qa/` 中
