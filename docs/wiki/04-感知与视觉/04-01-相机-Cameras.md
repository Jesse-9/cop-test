---
title: 相机 Cameras
type: concept
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/感知
  - 难度/初级
created: 2026-07-24
updated: 2026-07-24
status: published
source:
  - cameras.htm
  - cameraPropertiesDialog.htm
  - viewableObjects.htm
related:
  - "[[wiki/04-感知与视觉/04-02-视觉传感器]]"
  - "[[wiki/00-基础知识/00-05-用户界面与视图]]"
  - "[[wiki/00-基础知识/00-04-场景与模型管理]]"
  - "[[wiki/02-仿真建模/02-01-分层命名场景构建]]"
sim_version: "4.10"
---

# 相机 Cameras

## 概念

相机（Camera）是一种**可视对象（viewable object）**，用于在视口（view）中观察场景。可以在场景中放置任意数量的相机，每个相机提供一个不同的观察视角。

可视对象包含两类：**相机**与**视觉传感器（vision sensor）**。两者都能关联到视口显示图像内容，但职责不同——相机只用于"人看场景"，视觉传感器用于"程序读图像做感知"。

## 相机 vs 视觉传感器

| 维度 | 相机 Camera | 视觉传感器 Vision sensor |
|------|-------------|--------------------------|
| 分辨率 | 无固定分辨率，自动适配视口尺寸 | 固定分辨率 |
| 图像访问 | 不直接经 API 暴露（仅经回调机制） | 可经 `sim.getVisionSensorImg` 等 API 读取 |
| 图像处理 | 不直接支持 | 支持视觉回调、插件处理 |
| 性能 | CPU 占用低、较快 | 较慢 |
| 可渲染对象 | 所有对象类型 | 主要是 shape |

> 切勿将二者混淆。需要程序读取图像/做识别时用视觉传感器，见 [[wiki/04-感知与视觉/04-02-视觉传感器]]。

## 添加相机

- 菜单 `Add > Camera`：在默认位姿添加相机。
- 推荐做法：在某个视口右键 `Popup menu > Add > Camera`，相机将被添加在当前视图前方，并自动选中。随后用 `Popup menu > View > Associate view with selected camera` 即可透过它观察。
- 当视口尚未关联任何可视对象时，`Add > Camera` 会直接添加相机并与之关联。
- 一个相机可关联到任意多个视口。

也可通过 `Popup menu > View > View selector..` 在预览窗口中选择目标相机视图。

## 鼠标导航

相机关联到视口后，可用工具栏按钮操作：

- **Pan**：在垂直于视线的平面内平移相机，点击的点与鼠标同速移动。
- **Rotate**：绕点击点旋转相机。
- **Shift**：沿视线方向平移相机（透视模式下相当于缩放）。
- **Camera angle**：调整透视投影角度，或正交投影下的视口尺寸。
- **Fit-to-view**：自动框选所有选中对象（或整个场景）。

鼠标滚轮沿视线平移（透视模式）或调整视口尺寸（正交模式）。按住右键拖动可绕点击点旋转。

默认导航三件套：左键平移、滚轮沿视线平移、右键旋转。

## 对象追踪

选中某对象后，在视口 `Popup menu > View > Track selected object` 可让相机自动追踪该对象。追踪时相机**位置不变**，仅自动调整朝向使目标始终在视野内。

这与把相机作为子对象挂到目标下的父子关系不同：父子关系会随目标一起移动，而追踪只改朝向。

## 相机属性对话框

通过 `Tools > Scene object properties` 打开场景对象对话框，选中相机后点击 `Camera` 按钮进入相机属性子对话框。主要参数：

| 参数 | 说明 |
|------|------|
| Perspective angle | 透视投影下的最大张开角 |
| Orthographic size | 正交投影下的视口尺寸 |
| Near / far clipping plane | 近/远裁剪面距离；两者间距过大易出现 z-fighting 伪影 |
| Render mode | 渲染模式 |
| Tracked object | 被追踪的对象（仅影响朝向） |
| Allow translation / rotation | 是否允许平移/旋转操作 |
| Parent as manipulation proxy | 启用后工具栏操作作用于父对象而非相机本身（便于多相机联动） |
| Object size / Color | 相机图标的视觉尺寸与颜色，无功能意义 |
| Show view frustum | 显示视锥体 |

## 与本仓库的关系

`cop-test` 的 `build_scene.clear_scene` 默认会把 `object_camera_type` 纳入清场类型清单。若清场后视口丢失默认观察视角，可：

1. 从清场类型清单中移除 `object_camera_type`，保留默认相机；或
2. 构建场景后重新添加一个默认相机用于观察。

本仓库当前是纯控制 + 机械臂视觉动画层，未启用感知，相机仅用于人工观察仿真过程。

## 参见

- [[wiki/04-感知与视觉/04-02-视觉传感器]] — 程序可读取图像的感知对象
- [[wiki/02-仿真建模/02-01-分层命名场景构建]] — 清场时对相机/视觉传感器的处理
