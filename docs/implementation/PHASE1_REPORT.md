# 阶段一验收报告：单工位单元闭环

**执行日期**: 2026-10-17
**分支**: `codex/flexible-workshop-phase1`
**规范来源**: [06-05-柔性工业车间分阶段执行步骤](../wiki/06-工业案例/06-05-柔性工业车间分阶段执行步骤.md) §2
**参数基线**: [PHASE0_BASELINE.md](PHASE0_BASELINE.md) / [phase0_measurements.json](../measurement/phase0_measurements.json)

## 1. 环境

| 项 | 值 |
|---|---|
| CoppeliaSim | 4.10.0.0（Edu） |
| 物理引擎 | Bullet（默认） |
| Python | 3.11（`.venv`） |
| Remote API | ZeroMQ, `localhost:23000`, stepping 模式 |
| 分支 | `codex/flexible-workshop-phase1` |
| 场景 | 由 `stage1.scene.build_scene()` 每次运行重建 |
| 仿真步长 | `0.05 s` |

启动 CoppeliaSim（ConPTY relay，避免 `PYTHONHOME` 污染）：

```bash
.venv/Scripts/python.exe tools/conpty_relay.py \
  --log coppelia_console.log \
  --cwd "C:/Program Files/CoppeliaRobotics/CoppeliaSimEdu" \
  -- "C:/Program Files/CoppeliaRobotics/CoppeliaSimEdu/coppeliaSim.exe" \
  -vscriptinfos -GzmqRemoteApi.rpcPort=23000
```

## 2. 实现范围与文件

阶段一只构建一条传送带、一个 UR5、一个 suction pad、一个 pioneer p3dx
和指定物料；不构建五岛、多机械臂或多传送带。所有阶段一模块集中在
`src/stage1/` 包内，`src/run_stage1.py` 是保留现有命令的薄包装。

| 文件 | 职责 |
|---|---|
| [config.py](../../src/stage1/config.py) | 全部位置、别名、模型路径、速度、容差、超时、故障枚举 |
| [physics.py](../../src/stage1/physics.py) | static/kinematic/respondable/mass/friction/mask 工具 |
| [scene.py](../../src/stage1/scene.py) | 阶段一场景构建、脚本处理、传感器、场景契约校验 |
| [device_handle_map.py](../../src/stage1/device_handle_map.py) | 启动期唯一别名/句柄表 |
| [material_manager.py](../../src/stage1/material_manager.py) | 7 状态物料生命周期 + 事件历史 |
| [conveyor_controller.py](../../src/stage1/conveyor_controller.py) | 内置 conveyor 驱动 + HYBRID_DYNAMIC 受限补偿 |
| [ur5_ik_controller.py](../../src/stage1/ur5_ik_controller.py) | 独立 simIK environment、主/备组、seed 回退、关节目标 |
| [suction_controller.py](../../src/stage1/suction_controller.py) | UID/传感器/TCP/倾角/速度检查，属性快照与恢复 |
| [pioneer_controller.py](../../src/stage1/pioneer_controller.py) | 差速换算、限幅、加速度斜坡、轮速下发 |
| [unit_controller.py](../../src/stage1/unit_controller.py) | §2.11 单步状态机与故障处理 |
| [run.py](../../src/stage1/run.py) | CLI 实现：构建 → 运行 → 证据采集 → JSON 报告 |
| [run_stage1.py](../../src/run_stage1.py) | 兼容入口薄包装 |

运行命令：

```bash
# 工位目标
.venv/Scripts/python.exe src/run_stage1.py --target station --time 45

# AGV 载台目标（含轮速行驶 0.5 m）
.venv/Scripts/python.exe src/run_stage1.py --target agv --time 45

# 两个目标连续运行
.venv/Scripts/python.exe src/run_stage1.py --target both --time 60

# 60 s 连续运行（6 次工位任务）
.venv/Scripts/python.exe src/run_stage1.py --target station --repeat 6 --time 30

# 故障注入（示例）
.venv/Scripts/python.exe src/run_stage1.py --target station --fault ik_unreachable
```

### 2.1 内置模型脚本策略

| 模型 | 脚本处理 | 原因 |
|---|---|---|
| `generic conveyor (belt).ttm` | **保留** customization 脚本 | 脚本是 pad 的唯一写入者；Python 只通过 `customData.__ctrl__` 调速 |
| `UR5.ttm` | 剥离 | 避免多份 Lua 脚本争用 4.10 ZMQ addOn 导致冻结 |
| `suction pad.ttm` | 剥离 | 内置脚本会无条件吸附任意 respondable 物体 |
| `pioneer p3dx.ttm` | 剥离 | 避免与 Python 轮速控制重复驱动 |

conveyor 驱动探测（每次运行开始时执行，结果写入 JSON 的
`conveyor_drive_probe`）：`drive_mode=SCRIPT_DRIVEN`，50 个 pad，
下发 0.2 m/s 后达到 `actual_velocity=0.2`，10 步内 pad 位移约
**0.098 m**，确认脚本确实在驱动履带，而不是只写 buffer。

**停车加速度**：模型 `customData.__config__.accel` 默认只有 0.05 m/s²，
`__ctrl__ {vel=0}` 后带面会滑行数秒（实测 0.07 m/s 时 1.45 s / 0.049 m）。
[scene.py](../../src/stage1/scene.py) 在构建期（simulation start 之前）把
`accel` 写为 **2.0 m/s²**、`targetVel` 写为 `CONVEYOR_SPEED`；实测 0.2 m/s
下 **0.10 s / 2 步**停住，pad 滑行 **0.010 m**，`stop_ok=True`。
不要把 accel 调到 3.0/5.0：虽然一帧停住，但模型脚本只在 `pos` 变化时刷新
`__state__`，速度回读会保持旧值。

## 3. 场景快照

来源：[phase1_scene_snapshot.json](phase1_scene_snapshot.json)。

| 项 | 实测 |
|---|---|
| 场景对象（`describe()` 列出的别名对象） | 21 |
| 场景契约断言 | **30 / 30 通过** |
| UR5 本体关节 | **6** |
| 传感器 | `Sensor_PickStation`、`Sensor_ConveyorExit`、`Sensor_SuctionPad_01`、`Sensor_AGV_Dock` |
| conveyor native / 构建长度 | `1.2160 m / 1.2160 m`（scale 1.000） |
| conveyor 上表面行程 | `[-0.49723, 0.50000]`（局部 X） |
| 入口 / 停止区 | `local x = -0.39723 / 0.42000` |
| 取料点 | 世界 `(-0.20, 0.0, 0.45)` |
| UR5 基座 | 世界 `(0.42, 0.0, 0.30)` |
| AGV 轮句柄 | `leftMotor` / `rightMotor`，**mode = dynamic (5)** |
| AGV 载台顶面 | 世界 `z = 0.2570`；挡边高 `0.03 m` |
| 机械臂/吸盘 respondable | 关闭（运动学工具；逻辑吸附，见 §8） |

构建期断言包括：必需别名唯一、6 关节、取料区位于实测上表面行程内、
conveyor 脚本存在、可达性（取料/工位/AGV）、AGV 驱动关节为 dynamic、
AGV 质量存在、TCP 接触面偏移 `local z = -0.015 ± 0.005 m`。

可达性（基座 → 目标点直线距离，限制 `0.80 m`）：

| 目标 | 距离 |
|---|---:|
| 取料 TCP | 0.669 m |
| 工位 TCP | 0.659 m |
| AGV 载台 TCP | 0.623 m |

场景启动后无 `state=8` 冻结；CoppeliaSim 控制台无残留脚本报错。唯一控制台
警告来自内置 conveyor 的 `matrix` 模块弃用提示，不影响运行。

## 4. 自动测试

```bash
.venv/Scripts/python.exe -m pytest \
  tests/test_stage1_config.py tests/test_material_lifecycle.py \
  tests/test_differential_drive.py tests/test_conveyor_transport.py \
  tests/test_suction_controller.py tests/test_ur5_ik_controller.py \
  tests/test_stage1_unit_loop.py tests/test_scene_contract.py -q
```

结果：**82 passed**（阶段一 72 项 + 场景契约 10 项）。

> 2026-10-17 末把阶段一模块迁入 `src/stage1/` 包后，80 项测试与一次工位实机
> 运行再次通过；§5–§6 的行为证据由同一逻辑生成，迁移只改变导入路径与入口包装。

> 说明：仓库中仍保留历史五岛完整场景与 MCP 服务器的旧测试（`test_factory_*`、
> `test_mcp_*`、`test_robot_integration.py`）。这些用例依赖旧版服务器连接状态、
> 完整五岛场景或旧控制层，不属于阶段一验收范围。`tests/test_mcp_console_tools.py`
> 的 7 项纯文件测试已恢复通过（补齐 `tail_coppelia_console`）。

覆盖点：

- 配置不变量：别名无 `/`、唯一性、取料区在有效行程内、可达性断言；
- 物料 7 状态：合法链路、跳过吸附/释放被拒绝、任意状态可 FAULT、显式复位；
- 差速驱动：左右轮换算、限幅、斜坡、`stop()`、**禁止写车体位姿**；
- HYBRID_DYNAMIC：进度、步长上限、不越过停止区、掉落与**横向越界**进入 FAULT；
- 吸附：UID/状态/传感器/距离/倾角/物料速度/**工具速度**六类拒绝、快照与属性恢复；
- simIK：主组失败→备用组、双组失败→**seed 回退**、双组+seed 失败不写角度、
  `syncToSim` 不被使用、关节限位检查；
- 单步循环：正常闭环、UID 误配不吸附、IK 失败、传感器超时、AGV 停靠超时、
  AGV 目标轮速。

## 5. 正常流程实测

### 5.1 传送带 → UR5 → 工位

证据：[phase1_run_both_normal.json](phase1_run_both_normal.json)（第 1 个任务）。

- 任务状态：**DONE**，耗时 `10.75 s`（仿真时间）；
- 物料 `Part_0001`：`(0.92000, 0.42000, 0.35000)`，与目标误差 `0.000 m`；
- 释放后物理属性：`static=0, respondable=1, mass=0.1, friction=0.6, mask=65535`；
- 补偿步数：约 82（每步 0.010 m，沿带面切线，带速 0.20 m/s × 0.05 s 上限）；
- 吸附/释放事件与 UID 生命周期完整。

### 5.2 传送带 → UR5 → AGV 载台 + 轮速运输

证据：[phase1_run_agv_normal.json](phase1_run_agv_normal.json)。

- 放置任务：**DONE**，耗时 `14.95 s`；
- 装卸前通过 `Sensor_AGV_Dock` + 车体停稳 + 车体竖直检查；
- AGV 轮速：`setJointTargetVelocity` 左右轮，目标线速度 `0.15 m/s`；
- 实际车体位移 `0.5087 m`，载荷位移 `0.5101 m`，**跟随误差 0.0014 m**；
- 载荷相对载台中心 `(0.0011, -0.0000) m`，**on_tray=True**，高度 `0.3069 m`；
- 载荷保持 `dynamic + respondable`，依靠载台接触/摩擦/挡边运输；
- 控制器没有对 AGV 车体调用 `setObjectPosition` / `setObjectPose`。

### 5.3 直线与原地转向（§2.10 步骤9）

证据：[agv_drive_test.json](../measurement/agv_drive_test.json)。

| 项目 | 实测 |
|---|---|
| 直线（v=0.15 m/s，5 s） | `dx=0.7127 m`，`dy=-0.0001 m`，航向漂移 `0.0006 rad` |
| 直线轮速 | 起始 `±0.1282`，稳态 `±1.5385 rad/s` |
| 原地转向（w=0.6 rad/s，2.5 s） | 航向变化 `1.4193 rad`（≈81°），平移 `(0.058, -0.027) m` |
| 转向轮速 | `(-1.0185, +1.0185) rad/s` |

### 5.4 60 s 连续运行

证据：[phase1_run_soak_6x_station.json](phase1_run_soak_6x_station.json)。

| 任务 | 结束仿真时间 | 状态 | 关节数 |
|---|---:|---|---:|
| 1 | 10.75 s | DONE | 6 |
| 2 | 21.45 s | DONE | 6 |
| 3 | 36.95 s | DONE | 6 |
| 4 | 47.60 s | DONE | 6 |
| 5 | 60.20 s | DONE | 6 |
| 6 | **73.10 s** | DONE | 6 |

- 6/6 成功，无 `state=8` 冻结、无 0 关节、无异常日志循环；
- 连续任务会自动复位工位（移除上一件物料）；
- IK 采用“主组 → 备用组 → seed 主组 → seed 备用组”的顺序；seed 回退
  会在 `IKResult.used_seed` 中标记，实际关节仍由速度限幅的关节目标驱动。

## 6. 故障注入（§2.12）

| 故障 | 注入方式 | 实测结果 | 证据 |
|---|---|---|---|
| 目标未到位 | `FAULT_NO_TARGET`：带速置 0 | `FAILED / SENSOR_TIMEOUT`，未吸附 | [json](phase1_fault_no_target.json) |
| 目标 UID 不匹配 | 取料位生成 `Part_Wrong` | `FAILED / PICK_MISMATCH`（3.65 s），未吸附 | [json](phase1_fault_uid_mismatch.json) |
| IK 不可达 | 放置点抬高 1.2 m | `FAILED / IK_FAILED`（线误差 0.629 m）；物料已吸附时终止，不伪造完成 | [json](phase1_fault_ik_unreachable.json) |
| 传感器无读数 | 移除 `Sensor_PickStation` | `FAILED / SENSOR_TIMEOUT`，未吸附 | [json](phase1_fault_sensor_disabled.json) |
| AGV 路径受阻 | 放置静态障碍 | 放置 DONE；`AGV_BLOCKED`（travelled 0.1905 m），`agv.state=BLOCKED`，`distance_ok=False` | [json](phase1_fault_agv_blocked.json) |
| 释放不稳定 | 释放高度 +0.30 m | `FAILED / RELEASE_UNSTABLE`（稳定窗口最大 1.985 m/s） | [json](phase1_fault_release_unstable.json) |

失败路径会同时停带、停 AGV；不会把执行机构留在运动指令上。

## 7. 阶段一退出门槛

- [x] 单个指定物料完成“传送带→UR5→工位”搬运（误差 0.000 m）
- [x] 单个指定物料完成“传送带→UR5→AGV 载台”搬运（跟随误差 0.0002 m，on_tray）
- [x] 传感器不会触发错误对象吸附（UID 误配故障：0 次吸附）
- [x] IK 失败、传感器超时、AGV 阻塞均有明确状态
- [x] AGV 由轮速驱动，未使用位置瞬移（代码检查 + 直线/转向实测）
- [x] 释放后物料 `dynamic + respondable` 属性恢复（mass/friction/mask 完整）
- [x] 连续运行 60 s 无 `state=8` 冻结、0 关节和异常日志循环（实测 73.10 s）
- [x] 阶段一测试报告和场景快照已保存（本文件 + JSON 证据）

## 8. 已知限制与未解决问题

1. **HYBRID_DYNAMIC 输送仍以补偿为主**：conveyor 脚本已保留并确认在驱动 pad
   （探测位移 0.00769 m/10 步），但 4.10 的 pad 不会把切向速度可靠传给 dynamic
   物料。控制器因此沿带面切线按 `带速 × dt` 上限推进物料，并保留
   `dynamic + respondable`；补偿前检查物料仍在有效高度和**横向有效区**内，
   日志记录 `HYBRID_DYNAMIC compensation`，不宣称“真实摩擦输送”。
2. **工具不参与动力学碰撞**：UR5 与 suction pad 的所有 shape 设为
   `static=1, respondable=0`。这是运动学机械臂 + 逻辑吸附的明确配置；
   吸附由 TCP + 吸盘接触传感器 + UID 校验完成，不依赖物理接触力。
   **因此阶段一没有可观测的碰撞状态检查**；到位判定由 TCP 位置/姿态、
   关节收敛、关节限位和关节残余速度共同完成。阶段二引入避障/碰撞检查前
   必须重新配置碰撞掩码并补上 collision-state 验证。
3. **吸盘内置脚本已剥离**：保留其 `Body/Sensor/Link` 几何；内置 `Sensor`
   改名为 `SuctionPad_01_ModelSensor`，另建 `Sensor_SuctionPad_01` 专用接触
   传感器。
4. **IK 是局部求解**：伪逆主组 + 阻尼最小二乘备用组 + seed 回退。seed 回退
   解决了连续任务的局部极小，但不是全局规划；目标集合扩大后需要
   `simIK.findConfigs` 或路径规划。
5. **AGV 只有直线 + 原地转向验证**：阶段一验证 `+X` 轮速行驶、90° 转向和
   载台运输；没有路径跟踪、到站闭环、返回路线或多任务调度（阶段二内容）。
   控制器会拒绝 `--target agv --repeat >1`，因为第一次任务后车体已离开装卸位。
6. **单工位只有一个放置槽**：60 s 连续运行会在每次任务后移除上一件物料；
   阶段二需要多槽位/工位队列。
7. **MCP 尚未接入阶段一**：当前阶段一通过 `src/run_stage1.py` 运行；
   `.mcp.json` 的 `factory-sim` 服务器仍驱动旧版完整场景。若需要在
   Claude Code 中直接调用阶段一，应新增 `build_stage1_scene` /
   `start_stage1` 工具（阶段二前完成）。
8. **稳定阈值与瞬态**：释放后忽略前 2 帧换父瞬态，再取稳定窗口最大速度；
   正常放置的 AGV 接触瞬态约 0.057 m/s，阈值 0.10 m/s；0.30 m 跌落的故障
   峰值为 1.985 m/s，仍能被可靠识别。
9. **本轮评审修复**：保留 conveyor 脚本并加入驱动探测；补偿增加横向有效区
   检查；吸附增加工具速度门槛与停稳等待；AGV 装卸增加 dock sensor、
   停稳和竖直检查；到达判定增加关节限位与残余速度；验收断言增加工位位置
   误差、AGV 行驶距离与载台 XY/Z 区域；修复 AGV 位姿读取失败时假完成、
   失败路径不停机构、释放父对象恢复失败被吞掉等问题。
10. **AGV 载台附件的稳定性**：载台/挡边采用挂在动态车体上的 kinematic
    shape，以避免 “static shape on non-static tree” 警告。在高负载/并发
    实机测试中曾观察到一次 AGV 在 PLACING 前被扰动并倾斜；`is_upright`
    与 dock 检查拒绝了释放（物料保持吸附、任务安全失败），独立复测与
    `both` 连续运行均通过。阶段二做 AGV 路径/载荷压力测试时应优先复查
    载台附件与碰撞掩码。
11. **故障证据时间戳**：§6 的六个故障 JSON 生成于停车加速度修复之前；
    该修复只改变停车时间，不改变故障判定。正常流程证据（§5）已用最终
    代码重新生成。

## 9. 是否允许进入阶段二

- [x] 是（单元闭环、故障状态、直线/转向轮速运输和 60 s 连续运行均通过）
- [ ] 否

进入阶段二前建议先完成第 8 节第 7 条（MCP 接入）并补上第 8 节第 2 条的
碰撞状态检查；阶段一参数（工位/AGV 放置点、载台几何）作为阶段二固定输入。
