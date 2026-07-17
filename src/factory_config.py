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
Z_BASE     = 0.6                         # 所有设备和传送带的工作高度

# ═══════════════════════════════════════════════════════════════
# 仿真参数
# ═══════════════════════════════════════════════════════════════
SIM_STEP           = 0.05               # 仿真步长 (s)
CONVEYOR_SPEED     = 0.5                # 传送带速度 (m/s)
AGV_SPEED          = 0.8                # AGV 移动速度 (m/s)
MATERIAL_INTERVAL  = 5.0                # 每个仓库产出物料的时间间隔 (s) - 从3.0增加到5.0
TOTAL_SIM_TIME     = 60.0               # 仿真总时长 (s)，0 表示无限运行
PROCESS_TIME       = 1.5                # 工作岛处理时间 (s) - 从2.0减少到1.5

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
        "alias":  "/Warehouse/S1_Main",
        "pos":    (-7.0, 2.0, Z_BASE),
        "size":   (1.2, 1.2, 0.8),
        "color":  (0.2, 0.4, 0.9),       # 蓝色 — 主原料库
    },
    "S2": {
        "alias":  "/Warehouse/S2_Auxiliary",
        "pos":    (-7.0, -4.5, Z_BASE),
        "size":   (1.2, 1.2, 0.8),
        "color":  (0.2, 0.8, 0.8),       # 青色 — 辅助原料/零部件库
    },
}

# ═══════════════════════════════════════════════════════════════
# 工作岛 (Work Islands) — 遵循 /Island/<Name> 命名层级
# ═══════════════════════════════════════════════════════════════
WORK_ISLANDS = {
    "A": {
        "alias":  "/Island/A_Assembly",
        "pos":    (-2.5, 2.0, Z_BASE),
        "size":   (1.4, 1.4, 0.9),
        "color":  (0.2, 0.8, 0.3),       # 绿色 — 前端装配/加工岛 A
    },
    "B": {
        "alias":  "/Island/B_Preprocess",
        "pos":    (-2.5, -1.0, Z_BASE),
        "size":   (1.4, 1.4, 0.9),
        "color":  (0.9, 0.8, 0.2),       # 黄色 — 前端预处理岛 B
    },
    "C": {
        "alias":  "/Island/C_Processing",
        "pos":    (2.0, 2.0, Z_BASE),
        "size":   (1.4, 1.4, 0.9),
        "color":  (0.9, 0.5, 0.2),       # 橙色 — 中间工序岛 C
    },
    "D": {
        "alias":  "/Island/D_QC_Assembly",
        "pos":    (-2.5, -4.5, Z_BASE),
        "size":   (1.4, 1.4, 0.9),
        "color":  (0.6, 0.3, 0.8),       # 紫色 — 质检装配岛 D
    },
    "E": {
        "alias":  "/Island/E_FinalAssembly",
        "pos":    (6.0, -0.5, Z_BASE),
        "size":   (1.6, 1.6, 1.0),
        "color":  (0.9, 0.2, 0.2),       # 红色 — 最终装配线 E
    },
}

# ═══════════════════════════════════════════════════════════════
# 传送带 (Conveyors) — 遵循 /Conveyor/<Name> 命名层级
# ═══════════════════════════════════════════════════════════════
CONVEYORS = {
    "S1_to_A": {
        "alias": "/Conveyor/S1_to_A",
        "start": (-6.4, 2.0),
        "end":   (-3.2, 2.0),
        "width": 0.5,
        "color": (0.5, 0.5, 0.5),
    },
    "S1_to_B": {
        "alias": "/Conveyor/S1_to_B",
        "start": (-6.4, 1.6),
        "end":   (-3.2, -0.6),
        "width": 0.5,
        "color": (0.5, 0.5, 0.5),
    },
    "S2_to_D": {
        "alias": "/Conveyor/S2_to_D",
        "start": (-6.4, -4.5),
        "end":   (-3.2, -4.5),
        "width": 0.5,
        "color": (0.5, 0.5, 0.5),
    },
    "C_to_E": {
        "alias": "/Conveyor/C_to_E",
        "start": (2.7, 1.6),
        "end":   (5.2, -0.1),
        "width": 0.5,
        "color": (0.5, 0.5, 0.5),
    },
}

# ═══════════════════════════════════════════════════════════════
# 共享 AGV 路径 — Shared AGV Routes
# ═══════════════════════════════════════════════════════════════
# 使用单一共享 AGV，通过优先级调度处理 B→A 和 D→E 两条路径。
# AGV alias: /Mobile/AGV_Shared
AGV_ALIAS = "/Mobile/AGV_Shared"
AGV_SIZE  = (0.6, 0.4, 0.25)            # AGV 车身尺寸
AGV_COLOR = (0.95, 0.95, 0.95)          # 白色

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
MATERIAL_SIZE   = (0.15, 0.15, 0.12)     # 物料方块尺寸
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
# 这些是场景中的"文件夹"对象，所有实际设备作为其子对象
HIERARCHY_ROOTS = [
    "/Warehouse",
    "/Conveyor",
    "/Island",
    "/Mobile",
    "/Part",
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
    # 机械臂 — 工作岛上的真实工业机器人
    "robot_arm_ur5":      "robots/non-mobile/UR5.ttm",
    "robot_arm_iiwa":     "robots/non-mobile/KUKA LBR iiwa 7 R800.ttm",
    "robot_arm_franka":  "robots/non-mobile/FrankaEmikaPanda.ttm",

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
# 每个工作岛可挂载一只机械臂（E 岛挂两只：C 线臂 + D 线臂）。
# 机械臂作为对应岛底座的子对象，alias 遵循 /Island/<X>/Robot_X 规范。
#
# model_key:    BUILTIN_MODELS 中的键，决定用哪种机械臂
# offset:       机械臂基座相对岛中心的 [x, y, z] 偏移（岛局部坐标）
# euler:        机械臂基座姿态 [alpha, beta, gamma] (弧度)，绕 Z 轴旋转
# gripper:      夹爪模型键，None 表示不挂夹爪
ISLAND_ROBOTS = {
    "A": {
        "alias":   "/Island/A_Assembly/Robot_A",
        "model_key": "robot_arm_ur5",
        "offset":  [0.0, 0.35, 0.45],   # 装配臂靠岛后侧
        "euler":   [0.0, 0.0, 0.0],
        "gripper": "gripper_robotiq",
    },
    "B": {
        "alias":   "/Island/B_Preprocess/Robot_B",
        "model_key": "robot_arm_iiwa",
        "offset":  [0.0, 0.35, 0.45],
        "euler":   [0.0, 0.0, 0.0],
        "gripper": "gripper_rg2",
    },
    "C": {
        "alias":   "/Island/C_Processing/Robot_C",
        "model_key": "robot_arm_franka",
        "offset":  [0.0, 0.35, 0.45],
        "euler":   [0.0, 0.0, 0.0],
        "gripper": "gripper_robotiq",
    },
    "D": {
        "alias":   "/Island/D_QC_Assembly/Robot_D",
        "model_key": "robot_arm_ur5",
        "offset":  [0.0, 0.35, 0.45],
        "euler":   [0.0, 0.0, 0.0],
        "gripper": "gripper_robotiq",
    },
    # E 岛为最终装配线，配双臂：C 线臂 + D 线臂协同装配
    "E_C": {
        "alias":   "/Island/E_FinalAssembly/Robot_E_C",
        "model_key": "robot_arm_ur5",
        "offset":  [-0.5, 0.35, 0.55],   # C 线（左侧）装配臂
        "euler":   [0.0, 0.0, 0.0],
        "gripper": "gripper_robotiq",
    },
    "E_D": {
        "alias":   "/Island/E_FinalAssembly/Robot_E_D",
        "model_key": "robot_arm_ur5",
        "offset":  [0.5, 0.35, 0.55],    # D 线（右侧）装配臂
        "euler":   [0.0, 0.0, 0.0],
        "gripper": "gripper_robotiq",
    },
}

# ═══════════════════════════════════════════════════════════════
# 机械臂关键帧动画参数 — Robot Arm Animation Keyframes
# ═══════════════════════════════════════════════════════════════
# 用于 factory_robot.py 的关节角度插值动画。每个动作序列由一组
# 关键帧组成，每个关键帧是各关节角度（弧度）列表。动画在两帧
# 间线性插值，单帧时长由 ARM_FRAME_DURATION 控制。
#
# 关节角度采用"通用 6 轴机械臂"近似姿态，UR5/iiwa/Franka 关节数
# 略有差异，运行时按实际关节数截断或补零。
ARM_FRAME_DURATION = 0.6   # 单帧插值时长 (秒)
ARM_IDLE_PERIOD    = 4.0    # IDLE 循环抖动周期 (秒)

# 动作序列定义: 每个序列是一组关键帧 [joint_angles...]
ARM_ANIMATIONS = {
    # 入位: 从 HOME 到抓取点上方 → 抓取 → 抬起 → 回 HOME
    "pick_and_place": [
        [0.0,  0.3,  0.0, -1.2, 0.0, 0.0],   # HOME
        [0.0,  0.6,  0.0, -1.5, 0.0, 0.0],   # 接近抓取点
        [0.0,  0.7,  0.0, -1.6, 0.0, 0.0],   # 下探抓取
        [0.0,  0.5,  0.0, -1.3, 0.0, 0.0],   # 抬起
        [0.5,  0.5,  0.0, -1.3, 0.0, 0.0],   # 旋转到放置点
        [0.5,  0.7,  0.0, -1.6, 0.0, 0.0],   # 下探放置
        [0.5,  0.6,  0.0, -1.5, 0.0, 0.0],   # 释放回退
        [0.0,  0.3,  0.0, -1.2, 0.0, 0.0],   # 回 HOME
    ],
    # 装配: 双臂对中合并动作（E 岛专用）
    "assemble": [
        [ 0.0, 0.3, 0.0, -1.2, 0.0, 0.0],   # HOME
        [ 0.3, 0.6, 0.0, -1.5, 0.0, 0.0],   # 向中心靠拢
        [ 0.5, 0.7, 0.0, -1.6, 0.0, 0.0],   # 合并接触
        [ 0.3, 0.5, 0.0, -1.3, 0.0, 0.0],   # 合并完成抬起
        [ 0.0, 0.3, 0.0, -1.2, 0.0, 0.0],   # 回 HOME
    ],
    # 空闲微动: 处理过程中机械臂轻微活动，避免完全静止
    "idle_wiggle": [
        [0.0, 0.3, 0.0, -1.2, 0.0, 0.0],
        [0.0, 0.35, 0.0, -1.25, 0.0, 0.05],
        [0.0, 0.3, 0.0, -1.2, 0.0, 0.0],
    ],
}
