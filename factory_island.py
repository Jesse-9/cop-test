"""
factory_island.py — 工作岛控制 + 工业节拍追踪 + 供料门控
=========================================================
本模块封装与工作岛相关的三个紧密耦合组件:

1. IslandController  — 单个工作岛的状态机和物料队列
2. BeatTracker       — 工业节拍偏差监控与自动降级/恢复
3. SupplyController  — 根据下游状态暂停/恢复上游供料

这三个组件协同工作以实现:
  - 工作岛超时 → BeatTracker 检测 → IslandController 进入 TIMEOUT
    → SupplyController 暂停上游供料
  - 连续3个正常周期 → BeatTracker 自动恢复 → IslandController 退出 TIMEOUT
    → SupplyController 恢复上游供料

对应架构层: Python Control Layer — BeatTracker + IslandController + SupplyController
参考文档:   references/control-framework.md §Beat Tracking System,
            references/scheduling.md §Island State Machine,
            references/exception-handling.md §3. Workstation Timeout, §4. Material Overflow

Exports:
  Classes:  IslandController, BeatTracker, SupplyController
  Enum:     IslandState (re-exported from factory_types)
"""

import logging
from typing import Optional

import factory_config as cfg
from factory_types import IslandState, IslandBeatState


# ═══════════════════════════════════════════════════════════════
# 工作岛控制器 — IslandController
# ═══════════════════════════════════════════════════════════════

class IslandController:
    """单个工作岛的状态管理和物料队列 — Single Work Island Controller.

    状态机: IDLE → PROCESSING → COMPLETE → (release_material) → IDLE
    异常路径: PROCESSING → TIMEOUT (上游暂停) → 恢复 → IDLE

    每个岛维护一个 FIFO 物料队列。当岛处于 IDLE 且有物料到达时，
    立即开始处理；否则物料进入队列等待。

    Attributes:
        name:                  工作岛名称 (A/B/C/D/E).
        state:                 当前状态.
        current_material_uid:  正在处理的物料 UID (-1 表示无).
        queue:                 等待队列 (FIFO, 存储物料 UID).
        processing_remaining:  当前物料剩余加工时间 (秒).
        max_queue:             队列容量上限 (超过则触发溢出控制).
        timeout_notified:      True 表示该岛的超时已被 ExceptionHandler 处理.
    """

    def __init__(self, name: str, max_queue: int = cfg.MAX_QUEUE_LENGTH,
                 unlimited_capacity: bool = False):
        self.name = name
        self.state = IslandState.IDLE
        self.current_material_uid: int = -1
        self.queue: list[int] = []
        self.processing_remaining: float = 0.0
        self.max_queue = max_queue
        self.unlimited_capacity = unlimited_capacity
        self.timeout_notified: bool = False

    def can_accept(self) -> bool:
        """检查是否可接受新物料 — Check if island can accept new materials.

        拒绝条件:
          - 岛处于 TIMEOUT 状态 (节拍超时，上游供料暂停)
          - 岛处于 OFFLINE 状态 (手动或紧急停止)
          - 队列已满 (≥ max_queue) — unlimited_capacity=True 时跳过此检查

        Returns:
            True 表示可以接受新物料.
        """
        if self.state in (IslandState.TIMEOUT, IslandState.OFFLINE):
            return False
        if self.unlimited_capacity:
            return True
        return len(self.queue) < self.max_queue

    def enqueue(self, material_uid: int, processing_time: float = cfg.PROCESS_TIME):
        """物料到达工作岛 — Material arrives at the island.

        若岛空闲且队列为空，立即开始处理；否则加入队列等待。

        Args:
            material_uid:    物料 UID.
            processing_time: 预设加工时间 (秒), 默认使用全局 PROCESS_TIME.
        """
        if self.state == IslandState.IDLE and not self.queue:
            self.current_material_uid = material_uid
            self.processing_remaining = processing_time
            self.state = IslandState.PROCESSING
            self.timeout_notified = False
        else:
            self.queue.append(material_uid)

    def update(self, dt: float):
        """推进处理时间 — Advance processing by dt seconds.

        当 processing_remaining 降为 0 时，状态切换为 COMPLETE，
        等待下游调度器 (AGV/传送带/刚性传输) 取走物料。

        Args:
            dt: 时间增量 (秒).
        """
        if self.state == IslandState.PROCESSING:
            self.processing_remaining -= dt
            if self.processing_remaining <= 0:
                self.processing_remaining = 0
                self.state = IslandState.COMPLETE

    def release_material(self) -> Optional[int]:
        """释放已完成的物料 — Release completed material for downstream transport.

        若队列中有等待的物料，自动开始处理下一个。

        Returns:
            已完成的物料 UID, 若岛不在 COMPLETE 状态则返回 None.
        """
        if self.state != IslandState.COMPLETE:
            return None
        released = self.current_material_uid
        if self.queue:
            # 队列中有等待物料: 立即开始处理下一个
            self.current_material_uid = self.queue.pop(0)
            self.processing_remaining = cfg.PROCESS_TIME
            self.state = IslandState.PROCESSING
            self.timeout_notified = False
        else:
            # 队列为空: 回到 IDLE
            self.current_material_uid = -1
            self.state = IslandState.IDLE
        return released

    def enter_timeout(self):
        """进入超时状态 — Enter TIMEOUT state, pausing upstream supply."""
        self.state = IslandState.TIMEOUT
        self.timeout_notified = True

    def exit_timeout(self):
        """退出超时状态 — Exit TIMEOUT state, resuming upstream supply."""
        if self.state == IslandState.TIMEOUT:
            self.state = IslandState.IDLE
            self.timeout_notified = False


# ═══════════════════════════════════════════════════════════════
# 工业节拍追踪 — BeatTracker
# ═══════════════════════════════════════════════════════════════

class BeatTracker:
    """监控各工作岛的加工时间与 TAKT_TIME 的偏差 — Industrial Beat Tracker.

    每个工作岛有预设的 TAKT_TIME (节拍时间)。BeatTracker 在每个仿真步进中
    累加处理时间，对比目标值:
      - 偏差 > TAKT_DEVIATION_THRESHOLD (默认 1.0s) → WARNING
      - 实际耗时 > TAKT_TIME * TAKT_TIMEOUT_FACTOR (默认 1.5×) → TIMEOUT
        → 自动降级: 延长该岛节拍至 1.5×, 连续 3 个正常周期后自动恢复

    参考: SKILL.md §Industrial Beat Alignment

    Attributes:
        states:          岛名 → IslandBeatState 的映射.
        logger:          日志记录器.
        warn_threshold:  节拍偏差警告阈值 (秒).
        timeout_factor:  超时判定倍数 (实际 > TAKT * factor → TIMEOUT).
    """

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
        """标记岛的处理周期开始 — Mark the start of an island's processing cycle.

        当物料到达工作岛时调用。重置 cycle_elapsed 和 cycle_start。

        Args:
            island:   工作岛名称.
            sim_time: 当前仿真时间.
        """
        state = self.states.get(island)
        if state:
            state.cycle_start = sim_time
            state.cycle_elapsed = 0.0

    def update(self, island: str, dt: float):
        """更新岛的累计处理时间 — Accumulate processing time for the island.

        应在每个仿真步进中调用 (仅当岛处于 PROCESSING 状态时).

        Args:
            island: 工作岛名称.
            dt:     本步进的时间增量 (秒).
        """
        state = self.states.get(island)
        if state is None or state.cycle_start == 0:
            return
        state.cycle_elapsed += dt
        state.deviation = state.cycle_elapsed - state.takt_target

    def check(self, island: str) -> Optional[str]:
        """检查节拍状态 — Check beat status for an island.

        检查顺序 (严重程度递减):
          1. TIMEOUT: 实际耗时 > TAKT * timeout_factor
          2. WARNING: 偏差绝对值 > warn_threshold
          3. OK:      正常范围内, 跟踪连续正常周期用于自动恢复

        自动恢复逻辑: 异常状态下连续 3 个 OK 周期 → 恢复原始节拍.

        Args:
            island: 工作岛名称.

        Returns:
            "timeout" — 超时，已自动降级
            "warn"    — 偏差较大，需要注意
            "ok"      — 正常
            None      — 岛不存在或尚未开始周期
        """
        state = self.states.get(island)
        if state is None or state.cycle_start == 0:
            return None

        # 超时检查 (最严重，优先处理)
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
# 供料门控控制器 — SupplyController
# ═══════════════════════════════════════════════════════════════

class SupplyController:
    """管理仓库到工作岛的供料启停 — Supply Gating Controller.

    当下游工作岛超时 (TIMEOUT) 或物料溢出 (队列过长) 时，
    暂停上游供料以防止物料堆积。恢复后重新开启。

    上游依赖关系由 factory_config.UPSTREAM_MAP 定义.

    参考: references/exception-handling.md §4. Material Overflow

    Attributes:
        logger: 日志记录器.
        paused: (source, target) → bool 映射, True 表示该供料路径已暂停.
    """

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        # paused[(source, target)] = True 表示 source→target 已暂停
        self.paused: dict[tuple[str, str], bool] = {}

    def pause(self, source: str, target: str):
        """暂停 source→target 的供料 — Pause supply from source to target.

        幂等操作: 已暂停的路径不会重复记录日志.

        Args:
            source: 上游来源 (如 "S1", "C").
            target: 下游目标岛 (如 "A", "E").
        """
        key = (source, target)
        if not self.paused.get(key):
            self.paused[key] = True
            self.logger.info(f"[SUPPLY] 暂停供料 {source} → {target}")

    def resume(self, source: str, target: str):
        """恢复 source→target 的供料 — Resume supply from source to target.

        Args:
            source: 上游来源.
            target: 下游目标岛.
        """
        key = (source, target)
        if self.paused.get(key):
            self.paused[key] = False
            self.logger.info(f"[SUPPLY] 恢复供料 {source} → {target}")

    def is_paused(self, source: str, target: str) -> bool:
        """检查 source→target 是否已暂停.

        Args:
            source: 上游来源.
            target: 下游目标岛.

        Returns:
            True 表示该路径的供料已暂停.
        """
        return self.paused.get((source, target), False)

    def is_target_paused(self, target: str) -> bool:
        """检查目标岛的所有上游是否已暂停 — Check if all upstreams are paused.

        用于溢出恢复判断: 仅当所有上游都被暂停时才认为该岛处于全暂停状态.

        Args:
            target: 目标岛名称.

        Returns:
            True 表示该岛的所有上游供料均已暂停.
        """
        upstream = cfg.UPSTREAM_MAP.get(target, [])
        if not upstream:
            return False
        return all(self.is_paused(src, target) for src in upstream)

    def pause_all_upstream(self, target: str):
        """暂停目标岛的所有上游供料 — Pause all upstream supplies to a target.

        用于工位超时或溢出时的一键暂停.

        Args:
            target: 目标岛名称 (如 "E" 会暂停 C→E 和 D→E).
        """
        upstream = cfg.UPSTREAM_MAP.get(target, [])
        for src in upstream:
            self.pause(src, target)

    def resume_all_upstream(self, target: str):
        """恢复目标岛的所有上游供料 — Resume all upstream supplies to a target.

        Args:
            target: 目标岛名称.
        """
        upstream = cfg.UPSTREAM_MAP.get(target, [])
        for src in upstream:
            self.resume(src, target)
