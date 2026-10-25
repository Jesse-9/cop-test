"""config.py — 阶段三双工位全部新增参数.

本模块只保存**阶段三新增**参数. 阶段一基线参数 (模型、TCP 安装、物料
物理、带速、步长) 仍来自 :mod:`stage1.config`, 阶段二物理/规划/联锁参数
仍来自 :mod:`stage2.config`; 阶段三不得复制或改写这些值 (CLAUDE.md §9).

新增内容:
  - 双工位布局 `L2_MIRROR`: A 单元保持阶段一/二 L1_S1 坐标, B 单元关于
    Y=0 镜像;
  - 共享取料区、两个 AGV dock、AGV 初始朝向与直线转运路线;
  - 资源锁表、任务流、阻塞/续行、验收故障注入参数.

坐标约定: X = 主输送方向, Y = 横向, Z = 高度; 单位 m / rad / s / kg.
"""

from __future__ import annotations

import math

from stage1 import config as base

# ═══════════════════════════════════════════════════════════════
# 契约、控制器与版本
# ═══════════════════════════════════════════════════════════════

STAGE3_CONTRACT_VERSION = "stage3_contract_20261025_v1"
STAGE3_CONTROLLER_ID = "stage3.unit_controller.Stage3UnitController"
STAGE3_COORDINATOR_ID = "stage3.coordinator.Stage3Coordinator"
STAGE3_RUNNER_ID = "stage3.run.run_loaded_scene_stage3"
STAGE3_PHASE = "stage3"
STAGE3_SCENE_BUILDER_VERSION = "stage3_scene_20261025"
STAGE3_TASK_FLOW_VERSION = "stage3_taskflow_20261025"
STAGE3_RESOURCE_POLICY_VERSION = "stage3_resources_20261025"
STAGE3_AGV_ROUTE_VERSION = "stage3_agv_route_20261025"

# 会话资源前缀: 与阶段二一致, 保存后的工件不得包含这些对象.
SESSION_RESOURCE_PREFIX = "Shadow_"

# ═══════════════════════════════════════════════════════════════
# 布局: L2_MIRROR
# ═══════════════════════════════════════════════════════════════

STAGE3_LAYOUT_NAME = "L2_MIRROR"

# A 单元沿用阶段一/二 L1_S1 的机械臂、岛体与置物台坐标, 不在此重复定义;
# 只有 AGV dock A 因 AGV 初始朝向改为 -Y 而向传送带方向内移 0.05 m,
# 避免车体长轴与 Station A 台面 (y>=1.0) 干涉.
AGV_DOCK_A = (0.40, 0.70)
AGV_DOCK_B = (0.40, -0.70)

# AGV 初始朝向: -Y (面向 dock B). 路线是 x=0.40 的直线穿梭, 不在 dock
# 处原地旋转, 因此不会扫过任一 Island/Station.
AGV_INITIAL_YAW = -math.pi / 2.0
AGV_ROUTE_X = 0.40
AGV_ROUTE_Y = (0.70, -0.70)

# 镜像单元 B: 机械臂/岛体/置物台与 A 关于 Y=0 对称.
UNIT_B = {
    "unit_id": "B",
    "index": 2,
    "arm_base": (-0.20, -0.55, base.ISLAND_TOP_Z),
    "island_pos": (-0.20, -0.55),
    "station_pos": (-0.20, -1.20),
    "dock": AGV_DOCK_B,
}

UNIT_A = {
    "unit_id": "A",
    "index": 1,
    "arm_base": (-0.20, 0.55, base.ISLAND_TOP_Z),
    "island_pos": (-0.20, 0.55),
    "station_pos": (-0.20, 1.20),
    "dock": AGV_DOCK_A,
}

UNIT_IDS = ("A", "B")

# ═══════════════════════════════════════════════════════════════
# 别名 — 全部扁平下划线, 禁止 '/' (CLAUDE.md §3)
# ═══════════════════════════════════════════════════════════════

ALIASES = {
    # 共享取料区/停机点 (不属于任何一台臂)
    "pick_zone": "Stage3_PickZone",
    "place_target_a": "PlaceTarget_Station_A",
    "place_target_b": "PlaceTarget_Station_B",
    # 两个 dock 的接近传感器; A 沿用阶段一别名
    "dock_sensor": {
        "A": base.ALIASES["agv_dock_sensor"],
        "B": "Sensor_AGV_Dock_B",
    },
    # 每台机械臂的稳定编号别名
    "unit": {
        "A": {
            "island": base.ALIASES["island"],
            "station": base.ALIASES["station"],
            "robot": base.ALIASES["robot"],
            "suction": base.ALIASES["suction"],
            "tcp": base.ALIASES["tcp"],
            "suction_sensor": base.ALIASES["suction_sensor"],
            "ik_target": base.ALIASES["ik_target"],
            "model_sensor": "SuctionPad_01_ModelSensor",
        },
        "B": {
            "island": "Island_B",
            "station": "Island_B_Station",
            "robot": "Robot_UR5_02",
            "suction": "SuctionPad_02",
            "tcp": "SuctionPad_02_TCP",
            "suction_sensor": "Sensor_SuctionPad_02",
            "ik_target": "IK_Target_02",
            "model_sensor": "SuctionPad_02_ModelSensor",
        },
    },
}

REQUIRED_ALIASES = [
    *base.REQUIRED_ALIASES,
    "Island_B",
    "Island_B_Station",
    "Robot_UR5_02",
    "SuctionPad_02",
    "SuctionPad_02_TCP",
    "Sensor_SuctionPad_02",
    "IK_Target_02",
    "Sensor_AGV_Dock_B",
    "Stage3_PickZone",
    "PlaceTarget_Station_A",
    "PlaceTarget_Station_B",
]

# 每台臂的影子会话资源别名 (不写入工件)
SHADOW_ALIASES = {
    "A": {
        "arm": "Shadow_UR5_01",
        "pad": "Shadow_SuctionPad_01",
        "tcp": "Shadow_TCP_01",
        "load": "Shadow_Load_01",
    },
    "B": {
        "arm": "Shadow_UR5_02",
        "pad": "Shadow_SuctionPad_02",
        "tcp": "Shadow_TCP_02",
        "load": "Shadow_Load_02",
    },
}

# ═══════════════════════════════════════════════════════════════
# 共享取料区、通道与静态几何
# ═══════════════════════════════════════════════════════════════

# 取料区中心: 与阶段一停带点一致
PICK_ZONE_CENTER = (
    base.CONVEYOR_CENTER[0] + base.CONVEYOR_STOP_LOCAL_X,
    base.CONVEYOR_CENTER[1],
    base.CONVEYOR_SURFACE_Z + base.MATERIAL_HALF_HEIGHT,
)

# 取料区释放判据: TCP 离开 conveyor 矩形并再加该余量后, 才释放 pick_zone.
PICK_ZONE_RELEASE_MARGIN = 0.15

# AGV 在 dock 处朝向 -Y 时的车体占地 (X 宽 0.415, Y 长 0.519).
AGV_FOOTPRINT_AT_DOCK = (base.AGV_FOOTPRINT_SIZE[1], base.AGV_FOOTPRINT_SIZE[0])

# AGV 直线转运通道: 车体 X 半宽 + 安全余量.
AGV_ROUTE_HALF_WIDTH = AGV_FOOTPRINT_AT_DOCK[0] / 2.0
AGV_ROUTE_CLEARANCE = 0.03

# 布局静态验证: 设备/通道之间的最小间隙 (m)
LAYOUT_MIN_CLEARANCE = 0.02

# ═══════════════════════════════════════════════════════════════
# AGV 路线与停靠
# ═══════════════════════════════════════════════════════════════

AGV_ROUTE_SPEED = 0.12              # m/s, 带载/空载同一上限
AGV_ROUTE_RESUME_SPEED = 0.08       # m/s, 清障后续行速度
AGV_ROUTE_ACCEL = base.AGV_MAX_ACCEL
AGV_ROUTE_DECEL = 0.20              # m/s²
AGV_ROUTE_HEADING_KP = 1.5
AGV_ROUTE_MAX_YAW_RATE = 0.40       # rad/s
AGV_ROUTE_ARRIVE_TOL = 0.020        # m, 停车位置误差
AGV_ROUTE_HEADING_TOL = 0.080       # rad, 停车航向误差
AGV_ROUTE_STOP_VEL_TOL = 0.010      # m/s
AGV_ROUTE_SETTLE_STEPS = 5
AGV_ROUTE_SPEED_WINDOW_TOL = 0.020  # m/s, 稳态窗口
AGV_ROUTE_BLOCK_TIMEOUT = 3.0       # s, 轮速已下发但车体不动
AGV_ROUTE_BLOCK_DISPLACEMENT = 0.010  # m, 窗口内最小位移
AGV_ROUTE_TIMEOUT = 45.0            # s, 单方向路线总时长上限
AGV_ROUTE_SLIP_TOL = 0.020          # m, 载荷相对载台滑移上限
AGV_ROUTE_RETRY_LIMIT = 0            # 不自动重试

# ═══════════════════════════════════════════════════════════════
# 资源锁表 (固定顺序申请)
# ═══════════════════════════════════════════════════════════════

RESOURCE_ORDER = (
    "pick_zone",
    "agv_lane",
    "dock_A",
    "dock_B",
    "arm_A",
    "arm_B",
)

RESOURCE_ROLES = {
    "pick_zone": "共享传送带取料区: 同一时刻只允许一台 UR5 跨入/离开",
    "agv_lane": "AGV 直线转运通道 x=0.40, y∈[-0.70,0.70]",
    "dock_A": "dock A 装卸窗口: AGV 对接待命或机械臂装卸时独占",
    "dock_B": "dock B 装卸窗口: AGV 对接待命或机械臂装卸时独占",
    "arm_A": "A 臂运动包络 (诊断/串行化后备)",
    "arm_B": "B 臂运动包络 (诊断/串行化后备)",
}

# ═══════════════════════════════════════════════════════════════
# 任务流
# ═══════════════════════════════════════════════════════════════

TASK_FLOW = (
    "conveyor_pick@A",
    "load_agv@A",
    "transport_A_to_B",
    "unload_agv@B",
    "place_station@B",
)

TASK_TIMEOUT = 180.0                # 单任务仿真时间上限 (s)
TASK_WALL_BUDGET = 900.0            # 单任务墙钟上限 (s)
TASK_MAX_ATTEMPTS = 1               # 阶段三不自动重试
QUEUE_CAPACITY = 8
DUPLICATE_REQUEST_POLICY = "reject_active_same_kind_uid"

# ═══════════════════════════════════════════════════════════════
# 场景/验收
# ═══════════════════════════════════════════════════════════════

SCENARIO_CLOSED_LOOP = "closed_loop"
SCENARIO_CONTENTION = "contention"
SCENARIO_BLOCKED = "blocked"
SCENARIO_UNSTABLE_UNLOAD = "unstable_unload"
SCENARIO_ROUTE_BASELINE = "route_baseline"

STAGE3_SCENARIOS = (
    SCENARIO_CLOSED_LOOP,
    SCENARIO_CONTENTION,
    SCENARIO_BLOCKED,
    SCENARIO_UNSTABLE_UNLOAD,
    SCENARIO_ROUTE_BASELINE,
)

FAULT_NONE = "none"
FAULT_AGV_BLOCKED = "agv_blocked"
FAULT_UNSTABLE_UNLOAD = "unstable_unload"
STAGE3_FAULT_CHOICES = (
    FAULT_NONE,
    FAULT_AGV_BLOCKED,
    FAULT_UNSTABLE_UNLOAD,
)

SCENARIO_FAULT = {
    SCENARIO_CLOSED_LOOP: FAULT_NONE,
    SCENARIO_CONTENTION: FAULT_NONE,
    SCENARIO_BLOCKED: FAULT_AGV_BLOCKED,
    SCENARIO_UNSTABLE_UNLOAD: FAULT_UNSTABLE_UNLOAD,
    SCENARIO_ROUTE_BASELINE: FAULT_NONE,
}

# 受控 AGV 阻塞障碍: 静态、可响应、放在路线中点, 清障后完全移除.
AGV_BLOCK_OBSTACLE_SIZE = (0.15, 0.15, 0.30)
AGV_BLOCK_OBSTACLE_ALIAS = "Fault_Stage3_AGV_Obstacle"
AGV_BLOCK_OBSTACLE_POS = (AGV_ROUTE_X, 0.0, 0.15)
AGV_BLOCK_CLEAR_AFTER_S = 10.0      # BLOCKED 持续该仿真时间后移除障碍

# 不稳定卸载: 在 B 臂 PLACING 阶段把已吸附载荷相对 TCP 偏移该量;
# 联锁应在释放前检出载荷漂移并锁存, 任务不得进入 DONE.
UNSTABLE_INJECT_PHASE = "PLACING"
UNSTABLE_LOAD_OFFSET = (0.040, 0.0, 0.0)
# PLACING 通常只持续一个控制步 (下一 pre_tick 就进入 RELEASING), 因此
# 在第一次观察到 PLACING 时立即注入, 让随后的 pre_tick 联锁先于释放执行.
UNSTABLE_INJECT_DELAY_STEPS = 1

# 锁存后继续推进的步数: 让控制器 post_tick 读取真实带速/车体速度并确认
# 停止; 不改变锁存状态, 不重试任务.
SAFETY_STOP_SETTLE_STEPS = 20

# 验收判据
MATERIAL_PLACE_TOL = base.MATERIAL_PLACE_TOL
AGV_TRAY_XY_TOL = base.AGV_TRAY_XY_TOL
AGV_TRAY_Z_TOL = base.AGV_TRAY_Z_TOL

DEFAULT_LOG_FILE = "stage3_sim.log"
