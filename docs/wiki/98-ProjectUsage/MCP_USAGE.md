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
get_simulation_state             → 轻量探针: 读状态码 + sim_time (频繁探活用)
inspect_scene_state(summary=true)→ 一次性快照: 状态码 + sim_time + dt + 物体计数
stop_simulation                  → 停掉
step_simulation(n=10)             → 手动多步逐帧 (绕过 controller 复杂初始化)
inspect_scene_state(summary=false)→ 全量物体真实位姿, 定位哪个物体/脚本卡住
```

> 新诊断工具 (`get_simulation_state` / `inspect_scene_state` / `get_object_pose`)
> 从 CoppeliaSim 物理引擎读**真实**状态与位姿，而非 Python 端逻辑值——
> 冻结时能直接看到引擎真实落点，定位是物体被推飞、碰撞触发还是脚本阻塞。

### 4. 读取与诊断 (A/B 档, 回答 Q1/Q2)

```
list_objects                     → 列场景所有物体 (探索"场景里有什么")
get_object_pose(alias="Island_A")→ 读某物体真实位姿 (Q2: pose/pos/euler/matrix)
get_object_tree(root_alias="Island_A_Assembly")→ 列机械臂/传送带子树
read_signal(name="console_log")  → 读 Lua 脚本暴露的信号 (Q1 正路)
inspect_scene_state(summary=false)→ 全量物体位姿快照
```

### 5. 远程 Lua 探针 (run_lua)

```
run_lua(code='sim.setStringSignal("probe_x", tostring(sim.getSimulationTime()))')
read_signal(name="probe_x")      → 读回 Lua 结果
```

> ⚠ `run_lua` 同步阻塞 ro_connector, 执行期间其他只读工具排队。带四层护栏:
> 代码长度上限 8000、危险调用正则审计 (os.execute/io.popen/...需 confirm=True)、
> dry_run 只审计不执行、超时 (默认 5s) 标记 lingering 拒绝后续重叠调用。
> 见下方「run_lua 执行机制」节。

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
(state 见 `_STATE_CODE_NAMES` 表, simTime 卡 0.15)，`sim.step()` 不再推进。单机械臂/空场景 stepping 正常，
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

> 注: A/B 档只读诊断工具走独立的 `ro_connector`，**不受此问题影响**——
> 这正是双连接设计的目的 (见上方「运行模型」)。

## run_lua 执行机制

`run_lua` 远程执行任意 Lua，但 ZMQ Remote API 的限制让它不能用"常规"方式:

- **ZMQ `sim` 对象不暴露 `executeScriptCode`** (实测 `hasattr` 为 False)。
- **定制脚本沙箱禁用 `load`/`loadstring`** (实测: 任何用 `load()` 的脚本报
  "error in script function")——无法运行时编译用户代码。

已实测验证的唯一可行机制 (见 spec):
1. `sim.createScript(sim.scripttype_customizationscript, script_src, 0)`
   创建一个临时定制脚本，其 `sysCall_init` 内**直接嵌入**用户代码并用 `pcall` 包裹。
2. 用户代码把结果/错误写入字符串信号 `mcp_run_lua_result`
   (如 `sim.setStringSignal("mcp_run_lua_result", tostring(x))`)。
3. Python 端 `sim.getStringSignal("mcp_run_lua_result")` 读回，再 `sim.removeObject`
   清理脚本。

因此调用 `run_lua` 时，**用户代码须显式把结果写到 `mcp_run_lua_result` 信号**，
否则返回 `NO_RETURN`。错误会被 `pcall` 捕获并以 `EXEC_ERROR:` 前缀回传。

四层护栏 (详见 spec):
1. **审计** — 代码超 8000 字符拒绝; 危险调用正则 (`os.execute`/`io.popen`/
   `os.exit`/`sim.removeObject`/`sim.deleteModel`/`loadstring`) 命中需 `confirm=True`。
2. **dry_run** — 只审计不执行。
3. **lingering 检查** — 若上一个 `run_lua` 超时未返回 (线程仍活)，拒绝新调用，
   提示"先 stop_simulation 重置"。`stop`/`disconnect` 会重置 lingering 标志。
4. **超时** — 执行在独立线程，`join(timeout)` (默认 5s); 超时不 kill 线程
   (无法安全 kill)，而是标记 lingering 并返回 timeout 结果。

## 测试

`tests/` 下分两类:

- **纯单测** (不需 CoppeliaSim, 始终过): `test_state_codes.py` (状态码映射表)、
  `test_mcp_diagnose_tools.py::TestAuditLua` (run_lua 护栏审计正则)。
- **集成测试** (需 CoppeliaSim 在线): `test_mcp_read_tools.py` (A 档 4 工具)、
  `test_mcp_diagnose_tools.py::TestInspectSceneState`/`TestRunLuaExecution`、
  `test_mcp_control_tools.py` (C 档 pause/resume/step_n)。

`tests/conftest.py` 的 `mcp_server` fixture **绑定模块级单例** `factory_mcp_server.server`
(MCP 工具函数通过闭包引用该单例，夹具若建新实例则工具仍操作未连接的单例，
导致测试静默 skip-by-failure)。CoppeliaSim 离线/冻结时 fixture 经 3s 超时探针
自动 skip 集成测试 (不挂起)。

```bash
# 跑全量 (CoppeliaSim 离线时集成测试自动 skip)
.venv/Scripts/python.exe -m pytest tests/ -v
# 只跑纯单测
.venv/Scripts/python.exe -m pytest tests/test_state_codes.py tests/test_mcp_diagnose_tools.py::TestAuditLua -v
```

## 开发调试

不用经过 Claude Code，直接命令行验证服务器：
```bash
# 打印 19 个工具清单
.venv/Scripts/python src/factory_mcp_server.py --inspect

# 直接跑各 CLI (不经 MCP)
.venv/Scripts/python src/build_scene.py
.venv/Scripts/python src/run_simulation.py --time 60
```
