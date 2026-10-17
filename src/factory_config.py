"""
工厂仿真场景配置 — Factory Simulation Scene Configuration
=========================================================
所有位置、尺寸、颜色、路径、节拍、异常阈值集中定义于此。
All positions, sizes, colors, paths, beat times, and exception thresholds
are centralized here. Modify this file to tune the simulation without
touching the control logic.

命名规范遵循: /Warehouse/<name>, /Island/<name>, /Conveyor/<name>, /Mobile/<name>
"""

# ═══════════════════════════════════════════════════════════════
# 全局参数
# ═══════════════════════════════════════════════════════════════
FLOOR_SIZE = (18.0, 11.0, 0.05)        # 地板 (X, Y, Z)，中心位于原点
                                          # Y 从 -5.5 到 5.5，容纳 D 岛下移后的布局
FLOOR_POS  = (0.0, 0.0, 0.0)           # 地板中心 = 世界原点 (0,0,0)

# ═══════════════════════════════════════════════════════════════
# 布局 Z 基准 — Layout Z Datum (重构方案 §2.1)
# ═══════════════════════════════════════════════════════════════
# 这是全场景唯一的竖向基准来源。任何模块都不允许再自行推导/补偿高度,
# 需要高度时一律引用下列常量 (或由 build_scene 从包围盒实测后写回
# CONVEYORS[*]["surface_z"] 等运行期字段)。
#
# 取值来自 CoppeliaSim 4.10 场景实测 (2026-10-13, 见 .superpowers/sdd/progress.md):
#   12 块 5×5m concrete 地板上表面 = 0.102
#   低矮平台顶面 (A/B/C/D)         = 0.740, E 岛 = 0.760
#
# 约定:
#   WORK_ISLANDS[*]["top_z"]      平台**顶面** 高度 (不是模型中心)
#   WAREHOUSES[*]["top_z"]        仓库**顶面** 高度
#   build_scene 接收顶面高度, 再用包围盒/尺寸算出模型中心后 setObjectPosition
FLOOR_TOP_Z        = 0.102   # 地面碰撞面上表面
ISLAND_TOP_Z       = 0.300   # 低矮工作台面上表面 (A/B/C/D)
ISLAND_TOP_Z_E     = 0.320   # E 岛最终装配线台面上表面

# 方案 §2.1 第一版约束: 传送带带面与工作台面齐平。
CONVEYOR_SURFACE_Z = ISLAND_TOP_Z

# 物料几何中心 — 由带面高度 + 物料高度推导, 不单独调
MATERIAL_CENTER_Z  = CONVEYOR_SURFACE_Z + 0.10 / 2   # 0.10 = MATERIAL_SIZE[2], 新尺寸 0.10×0.10×0.10
MATERIAL_SPAWN_GAP = 0.02    # 物料生成点相对带面的抬升间隙 (m)

# AGV 承载面 — 以地面为基准, 不复用平台/传送带的 Z 偏移 (方案 §2.2)
AGV_DECK_Z         = FLOOR_TOP_Z + 0.25              # 0.25 = AGV_SIZE[2]

# 构建期几何断言容差 (方案 §3.2) — 只读校验, 不在构建期偷偷挪对象
Z_TOLERANCE        = 0.01

# ═══════════════════════════════════════════════════════════════
# 仿真参数
# ═══════════════════════════════════════════════════════════════
SIM_STEP           = 0.05               # 仿真步长 (s)
CONVEYOR_SPEED     = 0.5                # 传送带速度 (m/s)
AGV_SPEED          = 0.8                # AGV 移动速度 (m/s)
MATERIAL_INTERVAL  = 5.0                # 每个仓库产出物料的时间间隔 (s) - 从3.0增加到5.0
TOTAL_SIM_TIME     = 60.0               # 仿真总时长 (s)，0 表示无限运行
PROCESS_TIME       = 1.5                # 工作岛处理时间 (s) - 从2.0减少到1.5

# ── 物理传送带配置 — Physics conveyor (4.10 friction-driven belt) ──
# True  = 传送带表面 kinematic+static+respondable, 由 Python 推动; 物料
#        dynamic+respondable, 由物理摩擦拖动; 到带末端时由距离判定入岛。
# False = 旧行为: Python 按 conv_progress 浮点累加 + setObjectPosition 瞬移。
# 切换无副作用, 同一场景 build 可反复试两套路径 (见 factory_robot 同名机制)。
# 参考: docs/wiki/02-仿真建模/02-11-动力学仿真设计.md, physicsEngineDifferences.htm:39
PHYSICS_CONVEYOR        = True
# 物料物理参数 (kg, 摩擦) — 0.15×0.15×0.12 m³ 物体适合此量级
MATERIAL_MASS           = 0.05
MATERIAL_FRICTION       = 0.6
# 物料动态属性
MATERIAL_START_IN_SLEEP = False
# 传送带面物理参数 — kinematic 表面; static; respondable
# (geometry thickness 仅用于 stopper, 主带面厚度由模型/回退几何决定)
# 到达判定: 物料世界坐标距带末端 <= ARRIVAL_RADIUS (m)
ARRIVAL_RADIUS          = 0.15
# 摩擦属性候选名 (按所选物理引擎生效; 其他引擎的属性会被 setProperty 静默忽略)
FRICTION_PROPS = (
    "bullet.friction",                # Bullet / MuJoCo(Bullet back) — 优先
    "ode.friction",                   # ODE
    "newton.kineticFriction",         # Newton Dynamics
    "vortex.linearPrimaryAxisFrictionValue",  # Vortex
)
# respondable_mask 打包: int32 = (global_mask << 8) | local_mask
# (objectParameterIDs.htm:165: 两个 8-bit 值, 共享父用 local, 否则用 global)
MATERIAL_RESPONDABLE_MASK_LOCAL  = 0xFF
MATERIAL_RESPONDABLE_MASK_GLOBAL = 0xFF
def _pack_respondable_mask(local: int, global_: int) -> int:
    return ((global_ & 0xFF) << 8) | (local & 0xFF)
MATERIAL_RESPONDABLE_MASK_PACKED = _pack_respondable_mask(
    MATERIAL_RESPONDABLE_MASK_LOCAL, MATERIAL_RESPONDABLE_MASK_GLOBAL)

# ═══════════════════════════════════════════════════════════════
# 工业节拍字典 — Industrial Beat Dictionary
# ═══════════════════════════════════════════════════════════════
# 各工作岛的预设加工节拍（秒）。BeatTracker 会在仿真中实时校验
# 实际耗时与目标值的偏差，超过阈值触发预警和自动降级。
TAKT_TIME = {
    'A': 5.0,       # Assembly/加工岛 A
    'B': 3.0,       # Preprocess/预处理岛 B
    'C': 4.0,       # Processing/处理岛 C
    'D': 6.0,       # QC_Assembly/质检装配岛 D
    'E': 8.0,       # FinalAssembly/最终装配线 E
}

# ═══════════════════════════════════════════════════════════════
# 异常处理阈值 — Exception Thresholds
# ═══════════════════════════════════════════════════════════════
ZMQ_TIMEOUT              = 3.0     # ZMQ 无响应超时 (s)
ZMQ_MAX_RETRIES          = 3       # ZMQ 最大重连次数
CONGESTION_TIMEOUT       = 5.0     # AGV 路径被占用超过此时间 → BLOCKED (s)
TAKT_DEVIATION_THRESHOLD = 1.0     # 节拍偏差超过此值 → WARNING (s)
TAKT_TIMEOUT_FACTOR      = 1.5     # 实际耗时 > TAKT_TIME * 此因子 → TIMEOUT
D_E_STARVATION_THRESHOLD = 15.0    # D→E 任务等待超过此时长 → 优先级升级 (s)
MAX_QUEUE_LENGTH         = 5       # 工作岛队列超过此值 → 溢出控制

# ═══════════════════════════════════════════════════════════════
# 上游依赖映射 — Upstream Dependency Map
# ═══════════════════════════════════════════════════════════════
# 用于工位超时/溢出时暂停上游供料。Key = 下游岛, Value = 上游来源列表
UPSTREAM_MAP = {
    "A": ["S1"],
    "B": ["S1"],
    "C": ["A"],
    "D": ["S2"],
    "E": ["C", "D"],
}

# ═══════════════════════════════════════════════════════════════
# 仓库 (Warehouses)
# ═══════════════════════════════════════════════════════════════
WAREHOUSES = {
    "S1": {
        "alias":  "Warehouse_S1_Main",
        "pos":    (-7.0, 2.0),           # 仅 XY; Z 由 top_z 推出
        "top_z":  FLOOR_TOP_Z + 0.8,     # 顶面高度 = 地面 + 箱体高
        "size":   (1.2, 1.2, 0.8),
        "color":  (0.2, 0.4, 0.9),       # 蓝色 — 主原料库
    },
    "S2": {
        "alias":  "Warehouse_S2_Auxiliary",
        "pos":    (-7.0, -4.5),
        "top_z":  FLOOR_TOP_Z + 0.8,
        "size":   (1.2, 1.2, 0.8),
        "color":  (0.2, 0.8, 0.8),       # 青色 — 辅助原料/零部件库
    },
}

# ═══════════════════════════════════════════════════════════════
# 工作岛 (Work Islands) — 别名遵循 Island_<Name> 命名 (CoppeliaSim 命名不用 '/')
# ═══════════════════════════════════════════════════════════════
WORK_ISLANDS = {
    "A": {
        "alias":  "Island_A_Assembly",
        "pos":    (-2.5, 2.0),           # 仅 XY; 平台中心 Z 由 top_z 推出
        "top_z":  ISLAND_TOP_Z,
        "size":   (1.4, 1.4, 0.28),
        "color":  (0.2, 0.8, 0.3),       # 绿色 — 前端装配/加工岛 A
    },
    "B": {
        "alias":  "Island_B_Preprocess",
        "pos":    (-2.5, -1.0),
        "top_z":  ISLAND_TOP_Z,
        "size":   (1.4, 1.4, 0.28),
        "color":  (0.9, 0.8, 0.2),       # 黄色 — 前端预处理岛 B
    },
    "C": {
        "alias":  "Island_C_Processing",
        "pos":    (2.0, 2.0),
        "top_z":  ISLAND_TOP_Z,
        "size":   (1.4, 1.4, 0.28),
        "color":  (0.9, 0.5, 0.2),       # 橙色 — 中间工序岛 C
    },
    "D": {
        "alias":  "Island_D_QC_Assembly",
        "pos":    (-2.5, -4.5),
        "top_z":  ISLAND_TOP_Z,
        "size":   (1.4, 1.4, 0.28),
        "color":  (0.6, 0.3, 0.8),       # 紫色 — 质检装配岛 D
    },
    "E": {
        "alias":  "Island_E_FinalAssembly",
        "pos":    (6.0, -0.5),
        "top_z":  ISLAND_TOP_Z_E,
        "size":   (1.6, 1.6, 0.32),
        "color":  (0.9, 0.2, 0.2),       # 红色 — 最终装配线 E
    },
}


def island_top_z(name: str) -> float:
    """工作岛台面上表面高度 — Top surface Z of an island platform."""
    return WORK_ISLANDS[name]["top_z"]


def island_center_z(name: str) -> float:
    """工作岛平台几何中心 Z — Platform center = top - height/2."""
    cfg_entry = WORK_ISLANDS[name]
    return cfg_entry["top_z"] - cfg_entry["size"][2] / 2.0


def island_pos3(name: str) -> list:
    """工作岛 [x, y, 中心 z] — 供构建期 setObjectPosition 使用。"""
    return [WORK_ISLANDS[name]["pos"][0], WORK_ISLANDS[name]["pos"][1],
            island_center_z(name)]

# ═══════════════════════════════════════════════════════════════
# 传送带 (Conveyors) — 别名遵循 Conveyor_<Name> 命名 (CoppeliaSim 命名不用 '/')
# ═══════════════════════════════════════════════════════════════
# start/end 是**设计端点** (XY)。运行时实际端点由 build_scene 从内置模型的
# 真实包围盒推导后写回 CONVEYORS[*] 的运行期字段 (方案 §4.2):
#   actual_length   实际带长 (m, 缩放后实测) = 设计长度
#   actual_start    入口端世界 [x, y, z]
#   actual_end      末端世界 [x, y, z]
#   surface_z       带面上表面世界 Z
#   direction       入口→末端单位方向 [dx, dy]
#   width           实际带宽 (m, 随模型等比缩放)
#   entry_handle    入口 dummy 句柄 (物料生成点)
#   stop_handle     末端停止区 dummy 句柄 (吸盘取料点)
#   script_handle   内置 conveyor 脚本句柄 (-1 = 无)
#   drive_mode      "SCRIPT_DRIVEN" | "PYTHON_PAD_DRIVEN" | "FALLBACK_GEOMETRY"
# 控制器只允许读取这组实际值, 不得再用配置端点自行推算 (方案 §4.2)。
CONVEYORS = {
    "S1_to_A": {
        "alias": "Conveyor_S1_to_A",
        "start": (-6.4, 2.0),
        "end":   (-3.2, 2.0),
        "width": 0.5,
        "color": (0.5, 0.5, 0.5),
    },
    "S1_to_B": {
        "alias": "Conveyor_S1_to_B",
        "start": (-6.4, 1.6),
        "end":   (-3.2, -0.6),
        "width": 0.5,
        "color": (0.5, 0.5, 0.5),
    },
    "S2_to_D": {
        "alias": "Conveyor_S2_to_D",
        "start": (-6.4, -4.5),
        "end":   (-3.2, -4.5),
        "width": 0.5,
        "color": (0.5, 0.5, 0.5),
    },
    "C_to_E": {
        "alias": "Conveyor_C_to_E",
        "start": (2.7, 1.6),
        "end":   (5.2, -0.1),
        "width": 0.5,
        "color": (0.5, 0.5, 0.5),
    },
}

# ── 传送带驱动 (方案 §5.1 / §5.4) ────────────────────────────
# 内置 `generic conveyor (belt).ttm` 的 Script 是 customization 脚本, 正文只有
# `require('models.conveyor_customization-3')`, **没有公开函数**, 无法用
# sim.callScriptFunction。它的外部控制接口是 buffer 属性 (模型自带注释给出):
#   sim.setBufferProperty(h, 'customData.__ctrl__', sim.packTable({vel=v}))
#   sim.readCustomTableData(h, '__state__')  → {vel = ..., pos = ...}
# 4.10 实测 (2026-10-13): 默认 vel=0.1 且 pad 确实移动; 下发 {vel=0} 后
# __state__.vel → 5e-7, 实测带速 0.0000 m/s; 下发 {vel=0.5} 后实测 0.427 m/s。
CONVEYOR_CTRL_KEY     = "customData.__ctrl__"
CONVEYOR_STATE_KEY    = "__state__"
# 启动探测采样步数: 每个 pad 在一个仿真步内的位移超过该阈值即判为脚本驱动
CONVEYOR_PROBE_STEPS  = 3
CONVEYOR_PROBE_MIN_MOVE = 1e-5   # m/步

# 传送带驱动模式 — build_scene 写入 CONVEYORS[*]["drive_mode"]。
#   SCRIPT_DRIVEN       内置脚本是 pad 的唯一写入者; Python 只用 __ctrl__ 调速
#   PYTHON_PAD_DRIVEN   脚本不可用/不驱动 → Python 按闭环采样推进 pad
#   FALLBACK_GEOMETRY   内置模型缺失 → 单一几何体, Python 按 conv_progress 插值
# 物料**输送机制** — 与履带驱动源正交。4.10 实测无法做摩擦输送 (见
# factory_controller._move_conveyors_physics 的实测记录), 故物料按实测路径
# 做运动学输送, 履带视觉由脚本以同一带速驱动。日志里显式标注该机制。
CONVEYOR_TRANSPORT_KINEMATIC = "KINEMATIC_BELT_TRANSPORT"

CONVEYOR_DRIVE_SCRIPT = "SCRIPT_DRIVEN"
CONVEYOR_DRIVE_PYTHON = "PYTHON_PAD_DRIVEN"
CONVEYOR_DRIVE_FALLBACK = "FALLBACK_GEOMETRY"

# 末端吸附等待区 — 物料进入该半径内即被视为到达取料点 (m)
CONVEYOR_STOP_MARGIN = 0.18
# 抓取失败 (IK 不可达) 后再次尝试取料的退避时间 (s) — 防止逐帧重试刷屏
CONVEYOR_PICKUP_RETRY_DELAY = 2.0
# 入口 dummy 相对带首端的内缩 (m) — 让物料落在带面上而不是端部滚筒外
CONVEYOR_ENTRY_INSET = 0.10
# 末端停止区 dummy 相对带尾端的内缩 (m) — 物料停住后仍完全在带面上
CONVEYOR_STOP_INSET  = 0.08   # 物料半长 0.075 m, 内缩 0.08 保证物料仍完整压在带面上

# ═══════════════════════════════════════════════════════════════
# 共享 AGV 路径 — Shared AGV Routes
# ═══════════════════════════════════════════════════════════════
# 使用单一共享 AGV，通过优先级调度处理 B→A 和 D→E 两条路径。
# AGV alias: Mobile_AGV_Shared (CoppeliaSim 命名不用 '/')
AGV_ALIAS = "Mobile_AGV_Shared"
AGV_SIZE  = (0.6, 0.4, 0.25)            # AGV 车身尺寸
AGV_COLOR = (0.95, 0.95, 0.95)          # 白色

# ── pioneer p3dx 差速驱动参数 (阶段一 §2.10 / CLAUDE.md §7) ─────────
# AGV 必须通过左右轮 setJointTargetVelocity 驱动; 禁止逐帧写车体位姿。
# 初值取自 pioneer p3dx 官方尺寸, 构建期由 build_scene 实测覆盖
# (见 docs/measurement/phase0_measurements.json)。
AGV_WHEEL_RADIUS   = 0.0981   # 驱动轮半径 (m)
AGV_WHEEL_BASE     = 0.1995   # 左右轮间距 (m)
AGV_MAX_LINEAR_VEL = 0.8      # 车体最大线速度 (m/s)
AGV_MAX_WHEEL_VEL  = 12.0     # 单轮最大角速度 (rad/s)
AGV_MAX_YAW_RATE   = 2.0      # 最大角速度 (rad/s)
AGV_MAX_ACCEL      = 0.5      # 最大加速度 (m/s²), 加速度斜坡用
AGV_CHASSIS_ALIAS  = "p3dx"   # 车体根 shape 叶子名 (构建期改名为 Mobile_AGV_Shared)
AGV_LEFT_WHEEL_ALIAS  = "leftMotor"    # 左驱动轮关节叶子名 (内置模型原样)
AGV_RIGHT_WHEEL_ALIAS = "rightMotor"   # 右驱动轮关节叶子名

# AGV 路径定义: { task_key: (priority_label, waypoints) }
# B_TO_A: 高优先级，D_TO_E: 低优先级（饥饿 15s 后升级）
AGV_ROUTES = {
    "B_TO_A": [
        (-2.5, -1.0),                    # B 岛位置
        (-2.5, 2.0),                     # A 岛位置
    ],
    "D_TO_E": [
        (-2.5, -4.5),                    # D 岛位置
        (-1.0, -2.5),                    # 中途转弯点1
        (2.0, -1.0),                     # 中途转弯点2
        (6.0, -0.5),                     # E 岛位置
    ],
}

# ═══════════════════════════════════════════════════════════════
# 物料 (Materials)
# ═══════════════════════════════════════════════════════════════
MATERIAL_SIZE   = (0.10, 0.10, 0.10)     # 物料方块尺寸
# 物料颜色
MATERIAL_COLORS = {
    "raw_A":      (0.3, 0.7, 1.0),       # 浅蓝 — 从 S1 供给 A 的原料
    "raw_B":      (1.0, 0.7, 0.3),       # 橙色 — 从 S1 供给 B 的原料
    "semi_AB":    (0.3, 1.0, 0.3),       # 绿色 — B→A 转运后的半成品
    "semi_AC":    (1.0, 0.5, 0.5),       # 粉色 — A→C 的半成品
    "raw_D":      (0.7, 0.3, 1.0),       # 紫色 — 从 S2 供给 D 的零部件
    "semi_CE":    (1.0, 0.3, 0.3),       # 红色 — C→E 的半成品
    "part_DE":    (0.8, 0.5, 1.0),       # 淡紫 — D→E 的专用件
    "final_E":    (0.9, 0.9, 0.1),       # 金色 — 最终成品
}

# ═══════════════════════════════════════════════════════════════
# 场景层级父对象 — Scene Hierarchy Parent Group Aliases
# ═══════════════════════════════════════════════════════════════
# 这些是场景中的"文件夹"对象，所有实际设备作为其子对象。
# 注意: CoppeliaSim 命名不支持字符 '/'，所有别名一律用下划线连接，
# 不使用任何斜杠 (详见 CLAUDE.md「已知问题与坑」第 1 条)。
HIERARCHY_ROOTS = [
    "Warehouse",
    "Conveyor",
    "Island",
    "Mobile",
    "Part",
]

# ═══════════════════════════════════════════════════════════════
# CoppeliaSim 内置模型路径 — Built-in Model Base
# ═══════════════════════════════════════════════════════════════
# 优先使用内置模型；若文件不存在则回退到基本几何体
MODEL_BASE = r"C:\Program Files\CoppeliaRobotics\CoppeliaSimEdu\models"

# ═══════════════════════════════════════════════════════════════
# 内置模型相对路径 — Built-in Model Relative Paths
# ═══════════════════════════════════════════════════════════════
# 这些路径相对 MODEL_BASE。若某文件在本机不存在，加载函数会
# 自动回退到基本几何体，不会中断场景构建。
BUILTIN_MODELS = {
    # 机械臂 — 全工作岛统一使用 UR5 (6 轴)。
    # 不再使用 iiwa/Franka: 它们携带的 Lua 定制脚本与 UR5 组合会在
    # 4.10 启动约 3 帧处阻塞, 触发 "abort execution Connectivity >>
    # ZMQ remote API server@addOnScript" 整场冻结 (见 CLAUDE.md §3)。
    "robot_arm_ur5":      "robots/non-mobile/UR5.ttm",

    # 夹爪 — 挂载到机械臂末端
    "gripper_robotiq":   "components/grippers/ROBOTIQ 85.ttm",
    "gripper_rg2":       "components/grippers/RG2.ttm",
    "gripper_suction":   "components/grippers/suction pad.ttm",

    # 传送带 — 替代手绘长方体
    "conveyor_belt":     "equipment/conveyors/generic conveyor (belt).ttm",
    "conveyor_rollers":  "equipment/conveyors/generic conveyor (rollers).ttm",
    "conveyor_efficient": "equipment/conveyors/generic conveyor (efficient).ttm",

    # 移动 AGV — pioneer p3dx (已在使用)
    "agv_pioneer":       "robots/mobile/pioneer p3dx.ttm",

    # 工业地板 — 替代纯色方块
    "floor_concrete":    "infrastructure/floors/5mX5m concrete floor.ttm",
    "floor_metallic":    "infrastructure/floors/5mX5m metallic floor.ttm",
}

# ═══════════════════════════════════════════════════════════════
# 工作岛机械臂配置 — Per-Island Robot Arm Configuration
# ═══════════════════════════════════════════════════════════════
# 每个工作岛挂载一只 UR5 机械臂（E 岛挂两只：C 线臂 + D 线臂）。
# 全部统一为 UR5 (6 轴) — 早期混用 UR5/iiwa/Franka 导致:
#   1) 内置模型各自携带的 Lua 定制脚本组合在 4.10 启动约 3 帧处阻塞,
#      触发 "abort execution Connectivity >> ZMQ remote API server@addOnScript"
#      并使整场冻结 (CLAUDE.md §3 已知问题)。统一型号避免脚本冲突。
#   2) UR5=6 关节, iiwa/Franka=7 关节, 6 元素关键帧数组对 iiwa/Franka 越界/
#      错位, 动画姿态错乱。统一 UR5 后关键帧与关节数严格对齐。
# 机械臂作为对应岛底座的子对象, alias 遵循 Island_<X>/Robot_X 规范
# (CoppeliaSim 命名不用 '/', 用下划线连接, 如 Island_A_Assembly_Robot_A)。
#
# UR5 关节顺序 (CoppeliaSim 内置 UR5.ttm): base→tip 共 6 个旋转关节
#   [joint1, joint2, joint3, joint4, joint5, joint6]
#   joint1=底座回转(绕Z), joint2=肩部俯仰, joint3=肘部,
#   joint4=腕1, joint5=腕2, joint6=腕3(法兰)
# 关键帧角度按此顺序定义, 单位弧度。
#
# model_key:    BUILTIN_MODELS 中的键, 决定用哪种机械臂 (一律 robot_arm_ur5)
# offset:       机械臂基座相对岛中心的 [x, y, z] 偏移（岛局部坐标）
# euler:        机械臂基座姿态 [alpha, beta, gamma] (弧度)，绕 Z 轴旋转
# suction:      末端吸盘模型键；不再使用机械夹爪
ISLAND_ROBOTS = {
    "A": {
        "alias":   "Island_A_Assembly_Robot_A",
        "model_key": "robot_arm_ur5",
        "offset":  [-0.50, 0.35, 0.20],  # 向带尾侧靠近, base_z≈0.360, 保证取料点在 UR5 工作半径内
        "euler":   [0.0, 0.0, 0.0],
        "suction": "gripper_suction",
    },
    "B": {
        "alias":   "Island_B_Preprocess_Robot_B",
        "model_key": "robot_arm_ur5",   # 统一 UR5 (原 iiwa 7 轴 → 6 轴对齐)
        "offset":  [-0.50, 0.35, 0.20],  # 向带尾侧靠近, base_z≈0.360, 保证取料点在 UR5 工作半径内
        "euler":   [0.0, 0.0, 0.0],
        "suction": "gripper_suction",
    },
    "C": {
        "alias":   "Island_C_Processing_Robot_C",
        "model_key": "robot_arm_ur5",   # 统一 UR5 (原 Franka 7 轴 → 6 轴对齔)
        "offset":  [0.0, 0.35, 0.20],   # C 岛无来料传送带, 只做 A→C 后的动作展示
        "euler":   [0.0, 0.0, 0.0],
        "suction": "gripper_suction",
    },
    "D": {
        "alias":   "Island_D_QC_Assembly_Robot_D",
        "model_key": "robot_arm_ur5",
        "offset":  [-0.50, 0.35, 0.20],
        "euler":   [0.0, 0.0, 0.0],
        "suction": "gripper_suction",
    },
    # E 岛为最终装配线，配双臂：C 线臂 + D 线臂协同装配
    "E_C": {
        "alias":   "Island_E_FinalAssembly_Robot_E_C",
        "model_key": "robot_arm_ur5",
        "offset":  [-0.5, 0.35, 0.20],   # C 线（左侧）装配臂
        "euler":   [0.0, 0.0, 0.0],
        "suction": "gripper_suction",
    },
    "E_D": {
        "alias":   "Island_E_FinalAssembly_Robot_E_D",
        "model_key": "robot_arm_ur5",
        "offset":  [0.5, 0.35, 0.55],    # D 线（右侧）装配臂
        "euler":   [0.0, 0.0, 0.0],
        "suction": "gripper_suction",
    },
}

# ═══════════════════════════════════════════════════════════════
# 机械臂关键帧动画参数 — Robot Arm Animation Keyframes (UR5, 6 轴)
# ═══════════════════════════════════════════════════════════════
# 用于 factory_robot.py 的关节角度插值动画。每个动作序列由一组
# 关键帧组成，每个关键帧是 6 个关节角度（弧度）列表，顺序与
# UR5 关节顺序 [j1 底座回转, j2 肩部, j3 肘部, j4 腕1, j5 腕2, j6 法兰]
# 一致 (见 ISLAND_ROBOTS 注释)。
#
# 姿态标定 (基于 UR5 DH 参数 + CoppeliaSim UR5.ttm 零位):
#   - 关键约定: 关节角为正时绕该关节 +Z 轴右手定则方向。
#   - HOME (待机): 肩部 j2≈-π/2 (大臂水平前伸), 肘部 j3≈+π/2 (小臂竖直上举),
#     形成标准 L 形; j1/j4/j5/j6≈0。这是 UR5 最常见的安全待机姿态。
#   - 抓取点: 在 HOME 基础上 j5 略转使腕部下俯, 整体接近工作台面。
#   - 放置点: j1 转 0.6 rad (~34°) 到侧方, 肘部回收下探。
# 关节角度在 UR5 关节限位内 (各关节 ±2π, 多数姿态远离极限)。
# ═══════════════════════════════════════════════════════════════
# 机械臂控制与运控参数 — Arm Control & Motion Parameters
# ═══════════════════════════════════════════════════════════════
# 重构方案 (docs/wiki/98-ProjectUsage/机械臂与传送带代码审查计划.md §9) 落地配置。

# 本体关节控制模式 — "kinematic" | "dynamic"
#   kinematic: 六轴设运动学模式 (4.10 实测 sim.jointmode_kinematic=0), 由 Python
#              下发目标位, 关节按运动轮廓逼近。**默认值** —— 前提是机械臂已被
#              build_scene 转成纯运动学机构 (见下)。
#   dynamic:   保持模型自带 dynamic + position control (4.10 实测
#              jointmode_dynamic=5 且 dynctrl=8), 由物理解算驱动。
#
# ⚠ 本项必须与「连杆是否非动态」配套, 否则机械臂会塌:
#   本版 UR5.ttm 的连杆与夹爪碰撞体是 static=0 respondable=1 (动态启用)。
#   关节处于 kinematic、而连杆仍是动态时, 引擎不为这些体建立约束 → 连杆自由
#   落体 (TCP 世界 Z 由 2.04 m 掉到 0.13 m), 而**关节角读数仍精确等于设定值**,
#   只查关节角会得到假通过 (必须查世界坐标)。
#   故 ARM_CONTROL_MODE == "kinematic" 时, build_scene 会把整条机械臂子树
#   (6 连杆 + 夹爪根/手指/配重块) 设为 static=1 —— 见 build_scene.make_arm_kinematic()。
#
# 实测对照 (2026-10-11, 六台臂):
#   子树 static=1 + 关节 kinematic (构建期切 / 运行期切)  → 正常
#   子树 static=1 + 关节 dynamic                          → 正常
#   子树保持动态 + 关节 kinematic                          → ★塌陷★
#   只设 shapeintparam_kinematic=1 而不设 static            → ★塌陷★ (该标志无效)
# 注意: 常量一律取 sim.jointmode_* —— 4.10 实测 dependent=4/dynamic=5,
# 与旧版 1/2 不同, 写字面量必错。
ARM_CONTROL_MODE = "kinematic"

# 本体关节数不变量 — §9.3: 必须恰好六个 (UR5 六轴)
# 少于六个只驱动采集到的那几个轴, 整臂会静默乱动; 多于六个说明采集越界。
# 两种情况都判设备不可用并告警, 而不是带着半个臂继续跑。
ARM_BODY_JOINT_COUNT = 6

# 动作完成判据 (§9.4): 六轴实际位置与目标位置最大偏差小于该值 (rad)。
# 当前 kinematic 模式下的实测收敛阈值; 关键帧动画的"到位"判定复用。
ARM_POSITION_TOLERANCE = 0.05

# TCP 定位候选叶子名 — 末端执行器参考点 (按此顺序在机械臂子树中匹配)
# 4.10 内置 UR5.ttm 的末端为 "connection" (force sensor), 非 dummy。
ARM_TCP_ALIASES = (
    "connection", "tip", "flange", "linkTip", "tcp", "endpoint",
)

# 吸盘根对象别名 — build_scene 挂载时统一设为该名
ARM_SUCTION_ALIAS = "Suction"
# 旧字段保留供旧场景读取，但新场景不再创建 Gripper 对象。
ARM_GRIPPER_ALIAS = "Gripper"

# ── 吸盘接入 (方案 §6) — 回退路径: 接近传感器 + 父子绑定 ──────
# 实测结论 (2026-10-13): `suction pad.ttm` 自带的脚本是 child script,
# sysCall_sensing 每步枚举全场景 shape 并对**第一个** respondable 且
# checkProximitySensor 命中的对象做 setObjectParent + setLinkDummy ——
# 既没有 activate/deactivate 公开函数 (方案 §6.2 的前提不成立), 又会
# 无条件吸附传送带 pad 等错误对象。故按方案 §6.3 走兼容回退:
#   剥离 suction 脚本, 保留 Sensor/Link 几何, 由 Python 用
#   sim.checkProximitySensor 静默检测 + sim.setObjectParent 显式绑定。
SUCTION_SENSOR_ALIAS   = "Sensor"   # 吸盘模型内的接近传感器叶子名
SUCTION_LINK_ALIAS     = "Link"     # 吸盘模型内的力传感器 (吸附参考点)
# 方案 §6.2 的例外开关: 只有当内置 suction 脚本提供公开的 activate/deactivate
# 函数时才置 True (此时 _strip_model_scripts 会跳过 suction 子树)。
# 实测该模型**没有**公开函数 → 保持 False, 按 §6.3 回退到传感器 + 父子绑定。
SUCTION_USE_BUILTIN_SCRIPT = False
# 连续命中多少帧才认定为"吸住" (防止单帧抖动导致误吸附)
SUCTION_CONFIRM_FRAMES = 2
# 物料与吸盘参考点的最大允许距离 (m) — 超过则判定吸附失效
SUCTION_HOLD_TOLERANCE = 0.05
# 吸附时物料必须处于的合法状态 (方案 §5.3) — 只有等待取料的物料可被吸附
SUCTION_ATTACHABLE_STATES = ("WAITING_FOR_SUCTION",)
# 吸附前置检查: 物料速度超过此值拒绝吸附 (m/s) — 带面移动中的物料不抓
SUCTION_MAX_ATTACH_SPEED = 0.05
# 释放后稳定性判据: 物料速度低于此值视为已稳定 (m/s)
SUCTION_RELEASE_MAX_SPEED = 0.02

# ── 末端停料 (方案 §5.3) ─────────────────────────────────────
# True  = 在每条传送带末端创建 static+respondable 的 PickupStopper 挡停体,
#         让物料自然停在取料点 (优先方案)
# False = 一次性冻结: 记录动态属性 → static=1 → resetDynamicObject,
#         取料后恢复 (方案 §5.3 第 3 条的回退)
PICKUP_USE_STOPPER     = True
PICKUP_STOPPER_SIZE    = (0.04, 0.5, 0.16)  # [沿带方向厚度, 带宽, 高度]
PICKUP_STOPPER_COLOR   = (0.85, 0.85, 0.2)

# ── 机械臂 IK (方案 §7) ──────────────────────────────────────
# 每台臂建立**一次** RobotArmIKContext 并复用; 禁止每个控制周期重建 IK 环境。
ARM_IK_METHOD_MAIN     = "pseudo_inverse"        # 主组
ARM_IK_METHOD_FALLBACK = "damped_least_squares"  # 备用组
ARM_IK_MAX_ITERATIONS  = 120
ARM_IK_MAX_STEP        = 0.5      # 每周期最大关节增量 (rad)
ARM_IK_PRECISION       = 1e-3     # 位置精度要求 (m)
ARM_IK_CONSTRAINT      = "pose"   # 约束类型: 位置 + 姿态 (吸盘朝下)
# 取放路径中"抓取点/放置点上方"的抬升高度 (m)。TCP 先到上方点, 再下探。
ARM_PICK_APPROACH_Z    = 0.15
# 单个 IK 路径点连续解不出来的上限 — 超过则放弃本次取放, 交给控制器退避重试
ARM_IK_MAX_ATTEMPTS    = 40
# IK 到位判据 (TCP 世界坐标与目标的距离, m)
ARM_IK_ARRIVAL_TOL     = 0.05
# IK target 每步限幅 (m/步) — 一次跳太远数值 IK 解不出来, 必须逐步逼近
ARM_IK_TARGET_STEP     = 0.02
# UR5 标称工作半径 (m) — 构建期用它断言"机械臂底座到取料点"是否可达。
# 实测教训 (2026-10-13): 原 offset=[0,0.35,0.45] 使 A/B/D 的底座到取料点
# 距离达 0.89~0.97 m, 超出工作半径 → simIK 主组/阻尼组必然失败。
ARM_REACH_RADIUS       = 0.85
ARM_REACH_MARGIN       = 0.05     # 留出的安全余量 (m)

# 末端工具轴线最大倾角 (rad) — 方案 §7 "目标朝向固定为吸盘朝下, 避免吸盘在
# 抓取时翻转"。IK 用位置约束求解, 翻转风险由这条硬判据在**抓取时刻**挡住:
# 吸盘 Z 轴相对动作起始姿态偏转超过该角度就拒绝吸附, 动作判为 FAILED。
ARM_TOOL_MAX_TILT      = 0.35     # ≈20°

ARM_FRAME_DURATION = 0.6   # 单帧插值时长 (秒)
ARM_IDLE_PERIOD    = 4.0    # IDLE 循环抖动周期 (秒)

# 动作序列定义: 每个序列是一组关键帧 [j1, j2, j3, j4, j5, j6]
# UR5 6 关节, 每帧严格 6 元素。idle_wiggle 循环播放, 其余播完停在末帧。
ARM_ANIMATIONS = {
    # 入位: HOME → 抓取点上方 → 下探抓取 → 抬起 → 回转到放置点 → 下探放置 → 释放回退 → 回 HOME
    "pick_and_place": [
        [0.0,  -1.5708, 1.5708, 0.0, 0.0, 0.0],   # HOME (L 形待机)
        [0.0,  -1.4,    1.2,    0.0, -0.9, 0.0],   # 接近抓取点 (腕下俯)
        [0.0,  -1.2,    1.0,    0.0, -1.0, 0.0],   # 下探抓取
        [0.0,  -1.5708, 1.5708, 0.0, -0.9, 0.0],   # 抬起回中
        [0.6,  -1.5708, 1.5708, 0.0, -0.9, 0.0],   # 回转到放置点 (j1 转 34°)
        [0.6,  -1.2,    1.0,    0.0, -1.0, 0.0],   # 下探放置
        [0.6,  -1.4,    1.2,    0.0, -0.9, 0.0],   # 释放回退
        [0.0,  -1.5708, 1.5708, 0.0, 0.0, 0.0],   # 回 HOME
    ],
    # 装配: 双臂对中合并动作（E 岛专用, E_C 向右、E_D 向左对称）
    "assemble": [
        [0.0,  -1.5708, 1.5708, 0.0, 0.0, 0.0],    # HOME
        [0.25, -1.45,   1.35,   0.0, -0.8, 0.0],   # 向中心靠拢
        [0.35, -1.3,    1.2,    0.0, -0.9, 0.0],   # 合并接触
        [0.2,  -1.5,    1.4,    0.0, -0.7, 0.0],   # 合并完成抬起
        [0.0,  -1.5708, 1.5708, 0.0, 0.0, 0.0],    # 回 HOME
    ],
    # 空闲微动: 处理过程中机械臂轻微活动，避免完全静止 (循环播放)
    "idle_wiggle": [
        [0.0,  -1.5708, 1.5708, 0.0,  0.0,  0.0],
        [0.0,  -1.55,   1.55,   0.0, -0.05, 0.03],
        [0.0,  -1.59,   1.59,   0.0,  0.05,-0.03],
        [0.0,  -1.5708, 1.5708, 0.0,  0.0,  0.0],
    ],
}
