# Factory Simulation — CoppeliaSim ZeroMQ Remote API

基于 CoppeliaSim 4.10+ 的岛式车间柔性生产仿真系统，使用 Python + ZeroMQ Remote API
实现分层场景构建与实时控制。

> 📖 **使用指南**：从安装到运行的完整流程见 [docs/wiki/98-ProjectUsage/USAGE.md](docs/wiki/98-ProjectUsage/USAGE.md)。
> 📖 **MCP 工具**：19 个工具（A 读取/B 诊断/C 控制）见 [docs/wiki/98-ProjectUsage/MCP_USAGE.md](docs/wiki/98-ProjectUsage/MCP_USAGE.md)。
> 📖 **架构与模块**：见 [CLAUDE.md](CLAUDE.md)。

## 核心特性

| 特性 | 说明 |
|------|------|
| **分层命名** | `/Warehouse/S1_Main`, `/Island/A_Assembly`, `/Conveyor/S1_to_A`, `/Mobile/AGV_Shared` |
| **共享 AGV 柔性调度** | 单 AGV 双路径 (B→A 优先, D→E 弹性), 支持抢占 + 饥饿升级 |
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

## AGV 调度策略

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

所有对象遵循分层路径命名 (`/Group/Item_Description`):

```
/Warehouse/S1_Main             — 主原料库
/Warehouse/S2_Auxiliary        — 辅料库
/Conveyor/S1_to_A              — S1→A 传送带
/Conveyor/S1_to_B              — S1→B 传送带
/Conveyor/S2_to_D              — S2→D 传送带
/Conveyor/C_to_E               — C→E 传送带
/Island/A_Assembly             — 装配加工岛 A
/Island/B_Preprocess           — 预处理岛 B
/Island/C_Processing           — 处理岛 C
/Island/D_QC_Assembly          — 质检装配岛 D
/Island/E_FinalAssembly        — 最终装配线 E
/Mobile/AGV_Shared             — 共享柔性 AGV
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
uv run python src/build_scene.py
#    或用入口脚本: uv run build-scene

# 5. 运行仿真（柔性调度 + 节拍追踪 + 异常处理）
uv run python src/run_simulation.py --time 60
#    或用入口脚本: uv run run-simulation --time 60

# 6. 或使用交互菜单
uv run python src/main.py
```

### 远程连接

如果 CoppeliaSim 运行在另一台机器上：

```bash
uv run python src/build_scene.py --host 192.168.1.100 --port 23000
uv run python src/run_simulation.py --host 192.168.1.100 --port 23000
```

## 项目结构

```
cop-test/
├── pyproject.toml              # uv 项目配置 + 依赖 + 入口脚本 + src 布局声明
├── .mcp.json                   # MCP 服务器注册 (Claude Code 自动加载, 指向 src/factory_mcp_server.py)
├── CLAUDE.md                   # 项目说明 + 架构 + 已知坑 (Claude Code 上下文)
├── README.md                   # 本文档
├── src/                        # ── 全部源码 (平铺顶层模块, 见下方分类) ──
│   │  # 基础层
│   ├── factory_config.py          # 集中配置 (布局/节拍/阈值/路径/机械臂/内置模型)
│   ├── factory_core.py            # ZMQ 连接+重连/健康监控/步进循环/句柄映射/日志
│   ├── factory_types.py           # 枚举/数据类/物料生成 (ItemType/AGVState/Material…)
│   │  # 控制层
│   ├── factory_controller.py      # 顶层编排器 (13 步固定顺序步进)
│   ├── factory_island.py          # 工作岛状态机 + 节拍追踪 + 供料门控
│   ├── factory_agv.py             # 共享 AGV 柔性调度器 (优先级/饥饿升级/阻塞恢复)
│   ├── factory_exceptions.py      # 统一异常轮询与紧急停止
│   │  # 视觉层
│   ├── factory_robot.py           # 机械臂关键帧动画 (纯视觉层, 不影响调度)
│   │  # 接入层
│   ├── factory_mcp_server.py      # MCP 服务器 (19 个工具: A 读取/B 诊断/C 控制, 双连接)
│   │  # 场景 / CLI 入口
│   ├── build_scene.py             # 场景构建器 (分层命名 + 内置模型回退)
│   ├── run_simulation.py          # 柔性仿真 CLI 入口
│   └── main.py                    # 交互菜单入口
├── factory-sim.ttt              # 构建好的 CoppeliaSim 场景 (按需跟踪)
├── docs/                        # LLM 知识库 (CoppeliaSim wiki / raw 源材料 / outputs)
│   ├── CLAUDE.md                # 知识库 Schema 与维护规范
│   ├── raw/                     # 官方手册原始资料 (只读, 不修改)
│   ├── wiki/                    # 整理后的 CoppeliaSim 维基 (00~99 分类)
│   │   └── 98-ProjectUsage/     # USAGE.md (安装/运行) + MCP_USAGE.md (19 工具)
│   └── outputs/                 # 研究报告 / QA / 分析
├── evals/                       # skill 评测集 (evals.json)
├── .claude/skills/             # Claude Code skill 定义 (coppeliasim-factory-sim)
└── .venv/                       # uv 虚拟环境 (不入库)
```

> 运行产物 `factory_sim.log` 与 `emergency_dump_*.json` 每次运行重新生成，
> 已在 `.gitignore` 中忽略，不纳入版本控制。

## 关键配置参数

所有可调参数集中在 [factory_config.py](src/factory_config.py)：

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

修改配置后无需重新构建场景 — 直接运行 `uv run python src/run_simulation.py` 即可生效。
若修改了坐标/尺寸，则需重新运行 `uv run python src/build_scene.py`。
