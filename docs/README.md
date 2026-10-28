# cop-test 文档与知识库

本目录的导航、文档职责和快速入口见本文件。具体运行命令由根目录下的 [项目使用指南](wiki/98-ProjectUsage/USAGE.md) 维护；不要在此复制阶段运行步骤。

本目录是 `cop-test`（岛式车间柔性生产仿真）的文档与 LLM 知识库根目录。

## 目录结构

```
docs/
├── README.md          # 本文件
├── CLAUDE.md          # 知识库 Schema 与维护规范 (AI 维护者必读)
├── raw/               # 官方手册原始资料 (只读, 永不修改)
│   └── README.md      # 原始资料清单与来源验证
├── wiki/              # CoppeliaSim 维基 (AI 维护, 00~99 分类, 119 条目)
│   ├── index.md       # 维基导航索引
│   ├── _REBUILD_SPEC.md  # 知识库重构规范
│   ├── 00-基础知识/    # 7 条 (平台概述/新特性/安装/场景/界面/插件/兼容性)
│   ├── 01-API参考/    # 7 概念 + 57 核心独立 API 条目
│   │   └── api/       # 57 个 Regular API 独立条目
│   ├── 02-仿真建模/    # 13 条 (场景/形状/关节/dummy/路径/传感器/灯光等)
│   ├── 03-运动规划与控制/ # 6 条 (IK/OMPL/FK-IK/simGeom/轨迹/力控制)
│   ├── 04-感知与视觉/  # 3 条 (相机/视觉传感器/回调与图像处理)
│   ├── 05-数字孪生与协同仿真/ # 6 条 (ROS2/ROS1/Python/b0/legacy/simROS2)
│   ├── 06-工业案例/    # 14 条 (UR5案例/岛式车间/阶段一~三实施与文档/运动阶段说明)
│   ├── 07-API脚本示例/ # 1 条 (Lua脚本模式)
│   ├── 98-ProjectUsage/ # 项目使用指南与 MCP 使用说明
│   └── 99-附录/       # 3 条 (术语表/快捷键/对话框)
└── outputs/           # 生成产物
    ├── reports/       # 研究报告
    ├── qa/            # 问答记录
    └── analysis/      # 技术分析
```

## 快速入口

| 想做什么 | 去哪 |
|---------|------|
| 从安装到运行的使用指南 | [wiki/98-ProjectUsage/USAGE.md](wiki/98-ProjectUsage/USAGE.md) |
| 用 MCP 编排仿真 | [wiki/98-ProjectUsage/MCP_USAGE.md](wiki/98-ProjectUsage/MCP_USAGE.md) |
| 本项目工程约束与实测记录 | [../PROJECT_ENGINEERING_REFERENCE.md](../PROJECT_ENGINEERING_REFERENCE.md) |
| 了解知识库写作规范 | [CLAUDE.md](CLAUDE.md) |
| 查官方手册原文 | [raw/](raw/) |
| 学 CoppeliaSim | [wiki/index.md](wiki/index.md) |
| 看本项目实战案例 | [wiki/06-工业案例/06-02-岛式车间柔性生产案例.md](wiki/06-工业案例/06-02-岛式车间柔性生产案例.md) |

## 项目源码导航

项目根目录在 `docs/` 的上一层，源码模块见根目录 [README.md](../README.md) 与 [CLAUDE.md](../CLAUDE.md)。
