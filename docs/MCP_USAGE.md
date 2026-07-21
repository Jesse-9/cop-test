# MCP 服务器使用文档 — factory_mcp_server.py

`factory_mcp_server.py` 是一个本地 MCP (Model Context Protocol) 服务器，
通过 stdio 向 Claude Code 暴露岛式车间工厂仿真的完整生命周期工具。
Claude Code 在本项目会话中可像调用本地函数一样编排 CoppeliaSim 仿真。

## 注册方式

[.mcp.json](../.mcp.json) 已在项目根注册该服务器：

```json
{
  "mcpServers": {
    "factory-sim": {
      "command": "e:\\Work\\paper\\cop-test\\.venv\\Scripts\\python.exe",
      "args": ["e:\\Work\\paper\\cop-test\\src\\factory_mcp_server.py"],
      "env": { "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1" }
    }
  }
}
```

Claude Code 启动本项目会话时自动加载。用 `/mcp` 命令可确认 `factory-sim` 已连接。

> 新增/修改 `.mcp.json` 后需**重启 Claude Code 会话**才能生效。

## 运行模型

- **stdio 传输**: Claude Code 通过启动一个 Python 子进程与服务器通信，无需额外端口。
- **主连接复用**: 写/控制类工具 (start/stop/step/set_config 等) 与主仿真循环共享一个
  `CoppeliaConnector` (ZMQ → CoppeliaSim:23000)，避免重复握手。
- **仿真在守护线程**: `start_simulation` 调 `FactoryController.run()` 于 daemon 线程，
  立即返回；`status` / `stop` / `step` 可在运行中实时查询与控制。
- **启动探活**: 启动 5s 后若 `sim_time` 不推进，自动在 `status.last_error` 写入冻结告警。
- **双连接**: 主连接 (写/控制类工具 + 主仿真循环) 与 `ro_connector` (只读诊断
  工具 + `run_lua`) 隔离，避免运行中诊断工具与主循环互挤 (解决 CLAUDE.md
  「已知问题」第 4 条假断连)。诊断类工具 (`get_object_pose` / `list_objects` /
  `read_signal` / `get_object_tree` / `inspect_scene_state` / `get_simulation_state`
  / `run_lua`) 走只读连接；其他工具走主连接。

## 19 个工具

### 连接与场景

| 工具 | 说明 | 关键参数 |
|------|------|---------|
| `connect` | 连接 CoppeliaSim ZMQ Remote API | `host` (默认 localhost), `port` (默认 23000) |
| `build_scene` | 构建工厂场景 (调 `build_scene.build_scene(sim)`) | `clear` (默认 true, 先清场) |
| `disconnect` | 断开连接 (会先停止运行中的仿真) | — |

### 仿真生命周期

| 工具 | 说明 | 关键参数 |
|------|------|---------|
| `start_simulation` | 后台线程启动仿真主循环 | `total_time` (秒, 0=无限直到 stop), `log_file` |
| `stop_simulation` | 停止仿真 (最多等 5s 收尾) | — |
| `step_simulation` | 单步/多步推进 (stepping 模式, 需先 start) | `n` (默认 1, 上限 1000) |

### 状态与配置

| 工具 | 说明 | 关键参数 |
|------|------|---------|
| `status` | 实时状态: 连接/运行/仿真时间/步数/装配数/AGV/各岛状态/最近错误 | — |
| `get_config` | 读取 factory_config 关键参数 (节拍/位置/路径/机械臂/阈值) | — |
| `set_config` | 动态改单个配置 (内存级, 对已构建场景无效, 应在 build 前) | `key` (支持 `TAKT_TIME.A` 点号取值), `value` |
| `tail_log` | 读 `factory_sim.log` 末尾 N 行 | `lines` (默认 50) |

### A 档 — 读取 (Q1/Q2)

只读诊断工具，走独立的 `ro_connector`，可在仿真运行中安全调用。

| 工具 | 说明 | 关键参数 |
|------|------|---------|
| `get_object_pose` | 读物体真实位姿 (pose/pos/euler/matrix) | `alias_or_path`, `ref=world`, `components=pose` |
| `list_objects` | 列场景物体 (按类型/别名过滤) | `filter=*`, `object_type=all` |
| `read_signal` | 读信号 (string/int/float/double 自动判类型) | `name` |
| `get_object_tree` | 列某对象子树 (joints/shapes/children) | `root_alias` |

### B 档 — 诊断

| 工具 | 说明 | 关键参数 |
|------|------|---------|
| `inspect_scene_state` | 一次性快照 (状态码 + sim_time + 物体位姿) | `summary=true` |
| `get_simulation_state` | 轻量状态探针 (冻结排查, 仅状态码 + sim_time) | — |
| `run_lua` | 远程 Lua 探针 (带护栏: 审计/超时/lingering/confirm) | `code`, `timeout=5`, `dry_run`, `confirm` |

### C 档 — 运行时控制

| 工具 | 说明 | 关键参数 |
|------|------|---------|
| `pause_simulation` | 暂停控制逻辑 (物理仍推进, stepping 模式下) | — |
| `resume_simulation` | 恢复控制逻辑 (清 pause 标志) | — |

> `step_simulation` 见「仿真生命周期」表 (扩展签名, 支持 `n` 多步)。

## 典型工作流

### 1. 标准仿真流程

让 Claude Code 依次调用：
```
connect                          → 连接 CoppeliaSim
build_scene                      → 构建工厂场景
start_simulation(total_time=60)  → 启动 60s 仿真
status                           → 查看推进情况
tail_log(lines=30)               → 看调度/节拍日志
stop_simulation                  → 停止
```

### 2. 调参实验

```
get_config                       → 看当前配置
set_config(key="TAKT_TIME.A", value=4.0)   → 调 A 岛节拍
set_config(key="CONVEYOR_SPEED", value=0.8)→ 调传送带速度
build_scene                      → 重新构建 (参数生效)
start_simulation                 → 跑仿真对比
```

> ⚠ `set_config` 修改的是 Python 内存配置，**对已构建的场景物体无效**。
> 调参后需重新 `build_scene` 才影响场景构建；控制逻辑参数 (节拍/速度/阈值)
> 则在下次 `start_simulation` 时生效。

### 3. 冻结排查 (见下方已知问题)

```
start_simulation                 → 启动
status                           → 5s 后若 last_error 报"疑似场景冻结"
stop_simulation                  → 停掉
step_simulation                  → 手动逐帧 (绕过 controller 复杂初始化)
                                  或简化场景定位阻塞脚本
```

## status 返回字段

```json
{
  "connected": true,          // ZMQ 连接是否健康
  "running": true,            // 仿真线程是否在跑
  "host": "localhost",
  "port": 23000,
  "last_error": null,         // 最近异常/告警 (冻结时会在此报)
  "sim_time": 3.25,           // 当前仿真时间 (秒)
  "step_count": 65,
  "assembled_count": 0,       // E 岛装配成品数
  "material_count": 4,        // 活跃物料总数
  "agv_state": "MOVING_B_TO_A",
  "islands": { "A": "PROCESSING", "B": "IDLE", ... }
}
```

## 已知问题

### 完整场景启动后冻结

完整工厂场景 (6 机械臂 + 传送带 + AGV) 启动后约 3 帧处仿真冻结
(`state=8`, simTime 卡 0.15)，`sim.step()` 不再推进。单机械臂/空场景 stepping 正常，
推断是某机械臂模型脚本组合阻塞。

**MCP 已内置探活**: `start_simulation` 启动 5s 后若 `sim_time` 无增长，
`status.last_error` 会显示类似：
```
仿真已启动 5s 但 sim_time 仍为 0.000s (state=err:ZMQError) — 疑似场景冻结 ...
建议: stop_simulation 后用 step_simulation 手动逐帧, 或简化场景 (减少机械臂) 定位阻塞脚本。
```

### 单连接共享的瞬时假断连

仿真运行中调 `status`，`connected` 可能瞬时为 `false`——因为单 ZMQ 连接被
主循环占用，`check_alive` 探针瞬时失败。这不是真断连，过几秒再查即可。

## 开发调试

不用经过 Claude Code，直接命令行验证服务器：
```bash
# 打印 19 个工具清单
.venv/Scripts/python src/factory_mcp_server.py --inspect

# 直接跑各 CLI (不经 MCP)
.venv/Scripts/python src/build_scene.py
.venv/Scripts/python src/run_simulation.py --time 60
```
