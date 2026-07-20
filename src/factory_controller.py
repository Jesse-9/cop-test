"""
factory_controller.py — 工厂仿真主控制器 (Orchestrator)
========================================================
整合所有子系统 (通信、步进、AGV 调度、工作岛、节拍追踪、供料门控、
异常处理) 为完整的岛式车间柔性生产仿真循环。

这是三层架构中的 "Python Control Layer" 顶层编排器。每个仿真步进中，
按固定顺序执行: 异常轮询 → 仓库产出 → 传送带移动 → 岛处理 →
AGV 调度 → 刚性传输 → 场景同步。

对应架构层: Python Control Layer — FactoryController (顶层编排)
参考文档:   SKILL.md §Production Workflow State Machine

用法:
  connector = CoppeliaConnector()
  connector.connect()
  controller = FactoryController(connector.sim, total_time=60.0)
  controller.run()

Exports:
  Class:  FactoryController
"""

import json
import logging
import math
from datetime import datetime
from typing import Optional

import factory_config as cfg
from factory_types import (
    ItemType, AGVState, AGVTask, IslandState,
    Material, ITEM_COLOR_MAP, lerp_2d, spawn_material
)
from factory_core import (
    setup_logger, CoppeliaConnector, ZMQMonitor
)
from factory_agv import AGVScheduler
from factory_island import IslandController, BeatTracker, SupplyController
from factory_exceptions import ExceptionHandler
from factory_robot import RobotArmAnimator


class FactoryController:
    """岛式车间柔性生产仿真主控制器 — Island-Style Flexible Factory Controller.

    整合所有子系统并以固定顺序驱动每个仿真步进。每个步进包含:
      1. 异常轮询 (ZMQ 通信 / 工位超时 / 物料溢出 / AGV 拥堵)
      2. 若通信暂停 (reconnecting) → 跳过控制逻辑, 仅推进仿真时间
      3. 仓库按节拍产出物料
      4. 传送带移动物料
      5. 工作岛处理 (更新加工时间 + 节拍追踪)
      6. AGV 任务调度 (B→A, D→E)
      7. AGV 状态机更新
      8. A→C 刚性传输更新
      9. C→E 传送带传输更新
      10. E 岛装配检查
      11. 场景位置同步
      12. sim.step() 推进物理引擎

    Public API:
      __init__(sim, total_time, logger) → 初始化所有子系统
      run()                              → 启动主循环 (阻塞)

    Attributes:
        sim:          CoppeliaSim Remote API "sim" 模块.
        total_time:   仿真总时长 (秒).
        logger:       结构化日志记录器.
        materials:    所有存活的物料列表.
        islands:      岛名 → IslandController 映射.
        agv_scheduler: AGV 柔性调度器.
        beat_tracker:  工业节拍追踪器.
        supply_ctrl:   供料门控控制器.
        exception_handler: 统一异常处理器.
        assembled_count: 已完成装配的成品数量.
        sim_time:     当前仿真时间.
        step_count:   已执行的仿真步数.
    """

    def __init__(self, sim, total_time: float = cfg.TOTAL_SIM_TIME,
                 logger: logging.Logger = None):
        """初始化工厂控制器 — Initialize the factory controller.

        创建并关联所有子系统: 工作岛、AGV 调度器、节拍追踪、供料控制、
        ZMQ 监控和异常处理。不启动仿真 (调用 run() 启动).

        Args:
            sim:        CoppeliaSim Remote API "sim" 模块 (已连接).
            total_time: 仿真总时长 (秒), 0 表示无限运行.
            logger:     日志记录器, 不提供则自动创建.
        """
        self.sim = sim
        self.total_time = total_time
        self.logger = logger or setup_logger()
        self.materials: list[Material] = []

        # ── 供料计时器 — Warehouse spawn timers ──
        # 每个仓库→岛路径独立计时，起始偏移避免同时产出
        self.s1_timer_A = 0.0
        self.s1_timer_B = 2.0          # S1→B 比 S1→A 晚 2s 启动
        self.s2_timer_D = 1.0          # S2→D 比 S1→A 晚 1s 启动
        self.s1_interval_A = cfg.MATERIAL_INTERVAL          # 3.0s
        self.s1_interval_B = cfg.MATERIAL_INTERVAL * 1.5    # 4.5s
        self.s2_interval_D = cfg.MATERIAL_INTERVAL * 2.0    # 6.0s

        # ── 工作岛控制器 — Island state machines ──
        # E 岛是最终装配线，设为无限容量 (不拒绝任何来料)
        self.islands: dict[str, IslandController] = {
            name: IslandController(name)
            for name in ["A", "B", "C", "D"]
        }
        self.islands["E"] = IslandController("E", max_queue=9999,
                                              unlimited_capacity=True)

        # ── AGV 调度器 — Shared AGV flexible scheduler ──
        agv_config = {
            "agv_speed": cfg.AGV_SPEED,
            "d_e_starvation_threshold": cfg.D_E_STARVATION_THRESHOLD,
            "congestion_timeout": cfg.CONGESTION_TIMEOUT,
        }
        self.agv_scheduler = AGVScheduler(agv_config, self.logger)

        # ── 节拍追踪 — Industrial beat tracker ──
        self.beat_tracker = BeatTracker(cfg.TAKT_TIME, self.logger)

        # ── 供料控制 — Supply gating controller ──
        self.supply_ctrl = SupplyController(self.logger)

        # ── ZMQ 监控 — Connection health monitor ──
        self.connector = CoppeliaConnector()
        self.zmq_monitor = ZMQMonitor(self.connector, logger=self.logger)

        # ── 异常处理 — Unified exception handler ──
        self.exception_handler = ExceptionHandler(
            self.zmq_monitor, self.beat_tracker,
            self.supply_ctrl, self.logger
        )

        # ── 统计 — Runtime statistics ──
        self.assembled_count = 0
        self.sim_time = 0.0
        self.step_count = 0
        self.agv_handle: int = -1

        # ── E 岛装配定时器 — E island timed assembly (TAKT_TIME['E']=8.0s) ──
        self.e_assembly_timer: float = cfg.TAKT_TIME['E']

        # ── A→C 刚性传输追踪 — Non-AGV rigid transfer progress ──
        self.ac_transfer_progress: dict[int, float] = {}

        # ── 机械臂动画器 — Robot arm visual animator (视觉增强层) ──
        # 纯视觉叠加，不影响调度/节拍/异常逻辑。机械臂句柄缺失时静默跳过。
        self.robot_animator: Optional[RobotArmAnimator] = None

    # ═══════════════════════════════════════════════════════════
    # 初始化 — Initialization
    # ═══════════════════════════════════════════════════════════

    def _setup_agv_visual(self):
        """获取 AGV 可视化句柄 — Acquire AGV visual handle for runtime updates.

        优先使用场景中已有的 AGV 对象 (由 build_scene.py 加载的 pioneer p3dx)，
        避免创建重复物体。若场景中不存在则创建一个新的几何体 AGV。

        CoppeliaSim 命名不支持字符 '/', 项目内所有别名一律用下划线连接
        (见 CLAUDE.md「已知问题与坑」第 1 条)。AGV 别名为
        cfg.AGV_ALIAS = "Mobile_AGV_Shared"，此处按别名匹配定位 AGV 根句柄，
        以兼容内置 pioneer 模型可能残留的原生别名。
        """
        sim = self.sim
        # 取别名末两段 "AGV_Shared" 作为叶子, 用于匹配内置 pioneer 模型
        parts = cfg.AGV_ALIAS.split("_")
        agv_leaf = "_".join(parts[-2:]) if len(parts) >= 2 else cfg.AGV_ALIAS

        # 遍历所有 shape/dummy 对象，按别名匹配 AGV 根
        for tval in (sim.object_shape_type, sim.object_dummy_type):
            i = 0
            while i < 5000:
                try:
                    h = sim.getObjects(i, tval)
                    try:
                        a = sim.getObjectAlias(h)
                    except Exception:
                        a = ""
                    if a and (a == cfg.AGV_ALIAS or a == agv_leaf
                              or a.endswith("_" + agv_leaf)):
                        # 确认是 AGV 根: 其父对象别名含 Mobile
                        self.agv_handle = h
                        self.logger.info(
                            "AGV 可视化: 复用场景中已有的 AGV 对象"
                            f" (alias={a})"
                        )
                        return
                    i += 1
                except Exception:
                    break

        # 回退: 场景中无 AGV 对象，创建一个
        sx, sy, sz = cfg.AGV_SIZE
        pos = cfg.AGV_ROUTES["B_TO_A"][0]
        handle = sim.createPrimitiveShape(
            sim.primitiveshape_cuboid, [sx, sy, sz]
        )
        sim.setObjectPosition(
            handle,
            [pos[0], pos[1], cfg.Z_BASE + sz / 2],
            sim.handle_world,
        )
        sim.setObjectColor(
            handle, 0, sim.colorcomponent_ambient_diffuse,
            list(cfg.AGV_COLOR)
        )
        sim.setObjectAlias(handle, cfg.AGV_ALIAS)
        self.agv_handle = handle
        self.logger.info("AGV 可视化已创建 (场景中无已有对象)")

    # ═══════════════════════════════════════════════════════════
    # 主循环 — Main Simulation Loop
    # ═══════════════════════════════════════════════════════════

    def run(self):
        """启动仿真主循环 — Launch the main simulation loop.

        执行流程:
          1. 设置步进模式并启动仿真
          2. 创建 AGV 可视化
          3. 循环 until sim_time >= total_time:
             a. 异常轮询 (ZMQ / timeout / overflow / congestion)
             b. 若通信暂停 → 仅推进物理引擎, 不执行控制逻辑
             c. 正常步进: 仓库产出 → 传送带 → 岛处理 → AGV → 场景同步
             d. sim.step() 推进仿真
          4. 捕获 KeyboardInterrupt → 优雅停止
          5. finally: 停止仿真 + 输出摘要

        每个步进的 dt 固定为 cfg.SIM_STEP (默认 0.05s)。
        """
        sim = self.sim
        # 幂等清场: 若实例残留 paused/stopped 之外的态 (如手动暂停 state=16),
        # startSimulation 不会清除暂停, 后续 sim.step() 会永久挂起。
        # 先 stopSimulation 让引擎回到 stopped 态 (state=0), 再启动。
        try:
            sim.stopSimulation()
            # 等待引擎真正回到停止态, 否则紧接的 start 可能仍带残留
            import time as _t
            for _ in range(40):
                if sim.getSimulationState() == 0:
                    break
                _t.sleep(0.05)
        except Exception as e:
            self.logger.debug(f"run() 预清理 stopSimulation 忽略: {e}")
        sim.setStepping(True)
        sim.startSimulation()

        self._setup_agv_visual()

        # ── 机械臂动画器初始化 — 采集各岛机械臂关节句柄 ──
        try:
            self.robot_animator = RobotArmAnimator(sim, self.logger)
        except Exception as e:
            self.logger.warning(f"[ROBOT] 机械臂动画器初始化失败: {e}")
            self.robot_animator = None

        # ── dt 对齐检查 — Verify Python SIM_STEP matches CoppeliaSim physics dt ──
        sim_dt = sim.getSimulationTimeStep()
        if abs(sim_dt - cfg.SIM_STEP) > 0.001:
            self.logger.warning(
                f"dt 不匹配: Python SIM_STEP={cfg.SIM_STEP}s, "
                f"CoppeliaSim physics dt={sim_dt}s — "
                f"可能导致节拍追踪和传送带速度偏差"
            )
            # 自动对齐: 使用仿真软件的实际 dt
            actual_dt = sim_dt
        else:
            actual_dt = cfg.SIM_STEP
            self.logger.info(f"dt 对齐: Python={cfg.SIM_STEP}s, "
                             f"CoppeliaSim={sim_dt}s ✓")

        print("\n" + "=" * 55)
        print(f"  岛式车间柔性生产仿真")
        print(f"  共享 AGV | 节拍追踪 | 异常处理")
        print(f"  总时长: {self.total_time}s")
        print(f"  物理dt: {actual_dt:.3f}s (Python) / {sim_dt:.3f}s (Sim)")
        print("=" * 55)

        try:
            while self.sim_time < self.total_time:
                dt = actual_dt  # 使用与仿真软件对齐的 dt

                # ── 1. 异常轮询 — Exception polling (before any control) ──
                if not self.exception_handler.poll(
                    self.sim_time, self.islands, self.agv_scheduler
                ):
                    self._emergency_stop("ZMQ 连接彻底丢失")
                    break

                # ── 2. 通信暂停检查 — Skip control if reconnecting ──
                if self.zmq_monitor.comm_paused:
                    sim.step()
                    self.sim_time = sim.getSimulationTime()
                    continue

                # ── 3. 正常仿真步进 — Normal step sequence ──
                self._spawn_from_warehouses(dt)        # 仓库按节拍产出
                self._move_conveyors(dt)               # 传送带移动物料
                self._process_islands(dt)              # 工作岛加工 + 节拍更新
                self._schedule_agv_tasks(dt)           # AGV 任务提交 (B→A, D→E)
                self._update_agv(dt)                   # AGV 状态机推进
                self._handle_ac_transfer(dt)           # A→C 刚性传输
                self._handle_ce_transfer(dt)           # C→E 传送带传输
                self._update_scene_positions()          # 场景位置同步
                self._update_robot_animations(dt)        # 机械臂动画 (视觉增强)
                self._log_periodic()                   # 定期状态摘要

                # ── 4. 推进物理引擎 — Advance simulation ──
                sim.step()
                self.sim_time = sim.getSimulationTime()
                self.step_count += 1

        except KeyboardInterrupt:
            print("\n[INFO] 用户中断")
        finally:
            sim.stopSimulation()
            self._print_summary()

    # ═══════════════════════════════════════════════════════════
    # Phase 1: 仓库产出 — Warehouse Material Supply
    # ═══════════════════════════════════════════════════════════

    def _spawn_from_warehouses(self, dt: float):
        """仓库按节拍产出物料 — Spawn materials from warehouses on beat.

        每个仓库→岛路径有独立的计时器和产出间隔。产出前检查:
          - 下游岛的上游供料是否被暂停 (超时/溢出控制)
          - 计时器是否达到间隔阈值

        三条产出线:
          S1 → A: 原料 RAW_A, 间隔 MATERIAL_INTERVAL (3.0s)
          S1 → B: 原料 RAW_B, 间隔 MATERIAL_INTERVAL * 1.5 (4.5s)
          S2 → D: 零部件 RAW_D, 间隔 MATERIAL_INTERVAL * 2.0 (6.0s)

        Args:
            dt: 本步进的时间增量 (秒).
        """
        sim = self.sim
        s1_pos = list(cfg.WAREHOUSES["S1"]["pos"])
        s2_pos = list(cfg.WAREHOUSES["S2"]["pos"])

        # S1 → A: 检查 A 的上游供料是否被暂停
        if not self.supply_ctrl.is_paused("S1", "A"):
            self.s1_timer_A += dt
            if self.s1_timer_A >= self.s1_interval_A:
                self.s1_timer_A = 0.0
                mat = spawn_material(sim, ItemType.RAW_A, s1_pos, self.sim_time)
                mat.on_conveyor = "S1_to_A"
                mat.conv_progress = 0.0
                self.materials.append(mat)
                self.logger.info(f"S1 → A: raw_A mat#{mat.uid}")

        # S1 → B: 检查 B 的上游供料是否被暂停
        if not self.supply_ctrl.is_paused("S1", "B"):
            self.s1_timer_B += dt
            if self.s1_timer_B >= self.s1_interval_B:
                self.s1_timer_B = 0.0
                mat = spawn_material(sim, ItemType.RAW_B, s1_pos, self.sim_time)
                mat.on_conveyor = "S1_to_B"
                mat.conv_progress = 0.0
                self.materials.append(mat)
                self.logger.info(f"S1 → B: raw_B mat#{mat.uid}")

        # S2 → D: 检查 D 的上游供料是否被暂停
        if not self.supply_ctrl.is_paused("S2", "D"):
            self.s2_timer_D += dt
            if self.s2_timer_D >= self.s2_interval_D:
                self.s2_timer_D = 0.0
                mat = spawn_material(sim, ItemType.RAW_D, s2_pos, self.sim_time)
                mat.on_conveyor = "S2_to_D"
                mat.conv_progress = 0.0
                self.materials.append(mat)
                self.logger.info(f"S2 → D: raw_D mat#{mat.uid}")

    # ═══════════════════════════════════════════════════════════
    # 传送带逻辑 — Conveyor Movement
    # ═══════════════════════════════════════════════════════════

    def _move_conveyors(self, dt: float):
        """移动传送带上的物料 — Advance materials on conveyors.

        每条传送带由 start→end 定义的直线段。物料沿线段以 CONVEYOR_SPEED
        匀速移动。到达终点 (progress ≥ 1.0) 时:
          1. 检查目标岛是否可接受 (can_accept)
          2. 若不可接受 → 物料在传送带末端等待 (progress 保持在 1.0)
          3. 若可接受 → 物料移入目标岛, 触发 enqueue + 节拍周期开始
          4. C→E 传送带到达时类型转换为 SEMI_CE

        Args:
            dt: 本步进的时间增量 (秒).
        """
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
            # 速度 (m/s) 除以路径长度 (m) = 每秒完成的路径比例
            speed_fraction = cfg.CONVEYOR_SPEED / belt_len if belt_len > 0 else 1.0
            mat.conv_progress += speed_fraction * dt

            if mat.conv_progress >= 1.0:
                mat.conv_progress = 1.0
                src, dst = conv_map[cname]

                # 检查目标岛是否可接受 (TIMEOUT/OFFLINE/队列满 → 等待)
                island = self.islands.get(dst)
                if island and not island.can_accept():
                    self.logger.debug(
                        f"岛 {dst} 无法接受 mat#{mat.uid}，等待中..."
                    )
                    continue

                # 物料到达目标岛
                mat.on_conveyor = None
                mat.at_island = dst
                mat.conv_progress = 0.0
                if island:
                    island.enqueue(mat.uid)
                mat.history.append((self.sim_time, f"ARRIVED at island {dst}"))

                # C→E 传送带: 类型转换 RAW → SEMI_CE
                if cname == "C_to_E":
                    mat.item_type = ItemType.SEMI_CE

                # 开始节拍追踪
                if island:
                    self.beat_tracker.start_cycle(dst, self.sim_time)

                self.logger.info(
                    f"mat#{mat.uid} ({mat.item_type.name}) 到达岛 {dst}"
                )

    # ═══════════════════════════════════════════════════════════
    # 工作岛处理 — Island Processing
    # ═══════════════════════════════════════════════════════════

    def _process_islands(self, dt: float):
        """推进各工作岛的处理时间并更新节拍追踪 — Update island processing.

        ⚠ 重要设计决策: 此方法只负责更新处理进度和节拍状态，不释放物料。
        物料释放由下游调度器各自负责:
          - _schedule_agv_tasks:  B→A (AGV), D→E (AGV)
          - _handle_ac_transfer:  A→C (刚性传输)
          - _handle_ce_transfer:  C→E (传送带)
        这样确保 island.release_material() 只被调用一次。

        Args:
            dt: 本步进的时间增量 (秒).
        """
        for name, island in self.islands.items():
            if island.state == IslandState.PROCESSING:
                island.update(dt)
                self.beat_tracker.update(name, dt)

        # E 岛装配逻辑 — 定时配对 C 线和 D 线的半成品
        self._check_assembly_at_E(dt)

    # ═══════════════════════════════════════════════════════════
    # AGV 任务调度 — AGV Task Management
    # ═══════════════════════════════════════════════════════════

    def _schedule_agv_tasks(self, dt: float):
        """提交 AGV 运输任务 — Submit completed island materials as AGV jobs.

        检查各岛的 COMPLETE 状态:
          B 岛 COMPLETE → AGVTask.B_TO_A (高优先级, 不可抢占)
          D 岛 COMPLETE → AGVTask.D_TO_E (低优先级, 可抢占)

        修改：B岛物料完成后进入 WAITING_FOR_AGV 状态，等待AGV取货。
             只有AGV到达B岛后才确认取货并开始处理下一个物料。

        Args:
            dt: 本步进的时间增量 (秒) — 本方法中未直接使用但保留接口一致性.
        """
        # B → A: B 岛完成的物料需要 AGV 转运到 A 岛
        b_ctrl = self.islands.get("B")
        if b_ctrl and b_ctrl.state == IslandState.COMPLETE:
            # 释放物料并进入等待AGV状态
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

        # D → E: D 岛完成的物料需要 AGV 转运到 E 岛
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
        """推进 AGV 状态机并处理物料装卸 — Update AGV state and material loading.

        分三个阶段:
          1. 开始新任务: 将物料标记为 on_agv, 记录 LOADED 事件
          2. 推进 AGV 状态机: 调用 agv_scheduler.update(), 可能返回已送达的物料 UID
          3. 处理到达: 将送达的物料从 AGV 卸载到目标岛

        B→A 到达后类型转换: RAW_B → SEMI_AB, 目标岛 "A"
        D→E 到达后类型转换: RAW_D → PART_DE, 目标岛 "E"

        Args:
            dt: 本步进的时间增量 (秒).
        """
        # ── 1. 开始新任务: 物料装载到 AGV ──
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
                # 确认B岛或D岛物料已被取走，开始处理下一个
                if job.task == AGVTask.B_TO_A:
                    b_island = self.islands.get("B")
                    if b_island and b_island.state == IslandState.WAITING_FOR_AGV:
                        b_island.confirm_pickup()
                        self.logger.info(f"B岛确认取货，开始处理下一个物料")
                elif job.task == AGVTask.D_TO_E:
                    d_island = self.islands.get("D")
                    if d_island and d_island.state == IslandState.WAITING_FOR_AGV:
                        d_island.confirm_pickup()
                        self.logger.info(f"D岛确认取货，开始处理下一个物料")

        # ── 2. 推进 AGV 状态机 ──
        delivered_uid = self.agv_scheduler.update(dt, self.sim_time)

        # ── 3. 同步物料在 AGV 上的进度 ──
        if (self.agv_scheduler.state in (AGVState.MOVING_B_TO_A,
                                          AGVState.MOVING_D_TO_E)
                and self.agv_scheduler.current_job):
            mat = self._find_mat(self.agv_scheduler.current_job.material_uid)
            if mat and mat.on_agv:
                mat.agv_progress = self.agv_scheduler.progress

        # ── 4. 处理到达: 物料从 AGV 卸载到目标岛 ──
        if delivered_uid is not None:
            mat = self._find_mat(delivered_uid)
            if mat:
                mat.on_agv = None
                mat.agv_progress = 1.0

                # 确定目标岛和类型转换
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

    # ═══════════════════════════════════════════════════════════
    # A→C 刚性传输 — A→C Rigid Transfer (non-AGV)
    # ═══════════════════════════════════════════════════════════

    def _handle_ac_transfer(self, dt: float):
        """A→C 刚性传输 — Rigid transfer from Island A to Island C.

        这是固定衔接路径，不经过共享 AGV。A 岛完成后物料沿预设直线
        直接移动到 C 岛。

        传输路径: A(-2.5, 2.0) → C(2.0, 2.0), 水平直线.
        速度: 使用 cfg.AGV_SPEED.

        Args:
            dt: 本步进的时间增量 (秒).
        """
        # ── A 岛完成 → 启动 A→C 传输 ──
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

        # ── 推进传输中的物料 ──
        ac_path = [(-2.5, 2.0), (2.0, 2.0)]  # A → C 水平直线
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

            # 更新物料位置 (用于场景可视化)
            t = min(new_progress, 1.0)
            pt = lerp_2d(ac_path[0], ac_path[1], t)
            mat.pos = [pt[0], pt[1], cfg.Z_BASE + 0.05]

            if new_progress >= 1.0:
                completed.append(uid)
                mat.item_type = ItemType.SEMI_AC  # 类型转换: RAW → SEMI_AC
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

        # 清理已完成的传输记录
        for uid in completed:
            self.ac_transfer_progress.pop(uid, None)

    # ═══════════════════════════════════════════════════════════
    # C→E 传送带传输 — C→E Conveyor Transfer
    # ═══════════════════════════════════════════════════════════

    def _handle_ce_transfer(self, dt: float):
        """C→E 传送带传输 — Conveyor transfer from Island C to final assembly E.

        C 岛完成后物料自动进入 C_to_E 传送带。这是刚性衔接:
        C 岛不在 AGV 调度范围内，完成即自动流出到传送带。

        Args:
            dt: 本步进的时间增量 (秒) — 本方法中未直接使用但保留接口一致性.
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

    # ═══════════════════════════════════════════════════════════
    # E 岛装配 — Final Assembly at Island E
    # ═══════════════════════════════════════════════════════════

    def _check_assembly_at_E(self, dt: float = None):
        """E 岛定时装配 — Timed pairing: SEMI_CE + PART_DE → FINAL.

        E 岛不同于其他工作岛:
          - 无限容量: 来者不拒, 不会因为队列满而拒绝物料
          - 定时处理: 每 TAKT_TIME['E'] (默认 8s) 消耗一对物料产出成品
          - 无超时/溢出: E 是最终装配线, 不触发 TIMEOUT/OFFLINE 以外的异常

        与旧版 (即时配对) 的区别: 装配按固定节拍进行, 更贴近真实产线。

        Args:
            dt: 时间增量 (秒). 由 _process_islands() 传入.
        """
        if dt is None:
            return

        # 推进装配定时器
        self.e_assembly_timer -= dt
        if self.e_assembly_timer > 0:
            return

        # 定时器到期: 尝试配对, 无论成功与否都重置定时器
        self.e_assembly_timer = cfg.TAKT_TIME['E']

        # 收集 E 岛上的可配对物料
        e_items = [
            mat for mat in self.materials
            if mat.at_island == "E"
            and mat.processing_remaining <= 0
            and not mat.delivered
        ]
        from_c = [m for m in e_items if m.item_type == ItemType.SEMI_CE]
        from_d = [m for m in e_items if m.item_type == ItemType.PART_DE]

        if not from_c or not from_d:
            # 物料不足, 等待下一个定时周期
            pending_c = len(from_c)
            pending_d = len(from_d)
            if pending_c > 0 or pending_d > 0:
                self.logger.debug(
                    f"E 岛等待配对: C线={pending_c}, D线={pending_d}"
                )
            return

        # 配对待装配
        c_mat = from_c.pop(0)
        d_mat = from_d.pop(0)
        c_mat.delivered = True
        d_mat.delivered = True
        c_mat.at_island = None
        d_mat.at_island = None
        self.assembled_count += 1

        # 触发 E 岛双臂协同装配动画 (视觉增强，不影响逻辑)
        if self.robot_animator is not None:
            try:
                self.robot_animator.on_assembly()
            except Exception:
                pass

        e_pos = list(cfg.WORK_ISLANDS["E"]["pos"])
        final = spawn_material(self.sim, ItemType.FINAL, e_pos, self.sim_time)
        final.delivered = True  # 成品直接标记为已交付 (展示用)
        self.materials.append(final)
        self.logger.info(
            f"✅ 装配 #{self.assembled_count}! "
            f"mat#{c_mat.uid} + mat#{d_mat.uid} → mat#{final.uid} @ E"
        )

    # ═══════════════════════════════════════════════════════════
    # 场景位置同步 — Scene Position Synchronization
    # ═══════════════════════════════════════════════════════════

    def _update_scene_positions(self):
        """将 Python 状态同步回 CoppeliaSim 中的物体位置 — Sync positions.

        遍历所有活跃物料，根据其当前位置 (传送带/AGV/工作岛/A→C传输中)
        计算世界坐标，调用 sim.setObjectPosition() 更新场景中的视觉位置。

        处理顺序:
          1. 传送带上的物料: 沿 start→end 插值
          2. AGV 上的物料: 跟随 AGV 调度器的当前位置
          3. 岛上的物料: 排列在岛基座上方
          4. A→C 传输中的物料: 沿 A→C 路径插值
          5. AGV 车身: 与调度器同步
        """
        sim = self.sim

        for mat in self.materials:
            if mat.handle == -1 or mat.delivered:
                continue
            new_pos = None

            # 传送带上的物料 — 沿 start→end 线性插值
            if mat.on_conveyor:
                cname = mat.on_conveyor
                if cname in cfg.CONVEYORS:
                    c = cfg.CONVEYORS[cname]
                    pt = lerp_2d(c["start"], c["end"], mat.conv_progress)
                    new_pos = [pt[0], pt[1], cfg.Z_BASE + 0.05]

            # AGV 上的物料 — 跟随 AGV 调度器的插值位置
            elif mat.on_agv:
                agv_pos = self.agv_scheduler.current_position()
                new_pos = [agv_pos[0], agv_pos[1],
                           cfg.Z_BASE + cfg.AGV_SIZE[2] + 0.06]

            # 岛上的物料 — 排列在基座上方, 按 uid 偏移避免重叠
            elif mat.at_island and mat.at_island in cfg.WORK_ISLANDS:
                ip = cfg.WORK_ISLANDS[mat.at_island]["pos"]
                offset = len([
                    m for m in self.materials
                    if m.at_island == mat.at_island and m.uid < mat.uid
                ]) * 0.2
                new_pos = [ip[0] + offset - 0.3, ip[1], ip[2] + 0.5]

            # A→C 刚性传输中的物料
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

    def _update_robot_animations(self, dt: float):
        """推进机械臂动画 — Advance robot arm animations (visual layer).

        纯视觉增强：驱动各工作岛机械臂的关节动画。不影响任何调度、
        节拍或异常逻辑。动画器未初始化或机械臂缺失时静默跳过。

        Args:
            dt: 时间增量 (秒).
        """
        if self.robot_animator is None:
            return
        try:
            self.robot_animator.update(dt, self.islands, self.agv_scheduler)
        except Exception as e:
            self.logger.debug(f"[ROBOT] 动画更新异常 (已忽略): {e}")

    # ═══════════════════════════════════════════════════════════
    # 辅助方法 — Utilities
    # ═══════════════════════════════════════════════════════════

    def _find_mat(self, uid: int) -> Optional[Material]:
        """按 UID 查找物料 — Find material by unique ID.

        Args:
            uid: 物料唯一标识符.

        Returns:
            Material 对象, 未找到则返回 None.
        """
        for m in self.materials:
            if m.uid == uid:
                return m
        return None

    def _log_periodic(self):
        """定期输出状态摘要 — Periodic status summary every 100 steps.

        输出格式:
          t=<sim_time>s | 活跃: <count> | 装配: <count> |
          AGV: <state> | 岛: [<state_abbrevs>]

        间隔: 每 100 个仿真步进 (即每 5 秒仿真时间, 当 SIM_STEP=0.05).
        """
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
        """紧急停止 — Emergency stop with state preservation.

        当不可恢复的错误发生时 (如 ZMQ 连接彻底丢失):
          1. 记录 CRITICAL 日志
          2. 将当前状态序列化为 JSON 文件 (用于事后分析)
          3. 尝试停止仿真

        Args:
            reason: 紧急停止原因 (用于日志和文件名).
        """
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
        """输出仿真摘要 — Print simulation summary to console.

        摘要内容包括: 仿真时间、步数、物料总数、装配完成数、异常事件数、
        最近 10 条异常明细。
        """
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
