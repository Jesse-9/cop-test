"""
run_simulation.py — 岛式车间柔性生产仿真控制器
================================================
基于 CoppeliaSim 4.10+ ZeroMQ Remote API，实现：
  - 步进式仿真同步 (stepping mode, Python 端主导)
  - 共享 AGV 柔性调度 (优先级 + 抢占 + 饥饿升级)
  - 工业节拍对齐与自动降级恢复 (TAKT_TIME)
  - 四类异常处理 (ZMQ断连 / 物流拥堵 / 工位超时 / 物料溢出)
  - 结构化日志 (DEBUG/INFO/WARNING/ERROR)

运行前请确保:
  1. CoppeliaSim 已启动且已加载工厂场景 (先运行 build_scene.py)
  2. ZMQ Remote API 服务已激活

用法:
  uv run python run_simulation.py [--host HOST] [--port PORT] [--time TOTAL_TIME]
"""

import argparse
import json
import logging
import math
import sys
import time as wall_time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Optional

from coppeliasim_zmqremoteapi_client import RemoteAPIClient

import factory_config as cfg


# ═══════════════════════════════════════════════════════════════
# 类型定义 — Type Definitions
# ═══════════════════════════════════════════════════════════════

class ItemType(Enum):
    """物料类型 — 对应场景中的不同工件。"""
    RAW_A     = auto()   # S1→A 原料
    RAW_B     = auto()   # S1→B 原料
    SEMI_AB   = auto()   # B→A 转运半成品
    SEMI_AC   = auto()   # A→C 半成品
    RAW_D     = auto()   # S2→D 零部件
    SEMI_CE   = auto()   # C→E 半成品
    PART_DE   = auto()   # D→E 专用件
    FINAL     = auto()   # 最终成品


class AGVState(Enum):
    """AGV 柔性调度状态机"""
    IDLE          = auto()
    MOVING_B_TO_A = auto()
    MOVING_D_TO_E = auto()
    BLOCKED       = auto()


class AGVTask(Enum):
    """AGV 任务类型"""
    B_TO_A = auto()   # 高优先级 — 跨岛补料
    D_TO_E = auto()   # 低优先级 — 特殊路径 (可饥饿升级)


class IslandState(Enum):
    """工作岛状态机"""
    IDLE       = auto()
    PROCESSING = auto()
    COMPLETE   = auto()
    TIMEOUT    = auto()   # 节拍超时 — 上游供料暂停
    OFFLINE    = auto()   # 手动或紧急停止


@dataclass
class Material:
    """工厂中的物料/工件。"""
    uid:       int
    item_type: ItemType
    handle:    int = -1
    pos:       list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])

    # 位置追踪
    on_conveyor: Optional[str] = None # 传送带
    conv_progress: float = 0.0 # 传送带位置
    on_agv:       Optional[str] = None
    agv_progress: float = 0.0
    at_island:    Optional[str] = None
    processing_remaining: float = 0.0
    delivered:    bool = False

    # 物料履历
    history: list[tuple[float, str]] = field(default_factory=list)


@dataclass
class AGVJob:
    """AGV 调度任务"""
    task: AGVTask
    priority: int              # 0=最高, 越大越低
    material_uid: int
    wait_start: float = 0.0    # 排队开始时间 (simTime)
    preemptable: bool = True


@dataclass
class IslandBeatState:
    """单个工作岛的节拍追踪状态"""
    island: str
    takt_target: float
    cycle_start: float = 0.0
    cycle_elapsed: float = 0.0
    deviation: float = 0.0
    anomaly: bool = False
    normal_cycles: int = 0


ITEM_COLOR_MAP = {
    ItemType.RAW_A:    cfg.MATERIAL_COLORS["raw_A"],
    ItemType.RAW_B:    cfg.MATERIAL_COLORS["raw_B"],
    ItemType.SEMI_AB:  cfg.MATERIAL_COLORS["semi_AB"],
    ItemType.SEMI_AC:  cfg.MATERIAL_COLORS["semi_AC"],
    ItemType.RAW_D:    cfg.MATERIAL_COLORS["raw_D"],
    ItemType.SEMI_CE:  cfg.MATERIAL_COLORS["semi_CE"],
    ItemType.PART_DE:  cfg.MATERIAL_COLORS["part_DE"],
    ItemType.FINAL:    cfg.MATERIAL_COLORS["final_E"],
}


# ═══════════════════════════════════════════════════════════════
# 日志配置
# ═══════════════════════════════════════════════════════════════

def setup_logger(name: str = "factory-sim",
                 log_file: str = "factory_sim.log") -> logging.Logger:
    """配置结构化日志器。"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)-7s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("[%(levelname)-7s] %(message)s"))
    logger.addHandler(ch)

    return logger


# ═══════════════════════════════════════════════════════════════
# ZMQ 连接管理器 — CoppeliaConnector
# ═══════════════════════════════════════════════════════════════

class CoppeliaConnector:
    """ZMQ 连接管理器，支持自动重连和健康检查。"""

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
        """建立连接，支持重试。"""
        for attempt in range(1, self.max_retries + 1):
            try:
                self.client = RemoteAPIClient(host=self.host, port=self.port)
                self.sim = self.client.require("sim")
                version = self.sim.getStringSignal("coppeliaSimVersion")
                print(f"[OK] 已连接 CoppeliaSim {version} (attempt {attempt})")
                return True
            except Exception as e:
                print(f"[WARN] 连接尝试 {attempt}/{self.max_retries} 失败: {e}")
                if attempt < self.max_retries:
                    wall_time.sleep(1.0 * attempt)
        print(f"[FATAL] 无法连接 CoppeliaSim (共 {self.max_retries} 次尝试)")
        return False

    def check_alive(self) -> bool:
        """快速健康检查。"""
        try:
            self.sim.getStringSignal("coppeliaSimVersion")
            return True
        except Exception:
            return False

    def reconnect_if_needed(self) -> bool:
        """连接断开时尝试重连。"""
        if self.check_alive():
            return True
        print("[WARN] ZMQ 连接丢失，尝试重连...")
        return self.connect()


# ═══════════════════════════════════════════════════════════════
# 仿真循环 — SimulationLoop
# ═══════════════════════════════════════════════════════════════

class SimulationLoop:
    """时间驱动的步进式仿真循环。Python 端始终是时钟主导者。"""

    def __init__(self, connector: CoppeliaConnector):
        self.conn = connector
        self.sim = connector.sim
        self.sim_step = cfg.SIM_STEP
        self.sim_time = 0.0
        self.step_count = 0
        self.running = False

    def start(self):
        """启动步进模式。"""
        self.sim.setStepping(True)
        self.sim.startSimulation()
        self.running = True
        print(f"[INFO] 步进模式已启用, dt={self.sim_step}s")

    def step(self) -> float:
        """执行一个仿真步进。返回当前仿真时间。"""
        self.sim.step()
        self.sim_time = self.sim.getSimulationTime()
        self.step_count += 1
        return self.sim_time

    def stop(self):
        """优雅停止仿真。"""
        if self.running:
            self.sim.stopSimulation()
            self.running = False


# ═══════════════════════════════════════════════════════════════
# 设备句柄映射 — DeviceHandleMap
# ═══════════════════════════════════════════════════════════════

class DeviceHandleMap:
    """按规范路径获取场景对象句柄。"""

    def __init__(self, sim):
        self.sim = sim
        self.handles: dict[str, int] = {}

    def populate(self):
        """加载所有已知对象的句柄。"""
        paths = []
        for wh in cfg.WAREHOUSES.values():
            paths.append(wh["alias"])
        for isl in cfg.WORK_ISLANDS.values():
            paths.append(isl["alias"])
            paths.append(f"{isl['alias']}/Pillar")
        for cv in cfg.CONVEYORS.values():
            paths.append(cv["alias"])
        paths.append(cfg.AGV_ALIAS)

        for path in paths:
            try:
                handle = self.sim.getObject(path)
                self.handles[path] = handle
            except Exception as e:
                print(f"[WARN] 找不到对象 '{path}': {e}")

        print(f"[OK] 句柄映射已加载: {len(self.handles)} 个对象")

    def get(self, path: str) -> int:
        """获取句柄。若缺失则抛出 KeyError。"""
        if path not in self.handles:
            raise KeyError(f"对象 '{path}' 不在句柄映射中")
        return self.handles[path]

    def safe_get(self, path: str) -> Optional[int]:
        """安全获取句柄，缺失时返回 None。"""
        return self.handles.get(path)


# ═══════════════════════════════════════════════════════════════
# 节拍追踪 — BeatTracker
# ═══════════════════════════════════════════════════════════════

class BeatTracker:
    """监控各工作岛的加工时间与 TAKT_TIME 的偏差。"""

    def __init__(self, takt_time: dict, logger: logging.Logger):
        self.states: dict[str, IslandBeatState] = {
            name: IslandBeatState(
                island=name,
                takt_target=target,
            )
            for name, target in takt_time.items()
        }
        self.logger = logger
        self.warn_threshold = cfg.TAKT_DEVIATION_THRESHOLD
        self.timeout_factor = cfg.TAKT_TIMEOUT_FACTOR

    def start_cycle(self, island: str, sim_time: float):
        """标记岛的处理周期开始。"""
        state = self.states.get(island)
        if state:
            state.cycle_start = sim_time
            state.cycle_elapsed = 0.0

    def update(self, island: str, dt: float):
        """更新岛的累计处理时间。"""
        state = self.states.get(island)
        if state is None or state.cycle_start == 0:
            return
        state.cycle_elapsed += dt
        state.deviation = state.cycle_elapsed - state.takt_target

    def check(self, island: str) -> Optional[str]:
        """检查节拍状态。返回 'ok', 'warn', 或 'timeout'。"""
        state = self.states.get(island)
        if state is None or state.cycle_start == 0:
            return None

        # 超时检查 (最严重，优先)
        if state.cycle_elapsed > state.takt_target * self.timeout_factor:
            if not state.anomaly:
                state.anomaly = True
                state.normal_cycles = 0
                old_target = state.takt_target
                state.takt_target *= self.timeout_factor
                self.logger.warning(
                    f"[TIMEOUT] 岛 {island}: "
                    f"{state.cycle_elapsed:.1f}s > "
                    f"{old_target * self.timeout_factor:.1f}s "
                    f"(目标: {old_target:.1f}s)"
                )
                self.logger.info(
                    f"[DEGRADE] 岛 {island} 节拍延长: "
                    f"{old_target:.1f}s → {state.takt_target:.1f}s"
                )
            return "timeout"

        # 偏差警告
        if abs(state.deviation) > self.warn_threshold:
            return "warn"

        # 正常 — 跟踪连续正常周期 (用于自动恢复)
        if state.anomaly:
            state.normal_cycles += 1
            if state.normal_cycles >= 3:
                state.takt_target /= self.timeout_factor
                state.anomaly = False
                state.normal_cycles = 0
                self.logger.info(
                    f"[RESTORE] 岛 {island} 节拍恢复至 "
                    f"{state.takt_target:.1f}s"
                )
        return "ok"


# ═══════════════════════════════════════════════════════════════
# AGV 调度器 — AGVScheduler
# ═══════════════════════════════════════════════════════════════

class AGVScheduler:
    """共享 AGV 柔性调度器 — 优先级 + 抢占 + 饥饿升级 + 死锁恢复。"""

    def __init__(self, config: dict, logger: logging.Logger):
        self.state = AGVState.IDLE
        self.speed = config.get("agv_speed", cfg.AGV_SPEED)
        self.starvation_threshold = config.get(
            "d_e_starvation_threshold", cfg.D_E_STARVATION_THRESHOLD
        )
        self.congestion_timeout = config.get(
            "congestion_timeout", cfg.CONGESTION_TIMEOUT
        )
        self.logger = logger

        self.current_job: Optional[AGVJob] = None
        self.job_queue: list[AGVJob] = []
        self.progress: float = 0.0
        self.blocked_since: float = 0.0

        # 路径定义
        self.routes = {
            AGVTask.B_TO_A: cfg.AGV_ROUTES["B_TO_A"],
            AGVTask.D_TO_E: cfg.AGV_ROUTES["D_TO_E"],
        }

    def request_task(self, task: AGVTask, material_uid: int,
                     sim_time: float):
        """将新的运输请求加入队列。"""
        base_priority = 0 if task == AGVTask.B_TO_A else 1
        job = AGVJob(
            task=task,
            priority=base_priority,
            material_uid=material_uid,
            wait_start=sim_time,
            preemptable=(task == AGVTask.D_TO_E),
        )
        self.job_queue.append(job)
        self._sort_queue(sim_time)

    def _sort_queue(self, sim_time: float):
        """按优先级排序。对饥饿的 D→E 任务进行优先级升级。"""
        for job in self.job_queue:
            if (job.task == AGVTask.D_TO_E and
                    sim_time - job.wait_start > self.starvation_threshold):
                job.priority = 0  # 升级至与 B→A 同等
                job.preemptable = False
        self.job_queue.sort(key=lambda j: (j.priority, j.wait_start))

    def update(self, dt: float, sim_time: float,
               path_blocked: bool = False) -> Optional[int]:
        """推进 AGV 状态机一步。返回已完成交付的物料 uid，否则 None。"""
        # --- 抢占检查: 正在执行 D→E 时有新的 B→A 请求 ---
        if self.state == AGVState.MOVING_D_TO_E:
            waiting_b_to_a = [
                j for j in self.job_queue
                if j.task == AGVTask.B_TO_A
            ]
            if waiting_b_to_a and self.current_job and self.current_job.preemptable:
                self.logger.info(
                    f"[PREEMPT] AGV D→E 被 B→A 抢占 "
                    f"(mat#{self.current_job.material_uid} 重新入队)"
                )
                self.job_queue.append(self.current_job)
                self.current_job = None
                self.state = AGVState.IDLE
                self.progress = 0.0

        # --- BLOCKED 状态恢复 ---
        if self.state == AGVState.BLOCKED:
            if not path_blocked:
                self.logger.info("[RECOVER] AGV 路径已清除，恢复运行")
                if self.current_job:
                    self.state = (
                        AGVState.MOVING_B_TO_A
                        if self.current_job.task == AGVTask.B_TO_A
                        else AGVState.MOVING_D_TO_E
                    )
            return None

        # --- IDLE → 取新任务 ---
        if self.state == AGVState.IDLE:
            self._sort_queue(sim_time)
            if self.job_queue:
                self.current_job = self.job_queue.pop(0)
                self.state = (
                    AGVState.MOVING_B_TO_A
                    if self.current_job.task == AGVTask.B_TO_A
                    else AGVState.MOVING_D_TO_E
                )
                self.progress = 0.0
                self.logger.debug(
                    f"AGV 开始任务 {self.current_job.task.name} "
                    f"mat#{self.current_job.material_uid}"
                )

        # --- MOVING → 移动 ---
        if self.state in (AGVState.MOVING_B_TO_A, AGVState.MOVING_D_TO_E):
            if path_blocked:
                self.state = AGVState.BLOCKED
                self.blocked_since = sim_time
                self.logger.warning(
                    f"[BLOCKED] AGV 路径被占用 @ t={sim_time:.1f}s"
                )
                return None

            route = self.routes[self.current_job.task]
            path_len = self._path_length(route)
            if path_len > 0:
                self.progress += (self.speed / path_len) * dt

            if self.progress >= 1.0:
                delivered_uid = self.current_job.material_uid
                self.logger.info(
                    f"AGV 完成 {self.current_job.task.name} → "
                    f"mat#{delivered_uid} 已送达"
                )
                self.current_job = None
                self.state = AGVState.IDLE
                self.progress = 0.0
                return delivered_uid

        return None

    def current_position(self):
        """获取 AGV 当前位置的插值坐标。"""
        if self.current_job is None or self.state == AGVState.IDLE:
            # 默认停在 B 岛附近
            return self.routes[AGVTask.B_TO_A][0]
        route = self.routes[self.current_job.task]
        return self._interpolate(route, self.progress)

    def is_available(self) -> bool:
        """AGV 是否可接受新任务。"""
        return self.state in (AGVState.IDLE,)

    @staticmethod
    def _interpolate(waypoints, t):
        if t <= 0:
            return waypoints[0]
        if t >= 1:
            return waypoints[-1]
        segments = []
        total = 0.0
        for i in range(len(waypoints) - 1):
            seg = math.hypot(
                waypoints[i + 1][0] - waypoints[i][0],
                waypoints[i + 1][1] - waypoints[i][1],
            )
            segments.append(seg)
            total += seg
        target = t * total
        acc = 0.0
        for i, seg in enumerate(segments):
            if acc + seg >= target:
                lt = (target - acc) / seg if seg > 0 else 0
                x = waypoints[i][0] + (waypoints[i + 1][0] - waypoints[i][0]) * lt
                y = waypoints[i][1] + (waypoints[i + 1][1] - waypoints[i][1]) * lt
                return (x, y)
            acc += seg
        return waypoints[-1]

    @staticmethod
    def _path_length(waypoints):
        total = 0.0
        for i in range(len(waypoints) - 1):
            total += math.hypot(
                waypoints[i + 1][0] - waypoints[i][0],
                waypoints[i + 1][1] - waypoints[i][1],
            )
        return total


# ═══════════════════════════════════════════════════════════════
# 工作岛控制器 — IslandController
# ═══════════════════════════════════════════════════════════════

class IslandController:
    """单个工作岛的状态管理和物料队列。"""

    def __init__(self, name: str, max_queue: int = cfg.MAX_QUEUE_LENGTH):
        self.name = name
        self.state = IslandState.IDLE
        self.current_material_uid: int = -1
        self.queue: list[int] = []
        self.processing_remaining: float = 0.0
        self.max_queue = max_queue
        self.timeout_notified: bool = False

    def can_accept(self) -> bool:
        """检查是否可接受新物料。"""
        return (
            self.state not in (IslandState.TIMEOUT, IslandState.OFFLINE)
            and len(self.queue) < self.max_queue
        )

    def enqueue(self, material_uid: int, processing_time: float = cfg.PROCESS_TIME):
        """物料到达工作岛。"""
        if self.state == IslandState.IDLE and not self.queue:
            self.current_material_uid = material_uid
            self.processing_remaining = processing_time
            self.state = IslandState.PROCESSING
            self.timeout_notified = False
        else:
            self.queue.append(material_uid)

    def update(self, dt: float):
        """推进处理时间。"""
        if self.state == IslandState.PROCESSING:
            self.processing_remaining -= dt
            if self.processing_remaining <= 0:
                self.processing_remaining = 0
                self.state = IslandState.COMPLETE

    def release_material(self) -> Optional[int]:
        """释放已完成的物料（供下游运输）。返回物料 uid。"""
        if self.state != IslandState.COMPLETE:
            return None
        released = self.current_material_uid
        if self.queue:
            self.current_material_uid = self.queue.pop(0)
            self.processing_remaining = cfg.PROCESS_TIME
            self.state = IslandState.PROCESSING
            self.timeout_notified = False
        else:
            self.current_material_uid = -1
            self.state = IslandState.IDLE
        return released

    def enter_timeout(self):
        """进入超时状态 — 上游供料暂停。"""
        self.state = IslandState.TIMEOUT
        self.timeout_notified = True

    def exit_timeout(self):
        """退出超时状态 — 恢复上游供料。"""
        if self.state == IslandState.TIMEOUT:
            self.state = IslandState.IDLE
            self.timeout_notified = False


# ═══════════════════════════════════════════════════════════════
# ZMQ 通信监控 — ZMQMonitor
# ═══════════════════════════════════════════════════════════════

class ZMQMonitor:
    """监控 ZMQ 连接健康状态。"""

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
        """检查连接健康。返回 'ok', 'reconnecting', 或 'dead'。"""
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
# 供料控制器 — SupplyController
# ═══════════════════════════════════════════════════════════════

class SupplyController:
    """管理仓库到工作岛的供料启停。

    当工作岛超时或溢出时，暂停上游供料；恢复后重新开启。
    """

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        # paused[(source, target)] = True 表示 source→target 已暂停
        self.paused: dict[tuple[str, str], bool] = {}

    def pause(self, source: str, target: str):
        """暂停 source→target 的供料。"""
        key = (source, target)
        if not self.paused.get(key):
            self.paused[key] = True
            self.logger.info(f"[SUPPLY] 暂停供料 {source} → {target}")

    def resume(self, source: str, target: str):
        """恢复 source→target 的供料。"""
        key = (source, target)
        if self.paused.get(key):
            self.paused[key] = False
            self.logger.info(f"[SUPPLY] 恢复供料 {source} → {target}")

    def is_paused(self, source: str, target: str) -> bool:
        """检查 source→target 是否已暂停。"""
        return self.paused.get((source, target), False)

    def is_target_paused(self, target: str) -> bool:
        """检查目标岛的所有上游是否已暂停。"""
        upstream = cfg.UPSTREAM_MAP.get(target, [])
        if not upstream:
            return False
        return all(self.is_paused(src, target) for src in upstream)

    def pause_all_upstream(self, target: str):
        """暂停目标岛的所有上游供料。"""
        upstream = cfg.UPSTREAM_MAP.get(target, [])
        for src in upstream:
            self.pause(src, target)

    def resume_all_upstream(self, target: str):
        """恢复目标岛的所有上游供料。"""
        upstream = cfg.UPSTREAM_MAP.get(target, [])
        for src in upstream:
            self.resume(src, target)


# ═══════════════════════════════════════════════════════════════
# 异常处理器 — ExceptionHandler
# ═══════════════════════════════════════════════════════════════

class ExceptionHandler:
    """统一异常监控和恢复协调。每个仿真步长调用一次。"""

    def __init__(self, zmq_monitor: ZMQMonitor,
                 beat_tracker: BeatTracker,
                 supply_ctrl: SupplyController,
                 logger: logging.Logger):
        self.zmq = zmq_monitor
        self.beat = beat_tracker
        self.supply = supply_ctrl
        self.logger = logger
        self.exception_log: list[dict] = []

    def poll(self, sim_time: float,
             island_controllers: dict[str, IslandController],
             agv_scheduler: AGVScheduler) -> bool:
        """轮询所有异常源。返回 False 表示仿真应停止。"""
        # 1. ZMQ 通信 (最高优先级)
        comm = self.zmq.check(sim_time)
        if comm == "dead":
            return False
        if comm == "reconnecting":
            return True  # 跳过本步进

        # 2. 工位超时
        for name, ctrl in island_controllers.items():
            if ctrl.state == IslandState.PROCESSING:
                status = self.beat.check(name)
                if status == "timeout" and not ctrl.timeout_notified:
                    ctrl.enter_timeout()
                    self.supply.pause_all_upstream(name)
                    self._record("workstation_timeout", name, sim_time)
                elif status == "ok" and ctrl.state == IslandState.TIMEOUT:
                    # BeatTracker 已处理自动恢复逻辑
                    ctrl.exit_timeout()
                    self.supply.resume_all_upstream(name)
                    self._record("workstation_recovered", name, sim_time)

        # 3. 物料溢出
        for name, ctrl in island_controllers.items():
            if len(ctrl.queue) >= cfg.MAX_QUEUE_LENGTH:
                self.supply.pause_all_upstream(name)
                self._record("overflow", name, sim_time)
            elif (len(ctrl.queue) <= cfg.MAX_QUEUE_LENGTH // 2
                  and self.supply.is_target_paused(name)):
                self.supply.resume_all_upstream(name)
                self._record("overflow_recovered", name, sim_time)

        # 4. 拥堵检测 — 由 AGVScheduler 内部处理 BLOCKED 状态
        if agv_scheduler.state == AGVState.BLOCKED:
            blocked_dur = sim_time - agv_scheduler.blocked_since
            if blocked_dur > cfg.CONGESTION_TIMEOUT:
                self._record("congestion", "AGV_Shared", sim_time)

        return True

    def _record(self, exc_type: str, target: str, sim_time: float):
        self.exception_log.append({
            "type": exc_type,
            "target": target,
            "sim_time": sim_time,
        })


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════

def lerp_2d(p1: tuple, p2: tuple, t: float) -> list[float]:
    """两点线性插值。"""
    return [p1[0] + (p2[0] - p1[0]) * t,
            p1[1] + (p2[1] - p1[1]) * t]


_uid_counter = 0


def spawn_material(sim, item_type: ItemType,
                   position: list[float],
                   sim_time: float) -> Material:
    """在场景中创建物料方块。"""
    global _uid_counter
    _uid_counter += 1
    sx, sy, sz = cfg.MATERIAL_SIZE
    color = ITEM_COLOR_MAP.get(item_type, [1, 1, 1])
    handle = sim.createPrimitiveShape(sim.primitiveshape_cuboid, [sx, sy, sz])
    sim.setObjectPosition(handle, position, sim.handle_world)
    sim.setObjectColor(handle, 0, sim.colorcomponent_ambient_diffuse, list(color))
    mat = Material(uid=_uid_counter, item_type=item_type,
                   handle=handle, pos=position)
    mat.history.append((sim_time, f"SPAWNED as {item_type.name}"))
    return mat


def remove_material(sim, mat: Material):
    """从场景中删除物料。"""
    if mat.handle != -1:
        sim.removeObject(mat.handle)
        mat.handle = -1


# ═══════════════════════════════════════════════════════════════
# 工厂主控制器 — FactoryController
# ═══════════════════════════════════════════════════════════════

class FactoryController:
    """岛式车间柔性生产仿真主控制器。

    整合：
      - ZMQ 连接管理 + 自动重连
      - 步进式仿真循环
      - 共享 AGV 柔性调度 (优先级 + 抢占)
      - 工作岛状态机 (IDLE→PROCESSING→COMPLETE)
      - 工业节拍对齐 (BeatTracker)
      - 四类异常检测与恢复
      - 结构化日志
    """

    def __init__(self, sim, total_time: float = cfg.TOTAL_SIM_TIME,
                 logger: logging.Logger = None):
        self.sim = sim
        self.total_time = total_time
        self.logger = logger or setup_logger()
        self.materials: list[Material] = []

        # --- 供料计时器 ---
        self.s1_timer_A = 0.0
        self.s1_timer_B = 2.0
        self.s2_timer_D = 1.0
        self.s1_interval_A = cfg.MATERIAL_INTERVAL
        self.s1_interval_B = cfg.MATERIAL_INTERVAL * 1.5
        self.s2_interval_D = cfg.MATERIAL_INTERVAL * 2.0

        # --- 工作岛控制器 ---
        self.islands: dict[str, IslandController] = {
            name: IslandController(name) for name in ["A", "B", "C", "D", "E"]
        }

        # --- AGV 调度器 ---
        agv_config = {
            "agv_speed": cfg.AGV_SPEED,
            "d_e_starvation_threshold": cfg.D_E_STARVATION_THRESHOLD,
            "congestion_timeout": cfg.CONGESTION_TIMEOUT,
        }
        self.agv_scheduler = AGVScheduler(agv_config, self.logger)

        # --- 节拍追踪 ---
        self.beat_tracker = BeatTracker(cfg.TAKT_TIME, self.logger)

        # --- 供料控制 ---
        self.supply_ctrl = SupplyController(self.logger)

        # --- ZMQ 监控 ---
        self.connector = CoppeliaConnector()
        self.zmq_monitor = ZMQMonitor(self.connector, logger=self.logger)

        # --- 异常处理 ---
        self.exception_handler = ExceptionHandler(
            self.zmq_monitor, self.beat_tracker,
            self.supply_ctrl, self.logger
        )

        # --- 统计 ---
        self.assembled_count = 0
        self.sim_time = 0.0
        self.step_count = 0
        self.agv_handle: int = -1

        # A→C 刚性传输追踪 (非 AGV)
        self.ac_transfer_progress: dict[int, float] = {}

    # ── 初始化 ─────────────────────────────────────────────

    def _setup_agv_visual(self):
        """为 AGV 创建运行时可视化。"""
        sx, sy, sz = cfg.AGV_SIZE
        # 初始停在 B 岛
        pos = cfg.AGV_ROUTES["B_TO_A"][0]
        handle = self.sim.createPrimitiveShape(
            self.sim.primitiveshape_cuboid, [sx, sy, sz]
        )
        self.sim.setObjectPosition(
            handle,
            [pos[0], pos[1], cfg.Z_BASE + sz / 2],
            self.sim.handle_world,
        )
        self.sim.setObjectColor(
            handle, 0, self.sim.colorcomponent_ambient_diffuse,
            list(cfg.AGV_COLOR)
        )
        self.agv_handle = handle
        self.logger.info("AGV 可视化已创建")

    # ── 主循环 ─────────────────────────────────────────────

    def run(self):
        """启动仿真主循环。"""
        sim = self.sim
        sim.setStepping(True)
        sim.startSimulation()

        self._setup_agv_visual()

        print("\n" + "=" * 55)
        print(f"  岛式车间柔性生产仿真")
        print(f"  共享 AGV | 节拍追踪 | 异常处理")
        print(f"  总时长: {self.total_time}s")
        print("=" * 55)

        try:
            while self.sim_time < self.total_time:
                dt = cfg.SIM_STEP

                # --- 异常轮询 ---
                if not self.exception_handler.poll(
                    self.sim_time, self.islands, self.agv_scheduler
                ):
                    self._emergency_stop("ZMQ 连接彻底丢失")
                    break

                if self.zmq_monitor.comm_paused:
                    sim.step()
                    self.sim_time = sim.getSimulationTime()
                    continue

                # --- 正常仿真步进 ---
                self._spawn_from_warehouses(dt)
                self._move_conveyors(dt)
                self._process_islands(dt)
                self._schedule_agv_tasks(dt)
                self._update_agv(dt)
                self._handle_ac_transfer(dt)
                self._handle_ce_transfer(dt)
                self._update_scene_positions()
                self._log_periodic()

                sim.step()
                self.sim_time = sim.getSimulationTime()
                self.step_count += 1

        except KeyboardInterrupt:
            print("\n[INFO] 用户中断")
        finally:
            sim.stopSimulation()
            self._print_summary()

    # ── 仓库产出 ──────────────────────────────────────────

    def _spawn_from_warehouses(self, dt: float):
        """仓库按节拍产出物料。如果下游被暂停则跳过。"""
        sim = self.sim
        s1_pos = list(cfg.WAREHOUSES["S1"]["pos"])
        s2_pos = list(cfg.WAREHOUSES["S2"]["pos"])

        # S1 → A (检查 A 的上游是否被暂停)
        if not self.supply_ctrl.is_paused("S1", "A"):
            self.s1_timer_A += dt
            if self.s1_timer_A >= self.s1_interval_A:
                self.s1_timer_A = 0.0
                mat = spawn_material(sim, ItemType.RAW_A, s1_pos, self.sim_time)
                mat.on_conveyor = "S1_to_A"
                mat.conv_progress = 0.0
                self.materials.append(mat)
                self.logger.info(f"S1 → A: raw_A mat#{mat.uid}")

        # S1 → B
        if not self.supply_ctrl.is_paused("S1", "B"):
            self.s1_timer_B += dt
            if self.s1_timer_B >= self.s1_interval_B:
                self.s1_timer_B = 0.0
                mat = spawn_material(sim, ItemType.RAW_B, s1_pos, self.sim_time)
                mat.on_conveyor = "S1_to_B"
                mat.conv_progress = 0.0
                self.materials.append(mat)
                self.logger.info(f"S1 → B: raw_B mat#{mat.uid}")

        # S2 → D
        if not self.supply_ctrl.is_paused("S2", "D"):
            self.s2_timer_D += dt
            if self.s2_timer_D >= self.s2_interval_D:
                self.s2_timer_D = 0.0
                mat = spawn_material(sim, ItemType.RAW_D, s2_pos, self.sim_time)
                mat.on_conveyor = "S2_to_D"
                mat.conv_progress = 0.0
                self.materials.append(mat)
                self.logger.info(f"S2 → D: raw_D mat#{mat.uid}")

    # ── 传送带逻辑 ─────────────────────────────────────────

    def _move_conveyors(self, dt: float):
        """移动传送带上的物料。到达终点转入目标工作岛。"""
        conv_map = {
            "S1_to_A": ("S1", "A"),
            "S1_to_B": ("S1", "B"),
            "S2_to_D": ("S2", "D"),
            "C_to_E":  ("C",  "E"),
        }

        for mat in self.materials:
            if mat.delivered or mat.on_conveyor is None:
                continue
            cname = mat.on_conveyor
            if cname not in conv_map:
                continue

            conv_cfg = cfg.CONVEYORS[cname]
            x1, y1 = conv_cfg["start"]
            x2, y2 = conv_cfg["end"]
            belt_len = math.hypot(x2 - x1, y2 - y1)
            speed_fraction = cfg.CONVEYOR_SPEED / belt_len if belt_len > 0 else 1.0
            mat.conv_progress += speed_fraction * dt

            if mat.conv_progress >= 1.0:
                mat.conv_progress = 1.0
                src, dst = conv_map[cname]

                # 检查目标岛是否可接受
                island = self.islands.get(dst)
                if island and not island.can_accept():
                    self.logger.debug(
                        f"岛 {dst} 无法接受 mat#{mat.uid}，等待中..."
                    )
                    continue

                mat.on_conveyor = None
                mat.at_island = dst
                mat.conv_progress = 0.0
                if island:
                    island.enqueue(mat.uid)
                mat.history.append((self.sim_time, f"ARRIVED at island {dst}"))

                # 类型转换
                if cname == "C_to_E":
                    mat.item_type = ItemType.SEMI_CE

                # 开始节拍追踪
                if island:
                    self.beat_tracker.start_cycle(dst, self.sim_time)

                self.logger.info(
                    f"mat#{mat.uid} ({mat.item_type.name}) 到达岛 {dst}"
                )

    # ── 工作岛处理 ─────────────────────────────────────────

    def _process_islands(self, dt: float):
        """推进各工作岛的处理时间并更新节拍追踪。

        重要：此方法只负责更新处理进度和节拍状态，不释放物料。
        物料释放由下游调度器负责：
          - _schedule_agv_tasks:  B→A (AGV), D→E (AGV)
          - _handle_ac_transfer:  A→C (刚性)
          - _handle_ce_transfer:  C→E (传送带)
        这样确保 island.release_material() 只被调用一次。
        """
        for name, island in self.islands.items():
            if island.state == IslandState.PROCESSING:
                island.update(dt)
                self.beat_tracker.update(name, dt)

        # E 岛装配逻辑
        self._check_assembly_at_E()

    # ── AGV 任务调度 ────────────────────────────────────────

    def _schedule_agv_tasks(self, dt: float):
        """检查各岛的完成物料，将运输任务提交给 AGV 调度器。"""
        # B → A: B 岛完成的物料需要转运到 A
        b_ctrl = self.islands.get("B")
        if b_ctrl and b_ctrl.state == IslandState.COMPLETE:
            released = b_ctrl.release_material()
            if released is not None:
                mat = self._find_mat(released)
                if mat:
                    mat.at_island = None
                    self.agv_scheduler.request_task(
                        AGVTask.B_TO_A, mat.uid, self.sim_time
                    )
                    self.logger.info(
                        f"AGV 请求: B→A mat#{mat.uid} (优先级:高)"
                    )

        # D → E: D 岛完成的物料需要转运到 E
        d_ctrl = self.islands.get("D")
        if d_ctrl and d_ctrl.state == IslandState.COMPLETE:
            released = d_ctrl.release_material()
            if released is not None:
                mat = self._find_mat(released)
                if mat:
                    mat.at_island = None
                    self.agv_scheduler.request_task(
                        AGVTask.D_TO_E, mat.uid, self.sim_time
                    )
                    self.logger.info(
                        f"AGV 请求: D→E mat#{mat.uid} (优先级:低)"
                    )

    def _update_agv(self, dt: float):
        """推进 AGV 状态机，处理装卸。"""
        # 开始新任务时：将物料标记为在 AGV 上
        if (self.agv_scheduler.state in (AGVState.MOVING_B_TO_A,
                                          AGVState.MOVING_D_TO_E)
                and self.agv_scheduler.current_job):
            job = self.agv_scheduler.current_job
            mat = self._find_mat(job.material_uid)
            if mat and not mat.on_agv:
                mat.on_agv = cfg.AGV_ALIAS
                mat.agv_progress = 0.0
                mat.history.append(
                    (self.sim_time,
                     f"LOADED on AGV ({job.task.name})")
                )

        # 更新 AGV
        delivered_uid = self.agv_scheduler.update(dt, self.sim_time)

        # 同步物料进度
        if (self.agv_scheduler.state in (AGVState.MOVING_B_TO_A,
                                          AGVState.MOVING_D_TO_E)
                and self.agv_scheduler.current_job):
            mat = self._find_mat(self.agv_scheduler.current_job.material_uid)
            if mat and mat.on_agv:
                mat.agv_progress = self.agv_scheduler.progress

        # 处理到达
        if delivered_uid is not None:
            mat = self._find_mat(delivered_uid)
            if mat:
                mat.on_agv = None
                mat.agv_progress = 1.0

                # 确定目标岛
                if mat.item_type == ItemType.RAW_B:
                    # B→A 转运: 类型转换为 SEMI_AB
                    mat.item_type = ItemType.SEMI_AB
                    dst = "A"
                elif mat.item_type == ItemType.RAW_D:
                    # D→E 转运: 类型转换为 PART_DE
                    mat.item_type = ItemType.PART_DE
                    dst = "E"
                else:
                    dst = "E"

                island = self.islands.get(dst)
                if island and island.can_accept():
                    mat.at_island = dst
                    island.enqueue(mat.uid)
                    mat.history.append(
                        (self.sim_time, f"UNLOADED at island {dst}")
                    )
                    self.beat_tracker.start_cycle(dst, self.sim_time)
                    self.logger.info(f"mat#{mat.uid} 已送达岛 {dst}")
                else:
                    self.logger.warning(
                        f"mat#{mat.uid} 到达但岛 {dst} 无法接受 (超时/溢出)"
                    )

    # ── A→C 刚性传输 ────────────────────────────────────────

    def _handle_ac_transfer(self, dt: float):
        """A→C 刚性传输 (非 AGV，固定路径直线搬运)。

        A 岛完成后，物料通过预定义路径直接移动到 C 岛。
        这是固定衔接，不经过共享 AGV。
        """
        # A 岛完成 → 启动 A→C 传输
        a_ctrl = self.islands.get("A")
        if a_ctrl and a_ctrl.state == IslandState.COMPLETE:
            released = a_ctrl.release_material()
            if released is not None:
                mat = self._find_mat(released)
                if mat:
                    mat.at_island = None
                    self.ac_transfer_progress[mat.uid] = 0.0
                    mat.history.append(
                        (self.sim_time, "A→C rigid transfer started")
                    )
                    self.logger.info(
                        f"mat#{mat.uid} 启动 A→C 刚性传输"
                    )

        # 推进传输中的物料
        ac_path = [(-2.5, 2.0), (2.0, 2.0)]  # A → C 直线
        path_len = math.hypot(
            ac_path[1][0] - ac_path[0][0],
            ac_path[1][1] - ac_path[0][1],
        )
        speed_fraction = cfg.AGV_SPEED / path_len if path_len > 0 else 1.0

        completed = []
        for uid, progress in list(self.ac_transfer_progress.items()):
            mat = self._find_mat(uid)
            if mat is None or mat.delivered:
                completed.append(uid)
                continue

            new_progress = progress + speed_fraction * dt
            self.ac_transfer_progress[uid] = new_progress

            # 更新物料位置 (用于可视化)
            t = min(new_progress, 1.0)
            pt = lerp_2d(ac_path[0], ac_path[1], t)
            mat.pos = [pt[0], pt[1], cfg.Z_BASE + 0.05]

            if new_progress >= 1.0:
                completed.append(uid)
                mat.item_type = ItemType.SEMI_AC
                island = self.islands.get("C")
                if island and island.can_accept():
                    mat.at_island = "C"
                    island.enqueue(mat.uid)
                    mat.history.append(
                        (self.sim_time, "ARRIVED at island C (A→C rigid)")
                    )
                    self.beat_tracker.start_cycle("C", self.sim_time)
                    self.logger.info(f"mat#{mat.uid} A→C 到达岛 C")
                else:
                    self.logger.warning(
                        f"mat#{mat.uid} A→C 到达但岛 C 无法接受"
                    )

        for uid in completed:
            self.ac_transfer_progress.pop(uid, None)

    # ── C→E 传送带传输 ────────────────────────────────────

    def _handle_ce_transfer(self, dt: float):
        """C→E 传送带传输。C 岛完成后物料自动进入 C_to_E 传送带。

        这是刚性衔接：C 岛不在 AGV 调度范围内，完成即自动流出。
        """
        c_ctrl = self.islands.get("C")
        if c_ctrl and c_ctrl.state == IslandState.COMPLETE:
            released = c_ctrl.release_material()
            if released is not None:
                mat = self._find_mat(released)
                if mat:
                    mat.at_island = None
                    mat.on_conveyor = "C_to_E"
                    mat.conv_progress = 0.0
                    mat.history.append(
                        (self.sim_time, "C→E conveyor started")
                    )
                    self.logger.info(
                        f"mat#{mat.uid} 进入 C→E 传送带"
                    )

    # ── E 岛装配 ───────────────────────────────────────────

    def _check_assembly_at_E(self):
        """E 岛配对消耗 C 线和 D 线的半成品，生成成品。"""
        e_items = [
            mat for mat in self.materials
            if mat.at_island == "E"
            and mat.processing_remaining <= 0
            and not mat.delivered
        ]
        from_c = [m for m in e_items if m.item_type == ItemType.SEMI_CE]
        from_d = [m for m in e_items if m.item_type == ItemType.PART_DE]

        while from_c and from_d:
            c_mat = from_c.pop(0)
            d_mat = from_d.pop(0)
            c_mat.delivered = True
            d_mat.delivered = True
            c_mat.at_island = None
            d_mat.at_island = None
            self.assembled_count += 1

            e_pos = list(cfg.WORK_ISLANDS["E"]["pos"])
            final = spawn_material(self.sim, ItemType.FINAL, e_pos, self.sim_time)
            final.delivered = True
            self.materials.append(final)
            self.logger.info(
                f"✅ 装配 #{self.assembled_count}! "
                f"mat#{c_mat.uid} + mat#{d_mat.uid} → mat#{final.uid} @ E"
            )

    # ── 场景位置同步 ────────────────────────────────────────

    def _update_scene_positions(self):
        """将 Python 状态同步回 CoppeliaSim 中的物体位置。"""
        sim = self.sim

        for mat in self.materials:
            if mat.handle == -1 or mat.delivered:
                continue
            new_pos = None

            if mat.on_conveyor:
                cname = mat.on_conveyor
                if cname in cfg.CONVEYORS:
                    c = cfg.CONVEYORS[cname]
                    pt = lerp_2d(c["start"], c["end"], mat.conv_progress)
                    new_pos = [pt[0], pt[1], cfg.Z_BASE + 0.05]

            elif mat.on_agv:
                # 使用 AGV 调度器的当前位置
                agv_pos = self.agv_scheduler.current_position()
                new_pos = [agv_pos[0], agv_pos[1],
                           cfg.Z_BASE + cfg.AGV_SIZE[2] + 0.06]

            elif mat.at_island and mat.at_island in cfg.WORK_ISLANDS:
                ip = cfg.WORK_ISLANDS[mat.at_island]["pos"]
                offset = len([
                    m for m in self.materials
                    if m.at_island == mat.at_island and m.uid < mat.uid
                ]) * 0.2
                new_pos = [ip[0] + offset - 0.3, ip[1], ip[2] + 0.5]

            # A→C 传输中的物料
            if mat.uid in self.ac_transfer_progress and mat.pos:
                new_pos = [mat.pos[0], mat.pos[1], cfg.Z_BASE + 0.05]

            if new_pos:
                sim.setObjectPosition(mat.handle, new_pos, sim.handle_world)

        # 更新 AGV 车身位置
        if self.agv_handle != -1:
            agv_pos = self.agv_scheduler.current_position()
            sim.setObjectPosition(
                self.agv_handle,
                [agv_pos[0], agv_pos[1],
                 cfg.Z_BASE + cfg.AGV_SIZE[2] / 2],
                sim.handle_world,
            )

    # ── 辅助 ───────────────────────────────────────────────

    def _find_mat(self, uid: int) -> Optional[Material]:
        for m in self.materials:
            if m.uid == uid:
                return m
        return None

    def _log_periodic(self):
        """定期输出状态摘要。"""
        if self.step_count % 100 == 0:
            active = sum(1 for m in self.materials if not m.delivered)
            agv_state = self.agv_scheduler.state.name
            island_states = ",".join(
                f"{n}:{c.state.name[:4]}"
                for n, c in self.islands.items()
            )
            self.logger.info(
                f"t={self.sim_time:.1f}s | 活跃: {active} | "
                f"装配: {self.assembled_count} | "
                f"AGV: {agv_state} | 岛: [{island_states}]"
            )

    def _emergency_stop(self, reason: str):
        """紧急停止 — 保存状态后优雅退出。"""
        self.logger.critical(f"[EMERGENCY] {reason}")
        self.logger.info("[EMERGENCY] 保存状态...")

        state = {
            "sim_time": self.sim_time,
            "step_count": self.step_count,
            "island_states": {
                name: ctrl.state.name
                for name, ctrl in self.islands.items()
            },
            "agv_state": self.agv_scheduler.state.name,
            "material_count": len(self.materials),
            "assembled_count": self.assembled_count,
            "exception_log": self.exception_handler.exception_log[-50:],
        }

        dump_file = f"emergency_dump_{datetime.now():%Y%m%d_%H%M%S}.json"
        with open(dump_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, default=str)
        self.logger.info(f"[EMERGENCY] 状态已保存至 {dump_file}")

        try:
            self.sim.stopSimulation()
        except Exception:
            pass

    def _print_summary(self):
        """输出仿真摘要。"""
        print("\n" + "=" * 55)
        print(f"  仿真结束")
        print(f"  仿真时间: {self.sim_time:.1f}s / 步数: {self.step_count}")
        print(f"  总物料: {len(self.materials)}")
        print(f"  装配完成: {self.assembled_count}")
        print(f"  异常事件: {len(self.exception_handler.exception_log)}")
        if self.exception_handler.exception_log:
            print(f"  异常明细:")
            for exc in self.exception_handler.exception_log[-10:]:
                print(f"    [{exc['type']}] {exc['target']} "
                      f"@ t={exc['sim_time']:.1f}s")
        print(f"  日志文件: factory_sim.log")
        print("=" * 55)


# ═══════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="岛式车间柔性生产仿真控制器")
    parser.add_argument("--host", default="localhost",
                        help="CoppeliaSim 主机地址")
    parser.add_argument("--port", type=int, default=23000,
                        help="ZMQ Remote API 端口")
    parser.add_argument("--time", type=float, default=cfg.TOTAL_SIM_TIME,
                        help=f"仿真总时长 (秒), 默认 {cfg.TOTAL_SIM_TIME}s")
    parser.add_argument("--log", default="factory_sim.log",
                        help="日志文件路径")
    args = parser.parse_args()

    # 初始化日志
    logger = setup_logger(log_file=args.log)

    # 连接
    connector = CoppeliaConnector(host=args.host, port=args.port)
    if not connector.connect():
        sys.exit(1)

    # 注入 connector 到 ZMQ 监控 (覆盖默认的 localhost connector)
    controller = FactoryController(connector.sim, total_time=args.time,
                                   logger=logger)
    # 替换默认 connector 为实际连接的 connector
    controller.connector = connector
    controller.zmq_monitor.connector = connector
    controller.zmq_monitor.last_response = 0.0

    controller.run()


if __name__ == "__main__":
    main()
