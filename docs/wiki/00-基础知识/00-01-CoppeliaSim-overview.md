---
title: CoppeliaSim 概述与架构
type: concept
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/基础知识
  - 难度/初级
created: 2024-07-13
updated: 2024-07-13
status: published
source: src-doc-CoppeliaSim-API-overview.html
related:
  - [[wiki/00-基础知识/00-03-安装与设置]]
  - [[wiki/01-API参考/01-01-Regular-API-overview]]
sim_version: "4.10"
---

# CoppeliaSim 概述与架构

## 简介

**CoppeliaSim**（原名 V-REP，Virtual Robot Experimentation Platform）是一款功能强大的机器人仿真软件，由 Coppelia Robotics 开发。它基于**分布式控制架构**，每个实体（对象、模型、脚本）可以独立运行和控制，非常适合工业场景中复杂机器人系统的仿真。

## 核心特性

### 1. 分布式控制架构
- 每个场景对象可以挂载自己的**脚本**（Lua/Python），实现去中心化控制
- 支持**多种控制方式**同时运行：嵌入脚本、插件、Remote API、ROS/ROS2 接口
- 适合模拟**多机器人协作**和**复杂自动化系统**

### 2. 多种编程接口（API）
| API 类型 | 语言支持 | 用途 |
|----------|----------|------|
| Regular API | Lua, Python (via plugin) | 嵌入式脚本，在仿真内部运行 |
| Legacy Remote API | C/C++, Python, Java, Lua, MATLAB | 外部客户端远程控制 |
| ZeroMQ Remote API | C++, Python, Java, MATLAB | 推荐的外部通信方式（新版） |
| ROS/ROS2 Interface | C++ (Lua 绑定) | ROS 生态集成 |
| BlueZero Interface | C++ | 高性能自定义通信 |

### 3. 内置功能模块
- **运动学求解器**（simIK）— 正/逆运动学
- **运动规划器**（simOMPL）— 基于 OMPL 的路径规划
- **碰撞检测** — 内置碰撞/距离计算
- **动力学引擎** — 支持 Bullet, ODE, Newton, MuJoCo
- **路径规划** — 避障路径规划

### 4. 支持的动力学引擎
| 引擎 | 特点 | 适用场景 |
|------|------|----------|
| Bullet | 默认引擎，稳定高效 | 通用场景 |
| ODE | 精确碰撞处理 | 复杂接触场景 |
| Newton | 稳定的物理模拟 | 高精度需求 |
| MuJoCo (4.0+) | 生物力学和接触 | 高级研究 |

## 软件架构

```
┌─────────────────────────────────────────────────┐
│                 CoppeliaSim GUI                  │
├─────────────────────────────────────────────────┤
│              Main Script (场景主控)               │
├─────────┬─────────┬──────────┬──────────────────┤
│ 子脚本1 │ 子脚本2 │ ...      │ 定制化脚本        │
│ (Lua)   │ (Lua)   │          │ (Python/Lua)     │
├─────────┴─────────┴──────────┴──────────────────┤
│              插件系统 (Plugins)                   │
│  simIK  simOMPL  simROS2  simBlueZero  ...      │
├─────────────────────────────────────────────────┤
│            物理引擎 (Physics Engine)              │
│  Bullet | ODE | Newton | MuJoCo                 │
├─────────────────────────────────────────────────┤
│             通讯层 (Communication)               │
│  ZeroMQ | ROS/ROS2 | BlueZero | Serial          │
└─────────────────────────────────────────────────┘
```

## 脚本体系

CoppeliaSim 中有 4 种脚本类型：

### 1. 主脚本 (Main Script)
- 每个场景**只有一个**
- 负责仿真循环的核心控制（初始化、步进、结束）
- 通常**不推荐修改**（除非高级用户）

### 2. 子脚本 (Child Scripts)
- 挂载在场景中的对象上
- 分为**非线程**（Non-threaded，每仿真步执行一次）和**线程**（Threaded，独立循环运行）
- 大多数仿真逻辑在这里实现

### 3. 定制化脚本 (Customization Scripts)
- 附加在模型上，处理用户交互（拖拽、参数面板等）
- 不影响仿真逻辑

### 4. 联合仿真脚本 (Joint Control Scripts)
- 附加在关节上，用于自定义关节控制模式

## 仿真流程 (Simulation Loop)

```
1. 初始化阶段 ──→ 2. 仿真步进 (x N) ──→ 3. 结束阶段
     │                   │                     │
     ├─ 加载场景          ├─ 执行主脚本           ├─ 清理资源
     ├─ 初始化脚本        ├─ 执行子脚本           ├─ 保存状态
     ├─ 重置状态          ├─ 物理引擎计算         └─ 输出结果
     └─ 启动通讯          ├─ 传感器更新
                          ├─ 绘制渲染
                          └─ Remote API 同步
```

## 文件类型

| 扩展名 | 类型 | 说明 |
|--------|------|------|
| `.ttt` | 场景文件 | 完整的仿真场景 |
| `.ttm` | 模型文件 | 可复用的模型组件 |
| `.tt`  | 纹理文件 | 纹理贴图 |
| `.sim` | 仿真文件 | CoppeliaSim 4.0 以下版本旧格式 |

## 版本历史 (V-REP → CoppeliaSim)

- **V-REP 3.x** — 早期版本
- **CoppeliaSim 4.0** — 更名为 CoppeliaSim
- **CoppeliaSim 4.1** — 引入 ZeroMQ Remote API
- **CoppeliaSim 4.2** — PyRep 集成改善
- **CoppeliaSim 4.3** — MuJoCo 引擎支持
- **CoppeliaSim 4.4** — 改进的 ROS2 支持
- **CoppeliaSim 4.5** — 性能优化
- **CoppeliaSim 4.6** — 新 UI 改进
- **CoppeliaSim 4.7** — Python 脚本支持增强
- **CoppeliaSim 4.8** — 进一步优化
- **CoppeliaSim 4.9** — 功能增强
- **→ **CoppeliaSim 4.10** — 最新版本

> 详细版本历史见 [[raw/src-doc-CoppeliaSim-version-history.html]]

## 应用场景

- **工业机器人仿真** — 焊接、搬运、装配、喷涂
- **移动机器人** — AGV/AMR 导航、路径规划
- **多机器人协作** — 双臂协同、多机调度
- **数字孪生** — 物理设备与虚拟同步
- **生产线仿真** — 自动化产线设计与验证
- **机器视觉** — 视觉传感器、点云处理
- **教育与研究** — 算法验证、教学演示

## 学习路径推荐

```
初级 ──→ 掌握基本界面与场景搭建
  │
  ↓
中级 ──→ 学习 Lua 脚本编程与 API 使用
  │
  ↓
高级 ──→ Remote API 控制、ROS集成、插件开发
  │
  ↓
专家 ──→ 数字孪生、协同仿真、性能优化
```

---

**关联条目：**
- [[wiki/00-基础知识/00-03-安装与设置]] — 安装与配置指南
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 详解
- [[wiki/99-附录/99-01-术语表]] — 术语表
