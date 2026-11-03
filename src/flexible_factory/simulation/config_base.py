"""config_base.py — v1 基础层配置: A/B 单站闭环、模型与共享物理参数.

本层保留 v1 迁移前阶段一的实测基线. 所有位置、尺寸、速度、容差、模型路径、
别名与故障注入参数集中在这里。控制模块不得散落硬编码坐标或阈值（见工程参考）。

数值来源:
  早期 CoppeliaSim 4.10.0.0 实机测量与 suction pad 安装标定（2026-10-17/20），
  已冻结为 v1 基线；历史测量原始资料已随旧阶段资料清理。

单位约定: m / rad / s / kg。
坐标约定: X = 主输送方向, Y = 横向, Z = 高度。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..spec import MODEL_BASE, MODEL_PATHS

# ═══════════════════════════════════════════════════════════════
# 仿真与模型
# ═══════════════════════════════════════════════════════════════

SIM_STEP = 0.05                      # 一个 stepping 步的仿真时长 (s)

# 场景构建器版本; 场景结构/构建逻辑变化时必须递增
SCENE_BUILDER_VERSION = "v1_scene_20261103"

# ═══════════════════════════════════════════════════════════════
# 场景别名 — 全部扁平下划线, 禁止 '/' (CLAUDE.md §3)
# ═══════════════════════════════════════════════════════════════

ALIASES = {
    # 层级根
    "roots": ["Warehouse", "Conveyor", "Island", "Mobile", "Part"],
    # 地面与仓库
    "floor": "Floor",
    "warehouse": "Warehouse_Raw",
    # 传送带与传感器
    "conveyor": "Conveyor_In",
    "conveyor_entry": "Conveyor_In_Entry",
    "conveyor_stop": "Conveyor_In_PickupStop",
    "pick_sensor": "Sensor_PickStation",
    "exit_sensor": "Sensor_ConveyorExit",
    # 工位 / 机械臂 / 吸盘
    "island": "Island_A",
    "station": "Island_A_Station",
    "place_target_station": "PlaceTarget_Station",
    "robot": "Robot_UR5_01",
    "suction": "SuctionPad_01",
    "tcp": "SuctionPad_01_TCP",
    "suction_sensor": "Sensor_SuctionPad_01",
    "ik_target": "IK_Target_01",
    # AGV
    "agv": "Mobile_AGV_P3DX",
    "agv_tray": "AGV_LoadTray",
    "agv_wall_front": "AGV_TrayWall_Front",
    "agv_wall_back": "AGV_TrayWall_Back",
    "agv_wall_left": "AGV_TrayWall_Left",
    "agv_wall_right": "AGV_TrayWall_Right",
    "agv_dock_sensor": "Sensor_AGV_Dock",
}

REQUIRED_ALIASES = [
    ALIASES["floor"], ALIASES["warehouse"], ALIASES["conveyor"],
    ALIASES["conveyor_entry"], ALIASES["conveyor_stop"],
    ALIASES["pick_sensor"], ALIASES["island"], ALIASES["station"],
    ALIASES["robot"], ALIASES["suction"], ALIASES["tcp"],
    ALIASES["suction_sensor"], ALIASES["ik_target"], ALIASES["agv"],
    ALIASES["agv_tray"], ALIASES["agv_dock_sensor"],
]

# 旧阶段一场景的挡停体别名。生产场景不再构建它, 仅用于断言旧对象未残留。
LEGACY_STOPPER_ALIAS = "Conveyor_In_PickupStopper"

# ═══════════════════════════════════════════════════════════════
# 地面与静态平台 (06-05 §2.3)
# ═══════════════════════════════════════════════════════════════

FLOOR_SIZE = (8.0, 6.0, 0.1)
FLOOR_CENTER = (0.0, 0.0, -FLOOR_SIZE[2] / 2.0)   # 上表面 = 0.0
FLOOR_TOP_Z = 0.0

ISLAND_TOP_Z = 0.30
ISLAND_SIZE = (0.60, 0.60, ISLAND_TOP_Z)
ISLAND_POS = (0.42, 0.0)                          # 底座中心 (X, Y)

STATION_TOP_Z = 0.30
STATION_SIZE = (0.40, 0.40, STATION_TOP_Z)
STATION_POS = (0.92, 0.42)

WAREHOUSE_POS = (-1.02, 0.42)
WAREHOUSE_SIZE = (0.35, 0.35, 0.25)

# ═══════════════════════════════════════════════════════════════
# 传送带 — 实测 native 尺寸与有效承载区
# ═══════════════════════════════════════════════════════════════

CONVEYOR_NATIVE_LENGTH = 1.2160000030199831
CONVEYOR_NATIVE_WIDTH = 0.2000000029802324
CONVEYOR_TOP_RUN_LOCAL = (-0.4972298351066111, 0.5)   # 上表面 pad 局部 X 范围
CONVEYOR_SURFACE_OFFSET = 0.0                          # 带面相对模型根的局部 Z
CONVEYOR_DESIGN_LENGTH = 1.216                         # 构建期设计长度 (等比缩放)
CONVEYOR_CENTER = (-0.62, 0.0)
CONVEYOR_SURFACE_Z = 0.45
CONVEYOR_SPEED = 0.20                                  # m/s
# 内置 conveyor 的 __config__.accel 默认只有 0.05 m/s²，停车要数秒。
# 构建期改写为 2.0：0.2 m/s 下约 0.10 s（2 帧）停住；再高会让模型脚本
# 在 pos 不变时不再刷新 __state__，速度回读会失真。
CONVEYOR_ACCEL = 2.0                                   # m/s²
CONVEYOR_MAX_STOP_TIME = 3 * SIM_STEP                  # 0.15 s 停车验收上限

CONVEYOR_ENTRY_INSET = 0.10     # 入口相对上表面行程首端内缩 (m)
CONVEYOR_STOP_INSET = 0.08      # 停止区相对上表面行程末端内缩 (m)

# HYBRID_DYNAMIC: 物理接触优先。步骤 1.1 C 组实测当前场景 pad→物料存在
# 真实物理输送 (≈0.20 m/s); 补偿仅用于物料落后于带速时, 沿带面切线做
# 受限补偿。补偿步长不得超过带速 * dt, 且必须仍在有效区内。
TRANSPORT_MODE = "HYBRID_DYNAMIC"
DRIVE_SOURCE_SCRIPT = "SCRIPT_DRIVEN"
CONVEYOR_MAX_COMPENSATION_STEP = CONVEYOR_SPEED * SIM_STEP
CONVEYOR_MAX_LATERAL_ERROR = 0.04  # 物料偏离带中心线的上限 (m)
CONVEYOR_FAULT_DROP_Z = 0.15    # 物料低于带面该值 → CONVEYOR_FAULT

# 内置 conveyor 的控制接口 (模型自带注释; 4.10 实测)
CONVEYOR_CTRL_KEY = "customData.__ctrl__"
CONVEYOR_STATE_KEY = "__state__"

# ═══════════════════════════════════════════════════════════════
# 传感器 (06-04 §4)
# ═══════════════════════════════════════════════════════════════

# 取料传感器: 圆柱体积朝下, 覆盖物料顶部但不触及带面
PICK_SENSOR_Z = CONVEYOR_SURFACE_Z + 0.15
PICK_SENSOR_RANGE = 0.12
PICK_SENSOR_RADIUS = 0.08
PICK_SENSOR_ORIENTATION = (math.pi, 0.0, 0.0)

# 吸盘专用接触传感器: 从 TCP 接触面向下, 只覆盖物料顶面
SUCTION_SENSOR_RANGE = 0.03
SUCTION_SENSOR_RADIUS = 0.06

EXIT_SENSOR_LOCAL_X = CONVEYOR_TOP_RUN_LOCAL[1] - 0.03
EXIT_SENSOR_Z = CONVEYOR_SURFACE_Z + 0.15
EXIT_SENSOR_RANGE = 0.12
EXIT_SENSOR_RADIUS = 0.08

AGV_DOCK_SENSOR_Z = 0.30
AGV_DOCK_SENSOR_RANGE = 0.30
AGV_DOCK_SENSOR_RADIUS = 0.30

SENSOR_INTPARAMS = (16, 16, 0, 0, 0, 0, 0, 0)   # 面数/细分 (4.10 默认)
SENSOR_OPTIONS = 4 | 1                          # 隐藏体积 + explicit handling

# ═══════════════════════════════════════════════════════════════
# 物料 (06-05 §2.5)
# ═══════════════════════════════════════════════════════════════

MATERIAL_SIZE = (0.10, 0.10, 0.10)
MATERIAL_MASS = 0.10
MATERIAL_FRICTION = 0.6
MATERIAL_RESPONDABLE_MASK = 0xFFFF               # (global<<8) | local
MATERIAL_SPAWN_GAP = 0.01
MATERIAL_COLORS = {
    "Part_0001": (0.30, 0.70, 1.00),
    "Part_0002": (1.00, 0.65, 0.25),
}

# ═══════════════════════════════════════════════════════════════
# UR5 与 simIK (06-05 §2.6–§2.9)
# ═══════════════════════════════════════════════════════════════

ARM_SEED_JOINTS = [0.0, 0.0, -math.pi / 2, 0.0, math.pi / 2, 0.0]
ARM_JOINT_LIMIT = (-2.0 * math.pi, 2.0 * math.pi)   # 实测模型区间
ARM_JOINT_TARGET_VEL = 1.5        # rad/s
ARM_JOINT_TARGET_ACCEL = 6.0      # rad/s²
ARM_JOINT_TARGET_JERK = 10.0      # rad/s³
ARM_JOINT_CONVERGE_TOL = 0.02     # rad
ARM_JOINT_STOP_SPEED = 0.10       # 到位时关节最大残余速度 (rad/s)
ARM_APPROACH_CLEARANCE = 0.15     # 取/放点上方预接近高度 (m)
ARM_TCP_POS_TOL = 0.015           # m
ARM_TCP_ANG_TOL = 0.12            # rad
ARM_MOVE_TIMEOUT = 8.0            # s
ARM_IK_MAX_RETRIES = 2
ARM_IK_RETRY_DELAY = 2.0          # s
ARM_REACH_RADIUS = 0.85           # UR5 标称工作半径 (安全断言用)
ARM_SOLVE_PRECISION = [0.01, 0.01]  # simIK element 线性/角度精度
ARM_IK_MAX_ITERATIONS = 200       # simIK group 最大迭代次数

# 吸盘 TCP: 接触面中心, 相对吸盘根 local Z = 实测包围盒最低点。
# 安装翻转后 TCP +Z 与 pad +Z 同向; 真实吸附法向为 pad -Z。
TCP_LOCAL_Z = -0.015000049956142902
TCP_LOCAL_POS = (0.0, 0.0, TCP_LOCAL_Z)
TCP_LOCAL_QUATERNION = (0.0, 0.0, 0.0, 1.0)   # [qx,qy,qz,qw]
TCP_ALIAS = ALIASES["tcp"]

# 抓取目标位姿: TCP 位于物料顶面中心; 工具 Z 轴朝上 (真实吸附面朝下)
TOOL_TARGET_QUATERNION = [0.0, 0.0, 0.0, 1.0]

# ── suction pad 安装变换 (v1 冻结的安装标定) ──────────────────────────
# 模型实测: pad 本地 -Z 是吸附接触方向; Body 的 Z 包围范围
# [-0.015, +0.035] m。安装时绕 connection X 轴转 π, 使 pad -Z 与
# connection +Z 同向, 并沿 +Z 平移 d, 避免背部伸入法兰平面。
# e = 末端 link (link7_visible) 超出 connection 平面的外向突出量:
#     2026-10-20 实时场景 shape 包围盒测量的保守值 (不是网格净距)。
# g = 设计间距, 验收前冻结; 改动模型或安装必须重新标定。
SUCTION_MOUNT_QUATERNION = (1.0, 0.0, 0.0, 0.0)   # Rx(π), [qx,qy,qz,qw]
SUCTION_BACK_LOCAL_Z = 0.03500004019588232
SUCTION_FLANGE_PROTRUSION_Z = 0.007549934089183821
SUCTION_LINK_CLEARANCE = 0.005
SUCTION_MOUNT_Z = (
    SUCTION_BACK_LOCAL_Z
    + SUCTION_FLANGE_PROTRUSION_Z
    + SUCTION_LINK_CLEARANCE
)
SUCTION_TOOL_VERSION = "suction_mount_rx180_20261020"
SUCTION_MOUNT_POS_TOL = 0.0005    # 安装/TCP/接触点位置校验容差 (m)
SUCTION_AXIS_DOT_TOL = 0.001      # 轴向共向判据: dot >= 1 - tol

# ═══════════════════════════════════════════════════════════════
# 吸附与释放 (06-05 §2.9 / CLAUDE.md §6)
# ═══════════════════════════════════════════════════════════════

SUCTION_CONTACT_TOL = 0.030       # TCP 与物料顶面中心的距离容差 (m)
ATTACH_SETTLE_TIMEOUT = 2.0       # 等待工具停稳再吸附的上限 (s)
SUCTION_MAX_ATTACH_SPEED = 0.05   # 物料速度上限 (m/s)
SUCTION_MAX_TOOL_SPEED = 0.10     # 吸附时 TCP/工具速度上限 (m/s)
SUCTION_TILT_TOL = 0.35           # 吸盘轴线与竖直方向夹角上限 (rad ≈ 20°)
SUCTION_RELEASE_MAX_SPEED = 0.10  # 释放后稳定判据 (m/s) — 覆盖载台接触瞬态
SUCTION_SETTLE_STEPS = 15         # 释放后等待稳定的仿真步数
SUCTION_SETTLE_GRACE_STEPS = 2    # 忽略释放/换父后的第一帧速度瞬态
SUCTION_SETTLE_Z_TOL = 0.02       # 物料中心应高于承载面 5cm 的容差 (m)

# ═══════════════════════════════════════════════════════════════
# 无挡板取料: 传感器触发停带 → 物料稳定 → 实测位姿抓取
# (步骤 1.1 B 组流程, 2026-10-20 迁移为阶段一生产流程)
# ═══════════════════════════════════════════════════════════════

PICK_STOP_VEL_TOL = 0.01          # 带面速度回读上限 (m/s)
PICK_STOP_POS_TOL = 0.002         # 连续两步 pad 局部位移上限 (m)
PICK_STOP_CONFIRM_STEPS = 2       # 连续确认步数
PICK_STOP_TIMEOUT = 3.0           # 等待带面停止上限 (s)

# 物料稳定判据使用“每步位移 + 线速度”, 而不是距停带命令的总位移:
# 停带瞬间的最后一次补偿 (≤1 cm) 不应让物料永远判为不稳定。
MATERIAL_STABLE_SPEED_TOL = SUCTION_MAX_ATTACH_SPEED   # 0.05 m/s
MATERIAL_STABLE_POS_TOL = 0.005   # 单步位移上限 (m)
MATERIAL_STABLE_STEPS = 5         # 连续稳定步数
MATERIAL_SETTLE_TIMEOUT = 3.0     # 等待物料稳定上限 (s)
MATERIAL_MAX_TILT = 0.15          # 抓取前物料最大倾角 (rad)

# ═══════════════════════════════════════════════════════════════
# pioneer p3dx (06-05 §2.10)
# ═══════════════════════════════════════════════════════════════

AGV_WHEEL_RADIUS = 0.0975         # 实测轮直径 0.195 / 2
AGV_WHEEL_BASE = 0.3310           # 实测左右轮中心距 |0.1655 - (-0.1655)|
# 实测车体包围盒 0.519 x 0.415 m (phase0_measurements.json), 用于布局压占校验
AGV_FOOTPRINT_SIZE = (0.519, 0.415)
AGV_CHASSIS_MIN_Z = -0.1387857600840104
AGV_CHASSIS_TOP_LOCAL_Z = 0.09823927488851028
AGV_MAX_LINEAR_VEL = 0.35
AGV_MAX_YAW_RATE = 1.0
AGV_MAX_WHEEL_VEL = 6.0
AGV_MAX_ACCEL = 0.25
AGV_TRAY_FRICTION = 1.0           # 载台摩擦 (减少加速时载荷滑移)
AGV_DOCK_POS = (0.42, -0.62)
AGV_ROOT_Z = FLOOR_TOP_Z - AGV_CHASSIS_MIN_Z

AGV_TRAY_THICKNESS = 0.02
AGV_TRAY_SIZE = (0.30, 0.30, AGV_TRAY_THICKNESS)
AGV_TRAY_CENTER_LOCAL_Z = AGV_CHASSIS_TOP_LOCAL_Z + AGV_TRAY_THICKNESS / 2.0
AGV_TRAY_TOP_Z = AGV_ROOT_Z + AGV_CHASSIS_TOP_LOCAL_Z + AGV_TRAY_THICKNESS
AGV_WALL_THICKNESS = 0.010
AGV_WALL_HEIGHT = 0.030
AGV_WALL_OFFSET = 0.155           # 挡边到载台中心的距离

AGV_DRIVE_DISTANCE = 0.50         # 阶段一 AGV 轮速验证行驶距离 (m)
AGV_DRIVE_SPEED = 0.15            # m/s
AGV_BLOCK_TIMEOUT = 2.5           # 轮速已下发但车体停滞多久判 BLOCKED (s)
AGV_DOCK_TIMEOUT = 3.0            # 等待 AGV 到站/停稳的上限 (s)
AGV_DOCK_LIN_TOL = 0.02           # 到站停稳线速度阈值 (m/s)
AGV_MAX_TILT = 0.30               # 装卸前车体最大倾角 (rad)
AGV_TRAY_XY_TOL = 0.12            # 载荷相对载台中心的 XY 容差 (m)
AGV_TRAY_Z_TOL = 0.03             # 载荷高度相对载台顶面的容差 (m)

# ═══════════════════════════════════════════════════════════════
# 放置目标 (历史阶段一基线坐标)
# ═══════════════════════════════════════════════════════════════
# 生产流程使用 `layout.LayoutSpec` 的派生目标; 下面这组常量保留给
# 步骤 1.1 A 组/历史回归对照, 不再被 v1 基础层控制循环直接使用。

# 物料中心的世界目标位姿
STATION_MATERIAL_POSE = (
    STATION_POS[0], STATION_POS[1], STATION_TOP_Z + MATERIAL_SIZE[2] / 2.0)
AGV_MATERIAL_POSE = (
    AGV_DOCK_POS[0], AGV_DOCK_POS[1],
    AGV_TRAY_TOP_Z + MATERIAL_SIZE[2] / 2.0,
)

# 物料中心距带面/台面的竖直距离
MATERIAL_HALF_HEIGHT = MATERIAL_SIZE[2] / 2.0
MATERIAL_PLACE_TOL = 0.03          # 工位放置完成的位置容差 (m)

# ═══════════════════════════════════════════════════════════════
# 允许抓取区域 (步骤 1.1 §5.1, 运行前冻结)
# ═══════════════════════════════════════════════════════════════
# 该区域不是 proximity sensor 的检测体积, 也不能用 sensor 体积替代。
# X 上限 -0.19 m 由带面末端 -0.12 m 再留 2 cm 边缘余量得到;
# 下限 -0.40 m 覆盖传感器触发点与旧的行程停止点。


@dataclass(frozen=True)
class PickRegion:
    x: tuple[float, float]
    y: tuple[float, float]
    z_center: float
    z_tol: float

    def contains(self, pose) -> bool:
        """pose 至少包含世界 (x, y, z); 四元数部分忽略。"""
        return (self.x[0] - 1e-9 <= float(pose[0]) <= self.x[1] + 1e-9
                and self.y[0] - 1e-9 <= float(pose[1]) <= self.y[1] + 1e-9
                and abs(float(pose[2]) - self.z_center) <= self.z_tol + 1e-9)


ALLOWED_PICK_REGION = PickRegion(
    x=(-0.40, -0.19),
    y=(-0.04, 0.04),
    z_center=CONVEYOR_SURFACE_Z + MATERIAL_HALF_HEIGHT,
    z_tol=0.02,
)

# ═══════════════════════════════════════════════════════════════
# 任务与超时
# ═══════════════════════════════════════════════════════════════

PICK_SENSOR_TIMEOUT = 15.0        # 等待物料到取料位 (s)
MISMATCH_TIMEOUT = 3.0            # 命中非目标物料后保持等待的上限 (s)
TASK_TIMEOUT = 60.0               # 单任务总超时 (s)
DEFAULT_LOG_FILE = "flexible_factory.log"

FAULT_NONE = "none"
FAULT_NO_TARGET = "no_target"           # 传送带停转, 物料不到达
FAULT_UID_MISMATCH = "uid_mismatch"     # 取料位放另一物料
FAULT_IK_UNREACHABLE = "ik_unreachable" # 放置点移出工作半径
FAULT_SENSOR_DISABLED = "sensor_disabled"
FAULT_AGV_BLOCKED = "agv_blocked"
FAULT_RELEASE_UNSTABLE = "release_unstable"
FAULT_CHOICES = (
    FAULT_NONE, FAULT_NO_TARGET, FAULT_UID_MISMATCH, FAULT_IK_UNREACHABLE,
    FAULT_SENSOR_DISABLED, FAULT_AGV_BLOCKED, FAULT_RELEASE_UNSTABLE,
)

# 故障注入时把放置点移出可达域的距离 (m)
FAULT_IK_OFFSET = (0.0, 0.0, 1.2)

# ═══════════════════════════════════════════════════════════════
# 派生布局 — 不重复手写数字
# ═══════════════════════════════════════════════════════════════

CONVEYOR_ENTRY_LOCAL_X = CONVEYOR_TOP_RUN_LOCAL[0] + CONVEYOR_ENTRY_INSET
CONVEYOR_STOP_LOCAL_X = CONVEYOR_TOP_RUN_LOCAL[1] - CONVEYOR_STOP_INSET
CONVEYOR_ENTRY_WORLD = (
    CONVEYOR_CENTER[0] + CONVEYOR_ENTRY_LOCAL_X,
    CONVEYOR_CENTER[1],
    CONVEYOR_SURFACE_Z,
)
CONVEYOR_STOP_WORLD = (
    CONVEYOR_CENTER[0] + CONVEYOR_STOP_LOCAL_X,
    CONVEYOR_CENTER[1],
    CONVEYOR_SURFACE_Z,
)
CONVEYOR_TRAVEL_LENGTH = abs(CONVEYOR_STOP_LOCAL_X - CONVEYOR_ENTRY_LOCAL_X)
PICK_SENSOR_WORLD = (
    CONVEYOR_STOP_WORLD[0], CONVEYOR_STOP_WORLD[1], PICK_SENSOR_Z)
EXIT_SENSOR_WORLD = (
    CONVEYOR_CENTER[0] + EXIT_SENSOR_LOCAL_X,
    CONVEYOR_CENTER[1], EXIT_SENSOR_Z)

ARM_BASE_POS = (ISLAND_POS[0], ISLAND_POS[1], ISLAND_TOP_Z)

# 物料生成点: 入口中心上方一个半高 + 间隙
MATERIAL_SPAWN_POS = (
    CONVEYOR_ENTRY_WORLD[0], CONVEYOR_ENTRY_WORLD[1],
    CONVEYOR_SURFACE_Z + MATERIAL_HALF_HEIGHT + MATERIAL_SPAWN_GAP,
)
# 物料停在取料位时的中心
MATERIAL_PICK_POS = (
    CONVEYOR_STOP_WORLD[0], CONVEYOR_STOP_WORLD[1],
    CONVEYOR_SURFACE_Z + MATERIAL_HALF_HEIGHT,
)

# 取料/放置 TCP 位姿 (物料顶面中心)
def tcp_pose_above(material_center, dz=MATERIAL_HALF_HEIGHT):
    return [material_center[0], material_center[1], material_center[2] + dz,
            *TOOL_TARGET_QUATERNION]


PICK_TCP_POSE = tcp_pose_above(MATERIAL_PICK_POS)
LIFT_TCP_POSE = tcp_pose_above(MATERIAL_PICK_POS, MATERIAL_HALF_HEIGHT + 0.15)
STATION_TCP_POSE = tcp_pose_above(STATION_MATERIAL_POSE)
AGV_TCP_POSE = tcp_pose_above(AGV_MATERIAL_POSE)


# 历史基线目标解析; 生产流程改用 `LayoutSpec.tcp_pose_for_target` /
# `LayoutSpec.material_pose_for_target`。
def target_tcp_pose(target: str) -> list[float]:
    """按目标名返回 TCP 放置位姿; target ∈ {station, agv}."""
    if target == "station":
        return list(STATION_TCP_POSE)
    if target == "agv":
        return list(AGV_TCP_POSE)
    raise ValueError(f"未知目标: {target!r} (可用: station / agv)")


def material_pose_for_target(target: str) -> list[float]:
    """放置完成时物料中心应处的世界位姿."""
    if target == "station":
        return [*STATION_MATERIAL_POSE, 0.0, 0.0, 0.0, 1.0]
    if target == "agv":
        return [*AGV_MATERIAL_POSE, 0.0, 0.0, 0.0, 1.0]
    raise ValueError(f"未知目标: {target!r} (可用: station / agv)")


def arm_reach_to(point) -> float:
    """UR5 基座到目标点的直线距离 (构建期可达性断言用)."""
    return math.dist(ARM_BASE_POS, point[:3])


# 可达性断言: 取料/放置点必须在 UR5 工作半径减去安全余量内
ARM_REACH_MARGIN = 0.05
REACH_LIMIT = ARM_REACH_RADIUS - ARM_REACH_MARGIN
