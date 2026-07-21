---
title: 原始资料清单
type: reference
tags:
  - coppeliasim
  - 类别/基础知识
created: 2024-07-13
updated: 2026-07-21
status: published
---

# 原始资料清单 (Raw Sources)

## 来源验证

所有资料均从 [CoppeliaSim 官方用户手册](https://manual.coppeliarobotics.com/) 下载，已验证 URL 有效性（HTTP 200）。

**验证日期**: 2024-07-13
**验证方式**: `curl -sI -L` (HEAD + 跟随重定向)
**所有 URL 状态**: ✅ HTTP 200 OK

## 文件清单

### 官方手册文档 (12 份 + 1 补充)

| # | 文件名 | 原始 URL | 大小 (HTML) | 说明 |
|---|--------|----------|-------------|------|
| 1 | `src-doc-CoppeliaSim-API-functions` | `manual.coppeliarobotics.com/en/apiFunctions.htm` | 77 KB | Regular API 完整函数列表 |
| 2 | `src-doc-CoppeliaSim-OMPL-plugin` | `manual.coppeliarobotics.com/en/simOMPL.htm` | 128 KB | simOMPL 运动规划插件 API |
| 3 | `src-doc-CoppeliaSim-version-history` | `manual.coppeliarobotics.com/en/versionInfo.htm` | 150 KB | 完整版本历史 |
| 4 | `src-doc-CoppeliaSim-messaging-communication` | `manual.coppeliarobotics.com/en/meansOfCommunication.htm` | 16 KB | 通信框架概览 |
| 5 | `src-doc-CoppeliaSim-ROS2-tutorial` | `manual.coppeliarobotics.com/en/ros2Tutorial.htm` | 15 KB | ROS2 集成教程 |
| 6 | `src-doc-CoppeliaSim-kinematics-plugin` | `manual.coppeliarobotics.com/en/kinematicsPlugin.htm` | 11 KB | simIK 运动学插件文档 |
| 7 | `src-doc-CoppeliaSim-external-controller-tutorial` | `manual.coppeliarobotics.com/en/externalControllerTutorial.htm` | 8 KB | 外部控制器教程 |
| 8 | `src-doc-CoppeliaSim-solving-IK-FK` | `manual.coppeliarobotics.com/en/solvingIkAndFk.htm` | 7 KB | IK/FK 求解教程 |
| 9 | `src-doc-CoppeliaSim-plugins` | `manual.coppeliarobotics.com/en/plugins.htm` | 6 KB | 插件开发指南 |
| 10 | `src-doc-CoppeliaSim-path-motion-planning` | `manual.coppeliarobotics.com/en/pathAndMotionPlanningModules.htm` | 4 KB | 路径规划模块 |
| 11 | `src-doc-CoppeliaSim-ROS2-interface` | `manual.coppeliarobotics.com/en/ros2Interface.htm` | 3 KB | ROS2 接口 API |
| 12 | `src-doc-CoppeliaSim-apisOverview` | `manual.coppeliarobotics.com/en/apisOverview.htm` | 2 KB | API 框架总览 |
| — | `src-doc-CoppeliaSim-API-overview` | ~~`apiOverview.htm`~~ (重定向至 `apiFunctions.htm`) | 327 B | 重定向页，保留供参考 |

### 格式说明

每个源文件有两种格式：
- `.html` — 原始 HTML 版本（**权威来源**）
- `.md` — Pandoc 转换的 Markdown 版本（便于阅读，可能存在格式差异）

### 官方文档结构关系

```
manual.coppeliarobotics.com/en/
├── apiFunctions.htm ◄────────────── Regular API 函数目录（主索引）
├── apisOverview.htm ◄────────────── API 框架总览
├── plugins.htm ◄────────────────── 插件开发指南
├── meansOfCommunication.htm ◄───── 通信方式总览
├── kinematicsPlugin.htm ◄───────── simIK 插件
├── simOMPL.htm ◄────────────────── simOMPL 插件
├── ros2Tutorial.htm ◄───────────── ROS2 教程
├── ros2Interface.htm ◄──────────── ROS2 接口 API
├── solvingIkAndFk.htm ◄─────────── IK/FK 求解
├── pathAndMotionPlanningModules.htm ◄─ 路径规划
├── externalControllerTutorial.htm ◄─── 外部控制器
└── versionInfo.htm ◄────────────── 版本历史
```

### 补充源材料 (2026-07-21 抓取)

为丰富 `01-API参考`（Regular API 与 ZMQ Remote API）两条 wiki，从官方手册与 GitHub 补抓 7 份源材料。抓取方式：`defuddle parse <url> --md -o`（手册页），`curl -sL raw.githubusercontent.com`（GitHub markdown）。

| # | 文件名 | 原始 URL | 大小 | 说明 |
|---|--------|----------|------|------|
| 13 | `src-doc-CoppeliaSim-zmq-remote-api.md` | `manual.coppeliarobotics.com/en/zmqRemoteApiOverview.htm` | 7 KB | ZeroMQ Remote API 总览 + 各语言客户端示例 |
| 14 | `src-doc-CoppeliaSim-zmq-remote-api-protocol.md` | `github.com/CoppeliaRobotics/zmqRemoteApi/PROTOCOL.md` | 2 KB | ZMQ 协议 v2：REQ socket、CBOR、请求/响应字段、版本对应 |
| 15 | `src-doc-CoppeliaSim-zmq-remote-api-python-client.md` | `github.com/CoppeliaRobotics/zmqRemoteApi/clients/python/README.md` | 2 KB | Python 客户端安装/用法/asyncio/gather/Windows 事件循环 |
| 16 | `src-doc-CoppeliaSim-callback-functions.md` | `manual.coppeliarobotics.com/en/callbackFunctions.htm` | 13 KB | 全部系统回调 sysCall_*（Python+Lua 签名） |
| 17 | `src-doc-CoppeliaSim-accessing-scene-objects.md` | `manual.coppeliarobotics.com/en/accessingSceneObjects.htm` | 6 KB | 对象路径/别名语法（绝对/相对/通配/索引）+ setObjectAlias 字符限制 |
| 18 | `src-doc-CoppeliaSim-simulation.md` | `manual.coppeliarobotics.com/en/simulation.htm` | 4 KB | 仿真循环、实时模式、stepping 单步模式（官方示例） |
| 19 | `src-doc-CoppeliaSim-scripts.md` | `manual.coppeliarobotics.com/en/scripts.htm` | 2 KB | 脚本类型：sandbox/add-on/embedded(main/simulation/customization) |

> 注：第 13/16/17/18/19 份由 defuddle 转 markdown（手册为 frameset，转出正文完整）；第 14/15 份为 GitHub 原始 markdown（curl 直取，无格式损失）。

### 使用说明

1. ⚠️ **raw/ 目录的文件为源材料，不要修改**
2. 引用时在 wiki 条目的 `source:` 字段中注明文件名
3. 如发现官方文档更新，重新 curl 下载并替换对应文件
4. `.md` 版本由 Pandoc 自动转换，复杂的多语言代码块可能需参考 `.html` 原始版本
5. 2026-07-21 补充的 5 份手册页用 defuddle 而非 Pandoc 转出，仅保留 `.md` 一种格式
