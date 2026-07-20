"""
factory_core.py — 仿真基础设施层 (ZMQ 通信 + 步进循环 + 设备句柄 + 日志)
======================================================================
本模块封装与 CoppeliaSim 的底层通信、步进式仿真循环、场景对象句柄映射
以及结构化日志配置。这是三层架构中的"通信与基础设施层"。

对应架构层: ZeroMQ Remote API (synchronous, stepping mode)
参考文档:   references/control-framework.md

Exports:
  Functions:  setup_logger()
  Classes:    CoppeliaConnector, ZMQMonitor, SimulationLoop, DeviceHandleMap
"""

import logging
import sys
import time as wall_time
from typing import Optional

from coppeliasim_zmqremoteapi_client import RemoteAPIClient

import factory_config as cfg


# ═══════════════════════════════════════════════════════════════
# 日志配置 — Logger Configuration
# ═══════════════════════════════════════════════════════════════

def setup_logger(name: str = "factory-sim",
                 log_file: str = "factory_sim.log") -> logging.Logger:
    """配置结构化日志器 — Configure structured dual-output logger.

    File handler (DEBUG level): 记录所有事件到日志文件，用于事后分析.
    Console handler (INFO level): 仅关键信息输出到终端.

    Log level usage convention:
      DEBUG   — 每步位置更新、状态转换细节
      INFO    — 物料产出/到达、AGV 任务起止、装配事件
      WARNING — 节拍偏差、重连尝试、队列接近上限
      ERROR   — 超时、死锁、连接丢失、恢复失败

    Args:
        name:     日志器名称, 默认 "factory-sim".
        log_file: 日志文件路径, 默认 "factory_sim.log".

    Returns:
        配置完成的 logging.Logger 实例.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    # 文件处理器 — 完整记录
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)-7s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(fh)

    # 控制台处理器 — INFO 及以上
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("[%(levelname)-7s] %(message)s"))
    logger.addHandler(ch)

    return logger


# ═══════════════════════════════════════════════════════════════
# ZMQ 连接管理器 — CoppeliaConnector
# ═══════════════════════════════════════════════════════════════

class CoppeliaConnector:
    """ZMQ 连接管理器 — ZMQ Connection Manager with auto-reconnect.

    Wraps the RemoteAPIClient and provides retry-capable connection
    establishment and health checking. Uses exponential backoff between
    retry attempts.

    Collaborators:
      - ZMQMonitor: 调用 check_alive() / reconnect_if_needed() 进行健康监控.
      - SimulationLoop: 提供 sim 句柄以执行步进控制.

    Attributes:
        host:         CoppeliaSim 主机地址.
        port:         ZMQ Remote API 端口 (默认 23000).
        max_retries:  最大重试次数.
        timeout:      单次连接超时 (秒).
        client:       RemoteAPIClient 实例.
        sim:          Remote API "sim" 模块句柄.
    """

    def __init__(self, host: str = "localhost", port: int = 23000,
                 max_retries: int = cfg.ZMQ_MAX_RETRIES,
                 timeout: float = cfg.ZMQ_TIMEOUT):
        self.host = host
        self.port = port
        self.max_retries = max_retries
        self.timeout = timeout
        self.client = None
        self.sim = None

    def connect(self) -> bool:
        """建立连接 — Establish ZMQ connection with retry logic.

        Attempts up to max_retries connections with exponential backoff
        (1s × attempt number). On success, verifies the connection by
        reading the CoppeliaSim version string.

        Returns:
            True 表示连接成功, False 表示全部重试均失败.
        """
        for attempt in range(1, self.max_retries + 1):
            try:
                self.client = RemoteAPIClient(host=self.host, port=self.port)
                self.sim = self.client.require("sim")
                version = self._read_version()
                if version:
                    print(f"[OK] 已连接 CoppeliaSim {version} (attempt {attempt})")
                else:
                    print(f"[OK] 已连接 CoppeliaSim @ {self.host}:{self.port} "
                          f"(attempt {attempt}, 版本信号未设置)")
                return True
            except Exception as e:
                print(f"[WARN] 连接尝试 {attempt}/{self.max_retries} 失败: {e}")
                if attempt < self.max_retries:
                    wall_time.sleep(1.0 * attempt)
        print(f"[FATAL] 无法连接 CoppeliaSim (共 {self.max_retries} 次尝试)")
        return False

    def _read_version(self) -> Optional[str]:
        """读取 CoppeliaSim 版本字符串 — Read version string, tolerant of None.

        部分 CoppeliaSim 实例/配置下 coppeliaSimVersion 信号未设置
        (getStringSignal 返回 None), 此时不影响连接有效性, 仅返回空串。
        """
        try:
            v = self.sim.getStringSignal("coppeliaSimVersion")
            return v if isinstance(v, str) and v else ""
        except Exception:
            return ""

    def check_alive(self) -> bool:
        """快速健康检查 — Quick liveness check.

        首选读取 coppeliaSimVersion 信号; 该信号在部分实例未设置 (返回 None)
        时回退到 getSimulationState()——后者调用成功不抛异常即证明 ZMQ 通路
        健康。任一探针成功即判定连接存活。

        Returns:
            True 表示连接正常, False 表示连接已断开.
        """
        try:
            v = self.sim.getStringSignal("coppeliaSimVersion")
            if isinstance(v, str) and v:
                return True
            # 版本信号未设置 → 回退探针: 仿真状态码调用成功即活
            self.sim.getSimulationState()
            return True
        except Exception:
            return False

    def reconnect_if_needed(self) -> bool:
        """连接断开时尝试重连 — Reconnect on demand if connection is dead.

        First checks if connection is still alive; if not, attempts a full
        reconnect sequence.

        Returns:
            True 表示连接正常 (原本正常或重连成功), False 表示重连失败.
        """
        if self.check_alive():
            return True
        print("[WARN] ZMQ 连接丢失，尝试重连...")
        return self.connect()


# ═══════════════════════════════════════════════════════════════
# ZMQ 通信监控 — ZMQMonitor
# ═══════════════════════════════════════════════════════════════

class ZMQMonitor:
    """ZMQ 连接健康监控器 — ZMQ Connection Health Monitor.

    每个仿真步长调用 check() 以检测连接状态.
    连续失败超过阈值时判定为 "dead"，触发紧急停止.

    参考: references/exception-handling.md §1. Communication Exception

    Collaborators:
      - CoppeliaConnector: 执行实际的重连操作.
      - ExceptionHandler: 消费 check() 返回值以决定是否继续仿真.

    Attributes:
        connector:           关联的 CoppeliaConnector 实例.
        timeout:             无响应判定超时 (秒).
        max_failures:        最大连续失败次数 (超过后判定 dead).
        last_response:       最后一次成功响应的仿真时间.
        consecutive_failures: 当前连续失败计数.
        comm_paused:          True 表示通信暂停 (跳过本步进的控制逻辑).
    """

    def __init__(self, connector: CoppeliaConnector,
                 timeout: float = cfg.ZMQ_TIMEOUT,
                 max_failures: int = cfg.ZMQ_MAX_RETRIES,
                 logger: logging.Logger = None):
        self.connector = connector
        self.timeout = timeout
        self.max_failures = max_failures
        self.logger = logger
        self.last_response: float = 0.0
        self.consecutive_failures: int = 0
        self.comm_paused: bool = False

    def check(self, sim_time: float) -> str:
        """检查连接健康 — Poll ZMQ connection status.

        应在每个仿真步进之前调用。若检测到断连，自动尝试重连。
        连续失败达到 max_failures 时返回 "dead"。

        Args:
            sim_time: 当前仿真时间 (用于判断无响应时长).

        Returns:
            "ok"           — 连接正常.
            "reconnecting" — 正在重连中，建议跳过本步进的控制逻辑.
            "dead"         — 连接彻底丢失，仿真应紧急停止.
        """
        if self.connector.check_alive():
            self.last_response = sim_time
            self.consecutive_failures = 0
            if self.comm_paused:
                if self.logger:
                    self.logger.info("[RECOVER] ZMQ 连接已恢复，继续执行")
                self.comm_paused = False
            return "ok"

        elapsed = sim_time - self.last_response
        if elapsed > self.timeout:
            self.consecutive_failures += 1
            msg = (f"[WARN] ZMQ 无响应 {elapsed:.1f}s "
                   f"(失败 {self.consecutive_failures}/{self.max_failures})")
            if self.logger:
                self.logger.warning(msg)
            else:
                print(msg)

            if self.consecutive_failures >= self.max_failures:
                if self.logger:
                    self.logger.critical("[FATAL] ZMQ 连接丢失 — 无法恢复")
                return "dead"

            if self.connector.reconnect_if_needed():
                self.consecutive_failures = 0
                self.comm_paused = False
                return "ok"
            else:
                self.comm_paused = True
                return "reconnecting"

        return "ok"


# ═══════════════════════════════════════════════════════════════
# 步进式仿真循环 — SimulationLoop
# ═══════════════════════════════════════════════════════════════

class SimulationLoop:
    """时间驱动的步进式仿真循环 — Time-Driven Stepping Simulation Loop.

    Python 端始终是时钟主导者 (clock master)。每步通过 sim.step() 触发
    CoppeliaSim 物理引擎前进，确保控制逻辑与物理引擎严格同步。
    这是精确工业节拍时序的基础。

    参考: references/control-framework.md §Stepping Mode: The Clock Master Pattern

    Attributes:
        conn:       CoppeliaConnector 实例.
        sim:        CoppeliaSim Remote API "sim" 模块.
        sim_step:   仿真步长 (秒).
        sim_time:   当前仿真时间 (秒).
        step_count: 已执行的步数.
        running:    True 表示仿真正在运行.
    """

    def __init__(self, connector: CoppeliaConnector):
        self.conn = connector
        self.sim = connector.sim
        self.sim_step = cfg.SIM_STEP
        self.sim_time = 0.0
        self.step_count = 0
        self.running = False

    def start(self):
        """启动步进模式 — Enable stepping mode and start simulation.

        调用 sim.setStepping(True) 后，仿真不会自动推进，
        每次 sim.step() 推进一个 sim_step 时长。
        """
        self.sim.setStepping(True)
        self.sim.startSimulation()
        self.running = True
        print(f"[INFO] 步进模式已启用, dt={self.sim_step}s")

    def step(self) -> float:
        """执行一个仿真步进 — Advance simulation by one step.

        Returns:
            当前仿真时间 (秒).
        """
        self.sim.step()
        self.sim_time = self.sim.getSimulationTime()
        self.step_count += 1
        return self.sim_time

    def stop(self):
        """优雅停止仿真 — Gracefully stop the simulation."""
        if self.running:
            self.sim.stopSimulation()
            self.running = False


# ═══════════════════════════════════════════════════════════════
# 设备句柄映射 — DeviceHandleMap
# ═══════════════════════════════════════════════════════════════

class DeviceHandleMap:
    """场景对象句柄映射 — Scene Object Handle Map.

    在启动时按规范路径批量获取所有场景对象的整数句柄，缓存到字典中。
    避免每个步进重复调用 sim.getObject() (每次都是 ZMQ 往返).

    参考: references/control-framework.md §Device Handle Mapping

    Attributes:
        sim:     CoppeliaSim Remote API "sim" 模块.
        handles: 路径 → 整数句柄 的映射字典.
    """

    def __init__(self, sim):
        self.sim = sim
        self.handles: dict[str, int] = {}

    def populate(self):
        """批量加载所有已知对象的句柄 — Populate handle map.

        从 factory_config 中读取所有仓库、工作岛、传送带和 AGV 的规范路径，
        逐一调用 sim.getObject() 获取句柄。缺失的对象打印 WARNING 但不会中断.
        """
        paths = []
        for wh in cfg.WAREHOUSES.values():
            paths.append(wh["alias"])
        for isl in cfg.WORK_ISLANDS.values():
            paths.append(isl["alias"])
            paths.append(f"{isl['alias']}_Pillar")
        for cv in cfg.CONVEYORS.values():
            paths.append(cv["alias"])
        paths.append(cfg.AGV_ALIAS)

        # CoppeliaSim 的 sim.getObject(query) 按路径查询, 路径须以 '/' 开头
        # 表示从场景根开始; '/' 是 API 的路径分隔符, 不是对象别名的一部分。
        # 对象别名本身不含 '/' (见 CLAUDE.md「已知问题与坑」第 1 条),
        # 故查询时在无斜杠别名前加 '/'。
        for path in paths:
            try:
                handle = self.sim.getObject("/" + path)
                self.handles[path] = handle
            except Exception as e:
                print(f"[WARN] 找不到对象 '{path}': {e}")

        print(f"[OK] 句柄映射已加载: {len(self.handles)} 个对象")

    def get(self, path: str) -> int:
        """获取句柄 — Get handle by canonical path.

        Raises:
            KeyError: 若路径不在映射中.
        """
        if path not in self.handles:
            raise KeyError(f"对象 '{path}' 不在句柄映射中")
        return self.handles[path]

    def safe_get(self, path: str) -> Optional[int]:
        """安全获取句柄 — Get handle, returning None if missing."""
        return self.handles.get(path)
