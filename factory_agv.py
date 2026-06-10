"""
factory_agv.py — 共享 AGV 柔性调度器
=====================================
本模块实现单一共享 AGV 的完整状态机，包括优先级队列管理、任务抢占、
饥饿升级、路径阻塞恢复和路径插值定位。

对应架构层: Python Control Layer — AGV_Scheduler
参考文档:   references/scheduling.md §AGV State Machine Implementation

核心设计决策:
  - 使用单一共享 AGV 消除多 AGV 死锁场景
  - B→A 为高优先级 (priority=0)，不可抢占
  - D→E 为低优先级 (priority=1)，可抢占，等待超过 D_E_STARVATION_THRESHOLD 后升级

Exports:
  Class:  AGVScheduler
"""

import math
import logging
from typing import Optional

from factory_types import AGVState, AGVTask, AGVJob
import factory_config as cfg


class AGVScheduler:
    """共享 AGV 柔性调度器 — Shared AGV Flexible Scheduler.

    实现优先级队列 + 抢占 + 饥饿升级 + 死锁恢复的完整状态机。
    外部通过 request_task() 提交运输请求, 通过 update() 推进状态。
    AGV 在二维平面沿预定义路径点 (waypoints) 移动，使用线性插值定位。

    Public API:
      request_task(task, material_uid, sim_time) → 提交运输请求
      update(dt, sim_time, path_blocked)        → 推进状态机一步
      current_position()                         → 获取 AGV 的插值坐标
      is_available()                             → 是否可接受新任务

    Attributes:
        state:                当前状态 (IDLE/MOVING_B_TO_A/MOVING_D_TO_E/BLOCKED).
        speed:                AGV 移动速度 (m/s), 来自 factory_config.AGV_SPEED.
        starvation_threshold: D→E 饥饿升级等待时间 (秒).
        congestion_timeout:   路径阻塞判定超时 (秒).
        current_job:          当前正在执行的任务, None 表示空闲.
        job_queue:            排队等待的任务列表 (已按优先级+等待时间排序).
        progress:             当前路径完成进度 (0.0 → 1.0).
        blocked_since:        进入 BLOCKED 状态的仿真时间.
        routes:               任务类型 → 路径点列表的映射.
    """

    def __init__(self, config: dict, logger: logging.Logger):
        """初始化 AGV 调度器.

        Args:
            config: 配置字典，可覆盖默认参数:
                    - agv_speed: AGV 移动速度 (m/s)
                    - d_e_starvation_threshold: D→E 饥饿升级阈值 (s)
                    - congestion_timeout: 拥堵超时 (s)
            logger: 日志记录器.
        """
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

        # 路径定义 — Route waypoints (从 factory_config 读取)
        self.routes = {
            AGVTask.B_TO_A: cfg.AGV_ROUTES["B_TO_A"],
            AGVTask.D_TO_E: cfg.AGV_ROUTES["D_TO_E"],
        }

    # ── 公共接口 ─────────────────────────────────────────────

    def request_task(self, task: AGVTask, material_uid: int,
                     sim_time: float):
        """提交新的运输请求 — Queue a new AGV transport task.

        新任务根据其类型分配基础优先级 (B_TO_A=0, D_TO_E=1)，
        加入队列后自动排序。D→E 任务标记为可抢占。

        Args:
            task:         任务类型.
            material_uid: 待运输的物料 UID.
            sim_time:     当前仿真时间 (用于饥饿计时).
        """
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

    def update(self, dt: float, sim_time: float,
               path_blocked: bool = False) -> Optional[int]:
        """推进 AGV 状态机一步 — Advance AGV state machine by dt seconds.

        处理顺序:
          1. 抢占检查: 若正在执行 D→E 且有 B→A 等待，则抢占
          2. BLOCKED 恢复: 若路径清除则自动恢复
          3. IDLE → 取新任务: 从队列中取优先级最高的任务
          4. MOVING → 沿路径前进: 更新 progress, 到达后返回物料 UID

        Args:
            dt:           本步进的时间增量 (秒).
            sim_time:     当前仿真时间.
            path_blocked: True 表示当前路径被占用.

        Returns:
            已完成交付的物料 UID, 未完成时返回 None.
        """
        # ── 1. 抢占检查 — Preemption: D→E 可被 B→A 抢占 ──
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
                # 将被抢占的任务放回队列头部
                self.job_queue.append(self.current_job)
                self.current_job = None
                self.state = AGVState.IDLE
                self.progress = 0.0

        # ── 2. BLOCKED 恢复 — Recovery from blockage ──
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

        # ── 3. IDLE → 取新任务 — Pick up next job ──
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

        # ── 4. MOVING → 沿路径前进 ──
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
                # 速度除以路径长度 = 每秒完成的路径比例
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
        """获取 AGV 当前位置的插值坐标 — Interpolated position on current route.

        Returns:
            (x, y) 元组。若空闲则返回 B→A 路径的起点 (默认停靠位置).
        """
        if self.current_job is None or self.state == AGVState.IDLE:
            return self.routes[AGVTask.B_TO_A][0]
        route = self.routes[self.current_job.task]
        return self._interpolate(route, self.progress)

    def is_available(self) -> bool:
        """AGV 是否可接受新任务 — Check if AGV can accept new jobs."""
        return self.state in (AGVState.IDLE,)

    # ── 内部方法 ─────────────────────────────────────────────

    def _sort_queue(self, sim_time: float):
        """按优先级排序任务队列 — Sort queue, escalating starved D→E jobs.

        饥饿升级规则:
          若 D→E 任务的等待时间超过 starvation_threshold (默认 15s),
          将其优先级提升至 0 (与 B→A 同等), 并标记为不可抢占.

        Args:
            sim_time: 当前仿真时间 (用于计算等待时长).
        """
        for job in self.job_queue:
            if (job.task == AGVTask.D_TO_E and
                    sim_time - job.wait_start > self.starvation_threshold):
                job.priority = 0  # 升级至与 B→A 同等
                job.preemptable = False
        # 稳定排序: 先按优先级，再按等待时间
        self.job_queue.sort(key=lambda j: (j.priority, j.wait_start))

    @staticmethod
    def _interpolate(waypoints, t):
        """沿多点路径的线性插值 — Linear interpolation along multi-segment path.

        Args:
            waypoints: 路径点列表 [(x1,y1), (x2,y2), ...].
            t:         进度参数 (0.0=起点, 1.0=终点).

        Returns:
            (x, y) 插值坐标.
        """
        if t <= 0:
            return waypoints[0]
        if t >= 1:
            return waypoints[-1]
        # 计算每段的长度
        segments = []
        total = 0.0
        for i in range(len(waypoints) - 1):
            seg = math.hypot(
                waypoints[i + 1][0] - waypoints[i][0],
                waypoints[i + 1][1] - waypoints[i][1],
            )
            segments.append(seg)
            total += seg
        # 按比例定位到具体段
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
        """计算多点路径的总长度 — Total Euclidean length of a waypoint path.

        Args:
            waypoints: 路径点列表 [(x1,y1), (x2,y2), ...].

        Returns:
            总长度 (float).
        """
        total = 0.0
        for i in range(len(waypoints) - 1):
            total += math.hypot(
                waypoints[i + 1][0] - waypoints[i][0],
                waypoints[i + 1][1] - waypoints[i][1],
            )
        return total
