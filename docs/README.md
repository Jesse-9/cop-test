# cop-test 文档与知识库

本目录是 `cop-test`（岛式车间柔性生产仿真）的文档与 LLM 知识库根目录。

## 目录结构

```
docs/
├── README.md          # 本文件
├── CLAUDE.md          # 知识库 Schema 与维护规范 (AI 维护者必读)
├── MCP_USAGE.md       # MCP 服务器使用文档 (10 个工具)
├── USAGE.md           # 从安装到运行的使用指南
├── raw/               # 官方手册原始资料 (只读, 永不修改)
│   └── README.md      # 原始资料清单与来源验证
├── wiki/              # CoppeliaSim 维基 (AI 维护, 00~99 分类)
│   └── index.md       # 维基导航索引
└── outputs/           # 生成产物
    ├── reports/       # 研究报告
    ├── qa/            # 问答记录
    └── analysis/      # 技术分析
```

## 快速入口

| 想做什么 | 去哪 |
|---------|------|
| 从安装到运行的使用指南 | [USAGE.md](USAGE.md) |
| 用 MCP 编排仿真 | [MCP_USAGE.md](MCP_USAGE.md) |
| 了解知识库写作规范 | [CLAUDE.md](CLAUDE.md) |
| 查官方手册原文 | [raw/](raw/) |
| 学 CoppeliaSim | [wiki/index.md](wiki/index.md) |
| 看本项目实战案例 | [wiki/06-工业案例/06-02-岛式车间柔性生产案例.md](wiki/06-工业案例/06-02-岛式车间柔性生产案例.md) |

## 项目源码导航

项目根目录在 `docs/` 的上一层，源码模块见根目录 [README.md](../README.md) 与 [CLAUDE.md](../CLAUDE.md)。
