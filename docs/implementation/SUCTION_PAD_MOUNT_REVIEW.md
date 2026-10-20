# suction pad 安装方向与修改方案

日期：2026-10-20。状态：步骤 1–9 已执行；`L1_S1` 全姿态 IK、station/AGV 正常闭环、
连续 6 次运行和现有故障注入通过。完整运动路径的网格净距/碰撞检查仍未执行，
因此新安装尚不能声明为路径无碰撞。未修改 `.ttt`（另存副本见证据目录说明）。

## 判断与证据

按当前 `stage1` 构建代码及配套场景快照，吸盘实际接触面朝上，TCP 的朝向没有表达真实吸盘朝向。应同时修正安装变换与 TCP 局部姿态，不能仅翻转吸盘。

- `src/stage1/scene.py:681`：吸盘原点与 UR5 `connection` 重合，相对四元数为单位四元数。
- `src/stage1/scene.py:703` 起：新建 TCP dummy，`setObjectParent(tcp, pad, True)` 保留世界姿态，随后仅设置局部位置；没有显式设置局部四元数。
- `docs/measurement/phase0_measurements.json`：吸盘包围盒局部 Z 范围为约 `[-0.015, +0.035] m`；内置 Sensor 在 `z=-0.013 m`，其探测 +Z 轴指向吸盘根坐标的 -Z；Link/闭环 dummy 在 `z=-0.015 m`。这些信息共同支持吸附侧在局部 -Z。
- `phase1_scene_snapshot_20261020_163555.json`：pad 世界位置 `(-0.3100, 0.0631, 0.7247)`，TCP 为 `(-0.3100, 0.0631, 0.7397)`。TCP 局部 Z 为 -0.015 m，却位于 pad 上方 0.015 m，因此 pad 的 -Z 接触方向朝世界 +Z。
- TCP 创建时保留世界单位姿态，目标姿态也为单位四元数。IK 保持的是这个 TCP 朝向，实际吸附面仍可朝上。专用传感器相对 TCP 转 X 轴 π，向下探测，因此与真正吸附面相反。

现有 `_tool_tilt()` 只检查 TCP 的 +Z；`reached()` 只检查 TCP 位姿；场景契约只检查 TCP 局部 Z。它们都未检验 TCP 与真实吸附方向是否一致。

以历史初始姿态估算，吸盘总体包围范围位于 TCP 下方约 0–50 mm；若 TCP 放在物料顶面，工具包围范围进入物料所在空间。这是轴向包围范围推算，不是网格交叠深度实测。UR5/吸盘 shape 的 `respondable=0`，加上逻辑吸附，因此任务 DONE 不能证明几何安装正确或没有穿模。

实时读取被自动审批服务错误阻断（503，审批未完成；并非判定操作不安全）。本结论不声称已读取用户此刻打开的场景；若场景经过手工编辑，需按下述步骤重新核对。

## 坐标与安装距离

定义 F 为 UR5 connection 坐标，P 为吸盘模型根，T 为 TCP。先测量确认 F 的 +Z 是法兰外向轴；以下公式以此为前提，不能直接套用世界 Z 平移。

吸盘绕 F 的 X 轴转 π，四元数使用 CoppeliaSim 的 `[qx,qy,qz,qw]` 顺序：`[1,0,0,0]`。此时吸附侧 -Z_P 与 +Z_F 同向。

只旋转不平移时，吸盘 Z 包围范围由 `[-15,+35] mm` 变为 `[-35,+15] mm`，背部会伸入 connection 平面内侧 35 mm。必须补偿平移。

设：

- `b = 0.03500004 m`，吸盘背部在 P 中的最大 Z；
- `a = -0.01500005 m`，接触面在 P 中的 Z；
- `e` 为末端 link 在工具安装区域内，超过 F 原点平面的外向突出量；须由真实 shape 测量；
- `g` 为预留间距，例如先用 5 mm 作为待验证设计值。

安装原点沿 +Z_F 的偏移应满足 `d >= b + e + g`。翻转后吸盘轴向范围为 `[d-b, d-a]`，TCP 在 F 中的 Z 为 `d-a`。

下表仅以 `e=0` 的法兰平面估算，不能称为已经测得的 link 表面间距：

| 法兰平面间距 g | pad 原点偏移 d | pad 相对法兰轴向范围 | 法兰到 TCP |
|---|---|---|---|
| 0 mm | 35 mm | 0–50 mm | 50 mm |
| 5 mm | 40 mm | 5–55 mm | 55 mm |
| 10 mm | 45 mm | 10–60 mm | 60 mm |

推荐从 `e + 40 mm` 的原点偏移开始验证；如果需要实际连接，应建立与相应间距匹配的转接座，避免吸盘悬空。转接座与法兰/吸盘的设计接触需要与非预期碰撞区别处理。

末端 link 与工具刚性连接，二者相对距离在关节运动中不变；工具与其他 wrist/forearm link、物料和环境的距离随姿态变化，必须检查完整运动过程。不能用两个对象原点的距离作为表面净距，也不能把法兰平面估算替代网格距离。

## 建议代码修改（待实施）

集中配置安装四元数、模型背部偏移、末端突出量、转接座长度/间距；从模型测量或校准数据获取尺寸，避免在控制循环内写硬编码。

在 `_create_robot()` 中先完成工具安装，再完整指定 TCP 相对 pad 的位姿：

```python
# 示意；d 由上述模型尺寸和 link 净距测量确定。
sim.setObjectPose(pad, [0, 0, d, 1, 0, 0, 0], connection)
sim.setObjectParent(tcp, pad, True)
sim.setObjectPose(tcp, [0, 0, cfg.TCP_LOCAL_Z, 0, 0, 0, 1], pad)
```

`TCP_LOCAL_Z` 仍取接触面 `-0.015 m`，无需因安装翻转而改正负号。TCP 的 +Z 与 pad 的 +Z 对齐，所以 TCP 目标单位四元数仍表示接触面朝世界下方。专用 suction sensor 继续相对 TCP 绕 X 轴转 π，它与模型原生 Sensor 的探测轴将保持同向。

增加构建期和运行期几何断言：

1. `R_world_pad @ (0,0,-1)` 在取放姿态指向下方，不能仅检查 TCP 四元数。
2. TCP 与 pad 的局部旋转明确且正确；TCP 接触点与内置 Link/接触标记的位置一致。
3. 专用传感器 +Z 与真实接触法向同向，排除传感器朝下而吸盘朝上的状态。
4. 测量工具与末端 link 的 mesh 距离，并检查非连接部位无交叠；其余连杆、携带物料和环境需要沿路径检查。

这里的 UR5 末端 link 与吸盘内置名为 `Link` 的 force sensor 是不同对象。当前 `SuctionController.link_handle` 仅保存句柄，实际物料 parent 到 TCP；此次方案继续使用现有逻辑吸附，不通过修改内置 Link 或启用内置自动吸附脚本补救方向。

## 其他操作的影响与验证

| 操作 | 影响与处理 |
|---|---|
| IK 与关节目标 | 工具变换改变，必须在修改后重新创建主/备用 IK environment，不能复用旧映射或旧关节解；全姿态约束重新求解。 |
| 抓取、抬升、工位放置、AGV 放置 | 真实 TCP 世界目标仍为物料顶面；工具偏移由 IK 链表达。重新验证可达性、限位、关节速度、TCP 误差、姿态与超时。不能统一给物料目标 Z 加 55 mm。 |
| 接近/撤离路径 | flange、wrist 与其他连杆的姿态和高度会改变。原 150 mm 接近高度不能视为自动足够；携带物料和转接座都应计入路径包络。 |
| 吸附与 UID | parent 到 TCP、指定 UID、速度门和物理属性快照流程可保留；重新验证传感器命中、距离和真实法向门。不要靠放宽 30 mm 接触容差掩盖安装错误。 |
| 释放 | 保留真实物料世界目标和物理属性恢复；验证释放前物料已在支承面上方，记录释放前后位姿，防止 release() 的目标位姿写入掩盖抓取偏移。 |
| 传送带停带与物料稳定 | 工具修改不直接改变带速或停带算法；需联调确认手臂接近不会侵入带面、传感器和物料。 |
| AGV 停靠与行驶 | 轮径、轮距、驱动参数无需因工具翻转修改；装载过程的 wrist/载台/挡边间距、落料稳定性及带载运输需回归。 |
| 场景与历史证据 | 修改构建代码不会自动更新已有 `.ttt`。另存新场景、重新标定并生成证据；保留旧文件和旧报告，注明工具变换已变化，旧验收不覆盖新配置。 |

建议验收顺序：

1. 保存原场景副本；读取 connection、末端实体 link、pad、Body、TCP、两个 suction sensor 的相对矩阵和 shape 网格。
2. 在独立场景确认法兰外向轴，测出 e，应用安装变换、明确 TCP 局部姿态，再测工具与末端 link 的真实净距。
3. 运行步骤 1.1 的 31 个离散全姿态目标及边界余量扫描；原 L1_S1 布局的通过结果需要重新验证。
4. 检查种子姿态到接近点、下探、吸附、抬升、转运、放置、撤离的完整路径；离散 IK 点成功不能证明路径无碰撞。
5. 执行 station/AGV 正常闭环、连续运行及相关故障注入；确认无错误 UID、无虚假朝向通过、无未记录的属性变更，释放后载荷稳定。

阶段一目前没有完备的路径碰撞验收。正式声明新安装可用之前，应补充所需的几何距离/碰撞检测证据；不能把现有 `respondable=0` 下流程成功作为无碰撞证明。

## 本次完成的验证

使用本地 Python 读取已提交模型测量和场景快照，复算接触方向、包围范围、翻转平移公式及 0/5/10 mm 三组安装尺寸。结果见 `evidence/suction_pad_mount_analysis_20261020.json`。

2026-10-20 实时场景补充测量（仿真停止状态）：末端 `link7_visible` 相对 connection 的最大 Z 为 **0.007549934 m**（shape 包围盒，保守值，非网格净距），`link` 为 0.000050 m；据此取 `SUCTION_FLANGE_PROTRUSION_Z = 0.007549934089183821`，`SUCTION_MOUNT_Z = 0.04754997428506614 m`。

步骤 3–6 已实施：`config.py` 安装参数与工具版本；`scene.py` 安装 Rx(π)、TCP 完整局部位姿、契约断言与工具几何快照；`suction_controller.py` 真实吸附法向与 TCP/pad/sensor 轴向校验；`run.py` / `stage1_1/run_step1_1.py` 装配与证据字段。

## 实施后测试结果（2026-10-20）

测试在 `codex/step1.1-layout-pickup` 分支、基线提交 `86cf04e` + 本次未提交修改上执行。原始输出在 `runs/suction_mount_v2/`，被引用的证据已复制到 `evidence/`。

| 项目 | 结果 | 证据 |
|---|---|---|
| 几何/吸附/IK/场景契约单元测试 | 89 passed（含新增 `test_stage1_tcp_geometry.py` 11 项） | `pytest` 输出 |
| 全量测试（排除旧版 MCP 连接状态测试） | 177 passed, 3 skipped | `pytest` 输出 |
| `L1_S1` 全姿态 IK 扫描 | 31 个离散目标；15/15 运动验证目标成功，`all_ok=true`；边界余量 `+x=0.59, -x=0.40, +y=0.40, -y=0.27 m` | `step1_1_layout_20261020_221308.json` |
| station 正常闭环 | DONE，13.6 s；物料位置误差 0.0 m；dynamic+respondable 恢复 | `phase1_run_station_none_20261020_221600.json` |
| station + AGV 正常闭环 | 两项 DONE；AGV 载荷跟随误差 0.0069 m，`on_tray=true` | `phase1_run_both_none_20261020_221642.json` |
| station 连续 6 次 | 6/6 DONE | `phase1_run_station_none_20261020_221725.json` |
| 步骤 1.1 B 组无挡板取料 | build_ok；6/6 success，gate_ok=6，pad 停止模式均为 `velocity+position` | `step1_1_experiments_20261020_222346.json`、`step1_1_trials_B_20261020_222346.jsonl` |
| `no_target` | FAILED: `SENSOR_TIMEOUT` | `phase1_run_station_no_target_20261020_221841.json` |
| `uid_mismatch` | FAILED: `SENSOR_TIMEOUT`（错误物料占位，目标未到达） | `phase1_run_station_uid_mismatch_20261020_221913.json` |
| `ik_unreachable` | FAILED: `IK_FAILED` | `phase1_run_station_ik_unreachable_20261020_221944.json` |
| `sensor_disabled` | FAILED: `NO_SENSOR_TRIGGER_AT_STOP` | `phase1_run_station_sensor_disabled_20261020_222009.json` |
| `release_unstable` | FAILED: `RELEASE_UNSTABLE` | `phase1_run_station_release_unstable_20261020_222035.json` |
| `agv_blocked` | AGV `BLOCKED`，行驶 0.195 m 后停车，载荷仍在载台 | `phase1_run_agv_agv_blocked_20261020_222105.json` |
| 真实场景工具反向 | pad `-Z` 法向世界 `+Z`，倾角 π → 拒绝 | `tool_fault_injection_20261020_222250.json` |
| 真实场景净距不足 | 安装 d 压到 0.037 m，实测 gap = -0.00555 m < 0.005 m → 拒绝 | 同上 |

正常闭环中记录到：真实吸附法向 `≈(0,0,-1)`，专用传感器与内置 ModelSensor 轴向同向，末端 link 净距 `0.005000 m`，物料释放后 `static=0, respondable=1`。

未执行：完整运动路径的离散网格净距/碰撞扫描、吸盘/转接座与腕部 link 的连续路径检查。因此本次结果证明几何安装、离散 IK、正常闭环和故障拒绝有效，不证明路径无碰撞。

旧版 `test_mcp_control_tools.py` / `test_mcp_diagnose_tools.py` / `test_mcp_read_tools.py` 在全量运行中有 8 项失败（`未连接 CoppeliaSim`、ZMQ `Operation cannot be accomplished in current state`），与本修改无关；单独运行仍复现，属于这些测试自身的连接状态隔离问题。

## 逐步实施清单（执行记录）

以下为原计划步骤。步骤 1–9 已按此执行；步骤 10 中“完整路径网格净距/碰撞检查”仍未完成，
其余正常/故障回归已执行并记录在上文。

### 1. 保存原场景和基线

先在 CoppeliaSim 停止仿真，将当前场景另存副本；以下 CLI 会调用 `build_scene(clear=True)` 清场重建，不能直接用于含有未保存手工改动的场景。开发使用独立 `codex/*` 分支，保留用户现有改动。

记录 connection、其上游末端实体 shape、pad、Body、TCP、ModelSensor、专用 Sensor 的句柄、父对象和完整相对矩阵。把当前关节角、pad/TCP 世界四元数和模型网格一并归档。特别注意 force sensor `connection` 本身不能替代末端实体 shape。

### 2. 标定安装轴及 e

在 connection 坐标系显示坐标轴，检查末端实体 link 位于哪侧。只有 +Z 确为外向轴时才采用本文的 Rx(π) 方案；若模型不同，应先定义正确的法兰安装坐标变换。

将末端实体 link 的网格顶点变换到 connection 坐标，检查与吸盘/转接座横向投影重叠部分的 Z 范围，取得外向突出量 e。不要用末端整体模型包围盒，里面可能包含已经挂载的吸盘，造成自引用；也不要把 force sensor 的显示尺寸当法兰厚度。使用包围盒可做保守粗筛，最终由真实 shape 对距离/碰撞检查确认。

### 3. 修改 stage1/config.py

在 TCP 配置附近新增集中参数，示意如下。`SUCTION_FLANGE_PROTRUSION_Z` 必须赋为步骤 2 的校准值，不能把未知值默认为已测得的零：

```python
SUCTION_MOUNT_QUATERNION = (1.0, 0.0, 0.0, 0.0)
SUCTION_BACK_LOCAL_Z = 0.03500004019588232
SUCTION_LINK_CLEARANCE = 0.005  # 初始设计值，验收前冻结
# SUCTION_FLANGE_PROTRUSION_Z = 校准所得的 e，单位 m
SUCTION_MOUNT_Z = (
    SUCTION_BACK_LOCAL_Z
    + SUCTION_FLANGE_PROTRUSION_Z
    + SUCTION_LINK_CLEARANCE
)
```

保留 `TCP_LOCAL_Z` 和 `TOOL_TARGET_QUATERNION` 的现有数值，更新注释说明 TCP +Z 与 pad +Z 同向、吸附方向为 -Z。为校准记录增加工具版本，日志同时写出版本、安装变换和尺寸来源。

### 4. 修改 stage1/scene.py 的 _create_robot()

替换现有的单位安装变换，并将 TCP 的“只设置位置”替换为完整局部 pose：

```python
sim.setObjectPose(
    pad,
    [0.0, 0.0, cfg.SUCTION_MOUNT_Z,
     *cfg.SUCTION_MOUNT_QUATERNION],
    connection,
)

# tcp 创建及设置 alias 后：
sim.setObjectParent(tcp, pad, True)
sim.setObjectPose(
    tcp,
    [0.0, 0.0, cfg.TCP_LOCAL_Z, 0.0, 0.0, 0.0, 1.0],
    pad,
)
```

安装变换设置在 TCP 创建前完成。保留专用 sensor 相对 TCP 的 `(π,0,0)` 旋转；它的 +Z 应与 pad 的 -Z 同向。保留内置吸附脚本剥离逻辑。需要转接座时，给它独立 alias、句柄和明确的几何/物理属性，并纳入工具几何集合；设计连接面接触单独标记。

### 5. 修改场景契约与证据字段

在 `Stage1Scene.verify()` 中，在现有 `tcp_contact_offset` 基础上增加：

- TCP 相对 pad 的 XYZ 全分量及局部旋转校验，不能仅比较 Z。
- pad 相对 connection 的安装位姿校验。
- `z_tcp · z_pad ≈ 1`；`z_sensor · (-z_pad) ≈ 1`，内置/专用 sensor 都检查。
- pad 与 TCP 的接触点一致性，必要时与模型内置 Link/闭环 dummy 交叉核对。
- 非预期工具/link 交叠为失败；未知几何检查结果不能按通过处理。

这里的坐标轴均由对象矩阵读取。世界朝下检查只应在指定取放姿态验证，不能要求任意 HOME 或中间姿态始终竖直向下。

扩展场景快照和 `collect_evidence()`：保留原有位置字段，新增 world pose、parent-relative pose、真实吸附法向、传感器探测轴、工具标定版本及最小净距。每项距离同时记录两个对象/集合、关节角和测量时刻。

### 6. 修改 suction_controller.py 与组件装配

为 `SuctionController` 增加真实吸盘根句柄 `pad_handle`。在 `stage1/run.py` 的 `_build_components()` 中传入 `scene.suction_root`；搜索其他构造调用并同步更新，包括测试 fake。

真实法向 `n = -R_world_pad[:,2]`，向下参考为 `down=(0,0,-1)`。`_tool_tilt()` 改为 `acos(clamp(n·down,-1,1))`；这样 TCP 被手工转正也不能掩盖吸盘反向。吸附前再检查 TCP/pad、sensor/pad 相对轴关系，失败返回明确的几何错误，且不执行换父或物理属性修改。

保留 UID/状态/传感器命中/接触距离/速度检查顺序的可追踪性，沿用既有倾角和速度门限。新增几何误配错误时，同步处理状态机中的诊断日志；不以无限重试掩盖静态工具装配错误。

### 7. 重建 IK，先检查静态目标

先完成上述几何修改，再创建 `Ur5IkController`；调试中若重新挂载工具，要销毁旧 IK environment，重新创建主组、备用组和映射，更新目标并重新同步求解。不能只调用已有实例的 `initialize()`，因为其 `initialized=True` 时会直接返回。

运行下列命令前先完成步骤 1 的场景备份。它扫描所有候选布局；必须在 JSON 中确认 L1_S1 自身的全部 pose/限位/边界余量，而不只看其他布局是否被选中，也不只看进程返回码：

```powershell
.venv/Scripts/python.exe src/run_step1_1.py --phase layout --out-dir docs/implementation/runs/suction_mount_v2
```

名义取料 TCP 高度为 0.55 m。若 e=0、g=5 mm，修正后 flange 高度约为 `0.55+0.055=0.605 m`；旧装配在 TCP 竖直单位目标时 flange 约为 `0.55-0.015=0.535 m`，二者相差约 70 mm，且 flange 朝向翻转。因此旧 wrist 轨迹不能复用。以上为理想目标的几何计算，不是实测关节轨迹。

### 8. 检查完整运动与释放前位姿

按 HOME→接近→下探→吸附→抬升→转运→放置→释放→撤离逐段检查，并覆盖不同 IK 分支、取料区边缘和相关重试运动。碰撞实体包括实际 link shape、pad/Body、转接座、携带物料、带体/带面、工位和 AGV 挡边；不能拿包含工具自身的整臂集合去做工具/机器人检查。

检查需要同时包含碰撞与净距。针对有厚度的网格发生包含或相交等情况，不能只依赖一个表面距离数值。若用离散采样，应记录采样分辨率及最大运动间隔，必要时细分；不能据此直接声明连续路径有保证。

释放前检查物料实际位置与目标支承面的关系，并验证 `T_tcp^-1 * T_material` 在搬运期间稳定。`release()` 当前会先写物料目标 pose，因此需要在这次写入之前记录并校验偏差，超限时拒绝释放或报告任务失败，避免最终落点看似正确却掩盖搬运偏移。涉及此行为变更时，增加对应单元测试。

### 9. 执行控制回归与故障测试

新增 `tests/test_stage1_tcp_geometry.py`，重点覆盖：非单位世界姿态下建 TCP、不同基座朝向、错误 TCP 局部四元数、反装 pad 但正确 TCP、sensor 反向、工具/link 交叠或距离不足，以及吸附拒绝前物理状态不变。几何计算测试要验证矩阵组合和物理轴关系，不能仅断言某个常量等于 0.04。

完成实现后运行相关单元测试，再在备份后的仿真场景中执行以下正常流程：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_stage1_tcp_geometry.py tests/test_suction_controller.py tests/test_ur5_ik_controller.py tests/test_stage1_unit_loop.py tests/test_scene_contract.py tests/test_stage1_config.py tests/test_stage1_layout.py tests/test_step1_1_layout_eval.py tests/test_step1_1_pickup.py -q
.venv/Scripts/python.exe src/run_stage1.py --target both --fault none --time 90 --out-dir docs/implementation/runs/suction_mount_v2
.venv/Scripts/python.exe src/run_stage1.py --target station --repeat 6 --time 90 --out-dir docs/implementation/runs/suction_mount_v2
```

再补 AGV 连续装载/运输、传感器禁用、UID 不匹配、IK 不可达、释放不稳定等现有故障注入，并增加真实工具反向和安装净距不足故障。故障运行预期为明确拒绝或进入对应故障态，不把所有返回码为零作为通过标准。命令中的新增测试文件需先按本步骤实现。

### 10. 固化新场景和验收结果

只有几何检查、全姿态 IK、路径检查和正常/故障回归均完成后，才另存新版场景并同步阶段报告、配置说明与相关模块文档。新证据注明工具版本，保留旧证据的历史身份。

验收记录至少给出：真实吸附法向、TCP/真实接触点误差、传感器轴一致性、末端 link 净距、路径最小净距及对应姿态、31 个目标成功数、释放前后物料偏差、UID 生命周期、属性恢复和 AGV 载荷稳定性。尚未完成的检查逐项写明，不用 DONE 状态替代这些数据。
