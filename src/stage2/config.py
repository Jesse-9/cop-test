"""config.py — 阶段二物理约束与运动规划的集中配置.

本模块只保存**阶段二新增**参数. 阶段一基线参数 (布局、模型、带速、步长、
TCP 安装、质量/摩擦) 仍来自 :mod:`stage1.config`, 阶段二不得复制或改写,
以保证父工件配置摘要不变 (06-09 §8.2).

数值来源与状态:
  - 4.10 实机探针 (2026-10-21, ``docs/measurement/probe_stage2_inventory.py``):
    碰撞查询返回形态、collection 语义、self-collision indicator 无效结论;
  - 当前场景实测: 吸盘 Body 包围盒 0.047159 x 0.047159 x 0.05 m, 杯口半径
    由该实测导出;
  - 标注 ``待实测冻结`` 的参数是候选值, 首次完整验收后必须在报告中记录实测
    结果; 未标记者为已由 4.10 探针或几何测量确认.
"""

from __future__ import annotations

import math

from stage1 import config as base

# ═══════════════════════════════════════════════════════════════
# 契约与控制器标识
# ═══════════════════════════════════════════════════════════════

STAGE2_CONTRACT_VERSION = "stage2_contract_20261021"
STAGE2_CONTROLLER_ID = "stage2.unit_controller.Stage2UnitController"
STAGE2_RUNNER_ID = "stage2.run.run_loaded_scene_stage2"
STAGE2_PHASE = "stage2"

# 会话资源别名前缀: 不允许出现在保存后的 .ttt 工件中 (06-09 §8.4)
SESSION_RESOURCE_PREFIX = "Shadow_"

# 影子臂 (派生场景对象/会话资源; 不持久化到工件)
SHADOW_ARM_ALIAS = "Shadow_UR5_01"
SHADOW_PAD_ALIAS = "Shadow_SuctionPad_01"
SHADOW_TCP_ALIAS = "Shadow_TCP_01"
SHADOW_LOAD_ALIAS = "Shadow_Load_01"
FK_POS_TOL = 1e-4                           # 真实/影子 FK 位置容差 (m)
FK_ANG_TOL = 1e-3                           # 真实/影子 FK 姿态容差 (rad)

# ═══════════════════════════════════════════════════════════════
# 物料外表面接触 (06-09 §2)
# ═══════════════════════════════════════════════════════════════

# 杯口投影半径由吸盘 Body 实测包围盒 XY 半宽导出; 启动时重新测量并校验.
CUP_RADIUS_DESIGN = 0.047159 / 2.0          # 0.0235795 m (2026-10-21 实测)
CUP_RADIUS_TOL = 0.001                      # 实测/设计一致性容差 (m)

CONTACT_FACE_TOLERANCE = 0.001              # 面内边界数值容差 (m)
CONTACT_MAX_GAP = 0.005                     # TCP 高于顶面的最大间隙 (m)
CONTACT_MIN_SIGNED_DISTANCE = -0.0015       # 允许下探的数值误差预算 (m)
CONTACT_MAX_LATERAL = 0.020                 # TCP 投影距顶面中心上限 (m)
CONTACT_MAX_NORMAL_ANGLE = 0.12             # 工具探测方向与 -n 夹角上限 (rad)
# 杯口完整覆盖判据独立于传感器/逻辑吸附: |offset| + cup_radius <= half_extent
CONTACT_REQUIRE_CUP_COVERAGE = True

# ═══════════════════════════════════════════════════════════════
# 碰撞查询与安全裕量 (06-09 §3)
# ═══════════════════════════════════════════════════════════════

# checkDistance 的 threshold 是"仅在更近时返回"; 0 表示不设阈值.
COLLISION_MARGIN = 0.005                    # 规划近距阈值 (m) — 待实测冻结
COLLISION_NEAR_MARGIN = 0.010               # 远距预警阈值 (m) — 待实测冻结
# 最终接触/放置构型不套用全工作区统一阈值 (06-09 §3)
CONTACT_PHASES = frozenset({"CONTACT", "PLACE"})

# 显式自碰撞非相邻连杆对: (1,3)..(1,7),(2,4).. ; 下标 1 基.
UR5_LINK_COUNT = 7
UR5_SELF_PAIR_MIN_GAP = 2                   # |i-j| >= 2 才检查
# 吸盘 Body 与这些连杆的接触视为安装接触 (pad 挂在 link7/连接件上)
PAD_ALLOWED_LINK_INDICES = frozenset({6, 7})

# 规划采样 (候选值, 见 06-09 §4.5)
JOINT_SAMPLE_MAX_DELTA = math.radians(2.0)  # rad
SCREEN_SAMPLE_MAX_DELTA = math.radians(8.0) # 候选粗筛步长; 最终计划按 2° 复核
CARTESIAN_SAMPLE_MAX_DELTA = 0.002          # m; collision 采样细分阈值
# 法向接近段的笛卡尔 IK 步长: 先用粗分段求解, 再对关节插值做 FK 偏差
# 复核 (06-09 §5.4). 2 mm 逐点 IK 在 4.10 实机约 100 ms/点, 不作为默认.
CARTESIAN_IK_STEP = 0.010                   # m
CARTESIAN_PATH_POS_TOL = 0.005              # m; 关节插值相对直线偏差
CARTESIAN_PATH_ANG_TOL = 0.020              # rad
CARTESIAN_VERIFY_SAMPLES = 5
LINK_MOTION_MAX_DELTA = 0.025               # m; 关键连杆单采样位移上限
SEGMENT_MAX_SAMPLES = 250                   # 单段采样上限
SEGMENT_MAX_SUBDIVISION = 6
# 自碰撞是 4.10 中较慢的显式连杆对查询 (~2 ms/对); 采样时按步长间隔检查,
# 工作区碰撞每个样本都检查 (更高风险), 端点和细分点必查.
SELF_CHECK_STRIDE = 4

# ═══════════════════════════════════════════════════════════════
# 多解 IK 与局部规划 (06-09 §5)
# ═══════════════════════════════════════════════════════════════

IK_MAX_SEEDS = 12
IK_MAX_CANDIDATES = 6
IK_DEDUPE_TOL = 0.05                        # rad
IK_CONTINUITY_WEIGHT = 1.0                  # 与当前 q 的关节距离权重
IK_WRIST_PENALTY_WEIGHT = 0.05              # |wrap(q6)| 的小权重, 不强制趋零

# 多解 seed 集合: 2026-10-21 实测 (probe_stage2_ik_candidates.py) 在
# 当前原布局上对 pick/station/agv 均找到安全分支; 只用于自由关节段,
# 法向接近段仍以上一解为 seed 连续求解.
IK_SEED_VARIANTS = (
    (0.0, 0.0, -math.pi / 2, 0.0, math.pi / 2, 0.0),
    (0.0, 0.0, math.pi / 2, 0.0, math.pi / 2, 0.0),
    (0.0, -0.8, -1.2, 0.0, math.pi / 2, 0.0),
    (0.0, -0.8, 1.2, 0.0, math.pi / 2, 0.0),
    (0.0, 0.8, -1.2, 0.0, math.pi / 2, 0.0),
    (0.0, 0.8, 1.2, 0.0, math.pi / 2, 0.0),
    (0.0, 0.0, -math.pi / 2, 0.0, -math.pi / 2, 0.0),
    (0.0, 0.0, math.pi / 2, 0.0, -math.pi / 2, 0.0),
    (0.0, -0.8, -1.2, 0.0, -math.pi / 2, 0.0),
    (0.0, 0.8, 1.2, 0.0, -math.pi / 2, 0.0),
    (0.0, -0.8, 1.2, 0.0, -math.pi / 2, 0.0),
    (0.0, 0.8, -1.2, 0.0, -math.pi / 2, 0.0),
    (0.0, -1.5, -0.5, -2.0, 1.57, -1.5),
    (0.0, 1.5, -0.5, 2.0, 1.57, 1.5),
    (0.0, 0.0, -2.5, -1.57, 1.57, 0.0),
    (0.0, 0.0, 2.5, 1.57, 1.57, 0.0),
)

PRE_APPROACH_CLEARANCE = base.ARM_APPROACH_CLEARANCE   # 0.15 m
PRE_APPROACH_HEIGHT_VARIANTS = (0.15, 0.20, 0.25)      # 局部候选
LATERAL_OFFSET_VARIANTS = (0.0, 0.06, -0.06, 0.12, -0.12)  # m, 沿置物台/AGV 横向
TOOL_YAW_VARIANTS = (0.0, math.radians(15), math.radians(-15),
                     math.radians(30), math.radians(-30))
PLAN_MAX_CANDIDATES = 16
PLAN_MAX_TOTAL_SAMPLES = 6000
PLAN_WALL_BUDGET_S = 60.0                   # 实测最坏约 30 s; 冻结 60 s 余量
PLAN_MAX_WAYPOINT_CANDIDATES = 6            # 每个自由目标的候选分支上限
PLAN_MAX_DFS_NODES = 48                     # 局部回溯节点上限

# 带载转运: 先竖直抬升, 再经基座与放置点之间的高位中转, 最后法向放置.
# 2026-10-21 实测: 直接从 lift 关节插值到 pre_place 会让 10 cm 载荷扫过
# 传送带/工位台面; 高位中转 (z>=0.80) 在 station 上找到完整安全路径.
TRANSFER_RISE_HEIGHT = 0.12                 # m, 从 lift 再抬升
TRANSFER_TRANSIT_HEIGHT_MIN = 0.80          # m, 中转 TCP 最低高度
TRANSFER_TRANSIT_OFFSET = 0.20              # m, 中转点向基座方向偏移

# ═══════════════════════════════════════════════════════════════
# 轨迹执行 (06-09 §5.6–§5.7)
# ═══════════════════════════════════════════════════════════════

# 流式执行使用较大的关节步长和更高的 jerk 上限: 4.10 实测将 0.075 rad
# 微步与 jerk=10 组合时每步约 0.45 s, 1.4 rad 接近 8 s 超时; 0.20 rad +
# jerk=100 每步约 0.35 s, 且仍受 maxVel/maxAccel 限制.
EXEC_JOINT_VEL = base.ARM_JOINT_TARGET_VEL        # 1.5 rad/s
EXEC_JOINT_ACCEL = base.ARM_JOINT_TARGET_ACCEL    # 6.0 rad/s²
EXEC_JOINT_JERK = 100.0                           # rad/s³ (实测 2026-10-21)
EXEC_MAX_JOINT_STEP = 0.20                        # rad
EXEC_JOINT_TOL = 0.010                      # rad, 到达路径点判据
EXEC_COMMAND_SETTLE_TOL = 0.005             # rad, 上一条命令实际达到的判据
EXEC_TCP_POS_TOL = 0.010                    # m
EXEC_TCP_ANG_TOL = 0.05                     # rad
# 偏差上限必须大于单步最大关节增量 (0.20), 否则每步新命令都会触发假联锁;
# 超出该值视为外部干涉/失步.
EXEC_MAX_DEVIATION = 0.30                   # rad
# 单个路径点执行上限: 大范围中转 (肩部 ~3 rad) 按 0.2 rad/命令约需
# 5-10 s; 阶段一的 8 s 是单段目标, 不适用于流式多路径点.
EXEC_REACH_TIMEOUT = 20.0                   # s

# ═══════════════════════════════════════════════════════════════
# 释放约束 (06-09 §5.8)
# ═══════════════════════════════════════════════════════════════

RELEASE_POSE_CORRECTION_MAX = 0.010         # 允许的释放位姿修正量 (m)
RELEASE_ANGLE_CORRECTION_MAX = 0.05         # rad
RELEASE_SLIP_TOL = 0.010                    # 稳定窗口内 XY 漂移上限 (m)
RELEASE_SETTLE_STEPS = 15                   # 复用阶段一稳定窗口步数

# ═══════════════════════════════════════════════════════════════
# 主动停带、摩擦输送与稳定 (06-09 §10)
# ═══════════════════════════════════════════════════════════════

CONVEYOR_STOP_VEL_TOL = 0.005               # 带速回读上限 (m/s)
CONVEYOR_STOP_POS_TOL = 0.001               # 连续两步 pad 局部位移上限 (m)
CONVEYOR_STOP_CONFIRM_STEPS = 2
CONVEYOR_STOP_TIMEOUT = 3.0                 # s
CONVEYOR_STOP_MAX_LATENCY = 0.20            # 零速命令 → 实测停止上限 (s)
CONVEYOR_STOP_MAX_OVERRUN = 0.030           # 沿带面切线最大越程 (m)
CONVEYOR_STOP_OBSERVE_STEPS = 60            # 等待停止的仿真步上限
CONVEYOR_MATERIAL_STABLE_SPEED = 0.01       # m/s
CONVEYOR_MATERIAL_STABLE_STEPS = 5
CONVEYOR_MATERIAL_SETTLE_TIMEOUT = 3.0      # s
CONVEYOR_MAX_LATERAL_DRIFT = 0.010          # 停带窗口内横向漂移上限 (m)
CONVEYOR_MAX_HEIGHT_DRIFT = 0.010           # 停带窗口内高度漂移上限 (m)

# HYBRID_DYNAMIC 补偿: 只补"物理输送落后于带速"的差额, 不叠加固定步长.
COMPENSATION_LAG_TOL = 0.002                # 允许的每步落后量 (m)
COMPENSATION_MAX_STEP = base.CONVEYOR_MAX_COMPENSATION_STEP  # 0.01 m
COMPENSATION_MAX_CONSECUTIVE = 20           # 连续补偿步数上限, 超过记故障

# ═══════════════════════════════════════════════════════════════
# 运行期联锁与锁存 (06-09 §7)
# ═══════════════════════════════════════════════════════════════

SAFETY_STOP_CONFIRM_STEPS = 3
SAFETY_STOP_VEL_TOL = 0.010                 # m/s
SAFETY_POSE_DRIFT_TOL = 0.020               # 物料/载荷位姿漂移上限 (m)
SAFETY_TCP_DRIFT_TOL = 0.020                # TCP 偏离计划上限 (m)
SAFETY_LATCH_CODE = "SAFETY_LATCH"

# 运行期联锁验收用的受控障碍 (实验副本, 不进入生产工件)
RUNTIME_HAZARD_FAULT = "runtime_obstacle"
RUNTIME_HAZARD_SIZE = (0.08, 0.08, 0.08)
RUNTIME_HAZARD_ALIAS = "Fault_Runtime_Obstacle"
STAGE2_FAULT_CHOICES = tuple(base.FAULT_CHOICES) + (RUNTIME_HAZARD_FAULT,)
