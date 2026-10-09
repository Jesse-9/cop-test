# cop-test 使用说明 (Usage Guide)

岛式车间柔性生产仿真 — 从安装到运行的完整指南。

> 源码已全部平铺到 [src/](../src/) 下，配置/资产/文档留在项目根。
> 架构与模块职责见 [CLAUDE.md](../CLAUDE.md)，知识库见 [docs/](README.md)。

## 1. 环境要求

| 组件 | 版本 | 说明 |
|------|------|------|
| Python | ≥ 3.11 | 由 [uv](https://docs.astral.sh/uv/) 管理虚拟环境 |
| CoppeliaSim | ≥ 4.10 | 教育版或专业版均可 |
| ZMQ Remote API | 服务端内置 | 菜单 Add-ons → ZMQ remote API server → 端口 23000 |

操作系统：Windows / macOS / Linux 均可（本文示例为 Windows venv 路径）。

## 2. 安装

```bash
# 1. 克隆 / 进入项目
cd cop-test

# 2. 安装依赖（uv 自动创建 .venv 并安装本包，使 src/ 下模块可被入口脚本导入）
uv sync
```

`uv sync` 会：
- 创建 `.venv/`
- 安装 `coppeliasim-zmqremoteapi-client` 与 `mcp` 依赖
- 以"可编辑"方式安装本项目（`factory-sim` 包），让 `uv run build-scene` 这类入口脚本能解析到 [src/](../src/) 下的模块

## 3. 启动 CoppeliaSim 并激活 ZMQ

1. 打开 CoppeliaSim 4.10+
2. 菜单 **Add-ons → ZMQ remote API server**，确认运行在 `localhost:23000`
3. 场景为空（构建脚本会先 `clear_scene` 清场）

## 4. 运行方式（三选一）

### 4.1 直接跑 Python 脚本

源码在 `src/` 下，直接用解释器跑（脚本目录自动上 `sys.path`，`import factory_*` 即可）：

```bash
# 构建工厂场景（幂等，先清场；首次必跑一次）
.venv/Scripts/python src/build_scene.py
# 或远程 CoppeliaSim
.venv/Scripts/python src/build_scene.py --host 192.168.1.100 --port 23000

# 运行柔性生产仿真
.venv/Scripts/python src/run_simulation.py --time 60 --log factory_sim.log

# 交互菜单
.venv/Scripts/python src/main.py
```

### 4.2 用 uv 入口脚本（路径无关，推荐）

`pyproject.toml` 声明了两个入口脚本（已装包）：

```bash
uv run build-scene              # = src/build_scene.py main()
uv run run-simulation --time 60  # = src/run_simulation.py main() --time 60
uv run python src/main.py        # 交互菜单（无独立入口脚本）
```

### 4.3 通过 MCP 让 Claude Code 编排（AI 接入点）

[.mcp.json](../.mcp.json) 已注册 `factory-sim` MCP 服务器（指向 `src/factory_mcp_server.py`）。
在本项目会话中 Claude Code 自动加载，可用 `/mcp` 确认连接，然后像调函数一样：

```
connect → build_scene → start_simulation(total_time=60) → status → tail_log → stop_simulation
```

19 个 MCP 工具与典型工作流见 [MCP_USAGE.md](MCP_USAGE.md)（含 A 读取 / B 诊断 / C 控制 三档工具，以及只读诊断双连接架构）。

## 5. CLI 参数

### build_scene.py
```
--host HOST    CoppeliaSim 地址 (默认 localhost)
--port PORT    ZMQ 端口 (默认 23000)
--arms ARMS    只构建指定岛的机械臂 (逗号分隔, 如 A 或 A,E_C; all=全部, 默认)
```
`--arms` 用于逐台带动验收：首次只启用 A 岛机械臂，确认后再逐台放开 B/C/D/E_C/E_D
（机械臂实际运行重构方案 §9.7 第 5 条）。

### run_simulation.py
```
--host HOST    CoppeliaSim 地址 (默认 localhost)
--port PORT    ZMQ 端口 (默认 23000)
--time TIME    仿真总时长秒 (0 = 无限, 直到 stop; 默认 60)
--log  LOG     日志文件路径 (默认 factory_sim.log)
```

### main.py
交互菜单：选 (1) 构建场景 或 (2) 运行仿真。

## 6. 配置调参

所有可调参数集中在 [src/factory_config.py](../src/factory_config.py)，分四类：

| 类别 | 示例参数 | 改动后是否需要重建场景 |
|------|---------|----------------------|
| 场景布局 | `WORK_ISLANDS` 坐标/尺寸、`CONVEYORS` 起止点 | ✅ 需重跑 `build_scene` |
| 节拍/阈值 | `TAKT_TIME`、`ZMQ_TIMEOUT`、`D_E_STARVATION_THRESHOLD` | ❌ 重跑仿真即生效 |
| 速度/步长 | `SIM_STEP`、`CONVEYOR_SPEED`、`AGV_SPEED` | ❌ 重跑仿真即生效 |
| 机械臂动画/控制 | `ARM_ANIMATIONS`、`ISLAND_ROBOTS`、`ARM_CONTROL_MODE`、`ARM_POSITION_TOLERANCE` | ⚠️ 动画/控制参数改后重跑仿真；机械臂模型改动需重跑 `build_scene` |

关键参数速查：

| 参数 | 默认 | 说明 |
|------|------|------|
| `TAKT_TIME` | `{A:5,B:3,C:4,D:6,E:8}` s | 各工位工业节拍 |
| `SIM_STEP` | 0.05 s | 仿真步长（Python 主导） |
| `CONVEYOR_SPEED` | 0.5 m/s | 传送带速度 |
| `AGV_SPEED` | 0.8 m/s | AGV 移动速度 |
| `D_E_STARVATION_THRESHOLD` | 15.0 s | D→E 饥饿升级阈值 |
| `CONGESTION_TIMEOUT` | 5.0 s | AGV 路径拥堵判定 |
| `MATERIAL_INTERVAL` | 3.0 s | 物料产出间隔 |
| `ARM_CONTROL_MODE` | `"kinematic"` | 机械臂本体关节控制模式（`kinematic` 运动学 / `dynamic` 物理解算） |
| `ARM_POSITION_TOLERANCE` | 0.05 rad | 关节到位判据：最大位置偏差阈值 |

> 调参实验也可用 MCP 的 `get_config` / `set_config`（仅改内存级配置，详见 [MCP_USAGE.md](MCP_USAGE.md) 的调参工作流）。

## 7. 典型工作流

```
启动 CoppeliaSim + ZMQ 服务端
        │
        ▼
uv run build-scene                 ← 构建场景（一次）
        │
        ▼
uv run run-simulation --time 60    ← 跑仿真
        │  日志写入 factory_sim.log
        ▼
观察控制台 / 查看日志
        │  紧急停止 → emergency_dump_<时间戳>.json
        ▼
改 factory_config.py 调参 → 重跑仿真（布局改了才需重 build）
```

## 8. 日志与故障排查

- **`factory_sim.log`**：双输出（文件 DEBUG / 控制台 INFO），记录调度、节拍、异常、ZMQ 状态。
- **`emergency_dump_<timestamp>.json`**：紧急停止时自动写入状态快照供事后分析。
- 两者均为运行产物，已在 `.gitignore` 忽略。

### 常见问题

| 现象 | 原因 / 处置 |
|------|------------|
| `无法连接 CoppeliaSim` | 确认 CoppeliaSim 已启动且 ZMQ Remote API 服务已激活（Add-ons 菜单） |
| 完整场景启动后冻结（simTime 卡 0.15） | 已知问题（见 [CLAUDE.md](../CLAUDE.md) 已知坑 #3）；用 MCP `step_simulation` 手动逐帧，或用 `inspect_scene_state` / `get_simulation_state` 读取引擎真实状态定位阻塞脚本 |
| MCP `status` 瞬时报 `connected:false` | 主 ZMQ 连接被主循环占用；A/B 档只读诊断工具走独立的 `ro_connector`，不受此影响（见 [MCP_USAGE.md](MCP_USAGE.md) 双连接说明） |
| `dt 不匹配` 警告 | Python `SIM_STEP` 与 CoppeliaSim 物理 dt 不一致，改 [factory_config.py](../src/factory_config.py) 的 `SIM_STEP` 对齐 |
| 别名查询失败 | 4.10 `setObjectAlias` 把斜杠路径转下划线（见 [CLAUDE.md](../CLAUDE.md) 已知坑 #1），用别名叶名匹配 |

## 9. 源码布局速查

[src/](../src/) 下按职责分四层（平铺模块，非包）：

| 层 | 文件 | 职责 |
|----|------|------|
| 基础层 | [factory_core.py](../src/factory_core.py) / [factory_types.py](../src/factory_types.py) / [factory_config.py](../src/factory_config.py) | ZMQ 通信 / 类型 / 配置 |
| 控制层 | [factory_controller.py](../src/factory_controller.py) / [factory_island.py](../src/factory_island.py) / [factory_agv.py](../src/factory_agv.py) / [factory_exceptions.py](../src/factory_exceptions.py) | 编排 / 岛 / AGV / 异常 |
| 视觉层 | [factory_robot.py](../src/factory_robot.py) | 机械臂动画（纯视觉，不影响调度） |
| 接入层 | [factory_mcp_server.py](../src/factory_mcp_server.py) | MCP 工具（Claude Code 接入） |
| 场景/CLI | [build_scene.py](../src/build_scene.py) / [run_simulation.py](../src/run_simulation.py) / [main.py](../src/main.py) | 构建场景 / 仿真入口 / 菜单 |

完整的单步仿真循环（13 步）顺序见 [CLAUDE.md](../CLAUDE.md) 的「单步仿真循环顺序」。

## 10. 开发调试

```bash
# 不经 Claude Code，直接验证 MCP 服务器工具清单（19 个工具）
.venv/Scripts/python src/factory_mcp_server.py --inspect

# 重装本地包（改了 pyproject 后）
uv sync

# 只跑单步仿真（绕过完整 controller 初始化，用于定位冻结）
# 通过 MCP: start_simulation → step_simulation(n=10)
# 冻结排查: get_simulation_state / inspect_scene_state(summary=false) 读引擎真实位姿
```
