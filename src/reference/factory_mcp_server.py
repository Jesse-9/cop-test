"""
factory_mcp_server.py — CoppeliaSim 工厂仿真 MCP 服务器
=========================================================
通过 MCP (Model Context Protocol) 向 Claude Code 暴露岛式车间工厂仿真的
完整生命周期工具: 构建场景 / 启停仿真 / 单步推进 / 状态查询 / 配置调参 /
日志读取。让 Claude Code 能像调用本地函数一样编排 CoppeliaSim 仿真。

架构:
  - stdio 传输: Claude Code 通过 npx/stdio 启动本进程, 长驻管理 ZMQ 连接。
  - 双连接: 主连接 (写/控制类工具 + 主仿真循环) 与 ro_connector (只读诊断工具 +
    run_lua) 经 ro_lock 隔离, 避免运行中诊断工具与主循环互挤 (解决假断连)。
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

import json
import os
import re
import sys
import threading
import time as wall_time
from pathlib import Path
from typing import Any, Optional

# 确保本脚本所在目录和 src/ 在 sys.path 上, 以便 import factory_* / stage1
_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent
for _p in (_HERE, _SRC):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from mcp.server.fastmcp import FastMCP  # noqa: E402

import reference.factory_config as cfg  # noqa: E402
from reference.factory_core import CoppeliaConnector, setup_logger  # noqa: E402


# ═══════════════════════════════════════════════════════════════
# MCP 服务器实例 — FastMCP server instance
# ═══════════════════════════════════════════════════════════════
mcp = FastMCP("factory-sim")


class FactoryMCPServer:
    """仿真生命周期管理器 — Simulation lifecycle manager backing the MCP tools.

    持有一个共享的 CoppeliaConnector 与可选的运行中 FactoryController。
    仿真在守护线程中运行, 主线程负责响应 MCP 请求。

    设计要点:
      - 双连接: connect 后主连接 (写/控制类工具 + 主仿真循环) 与 ro_connector
        (只读诊断工具 + run_lua) 隔离, 避免诊断工具与主循环互挤。
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
        # 当前后端: "stage1" (默认/当前实现) 或 "legacy" (旧五岛参考层)
        self.backend: Optional[str] = None
        # ── stage1 后端状态 ────────────────────────────────────
        self.stage1_scene: Any = None
        self.stage1_comp: Any = None
        self.stage1_controller: Any = None
        self.stage1_runner: Optional[threading.Thread] = None
        self.stage1_stop = threading.Event()
        self.stage1_pause = threading.Event()
        self.stage1_pause_ack = threading.Event()
        self.stage1_results: list[dict] = []
        self.stage1_report_path: Optional[str] = None
        # ── 步骤 1.2 场景生命周期 (stage1) ─────────────────────
        self.stage1_lifecycle: Any = None
        self.stage1_run_id: Optional[str] = None
        # 只读诊断专用连接 — 与主连接隔离,避免与主循环抢锁
        self.ro_connector: Optional[CoppeliaConnector] = None
        self.ro_lock = threading.Lock()
        # run_lua 超时标志: True 表示上一个 run_lua 未返回, 拒绝重叠调用
        # (stop_simulation 与 disconnect 均会重置)
        self._run_lua_lingering: bool = False

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
            self.stage1_lifecycle = None      # 新连接 → 重建生命周期会话
            self.last_error = None
            return True

    def _ensure_lifecycle(self):
        """返回与当前连接绑定的 stage1 生命周期管理器。"""
        if self.sim is None:
            raise RuntimeError("未连接 CoppeliaSim, 先调用 connect")
        from stage1.lifecycle import LifecycleManager

        if self.stage1_lifecycle is None:
            self.stage1_lifecycle = LifecycleManager(
                self.sim, getattr(self.connector, "simIK", None),
                actor="mcp",
                host=getattr(self.connector, "host", "localhost"),
                port=getattr(self.connector, "port", 23000))
        else:
            self.stage1_lifecycle.sim = self.sim
            self.stage1_lifecycle.simIK = getattr(
                self.connector, "simIK", None)
        return self.stage1_lifecycle

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
        """仿真是否正在运行 — True if either backend loop is alive."""
        legacy = (self.sim_thread is not None
                  and self.sim_thread.is_alive()
                  and self.controller is not None)
        stage1 = (self.stage1_runner is not None
                  and self.stage1_runner.is_alive())
        return bool(legacy or stage1)

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

        已知问题: 完整工厂场景启动后可能冻结 (state 见 _STATE_CODE_NAMES 表,
        simTime 卡 0.15), FactoryController.run() 不会自报。此方法在
        PROGRESS_WATCH_SECONDS 秒后检查 controller.sim_time 是否有增长,
        若无则写入告警到 last_error, 使 status 工具能如实反映"已启动但不推进"
        的状态, 避免哑启动。
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

    def start(self, total_time: float, log_file: str,
              backend: str = "stage1", target: str = "station",
              fault: str = "none", scenario: str = "") -> str:
        """启动仿真主循环 (守护线程) — Start simulation in a daemon thread.

        backend="stage1" (默认/当前实现) 只启动**已加载**的场景, 不执行
        build/clear; 启动点由 LifecycleManager.prepare_start() 取运行锁并
        执行完整只读校验。若当前工件 source_phase=stage2/stage3, 自动
        路由到阶段二/阶段三 runner。backend="legacy" 运行旧五岛控制器。
        """
        if backend == "stage1":
            return self._start_stage1(total_time, log_file, target, fault,
                                      scenario=scenario)
        if backend == "legacy":
            return self._start_legacy(total_time, log_file)
        return f"未知 backend: {backend!r} (可选 stage1/legacy)"

    def _start_legacy(self, total_time: float, log_file: str) -> str:
        with self._lock:
            if self.is_running():
                return "仿真已在运行中, 先 stop 再启动"
            if self.sim is None:
                return "未连接 CoppeliaSim, 先调用 connect"

            # 延迟导入避免未连接时拖慢启动
            from reference.factory_controller import FactoryController

            logger = setup_logger(log_file=log_file)
            # 0 表示无限运行 → 用一个足够大的值, 由 stop 主动终止
            tt = total_time if total_time and total_time > 0 else 1e9
            ctrl = FactoryController(self.sim, total_time=tt, logger=logger)
            ctrl.connector = self.connector
            ctrl.zmq_monitor.connector = self.connector
            ctrl.zmq_monitor.last_response = 0.0
            self.controller = ctrl
            self.backend = "legacy"
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

            return (f"仿真已启动 (backend=legacy, total_time={total_time}s, "
                    f"log={log_file}), 线程={t.name}; "
                    f"{self.PROGRESS_WATCH_SECONDS:.0f}s 后将探活, "
                    f"用 status 查看推进情况")

    def _start_stage1(self, total_time: float, log_file: str,
                      target: str, fault: str,
                      scenario: str = "") -> str:
        """启动当前已加载的 stage1/stage2/stage3 场景。

        步骤 1.2 起本方法**不构建、不清场、不加载默认场景**; 调用方必须
        先显式 build 或 load。load 后可直接 start: ``prepare_start()`` 会在
        取得运行锁后执行完整只读校验, 失败时拒绝启动并释放锁。阶段由工件
        `source_phase` 决定, 不接受调用方指定更低的 runner。
        """
        from stage1 import config as s1cfg
        from stage1 import run as s1run
        from stage1.lifecycle import LifecycleError
        from stage2.config import STAGE2_FAULT_CHOICES
        from stage3 import config as s3cfg

        with self._lock:
            if self.is_running():
                return "仿真已在运行中, 先 stop 再启动"
            if self.sim is None:
                return "未连接 CoppeliaSim, 先调用 connect"
            simIK = getattr(self.connector, "simIK", None)
            if simIK is None:
                return ("当前连接缺少 simIK 模块, 无法运行 "
                        "stage1/stage2/stage3")
            manager = self._ensure_lifecycle()
            try:
                prepared = manager.prepare_start(target=target)
            except LifecycleError as exc:
                self.last_error = f"{exc.code}: {exc}"
                return (f"start 被拒绝 ({exc.code}): {exc}; "
                        "请先 build 或 load 一个场景工件")
            artifact = getattr(manager, "artifact", None)
            manifest = getattr(artifact, "manifest", {}) if artifact else {}
            source_phase = manifest.get("source_phase")
            is_stage2 = bool(source_phase == "stage2")
            is_stage3 = bool(source_phase == "stage3")
            scenario_name = ""
            if is_stage3:
                scenario_name = (scenario or
                                 (target if target in s3cfg.STAGE3_SCENARIOS
                                  else s3cfg.SCENARIO_CLOSED_LOOP))
                if scenario_name not in s3cfg.STAGE3_SCENARIOS:
                    manager.finish_run("FAILED",
                                       f"未知 stage3 scenario: {scenario_name}")
                    return (f"stage3 scenario 必须是 "
                            f"{s3cfg.STAGE3_SCENARIOS}: {scenario_name!r}")
                if fault not in s3cfg.STAGE3_FAULT_CHOICES:
                    manager.finish_run("FAILED", f"未知 stage3 fault: {fault}")
                    return (f"stage3 fault 必须是 "
                            f"{s3cfg.STAGE3_FAULT_CHOICES}: {fault!r}")
            else:
                if target not in ("station", "agv", "both"):
                    manager.finish_run("FAILED", f"未知 target: {target}")
                    return f"target 必须是 station/agv/both: {target!r}"
                if is_stage2 and fault not in STAGE2_FAULT_CHOICES:
                    manager.finish_run("FAILED", f"未知 stage2 fault: {fault}")
                    return (f"stage2 fault 必须是 "
                            f"{STAGE2_FAULT_CHOICES}: {fault!r}")
                if not is_stage2 and fault not in s1cfg.FAULT_CHOICES:
                    manager.finish_run("FAILED", f"未知 stage1 fault: {fault}")
                    return (f"stage1 fault 必须是 "
                            f"{s1cfg.FAULT_CHOICES}: {fault!r}")
            self.stage1_scene = manager.scene
            self.stage1_comp = None
            self.stage1_controller = None
            self.stage1_results = []
            self.stage1_report_path = None
            self.stage1_run_id = prepared.get("run_id")
            self.stage1_stop.clear()
            self.stage1_pause.clear()
            self.stage1_pause_ack.clear()
            self.backend = ("stage3" if is_stage3
                            else ("stage2" if is_stage2 else "stage1"))
            self.last_error = None

        logger = setup_logger(log_file=log_file)
        try:
            if is_stage3:
                from stage3.run import build_components_stage3
                comp = build_components_stage3(
                    self.sim, simIK, manager.scene, logger)
            elif is_stage2:
                from stage2.run import build_components_stage2
                comp = build_components_stage2(
                    self.sim, simIK, manager.scene, logger)
            else:
                comp = s1run.build_components(
                    self.sim, simIK, manager.scene, logger)
            s1run.start_stepping(self.sim)
        except Exception as e:  # noqa: BLE001
            manager.finish_run("FAILED", f"{type(e).__name__}: {e}")
            label = ("stage3" if is_stage3
                     else ("stage2" if is_stage2 else "stage1"))
            self.last_error = f"{label} 启动失败: {e}"
            return f"{label} 启动失败: {e}"
        self.stage1_comp = comp

        tt = total_time if total_time and total_time > 0 else 90.0
        if is_stage3:
            runner = self._run_stage3
            args = (scenario_name, fault, tt, logger, manager)
            thread_name = "stage3-mcp-loop"
        elif is_stage2:
            runner = self._run_stage2
            args = (target, fault, tt, logger, manager)
            thread_name = "stage2-mcp-loop"
        else:
            runner = self._run_stage1
            args = (target, fault, tt, logger, manager)
            thread_name = "stage1-mcp-loop"
        t = threading.Thread(target=runner, args=args, name=thread_name,
                             daemon=True)
        self.stage1_runner = t
        t.start()
        backend = self.backend
        label = ("阶段三" if is_stage3
                 else ("阶段二" if is_stage2 else "阶段一"))
        detail = (f"scenario={scenario_name}, " if is_stage3
                  else f"target={target}, ")
        return (f"{label}仿真已启动 (backend={backend}, {detail}"
                f"fault={fault}, 单任务上限={tt:.1f}s, "
                f"run_id={self.stage1_run_id}, "
                f"artifact={manager.session.artifact_id}, log={log_file}); "
                f"线程={t.name}; 用 status 查询, stop_simulation 停止")

    def _on_stage1_controller(self, controller) -> None:
        """run_task 创建控制器后的回调, 供 status 读取实时状态."""
        self.stage1_controller = controller

    def _run_stage1(self, target: str, fault: str,
                    max_sim_time: float, logger, manager) -> None:
        from stage1 import run as s1run

        outcome, error = "DONE", None
        try:
            targets = ["station", "agv"] if target == "both" else [target]
            for idx, tgt in enumerate(targets, start=1):
                if self.stage1_stop.is_set():
                    outcome = "STOP_REQUESTED"
                    break
                evidence, material = s1run.run_task(
                    self.sim, self.stage1_scene, self.stage1_comp,
                    tgt, idx, fault, max_sim_time, logger,
                    stop_event=self.stage1_stop,
                    pause_event=self.stage1_pause,
                    pause_ack_event=self.stage1_pause_ack,
                    on_start=self._on_stage1_controller,
                )
                self.stage1_results.append(evidence)
                if idx < len(targets):
                    self.stage1_scene.remove_material(material)
                    self.stage1_comp["materials"].remove(idx)
            self._write_stage1_report(target, fault)
            if not all(r.get("ok") for r in self.stage1_results):
                outcome = "FAILED"
        except Exception as e:  # noqa: BLE001 - 线程入口需兜底所有异常
            outcome, error = "FAILED", f"{type(e).__name__}: {e}"
            self.last_error = f"stage1 run 异常: {error}"
            logger.exception("[MCP] stage1 run 异常")
        finally:
            try:
                s1run.stop_simulation(self.sim)
            except Exception:
                pass
            try:
                manager.finish_run(outcome, error)
                self.stage1_scene = manager.scene
            except Exception as exc:  # noqa: BLE001
                logger.exception("[MCP] finish_run 失败: %s", exc)
            self.stage1_run_id = None

    def _run_stage2(self, target: str, fault: str,
                    max_sim_time: float, logger, manager) -> None:
        """阶段二 MCP 运行线程: 规划/执行/联锁 + 会话资源清理."""
        from stage1.run import stop_simulation
        from stage2.run import run_task_stage2

        outcome, error = "DONE", None
        try:
            targets = ["station", "agv"] if target == "both" else [target]
            for idx, tgt in enumerate(targets, start=1):
                if self.stage1_stop.is_set():
                    outcome = "STOP_REQUESTED"
                    break
                evidence, material = run_task_stage2(
                    self.sim, self.stage1_scene, self.stage1_comp,
                    tgt, idx, fault, max_sim_time, logger,
                    stop_event=self.stage1_stop,
                    pause_event=self.stage1_pause,
                    pause_ack_event=self.stage1_pause_ack,
                    on_start=self._on_stage1_controller,
                )
                self.stage1_results.append(evidence)
                if idx < len(targets):
                    self.stage1_comp["runtime"].forget_material(material)
                    self.stage1_scene.remove_material(material)
                    self.stage1_comp["materials"].remove(idx)
            self._write_stage2_report(target, fault)
            if not all(r.get("ok") for r in self.stage1_results):
                outcome = "FAILED"
        except Exception as e:  # noqa: BLE001 - 线程入口需兜底所有异常
            outcome, error = "FAILED", f"{type(e).__name__}: {e}"
            self.last_error = f"stage2 run 异常: {error}"
            logger.exception("[MCP] stage2 run 异常")
        finally:
            try:
                stop_simulation(self.sim)
            except Exception:
                pass
            try:
                if self.stage1_comp is not None:
                    self.stage1_comp["runtime"].destroy()
            except Exception as exc:  # noqa: BLE001
                logger.exception("[MCP] stage2 runtime 清理失败: %s", exc)
            try:
                manager.finish_run(outcome, error)
                self.stage1_scene = manager.scene
            except Exception as exc:  # noqa: BLE001
                logger.exception("[MCP] finish_run 失败: %s", exc)
            self.stage1_run_id = None

    def _run_stage3(self, scenario: str, fault: str,
                    max_sim_time: float, logger, manager) -> None:
        """阶段三 MCP 运行线程: 双工位任务链 + 会话资源清理."""
        from stage1.run import stop_simulation
        from stage3.run import run_scenario_stage3

        outcome, error = "DONE", None
        try:
            evidence = run_scenario_stage3(
                self.sim, self.stage1_scene, self.stage1_comp,
                scenario=scenario, max_sim_time=max_sim_time,
                logger=logger, fault=fault,
                stop_event=self.stage1_stop,
                pause_event=self.stage1_pause,
                pause_ack_event=self.stage1_pause_ack,
                on_start=self._on_stage1_controller,
            )
            self.stage1_results.append(evidence)
            self._write_stage3_report(scenario, fault)
            if not evidence.get("ok"):
                outcome = "FAILED"
        except Exception as e:  # noqa: BLE001 - 线程入口需兜底所有异常
            outcome, error = "FAILED", f"{type(e).__name__}: {e}"
            self.last_error = f"stage3 run 异常: {error}"
            logger.exception("[MCP] stage3 run 异常")
        finally:
            try:
                stop_simulation(self.sim)
            except Exception:
                pass
            try:
                if self.stage1_comp is not None and \
                        self.stage1_comp.get("runtime") is not None:
                    self.stage1_comp["runtime"].destroy()
            except Exception as exc:  # noqa: BLE001
                logger.exception("[MCP] stage3 runtime 清理失败: %s", exc)
            try:
                manager.finish_run(outcome, error)
                self.stage1_scene = manager.scene
            except Exception as exc:  # noqa: BLE001
                logger.exception("[MCP] finish_run 失败: %s", exc)
            self.stage1_run_id = None

    def _write_stage3_report(self, scenario: str, fault: str) -> None:
        """把 MCP 触发的阶段三运行结果写入 docs/implementation/runs/."""
        from datetime import datetime
        from stage1 import run as s1run

        os.makedirs(s1run.REPORT_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(
            s1run.REPORT_DIR, f"phase3_mcp_{scenario}_{stamp}.json")
        summary = {
            "timestamp": stamp,
            "backend": "stage3",
            "run_id": self.stage1_run_id,
            "artifact_id": (self.stage1_lifecycle.session.artifact_id
                            if self.stage1_lifecycle is not None else None),
            "scenario": scenario,
            "fault": fault,
            "results": self.stage1_results,
            "all_ok": bool(self.stage1_results) and all(
                r.get("ok") for r in self.stage1_results),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        self.stage1_report_path = path

    def _write_stage2_report(self, target: str, fault: str) -> None:
        """把 MCP 触发的阶段二运行结果写入 docs/implementation/runs/."""
        from datetime import datetime
        from stage1 import run as s1run

        os.makedirs(s1run.REPORT_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(
            s1run.REPORT_DIR, f"phase2_mcp_{target}_{fault}_{stamp}.json")
        summary = {
            "timestamp": stamp,
            "backend": "stage2",
            "run_id": self.stage1_run_id,
            "artifact_id": (self.stage1_lifecycle.session.artifact_id
                            if self.stage1_lifecycle is not None else None),
            "target": target,
            "fault": fault,
            "results": self.stage1_results,
            "all_ok": bool(self.stage1_results) and all(
                r.get("ok") for r in self.stage1_results),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        self.stage1_report_path = path

    def _write_stage1_report(self, target: str, fault: str) -> None:
        """把 MCP 触发的阶段一运行结果写入 docs/implementation/runs/."""
        from datetime import datetime
        from stage1 import run as s1run

        os.makedirs(s1run.REPORT_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(
            s1run.REPORT_DIR, f"phase1_mcp_{target}_{fault}_{stamp}.json")
        summary = {
            "timestamp": stamp,
            "backend": "stage1",
            "run_id": self.stage1_run_id,
            "artifact_id": (self.stage1_lifecycle.session.artifact_id
                            if self.stage1_lifecycle is not None else None),
            "target": target,
            "fault": fault,
            "results": self.stage1_results,
            "all_ok": bool(self.stage1_results) and all(
                r.get("ok") for r in self.stage1_results),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        self.stage1_report_path = path

    def build_scene(self, clear: bool = True,
                    backend: str = "stage1") -> str:
        """构建场景 — stage1 (默认/当前实现) 或 legacy 五岛参考层.

        完成后状态为 SCENE_VALIDATED (构建契约通过); 构建本身没有 manifest,
        不能声称完成工件级全量校验。临时场景不保存时, start 会重新执行
        构建契约检查; 需要完整工件校验时请 build → save。
        """
        if self.is_running():
            return "仿真运行中, 先 stop_simulation"
        try:
            self.ensure_connected()
        except Exception as e:  # noqa: BLE001
            return f"未连接: {e}"

        if backend == "legacy":
            try:
                import reference.build_scene as bs
                if clear:
                    bs.clear_scene(self.sim)
                bs.build_scene(self.sim)
                self.backend = "legacy"
                self.stage1_scene = None
                self.stage1_comp = None
                self.stage1_controller = None
                self.stage1_results = []
                return "场景构建完成 (backend=legacy 五岛)"
            except Exception as e:  # noqa: BLE001
                self.last_error = f"build_scene(legacy) 失败: {e}"
                return f"build_scene(legacy) 失败: {e}"

        if backend != "stage1":
            return f"未知 backend: {backend!r} (可选 stage1/legacy)"
        try:
            manager = self._ensure_lifecycle()
            manager.build(clear=clear)
            scene = manager.scene
        except Exception as e:  # noqa: BLE001
            self.last_error = f"build_scene(stage1) 失败: {e}"
            return f"build_scene(stage1) 失败: {e}"

        self.backend = "stage1"
        self.stage1_scene = scene
        self.stage1_comp = None
        self.stage1_controller = None
        self.stage1_results = []
        self.last_error = None
        report = scene.build_report or {}
        payload = {
            "backend": "stage1",
            "ok": bool(report.get("ok")),
            "lifecycle_state": manager.sm.state.value,
            "artifact_id": manager.session.artifact_id,
            "layout": scene.layout.name if scene.layout else None,
            "joints": len(scene.robot_joints),
            "tool": scene.tool_geometry(),
            "checks": report.get("checks", []),
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    # ── 步骤 1.2 场景工件生命周期 ────────────────────────────
    def save_scene(self, phase: str = "stage1", parent: str | None = None,
                   note: str = "", protected: bool = True) -> str:
        if self.is_running():
            return "仿真运行中, 先 stop_simulation"
        try:
            manager = self._ensure_lifecycle()
            result = manager.save(phase=phase, parent=parent, note=note,
                                  protected=protected)
        except Exception as e:  # noqa: BLE001
            self.last_error = f"save_scene 失败: {e}"
            return f"save_scene 失败: {type(e).__name__}: {e}"
        artifact = getattr(manager, "artifact", None)
        self.backend = ((artifact.manifest.get("source_phase")
                         if artifact is not None else None)
                        or "stage1")
        self.stage1_scene = manager.scene
        self.stage1_comp = None
        return json.dumps({"ok": True, **result}, ensure_ascii=False,
                          indent=2, default=str)

    def load_scene(self, artifact: str = "current",
                   phase: str | None = None) -> str:
        if self.is_running():
            return "仿真运行中, 先 stop_simulation"
        try:
            manager = self._ensure_lifecycle()
            result = manager.load(artifact, phase=phase)
        except Exception as e:  # noqa: BLE001
            self.last_error = f"load_scene 失败: {e}"
            return f"load_scene 失败: {type(e).__name__}: {e}"
        artifact = getattr(manager, "artifact", None)
        self.backend = ((artifact.manifest.get("source_phase")
                         if artifact is not None else None)
                        or "stage1")
        self.stage1_scene = manager.scene
        self.stage1_comp = None
        self.stage1_controller = None
        self.stage1_results = []
        return json.dumps({"ok": True, **result}, ensure_ascii=False,
                          indent=2, default=str)

    def validate_scene(self, artifact: str | None = None) -> str:
        try:
            manager = self._ensure_lifecycle()
            result = manager.validate(artifact)
        except Exception as e:  # noqa: BLE001
            self.last_error = f"validate_scene 失败: {e}"
            return f"validate_scene 失败: {type(e).__name__}: {e}"
        return json.dumps({"ok": True, **result}, ensure_ascii=False,
                          indent=2, default=str)

    def lifecycle_status(self) -> str:
        try:
            manager = self._ensure_lifecycle()
        except Exception as e:  # noqa: BLE001
            return json.dumps({"ok": False, "error": str(e)},
                              ensure_ascii=False)
        try:
            info = manager.status()
        except Exception as e:  # noqa: BLE001
            return json.dumps({"ok": False, "error": str(e),
                               "lifecycle_state": manager.sm.state.value},
                              ensure_ascii=False)
        return json.dumps(info, ensure_ascii=False, indent=2, default=str)

    def stop(self) -> str:
        """停止仿真 — Stop stage1 runner or legacy controller loop."""
        with self._lock:
            # 重置 run_lua lingering 标志 (stop 后场景已重置)
            self._run_lua_lingering = False
            stage1_running = (self.stage1_runner is not None
                              and self.stage1_runner.is_alive())
            legacy_running = (self.sim_thread is not None
                              and self.sim_thread.is_alive())
            manager = self.stage1_lifecycle
            lifecycle_running = (manager is not None and
                                 manager.sm.state.value
                                 == "SIMULATION_RUNNING")
            if not (stage1_running or legacy_running or lifecycle_running):
                # 可能是上一个客户端崩溃后遗留的外部运行/stepping 状态;
                # 显式 stop 允许清理它, 但不会删除场景。
                if manager is not None:
                    try:
                        result = manager.stop()
                        return json.dumps({"ok": True, **result},
                                          ensure_ascii=False, default=str)
                    except Exception as e:  # noqa: BLE001
                        self.last_error = f"stop 异常: {e}"
                        return f"stop 失败: {e}"
                return "仿真未在运行"
            if stage1_running:
                self.stage1_stop.set()

        if stage1_running:
            self.stage1_runner.join(timeout=8.0)
            if self.stage1_runner.is_alive():
                # run_task 每步检查 stop; 若卡在单次调用, 用 stopSimulation 强制退出
                try:
                    self.sim.stopSimulation()
                except Exception as e:  # noqa: BLE001
                    self.last_error = f"stop 异常: {e}"
                self.stage1_runner.join(timeout=3.0)
            self.stage1_pause.clear()
            self.stage1_pause_ack.clear()
            if manager is not None and not self.stage1_runner.is_alive():
                try:
                    manager.finish_run("STOP_REQUESTED")
                except Exception as e:  # noqa: BLE001
                    self.last_error = f"finish_run 异常: {e}"
            return (f"已停止 (backend={self.backend})"
                    if not self.stage1_runner.is_alive()
                    else f"停止信号已发, {self.backend} 线程仍在收尾")

        if manager is not None and lifecycle_running:
            # 由其他进程 (CLI) 启动的运行: 只停止物理引擎并等待其收尾;
            # 运行锁由持有进程释放, 本服务不抢占。
            try:
                result = manager.stop()
            except Exception as e:  # noqa: BLE001
                self.last_error = f"stop 异常: {e}"
                return f"stop 失败: {e}"
            return json.dumps({"ok": True, **result},
                              ensure_ascii=False, default=str)

        try:
            # 主动停止 CoppeliaSim 仿真 → run() 内 sim.step() 将抛异常退出
            self.sim.stopSimulation()
        except Exception as e:  # noqa: BLE001
            self.last_error = f"stop 异常: {e}"

        # 释放锁, 等待线程退出 (最多 5s)
        if self.sim_thread is not None:
            self.sim_thread.join(timeout=5.0)
        return "已停止 (backend=legacy)" if not self.is_running() \
            else "停止信号已发, 线程仍在收尾"

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
        if running and self.backend in ("stage1", "stage2", "stage3"):
            # 主连接被 stage1 runner 独占; 探活会与主循环抢锁, 直接视为已连接。
            connected = True
        else:
            connected = (self.connector is not None
                         and self.connector.check_alive()
                         ) if self.connector else False
        info: dict[str, Any] = {
            "connected": connected,
            "running": running,
            "backend": self.backend,
            "host": self.connector.host if self.connector else None,
            "port": self.connector.port if self.connector else None,
            "last_error": self.last_error,
        }
        if self.backend in ("stage1", "stage2", "stage3") \
                or self.stage1_scene is not None:
            info["backend"] = self.backend
            info["scene_built"] = self.stage1_scene is not None
            info["paused"] = self.stage1_pause.is_set()
            if self.stage1_scene is not None:
                report = self.stage1_scene.build_report or {}
                info["build_ok"] = bool(report.get("ok"))
                info["layout"] = (self.stage1_scene.layout.name
                                  if self.stage1_scene.layout else None)
                info["tool_version"] = cfg_suction_tool_version()
            ctrl = self.stage1_controller
            if self.backend == "stage3" and ctrl is not None \
                    and hasattr(ctrl, "queue"):
                info.update({
                    "scenario": ctrl.scenario,
                    "fault": ctrl.fault,
                    "sim_time": round(ctrl._sim_time(), 3),
                    "aborted": bool(ctrl.aborted),
                    "stop_reason": ctrl.stop_reason,
                    "task_states": {
                        task.task_id: task.status.value
                        for task in ctrl.queue.all()},
                    "resources": ctrl.resources.snapshot(),
                })
                ctrl = None
            if ctrl is not None:
                sim_time = float(ctrl.sim_time)
                if self.stage1_pause.is_set():
                    # runner 已暂停, 连接空闲; 报告手动 step 后的真实时间。
                    try:
                        sim_time = float(self.sim.getSimulationTime())
                    except Exception:
                        pass
                info.update({
                    "sim_time": round(sim_time, 3),
                    "unit_state": ctrl.state.name,
                    "failure_reason": ctrl.failure_reason,
                    "uid": ctrl.uid,
                    "target": ctrl.target,
                    "fault": ctrl.fault,
                    "agv_blocked": bool(getattr(ctrl, "agv_blocked", False)),
                    "compensation_count": int(
                        getattr(ctrl, "compensation_count", 0)),
                })
                rec = (ctrl.materials.by_uid(ctrl.uid)
                       if ctrl.materials is not None else None)
                if rec is not None:
                    info["material_state"] = rec.state.value
                    info["material_handle"] = rec.sim_handle
                agv = getattr(ctrl, "agv", None)
                if agv is not None:
                    info["agv_state"] = getattr(
                        agv.state, "name", str(agv.state))
                if not running:
                    # 运行中不额外访问主连接; 暂停/结束后再读带速。
                    try:
                        aliases = ctrl.conveyor.aliases()
                        if aliases:
                            info["conveyor_velocity"] = round(float(
                                ctrl.conveyor.read_actual_velocity(
                                    aliases[0])), 4)
                    except Exception:
                        pass
            if self.stage1_results:
                info["results"] = self.stage1_results
            if self.stage1_report_path:
                info["report_path"] = self.stage1_report_path
            manager = self.stage1_lifecycle
            if manager is not None:
                # 运行中不访问主连接; 这里只读内存/会话状态。
                try:
                    lock_owner = manager.run_lock.owner()
                except Exception:
                    lock_owner = None
                info.update({
                    "lifecycle_state": manager.sm.state.value,
                    "artifact_id": getattr(manager.session,
                                           "artifact_id", None),
                    "scene_origin": getattr(manager.session,
                                            "scene_origin", None),
                    "manifest_digest": getattr(manager.session,
                                               "manifest_digest", None),
                    "validated_at": getattr(manager.session,
                                            "validated_at", None),
                    "run_id": getattr(manager.session, "run_id", None),
                    "start_in_progress": bool(
                        lock_owner and manager.sm.state.value
                        != "SIMULATION_RUNNING"),
                    "allowed_actions": manager.allowed_actions(),
                })
            return info

        ctrl = self.controller
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


def cfg_suction_tool_version() -> Optional[str]:
    """stage1 工具版本 (供 status 显示, 避免在 status 内重复 import)."""
    try:
        from stage1 import config as s1cfg
        return getattr(s1cfg, "SUCTION_TOOL_VERSION", None)
    except Exception:
        return None


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
def build_scene(clear: bool = True, backend: str = "stage1") -> str:
    """显式构建仿真场景 (默认当前阶段一实现)。

    backend="stage1" (默认): 构建单工位阶段一场景 (内置 conveyor + UR5 +
    suction pad + p3dx), 执行构建契约校验, 生命周期状态变为
    SCENE_VALIDATED (scene_origin=BUILT)。构建本身没有工件 manifest;
    需要完整工件校验时请继续 save_scene。
    backend="legacy": 构建旧五岛参考场景 (reference.build_scene)。

    前置: 已 connect 且仿真处于停止态。start_simulation 不会调用本工具。

    Args:
        clear:   是否先清空场景, 默认 True (建议保持)。
        backend: 场景实现, "stage1" (默认) 或 "legacy"。

    Returns:
        构建结果摘要 (JSON) 或失败原因。
    """
    return server.build_scene(clear=clear, backend=backend)


@mcp.tool()
def save_scene(phase: str = "stage1", parent: str | None = None,
               note: str = "", protected: bool = True) -> str:
    """把当前已校验的停止态场景保存为带 manifest 的新版本工件。

    保存后立即重新加载并只读校验; 校验通过才原子更新 current 指针。
    阶段一工件默认 protected=True, 不允许覆盖。

    Args:
        phase:     命名空间: "stage1" / "stage2" / "stage3" / "diagnostic"。
        parent:    父工件 ID (阶段二从 stage1、阶段三从 stage2 派生时必填)。
        note:      版本说明。
        protected: 是否设置为只读基线。

    Returns:
        JSON (artifact_id / 路径 / 校验摘要) 或失败原因。
    """
    return server.save_scene(phase=phase, parent=parent, note=note,
                             protected=protected)


@mcp.tool()
def load_scene(artifact: str = "current", phase: str | None = None) -> str:
    """在仿真停止态加载指定场景工件 (.ttt) 并重新解析句柄。

    ``artifact="current"`` 时读取
    ``docs/scene_artifacts/<phase>/current.json`` 的 ``artifact_id``，
    再加载对应工件目录的 ``scene.ttt``（``phase`` 默认 ``stage1``）。
    不自动改写、重建或升级模型，也不读取 ``factory-sim.ttt``/``scenes``；
    加载后可直接 start_simulation，启动点会在取得运行锁后执行完整只读
    校验；validate_scene 仅用于可选的主动检查/差异报告。

    Args:
        artifact: 工件 ID (如 stage1/L1_S1/v001_...) / 目录名 / current。
        phase:    仅在按短名/current 查找时限定命名空间。

    Returns:
        JSON (lifecycle_state=SCENE_LOADED, validation_at_start=true)。
    """
    return server.load_scene(artifact=artifact, phase=phase)


@mcp.tool()
def validate_scene(artifact: str | None = None) -> str:
    """只读校验当前已加载场景与 manifest (可选诊断; 不修复/不补建)。

    对比场景文件摘要、配置摘要、模型摘要、仿真器版本/引擎、对象
    alias/类型/父级/位姿和场景契约。任何影响行为的差异都会拒绝 start。
    start_simulation 在启动点总会重新执行同一套完整校验; 本工具用于
    主动检查并查看详细差异报告, 不是 start 的前置条件。

    Args:
        artifact: 可选; 必须与当前已加载工件一致。

    Returns:
        JSON {ok, checks, differences, ...}。
    """
    return server.validate_scene(artifact=artifact)


@mcp.tool()
def lifecycle_status() -> str:
    """查询场景生命周期状态、当前工件、运行锁和可用动作。

    Returns:
        JSON {lifecycle_state, artifact_id, scene_origin, run_id,
              allowed_actions, artifacts, ...}。
    """
    return server.lifecycle_status()


@mcp.tool()
def start_simulation(total_time: float = 0.0,
                     log_file: str = "factory_sim.log",
                     backend: str = "stage1",
                     target: str = "station",
                     fault: str = "none",
                     scenario: str = "") -> str:
    """在后台线程启动仿真主循环。

    backend="stage1" (默认/当前实现): **不重建场景**; 只启动当前已加载
    的场景 (build 或 load 后均可)。启动点会取得运行锁并执行完整只读
    校验, 失败则拒绝启动; validate_scene 是可选诊断, 不是前置条件。
    若工件 source_phase=stage2/stage3, 由同一生命周期/运行锁路由到
    阶段二/阶段三 runner。
    没有已加载场景时返回明确错误, 请先 build_scene 或 load_scene。
    backend="legacy": 运行旧五岛 FactoryController。

    前置: 已 connect, 仿真停止, 当前场景已加载, 无其他控制循环持有
    运行锁; 启动点校验通过后才进入运行。

    Args:
        total_time: stage1 为单任务最大仿真时间 (s, 0→默认 90);
                    legacy 为仿真总时长 (0=无限直到 stop)。
        log_file:   日志文件路径。
        backend:    "stage1" (默认) 或 "legacy"。
        target:     stage1 目标: "station" / "agv" / "both";
                    stage3 也可直接传场景名。
        fault:      stage1/stage2/stage3 故障注入 (见各阶段 FAULT_CHOICES)。
        scenario:   stage3 场景: closed_loop / contention / blocked /
                    unstable_unload / route_baseline。

    Returns:
        启动结果描述 (含 run_id 与 artifact_id)。
    """
    return server.start(total_time, log_file, backend=backend,
                        target=target, fault=fault, scenario=scenario)


@mcp.tool()
def stop_simulation() -> str:
    """停止正在运行的仿真。

    向 CoppeliaSim 发送 stopSimulation, 触发主循环退出, 最多等待 5s。

    Returns:
        停止结果描述。
    """
    return server.stop()


@mcp.tool()
def step_simulation(n: int = 1) -> str:
    """单步或多步推进仿真 (stepping 模式)。

    legacy: 仿真运行中可直接推进;
    stage1: 必须先 pause_simulation, 否则会与守护线程重复步进。
    n 上限 1000 防失控。

    Args:
        n: 推进步数, 默认 1, 上限 1000。
    Returns:
        末步仿真时间 + 实际步数, 或失败原因。
    """
    if server.sim is None:
        return "未连接 CoppeliaSim, 先调用 connect"
    stage1_paused = (server.backend == "stage1"
                     and server.stage1_pause.is_set())
    if server.backend == "stage1" and not stage1_paused:
        if not server.is_running():
            return "仿真未运行, 先调用 start_simulation"
        return "stage1 运行中, 先 pause_simulation 再单步"
    if server.backend != "stage1" and not server.is_running():
        return "仿真未运行, 先调用 start_simulation"
    try:
        n = max(1, min(int(n), 1000))
    except (TypeError, ValueError):
        return "失败: n 必须为整数"
    try:
        for _ in range(n):
            server.sim.step()
        t = server.sim.getSimulationTime()
        return f"已推进 {n} 步, sim_time={t:.3f}s"
    except Exception as e:  # noqa: BLE001
        server.last_error = f"step 异常: {e}"
        return f"step 失败: {e}"


@mcp.tool()
def pause_simulation() -> str:
    """暂停仿真控制逻辑。

    stage1: 置 stage1_pause 事件, runner 停止访问 ZMQ 连接; 暂停期间用
    step_simulation 手动推进物理, resume 后恢复自动循环。
    legacy: 置 controller._pause_requested=True, 物理仍推进。
    resume 清除。

    Returns:
        操作结果 + 暂停时 sim_time。
    """
    if not server.is_running():
        return "仿真未运行, 先调用 start_simulation"
    if server.backend == "stage1":
        server.stage1_pause.set()
        acked = server.stage1_pause_ack.wait(timeout=2.0)
        ctrl = server.stage1_controller
        t = float(getattr(ctrl, "sim_time", 0.0)) if ctrl is not None else 0.0
        tail = ("runner 已停止访问连接" if acked
                else "等待 runner 暂停超时, 暂勿 step")
        return (f"已请求暂停 stage1 控制逻辑 @ sim_time={t:.3f}s; {tail}; "
                "用 step_simulation 手动推进, resume_simulation 恢复")
    ctrl = server.controller
    if ctrl is None:
        return "失败: controller 不存在"
    ctrl._pause_requested = True
    ctrl._pause_at_sim_time = server.sim.getSimulationTime()
    return (f"已请求暂停控制逻辑 @ sim_time="
            f"{ctrl._pause_at_sim_time:.3f}s; 物理 engine 仍推进, "
            "用 resume_simulation 恢复")


@mcp.tool()
def resume_simulation() -> str:
    """恢复仿真控制逻辑 (清除 pause 标志)。

    Returns:
        操作结果。
    """
    if not server.is_running():
        return "仿真未运行"
    if server.backend == "stage1":
        server.stage1_pause.clear()
        server.stage1_pause_ack.clear()
        return "已恢复 stage1 控制逻辑 (pause 标志已清除)"
    ctrl = server.controller
    if ctrl is None:
        return "失败: controller 不存在"
    ctrl._pause_requested = False
    return "已恢复控制逻辑 (pause 标志已清除)"


@mcp.tool()
def status() -> str:
    """查询仿真实时状态。

    stage1: backend/running/paused/sim_time/unit_state/failure_reason/uid/
    material_state/agv_state/conveyor_velocity/results/report_path;
    legacy: 连接/运行/仿真时间/步数/装配数/AGV/各岛状态/最近错误。

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
def get_config(backend: str = "stage1") -> str:
    """读取当前仿真配置。

    backend="stage1" (默认): stage1.config 的位置/速度/吸附/工具安装参数;
    backend="legacy": 旧五岛 factory_config (节拍/路径/岛配置)。

    Returns:
        JSON 格式的配置摘要。
    """
    import json
    if backend == "stage1":
        from stage1 import config as s1cfg
        keys = [
            "SIM_STEP", "CONVEYOR_SPEED", "CONVEYOR_ACCEL",
            "SUCTION_CONTACT_TOL", "SUCTION_MAX_ATTACH_SPEED",
            "SUCTION_MAX_TOOL_SPEED", "SUCTION_TILT_TOL",
            "SUCTION_RELEASE_MAX_SPEED", "SUCTION_SETTLE_STEPS",
            "SUCTION_MOUNT_Z", "SUCTION_MOUNT_QUATERNION",
            "SUCTION_MOUNT_POS_TOL", "SUCTION_TOOL_VERSION",
            "TCP_LOCAL_POS", "TCP_LOCAL_QUATERNION",
            "ARM_SEED_JOINTS", "ARM_TCP_POS_TOL", "ARM_TCP_ANG_TOL",
            "AGV_WHEEL_RADIUS", "AGV_WHEEL_BASE",
            "MATERIAL_SIZE", "MATERIAL_HALF_HEIGHT",
            "ALIASES", "REQUIRED_ALIASES", "FAULT_CHOICES",
        ]
        out = {k: getattr(s1cfg, k, "<未定义>") for k in keys}
        return json.dumps(out, ensure_ascii=False, indent=2, default=str)
    keys = [
        "FLOOR_SIZE", "FLOOR_POS", "SIM_STEP",
        "FLOOR_TOP_Z", "ISLAND_TOP_Z", "ISLAND_TOP_Z_E",
        "CONVEYOR_SURFACE_Z", "MATERIAL_CENTER_Z", "AGV_DECK_Z",
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
def set_config(key: str, value: Any, backend: str = "stage1") -> str:
    """动态修改单个仿真配置参数 (内存级)。

    警告: 已构建的场景对象不会自动更新; stage1 的几何/安装参数
    (SUCTION_MOUNT_*, CONVEYOR_* 等) 需要重新 build_scene 才生效。
    backend="stage1" (默认) 修改 stage1.config; backend="legacy" 修改
    reference.factory_config。

    Args:
        key:     配置属性名, 如 "CONVEYOR_SPEED"; 对字典用点号:
                 key="ALIASES.conveyor" 或 legacy 的 "TAKT_TIME.A"。
        value:   新值 (数字/字符串/列表/字典)。
        backend: "stage1" (默认) 或 "legacy"。

    Returns:
        修改结果描述 (旧值→新值)。
    """
    try:
        if backend == "stage1":
            from stage1 import config as target_cfg
        elif backend == "legacy":
            target_cfg = cfg
        else:
            return f"未知 backend: {backend!r} (可选 stage1/legacy)"
        if "." in key:
            mod_attr, dict_key = key.split(".", 1)
            container = getattr(target_cfg, mod_attr)
            old = container.get(dict_key, "<无>")
            container[dict_key] = value
            return (f"{backend}.{mod_attr}['{dict_key}']: "
                    f"{old!r} → {value!r}")
        old = getattr(target_cfg, key, "<未定义>")
        setattr(target_cfg, key, value)
        return f"{backend}.{key}: {old!r} → {value!r}"
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


def _console_log_path() -> Path:
    """CoppeliaSim 控制台日志路径 (可由环境变量覆盖, 便于测试)。"""
    return Path(os.environ.get("COPPELIA_CONSOLE_LOG",
                               "coppelia_console.log"))


def _read_console_since(path: Path, offset: int) -> dict:
    """从字节偏移读取控制台日志增量。

    偏移越界或为负时 reset=True 并从头返回, 避免日志轮转后丢内容。
    """
    size = path.stat().st_size
    reset = offset < 0 or offset > size
    start = 0 if reset else int(offset)
    with path.open("rb") as f:
        f.seek(start)
        data = f.read()
    return {
        "content": data.decode("utf-8", errors="replace").replace("\r\n", "\n"),
        "next_offset": size,
        "file_size": size,
        "reset": reset,
    }


@mcp.tool()
def tail_coppelia_console(lines: int = 50, offset: int | None = None) -> str:
    """读取 CoppeliaSim 控制台实时日志 (经 ConPTY relay 落盘)。

    与 tail_log 的区别: 本工具读 CoppeliaSim 自身的控制台输出 (脚本 print /
    sim.addLog、插件与 add-on 报错、冻结前最后一条消息); tail_log 读 Python
    控制层日志。控制台日志只有经 tools/conpty_relay.py 启动时才可靠存在。

    Args:
        lines: offset 为空时读取的末尾行数, 默认 50。
        offset: 给定字节偏移时做增量读取, 只返回该偏移之后的新内容。
                首次传 0; 之后传上次返回的 next_offset 即可持续跟随。

    Returns:
        offset 为空: 末尾文本, 或日志不存在时的提示。
        offset 给定: JSON {ok, content, next_offset, file_size, reset};
                     文件被截断/轮转时 reset=true 且从头返回。
    """
    p = _console_log_path()
    if not p.exists():
        return (f"控制台日志 {p} 不存在。"
                "请用 tools/conpty_relay.py 以 ConPTY 启动 CoppeliaSim。")
    try:
        if offset is None:
            all_lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            return "\n".join(all_lines[-max(1, int(lines)):])
        result = _read_console_since(p, int(offset))
        result["ok"] = True
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return f"读取控制台日志失败: {e}"


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
    if server.stage1_lifecycle is not None:
        try:
            server.stage1_lifecycle.disconnect()
        except Exception:
            pass
        server.stage1_lifecycle = None
    server.connector = None
    server.controller = None
    server.sim_thread = None
    server.backend = None
    server.stage1_scene = None
    server.stage1_comp = None
    server.stage1_controller = None
    server.stage1_runner = None
    server.stage1_stop.clear()
    server.stage1_pause.clear()
    server.stage1_pause_ack.clear()
    server.stage1_results = []
    server.stage1_report_path = None
    server.stage1_run_id = None
    # ro_connector 与 _run_lua_lingering 的清零必须持有 ro_lock:
    # 否则并发诊断工具 (get_object_pose 等) 在 ensure_ro_connected() 与
    # sim = server.ro_sim 之间看到 ro_connector 变 None → sim=None → 抛
    # AttributeError, 误报"失败"。同时 reset lingering 避免 run_lua 超时
    # 后未 stop 即 disconnect, 重连后 lingering 永久为 True 导致 run_lua 被砖。
    with server.ro_lock:
        server.ro_connector = None
        server._run_lua_lingering = False
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
