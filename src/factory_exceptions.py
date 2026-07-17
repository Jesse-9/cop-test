"""
factory_exceptions.py — 统一异常处理器
======================================
本模块在每个仿真步进中轮询所有异常源 (ZMQ 通信、工位超时、物料溢出、
AGV 拥堵)，协调恢复动作，并记录异常日志。

异常分类 (按优先级):
  1. ZMQ 通信异常     — 最高优先级, 判定 "dead" 时触发紧急停止
  2. 工位超时          — BeatTracker 检测到超时 → IslandController 进入 TIMEOUT
                        → SupplyController 暂停上游
  3. 物料溢出          — 队列长度 ≥ MAX_QUEUE_LENGTH → 暂停上游供料
                        队列恢复至 ≤ MAX_QUEUE_LENGTH/2 → 恢复上游供料
  4. AGV 拥堵          — BLOCKED 状态持续 > CONGESTION_TIMEOUT → 记录事件

对应架构层: Python Control Layer — Exception_Handler
参考文档:   references/exception-handling.md §Exception Handler — Main Integration

Exports:
  Class:  ExceptionHandler
"""

import logging

import factory_config as cfg
from factory_core import ZMQMonitor


class ExceptionHandler:
    """统一异常监控和恢复协调 — Unified Exception Monitor and Recovery Coordinator.

    每个仿真步长调用一次 poll()。poll() 返回 False 时，
    主循环应触发紧急停止。

    Public API:
      poll(sim_time, island_controllers, agv_scheduler) → bool

    Attributes:
        zmq:           ZMQMonitor 实例 (通信层健康检查).
        beat:          BeatTracker 实例 (节拍异常检测).
        supply:        SupplyController 实例 (供料门控).
        logger:        日志记录器.
        exception_log: 异常事件列表 [{type, target, sim_time}, ...].
    """

    def __init__(self, zmq_monitor: ZMQMonitor,
                 beat_tracker,           # BeatTracker
                 supply_ctrl,            # SupplyController
                 logger: logging.Logger):
        self.zmq = zmq_monitor
        self.beat = beat_tracker
        self.supply = supply_ctrl
        self.logger = logger
        self.exception_log: list[dict] = []

    def poll(self, sim_time: float,
             island_controllers: dict,   # dict[str, IslandController]
             agv_scheduler) -> bool:     # AGVScheduler
        """轮询所有异常源 — Poll all exception sources for one simulation step.

        检查顺序按严重程度排列:
          1. ZMQ 通信:   "dead" → 返回 False; "reconnecting" → 返回 True 但跳过控制
          2. 工位超时:   处理中的岛超时 → enter_timeout + pause_all_upstream
                         恢复正常 → exit_timeout + resume_all_upstream
          3. 物料溢出:   队列满 → pause_all_upstream; 队列恢复 → resume_all_upstream
          4. AGV 拥堵:   BLOCKED 超过阈值 → 记录事件

        Args:
            sim_time:            当前仿真时间.
            island_controllers:  岛名 → IslandController 的映射字典.
            agv_scheduler:       AGVScheduler 实例.

        Returns:
            True 表示仿真可继续, False 表示应紧急停止.
        """
        # ── 1. ZMQ 通信 (最高优先级) ──
        comm = self.zmq.check(sim_time)
        if comm == "dead":
            return False
        if comm == "reconnecting":
            return True  # 跳过本步进的控制逻辑

        # ── 2. 工位超时 — Workstation Timeout ──
        self._check_workstation_timeouts(sim_time, island_controllers)

        # ── 3. 物料溢出 — Material Overflow ──
        self._check_overflow(sim_time, island_controllers)

        # ── 4. AGV 拥堵 — AGV Congestion ──
        self._check_congestion(sim_time, agv_scheduler)

        return True

    def _check_workstation_timeouts(self, sim_time: float,
                                    island_controllers: dict):
        """检查并处理工位超时 — Check workstation timeouts and coordinate recovery.

        对每个处于 PROCESSING 状态的岛调用 BeatTracker.check()。
        超时 → IslandController.enter_timeout() + SupplyController.pause_all_upstream()
        恢复 → IslandController.exit_timeout() + SupplyController.resume_all_upstream()
        """
        from factory_island import IslandState  # 延迟导入避免循环依赖

        for name, ctrl in island_controllers.items():
            if ctrl.state == IslandState.PROCESSING:
                status = self.beat.check(name)
                if status == "timeout" and not ctrl.timeout_notified:
                    ctrl.enter_timeout()
                    self.supply.pause_all_upstream(name)
                    self._record("workstation_timeout", name, sim_time)
                elif status == "ok" and ctrl.state == IslandState.TIMEOUT:
                    # BeatTracker 已通过 normal_cycles >= 3 自动恢复
                    ctrl.exit_timeout()
                    self.supply.resume_all_upstream(name)
                    self._record("workstation_recovered", name, sim_time)

    def _check_overflow(self, sim_time: float,
                        island_controllers: dict):
        """检查并处理物料溢出 — Check for material overflow and throttle supply.

        溢出阈值: 队列长度 ≥ MAX_QUEUE_LENGTH
        恢复阈值: 队列长度 ≤ MAX_QUEUE_LENGTH / 2 (且所有上游被暂停)
        """
        for name, ctrl in island_controllers.items():
            if len(ctrl.queue) >= cfg.MAX_QUEUE_LENGTH:
                self.supply.pause_all_upstream(name)
                self._record("overflow", name, sim_time)
            elif (len(ctrl.queue) <= cfg.MAX_QUEUE_LENGTH // 2
                  and self.supply.is_target_paused(name)):
                self.supply.resume_all_upstream(name)
                self._record("overflow_recovered", name, sim_time)

    def _check_congestion(self, sim_time: float, agv_scheduler):
        """检查 AGV 拥堵 — Check for prolonged AGV blockage.

        当 AGV 处于 BLOCKED 状态超过 CONGESTION_TIMEOUT 时记录拥堵事件。
        实际的 BLOCKED 状态恢复由 AGVScheduler.update() 处理。
        """
        from factory_agv import AGVState  # 延迟导入避免循环依赖

        if agv_scheduler.state == AGVState.BLOCKED:
            blocked_dur = sim_time - agv_scheduler.blocked_since
            if blocked_dur > cfg.CONGESTION_TIMEOUT:
                self._record("congestion", "AGV_Shared", sim_time)

    def _record(self, exc_type: str, target: str, sim_time: float):
        """记录异常事件 — Record an exception event to the log.

        Args:
            exc_type: 异常类型 (workstation_timeout / overflow / congestion / ...).
            target:   异常目标 (岛名或 "AGV_Shared").
            sim_time: 发生时的仿真时间.
        """
        self.exception_log.append({
            "type": exc_type,
            "target": target,
            "sim_time": sim_time,
        })
