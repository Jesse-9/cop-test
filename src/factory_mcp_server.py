"""
factory_mcp_server.py — CoppeliaSim 工厂仿真 MCP 服务器
=========================================================
通过 MCP (Model Context Protocol) 向 Claude Code 暴露岛式车间工厂仿真的
完整生命周期工具: 构建场景 / 启停仿真 / 单步推进 / 状态查询 / 配置调参 /
日志读取。让 Claude Code 能像调用本地函数一样编排 CoppeliaSim 仿真。

架构:
  - stdio 传输: Claude Code 通过 npx/stdio 启动本进程, 长驻管理 ZMQ 连接。
  - 单连接复用: 所有工具共享一个 CoppeliaConnector, 避免重复握手。
  - 仿真在守护线程中运行: start_simulation 不阻塞 MCP 请求处理;
    status / stop / step 可在运行中实时查询与控制。

依赖:
  - mcp (FastMCP)        : MCP 服务器框架
  - coppeliasim-zmqremoteapi-client : 已在 pyproject.toml 中

被 Claude Code 调用方式 (settings.json 的 mcpServers):
  {
    "mcpServers": {
      "factory-sim": {
        "command": "e:\\Work\\paper\\cop-test\\.venv\\Scripts\\python.exe",
        "args": ["e:\\Work\\paper\\cop-test\\src\\factory_mcp_server.py"]
      }
    }
  }

用法 (开发调试, 非 Claude Code):
  .venv/Scripts/python src/factory_mcp_server.py            # stdio MCP 模式
  .venv/Scripts/python src/factory_mcp_server.py --inspect  # 打印工具清单后退出
  uv run python -m factory_mcp_server                       # 通过 uv (已装包)

对应架构层: MCP Tool Layer — FactoryMCPServer (Claude Code 接入点)
"""

from __future__ import annotations

import os
import re
import sys
import threading
import time as wall_time
from pathlib import Path
from typing import Any, Optional

# 确保本脚本所在目录在 sys.path 上, 以便 import factory_* 模块
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from mcp.server.fastmcp import FastMCP  # noqa: E402

import factory_config as cfg  # noqa: E402
from factory_core import CoppeliaConnector, setup_logger  # noqa: E402


# ═══════════════════════════════════════════════════════════════
# MCP 服务器实例 — FastMCP server instance
# ═══════════════════════════════════════════════════════════════
mcp = FastMCP("factory-sim")


class FactoryMCPServer:
    """仿真生命周期管理器 — Simulation lifecycle manager backing the MCP tools.

    持有一个共享的 CoppeliaConnector 与可选的运行中 FactoryController。
    仿真在守护线程中运行, 主线程负责响应 MCP 请求。

    设计要点:
      - 单连接复用: connect 后所有工具共享 sim 句柄, 避免重复握手。
      - 线程隔离: 仿真主循环在 daemon 线程, MCP 工具线程只读 controller 状态。
      - 启动探活: start 后派生短时监控线程, 若 N 秒内 sim_time 不推进
        (已知完整场景冻结问题) 则写入 last_error 告警, status 可如实暴露。
      - stop 通过 controller 的内部标志 + sim.stopSimulation 信号循环退出;
        由于现有 FactoryController.run() 以 sim_time >= total_time 为退出条件,
        本服务器在启动时把 total_time 设得很大, stop 时主动调用 stopSimulation
        触发异常循环退出。

    Attributes:
        connector:  共享的 CoppeliaConnector (含 sim 句柄)。
        controller: 当前运行中的 FactoryController, 未运行时为 None。
        sim_thread: 仿真主循环线程。
        last_error: 最近一次异常信息 (供 status 工具返回)。
    """

    # 启动后探活窗口 (秒): 若此时间内 sim_time 无增长则判定冻结
    PROGRESS_WATCH_SECONDS = 5.0

    def __init__(self):
        self.connector: Optional[CoppeliaConnector] = None
        self.controller: Any = None
        self.sim_thread: Optional[threading.Thread] = None
        self.last_error: Optional[str] = None
        self._lock = threading.Lock()
        # 只读诊断专用连接 — 与主连接隔离,避免与主循环抢锁
        self.ro_connector: Optional[CoppeliaConnector] = None
        self.ro_lock = threading.Lock()

    # ── 连接管理 — Connection management ──────────────────────
    @property
    def sim(self):
        """返回当前 sim 句柄, 未连接则返回 None。"""
        return self.connector.sim if self.connector else None

    def ensure_connected(self, host: str = "localhost",
                         port: int = 23000) -> bool:
        """确保已连接 CoppeliaSim — Connect if not already.

        幂等: 已连接则直接返回 True; 未连接则建立新连接。

        Returns:
            True 表示连接成功。
        Raises:
            RuntimeError: 连接失败时抛出, 由 MCP 工具转为错误返回。
        """
        with self._lock:
            if self.connector is not None and self.connector.check_alive():
                return True
            conn = CoppeliaConnector(host=host, port=port)
            if not conn.connect():
                self.last_error = f"无法连接 CoppeliaSim {host}:{port}"
                raise RuntimeError(self.last_error)
            self.connector = conn
            self.last_error = None
            return True

    @property
    def ro_sim(self):
        """只读连接的 sim 句柄。"""
        return self.ro_connector.sim if self.ro_connector else None

    def ensure_ro_connected(self, host: str = "localhost",
                            port: int = 23000) -> bool:
        """惰性建立只读诊断连接 — Lazily establish the read-only connection.

        host/port 跟随主连接(若已连),否则用参数默认值。
        """
        # 主连接未连 → 拒绝(单一入口)
        if self.connector is None or not self.connector.check_alive():
            raise RuntimeError("未连接 CoppeliaSim, 先调用 connect")
        h = self.connector.host if self.connector else host
        p = self.connector.port if self.connector else port
        with self.ro_lock:
            if self.ro_connector is not None and self.ro_connector.check_alive():
                return True
            conn = CoppeliaConnector(host=h, port=p)
            if not conn.connect():
                self.last_error = f"无法建立只读连接 {h}:{p}"
                raise RuntimeError(self.last_error)
            self.ro_connector = conn
            return True

    # ── 仿真生命周期 — Simulation lifecycle ───────────────────
    def is_running(self) -> bool:
        """仿真是否正在运行 — True if the sim thread is alive."""
        return (self.sim_thread is not None
                and self.sim_thread.is_alive()
                and self.controller is not None)

    def _run_controller(self, total_time: float, log_file: str):
        """守护线程入口 — Run the controller loop (daemon thread entry).

        捕获所有异常写入 last_error, 确保 controller 被清理以便重启。
        """
        try:
            self.controller.run()
        except Exception as e:  # noqa: BLE001 - 线程入口需兜底所有异常
            self.last_error = f"{type(e).__name__}: {e}"
        finally:
            self.controller = None

    def _watch_progress(self, started_at: float):
        """启动后探活监控 — Verify sim_time advances shortly after start.

        已知问题: 完整工厂场景启动后可能冻结 (state=8, simTime 卡 0.15),
        FactoryController.run() 不会自报。此方法在 PROGRESS_WATCH_SECONDS 秒后
        检查 controller.sim_time 是否有增长, 若无则写入告警到 last_error,
        使 status 工具能如实反映"已启动但不推进"的状态, 避免哑启动。
        """
        # 先等探活窗口过半, 给 controller 初始化留时间
        wall_time.sleep(self.PROGRESS_WATCH_SECONDS)
        # 仿真已停 → 无需告警
        if not self.is_running():
            return
        ctrl = self.controller
        if ctrl is None or self.sim is None:
            return
        sim_t = 0.0
        state = "?"
        try:
            sim_t = self.sim.getSimulationTime()
            state = self.sim.getSimulationState()
        except Exception as e:  # noqa: BLE001 - 异常态本身就是冻结征兆
            state = f"err:{type(e).__name__}"
        ctrl_t = getattr(ctrl, "sim_time", 0.0)
        # sim_time 停在 0 附近, 或读 sim 状态本身抛异常 → 判定冻结
        if sim_t < 0.01 and ctrl_t < 0.01:
            self.last_error = (
                f"仿真已启动 {self.PROGRESS_WATCH_SECONDS:.0f}s 但 sim_time 仍为 "
                f"{sim_t:.3f}s (state={state}) — 疑似场景冻结 "
                "(见 factory_mcp_server.py §已知问题 / CLAUDE.md)。"
                "建议: stop_simulation 后用 step_simulation 手动逐帧, "
                "或简化场景 (减少机械臂) 定位阻塞脚本。"
            )

    def start(self, total_time: float, log_file: str) -> str:
        """启动仿真主循环 (守护线程) — Start simulation in a daemon thread.

        前置: 必须已 connect 且场景已 build。若已有仿真在运行则拒绝。

        Args:
            total_time: 仿真总时长 (秒), 0 表示无限运行 (此处用大数近似)。
            log_file:   日志文件路径。

        Returns:
            启动结果描述字符串。
        """
        with self._lock:
            if self.is_running():
                return "仿真已在运行中, 先 stop 再启动"
            if self.sim is None:
                return "未连接 CoppeliaSim, 先调用 connect"

            # 延迟导入避免未连接时拖慢启动
            from factory_controller import FactoryController

            logger = setup_logger(log_file=log_file)
            # 0 表示无限运行 → 用一个足够大的值, 由 stop 主动终止
            tt = total_time if total_time and total_time > 0 else 1e9
            ctrl = FactoryController(self.sim, total_time=tt, logger=logger)
            ctrl.connector = self.connector
            ctrl.zmq_monitor.connector = self.connector
            ctrl.zmq_monitor.last_response = 0.0
            self.controller = ctrl
            self.last_error = None

            t = threading.Thread(
                target=self._run_controller,
                args=(tt, log_file),
                name="factory-sim-loop",
                daemon=True,
            )
            self.sim_thread = t
            t.start()

            # 启动后探活: N 秒内 sim_time 不推进则告警 (已知冻结问题)
            watcher = threading.Thread(
                target=self._watch_progress,
                kwargs={"started_at": wall_time.time()},
                name="factory-sim-watch",
                daemon=True,
            )
            watcher.start()

            return (f"仿真已启动 (total_time={total_time}s, "
                    f"log={log_file}), 线程={t.name}; "
                    f"{self.PROGRESS_WATCH_SECONDS:.0f}s 后将探活, "
                    f"用 status 查看推进情况")

    def stop(self) -> str:
        """停止仿真 — Stop the running simulation.

        现有 FactoryController.run() 的退出条件是 sim_time >= total_time,
        本方法通过 sim.stopSimulation() 触发循环内异常从而退出,
        或等待线程自然结束。
        """
        with self._lock:
            if not self.is_running():
                # 即使 controller 已置 None 但线程还在收尾, 也算未运行
                return "仿真未在运行"
            try:
                # 主动停止 CoppeliaSim 仿真 → run() 内 sim.step() 将抛异常退出
                self.sim.stopSimulation()
            except Exception as e:  # noqa: BLE001
                self.last_error = f"stop 异常: {e}"

            # 释放锁, 等待线程退出 (最多 5s)
        if self.sim_thread is not None:
            self.sim_thread.join(timeout=5.0)
        return "已停止" if not self.is_running() else "停止信号已发, 线程仍在收尾"

    def step_once(self) -> str:
        """单步推进仿真 — Advance the simulation by one physics step.

        要求仿真已 start。调用 sim.step() 推进一个 SIM_STEP。
        """
        if self.sim is None:
            return "未连接 CoppeliaSim, 先调用 connect"
        if not self.is_running():
            return "仿真未运行, 先调用 start_simulation"
        try:
            self.sim.step()
            t = self.sim.getSimulationTime()
            return f"已推进一步, sim_time={t:.3f}s"
        except Exception as e:  # noqa: BLE001
            self.last_error = f"step 异常: {e}"
            return f"step 失败: {e}"

    # ── 状态查询 — Status introspection ───────────────────────
    def status(self) -> dict:
        """采集当前仿真状态 — Gather live status for the status tool."""
        running = self.is_running()
        connected = (self.connector is not None
                     and self.connector.check_alive()) if self.connector else False
        ctrl = self.controller
        info: dict[str, Any] = {
            "connected": connected,
            "running": running,
            "host": self.connector.host if self.connector else None,
            "port": self.connector.port if self.connector else None,
            "last_error": self.last_error,
        }
        if ctrl is not None:
            info.update({
                "sim_time": round(ctrl.sim_time, 3),
                "step_count": ctrl.step_count,
                "assembled_count": ctrl.assembled_count,
                "material_count": len(ctrl.materials),
                "agv_state": ctrl.agv_scheduler.state.name,
                "islands": {
                    n: c.state.name for n, c in ctrl.islands.items()
                },
            })
        return info


# 进程级单例 — module-level singleton backing the MCP tools
server = FactoryMCPServer()


# 仿真状态码 → 中文/英文释义 — simulation state code → readable name
# 校准来源: 本机 CoppeliaSim 4.10.x 实测 (2026-07-20, sim.getSimulationState 轮询)
# 校准发现 (实测 vs brief 文档):
#   - brief 文档标 8 = "advancing_running"; 此版未现 8 (从表中移除)
#   - brief 文档标 17 = "advancing_lastbeforestop"; 实测 17 是稳态运行码
#     (state=17 时 sim_time 稳定推进 ~0.050s/步, 持续 1s+ 不变, 实为 advancing_running)
#   - 22 = stopSimulation 后过渡态 (0.3-0.5s 内回 0), brief 文档未列, 已补入
#   - 0 / 16 / 19 与 brief 文档一致
# 备注: 不同 4.10.x 子版本码值可能微调, 此表反映本机实测结果。
_STATE_CODE_NAMES = {
    0:  "stopped",                # sim_simulation_stopped (实测 ✓, 与 brief 一致)
    16: "advancing_first",        # sim_simulation_advancing_firstafterstart (实测 ✓, 与 brief 一致)
    17: "advancing_running",      # 实测稳态运行码 (brief 标 17=lastbeforestop, 此版 17 实为 running)
    19: "paused",                 # sim_simulation_paused (实测 ✓, 与 brief 一致)
    22: "advancing_stopping",     # 实测: stopSimulation 后过渡态 (0.5s 内回 0, brief 未列)
    # brief 文档另列: 8 = sim_simulation_advancing_running; 此 4.10.x 版未现 8。
    # 若在其他子版本观察到 8, 可补回 "advancing_running" 条目并将 17 重标为 lastbeforestop。
}
# 兜底: 未在表中的码返回 "unknown(<code>)"


# run_lua 护栏 — 危险 Lua 调用正则 (软护栏, 靠 confirm 拦手滑)
_LUA_DANGEROUS_PATTERNS = [
    re.compile(r"\bos\.execute\b"),
    re.compile(r"\bio\.popen\b"),
    re.compile(r"\bos\.exit\b"),
    re.compile(r"\bsim\.removeObject\b"),
    re.compile(r"\bsim\.deleteModel\b"),
    re.compile(r"\bloadstring\b"),   # 沙箱已禁用, 命中即拒
]
_LUA_MAX_CHARS = 8000


def _audit_lua_code(code: str, confirm: bool = False) -> tuple[bool, str]:
    """软护栏审计 — audit user Lua before execution.

    Returns: (ok, message). ok=False 时 message 说明拒绝原因。
    """
    if len(code) > _LUA_MAX_CHARS:
        return False, f"代码过长({len(code)}>{_LUA_MAX_CHARS}), 拒绝执行"
    for pat in _LUA_DANGEROUS_PATTERNS:
        if pat.search(code):
            if not confirm:
                return False, (f"检测到危险调用 {pat.pattern}; "
                              "需显式 confirm=True 才放行")
    return True, "ok"


# run_lua 信号通道
_RUN_LUA_SIGNAL = "mcp_run_lua_result"


def _build_run_lua_script(user_code: str) -> str:
    """把用户代码包成定制脚本源码 — wrap user code into a customization script.

    机制(实测): ZMQ sim 无 executeScriptCode; load/loadstring 在沙箱被禁。
    故把用户代码直接嵌入 sysCall_init, 用 pcall 包裹, 结果写信号回读。
    """
    return (
        "function sysCall_init()\n"
        "    local ok, err = pcall(function()\n"
        f"        {user_code}\n"
        "    end)\n"
        f"    local prev = sim.getStringSignal('{_RUN_LUA_SIGNAL}')\n"
        "    if not ok then\n"
        f"        sim.setStringSignal('{_RUN_LUA_SIGNAL}', "
        "'EXEC_ERROR:' .. tostring(err))\n"
        "    elseif not prev then\n"
        f"        sim.setStringSignal('{_RUN_LUA_SIGNAL}', 'NO_RETURN')\n"
        "    end\n"
        "end\n"
    )


@mcp.tool()
def run_lua(code: str, timeout: float = 5.0,
            dry_run: bool = False, confirm: bool = False) -> str:
    """远程执行 Lua 探针 — Execute arbitrary Lua (guarded).

    ⚠ 同步阻塞: 执行期间 ro_connector 被独占, 其他只读工具会排队等待。
    机制: 创建临时定制脚本, 用户代码嵌入 sysCall_init + pcall 包裹,
    结果经 mcp_run_lua_result 字符串信号回读 (ZMQ 无 executeScriptCode,
    且沙箱禁用 load/loadstring)。

    Args:
        code:     Lua 源码。结果请显式 sim.setStringSignal('mcp_run_lua_result', ...)。
        timeout:  秒, 超时则标记 lingering 并拒绝后续重叠调用 (无法安全 kill)。
        dry_run:  True 只审计不执行。
        confirm:  True 才放行含危险调用(os.execute/io.popen/...)的代码。
    Returns:
        JSON {ok, result, blocked_seconds, timeout, warning, lingering}。
    """
    import json
    import time
    # 护栏 ④: 审计
    ok, msg = _audit_lua_code(code, confirm=confirm)
    if not ok:
        return json.dumps({"ok": False, "result": msg,
                           "blocked_seconds": 0.0, "timeout": timeout,
                           "warning": None, "lingering": False},
                          ensure_ascii=False)
    if dry_run:
        return json.dumps({"ok": True,
                           "result": f"dry_run: 审计通过, 未执行 ({msg})",
                           "blocked_seconds": 0.0, "timeout": timeout,
                           "warning": None, "lingering": False},
                          ensure_ascii=False)
    # 检查 lingering (上一个 run_lua 未返回)
    if getattr(server, "_run_lua_lingering", False):
        return json.dumps({"ok": False,
                           "result": "上一个 run_lua 未返回(超时未 kill), "
                                     "请先 stop_simulation 重置",
                           "blocked_seconds": 0.0, "timeout": timeout,
                           "warning": "lingering", "lingering": True},
                          ensure_ascii=False)
    try:
        server.ensure_ro_connected()
    except Exception as e:
        return f"失败: {e}"
    sim = server.ro_sim
    # 先清信号
    try:
        sim.setStringSignal(_RUN_LUA_SIGNAL, "")
    except Exception:
        pass
    script_src = _build_run_lua_script(code)
    result_holder = {"ok": True, "result": "NO_RETURN", "warning": None,
                     "lingering": False}

    def _execute():
        try:
            h = sim.createScript(sim.scripttype_customizationscript,
                                script_src, 0)
            time.sleep(0.3)  # 让 sysCall_init 跑完
            try:
                res = sim.getStringSignal(_RUN_LUA_SIGNAL)
            except Exception:
                res = None
            if res:
                if res.startswith("EXEC_ERROR:"):
                    result_holder["ok"] = False
                result_holder["result"] = res
            try:
                sim.removeObject(h)
            except Exception:
                pass
        except Exception as e:
            result_holder["ok"] = False
            result_holder["result"] = f"EXEC_ERROR: {type(e).__name__}: {e}"

    t0 = time.time()
    th = threading.Thread(target=_execute, daemon=True,
                          name="mcp-run-lua")
    th.start()
    th.join(timeout=timeout)
    blocked = time.time() - t0
    if th.is_alive():
        # 超时, 线程仍在 → 标记 lingering, 拒绝后续重叠
        server._run_lua_lingering = True
        result_holder["ok"] = False
        result_holder["result"] = f"执行超时({timeout}s), 结果可能不完整"
        result_holder["warning"] = "timeout"
        result_holder["lingering"] = True
    return json.dumps({"ok": result_holder["ok"],
                       "result": result_holder["result"],
                       "blocked_seconds": round(blocked, 3),
                       "timeout": timeout,
                       "warning": result_holder["warning"],
                       "lingering": result_holder["lingering"]},
                      ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════
# MCP 工具 — Tools exposed to Claude Code
# ═══════════════════════════════════════════════════════════════

@mcp.tool()
def connect(host: str = "localhost", port: int = 23000) -> str:
    """连接到 CoppeliaSim ZMQ Remote API。

    前置: CoppeliaSim 4.10+ 已启动, ZMQ remote API 服务端已激活
    (Add-ons > ZMQ remote API server), 默认端口 23000。

    Args:
        host: CoppeliaSim 主机地址, 默认 localhost。
        port: ZMQ Remote API 端口, 默认 23000。

    Returns:
        连接结果与 CoppeliaSim 版本字符串, 或失败原因。
    """
    try:
        server.ensure_connected(host, port)
        version = server.connector._read_version()
        where = f"CoppeliaSim {version}" if version else f"CoppeliaSim @ {host}:{port}"
        return f"已连接 {where} (版本信号: {'有' if version else '未设置'})"
    except Exception as e:  # noqa: BLE001
        return f"连接失败: {e}"


@mcp.tool()
def build_scene(clear: bool = True) -> str:
    """在 CoppeliaSim 中构建岛式车间工厂场景。

    调用 build_scene.build_scene() 构建仓库/工作岛/传送带/共享AGV/机械臂,
    遵循 /Warehouse /Island /Conveyor /Mobile 分层命名, 优先使用内置模型,
    不存在时回退到基本几何体。幂等: 默认先清场避免别名冲突
    (CoppeliaSim 4.10 setObjectAlias 把斜杠路径转下划线)。

    前置: 已 connect。

    Args:
        clear: 是否先清空场景, 默认 True (建议保持)。

    Returns:
        构建结果摘要 (对象数量等) 或失败原因。
    """
    try:
        server.ensure_connected()
    except Exception as e:  # noqa: BLE001
        return f"未连接: {e}"
    try:
        import build_scene as bs
        sim = server.sim
        if clear:
            bs.clear_scene(sim)
        bs.build_scene(sim)
        return "场景构建完成"
    except Exception as e:  # noqa: BLE001
        server.last_error = f"build_scene 失败: {e}"
        return f"build_scene 失败: {e}"


@mcp.tool()
def start_simulation(total_time: float = 0.0,
                     log_file: str = "factory_sim.log") -> str:
    """在后台线程启动工厂仿真主循环。

    调用 FactoryController.run() 驱动仓库产出/传送带/工作岛/AGV 调度/
    节拍追踪/异常处理。仿真在守护线程中运行, 本调用立即返回,
    可用 status 实时查询、用 stop 停止。

    前置: 已 connect 且已 build_scene。

    Args:
        total_time: 仿真总时长 (秒), 0 表示无限运行直到 stop。
        log_file:   日志文件路径, 默认 factory_sim.log。

    Returns:
        启动结果描述。
    """
    return server.start(total_time, log_file)


@mcp.tool()
def stop_simulation() -> str:
    """停止正在运行的仿真。

    向 CoppeliaSim 发送 stopSimulation, 触发主循环退出, 最多等待 5s。

    Returns:
        停止结果描述。
    """
    return server.stop()


@mcp.tool()
def step_simulation() -> str:
    """单步推进仿真 (stepping 模式)。

    在仿真已启动的前提下, 推进一个 SIM_STEP (默认 0.05s)。
    适用于逐帧观察调度/节拍行为。

    Returns:
        推进后的仿真时间, 或失败原因 (未连接/未运行)。
    """
    return server.step_once()


@mcp.tool()
def status() -> str:
    """查询仿真实时状态。

    返回连接状态、是否运行中、仿真时间、步数、装配完成数、物料总数、
    AGV 状态、各工作岛状态 (A/B/C/D/E), 以及最近一次错误。

    Returns:
        JSON 格式的状态摘要字符串。
    """
    import json
    return json.dumps(server.status(), ensure_ascii=False, indent=2)


@mcp.tool()
def get_simulation_state() -> str:
    """读取仿真状态码 — Lightweight sim state probe for freeze diagnosis.

    轻量探针: 只返回 getSimulationState() 码 + 释义 + sim_time。
    用于冻结排查时频繁探活(inspect_scene_state 的廉价子集)。

    Returns:
        JSON {state_code, state_name, sim_time}。
    """
    import json
    try:
        server.ensure_ro_connected()
    except Exception as e:
        return f"失败: {e}"
    sim = server.ro_sim
    try:
        code = sim.getSimulationState()
        sim_t = sim.getSimulationTime()
        name = _STATE_CODE_NAMES.get(code, f"unknown({code})")
        return json.dumps({"state_code": code, "state_name": name,
                           "sim_time": round(sim_t, 3)},
                          ensure_ascii=False)
    except Exception as e:
        server.last_error = f"get_simulation_state 失败: {e}"
        return f"失败: {e}"


@mcp.tool()
def inspect_scene_state(summary: bool = True) -> str:
    """一次性场景快照 — One-shot snapshot of sim state + object poses.

    用于冻结排查:一次拿到状态码、sim_time、dt、物体真实位姿。
    summary=True 只给计数;summary=False 给全量位姿(可能很大)。

    Args:
        summary: True 只给计数; False 给全量物体位姿。
    Returns:
        JSON {state_code, state_name, sim_time, dt, object_count,
              poses?: [{alias, pose}]}。
    """
    import json
    try:
        server.ensure_ro_connected()
    except Exception as e:
        return f"失败: {e}"
    sim = server.ro_sim
    try:
        code = sim.getSimulationState()
        sim_t = sim.getSimulationTime()
        dt = sim.getSimulationTimeStep()
        # 枚举所有 shape+dummy — 越界返回 -1 (CoppeliaSim ZMQ 绑定不抛异常)
        # 首个 -1 即止; seen 去重与 list_objects 一致, 防止跨类型重复计数
        objs = []
        seen = set()
        for tval in (sim.object_shape_type, sim.object_dummy_type):
            i = 0
            while i < 5000:
                try:
                    h = sim.getObjects(i, tval)
                except Exception:
                    break
                if h == -1:
                    break  # 越界哨兵
                if h in seen:
                    i += 1
                    continue
                seen.add(h)
                objs.append(h)
                i += 1
        out = {
            "state_code": code,
            "state_name": _STATE_CODE_NAMES.get(code, f"unknown({code})"),
            "sim_time": round(sim_t, 3),
            "dt": round(dt, 4),
            "object_count": len(objs),
        }
        if not summary:
            poses = []
            for h in objs:
                try:
                    alias = sim.getObjectAlias(h)
                except Exception:
                    alias = ""
                try:
                    pose = sim.getObjectPose(h, sim.handle_world)
                except Exception:
                    pose = None
                poses.append({"alias": alias, "handle": h, "pose": pose})
            out["poses"] = poses
        return json.dumps(out, ensure_ascii=False, indent=2)
    except Exception as e:
        server.last_error = f"inspect_scene_state 失败: {e}"
        return f"失败: {e}"


@mcp.tool()
def get_config() -> str:
    """读取当前仿真配置 (factory_config)。

    返回关键参数: 节拍 TAKT_TIME、各设备位置/尺寸、传送带/AGV 路径、
    机械臂配置、异常阈值。用于让 Claude Code 理解场景结构后再调参。

    Returns:
        JSON 格式的配置摘要。
    """
    import json
    keys = [
        "FLOOR_SIZE", "FLOOR_POS", "Z_BASE", "SIM_STEP",
        "CONVEYOR_SPEED", "AGV_SPEED", "MATERIAL_INTERVAL",
        "TOTAL_SIM_TIME", "PROCESS_TIME", "TAKT_TIME",
        "WAREHOUSES", "WORK_ISLANDS", "CONVEYORS",
        "AGV_ALIAS", "AGV_ROUTES", "ISLAND_ROBOTS",
        "UPSTREAM_MAP",
    ]
    out = {}
    for k in keys:
        out[k] = getattr(cfg, k, "<未定义>")
    return json.dumps(out, ensure_ascii=False, indent=2, default=str)


@mcp.tool()
def set_config(key: str, value: Any) -> str:
    """动态修改单个仿真配置参数 (factory_config 模块属性)。

    警告: 仅修改 Python 端内存配置, 已构建的场景对象不会自动更新;
    通常应在 build_scene 之前调用以影响场景构建, 或在 start 之前影响控制逻辑。
    修改后可用 get_config 确认。常见可调项: TOTAL_SIM_TIME, PROCESS_TIME,
    CONVEYOR_SPEED, AGV_SPEED, MATERIAL_INTERVAL, TAKT_TIME['A'..'E']。

    Args:
        key:   factory_config 中的属性名, 如 "CONVEYOR_SPEED"。
               对 TAKT_TIME 等字典, 用点号取值: key="TAKT_TIME.A"。
        value: 新值 (数字/字符串/列表/字典), 由 JSON 反序列化得到。

    Returns:
        修改结果描述 (旧值→新值)。
    """
    try:
        if "." in key:
            mod_attr, dict_key = key.split(".", 1)
            container = getattr(cfg, mod_attr)
            old = container.get(dict_key, "<无>")
            container[dict_key] = value
            return f"cfg.{mod_attr}['{dict_key}']: {old!r} → {value!r}"
        old = getattr(cfg, key, "<未定义>")
        setattr(cfg, key, value)
        return f"cfg.{key}: {old!r} → {value!r}"
    except Exception as e:  # noqa: BLE001
        return f"set_config 失败: {e}"


@mcp.tool()
def get_object_pose(alias_or_path: str, ref: str = "world",
                   components: str = "pose") -> str:
    """读取场景中物体的真实位姿 — Read an object's real pose from CoppeliaSim.

    这是 Q2 的核心工具:从 CoppeliaSim 物理引擎读回真实位姿,而非 Python
    端的逻辑坐标。支持读取单个物体。

    Args:
        alias_or_path: 物体别名或路径。别名本身不含 '/'(见 CLAUDE.md
                       「已知问题」第 1 条);若传入已含 '/' 前缀则原样用,
                       否则前加 '/'。
        ref:          参考系, "world"(默认) 或 "parent"。
        components:   返回分量: "pose"(x,y,z,qx,qy,qz,qw)/
                      "pos"(xyz)/"euler"(xyz+欧拉角)/"matrix"(4x4)。
    Returns:
        JSON 字符串 {alias, handle, ref, components, pose/...}, 或失败原因。
    """
    import json
    try:
        server.ensure_ro_connected()
    except Exception as e:
        return f"失败: {e}"
    sim = server.ro_sim
    path = alias_or_path if alias_or_path.startswith("/") else "/" + alias_or_path
    try:
        h = sim.getObject(path)
    except Exception as e:
        return f"失败: 找不到对象 '{alias_or_path}': {e}"
    ref_handle = sim.handle_world if ref == "world" else sim.handle_parent
    try:
        if components == "pose":
            data = sim.getObjectPose(h, ref_handle)
            key = "pose"
        elif components == "pos":
            data = sim.getObjectPosition(h, ref_handle)
            key = "position"
        elif components == "euler":
            pos = sim.getObjectPosition(h, ref_handle)
            eul = sim.getObjectOrientation(h, ref_handle)
            data = {"position": pos, "euler": eul}
            key = "euler"
        elif components == "matrix":
            data = sim.getObjectMatrix(h, ref_handle)
            key = "matrix"
        else:
            return f"失败: 未知 components '{components}', 应为 pose/pos/euler/matrix"
        out = {"alias": alias_or_path, "handle": h, "ref": ref,
               "components": components, key: data}
        return json.dumps(out, ensure_ascii=False, indent=2)
    except Exception as e:
        server.last_error = f"get_object_pose 失败: {e}"
        return f"失败: {e}"


@mcp.tool()
def list_objects(filter: str = "*", object_type: str = "all") -> str:
    """列出场景中所有物体 — List scene objects by type and name filter.

    用于"不知道场景里有什么"的探索性诊断。

    Args:
        filter:       别名通配(filter="Island_*" 列工作岛前缀); "*" 列全部。
        object_type:  "all"|"shape"|"dummy"|"joint"|"..." 类型过滤。
    Returns:
        JSON {objects: [{alias, handle, type}], count}。
    """
    import fnmatch
    import json
    try:
        server.ensure_ro_connected()
    except Exception as e:
        return f"失败: {e}"
    sim = server.ro_sim
    type_map = {
        "all": None,
        "shape": sim.object_shape_type,
        "dummy": sim.object_dummy_type,
        "joint": sim.object_joint_type,
    }
    types_to_scan = [type_map[object_type]] if object_type in type_map \
        else [sim.object_shape_type, sim.object_dummy_type, sim.object_joint_type]
    if object_type == "all":
        types_to_scan = [sim.object_shape_type, sim.object_dummy_type,
                         sim.object_joint_type]
    results = []
    seen = set()
    for tval in types_to_scan:
        i = 0
        while i < 5000:
            try:
                h = sim.getObjects(i, tval)
            except Exception:
                break  # 越界即结束
            if h == -1:
                break  # 越界哨兵 — ZMQ 绑定返回 -1 不抛异常, 防止 5000 次空转 + junk 条目
            if h in seen:
                i += 1
                continue
            seen.add(h)
            try:
                alias = sim.getObjectAlias(h)
            except Exception:
                alias = ""
            if filter == "*" or fnmatch.fnmatch(alias, filter) \
               or fnmatch.fnmatch(alias, "_" + filter):
                results.append({"alias": alias, "handle": h,
                                "type": object_type if object_type != "all"
                                else "mixed"})
            i += 1
    return json.dumps({"objects": results, "count": len(results)},
                      ensure_ascii=False, indent=2)


@mcp.tool()
def read_signal(name: str) -> str:
    """读取 CoppeliaSim 信号 — Read a named signal (string/float/integer/double).

    这是 Q1(读取软件内命令行内容)的正路:Lua 脚本里用
    sim.setStringSignal("console_xxx", text) 暴露的控制台/状态文本,
    可通过本工具读回。自动探测信号类型。

    Args:
        name: 信号名。
    Returns:
        JSON {name, type, value}, 不存在则 type="none", value=null。
    """
    import json
    try:
        server.ensure_ro_connected()
    except Exception as e:
        return f"失败: {e}"
    sim = server.ro_sim
    try:
        # 按类型依次探测: string → integer → float → double
        v = sim.getStringSignal(name)
        if isinstance(v, str) and v:
            return json.dumps({"name": name, "type": "string", "value": v},
                              ensure_ascii=False)
        v = sim.getIntegerSignal(name)
        if v is not None:
            return json.dumps({"name": name, "type": "integer", "value": v},
                              ensure_ascii=False)
        v = sim.getFloatSignal(name)
        if v is not None:
            return json.dumps({"name": name, "type": "float", "value": v})
        v = sim.getDoubleSignal(name)
        if v is not None:
            return json.dumps({"name": name, "type": "double", "value": v})
        return json.dumps({"name": name, "type": "none", "value": None})
    except Exception as e:
        server.last_error = f"read_signal 失败: {e}"
        return f"失败: {e}"


@mcp.tool()
def get_object_tree(root_alias: str) -> str:
    """列出某对象的子树 — List an object's subtree (joints, shapes, children).

    用于排查机械臂关节、传送带组件等层级结构。模式参考 factory_robot.py
    / build_scene.py 中既有的 getObjectsInTree 用法。

    Args:
        root_alias: 根对象别名(不含 '/')。
    Returns:
        JSON {root, handle, children: [{alias, handle, type}], count}。
    """
    import json
    try:
        server.ensure_ro_connected()
    except Exception as e:
        return f"失败: {e}"
    sim = server.ro_sim
    path = root_alias if root_alias.startswith("/") else "/" + root_alias
    try:
        root_h = sim.getObject(path)
    except Exception as e:
        return f"失败: 找不到根对象 '{root_alias}': {e}"
    try:
        tree = sim.getObjectsInTree(root_h, sim.handle_all, 0)
        children = []
        for obj in tree:
            if obj == root_h:
                continue
            try:
                alias = sim.getObjectAlias(obj)
            except Exception:
                alias = ""
            children.append({"alias": alias, "handle": obj})
        return json.dumps({"root": root_alias, "handle": root_h,
                           "children": children,
                           "count": len(children)},
                          ensure_ascii=False, indent=2)
    except Exception as e:
        server.last_error = f"get_object_tree 失败: {e}"
        return f"失败: {e}"


@mcp.tool()
def tail_log(lines: int = 50) -> str:
    """读取仿真日志文件末尾若干行。

    默认读取 factory_sim.log 末尾 50 行, 用于诊断调度/节拍/异常事件。

    Args:
        lines: 读取末尾行数, 默认 50。

    Returns:
        日志末尾文本, 或文件不存在时的提示。
    """
    p = Path("factory_sim.log")
    if not p.exists():
        return "日志文件 factory_sim.log 不存在 (仿真可能未启动)"
    try:
        all_lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(all_lines[-lines:])
    except Exception as e:  # noqa: BLE001
        return f"读取日志失败: {e}"


@mcp.tool()
def disconnect() -> str:
    """断开与 CoppeliaSim 的连接 (会先停止运行中的仿真)。

    通常在结束会话前调用。断开后可重新 connect。

    Returns:
        断开结果描述。
    """
    if server.is_running():
        server.stop()
    server.connector = None
    server.controller = None
    server.sim_thread = None
    server.ro_connector = None  # 新增: 关闭只读连接
    return "已断开连接"


# ═══════════════════════════════════════════════════════════════
# 入口 — Entry point
# ═══════════════════════════════════════════════════════════════

def _print_tool_list():
    """打印工具清单 (仅 --inspect 用)。"""
    import asyncio
    import json

    async def _go():
        tools = await mcp.list_tools()
        return tools

    tools = asyncio.run(_go())
    print("factory-sim MCP tools:")
    for t in tools:
        print(f"  - {t.name}: {t.description.splitlines()[0] if t.description else ''}")
    print(f"\n共 {len(tools)} 个工具")


def main():
    """启动 MCP 服务器 (stdio 传输) 或打印工具清单。"""
    if "--inspect" in sys.argv:
        _print_tool_list()
        return
    # FastMCP.run() 默认 stdio 传输, 由 Claude Code 启动时驱动
    mcp.run()


if __name__ == "__main__":
    main()
