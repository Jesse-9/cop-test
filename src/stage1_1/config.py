"""config.py — 步骤 1.1 的集中配置 (06-07 §3–§5).

本模块只描述“要验证什么”和“判定门槛是什么”, 不包含控制循环。
所有坐标、阈值、试验矩阵都在这里集中定义, 控制/评估模块不得散落
硬编码数值 (CLAUDE.md §9)。

冻结顺序 (06-07 §5): 允许抓取区域和稳定标准在运行试验前写死;
试验后不得按观察结果放宽。
"""

from __future__ import annotations

from dataclasses import dataclass

from stage1 import config as base
from stage1.layout import L1_S1_LAYOUT, LayoutSpec

# ═══════════════════════════════════════════════════════════════
# 版本与证据
# ═══════════════════════════════════════════════════════════════

CONFIG_VERSION = "step1.1-2026-10-20"
# 原始运行输出不入库; 报告引用的证据按需复制到
# docs/implementation/evidence/ (见该目录 README)。
EVIDENCE_DIR = "docs/implementation/runs"
EVIDENCE_PREFIX = "step1_1"
LOG_FILE = "step1_1.log"
REPORT_FILE = "docs/implementation/STEP1_1_REPORT.md"

# ═══════════════════════════════════════════════════════════════
# 候选布局 (06-07 §3.1)
# ═══════════════════════════════════════════════════════════════
# 两个机械臂基座分别在传送带 +Y / -Y 侧, 岛体与带面横向错开;
# 每个基座配两个置物台位置。AGV 装卸点保持在与机械臂同侧、
# 0.80 m 可达余量内。所有坐标都是**候选输入**, 是否可用由
# simIK 扫描与几何校验决定, 不在此处预设结论。
#
# 传送带轴向/有效输送区不变: center=(-0.62, 0), 上表面 X[-1.117, -0.12]。
# 取料区中心仍为停止区 world X=-0.20。

_ARM_L1 = dict(
    arm_base=(-0.20, 0.55, base.ISLAND_TOP_Z),
    island_pos=(-0.20, 0.55),
    agv_dock=(0.40, 0.75),
)
_ARM_L2 = dict(
    arm_base=(-0.20, -0.55, base.ISLAND_TOP_Z),
    island_pos=(-0.20, -0.55),
    agv_dock=(0.40, -0.75),
)

CANDIDATE_LAYOUTS: tuple[LayoutSpec, ...] = (
    L1_S1_LAYOUT,
    LayoutSpec(name="L1_S2", station_pos=(-0.80, 0.90),
               include_stopper=False, **_ARM_L1),
    LayoutSpec(name="L2_S1", station_pos=(-0.20, -1.20),
               include_stopper=False, **_ARM_L2),
    LayoutSpec(name="L2_S2", station_pos=(-0.80, -0.90),
               include_stopper=False, **_ARM_L2),
)

# 历史阶段一基线布局, 仅用于步骤 1.1 A 组对照与回归记录。
# 迁移后 stage1 场景不再构建挡停体; `include_stopper` 只是历史标记。
BASELINE_LAYOUT = LayoutSpec(
    name="stage1_baseline",
    arm_base=tuple(base.ARM_BASE_POS),
    island_pos=tuple(base.ISLAND_POS),
    station_pos=tuple(base.STATION_POS),
    agv_dock=tuple(base.AGV_DOCK_POS),
    include_stopper=True,
)

# ═══════════════════════════════════════════════════════════════
# 允许抓取区域 (06-07 §5.1) — 运行前冻结
# ═══════════════════════════════════════════════════════════════
# 推导:
#   X: 上表面行程 world [-1.1172, -0.12]; 物料半长 0.05 → 中心上限 -0.17。
#      取 -0.19 留 2 cm 边缘余量; 下限 -0.40 覆盖传感器触发点 (-0.33)
#      与阶段一停止点 (-0.20), 物料边缘仍远离滚筒区。
#   Y: 带宽 0.20, 物料半宽 0.05 → 中心上限 ±0.05; 取 ±0.04 与
#      HYBRID_DYNAMIC 横向容差一致。
#   Z: 带面 0.45 + 物料半高 0.05 = 0.50; 允许 ±0.02 的接触/回弹偏差。
# 该区域不是 proximity sensor 检测体积, 也不能用 sensor 体积替代。

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

    def boundary_points(self) -> list[tuple[float, float, float]]:
        """中心 + 4 角 + 4 边中点, 共 9 个采样点 (Z 固定在 z_center)。"""
        x0, x1 = self.x
        y0, y1 = self.y
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        xy = [
            (cx, cy),
            (x0, y0), (x0, y1), (x1, y0), (x1, y1),
            (x0, cy), (x1, cy), (cx, y0), (cx, y1),
        ]
        return [(x, y, self.z_center) for x, y in xy]


ALLOWED_PICK_REGION = PickRegion(
    x=(-0.40, -0.19),
    y=(-0.04, 0.04),
    z_center=base.CONVEYOR_SURFACE_Z + base.MATERIAL_HALF_HEIGHT,
    z_tol=0.02,
)

# 取料 TCP 目标 = 物料中心上方一个半高 (吸盘接触面)
PICK_APPROACH_CLEARANCE = base.ARM_APPROACH_CLEARANCE
PICK_LIFT_CLEARANCE = base.ARM_APPROACH_CLEARANCE

# ═══════════════════════════════════════════════════════════════
# 试验矩阵 (06-07 §4.4)
# ═══════════════════════════════════════════════════════════════
# 物料中心在传感器检测区内的三个初始 X (world)。传感器中心 world X=-0.20,
# 半径 0.08 + 物料半长 0.05 → 检测范围约 ±0.13。
INITIAL_POSITIONS = {
    "front": -0.32,     # 前沿: 刚进入检测区
    "middle": -0.255,   # 中部
    "rear": -0.21,      # 后沿: 距允许区下游边界 -0.19 留 2 cm
}

# 只测阶段一已验证的名义速度。最低/最高速度尚无冻结值, 只记录为待冻结项,
# 不自行扩大到设备范围外 (06-07 §4.4)。
SPEEDS_MPS = (base.CONVEYOR_SPEED,)
SPEEDS_PENDING = ("min", "max")

REPEATS_PER_COMBO = 10
# 复现标识; 仿真本身是确定性的, 重复之间只改变初始偏航。
RANDOM_SEED = 20261020
# 每个重复使用小角度初始偏航 (-1.15°, 0°, +1.15°), 覆盖物料姿态敏感性;
# 不引入随机数, 保证可复现。
YAW_OFFSETS = (-0.02, 0.0, 0.02)

GROUP_SPECS: dict[str, dict] = {
    "A": {
        "name": "A_baseline_stopper",
        "description": "保留挡停体, HYBRID_DYNAMIC 补偿, 阶段一当前停带判据",
        "stopper": True,
        "compensation": True,
        "stop_mode": "progress",
        "positions": tuple(INITIAL_POSITIONS),
        "repeats": REPEATS_PER_COMBO,
    },
    "B": {
        "name": "B_no_stopper_active_stop",
        "description": "移除挡停体, 传感器命中后主动零速, 等待稳定后按实测位姿抓取",
        "stopper": False,
        "compensation": True,
        "stop_mode": "sensor",
        "positions": tuple(INITIAL_POSITIONS),
        "repeats": REPEATS_PER_COMBO,
    },
    "C": {
        "name": "C_physics_diagnosis",
        "description": "移除挡停体, 关闭位置补偿, 仅观察动态物料与带面相互作用",
        "stopper": False,
        "compensation": False,
        "stop_mode": "none",
        "positions": tuple(INITIAL_POSITIONS),
        "repeats": REPEATS_PER_COMBO,
    },
}
GROUP_ORDER = ("A", "B", "C")

# C 组额外从入口生成, 检查摩擦输送能否把物料带到检测区 (06-07 §4.2)。
C_ENTRY_REPEATS = 10
C_ENTRY_LABEL = "entry"
C_OBSERVE_TIME = 5.0          # s, 最长观察时间
C_STOP_X = ALLOWED_PICK_REGION.x[1]   # 到达允许区下游边界即停带, 防止越出带尾
C_TRANSPORT_MIN_DX = 0.02     # 位移 > 2 cm 才记为“发生了物理输送”

# B 组在完成 10 次筛选后, 每个初始位置各做 1 次完整取放验证。
FULL_PICK_VALIDATIONS = 1
# A 组阶段一完整流程对照次数 (确认当前流程是否/何时发零速)。
BASELINE_FLOW_RUNS = 3

# ═══════════════════════════════════════════════════════════════
# 时序与稳定门槛
# ═══════════════════════════════════════════════════════════════

SETTLE_STEPS = 10             # 物料生成后先落稳的步数
RUN_TIMEOUT = 10.0            # 等待传感器命中 (s)
PAD_STOP_TIMEOUT = 3.0        # 等待带面停止 (s)
PAD_STOP_VEL_TOL = 0.01       # m/s
PAD_STOP_POS_TOL = 0.002      # 连续两步 pad 局部位移上限 (m)
PAD_STOP_CONFIRM_STEPS = 2

MATERIAL_STABLE_SPEED_TOL = base.SUCTION_MAX_ATTACH_SPEED   # 0.05 m/s
MATERIAL_STABLE_POS_TOL = 0.005   # 稳定窗口内位移上限 (m)
MATERIAL_STABLE_STEPS = 5
MATERIAL_SETTLE_TIMEOUT = 3.0  # s
MATERIAL_MAX_TILT = 0.15       # rad (物料不竖直时拒绝抓取)
MATERIAL_TRAJECTORY_EVERY = 2  # 轨迹采样步长 (避免 JSON 过大)

ARM_MOVE_TIMEOUT = base.ARM_MOVE_TIMEOUT
ARM_RESET_TIMEOUT = 3.0

# ═══════════════════════════════════════════════════════════════
# 布局扫描 (06-07 §3.2)
# ═══════════════════════════════════════════════════════════════

BOUNDARY_SCAN_STEP = 0.01     # m
BOUNDARY_SCAN_MAX = 0.60      # m (从允许区中心向外)
BOUNDARY_SCAN_DIRECTIONS = (
    ("+x", (1.0, 0.0)),
    ("-x", (-1.0, 0.0)),
    ("+y", (0.0, 1.0)),
    ("-y", (0.0, -1.0)),
)

# 选定布局后, 对关键目标做实际关节运动验证 (TCP 世界误差)。
SELECTED_LAYOUT_MOTION_TARGETS = (
    "pick_center", "pick_front", "pick_rear", "pick_left", "pick_right",
    "station", "agv",
)
