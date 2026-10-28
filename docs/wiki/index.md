---
title: CoppeliaSim 知识库导航
type: reference
tags:
  - coppeliasim
  - 类别/基础知识
  - 难度/初级
created: 2024-07-13
updated: 2026-10-25
status: published
sim_version: "4.10"
---

# CoppeliaSim 知识库导航

> 基于 **CoppeliaSim 4.10** 官方手册（`docs/raw/manual/`）重构的工业场景仿真知识库。
> 所有条目以官方 .htm / regularApi 函数页 / JSON 索引为权威来源，中文精炼 + 可运行代码。

## 📚 快速入口

| 分类 | 说明 | 推荐起点 |
|------|------|----------|
| [[00-基础知识/00-01-CoppeliaSim-overview\|📖 平台概述]] | 架构、5 种编程途径、功能模块 | ⭐ 从这开始 |
| [[01-API参考/01-01-Regular-API-overview\|🔧 Regular API]] | API 框架、三语言、函数分类总览 | 快速查函数 |
| [[01-API参考/01-02-Remote-API-ZMQ\|🔌 ZMQ Remote API]] | ZeroMQ 远程 API + stepping + Python 客户端 | 外部控制必学 |
| [[03-运动规划与控制/03-01-逆运动学-simIK\|🎯 IK 求解]] | simIK 插件 + IK group/element | 机器人必学 |
| [[06-工业案例/06-01-UR5-抓取与放置案例\|🏭 UR5 案例]] | UR5 抓取放置完整案例 | 动手实践 |
| [[06-工业案例/06-02-岛式车间柔性生产案例\|🏗 岛式车间]] | 本仓库实战（MCP + ZMQ） | 仓库主线 |
| [[01-API参考/api/\|📇 核心 API 字典]] | 57 个核心 Regular API 独立条目 | 查 API |
| [[99-附录/99-01-术语表\|📗 术语表]] | 常用术语定义 | 随时查阅 |

## 📂 目录索引

### 00 基础知识
- [[00-基础知识/00-01-CoppeliaSim-overview]] — 平台概述与架构（5 编程途径 / 5 物理引擎 / script objects）
- [[00-基础知识/00-02-CoppeliaSim-v410-新特性]] — 4.10 新特性与版本基线
- [[00-基础知识/00-03-安装与设置]] — 安装、命令行、设置、add-on 激活
- [[00-基础知识/00-04-场景与模型管理]] — 场景(.ttt)/模型(.ttm)、对象类型总览、entity/collection
- [[00-基础知识/00-05-用户界面与视图]] — UI、页面视图、对象操作、快捷键
- [[00-基础知识/00-06-插件与附加组件]] — 插件体系、add-on、插件开发入口
- [[00-基础知识/00-07-兼容性与版本]] — 版本兼容、迁移、legacy 标注

### 01 API 参考
- [[01-API参考/01-01-Regular-API-overview]] — Regular API 详解（框架/三语言/函数分类/常量/Properties）
- [[01-API参考/01-02-Remote-API-ZMQ]] — ZeroMQ Remote API（架构/stepping/Python/WS/asyncio）
- [[01-API参考/01-03-常量与参数ID]] — API 常量、object parameter IDs、Properties 体系
- [[01-API参考/01-04-插件API总览]] — 各插件命名空间（simIK/simOMPL/simGeom/simVision/simROS2…）一览
- [[01-API参考/01-05-几何与运动学例程]] — Coppelia Geometric/Kinematics Routines（可嵌入 C++）
- [[01-API参考/01-06-脚本回调函数清单]] — 全部系统回调函数总览
- [[01-API参考/01-07-文件格式-XML-URDF-SDF]] — 场景/模型 XML、URDF/SDF/glTF/Assimp
- [[01-API参考/api/]] — 核心 API 独立条目（57 条，见下方 API 字典）

### 02 仿真建模
- [[02-仿真建模/02-01-分层命名场景构建]] — 分层路径 + 4.10 别名陷阱 + 内置模型回退
- [[02-仿真建模/02-02-形状与几何体]] — Shape 类型、primitive/mesh、编辑模式、凸包
- [[02-仿真建模/02-03-关节与关节模式]] — 4 关节类型、3 模式、高低层控制
- [[02-仿真建模/02-04-虚拟点-Dummy]] — Dummy、参考系、assembly、link dummy
- [[02-仿真建模/02-05-路径-Path]] — Path 伪对象、控制点、shaping、沿路径运动
- [[02-仿真建模/02-06-力传感器]] — Force sensor、断裂、接触回调
- [[02-仿真建模/02-07-接近传感器]] — Proximity sensor、检测体积、参数
- [[02-仿真建模/02-08-图与数据可视化]] — Graph、stream、curve、数据输出
- [[02-仿真建模/02-09-八叉树与点云]] — OC tree、point cloud
- [[02-仿真建模/02-10-集合-Collections]] — Collection、entity、可碰撞/可测量/可检测
- [[02-仿真建模/02-11-动力学仿真设计]] — 动力学模块、5 引擎、动态仿真设计
- [[02-仿真建模/02-12-碰撞检测与距离计算]] — collision/distance 模块、collection 配对
- [[02-仿真建模/02-13-灯光]] — Light 对象、三种类型

### 03 运动规划与控制
- [[03-运动规划与控制/03-01-逆运动学-simIK]] — simIK 插件 API 详解
- [[03-运动规划与控制/03-02-OMPL运动规划]] — simOMPL 运动规划
- [[03-运动规划与控制/03-03-运动学基础-FK-IK]] — FK/IK 概念、IK group/element、约束
- [[03-运动规划与控制/03-04-几何计算-simGeom]] — simGeom 独立几何例程
- [[03-运动规划与控制/03-05-轨迹与路径规划模块]] — 路径规划 vs 轨迹生成（Ruckig/RML）
- [[03-运动规划与控制/03-06-力控制基础]] — 力/力矩控制、阻抗、力位混合

### 04 感知与视觉
- [[04-感知与视觉/04-01-相机-Cameras]] — 相机（视口观察视角）
- [[04-感知与视觉/04-02-视觉传感器]] — 视觉传感器（图像/深度/检测/渲染时机）
- [[04-感知与视觉/04-03-视觉回调与图像处理]] — vision callback、simVision、图像变换

### 05 数字孪生与协同仿真
- [[05-数字孪生与协同仿真/05-01-ROS2集成]] — ROS2 集成指南
- [[05-数字孪生与协同仿真/05-02-数字孪生概述]] — 数字孪生架构
- [[05-数字孪生与协同仿真/05-03-Python客户端集成]] — 库方式 vs ZMQ 客户端、外部控制范式
- [[05-数字孪生与协同仿真/05-04-ROS1集成]] — ROS Interface（历史）
- [[05-数字孪生与协同仿真/05-05-B0与Legacy远程API]] — b0/legacy remote API（已弃用）
- [[05-数字孪生与协同仿真/05-06-simROS2接口]] — simROS2 插件 API 详解

### 06 工业案例
- [[06-工业案例/06-01-UR5-抓取与放置案例]] — UR5 抓取放置完整案例
- [[06-工业案例/06-02-岛式车间柔性生产案例]] — 岛式车间柔性生产（本仓库实战）
- [[06-工业案例/06-03-src模块参考]] — src 各源码模块作用/导出/用法参考
- [[06-工业案例/06-04-柔性工业车间重构方案]] — 内置传送带、UR5、吸盘与 pioneer p3dx 的重构方案（审核中）
- [[06-工业案例/06-05-柔性工业车间分阶段执行步骤]] — 四个生产建设阶段及最后的统一故障恢复阶段实施与验收计划（审核中）
- [[06-工业案例/06-09-柔性工业车间阶段二-物理约束与运动规划]] — 阶段二独立实施方案与最小验收（已实施，2026-10-21）
- [[06-工业案例/06-10-柔性工业车间阶段2.01-动作轨迹优化]] — 阶段 2.01 基础动作/轨迹优化（已验收）；§8/2.02 为历史 AGV 实验记录，路径策略已由 06-15 取代
- [[06-工业案例/06-11-柔性工业车间阶段一二遗留动作质量与后续优化规划]] — 阶段一/二遗留动作质量问题清单及候选阶段 2.03 验证规划（规划中；06-15 基线待重新测量，2026-10-28）
- [[06-工业案例/06-12-柔性工业车间阶段三双工位详细实施步骤]] — 侧置双工位布局、阶段一/二能力复用、双向 AGV 任务流及精简验收（已实施并通过，2026-10-25）
- [[06-工业案例/06-13-柔性工业车间阶段三双工位实施记录]] — L2_MIRROR 双工位、资源锁、AGV 两站转运、五个验收场景与动作质量记录（2026-10-25）
- [[06-工业案例/06-14-柔性工业车间机械臂运动阶段说明]] — 阶段二/三 target=agv 的 5 个臂运动阶段（含 PLACING 下探）、6 个语义路径点与执行约束（2026-10-28）
- [[06-工业案例/06-15-柔性工业车间水平带载转运与放置下探修改方案]] — 阶段二/三水平带载转运与放置下探的待实施方案；当前运动阶段说明见 06-14
- [[06-工业案例/06-16-柔性工业车间阶段四多岛实施步骤]] — 阶段四拓扑迁移、阶段三能力复用、布局方案、工作包与验收矩阵（规划中）
- [[06-工业案例/06-06-场景与工程一致性审查记录]] — 新方案与 skill、全局规范、wiki 的一致性审查（审核中）

### 07 API 脚本示例
- [[07-API脚本示例/07-01-Lua基本脚本模式]] — 4.10 脚本体系 + 回调骨架（Lua/Python）

### 98 项目使用
- [[98-ProjectUsage/MCP_USAGE]] — MCP 服务器使用说明
- [[98-ProjectUsage/USAGE]] — 项目使用指南

### 99 附录
- [[99-附录/99-01-术语表]] — 术语表
- [[99-附录/99-02-快捷键速查]] — 快捷键速查表
- [[99-附录/99-03-对话框与属性参考]] — 对话框与属性参考

## 📇 核心 API 字典（[[01-API参考/api/]]）

按主题分组，共 57 个核心 Regular API 独立条目（get/set 合并、同类小函数附录并入）。

**仿真生命周期** — [[01-API参考/api/api-simStartSimulation\|start]] · [[01-API参考/api/api-simStopSimulation\|stop]] · [[01-API参考/api/api-simPauseSimulation\|pause]] · [[01-API参考/api/api-simGetSimulationState\|getSimulationState]]

**对象句柄与查询** — [[01-API参考/api/api-simGetObject\|getObject]] · [[01-API参考/api/api-simGetObjects\|getObjects]] · [[01-API参考/api/api-simGetObjectsInTree\|getObjectsInTree]] · [[01-API参考/api/api-simGetObjectChild\|getObjectChild]] · [[01-API参考/api/api-simGetObjectParent\|getObjectParent]] · [[01-API参考/api/api-simGetObjectAlias\|getObjectAlias]] · [[01-API参考/api/api-simGetObjectUid\|getObjectUid]]

**位姿变换** — [[01-API参考/api/api-simGetObjectPosition\|getObjectPosition]] · [[01-API参考/api/api-simSetObjectPosition\|setObjectPosition]] · [[01-API参考/api/api-simGetObjectOrientation\|getObjectOrientation]] · [[01-API参考/api/api-simSetObjectOrientation\|setObjectOrientation]] · [[01-API参考/api/api-simGetObjectPose\|getObjectPose]] · [[01-API参考/api/api-simSetObjectPose\|setObjectPose]] · [[01-API参考/api/api-simGetObjectQuaternion\|getObjectQuaternion]] · [[01-API参考/api/api-simSetObjectQuaternion\|setObjectQuaternion]]

**矩阵与姿态运算** — [[01-API参考/api/api-simBuildMatrix\|buildMatrix]] · [[01-API参考/api/api-simMultiplyMatrices\|multiplyMatrices]] · [[01-API参考/api/api-simMultiplyPoses\|multiplyPoses]] · [[01-API参考/api/api-simInterpolatePoses\|interpolatePoses]]

**关节** — [[01-API参考/api/api-simGetJointPosition\|getJointPosition]] · [[01-API参考/api/api-simSetJointPosition\|setJointPosition]] · [[01-API参考/api/api-simGetJointTargetPosition\|getJointTargetPosition]] · [[01-API参考/api/api-simSetJointTargetPosition\|setJointTargetPosition]] · [[01-API参考/api/api-simGetJointTargetVelocity\|getJointTargetVelocity]] · [[01-API参考/api/api-simSetJointTargetVelocity\|setJointTargetVelocity]] · [[01-API参考/api/api-simGetJointMode\|getJointMode]] · [[01-API参考/api/api-simSetJointMode\|setJointMode]] · [[01-API参考/api/api-simCheckIkGroup\|checkIkGroup]]

**运动** — [[01-API参考/api/api-simGetObjectVelocity\|getObjectVelocity]] · [[01-API参考/api/api-simGetConfigForTipPose\|getConfigForTipPose]] · [[01-API参考/api/api-simMoveToConfig\|moveToConfig]]

**视觉传感器** — [[01-API参考/api/api-simGetVisionSensorImage\|getVisionSensorImage]] · [[01-API参考/api/api-simSetVisionSensorImage\|setVisionSensorImage]] · [[01-API参考/api/api-simGetVisionSensorDepthBuffer\|getVisionSensorDepthBuffer]] · [[01-API参考/api/api-simHandleVisionSensor\|handleVisionSensor]] · [[01-API参考/api/api-simCheckVisionSensor\|checkVisionSensor]]

**检测** — [[01-API参考/api/api-simCheckProximitySensor\|checkProximitySensor]] · [[01-API参考/api/api-simHandleCollision\|handleCollision]] · [[01-API参考/api/api-simHandleDistance\|handleDistance]]

**信号通信** — [[01-API参考/api/api-simSetIntegerSignal\|setIntegerSignal]] · [[01-API参考/api/api-simSetFloatSignal\|setFloatSignal]] · [[01-API参考/api/api-simSetStringSignal\|setStringSignal]] · [[01-API参考/api/api-simSetDoubleSignal\|setDoubleSignal]]

**集合** — [[01-API参考/api/api-simCreateCollection\|createCollection]] · [[01-API参考/api/api-simAddItemToCollection\|addItemToCollection]]

**创建场景对象** — [[01-API参考/api/api-simCreateDummy\|createDummy]] · [[01-API参考/api/api-simCreateJoint\|createJoint]] · [[01-API参考/api/api-simCreateVisionSensor\|createVisionSensor]] · [[01-API参考/api/api-simCreatePrimitiveShape\|createPrimitiveShape]]

**图与数据** — [[01-API参考/api/api-simSetGraphStreamValue\|setGraphStreamValue]] · [[01-API参考/api/api-simHandleGraph\|handleGraph]]

**脚本与日志** — [[01-API参考/api/api-simCallScriptFunction\|callScriptFunction]] · [[01-API参考/api/api-simAddLog\|addLog]]

## 🔗 知识图谱

```
基础知识 ◄────► API参考 ◄────► 脚本示例
   ▲                      ▲
   │                      │
   ▼                      ▼
仿真建模 ◄────► 运动规划与控制
   ▲                      ▲
   │                      │
   ▼                      ▼
感知与视觉 ◄────► 数字孪生 ◄────► 工业案例
```

## 📊 知识库统计

| 项目 | 数量 |
|------|------|
| 目录分类 | 9 个（00–07、99）+ 98 项目使用 |
| 概念/教程/案例条目 | 57 条 |
| 项目使用与附录 | 5 条 |
| 核心 API 独立条目 | 57 条 |
| 原始官方手册 | 200+ HTM + 939 函数页 + JSON 索引 |

## 🔍 阅读路径推荐

```
👤 初学者
  00-基础知识 → 07-脚本模式 → 03-IK → 06-案例

👤 中级开发者
  01-API参考 → 01-ZMQ → 03-OMPL → 04-感知

👤 高级工程师
  05-ROS2集成 → 05-数字孪生 → 插件开发
```
