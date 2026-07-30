---
title: 文件格式 - XML/URDF/SDF/glTF/Assimp
type: reference
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/API
  - 类别/建模
  - 难度/中级
created: 2026-07-24
updated: 2026-07-24
status: published
source:
  - xmlFormat.htm
  - urdfPlugin.htm
  - simURDF.htm
  - sdfPlugin.htm
  - simSDF.htm
  - colladaPlugin.htm
  - gltfApi.htm
  - simGLTF.htm
  - assimpApi.htm
  - simAssimp.htm
  - importExport.htm
related:
  - "[[wiki/00-基础知识/00-04-场景与模型管理]]"
  - "[[wiki/01-API参考/01-04-插件API总览]]"
  - "[[wiki/02-仿真建模/02-02-形状与几何体]]"
  - "[[wiki/06-工业案例/06-01-UR5-抓取与放置案例]]"
sim_version: "4.10"
---

# 文件格式 - XML/URDF/SDF/glTF/Assimp

> CoppeliaSim 场景(`.ttt`)/模型(`.ttm`)的二进制格式之外，还支持一系列文本中间格式用于导入导出：原生 XML、URDF、SDF、glTF、Collada，以及通过 Assimp 插件支持的通用网格格式。各格式定位不同——机器人模型用 URDF/SDF，通用网格用 glTF/Assimp，场景/模型的版本控制与外部生成用 XML。

## 格式总览

| 格式 | 用途 | 入口插件 | 可读性 |
|------|------|---------|--------|
| CoppeliaSim XML | 场景/模型的版本控制、外部生成、手工编辑 | 内置 | 纯文本 |
| URDF | 机器人模型导入/导出（ROS 生态） | simURDF 插件 | 纯文本 |
| SDF | Gazebo 风格机器人/世界模型导入 | simSDF 插件 | 纯文本 |
| glTF (.gltf/.glb) | 场景/动画导出，Web 可视化 | simGLTF 插件 | 文本/二进制 |
| Collada (.dae) | 网格导入导出（XML 交换格式） | Assimp 插件 | 纯文本 |
| Assimp 通用网格 | OBJ/STL/FBX/3DS 等几十种 CAD 网格 | Assimp 插件 | 视格式 |

## CoppeliaSim XML 格式（xmlFormat）

CoppeliaSim 支持两种 XML 序列化格式：

- **exhaustive（无损）**：保存与加载之间场景/模型信息完全一致。不便于手工修改，适合版本控制。
- **simple（有损）**：只保存部分信息，但可手工创建/修改，适合外部程序生成初始模型或编写导出器。

两者输出受 `system/usrset.txt` 中两个变量控制：

- `xmlExportSplitSize`：子集数据大小阈值，超过则引用并写入独立文件。设为 `0` 则只生成单文件。
- `xmlExportKnownFormats`：为 `true` 且导出产生多文件时，图片用 `.png`、网格用 `.dae`。`.dae` 仅 simple 格式支持。

### Simple XML 结构

`filetype` 取 `simpleScene` 或 `simpleModel`。顶层骨架：

```xml
<CoppeliaSim>
  <filetype>simpleScene</filetype>
  <xmlSerializationNb>1</xmlSerializationNb>
  <environment>...</environment>   <!-- 加载模型时忽略 -->
  <settings>...</settings>           <!-- 加载模型时忽略 -->
  <dynamics>...</dynamics>          <!-- 加载模型时忽略 -->
  <simulation>...</simulation>      <!-- 加载模型时忽略 -->
  <dummy>
    <camera>
      <common>
        <alias>DefaultCamera</alias>
        <localFrame>
          <position>1.120530 -1.899800 1.079718</position>
          <euler>-110.932999 -28.703005 169.590027</euler>
        </localFrame>
      </common>
    </camera>
  </dummy>
</CoppeliaSim>
```

### 形状元素

`<shape>` 内部可为 `compound`、`primitive`、`heightfield` 或 `mesh`：

- **primitive**：`<type>` 取 `cuboid`/`sphere`/`cylinder`/`cone`/`plane`/`disc`，带 `<size>`。
- **mesh**：可引用外部文件 `<fileName>...dae</fileName>`，或内联 `<vertices>` 与 `<indices>`。
- **heightfield**：`<size>` + `<data>` + `<gridStep>`。
- **compound**：至少包含 2 个 `primitive`/`mesh`/`compound` 子元素。

带动力学属性的形状还需 `<dynamics>`：`<respondableMask>`、`<mass>`、`<localInertiaFrame>`、`<principalMomentOfInertia>`、`<switches>`（`<static>`、`<respondable>` 等）、`<material>` 下分引擎 `<bullet>`/`<ode>`/`<vortex>`/`<newton>`。

### 关节元素

`<joint>` 的 `<type>` 取 `revolute`/`prismatic`/`spherical`；`<mode>` 取 `kinematic`/`dependent`/`dynamic`。动态模式带 `<dynamics>`：`<maxForce>`、`<ctrlMode>`、`<targetPosition>`、`<upperVelocityLimit>` 等。

## URDF 导入导出（simURDF）

URDF（Unified Robot Description Format）是 ROS 描述机器人模型的标准 XML 格式。CoppeliaSim 通过 simURDF 插件提供导入导出，GUI 入口为 `[Modules > Importers > URDF importer...]` 与 `[Modules > Exporters > URDF exporter...]`。

### simURDF.import

```python
robotName = simURDF.import(fileName, options=0, replacementString=None)
```

- `fileName` (str)：URDF 文件路径。
- `options` (int, bit 编码)：
  - bit0：显示碰撞链接
  - bit1：显示关节
  - bit2：对非凸碰撞体进行凸分解
  - bit3：无视觉时创建视觉
  - bit5：不居中到地面以上
  - bit6：不封装为模型
  - bit7：不交替本地响应掩码
  - bit8：动态关节不做位置控制
  - bit9：对非凸碰撞体取凸包
  - bit10：尽可能将 shape 原点设为关节位置
- `replacementString` (str, 可选)：替换 XML 中所有 `package://` 前缀。
- 返回：`robotName` (str)。

### simURDF.export

```python
simURDF.export(modelHandle, fileName, options=0)
```

- `modelHandle` (int)：模型句柄。
- `fileName` (str)：URDF 输出路径。
- `options` (int, bit 编码)：
  - bit0：将 dummy 转为红色立方体
  - bit1：尽可能让 visual/collision 原点位于父关节处
  - bit2：不将关节位置重置为 0

## SDF 导入（simSDF）

SDF（Simulation Description Format，Gazebo 生态）导入通过 simSDF 插件，GUI 入口为 `[Modules > Importers > SDF importer...]`。

### simSDF.import

```python
simSDF.import(fileName, options={})
```

`ImportOptions` 字段（Lua 中以 map 传入）：

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `ignoreMissingValues` | bool | false | 忽略缺失值 |
| `hideCollisionLinks` | bool | true | 隐藏碰撞链接 |
| `hideJoints` | bool | true | 隐藏关节 |
| `convexDecompose` | bool | true | 对非凸体凸分解 |
| `showConvexDecompositionDlg` | bool | false | 显示凸分解对话框 |
| `createVisualIfNone` | bool | true | 无视觉时创建 |
| `centerModel` | bool | true | 居中模型 |
| `prepareModel` | bool | true | 整理为模型 |
| `noSelfCollision` | bool | true | 禁用自碰撞 |
| `positionCtrl` | bool | true | 关节位置控制 |

### simSDF.dump

```python
simSDF.dump(fileName)  # 打印 SDF 文件结构，调试用
```

## glTF 导出与导入（simGLTF）

glTF 是 Web 3D 的事实标准。CoppeliaSim 可将场景或仿真动画导出为静态 `.gltf`(ASCII) 或 `.glb`(二进制)，也可加载外部 glTF 模型。GUI 入口为 `[Modules > Exporters > GLTF exporter...]` 与 `[Modules > Exporters > GLTF animation exporter...]`。

### 常用函数

```python
# 加载
result, warnings, errors = simGLTF.loadBinary(filepath)   # .glb
result, warnings, errors = simGLTF.loadASCII(filepath)    # .gltf

# 导出对象
simGLTF.exportAllObjects()                      # 全部对象
simGLTF.exportSelectedObjects()                 # 选中对象
simGLTF.exportObjects(objectHandles)            # 指定句柄列表
nodeIndex = simGLTF.exportObject(objectHandle)  # 单个对象
nodeIndex = simGLTF.exportShape(shapeHandle, parentHandle=-1, parentNodeIndex=0)

# 录制动画（仿真中逐帧记录所有对象位姿）
simGLTF.recordAnimation(True)
# 仿真结束后
simGLTF.exportAnimation()
simGLTF.saveBinary(filepath)  # 或 saveASCII(filepath)

# 内存清理
simGLTF.clear()
count = simGLTF.animationFrameCount()  # 动画帧数
json = simGLTF.serialize()              # 序列化为 JSON 字符串

# 纹理格式
simGLTF.setExportTextureFormat(simGLTF.TextureFormat.png)  # bmp/png/jpg
```

## 通用网格导入导出（Assimp 插件 / simAssimp）

Assimp（Open Asset Import Library）插件支持 OBJ、STL、FBX、3DS、Collada(.dae) 等数十种 CAD/网格格式。GUI 入口：`[File > Import > Meshes...]` 与 `[File > Export > Selected shapes...]`。

> 导入后若场景层次显示有新形状但视口看不到，多半是尺寸过大/过小，需缩放；网格三角形过多（机器人建议总计 1 万–2 万）会拖慢仿真，可用 `[Modules > Geometry / Mesh > Mesh decimation...]` 简化。

### simAssimp.importShapes

```python
shapeHandles = simAssimp.importShapes(
    filenames,                  # 分号分隔的文件名（含扩展名）
    maxTextureSize=512,         # 纹理最大边长
    scaling=0.0,                # 0.0 表示自动缩放
    upVector=simassimp_upvect_auto,  # auto/z/y
    options=0                   # 1=丢弃纹理 2=忽略颜色 4=忽略透明度
                                 # 8=不优化网格 16=保留相同顶点
                                 # 32=每文件一个 shape 64=对齐朝向
                                 # 128=忽略文件格式内置 up 向量 256=静默
)
```

### simAssimp.exportShapes

```python
simAssimp.exportShapes(
    shapeHandles,               # 要导出的 shape 句柄数组
    filename,                   # 文件名（含扩展名）
    formatId,                   # 见 simAssimp.getExportFormat
    scaling=1.0,
    upVector=simassimp_upvect_z,
    options=0                   # 1=丢弃纹理 2=忽略颜色 4=丢弃法线
                                 # 8=仅导出可见 256=静默
                                 # 512=坐标相对第一个 shape 的帧
)
```

### 格式枚举

```python
# 遍历支持的导入/导出格式
i = 0
while True:
    desc, ext = simAssimp.getImportFormat(i)
    if not desc: break
    print(f"import: {desc} (*.{ext})")
    i += 1

i = 0
while True:
    desc, ext, fid = simAssimp.getExportFormat(i)
    if not desc: break
    print(f"export: {desc} (*.{ext})  id={fid}")
    i += 1
```

`simAssimp.upVector` 枚举：`auto`（自动）、`z`（沿 Z）、`y`（沿 Y）。

## 综合示例：Python 端导入 URDF 机器人

通过 ZMQ Remote API 调用 simURDF 插件导入一个 URDF 机器人模型：

```python
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

client = RemoteAPIClient()
sim = client.require('sim')
simURDF = client.require('simURDF')

# 导入 URDF：显示碰撞 + 关节，不封装为模型
robotName = simURDF.import(
    r'C:/robots/my_robot/urdf/my_robot.urdf',
    options=0b0011,  # bit0=show collision links, bit1=show joints
    replacementString=r'C:/robots/my_robot'  # 替换 package://
)
print('Imported robot:', robotName)
```

## 各格式适用场景

- **机器人模型导入**：优先 URDF（ROS 生态）或 SDF（Gazebo 生态），两者自动生成关节、碰撞体、视觉体，无需手工拼装。
- **通用 CAD 网格导入**：OBJ/STL/FBX/Collada 等用 Assimp 插件 `simAssimp.importShapes`；只关心顶点/索引时用 `simAssimp.importMeshes`。
- **场景/模型版本控制**：用 CoppeliaSim XML（simple 格式可手工 diff，exhaustive 格式无损）。
- **Web 可视化或外部 3D 工具链**：导出 glTF（静态 `.glb` 或带动画的 `.gltf`）。
- **仿真动画录制**：`simGLTF.recordAnimation(True)` 在仿真中逐帧记录，结束后导出 glTF 动画。

## 参见

- [[wiki/00-基础知识/00-04-场景与模型管理]] — 场景(.ttt)/模型(.ttm) 的加载、保存与版本管理
- [[wiki/01-API参考/01-04-插件API总览]] — 各插件 API 命名空间一览
- [[wiki/02-仿真建模/02-02-形状与几何体]] — 形状类型（primitive/mesh/heightfield/compound）与几何操作
- [[wiki/06-工业案例/06-01-UR5-抓取与放置案例]] — UR5 机器人模型导入与控制实战
