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

import reference.factory_config as cfg
from reference.factory_types import (
    ItemType, AGVState, AGVTask, IslandState,
    Material, ITEM_COLOR_MAP, lerp_2d, spawn_material,
    MATERIAL_STATE_WAITING_FOR_SUCTION, MATERIAL_STATE_SUCTION_ATTACHED,
    MATERIAL_STATE_AT_ISLAND, MATERIAL_STATE_ON_CONVEYOR,
)
from reference.factory_core import (
    setup_logger, CoppeliaConnector, ZMQMonitor
)
from reference.factory_scene import find_by_alias
from reference.factory_agv import AGVScheduler
from reference.factory_island import IslandController, BeatTracker, SupplyController
from reference.factory_exceptions import ExceptionHandler
from reference.factory_robot import RobotArmAnimator


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
                 logger: logging.Logger = None, sim_ik=None):
        """初始化工厂控制器 — Initialize the factory controller.

        创建并关联所有子系统: 工作岛、AGV 调度器、节拍追踪、供料控制、
        ZMQ 监控和异常处理。不启动仿真 (调用 run() 启动).

        Args:
            sim:        CoppeliaSim Remote API "sim" 模块 (已连接).
            total_time: 仿真总时长 (秒), 0 表示无限运行.
            logger:     日志记录器, 不提供则自动创建.
        """
        self.sim = sim
        self.sim_ik = sim_ik
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
        # AGV 朝向追踪 — 用于 setObjectOrientation 令车头指向行进方向
        self._agv_yaw: float = 0.0
        self._agv_prev_pos: tuple[float, float] = (
            cfg.AGV_ROUTES["B_TO_A"][0][0], cfg.AGV_ROUTES["B_TO_A"][0][1]
        )

        # ── 用户主动暂停标志 — user-requested pause (区别于 zmq_monitor.comm_paused) ──
        self._pause_requested: bool = False
        self._pause_at_sim_time: float = 0.0

        # ── E 岛装配定时器 — E island timed assembly (TAKT_TIME['E']=8.0s) ──
        self.e_assembly_timer: float = cfg.TAKT_TIME['E']

        # ── A→C 刚性传输追踪 — Non-AGV rigid transfer progress ──
        self.ac_transfer_progress: dict[int, float] = {}

        # ── 物理传送带背压标志 — Physics-mode conveyor freeze (per belt) ──
        # 物理模式下, 物料到达带末端但目标岛拒绝 (TIMEOUT / 队列满) 时,
        # 冻结对应带面的移动, 避免物料被推散。等岛 can_accept=True 时解冻。
        self._conveyor_frozen: dict[str, bool] = {
            name: False for name in cfg.CONVEYORS
        }
        # 内置传送带 pad 的闭环位姿缓存。None 表示不是多 pad 模型，
        # 运行时自动退回物料位置插值，避免回退几何体传送带永久停滞。
        self._conveyor_pad_loops: dict[str, Optional[dict]] = {}
        # 传送带实测几何缓存 — 构建后不再变化, 避免每步每料重建 (性能)
        self._belt_geometry_cache: dict[str, dict] = {}
        # 运行期从场景读回传送带实测几何 (方案 §4.2) — 必须早于任何用到端点的
        # 逻辑; 构建期的元数据不会跨进程存活, 只能在这里重新量一遍。
        self._resolve_conveyor_geometry()
        self._apply_conveyor_resolution()
        self._pending_conveyor_pickups: list[tuple[Material, str, str]] = []
        # 冻结前保存的物料动态属性 (static 标志) — 方案 §10: 必须可还原
        self._frozen_material_props: dict[int, int] = {}
        # 起停下发/回读失败过的传送带 (方案 §5.4 SCRIPT_ERROR)
        self._conveyor_script_error: set[str] = set()
        # 履带驱动源是否已完成一次性启动探测 (方案 §5.1 PROBING)
        self._conveyor_drive_probed = False
        # GEOMETRIC_FALLBACK 只告警一次
        self._geometric_fallback_warned = False
        # 物料输送机制说明只打一次 (方案 §5.2 要求显式标注回退)
        self._transport_notice = False
        # IK 失败的物料: 退避重试而不是静默当成取料成功 (方案 §10)
        self._failed_pickups: set[int] = set()
        self._pickup_retry_at: dict[int, float] = {}

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
        cfg.AGV_ALIAS = "Mobile_AGV_Shared", 此处用 factory_scene.find_by_alias
        统一匹配 (完整别名 / 叶子 / _前缀) — 取代旧内联循环。
        """
        sim = self.sim
        h = find_by_alias(sim, cfg.AGV_ALIAS,
                          sim.object_shape_type, sim.object_dummy_type)
        if h >= 0:
            self.agv_handle = h
            self.logger.info(
                f"AGV 可视化: 复用场景中已有的 AGV 对象 (handle={h})"
            )
            return

        # 回退: 场景中无 AGV 对象，创建一个
        sx, sy, sz = cfg.AGV_SIZE
        pos = cfg.AGV_ROUTES["B_TO_A"][0]
        handle = sim.createPrimitiveShape(
            sim.primitiveshape_cuboid, [sx, sy, sz]
        )
        sim.setObjectPosition(
            handle,
            [pos[0], pos[1], cfg.FLOOR_TOP_Z + sz / 2],
            sim.handle_world,
        )
        sim.setObjectColor(
            handle, 0, sim.colorcomponent_ambient_diffuse,
            list(cfg.AGV_COLOR)
        )
        sim.setObjectAlias(handle, cfg.AGV_ALIAS)
        self.agv_handle = handle
        self.logger.info("AGV 可视化已创建 (场景中无已有对象)")
        # 创建后立即对齐朝向跟踪初值
        start = cfg.AGV_ROUTES["B_TO_A"][0]
        self._agv_prev_pos = (start[0], start[1])
        self._agv_yaw = 0.0

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
            self.robot_animator = RobotArmAnimator(sim, self.logger, self.sim_ik)
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

                # ── 2b. 用户主动暂停 — user pause: 只推进物理, 跳过控制逻辑 ──
                if self._pause_requested:
                    sim.step()
                    self.sim_time = sim.getSimulationTime()
                    self.step_count += 1
                    continue

                # ── 2c. 履带驱动源一次性探测 — One-shot conveyor drive probe ──
                # 方案 §5.1: 启动后只探测一次, 之后禁止在脚本驱动与 Python
                # pad 驱动之间每帧切换。探测本身会额外推进几个仿真步。
                if not self._conveyor_drive_probed:
                    try:
                        self._probe_conveyor_drive()
                    except Exception as e:
                        self._conveyor_drive_probed = True
                        self.logger.warning(f"[CONV] 履带驱动探测失败: {e}")

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
          S1 → A: 原料 RAW_A, 间隔 MATERIAL_INTERVAL (5.0s)
          S1 → B: 原料 RAW_B, 间隔 MATERIAL_INTERVAL * 1.5 (7.5s)
          S2 → D: 零部件 RAW_D, 间隔 MATERIAL_INTERVAL * 2.0 (10.0s)

        ⚠ 方案 §3.1 第 5 条: 物料生成在**入口 dummy 的带面上方一个小间隙**处,
        不再生成在仓库中心再靠位姿插值"跳"到传送带。生成 Z 一律由
        `_belt_geometry(cname)["surface_z"]` 推出。
        """
        sim = self.sim

        def _spawn(item_type: ItemType, cname: str, tag: str) -> Material:
            seg = self._belt_geometry(cname)
            pos = [seg["start"][0], seg["start"][1],
                   seg["surface_z"] + cfg.MATERIAL_SIZE[2] / 2.0
                   + cfg.MATERIAL_SPAWN_GAP]
            mat = spawn_material(sim, item_type, pos, self.sim_time,
                                 state=MATERIAL_STATE_ON_CONVEYOR)
            mat.on_conveyor = cname
            mat.conv_progress = 0.0
            self.materials.append(mat)
            self.logger.info(f"{tag}: mat#{mat.uid} @ "
                             f"({pos[0]:.2f}, {pos[1]:.2f}, {pos[2]:.2f})")
            return mat

        # S1 → A: 检查 A 的上游供料是否被暂停
        if not self.supply_ctrl.is_paused("S1", "A"):
            self.s1_timer_A += dt
            if self.s1_timer_A >= self.s1_interval_A:
                self.s1_timer_A = 0.0
                _spawn(ItemType.RAW_A, "S1_to_A", "S1 → A: raw_A")

        # S1 → B: 检查 B 的上游供料是否被暂停
        if not self.supply_ctrl.is_paused("S1", "B"):
            self.s1_timer_B += dt
            if self.s1_timer_B >= self.s1_interval_B:
                self.s1_timer_B = 0.0
                _spawn(ItemType.RAW_B, "S1_to_B", "S1 → B: raw_B")

        # S2 → D: 检查 D 的上游供料是否被暂停
        if not self.supply_ctrl.is_paused("S2", "D"):
            self.s2_timer_D += dt
            if self.s2_timer_D >= self.s2_interval_D:
                self.s2_timer_D = 0.0
                _spawn(ItemType.RAW_D, "S2_to_D", "S2 → D: raw_D")

    # ═══════════════════════════════════════════════════════════
    # 传送带逻辑 — Conveyor Movement
    # ═══════════════════════════════════════════════════════════

    # 物理模式: 传送带末端 → 目标岛映射 (用于到达检测)
    _CONVEYOR_DST = {
        "S1_to_A": ("S1", "A"),
        "S1_to_B": ("S1", "B"),
        "S2_to_D": ("S2", "D"),
        "C_to_E":  ("C",  "E"),
    }

    def _apply_conveyor_resolution(self):
        """让控制器真正用上 `_resolve_conveyor_geometry` 的实测结果。

        `_move_conveyors_physics` 里的每一处端点/高度读取都必须走实测元数据,
        否则实测就白做了。这里集中断言一次, 缺项直接告警而不是静默回退成
        设计值 —— 设计值只在"场景里压根没有标记对象"时才允许使用。
        """
        missing = []
        for cname, c in cfg.CONVEYORS.items():
            if not c.get("actual_start") or not c.get("actual_end"):
                missing.append(cname)
        if missing:
            self.logger.warning(
                f"[CONV] 这些传送带缺实测端点 (将退回设计值): {missing} — "
                "请用当前 build_scene 重建场景以生成 Entry/PickupStop 标记"
            )

    def _move_conveyors(self, dt: float):
        """移动传送带上的物料 — Advance materials on conveyors.

        kinematic 模式 (PHYSICS_CONVEYOR=False):
          每条传送带由 start→end 定义的直线段。物料沿线段以 CONVEYOR_SPEED
          匀速移动 (Python 浮点 conv_progress 累加)。到达终点 (progress ≥ 1.0)
          时检查目标岛可接受性, 决定入岛 / 末端等待。

        物理模式 (PHYSICS_CONVEYOR=True):
          每步把带面沿 start→end 方向 setObjectPosition 推进
          CONVEYOR_SPEED * dt 米 (kinematic+static+respondable 表面, 由摩擦带动
          上方 dynamic 物料)。然后对带上面物料做到达检测 (世界坐标距带末端
          ≤ ARRIVAL_RADIUS → 入岛)。目标岛拒绝时冻结该带面 (背压)。

        Args:
            dt: 本步进的时间增量 (秒).
        """
        if cfg.PHYSICS_CONVEYOR:
            self._move_conveyors_physics(dt)
        else:
            self._move_conveyors_kinematic(dt)

    # ═══════════════════════════════════════════════════════════
    # 传送带几何 (方案 §4.2) — 一律读实测元数据, 不再自行推算端点
    # ═══════════════════════════════════════════════════════════

    def _resolve_conveyor_geometry(self):
        """从场景读回传送带的实际几何与驱动源 — Resolve belt geometry at runtime.

        ⚠ 关键: build_scene 写进 `cfg.CONVEYORS[*]` 的元数据只活在**构建进程**
        的内存里, run_simulation / MCP 都是新进程、拿到的是初始配置。所以
        运行期必须按别名把入口/末端 dummy 与传送带根重新找回来, 用实测值覆盖
        设计值 (方案 §4.2: "控制器只使用这组实际值")。
        """
        from reference.factory_physics import find_all_belt_pad_shapes
        from reference.factory_scene import (
            model_local_bbox, measure_belt_surface_offset, find_script_child,
        )
        sim = self.sim
        for cname, c in cfg.CONVEYORS.items():
            alias = c["alias"]
            root = find_by_alias(sim, alias, sim.object_dummy_type,
                                 sim.object_shape_type)
            if root < 0:
                self.logger.warning(
                    f"[CONV] {cname}: 场景中找不到 {alias}, 该带按几何回退处理")
                c["handle"] = -1
                continue
            c["handle"] = root
            # 入口 / 末端取料点 (build_scene 创建的 dummy)
            entry = find_by_alias(sim, f"{alias}_Entry", sim.object_dummy_type)
            stop = find_by_alias(sim, f"{alias}_PickupStop", sim.object_dummy_type)
            stopper = find_by_alias(sim, f"{alias}_PickupStopper",
                                    sim.object_shape_type)
            try:
                if entry >= 0:
                    c["actual_start"] = list(
                        sim.getObjectPosition(entry, sim.handle_world))
                if stop >= 0:
                    c["actual_end"] = list(
                        sim.getObjectPosition(stop, sim.handle_world))
            except Exception:
                pass
            c["stopper_handle"] = stopper
            # 带面高度: 实测 pad 上表面 (不假定模型根就在带面上)
            pads = find_all_belt_pad_shapes(sim, root)
            try:
                offset = measure_belt_surface_offset(sim, root, pads)
                if offset is not None:
                    c["surface_z"] = (
                        sim.getObjectPosition(root, sim.handle_world)[2] + offset
                    )
            except Exception:
                pass
            # 实际长度 / 带宽
            bbox = model_local_bbox(sim, root)
            if bbox:
                c["actual_length"] = bbox[3] - bbox[0]
                c["width"] = bbox[4] - bbox[1]
            # 驱动源: 有脚本 + 有 pad 才是脚本驱动的候选 (最终由启动探测裁定)
            c["script_handle"] = find_script_child(sim, root)
            if c["script_handle"] >= 0 and pads:
                c["drive_mode"] = cfg.CONVEYOR_DRIVE_SCRIPT
            elif pads:
                c["drive_mode"] = cfg.CONVEYOR_DRIVE_PYTHON
            else:
                c["drive_mode"] = cfg.CONVEYOR_DRIVE_FALLBACK
            self.logger.info(
                f"[CONV] {cname}: 实测 L="
                f"{(c.get('actual_length') or 0):.3f}m "
                f"(设计 {math.hypot(c['end'][0] - c['start'][0], c['end'][1] - c['start'][1]):.3f}m), "
                f"带面 Z={(c.get('surface_z') or 0):.3f}, pad={len(pads)}, "
                f"脚本={'有' if c['script_handle'] >= 0 else '无'}, "
                f"驱动候选={c['drive_mode']}"
            )

    def _drive_mode(self, cname: str) -> str:
        """该传送带的驱动模式 — Conveyor drive mode.

        元数据缺失 (旧场景 / 未经 build_scene 的离线夹具) 时按
        FALLBACK_GEOMETRY 处理: 这是唯一不需要场景元数据也能跑的模式, 也是
        旧版行为。绝不默认成"脚本驱动", 否则 Python 会以为履带在动而不推 pad。
        """
        return cfg.CONVEYORS[cname].get("drive_mode") \
            or cfg.CONVEYOR_DRIVE_FALLBACK

    def _belt_geometry(self, cname: str) -> dict:
        """取传送带**实际**几何 — Actual belt geometry.

        优先用 build_scene 从内置模型包围盒实测后写回的元数据
        (actual_start / actual_end / surface_z); 元数据缺失 (旧场景或几何
        回退) 时才退回设计端点。调用方不得再对结果做 Z 补偿。

        Returns:
            {"start": [x, y], "end": [x, y], "length": float,
             "surface_z": float, "dir": [dx, dy]}
            起点 = 入口 dummy, 终点 = 末端取料点 dummy。
        """
        c = cfg.CONVEYORS[cname]
        cached = self._belt_geometry_cache.get(cname)
        if cached is not None:
            return cached
        start = c.get("actual_start") or c["start"]
        end = c.get("actual_end") or c["end"]
        sx, sy = float(start[0]), float(start[1])
        ex, ey = float(end[0]), float(end[1])
        length = math.hypot(ex - sx, ey - sy)
        if length > 1e-9:
            direction = [(ex - sx) / length, (ey - sy) / length]
        else:
            direction = list(c.get("dir") or [1.0, 0.0])
        result = {
            "start": [sx, sy],
            "end": [ex, ey],
            "length": length,
            "surface_z": float(c.get("surface_z", cfg.CONVEYOR_SURFACE_Z)),
            "dir": direction,
        }
        # 构建后几何不再变化, 缓存以避免每步每料重建字典 (ZMQ 每步超时风险)
        self._belt_geometry_cache[cname] = result
        return result

    # ═══════════════════════════════════════════════════════════
    # 传送带驱动源探测 (方案 §5.1 PROBING)
    # ═══════════════════════════════════════════════════════════

    def _probe_conveyor_drive(self):
        """启动探测: 为每条带选定**唯一**驱动源 — Pick exactly one writer.

        状态只允许在三种之间取一, 且探测后不再每帧切换 (方案 §5.1):
          SCRIPT_DRIVEN      内置脚本驱动履带 → Python 只用 __ctrl__ 调速
          PYTHON_PAD_DRIVEN  脚本不驱动 → 剥离脚本, Python 推 pad 闭环
          FALLBACK_GEOMETRY  无内置模型/pad → Python 按 conv_progress 插值

        判据是**实测**: 把带速设为 CONVEYOR_SPEED 后看一个仿真步内 pad 是否
        沿带方向位移。探测会额外推进 CONVEYOR_PROBE_STEPS 个仿真步。
        """
        from reference.factory_physics import (
            find_all_belt_pad_shapes, set_conveyor_running,
        )
        for cname, c in cfg.CONVEYORS.items():
            handle = c.get("handle", -1)
            if handle is None or handle < 0:
                c["drive_mode"] = cfg.CONVEYOR_DRIVE_FALLBACK
                continue
            try:
                pads = find_all_belt_pad_shapes(self.sim, handle)
            except Exception:
                pads = []
            if not pads:
                c["drive_mode"] = cfg.CONVEYOR_DRIVE_FALLBACK
                self.logger.info(
                    f"[CONV] {cname}: 无 pad → {cfg.CONVEYOR_DRIVE_FALLBACK}"
                )
                continue
            if c.get("script_handle", -1) < 0:
                c["drive_mode"] = cfg.CONVEYOR_DRIVE_PYTHON
                self.logger.info(
                    f"[CONV] {cname}: 有 pad 无脚本 → {cfg.CONVEYOR_DRIVE_PYTHON}"
                )
                continue

            # 有脚本 + 有 pad: 先把速度设到配置值, 再实测 pad 是否真的移动
            state = set_conveyor_running(
                self.sim, handle, True, cfg.CONVEYOR_SPEED
            )
            moved = self._measure_pad_motion(pads)
            if moved is None or moved < cfg.CONVEYOR_PROBE_MIN_MOVE:
                c["drive_mode"] = cfg.CONVEYOR_DRIVE_PYTHON
                # 脚本不驱动履带 → **停用**它 (不是删除!)。删除会让场景逐次
                # 退化: 第一次运行探测后脚本没了, 下次运行连探测对象都没有,
                # 且重新建场景才能恢复 (实测踩过)。scriptintparam_enabled 可逆。
                script = c.get("script_handle", -1)
                if script >= 0:
                    try:
                        self.sim.setObjectInt32Param(
                            script, self.sim.scriptintparam_enabled, 0)
                        c["script_enabled"] = False
                    except Exception as exc:
                        self.logger.warning(
                            f"[CONV] {cname} 停用脚本失败: {exc}")
                self.logger.warning(
                    f"[CONV] {cname}: 脚本未驱动履带 (单步位移={moved}) → "
                    f"{cfg.CONVEYOR_DRIVE_PYTHON}, 已停用脚本 (未删除)")
            else:
                c["drive_mode"] = cfg.CONVEYOR_DRIVE_SCRIPT
                c["script_vel"] = (state or {}).get("vel")
                self.logger.info(
                    f"[CONV] {cname}: {cfg.CONVEYOR_DRIVE_SCRIPT} "
                    f"(单步位移 {moved:.5f} m, __state__={state})"
                )
        self._conveyor_drive_probed = True

    def _measure_pad_motion(self, pads: list) -> Optional[float]:
        """实测一个仿真步内 pad 的局部最大位移 (m) — 只读探测。

        返回 None 表示读不到位姿 (视为"未驱动", 由调用方降级)。
        """
        try:
            before = [list(self.sim.getObjectPose(p, self.sim.handle_parent))
                      for p in pads[:3]]
        except Exception:
            return None
        try:
            for _ in range(max(1, cfg.CONVEYOR_PROBE_STEPS)):
                self.sim.step()
        except Exception:
            return None
        try:
            after = [list(self.sim.getObjectPose(p, self.sim.handle_parent))
                     for p in pads[:3]]
        except Exception:
            return None
        return max(
            math.dist(a[:3], b[:3]) for a, b in zip(before, after)
        )

    # ═══════════════════════════════════════════════════════════
    # 传送带起停 (方案 §5.4) — 只影响被点名的那一条带
    # ═══════════════════════════════════════════════════════════

    def _set_belt_running(self, cname: str, running: bool) -> bool:
        """起停单条传送带 — Start/stop one conveyor only.

        返回 False 表示下发失败或回读不上 — 调用方必须把该带记为
        SCRIPT_ERROR, 不得静默退回"物料位置插值"(方案 §5.4)。
        """
        from reference.factory_physics import set_conveyor_running
        c = cfg.CONVEYORS[cname]
        if self._drive_mode(cname) != cfg.CONVEYOR_DRIVE_SCRIPT:
            # 非脚本驱动: 用既有冻结标志表达"这条带停止推进"
            self._conveyor_frozen[cname] = not running
            return True
        state = set_conveyor_running(
            self.sim, c.get("handle", -1), running, cfg.CONVEYOR_SPEED
        )
        if state is None:
            self._conveyor_script_error.add(cname)
            self.logger.error(
                f"[CONV] {cname} 起停下发/回读失败 → SCRIPT_ERROR "
                f"(不退回位置插值)"
            )
            return False
        self._conveyor_script_error.discard(cname)
        c["script_vel"] = state["vel"]
        self.logger.info(
            f"[CONV] {cname} {'RUNNING' if running else 'STOPPED'} "
            f"(__state__.vel={state['vel']:.4f})"
        )
        return True

    # ═══════════════════════════════════════════════════════════
    # 物料冻结 / 解冻 (方案 §5.3 第 3 条, §10) — 必须可还原
    # ═══════════════════════════════════════════════════════════

    def _freeze_material(self, mat: Material) -> bool:
        """冻结物料在取料点 — 保存原动态属性后置 static。

        优先方案是用 PickupStopper 让物料自然停住 (方案 §5.3 第 2 条);
        只有在挡停体不可用时才走这里的一次性冻结。
        """
        if mat.handle < 0:
            return False
        if mat.uid in self._frozen_material_props:
            return True
        param = getattr(self.sim, "shapeintparam_static", None)
        if param is None:
            return False
        try:
            saved = int(self.sim.getObjectInt32Param(mat.handle, param))
            self.sim.setObjectInt32Param(mat.handle, param, 1)
            self.sim.resetDynamicObject(mat.handle)
        except Exception:
            return False
        self._frozen_material_props[mat.uid] = saved
        return True

    def _unfreeze_material(self, mat: Material) -> bool:
        """恢复物料原动态属性 (方案 §10: 修改前的属性一定存得住, 还原得回)。"""
        saved = self._frozen_material_props.pop(mat.uid, None)
        if saved is None:
            return True
        if mat.handle < 0:
            return False
        param = getattr(self.sim, "shapeintparam_static", None)
        try:
            self.sim.setObjectInt32Param(mat.handle, param, saved)
            self.sim.resetDynamicObject(mat.handle)
            return True
        except Exception:
            return False

    # ═══════════════════════════════════════════════════════════
    # 传送带输送 — Conveyor Movement
    # ═══════════════════════════════════════════════════════════

    def _move_conveyors_kinematic(self, dt: float):
        """无物理回退: 浮点 conv_progress 累加 + 入岛判定。

        ⚠ 这是方案 §5.2 明确标注的 **GEOMETRIC_FALLBACK** 路径: 物料位置由
        Python 按 conv_progress 插值伪造, 不是真实输送。只在 PHYSICS_CONVEYOR
        关闭 (离线逻辑验证) 时使用, 首次进入会打一条 WARNING。
        """
        if not self._geometric_fallback_warned:
            self._geometric_fallback_warned = True
            self.logger.warning(
                "[CONV] GEOMETRIC_FALLBACK: 无物理模式, 物料位置由 conv_progress "
                "插值生成 (非真实摩擦输送)"
            )
        for mat in self.materials:
            if mat.delivered or mat.on_conveyor is None:
                continue
            if mat.awaiting_pickup:
                continue
            cname = mat.on_conveyor
            if cname not in self._CONVEYOR_DST:
                continue

            seg = self._belt_geometry(cname)
            belt_len = seg["length"]
            if belt_len <= 0:
                continue
            mat.conv_progress = min(
                1.0, mat.conv_progress + cfg.CONVEYOR_SPEED / belt_len * dt
            )
            pt = lerp_2d(seg["start"], seg["end"], mat.conv_progress)
            mat.pos = [pt[0], pt[1], seg["surface_z"] + cfg.MATERIAL_SIZE[2] / 2.0]

            if mat.conv_progress >= 1.0:
                src, dst = self._CONVEYOR_DST[cname]
                island = self.islands.get(dst)
                if island and not island.can_accept():
                    self.logger.debug(
                        f"岛 {dst} 无法接受 mat#{mat.uid}，等待中..."
                    )
                    continue
                self._queue_conveyor_pickup(mat, cname, dst)

    def _move_conveyors_physics(self, dt: float):
        """有物理模式: 履带驱动 + 到达检测 (不伪造物料位置)。

        方案 §5.2: 物理模式下**不能**再每帧 setObjectPosition 伪造物料移动 —
        物料应通过 pad 接触、摩擦和动力学求解向前运动。故本函数只做两件事:

          1. 履带推进: 仅当 drive_mode == PYTHON_PAD_DRIVEN 时, Python 按闭环
             采样推进 pad; SCRIPT_DRIVEN 下 pad 由内置脚本驱动, Python 不碰;
             FALLBACK_GEOMETRY 下没有 pad 可推, 退回 conv_progress 插值。
          2. 到达检测: 读物料**世界位姿**判断是否进入末端停止区, 而不是读
             Python 自己累加的 conv_progress。

        末端停止区内的物料会被标记 WAITING_FOR_SUCTION 并停止对应的那一条带
        (方案 §5.3/§5.4)。
        """
        sim = self.sim
        from reference.factory_physics import (
            advance_belt_pad_loop, capture_belt_pad_loop,
        )

        # ── 1. 履带推进 (SCRIPT_DRIVEN 下不碰 pad) ──
        for cname, c in cfg.CONVEYORS.items():
            mode = c.get("drive_mode")
            belt_h = c.get("handle", -1)
            if belt_h is None or belt_h < 0:
                continue
            if cname not in self._conveyor_pad_loops:
                try:
                    self._conveyor_pad_loops[cname] = capture_belt_pad_loop(
                        sim, belt_h
                    )
                except Exception as e:
                    self.logger.warning(
                        f"[CONV] {cname} 履带闭环采集失败: {e}"
                    )
                    self._conveyor_pad_loops[cname] = None

            loop = self._conveyor_pad_loops[cname]
            if (mode == cfg.CONVEYOR_DRIVE_PYTHON and loop is not None
                    and not self._conveyor_frozen.get(cname, False)):
                if not advance_belt_pad_loop(
                    sim, loop, cfg.CONVEYOR_SPEED * dt
                ):
                    self.logger.warning(
                        f"[CONV] {cname} 履带闭环推进失败, 退回位置插值"
                    )
                    self._conveyor_pad_loops[cname] = None

        # ── 2. 到达检测 (按带分组) ──
        belts_mats: dict[str, list[Material]] = {}
        for mat in self.materials:
            if mat.delivered or mat.on_conveyor is None:
                continue
            if mat.on_conveyor not in self._CONVEYOR_DST:
                continue
            belts_mats.setdefault(mat.on_conveyor, []).append(mat)

        for cname, mats_on_belt in belts_mats.items():
            seg = self._belt_geometry(cname)
            _, dst = self._CONVEYOR_DST[cname]
            island = self.islands.get(dst)
            mode = self._drive_mode(cname)

            # ── 物料输送: 沿**实测带面路径**推进 ──
            # ⚠ 4.10 实测结论 (2026-10-13, 见 .superpowers/sdd/progress.md):
            #   履带 pad 由内置脚本 setObjectPosition 移动时, Bullet **不会**
            #   把切向速度传给上方 dynamic 物料 ——
            #     static=1 + kinematic=1        → 物料 10 s 位移 0.009 m
            #     static=0 + kinematic=1        → 物料 10 s 位移 0.008 m
            #   且 4.10 既没有 setObjectVelocity, 也没有 surfaceVelocity 属性
            #   (bullet/ode.surfaceVelocity 均 "property is unknown")。
            #   即方案 §5.2 要求的"靠 pad 接触摩擦把物料带走"在该引擎组合上
            #   **不可达**。按方案 §10 的回退条款, 这里改为显式标注的
            #   KINEMATIC_BELT_TRANSPORT: 物料按实测带速沿实测路径运动, 履带
            #   视觉由脚本以同一速度驱动, 两者同步; 绝不冒充"摩擦输送"。
            if not self._transport_notice:
                self._transport_notice = True
                self.logger.warning(
                    "[CONV] 输送机制 = %s: 4.10 无摩擦输送能力 "
                    "(无 setObjectVelocity / surfaceVelocity 属性; 传送带脚本移动 "
                    "pad 不传递切向速度, 实测 static=1 与 static=0+kinematic=1 "
                    "两种配置物料 10s 位移均 < 0.01 m) → 物料按实测路径以同一带速 "
                    "输送, 履带视觉由脚本驱动; 非物理摩擦驱动。",
                    cfg.CONVEYOR_TRANSPORT_KINEMATIC,
                )
            frozen = self._conveyor_frozen.get(cname, False)
            for mat in mats_on_belt:
                if mat.awaiting_pickup or mat.handle < 0:
                    continue
                if frozen:
                    continue
                if seg["length"] <= 0:
                    continue
                mat.conv_progress = min(
                    1.0, mat.conv_progress + cfg.CONVEYOR_SPEED * dt / seg["length"]
                )
                # 到达判据用**行程进度**而不是"距末端半径": 半径判据会让物料
                # 停在距停止点最多 0.18 m 处, 使实际抓取点超出构建期校验过的
                # arm[*].reach_to 位置 (实测 0.733 m → 0.85 m, 恰好越界)。
                pt = lerp_2d(seg["start"], seg["end"], mat.conv_progress)
                mat.transport = cfg.CONVEYOR_TRANSPORT_KINEMATIC
                try:
                    sim.setObjectPosition(
                        mat.handle,
                        [pt[0], pt[1],
                         seg["surface_z"] + cfg.MATERIAL_SIZE[2] / 2.0],
                        sim.handle_world,
                    )
                except Exception:
                    continue

            # 该带是否需要冻结: 有物料停在末端但目标岛拒绝 (背压)
            should_freeze = False
            for mat in mats_on_belt:
                if mat.awaiting_pickup or mat.handle < 0:
                    continue
                # 抓取失败退避中 → 不再立刻重新排队 (否则退避形同虚设)
                if self.sim_time < self._pickup_retry_at.get(mat.uid, 0.0):
                    continue
                if cname in self._conveyor_script_error:
                    # 该带脚本停不下来 → 不再继续往里送物料, 避免越过末端
                    continue
                # 到站判据 = 行程走完 (与物料实际推进同源, 不引入半径模糊)
                if mat.conv_progress < 1.0:
                    continue
                if island and not island.can_accept():
                    mat.frozen = True
                    should_freeze = True
                    continue
                self._queue_conveyor_pickup(mat, cname, dst)
            # 取料等待中同样视为"该带已停" — 与 _set_belt_running 的语义一致
            pickup_pending = any(m.awaiting_pickup for m in mats_on_belt)
            self._conveyor_frozen[cname] = should_freeze or pickup_pending

    # ═══════════════════════════════════════════════════════════
    # 末端取料 — 吸盘接管 (方案 §5.3/§5.4/§6.3)
    # ═══════════════════════════════════════════════════════════

    def _queue_conveyor_pickup(self, mat: Material, cname: str, dst: str):
        """物料进入末端停止区 — Park the material and stop THAT conveyor.

        流程 (方案 §5.3):
          1. 记录物料当前世界位姿与所在传送带
          2. 停止该物料继续接受传送带驱动 (优先挡停体, 否则一次性冻结)
          3. 停止当前这条传送带 (只停这条)
          4. 进入 WAITING_FOR_SUCTION, 等机械臂 IK 到位 + 吸盘命中
        """
        if mat.awaiting_pickup or mat.at_island is not None:
            return
        mat.awaiting_pickup = True
        mat.frozen = True
        mat.conv_progress = 1.0
        mat.state = MATERIAL_STATE_WAITING_FOR_SUCTION

        c = cfg.CONVEYORS[cname]
        seg = self._belt_geometry(cname)
        # 1) 记录实测世界位姿
        try:
            mat.pos = list(self.sim.getObjectPosition(
                mat.handle, self.sim.handle_world))
        except Exception:
            mat.pos = [seg["end"][0], seg["end"][1],
                       seg["surface_z"] + cfg.MATERIAL_SIZE[2] / 2.0]

        # 2) 让物料停住: 挡停体可用时不改动态属性 (让碰撞自然挡住)
        if not (cfg.PICKUP_USE_STOPPER and c.get("stopper_handle", -1) >= 0):
            if self._freeze_material(mat):
                self.logger.debug(
                    f"mat#{mat.uid} 到达 {cname} 末端, 一次性冻结等待吸盘")
        # 3) 停止**当前**传送带
        self._set_belt_running(cname, False)

        mat.history.append(
            (self.sim_time, f"{MATERIAL_STATE_WAITING_FOR_SUCTION} at {cname}")
        )
        item = (mat, cname, dst)
        if not any(existing[0].uid == mat.uid
                   for existing in self._pending_conveyor_pickups):
            self._pending_conveyor_pickups.append(item)
        self.logger.info(
            f"mat#{mat.uid} ({mat.item_type.name}) 停在 {cname} 末端, "
            f"等待吸盘取料 (岛 {dst})"
        )
        # 4) 让机械臂以 IK 路径点前往抓取点 (方案 §6.3/§7)
        arm_key = self._arm_key_for(dst)
        started = False
        if self.robot_animator is not None and arm_key is not None:
            started = self.robot_animator.start_pick_and_place(
                arm_key, mat.pos, self._island_place_point(dst, mat)
            )
        # 离线/局部场景 (无机械臂) 直接交付, 否则物料永远卡在末端
        if not started:
            self._complete_conveyor_pickup(mat, cname, dst)

    def _arm_key_for(self, dst: str) -> Optional[str]:
        """目标岛对应的机械臂键 — Which arm serves this island."""
        if dst in cfg.ISLAND_ROBOTS:
            return dst
        if dst == "E":
            return "E_C"
        return None

    def _island_place_point(self, dst: str, mat: Material) -> list:
        """目标岛台面上的放置点 (按 uid 错开, 避免堆叠)。"""
        if dst not in cfg.WORK_ISLANDS:
            seg = self._belt_geometry("C_to_E")
            return [seg["start"][0], seg["start"][1],
                    seg["surface_z"] + cfg.MATERIAL_SIZE[2] / 2.0
                    + cfg.MATERIAL_SPAWN_GAP]
        entry = cfg.WORK_ISLANDS[dst]
        # 错开幅度 ±0.1 m: 再大就会超出 UR5 工作半径 (构建期 arm[*].reach_to
        # 断言只覆盖取料点, 放置点由这里的幅度保证)
        offset = (mat.uid % 2) * 0.2 - 0.1
        return [entry["pos"][0] + offset, entry["pos"][1],
                cfg.island_top_z(dst) + cfg.MATERIAL_SIZE[2] / 2.0 + 0.02]

    def _complete_conveyor_pickup(self, mat: Material, cname: str,
                                  dst: str) -> bool:
        """完成一次吸盘取料并把物料交给目标工作岛 — Finish suction pickup.

        时序 (方案 §5.3 / §6.3):
          1. 等机械臂 IK 走到抓取点 → 接近传感器命中 → 父子绑定
             (状态 SUCTION_ATTACHED)
          2. 移动期间校验物料相对吸盘的位姿稳定 (verify_suction_hold)
          3. 动作走完全部路径点 → 释放 + 恢复动态属性 → AT_ISLAND

        Returns:
            True 表示本步完成了状态转移; False 表示还要等。
        """
        # 幂等门 — 同一步里离线回退与 _service_conveyor_pickups 都会调用本方法,
        # 少了这道门物料会被 enqueue 两次 (实测导致岛上队列虚高, 触发假溢出)。
        if mat.at_island is not None:
            return True
        if not mat.awaiting_pickup:
            return True
        island = self.islands.get(dst)
        if island is None or not island.can_accept():
            return False
        arm_key = self._arm_key_for(dst)
        animator = self.robot_animator
        arms = getattr(animator, "arms", {}) or {}

        if animator is not None and arm_key in arms:
            if not mat.suction_attached:
                # 机械臂已判定本次取放失败 (IK 连续解不出) → 交给退避重试
                if animator.arm_action_failed(arm_key):
                    self._failed_pickups.add(mat.uid)
                    return False
                # 吸盘只在机械臂真正下探到抓取点之后才允许绑定
                if not animator.has_reached_pick(arm_key):
                    return False
                if not animator.attach_material(arm_key, mat):
                    return False
                mat.state = MATERIAL_STATE_SUCTION_ATTACHED
                mat.history.append(
                    (self.sim_time,
                     f"{MATERIAL_STATE_SUCTION_ATTACHED} on arm {arm_key}")
                )
                self.logger.info(
                    f"mat#{mat.uid} 已被 {arm_key} 吸盘吸附 (SUCTION_ATTACHED)")
                return False
            # 移动期间校验吸附是否失效 (方案 §6.3 第 4 条)
            if not animator.verify_suction_hold(arm_key, mat):
                mat.history.append((self.sim_time, "SUCTION_HOLD_FAILED"))
                self._failed_pickups.add(mat.uid)
            if not animator.is_action_done(arm_key):
                return False
            # 动作完成 → 释放到台面 + 恢复动态属性
            animator.release_material(
                arm_key, mat, drop_position=self._island_place_point(dst, mat)
            )

        self._unfreeze_material(mat)
        mat.on_conveyor = None
        mat.at_island = dst
        mat.awaiting_pickup = False
        mat.suction_attached = False
        mat.frozen = False
        mat.conv_progress = 0.0
        mat.state = MATERIAL_STATE_AT_ISLAND
        island.enqueue(mat.uid)
        if cname == "C_to_E":
            mat.item_type = ItemType.SEMI_CE
        mat.history.append(
            (self.sim_time, f"{MATERIAL_STATE_AT_ISLAND} {dst} (from {cname})")
        )
        self.beat_tracker.start_cycle(dst, self.sim_time)
        # 物料已离开停止区 → 恢复这条传送带 (方案 §5.4)
        self._set_belt_running(cname, True)
        self.logger.info(
            f"mat#{mat.uid} ({mat.item_type.name}) 吸盘取料完成 → 岛 {dst}, "
            f"恢复 {cname}"
        )
        return True

    def _service_conveyor_pickups(self):
        """在机械臂更新后尝试完成末端等待区的取料 (方案 §5.3 第 4 步)。"""
        if not self._pending_conveyor_pickups:
            return
        pending = list(self._pending_conveyor_pickups)
        self._pending_conveyor_pickups.clear()
        for mat, cname, dst in pending:
            # IK 失败 → 退避重试; 退避期内不再逐帧重算
            if mat.uid in self._failed_pickups:
                self._abandon_pickup(mat, cname, dst)
                continue
            retry_at = self._pickup_retry_at.get(mat.uid, 0.0)
            if self.sim_time < retry_at:
                self._pending_conveyor_pickups.append((mat, cname, dst))
                continue
            if not self._complete_conveyor_pickup(mat, cname, dst):
                self._pending_conveyor_pickups.append((mat, cname, dst))

    def _abandon_pickup(self, mat: Material, cname: str, dst: str):
        """IK 抓取失败的处理 — Handle a failed grasp honestly.

        方案 §10: "若 IK 主组和阻尼组都失败，机械臂动作必须失败并释放/保留物料
        状态，不得把关键帧回退姿态当作完成"。

        这里把物料状态退回 `ON_CONVEYOR` 并让它继续停在末端停止区 (传送带保持
        停止), 隔 CONVEYOR_PICKUP_RETRY_DELAY 秒再试一次。物料既不会被判成
        已入岛, 也不会被丢弃。
        """
        self._failed_pickups.discard(mat.uid)
        animator = self.robot_animator
        arm_key = self._arm_key_for(dst)
        if animator is not None and arm_key is not None:
            try:
                animator.release_material(arm_key, mat)
            except Exception:
                pass
        self._unfreeze_material(mat)
        mat.suction_attached = False
        mat.awaiting_pickup = False
        mat.frozen = False
        mat.state = MATERIAL_STATE_ON_CONVEYOR
        mat.history.append(
            (self.sim_time, f"PICKUP_FAILED at {cname} (IK 不可达), "
                            f"{cfg.CONVEYOR_PICKUP_RETRY_DELAY:.1f}s 后重试")
        )
        self._pickup_retry_at[mat.uid] = (
            self.sim_time + cfg.CONVEYOR_PICKUP_RETRY_DELAY
        )
        self.logger.error(
            f"mat#{mat.uid} 在 {cname} 末端抓取失败 (IK 不可达) — 物料留在带面, "
            f"{cfg.CONVEYOR_PICKUP_RETRY_DELAY:.1f}s 后重试; 传送带保持停止"
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
                    self._trigger_robot_action("B", "pick_and_place")
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
                    self._trigger_robot_action("D", "pick_and_place")
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
                    self._trigger_robot_action("A", "pick_and_place")
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
            mat.pos = [pt[0], pt[1], cfg.MATERIAL_CENTER_Z]

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
                    self._trigger_robot_action("C", "pick_and_place")
                    mat.at_island = None
                    mat.on_conveyor = "C_to_E"
                    mat.conv_progress = 0.0
                    if cfg.PHYSICS_CONVEYOR:
                        seg = self._belt_geometry("C_to_E")
                        mat.pos = [seg["start"][0], seg["start"][1],
                                   seg["surface_z"]
                                   + cfg.MATERIAL_SIZE[2] / 2.0
                                   + cfg.MATERIAL_SPAWN_GAP]
                        self.sim.setObjectPosition(
                            mat.handle, mat.pos, self.sim.handle_world
                        )
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

        e_cfg = cfg.WORK_ISLANDS["E"]
        e_pos = [e_cfg["pos"][0], e_cfg["pos"][1],
                 cfg.island_top_z("E") + cfg.MATERIAL_SIZE[2] / 2 + 0.02]
        final = spawn_material(self.sim, ItemType.FINAL, e_pos, self.sim_time,
                               state=MATERIAL_STATE_AT_ISLAND)
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

        物理传送带模式 (PHYSICS_CONVEYOR=True): 在带上面的物料
        (mat.on_conveyor is not None) 由物理引擎带动, Python 不再瞬移 — 否则
        会与物理求解器争抢。其它状态 (AGV/岛/A→C) 仍由 Python 推进。

        处理顺序:
          1. 传送带上的物料 (kinematic: 插值; physics: 跳过)
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

            # 传送带上的物料
            if mat.on_conveyor:
                if cfg.PHYSICS_CONVEYOR:
                    # 物理模式: 物料位置由物理引擎控制, Python 跳过
                    continue
                cname = mat.on_conveyor
                if cname in cfg.CONVEYORS:
                    seg = self._belt_geometry(cname)
                    pt = lerp_2d(seg["start"], seg["end"], mat.conv_progress)
                    new_pos = [pt[0], pt[1],
                               seg["surface_z"] + cfg.MATERIAL_SIZE[2] / 2.0]

            # AGV 上的物料 — 跟随 AGV 调度器的插值位置
            elif mat.on_agv:
                agv_pos = self.agv_scheduler.current_position()
                new_pos = [agv_pos[0], agv_pos[1],
                           cfg.AGV_DECK_Z + cfg.MATERIAL_SIZE[2] / 2.0]

            # 岛上的物料 — 排在台面上方, 按 uid 偏移避免重叠
            elif mat.at_island and mat.at_island in cfg.WORK_ISLANDS:
                ip = cfg.WORK_ISLANDS[mat.at_island]["pos"]
                top_z = cfg.island_top_z(mat.at_island)
                offset = len([
                    m for m in self.materials
                    if m.at_island == mat.at_island and m.uid < mat.uid
                ]) * 0.2
                new_pos = [ip[0] + offset - 0.3, ip[1],
                           top_z + cfg.MATERIAL_SIZE[2] / 2.0 + 0.02]

            # A→C 刚性传输中的物料
            if mat.uid in self.ac_transfer_progress and mat.pos:
                new_pos = [mat.pos[0], mat.pos[1], cfg.MATERIAL_CENTER_Z]

            if new_pos:
                sim.setObjectPosition(mat.handle, new_pos, sim.handle_world)

        # 更新 AGV 车身位置 + 朝向
        if self.agv_handle != -1:
            agv_pos = self.agv_scheduler.current_position()
            sim.setObjectPosition(
                self.agv_handle,
                [agv_pos[0], agv_pos[1], cfg.AGV_DECK_Z],
                sim.handle_world,
            )
            # 朝向 (yaw): 令 AGV 车头指向行进方向。
            # 旧版只设位置不设朝向, 小车始终以初始朝向滑行, 视觉错乱。
            # 用上一帧位置与本帧位置的位移向量计算航向; 位移过小 (近乎静止)
            # 时保留上一朝向, 避免抖动。
            dx = agv_pos[0] - self._agv_prev_pos[0]
            dy = agv_pos[1] - self._agv_prev_pos[1]
            if math.hypot(dx, dy) > 1e-4:
                yaw = math.atan2(dy, dx)
                # pioneer p3dx / 几何体 AGV 的 +X 轴为车头方向,
                # atan2(dy,dx) 即车头航向, 直接作为绕 Z 的偏航角。
                self._agv_yaw = yaw
                self._agv_prev_pos = (agv_pos[0], agv_pos[1])
            sim.setObjectOrientation(
                self.agv_handle,
                [0.0, 0.0, self._agv_yaw],
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
        self._service_conveyor_pickups()

    # ═══════════════════════════════════════════════════════════
    # 辅助方法 — Utilities
    # ═══════════════════════════════════════════════════════════

    def _trigger_robot_action(self, island_key: str, action: str):
        """触发机械臂动作；视觉层故障不影响生产状态机。"""
        if self.robot_animator is None:
            return
        try:
            self.robot_animator.trigger(island_key, action)
        except Exception as e:
            self.logger.debug(f"[ROBOT] {island_key} 动作触发失败: {e}")

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
