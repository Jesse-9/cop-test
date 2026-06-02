# Factory Simulation — CoppeliaSim ZeroMQ Remote API

基于 CoppeliaSim 4.10+ 的工厂仿真系统，使用 Python + ZeroMQ Remote API 实现场景构建与实时控制。

## 场景拓扑

```
                        ┌─────────────┐
                        │   conv_CE   │
              ┌─────────┤  (传送带)   ├─────────┐
              │         └──────┬──────┘         │
              │                │                │
              ▼                ▼                ▼
┌──────┐  conv_S1A  ┌──────┐  agv_AC  ┌──────┐  conv_CE  ┌──────┐
│  S1  ├───────────►│  A   ├─────────►│  C   ├──────────►│  E   │
│ 仓库 │            │工作岛│           │工作岛│           │工作岛│
└──┬───┘            └──▲───┘           └──────┘           └──▲───┘
   │                   │                                     │
   │ conv_S1B   ┌──────┤  agv_BA                             │
   ├───────────►│  B   │◄────────┐                           │
   │            │工作岛│          │                           │
   │            └──────┘          │                           │
   │                              │      agv_DE (特殊路径)    │
   │            ┌──────┐  conv_S2D│                           │
   │            │  D   │◄─────────┤                           │
   └───────────►│工作岛├──────────┼───────────────────────────┘
                └──────┘          │
                                  │
┌──────┐                          │
│  S2  ├──────────────────────────┘
│ 仓库 │         conv_S2D
└──────┘
```

### 物料流向

| 路径 | 运输方式 | 说明 |
|------|---------|------|
| S1 → A | conv_S1A 传送带 | 主原料供应前端工序 A |
| S1 → B | conv_S1B 传送带 | 主原料供应前端工序 B |
| S2 → D | conv_S2D 传送带 | 辅助原料/零部件供应 D |
| B → A | AGV agv_BA | 跨工位物料转运，柔性衔接 |
| A → C | AGV agv_AC | 半成品送入中间工序 C |
| C → E | conv_CE 传送带 | 半成品连续输送至最终工位 |
| D → E | AGV agv_DE (曲线路径) | 专用件输送链路 |

### 装配逻辑

E 工作岛收到来自 C 线（SEMI_CE）和 D 线（PART_DE）的物料后，**配对消耗**两个半成品，生成一个金色成品（FINAL）。

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
uv run python build_scene.py

# 5. 运行仿真
uv run python run_simulation.py --time 60

# 6. 或使用交互菜单
uv run python main.py
```

### 远程连接

如果 CoppeliaSim 运行在另一台机器上：

```bash
uv run python build_scene.py --host 192.168.1.100 --port 23000
uv run python run_simulation.py --host 192.168.1.100 --port 23000
```

## 项目结构

```
cop-test/
├── pyproject.toml              # uv 项目配置 + 依赖
├── factory_config.py           # 工厂布局参数（坐标、尺寸、颜色、速度）
├── build_scene.py              # 场景构建器 — 在 CoppeliaSim 中生成 3D 模型
├── run_simulation.py           # 仿真控制器 — 传送带/AGV/物料流实时控制
├── main.py                     # 交互入口菜单
├── README.md                   # 本文档
└── .venv/                      # uv 虚拟环境
```

## 配置参数

所有可调参数集中在 [factory_config.py](factory_config.py)：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `FLOOR_SIZE` | (16, 8, 0.05) m | 地板长宽高 |
| `Z_BASE` | 0.6 m | 设备工作高度 |
| `CONVEYOR_SPEED` | 0.5 m/s | 传送带速度 |
| `AGV_SPEED` | 0.8 m/s | AGV 移动速度 |
| `MATERIAL_INTERVAL` | 3.0 s | 物料产出间隔 |
| `PROCESS_TIME` | 2.0 s | 工作岛处理时间 |
| `SIM_STEP` | 0.05 s | 仿真步长 |
| `TOTAL_SIM_TIME` | 60.0 s | 默认仿真时长 |

修改配置后无需重新构建场景——直接运行 `run_simulation.py` 即可生效。若修改了坐标/尺寸，则需重新运行 `build_scene.py`。

## 场景对象命名

所有场景对象通过 `sim.setObjectAlias` 进行了规范命名，可在 CoppeliaSim 场景浏览器中按名称查找：

| 命名模式 | 示例 |
|---------|------|
| `Floor` | 地板 |
| `Warehouse_S1`, `Warehouse_S2` | 仓库 |
| `WorkIsland_A` ~ `WorkIsland_E` | 工作岛主体 |
| `WorkIsland_*_Pillar` | 工作岛标签柱 |
| `Conveyor_conv_S1A` 等 | 传送带主体 |
| `Conveyor_*_RollerStart/End` | 传送带滚轮 |
| `AGV_agv_BA`, `AGV_agv_AC`, `AGV_agv_DE` | AGV 小车 |
| `Waypoint_*_Start/End/N` | 路径标记点 |
