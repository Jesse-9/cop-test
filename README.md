# Factory Simulation — CoppeliaSim ZeroMQ Remote API

基于 CoppeliaSim 4.10+ 的柔性工业车间仿真系统，使用 Python + ZeroMQ Remote API
实现分阶段场景构建与实时控制。当前审核方案以 `HYBRID_DYNAMIC` 内置 generic belt、
simIK + UR5 + suction pad、proximity sensor、以及 pioneer p3dx 轮速 AGV 为正式架构。
旧五岛案例仅作为迁移和诊断参考。

> 📖 **使用指南**：从安装到运行的完整流程见 [docs/wiki/98-ProjectUsage/USAGE.md](docs/wiki/98-ProjectUsage/USAGE.md)。
> 📖 **MCP 工具**：24 个工具（A 读取/B 诊断/C 控制 + 场景工件生命周期；默认 stage1 后端，legacy 可选）见 [docs/wiki/98-ProjectUsage/MCP_USAGE.md](docs/wiki/98-ProjectUsage/MCP_USAGE.md)。
> 📖 **架构与模块**：见 [CLAUDE.md](CLAUDE.md)。

## 当前实施范围：阶段一单元闭环

阶段零（参数冻结）与阶段一（单工位单元闭环）已于 2026-10-17 实施并通过验收：

```bash
# 运行工位目标 (默认加载 current 工件; 首次需先按步骤 1.2 构建/保存)
.venv/Scripts/python src/run_stage1.py --target station --time 45

# 显式重建并保存新版本后运行
.venv/Scripts/python src/run_stage1.py --build --target station --time 45

# AGV 载台目标（含 0.5 m 轮速行驶）
.venv/Scripts/python src/run_stage1.py --target agv --time 45

# 连续运行 / 故障注入
.venv/Scripts/python src/run_stage1.py --target station --repeat 6 --time 30
.venv/Scripts/python src/run_stage1.py --target station --fault ik_unreachable
```

- 验收报告：[docs/implementation/PHASE1_REPORT.md](docs/implementation/PHASE1_REPORT.md)
- 参数基线：[docs/implementation/PHASE0_BASELINE.md](docs/implementation/PHASE0_BASELINE.md)
- 实测数据：[docs/measurement/phase0_measurements.json](docs/measurement/phase0_measurements.json)

后续实施顺序已调整为四个阶段：

1. 阶段二：物理与运行基础能力（2026-10-21 已完成），包括 TCP 外表面接触、
   碰撞/局部避障、场景构建与仿真启动解耦、统一故障状态和传送带停机验证；
   阶段 2.01 动作与轨迹优化已完成（2026-10-21）；阶段 2.02 AGV 转运路径
   优化能力已实现（2026-10-25，实验开关默认关闭）；阶段 2.1（统一故障恢复）待开始；
2. 阶段三：双工位柔性搬运，包括第二工位、任务队列、资源锁和 AGV 调度；
3. 阶段四：多岛车间，包括 A–E 拓扑、仓库供料、节拍追踪和共享 AGV 调度。

阶段三依赖阶段二物理接口和阶段 2.1 的恢复契约；阶段 2.1 完成前不进入双工位
或多岛扩展。步骤 1.2（2026-10-21）已把场景构建与仿真启动解耦：`start`
不再隐式 build/clear，阶段一基线已保存为只读工件
`docs/scene_artifacts/stage1/L1_S1/v002_20261020_234403/`。

阶段一实现集中在 `src/stage1/` 包（config / layout / physics / scene /
scene_artifact / lifecycle / material_manager / conveyor_controller /
ur5_ik_controller / suction_controller / pioneer_controller / unit_controller /
run），`src/run_stage1.py` 与 `src/run_scene_lifecycle.py` 是兼容入口；
安装后也可用 `uv run run-stage1` / `uv run scene-lifecycle`。旧版
`build_scene.py` / `factory_controller.py` 仍用于历史五岛完整场景，阶段一不调用它们。

## 阶段二：物理约束与运动规划（2026-10-21 通过）

阶段二在阶段一工件上启用影子 UR5 碰撞检查、多解 IK + 局部规划、流式
关节执行、外表面接触判据、保持世界位姿释放、速度/pad 位移双判据停带和
运行期锁存。**阶段一默认行为不变**；阶段二工件从阶段一基线显式派生。

```bash
# 从阶段一基线派生 stage2 工件 (只需一次; 之后 current 指向它)
.venv/Scripts/python src/run_scene_lifecycle.py load stage1/L1_S1/v002_20261020_234403
.venv/Scripts/python src/run_scene_lifecycle.py validate
.venv/Scripts/python src/run_scene_lifecycle.py save --phase stage2 \
  --parent stage1/L1_S1/v002_20261020_234403 --note "阶段二派生"

# 正常流程 (默认 load current --phase stage2 + validate + start)
.venv/Scripts/python src/run_stage2.py --target station --time 150
.venv/Scripts/python src/run_stage2.py --target agv --time 150
.venv/Scripts/python src/run_stage2.py --target both --time 180

# 运行期锁存验收 (实验副本)
.venv/Scripts/python src/run_stage2.py --target station \
  --fault runtime_obstacle --time 150
```

- 报告与限制：[docs/implementation/PHASE2_REPORT.md](docs/implementation/PHASE2_REPORT.md)
- 方案与实施结果：[06-09 阶段二](docs/wiki/06-工业案例/06-09-柔性工业车间阶段二-物理约束与运动规划.md)
- 当前工件：`docs/scene_artifacts/stage2/L1_S1/v013_20261024_232558/`
- 阶段二模块：`src/stage2/`（config / contact / collisions / shadow /
  path_planning / trajectory / safety / conveyor_safety / suction /
  unit_controller / runtime / scene_contract / run，以及 2.01 的 branch /
  metrics 和 2.02 的 diagnostics），`src/run_stage2.py`
  为兼容入口；安装后可用 `uv run run-stage2`。
- OMPL 未启用（N/A）；阶段 2.1 的统一故障恢复尚未开始。

### 阶段 2.01：动作与轨迹优化（2026-10-21 通过）

在阶段二之上加入腕部分支硬约束（q[4] 归一化）、段内固定 q[0]
（lift/rise/place/retreat）、候选一步前瞻与实际 TCP 承接、逐段 TV/最大单步/
分支/净空指标和可行性点精简。高-中-预接近拆分能力保留但默认关闭（A/B 实测
增加总行程）。

```bash
# 影子臂 A/B 规划探针（不启动仿真）
.venv/Scripts/python docs/measurement/probe_stage2_motion_opt.py
.venv/Scripts/python docs/measurement/probe_stage2_motion_opt.py --no-branch-guard
```

- 报告：[docs/implementation/PHASE2_01_REPORT.md](docs/implementation/PHASE2_01_REPORT.md)
- 方案：[06-10 阶段 2.01](docs/wiki/06-工业案例/06-10-柔性工业车间阶段2.01-动作轨迹优化.md)
- station 实机：任务总 TV 19.897→11.966（−39.9%），transfer 15.644→7.601
  （−51.4%），pre_place 10.595→2.515（−76.3%）；agv/both 仍 DONE。
- 新增模块：`src/stage2/branch.py`、`src/stage2/metrics.py`。

### 阶段 2.02：AGV 转运路径优化（2026-10-25 实现，默认关闭）

按 06-10 §8 增加逐 waypoint 诊断、baseline/high_align/detour 路径变体、
有界整链 beam 选链、分层代价（TV + q0/q3 + 折返/加速度代理）、执行
vel/acc/jerk 剖面和段内平滑钩子。默认仍是 `baseline` + `greedy` + 无平滑，
2.01 生产路径不变。

```bash
# 影子规划 A/B（场景已加载并停止）
.venv/Scripts/python docs/measurement/probe_stage2_agv_transfer.py \
  --targets agv --pick-pose -0.30784 0.00012 0.5 \
  --policies baseline high_align --offsets 0.20 0.10 0.00 \
  --max-variants 1 --chain-modes greedy beam
```

- 报告：[docs/implementation/PHASE2_02_REPORT.md](docs/implementation/PHASE2_02_REPORT.md)
- 实测：默认 AGV transfer 计划 TV 14.345（执行 14.337），q[3] 净变化
  +5.019；transit 偏移 0.10/0.00 m 可把计划 TV 降到 7.57/7.55，但 q[0] TV
  升高，按 §8.4 B 不切换默认；beam 与 greedy 结果一致，detour 只增加 TV。
- 新增模块：`src/stage2/diagnostics.py`；探针
  `docs/measurement/probe_stage2_agv_transfer.py`。
- 边界：候选偏移的 3 次实机重复运行、执行平顺性（jerk 峰值 120.65 >
  100 rad/s³）仍属后续工作。

## 步骤 1.2：场景工件与生命周期（2026-10-21 通过）

```bash
# 显式构建 → 保存只读基线 → 加载 → 只读校验 → 启动
.venv/Scripts/python src/run_scene_lifecycle.py build
.venv/Scripts/python src/run_scene_lifecycle.py save --phase stage1 --note "..."
.venv/Scripts/python src/run_scene_lifecycle.py load current
.venv/Scripts/python src/run_scene_lifecycle.py validate
.venv/Scripts/python src/run_scene_lifecycle.py start --target station --time 45

# 状态 / 停止 / 工件列表
.venv/Scripts/python src/run_scene_lifecycle.py status
.venv/Scripts/python src/run_scene_lifecycle.py stop
.venv/Scripts/python src/run_scene_lifecycle.py list
```

- 报告：[docs/implementation/STEP1_2_REPORT.md](docs/implementation/STEP1_2_REPORT.md)
- 工件规范：[docs/scene_artifacts/README.md](docs/scene_artifacts/README.md)
- 执行计划：[06-08](docs/wiki/06-工业案例/06-08-柔性工业车间步骤1.2-场景生命周期与运行入口.md)

`run_stage1.py` 默认加载 `current` 工件；`--build` 才显式重建并保存新版本。
运行中 `build`/`load`/`save`/`validate` 与第二路 `start` 会被状态机和跨进程
运行锁拒绝。

### `load current` 到底加载哪个场景？

默认 `phase=stage1`，解析链是固定的：

```text
docs/scene_artifacts/stage1/current.json
  └─ artifact_id: stage1/L1_S1/v002_20261020_234403
      └─ docs/scene_artifacts/stage1/L1_S1/v002_20261020_234403/scene.ttt
          └─ sim.loadScene(该 .ttt) → adopt_scene() 按 alias 重建句柄
```

- `current.json` 只保存指针（artifact_id / 相对路径 / manifest 摘要 /
  scene sha256）；它只在新工件保存并重新加载校验通过后原子更新。
- 加载的是工件目录里的 `scene.ttt`，**不会**构建场景，也**不会**读取
  `factory-sim.ttt` 或 `scenes/*.ttt`。
- `--phase stage2` 则读取 `docs/scene_artifacts/stage2/current.json`；
  阶段二基线必须从阶段一 v002 派生。
- 加载后状态是 `SCENE_LOADED`，必须再执行 `validate` 才能 `start`。
- 也可直接指定工件 ID 或目录名，例如
  `load stage1/L1_S1/v002_20261020_234403`。

## 步骤 1.1：布局与无挡板取料验证（2026-10-20）

步骤 1.1 在阶段一与阶段二之间收敛 UR5/置物台布局，并用 A/B/C 对照试验评估
“移除挡停体、sensor 主动停带、按实测位姿抓取”的可行性。实现集中在
`src/stage1_1/`。**2026-10-20 起，选定布局 `L1_S1` 与 B 组流程已迁移到
`src/stage1/` 生产路径；阶段一场景不构建挡停体。**

```bash
# 基线场景测量 / 候选布局 simIK 扫描 / A/B/C 试验矩阵
.venv/Scripts/python src/run_step1_1.py --phase measure
.venv/Scripts/python src/run_step1_1.py --phase layout
.venv/Scripts/python src/run_step1_1.py --phase experiment --selected-layout L1_S1
.venv/Scripts/python src/run_step1_1.py --phase all --repeats 10
```

- 执行报告：[docs/implementation/STEP1_1_REPORT.md](docs/implementation/STEP1_1_REPORT.md)
- 运行输出：`docs/implementation/runs/`（每次运行重新生成，不提交）
- 报告引用的证据：`docs/implementation/evidence/`（提交；见该目录 README）
- 复算统计：`.venv/Scripts/python docs/measurement/analyze_step1_1.py --stamp <stamp>`
- 本步骤只做离散 IK 可达性与受控停带可行性，不声明路径无碰撞、运行期急停
  或现实安全认证；这些仍属于阶段二。

## 核心特性

| 特性 | 说明 |
|------|------|
| **场景命名** | 文档可用层级路径，实际 alias 使用扁平下划线并启动时缓存句柄 |
| **物理输送** | 内置 `generic conveyor (belt).ttm`，正式模式仅 `HYBRID_DYNAMIC` |
| **UR5 取放** | `simIK` 求解六关节，专用 `SuctionPad_*_TCP` + proximity sensor + UID 确认 |
| **AGV 运输** | 内置 `pioneer p3dx.ttm`，左右轮 `setJointTargetVelocity`，保留动态物理属性 |
| **工业节拍对齐** | TAKT_TIME 实时追踪, 偏差预警, 超时自动降级/恢复 |
| **四类异常处理** | ZMQ 断连重试, 物流拥堵检测, 工位超时, 物料溢出控制 |
| **结构化日志** | DEBUG/INFO/WARNING/ERROR 四级输出到 `factory_sim.log` |
| **步进式仿真** | `sim.setStepping(True)`, Python 端主导 `sim.step()` |

## 场景拓扑

```
                        ┌─────────────┐
                        │  C_to_E     │
              ┌─────────┤  (传送带)   ├─────────┐
              │         └──────┬──────┘         │
              │                │                │
              ▼                ▼                ▼
┌──────┐  S1_to_A    ┌──────┐  A→C刚性  ┌──────┐  C_to_E   ┌──────┐
│  S1  ├───────────►│  A   ├─────────►│  C   ├──────────►│  E   │
│ 仓库 │            │工作岛│           │工作岛│           │工作岛│
└──┬───┘            └──▲───┘           └──────┘           └──▲───┘
   │                   │                                     │
   │ S1_to_B    ┌──────┤  AGV B→A (优先)                     │
   ├───────────►│  B   │◄────────┐                          │
   │            │工作岛│          │                          │
   │            └──────┘          │                          │
   │                              │   AGV D→E (弹性路径)     │
   │            ┌──────┐  S2_to_D │                          │
   │            │  D   │◄─────────┤                          │
   └───────────►│工作岛├──────────┼──────────────────────────┘
                └──────┘          │
                                  │
┌──────┐                          │
│  S2  ├──────────────────────────┘
│ 仓库 │         S2_to_D
└──────┘
```

### 物料流向

| 路径 | 运输方式 | 类型 | 说明 |
|------|---------|------|------|
| S1 → A | conv_S1A 传送带 | 刚性 | 主原料供应前端工序 A |
| S1 → B | conv_S1B 传送带 | 刚性 | 主原料供应前端工序 B |
| S2 → D | conv_S2D 传送带 | 刚性 | 辅助原料/零部件供应 D |
| B → A | AGV_Shared (B→A) | **柔性** | 跨工位补料, 高优先级可抢占 |
| A → C | 刚性直线传输 | 刚性 | 半成品送入中间工序 C |
| C → E | conv_CE 传送带 | 刚性 | 半成品连续输送至最终工位 |
| D → E | AGV_Shared (D→E) | **弹性** | 专用件输送, 饥饿 15s 后升级 |

### 装配逻辑

E 工作岛收到来自 C 线（SEMI_CE）和 D 线（PART_DE）的物料后，
**配对消耗**两个半成品，生成一个金色成品（FINAL）。

## 历史案例调度策略

以下五岛共享 AGV 状态机属于历史案例，不能覆盖新方案的 pioneer p3dx 物理控制规范。

```
                    ┌──────────┐
                    │   IDLE   │
                    └────┬─────┘
                         │ 任务请求
                    ┌────▼─────┐
              ┌─────│ MOVING_  │─────┐
              │     │ B_TO_A   │     │ (D→E 被抢占)
              │     └────┬─────┘     │
              │          │ 到达      │
              │     ┌────▼─────┐     │
              │     │ MOVING_  │     │
              │     │ D_TO_E   │     │
              │     └────┬─────┘     │
              │          │           │
         ┌────▼──────────▼───────────▼────┐
         │           BLOCKED              │
         │  (路径占用 > 5s 超时)           │
         └────────────┬──────────────────┘
                      │ 路径清除 / 重新规划
                 ┌────▼─────┐
                 │   IDLE    │  (恢复)
                 └──────────┘
```

- **B→A**: 基础优先级 0（最高），不可抢占
- **D→E**: 基础优先级 1（较低），可抢占
- **饥饿升级**: D→E 等待 > 15s 自动升级至优先级 0
- **拥堵处理**: 路径阻塞 > 5s → BLOCKED → 重规划/等待恢复

## 坐标系

```
世界原点 (0,0,0) = 地板中心
地板范围: X ∈ [-8, 8], Y ∈ [-4, 4]

Y ↑
 4 ┤
 2 ┤  S1(-7,2)          A(-2.5,2)        C(2,2)
 0 ┤                                        ● E(6,-0.5)
-1 ┤                     B(-2.5,-1)
-3 ┤  S2(-7,-3)          D(-2.5,-3)
-4 ┤
   └──────────────────────────────────────────→ X
  -8   -6    -4    -2     0     2     4     6     8
```

## 场景命名规范

文档可以用分层路径表达归属，但 CoppeliaSim 实际 alias 必须使用扁平下划线形式；
运行时句柄通过 alias-leaf 匹配缓存，不能依赖 slash-path `getObject`：

```
Warehouse_S1_Main              — 主原料库
Warehouse_S2_Auxiliary         — 辅料库
Conveyor_S1_to_A               — S1→A 传送带
Conveyor_S1_to_B               — S1→B 传送带
Conveyor_S2_to_D               — S2→D 传送带
Conveyor_C_to_E                — C→E 传送带
Island_A_Assembly              — 装配加工岛 A
Island_B_Preprocess            — 预处理岛 B
Island_C_Processing            — 处理岛 C
Island_D_QC_Assembly           — 质检装配岛 D
Island_E_FinalAssembly         — 最终装配线 E
Mobile_AGV_Shared              — 共享柔性 AGV
```

## 工业节拍配置

```python
TAKT_TIME = {
    'A': 5.0,   # Assembly Island A
    'B': 3.0,   # Preprocess Island B
    'C': 4.0,   # Processing Island C
    'D': 6.0,   # QC/Assembly Island D
    'E': 8.0,   # Final Assembly Line E
}
```

- 偏差 > 1.0s → WARNING
- 实际耗时 > TAKT_TIME × 1.5 → TIMEOUT → 自动降级 (延长 50%)
- 连续 3 个正常周期 → 自动恢复原始节拍

## 异常处理机制

| 异常类型 | 检测方式 | 恢复策略 |
|---------|---------|---------|
| **ZMQ 通信中断** | 3s 无响应 | 自动重连 (3次), 暂停新指令 |
| **AGV 路径拥堵** | 目标路径占用 > 5s | BLOCKED → 重规划/等待 → 自动恢复 |
| **工位超时** | 加工时间 > TAKT_TIME × 1.5 | 暂停上游供料 → 节拍降级 → 自动恢复 |
| **物料溢出** | 工作岛队列 ≥ 5 | 暂停上游供料 → 队列降至 2 后恢复 |

## 环境要求

- **Python** ≥ 3.11（通过 uv 管理）
- **CoppeliaSim** ≥ 4.10（Edu 或 Pro 版均可）
- ZeroMQ Remote API 服务端已激活

## 快速开始

```bash
# 1. 克隆 / 进入项目
cd cop-test

# 2. 安装依赖（uv 自动创建 .venv）
uv sync

# 3. 启动 CoppeliaSim，激活 ZMQ Remote API
#    菜单: Add-ons → ZMQ remote API server → 确认运行在 23000 端口

# 4. 构建场景（首次运行，只需执行一次）
uv run python src/reference/build_scene.py
#    或用入口脚本: uv run build-scene
#    只构建某几个岛的机械臂（逐台带动验收）: uv run build-scene --arms A

# 5. 运行仿真（柔性调度 + 节拍追踪 + 异常处理）
uv run python src/reference/run_simulation.py --time 60
#    或用入口脚本: uv run run-simulation --time 60

# 6. 或使用交互菜单
uv run python src/reference/main.py
```

### 远程连接

如果 CoppeliaSim 运行在另一台机器上：

```bash
uv run python src/reference/build_scene.py --host 192.168.1.100 --port 23000
uv run python src/reference/run_simulation.py --host 192.168.1.100 --port 23000
```

## 项目结构

```
cop-test/
├── pyproject.toml              # uv 项目配置 + 依赖 + 入口脚本 + src 布局声明
├── .mcp.json                   # MCP 服务器注册 (Claude Code 自动加载, 指向 src/reference/factory_mcp_server.py)
├── CLAUDE.md                   # 项目说明 + 架构 + 已知坑 (Claude Code 上下文)
├── README.md                   # 本文档
├── src/                        # ── 源码 (三个包 + 兼容入口) ──
│   ├── stage1/                    # 当前实施范围: 阶段一单元闭环
│   │   ├── config.py              # 阶段一参数/别名/容差/故障枚举
│   │   ├── layout.py              # LayoutSpec / 候选布局几何校验
│   │   ├── physics.py             # static/kinematic/respondable/mass/friction/mask
│   │   ├── scene.py               # 单工位场景构建 + 场景契约断言 + adopt_scene
│   │   ├── scene_artifact.py      # 场景工件/manifest/摘要/只读校验
│   │   ├── lifecycle.py           # 生命周期状态机/运行锁/build-save-load-validate-start-stop
│   │   ├── device_handle_map.py   # 启动期唯一别名/句柄表
│   │   ├── material_manager.py    # 物料 7 状态生命周期
│   │   ├── conveyor_controller.py # 内置 conveyor 驱动 + HYBRID_DYNAMIC 补偿
│   │   ├── ur5_ik_controller.py   # simIK 主/备组 + seed 回退
│   │   ├── suction_controller.py  # 逻辑吸附/释放 + 属性快照
│   │   ├── pioneer_controller.py  # p3dx 差速轮速
│   │   ├── unit_controller.py     # §2.11 单步状态机
│   │   └── run.py                 # 阶段一运行 CLI (默认加载工件, --build 才重建)
│   ├── stage1_1/                  # 步骤 1.1: 布局与无挡板取料验证
│   │   ├── config.py              # 候选布局/允许抓取区/试验矩阵
│   │   ├── layout_eval.py         # 场景测量 + simIK 扫描 + 选择
│   │   ├── baseline.py            # 阶段一当前流程停带时序对照
│   │   ├── pickup.py              # A/B/C 单步试验控制
│   │   └── run_step1_1.py         # 步骤 1.1 CLI
│   ├── run_stage1.py              # 阶段一兼容入口 (薄包装)
│   ├── run_scene_lifecycle.py     # 步骤 1.2 生命周期 CLI 兼容入口
│   ├── run_step1_1.py             # 步骤 1.1 兼容入口 (薄包装)
│   └── reference/                 # 历史五岛完整场景层 (阶段一不调用)
│       ├── factory_config.py      # 集中配置 (布局/节拍/阈值/路径/机械臂/内置模型)
│       ├── factory_core.py        # ZMQ 连接+重连/健康监控/步进循环/句柄映射/日志
│       ├── factory_types.py       # 枚举/数据类/物料生成
│       ├── factory_controller.py  # 历史顶层编排器
│       ├── factory_island.py      # 历史工作岛/节拍/供料门控
│       ├── factory_agv.py         # 历史共享 AGV 调度器
│       ├── factory_exceptions.py  # 历史异常轮询
│       ├── factory_robot.py       # 历史关键帧兼容层
│       ├── factory_mcp_server.py  # MCP 服务器 (24 工具)
│       ├── build_scene.py         # 历史五岛场景构建器
│       ├── run_simulation.py      # 历史仿真 CLI
│       └── main.py                # 交互菜单
├── factory-sim.ttt              # 构建好的 CoppeliaSim 场景 (按需跟踪)
├── docs/                        # LLM 知识库 (CoppeliaSim wiki / raw 源材料 / outputs)
│   ├── CLAUDE.md                # 知识库 Schema 与维护规范
│   ├── raw/                     # 官方手册原始资料 (只读, 不修改)
│   ├── wiki/                    # 整理后的 CoppeliaSim 维基 (00~99 分类)
│   │   └── 98-ProjectUsage/     # USAGE.md (安装/运行) + MCP_USAGE.md (24 工具)
│   ├── scene_artifacts/         # 步骤 1.2 版本化场景工件 (stage1 只读基线)
│   └── outputs/                 # 研究报告 / QA / 分析
├── evals/                       # skill 评测集 (evals.json)
├── .claude/skills/             # Claude Code skill 定义 (coppeliasim-factory-sim)
└── .venv/                       # uv 虚拟环境 (不入库)
```

> 运行产物 `factory_sim.log` 与 `emergency_dump_*.json` 每次运行重新生成，
> 已在 `.gitignore` 中忽略，不纳入版本控制。

## 关键配置参数

阶段一可调参数集中在 [stage1/config.py](src/stage1/config.py)；
历史五岛参数集中在 [reference/factory_config.py](src/reference/factory_config.py)：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `TAKT_TIME` | `{A:5, B:3, C:4, D:6, E:8}` s | 各工位工业节拍 |
| `TAKT_DEVIATION_THRESHOLD` | 1.0 s | 节拍偏差警告阈值 |
| `TAKT_TIMEOUT_FACTOR` | 1.5 | 超时因子 |
| `ZMQ_TIMEOUT` | 3.0 s | ZMQ 无响应超时 |
| `ZMQ_MAX_RETRIES` | 3 | ZMQ 最大重连次数 |
| `CONGESTION_TIMEOUT` | 5.0 s | AGV 路径拥堵判定 |
| `D_E_STARVATION_THRESHOLD` | 15.0 s | D→E 饥饿升级阈值 |
| `MAX_QUEUE_LENGTH` | 5 | 工作岛队列溢出阈值 |
| `SIM_STEP` | 0.05 s | 仿真步长 |
| `CONVEYOR_SPEED` | 0.5 m/s | 传送带速度 |
| `AGV_SPEED` | 0.8 m/s | AGV 移动速度 |
| `MATERIAL_INTERVAL` | 3.0 s | 物料产出间隔 |

历史五岛场景: `uv run build-scene` / `uv run run-simulation`。
阶段一: `uv run run-stage1 --target station`（或 `python src/run_stage1.py`）。
步骤 1.1: 候选布局/冻结允许区/试验矩阵集中在
[stage1_1/config.py](src/stage1_1/config.py)；入口
`uv run run-step1-1 --phase all --repeats 10`（或 `python src/run_step1_1.py`）。
