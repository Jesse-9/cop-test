# MCP 读取/诊断/控制工具扩展 — 设计文档

- **日期**: 2026-07-20
- **范围**: 解决 Q1(读取 CoppeliaSim 命令行内容)、Q2(读取各 Objective 位姿),并补齐 A/B/C 三档工具 + D 档单连接隐患修复。
- **状态**: draft

## 背景与动机

现有 MCP 服务器 [factory_mcp_server.py](../../../src/factory_mcp_server.py) 暴露 10 个工具,但全部是"写/控制"或"读 Python 内存状态",没有任何工具能:

1. 读取 CoppeliaSim 控制台/状态栏输出(Q1)——ZMQ Remote API 不暴露 `getStatusBarMessage`/`announceSceneContent`(实测 `hasattr` 为 `False`)。
2. 读取场景中物体的**真实**位姿(Q2)——控制器只 `setObjectPosition` 写出去,从不读回(见 [factory_controller.py:838](../../../src/factory_controller.py#L838));`Material.pos` 是 Python 逻辑坐标,不是引擎真实值。
3. 远程执行 Lua 探针做深层诊断。
4. 暂停/批量单步等运行时控制。

此外 CLAUDE.md「已知问题」第 4 条指出:单 ZMQ 连接下,运行中调 `status` 可能瞬时假断连(主循环占用连接)。新增只读工具会让该问题更突出,且 `run_lua` 同步阻塞会卡住主循环 step 节奏——必须同时解决(D 档)。

## 已锁定的决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| run_lua 是否纳入 | 带护栏实现 | 排查深层问题需探针,护栏控风险 |
| 本轮范围 | A 全部 + B 核心 + C 核心(10 工具) | 贴边"9-10 上限",pause/resume/step_n 一组不可拆 |
| 单连接隐患(D 档) | 开第二个只读连接 | 彻底解决假断连 + run_lua 护栏依赖它隔离 |
| MCP Resources | 不引入 | YAGNI,tool 已满足诊断需求,记为未来扩展 |

## 架构:双连接分层

```
FactoryMCPServer
├── connector (主, 已有)        ← 写/控制类工具 + 主仿真循环
│     connect/disconnect/build_scene
│     start/stop/step_simulation(pause/resume)
│     set_config
└── ro_connector (只读, 新增)    ← 所有只读诊断工具 + run_lua
      get_object_pose/list_objects/read_signal/get_object_tree
      inspect_scene_state/get_simulation_state/run_lua
```

**规则:**
- `ro_connector` 惰性握手(lazy),首次只读工具调用时建立,复用 `CoppeliaConnector` 类,host/port 跟随主连接或默认 `localhost:23000`。
- 主连接未连接时,只读工具拒绝("先 connect"),保持单一入口。
- `disconnect` 同时关闭两个连接;`ro_connector` 独立 `check_alive`,不与主循环互挤。
- 只读工具内部加单把 `ro_lock` 串行化;run_lua 长脚本只会卡其他只读工具,**不会卡主循环 step**(主循环走主 connector)——这是"开第二个只读连接"的核心收益。

## 工具清单(10 个新工具)

### A 档 — 读取(4 个,回答 Q1/Q2)

#### `get_object_pose(alias_or_path: str, ref: str = "world", components: str = "pose") -> str`
- 句柄解析:`sim.getObject("/"+alias)`;若入参已含 `/` 前缀则原样用。
- `components`: `pose`(x,y,z,qx,qy,qz,qw)/`pos`(xyz)/`euler`(xyz+欧拉角)/`matrix`(4x4)。
- `ref`: `world`(默认,`sim.handle_world`)或 `parent`(`sim.handle_parent`)。
- 走 ro_connector。别名查不到 → `sim.getObject` 抛 `ZMQError`,捕获后返回 `"找不到对象 '<alias>'"`(不静默)。

#### `list_objects(filter: str = "*", object_type: str = "all") -> str`
- `sim.getObjects(i, type)` 遍历到抛异常为止 + `getObjectAlias`。
- `filter`: 别名前缀或 `fnmatch` 通配(`*` 全部)。
- `object_type`: `all`/`shape`/`dummy`/`joint`/...
- 返回 alias/handle/type 列表。走 ro_connector。

#### `read_signal(name: str) -> str`
- 优先 `sim.getStringSignal(name)`(字符串);回退 `getFloatSignal`/`getIntegerSignal`/`getDoubleSignal`,自动判类型。
- 返回 `{name, type, value}`。走 ro_connector。
- **这是 Q1 的正路**:Lua 脚本里 `sim.setStringSignal("console_xxx", text)` 暴露的控制台文本,Python 端读回。

#### `get_object_tree(root_alias: str) -> str`
- `sim.getObjectsInTree(h, sim.handle_all, 0)`。
- 返回树状 alias/handle/父子关系。走 ro_connector。
- 模式参考 [factory_robot.py:131](../../../src/factory_robot.py#L131) 与 [build_scene.py:261](../../../src/build_scene.py#L261)。

### B 档 — 诊断(3 个核心)

#### `inspect_scene_state(summary: bool = true) -> str`
- 一次性快照: `getSimulationState()` 码+中文释义、`getSimulationTime`、`getSimulationTimeStep`。
- `summary=true`: 只给物体计数 + state;`summary=false`: 遍历 `getObjects` 拿所有物体真实位姿(调 `getObjectPose`)。
- 走 ro_connector。

#### `get_simulation_state() -> str`
- 轻量版:只返回 state 码 + 中文释义 + sim_time。
- state 码全量映射表(0=stopped, 8=paused/blocked, 16=..., 见 CoppeliaSim `sim_simulation_*` 常量)。
- inspect 的廉价子集,用于冻结排查时频繁探活。

#### `run_lua(code: str, timeout: float = 5.0, dry_run: bool = False, confirm: bool = False) -> str`
- 护栏见下节"run_lua 护栏设计"。走 ro_connector + ro_lock。

### C 档 — 运行时控制(3 个核心)

#### `pause_simulation() -> str`
- stepping 模式下置 `controller._pause_requested = True`(新增标志,区别于 `zmq_monitor.comm_paused`),主循环在异常轮询后检测该标志:若置位则只调 `sim.step()` 推进物理引擎、跳过控制逻辑(代码路径与现有 `comm_paused` 跳过分支相同,见 [factory_controller.py:297](../../../src/factory_controller.py#L297)),但**触发源不同**——pause 是用户主动请求,comm_paused 是通信故障被动降级。
- 记录暂停时 sim_time,供 resume 后续跑。
- **走主 connector**(改变仿真状态)。

#### `resume_simulation() -> str`
- 清 `controller._pause_requested` 标志,主循环恢复完整控制逻辑。
- pause/resume 成对,不可拆。

#### `step_simulation(n: int = 1) -> str`(扩展现有)
- 现有单步签名改为 `step_simulation(n: int = 1)`,循环 n 次调 `sim.step()`。
- 返回末步 sim_time + 步数。n 上限 1000 防失控(超限截断并在返回里明示)。
- **走主 connector**。

## run_lua 护栏设计(关键安全边界)

`sim.executeScriptCode(code, handle)` 能执行任意 Lua,四层护栏:

### ① 同步阻塞语义,文档 + 返回双重明示
- 工具描述首行:"⚠ 同步阻塞:执行期间 ro_connector 被独占,其他只读工具会排队等待。"
- 返回结构带 `blocked_seconds`(实际耗时)+ `warning`(超 timeout 截断时)。

### ② 超时硬截断(timeout 默认 5.0s)
- Python 侧无法安全 kill 卡在 ZMQ 请求里的 `sim.executeScriptCode`。
- 实现:在独立线程里执行,主线程 `join(timeout)`;超时不 kill 线程(无法安全 kill),而是**返回 timeout 告警并把该线程标记为 "lingering"**。
- 后续 `run_lua` 调用若检测到 lingering 线程存活,则拒绝("上一个 run_lua 未返回,请先 stop_simulation 重置")。
- 策略:"无法安全中断就拒绝重叠"——比假装修能 kill 安全。

### ③ 只读连接隔离
- run_lua 走 ro_connector,卡住时主仿真循环 step 不受影响(主循环走主 connector)。
- 这是"开第二个只读连接"决策的核心收益。

### ④ 代码审计(软护栏,防呆)
- `len(code) <= 8000` 字符上限(超出拒绝)。
- `dry_run: bool = False`:`True` 时只做客户端预检不执行。
- 客户端预检正则扫描高危调用:`os.execute|io.popen|sim.removeObject|deleteModel|os.exit`;命中则要求 `confirm: bool = True` 才放行。
- 不做硬黑名单(Lua 灵活易绕),靠"显式确认"拦手滑。

### 返回结构
```json
{
  "ok": true,
  "result": "<Lua 返回值/输出>",
  "blocked_seconds": 0.43,
  "timeout": 5.0,
  "warning": null,
  "lingering": false
}
```

## 错误处理与命名一致性

- **非抛异常风格**: 所有新工具沿用现有 `connect`/`build_scene` 的返回字符串模式(见 [factory_mcp_server.py:324](../../../src/factory_mcp_server.py#L324)),连接/句柄失败返回 `"失败: <原因>"`,写 `server.last_error`,不抛。
- **ro_connector 惰性握手**: `ensure_ro_connected()` 在每个只读工具入口调;主连接未连接则拒绝。
- **ZMQ 调用统一兜底**: `except Exception` 写 `last_error`,返回失败串。
- **命名一致性**(贴 CLAUDE.md「已知问题」第 1 条): 用户传入 `alias_or_path` 已含 `/` 前缀则原样用,否则前加 `/`(别名本身不含 `/`,见 [factory_core.py:366](../../../src/factory_core.py#L366))。
- **输出格式**: JSON `ensure_ascii=False`(沿用现有 `status`/`get_config`),中文 key + 值。
- **docstring**: 中文为主,首行一句话功能,必要时⚠提示。

## 不在本轮范围

- `inject_anomaly`(B 档,下轮)。
- `get_joint_positions` / `check_collisions`(B 档,下轮)。
- MCP Resources(信号订阅式,未来扩展)。
- `getStatusBarMessage`/`announceSceneContent`(ZMQ Remote API 不暴露,无法实现)。

## 测试/验证

- **API 可用性已实测**: `getObjectPosition/getObjectOrientation/getObjectQuaternion/getObjectPose/getObjectMatrix/setStringSignal/getStringSignal/getObjectsInTree/getObjectAlias/getObjects/getObject/getConfigurationTree` 全部 `hasattr True`;`getStatusBarMessage/announceSceneContent` 为 `False`(印证 Q1 走 signal 通路)。
- **冒烟测试**: `.venv/Scripts/python src/factory_mcp_server.py --inspect` 打印工具清单(应有 20 个: 原 10 + 新 10)。
- **连接测试**: `connect` → `get_object_pose(alias="Island_A")` 验证读取链路;`start_simulation` 运行中调 `list_objects` 验证不假断连。
- **run_lua 护栏测试**: 超时脚本(如 `while true do end` 等价)验证 lingering 拒绝重叠;`dry_run` 高危脚本验证要求 confirm。
