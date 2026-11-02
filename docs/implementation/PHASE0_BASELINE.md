# 阶段零：实施准备与参数冻结

**实施分支**: `codex/flexible-workshop-phase1`（自 `main`@`2494d2f` 创建）
**执行日期**: 2026-10-17
**规范来源**: [06-05-柔性工业车间分阶段执行步骤](../wiki/06-工业案例/柔性车间/历史与待评估规划/06-05-柔性工业车间分阶段执行步骤.md) §1

## 1. 基线与变更边界

### 1.1 仓库状态

```text
main @ 2494d2f   （旧版 src 控制层已删除）
  └─ codex/flexible-workshop-phase1
       ├─ c4258f2  恢复基线并建立阶段一模块骨架
       ├─ 8eadffa  DeviceHandleMap 与 UR5 IK 离线测试
       ├─ 94a9045  单工位闭环控制循环
       ├─ db79878  阶段 0 探测脚本修正
       └─ 32ca4c7  （本会话起点）删除 src/、阶段一测试与测量脚本
```

`32ca4c7` 把阶段一实现从工作树中删除，只剩文档与 `tools/conpty_relay.py`。
本会话从 `db79878` 恢复了 `src/`、阶段一测试、`docs/implementation/` 和
`docs/measurement/`，**仅作为参考**；阶段一控制层与场景构建器按下述方式重写。

### 1.2 参考与重写范围

| 内容 | 处理方式 |
|---|---|
| `src/reference/build_scene.py`（旧五岛完整场景） | **不恢复使用**；阶段一新建 `src/stage1/scene.py`，只构建单工位 |
| `src/reference/factory_controller.py` / `factory_agv.py` / `factory_island.py` 等旧层 | 保留原文件（MCP 服务器仍依赖），不作为阶段一实现 |
| `src/stage1/` 包内的 conveyor_controller / material_manager / ur5_ik_controller / suction_controller / pioneer_controller | 按 06-05 §2 重写 |
| `docs/measurement/phase0_probe.py` 等旧脚本 | 删除；新建 `measure_phase0.py` 与 `ik_probe.py` |
| `docs/wiki/06-工业案例/06-04/06-05` | 规范来源，不覆盖 |

### 1.3 不覆盖清单

- 用户场景文件 `3arm_simulation_test.ttt`、`factory-sim.ttt`（磁盘文件，不被脚本修改）
- `docs/wiki/06-工业案例/柔性车间/历史与待评估规划/06-04-柔性工业车间重构方案.md`
- `docs/wiki/06-工业案例/柔性车间/历史与待评估规划/06-05-柔性工业车间分阶段执行步骤.md`（仅追加实施状态）
- `docs/raw/`（官方手册，永不修改）
- 运行日志 `coppelia_console*.log`、`stage1_*.log`（不入库）

## 2. 环境验证（§1.2）

| 检查项 | 结果 |
|---|---|
| CoppeliaSim 版本 | **4.10.0.0**（`getStringProperty(handle_app, 'productVersion')`） |
| 内置模型目录 | `C:\Program Files\CoppeliaRobotics\CoppeliaSimEdu\models` 可访问 |
| `generic conveyor (belt).ttm` | 存在 |
| `UR5.ttm` | 存在 |
| `suction pad.ttm` | 存在 |
| `pioneer p3dx.ttm` | 存在 |
| ZMQ Remote API | `localhost:23000` 连接成功 |
| stepping 20 步 | simTime `0.0 → 1.0 s`，`advanced=True` |
| `coppeliaSimVersion` 信号 | 未使用；版本走 `productVersion`，探活走 `getSimulationState()` |

启动方式（避免 Python 沙箱 `SRE module mismatch`）：

```bash
.venv/Scripts/python.exe tools/conpty_relay.py \
  --log coppelia_console.log \
  --cwd "C:/Program Files/CoppeliaRobotics/CoppeliaSimEdu" \
  -- "C:/Program Files/CoppeliaRobotics/CoppeliaSimEdu/coppeliaSim.exe" \
  -vscriptinfos -GzmqRemoteApi.rpcPort=23000
```

## 3. 参数与坐标系冻结（§1.3）

- 世界坐标：X = 主输送方向，Y = 横向，Z = 高度；单位 m / rad / s / kg。
- 地面碰撞面 `z = 0.0`（primitive floor，static + respondable）。
- 传送带带面 `z = 0.45`；取料点世界坐标 `(-0.20, 0.0, 0.45)`。
- UR5 基座 `(0.42, 0.0, 0.30)`；工位台面 `z = 0.30`；AGV 载台顶面 `z = 0.2570`。

### 3.1 内置模型实测（摘要）

| 模型 | 关键实测值 |
|---|---|
| conveyor | native 包围盒 `1.216 × 0.200 × 0.233 m`；上表面 pad 行程 `[-0.49723, 0.50000]`；带面相对根 `0.0`；50 个 pad；1 个 customization Script（type 6） |
| UR5 | 6 个 revolute 关节；`connection` 相对根 `[-0.18495, 0, 0.98655]`，四元数 `[0, -0.70711, 0, 0.70711]`；关节区间 `[-2π, +2π]` |
| suction pad | 包围盒 `0.0483 × 0.0483 × 0.0500 m`；接触面 `local z = -0.015`；`Sensor` `local z = -0.013`；`Link` `local z = -0.015` |
| pioneer p3dx | 包围盒 `0.519 × 0.415 × 0.237 m`；轮直径 `0.195 m` → 半径 **0.0975 m**；左右轮中心距 **0.3310 m**；车体质量 **16.0 kg**；最低点 `local z = -0.13879` |

完整原始数据：[docs/measurement/phase0_measurements.json](../measurement/phase0_measurements.json)。
测量脚本：[docs/measurement/measure_phase0.py](../measurement/measure_phase0.py)。

### 3.2 驱动与接口实测

- conveyor：保留内置脚本，通过 `customData.__ctrl__`（`sim.packTable({"vel": v})`）调速；
  `__state__` 可读回 `{vel, pos}`。默认 `vel≈0.0775`，启动后由控制器显式覆盖。
  **模型 `customData.__config__.accel` 实测为 0.05 m/s²**，`vel=0` 后带面
  要滑行数秒；阶段一在构建期写入 `accel=2.0`，0.2 m/s 下 0.10 s/2 步停住
  （见阶段一报告与 `run_stage1` 的 `conveyor_drive_probe`）。
- UR5 simIK：`addElementFromScene(env, group, base, tip, target, constraint)` 返回
  `(ikElement, simToIkMap, ikToSimMap)`；`handleGroup` 返回 `(result, reason, [lin, ang])`，
  `result_success = 1`。`setJointTargetPosition` 的目标必须在 `startSimulation()` 之后下发。
- p3dx：驱动关节保持 **dynamic + velocity** 控制；左右轮 `+2.0 rad/s` → 车体
  `+X` 前进约 `0.195 m/s`。改成 kinematic 会导致车体不动。
- 4.10 实测：pad 不会把切向速度传给 dynamic 物料，因此阶段一物料输送采用
  `HYBRID_DYNAMIC` 受限补偿（见阶段一报告）。

## 4. 阶段零产物

```text
docs/implementation/PHASE0_BASELINE.md       本文件
docs/measurement/measure_phase0.py           可重复测量脚本
docs/measurement/phase0_measurements.json    实测数据
docs/measurement/ik_probe.py                 simIK 控制链实验记录
src/stage1/config.py                         冻结后的阶段一参数
```

通过条件：连接、启动、暂停、单步、停止均成功；`coppeliaSimVersion=None`
不会误判断线；所有后续场景和测试引用同一套坐标、单位和测量值。

## 5. 交付状态（2026-10-17）

阶段零与阶段一的代码、测试和证据当前位于工作区，**尚未提交到 git**
（用户未要求提交）。`git status` 会显示 `?? src/`、`?? docs/implementation/`、
`?? tests/test_*.py`，这是 HEAD `32ca4c7` 删除 `src/` 后的正常状态。
提交时应按项目约定拆成两个 scoped commit：代码（`src/`、`tests/`、`tools/`）
在前，文档（`docs/`、`CLAUDE.md`、`README.md`）在后；不要用 `git add -A`。

阶段一模块位于 `src/stage1/` 包内，`src/run_stage1.py` 是兼容入口薄包装
（安装后也可 `uv run run-stage1`）。阶段一的 conveyor 脚本在最终实现中**保留**（`stage1.scene._load_model(...,
strip_scripts=False)`），并由每次运行开始的驱动探测确认 pad 确实移动；
UR5 / suction pad / p3dx 的脚本仍然剥离。
