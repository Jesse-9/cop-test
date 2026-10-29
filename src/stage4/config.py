"""config.py — 阶段四多岛车间全部新增参数.

本模块只保存**阶段四新增**参数. 阶段一基线 (模型、TCP、物料物理、带速、
步长)、阶段二物理/规划/联锁、阶段三双工位布局与两站 AGV 参数仍来自
:mod:`stage1.config` / :mod:`stage2.config` / :mod:`stage3.config`; 阶段四
不得复制或改写这些值 (CLAUDE.md §9). 阶段四在其上增加:

  - 五岛 A–E 与两个来源 S1/S2 的相对布局 `L4_COLUMN`;
  - S1→B、S2→D、C→E 三条独立传送带与 A→C 固定交接;
  - 多类型物料、有限批次、区域容量与背压规则;
  - AGV 直线路线图 (B→A 高优先, D→E 低优先+老化, 空载调机);
  - 阶段四资源表、故障注入与验收口径.

坐标约定: X = 主输送方向, Y = 横向, Z = 高度; 单位 m / rad / s / kg.
所有位置均为**世界坐标**; 传送带局部坐标只在 layout 派生时使用.
"""

from __future__ import annotations

import math

from stage1 import config as base
from stage3 import config as s3cfg

# ═══════════════════════════════════════════════════════════════
# 契约、控制器与版本
# ═══════════════════════════════════════════════════════════════

STAGE4_CONTRACT_VERSION = "stage4_contract_20261028_v1"
STAGE4_CONTROLLER_ID = "stage4.unit_controller.Stage4UnitController"
STAGE4_COORDINATOR_ID = "stage4.coordinator.Stage4Coordinator"
STAGE4_RUNNER_ID = "stage4.run.run_loaded_scene_stage4"
STAGE4_PHASE = "stage4"
STAGE4_SCENE_BUILDER_VERSION = "stage4_scene_20261028"
STAGE4_TASK_FLOW_VERSION = "stage4_taskflow_20261028"
STAGE4_RESOURCE_POLICY_VERSION = "stage4_resources_20261028"
STAGE4_AGV_ROUTE_VERSION = "stage4_agv_map_20261028"
STAGE4_MATERIAL_MODEL_VERSION = "stage4_materials_20261028"
STAGE4_METRICS_VERSION = "stage4_metrics_20261028"

# 会话资源前缀: 与阶段二/三一致, 保存后的工件不得包含这些对象.
SESSION_RESOURCE_PREFIX = "Shadow_"

# 父阶段: 阶段四必须从已验收 stage3 工件只读派生.
PARENT_PHASE = "stage3"

# ═══════════════════════════════════════════════════════════════
# 布局: L4_COLUMN
# ═══════════════════════════════════════════════════════════════
# 拓扑 (06-16 §1):
#   S1 ──► A ──固定衔接──► C ──Conveyor──► E
#    └──► B ──AGV 高优先级 B→A──► A
#   S2 ──► D ──AGV 低优先级 D→E──► E
#
# 首轮沿用阶段三实测的 A/B 侧置结构, 新增 C/D/E 不改变 A/B 坐标:
#   - A/B 基座 (-0.20, ±0.55, 0.30), 置物台 (-0.20, ±1.20),
#     AGV dock (0.40, ±0.70);
#   - C 位于 A 的固定交接下游 (-0.20, 1.75);
#   - E 位于 C→E 输送末端 (-0.20, 2.45);
#   - D 位于 AGV 通道另一侧 (-0.20, -2.45);
#   - 所有 AGV dock 共线于 x=0.40, 车头朝 -Y, 路线为直线, 不在 dock 旋转.

STAGE4_LAYOUT_NAME = "L4_COLUMN"

# 工位定义 (arms = 5). `station_pos` 对 C/D/E 是**标称输出位**, 仅用于
# 布局派生与控制器 TaskLayout; 只有 A/B 有实体置物台.
UNIT_DEFS = {
    "A": {
        "index": 1,
        "arm_base": (-0.20, 0.55, base.ISLAND_TOP_Z),
        "island_pos": (-0.20, 0.55),
        "station_pos": (-0.20, 1.20),
        "dock": s3cfg.AGV_DOCK_A,
        "has_station": True,
        "has_dock": True,
    },
    "B": {
        "index": 2,
        "arm_base": (-0.20, -0.55, base.ISLAND_TOP_Z),
        "island_pos": (-0.20, -0.55),
        "station_pos": (-0.20, -1.20),
        "dock": s3cfg.AGV_DOCK_B,
        "has_station": True,
        "has_dock": True,
    },
    "C": {
        "index": 3,
        # C 与 A→C 交接台相邻; 抬高 C 岛/基座 0.15 m 让 link3 越过
        # Station A 台面 (0.30 m), 避免接近交接点时与台体碰撞.
        "arm_base": (-0.20, 1.75, 0.45),
        "island_pos": (-0.20, 1.75),
        "island_top_z": 0.45,
        "island_size": (0.60, 0.60, 0.45),
        "station_pos": (-0.85, 1.553),      # == C→E 传送带入口 (放置点)
        "dock": None,
        "has_station": False,
        "has_dock": False,
    },
    "D": {
        "index": 4,
        # D 岛/基座抬高到 0.45 m 与带面齐平, link3 不再压到 pad;
        # S2→D 支线东移 0.20 m, 取料点 x≈-0.77 到基座约 0.67 m
        # (H_transfer=0.65), 仍覆盖 dock D (0.40,-2.45).
        "arm_base": (-0.20, -2.45, 0.45),
        "island_pos": (-0.20, -2.45),
        "island_top_z": 0.45,
        "island_size": (0.55, 0.55, 0.45),
        "station_pos": (-0.69, -2.45),      # == S2→D 传送带停止区
        "dock": (0.40, -2.45),
        "has_station": False,
        "has_dock": True,
    },
    "E": {
        "index": 5,
        "arm_base": (-0.20, 2.45, base.ISLAND_TOP_Z),
        "island_pos": (-0.20, 2.45),
        "station_pos": (-0.42, 2.60),       # == E 缓冲 C 位
        "dock": (0.40, 2.45),
        "has_station": False,
        "has_dock": True,
    },
}

UNIT_IDS = ("A", "B", "C", "D", "E")

# 每台臂的稳定别名 (扁平下划线, 禁止 '/'; CLAUDE.md §3).
UNIT_ALIASES = {
    "A": {
        "island": base.ALIASES["island"],
        "station": base.ALIASES["station"],
        "robot": base.ALIASES["robot"],
        "suction": base.ALIASES["suction"],
        "tcp": base.ALIASES["tcp"],
        "suction_sensor": base.ALIASES["suction_sensor"],
        "ik_target": base.ALIASES["ik_target"],
        "model_sensor": "SuctionPad_01_ModelSensor",
        "output_target": s3cfg.ALIASES["place_target_a"],
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
        "output_target": s3cfg.ALIASES["place_target_b"],
    },
    "C": {
        "island": "Island_C",
        "station": "Island_C_Output",
        "robot": "Robot_UR5_03",
        "suction": "SuctionPad_03",
        "tcp": "SuctionPad_03_TCP",
        "suction_sensor": "Sensor_SuctionPad_03",
        "ik_target": "IK_Target_03",
        "model_sensor": "SuctionPad_03_ModelSensor",
        "output_target": "PlaceTarget_Conveyor_C_to_E",
    },
    "D": {
        "island": "Island_D",
        "station": "Island_D_Input",
        "robot": "Robot_UR5_04",
        "suction": "SuctionPad_04",
        "tcp": "SuctionPad_04_TCP",
        "suction_sensor": "Sensor_SuctionPad_04",
        "ik_target": "IK_Target_04",
        "model_sensor": "SuctionPad_04_ModelSensor",
        "output_target": "PlaceTarget_AGV_Dock_D",
    },
    "E": {
        "island": "Island_E",
        "station": "Island_E_Buffer_C",
        "robot": "Robot_UR5_05",
        "suction": "SuctionPad_05",
        "tcp": "SuctionPad_05_TCP",
        "suction_sensor": "Sensor_SuctionPad_05",
        "ik_target": "IK_Target_05",
        "model_sensor": "SuctionPad_05_ModelSensor",
        "output_target": "E_Buffer_C",
    },
}

# 每台臂的影子会话资源 (不写入工件).
SHADOW_ALIASES = {
    "A": {"arm": "Shadow_UR5_01", "pad": "Shadow_SuctionPad_01",
          "tcp": "Shadow_TCP_01", "load": "Shadow_Load_01"},
    "B": {"arm": "Shadow_UR5_02", "pad": "Shadow_SuctionPad_02",
          "tcp": "Shadow_TCP_02", "load": "Shadow_Load_02"},
    "C": {"arm": "Shadow_UR5_03", "pad": "Shadow_SuctionPad_03",
          "tcp": "Shadow_TCP_03", "load": "Shadow_Load_03"},
    "D": {"arm": "Shadow_UR5_04", "pad": "Shadow_SuctionPad_04",
          "tcp": "Shadow_TCP_04", "load": "Shadow_Load_04"},
    "E": {"arm": "Shadow_UR5_05", "pad": "Shadow_SuctionPad_05",
          "tcp": "Shadow_TCP_05", "load": "Shadow_Load_05"},
}

# ═══════════════════════════════════════════════════════════════
# 物流线与固定交接
# ═══════════════════════════════════════════════════════════════
# `center` 是传送带模型根的世界 XY; `yaw` 是绕 Z 的朝向 (0 = +X 下游,
# +pi/2 = +Y 下游). 长度沿用阶段一设计长度 (等比缩放后宽度随之确定).

LINE_DEFS = {
    "line_s1a": {
        "conveyor": base.ALIASES["conveyor"],          # 现有 S1→A 线
        "entry": base.ALIASES["conveyor_entry"],
        "stop": base.ALIASES["conveyor_stop"],
        "sensor": base.ALIASES["pick_sensor"],
        "center": base.CONVEYOR_CENTER,
        "yaw": 0.0,
        "design_length": base.CONVEYOR_DESIGN_LENGTH,
        "source": "S1",
        "destination": "A",
    },
    "line_s1b": {
        "conveyor": "Conveyor_S1_to_B",
        "entry": "Conveyor_S1_to_B_Entry",
        "stop": "Conveyor_S1_to_B_Stop",
        "sensor": "Sensor_Conveyor_S1_to_B_Stop",
        # 支线靠近 B 臂: 取料点 (-0.75, -0.65) 到基座 (-0.20,-0.55)
        # 的水平距离 0.56 m, 抬升到 H_transfer=0.65 时约 0.66 m,
        # 留在 UR5 可达范围内; 原 (-0.85,-0.75) 在 0.80 m 时约 0.84 m.
        "center": (-0.75, -1.07),
        "yaw": math.pi / 2.0,
        "design_length": base.CONVEYOR_DESIGN_LENGTH,
        "source": "S1",
        "destination": "B",
    },
    "line_s2d": {
        "conveyor": "Conveyor_S2_to_D",
        "entry": "Conveyor_S2_to_D_Entry",
        "stop": "Conveyor_S2_to_D_Stop",
        "sensor": "Sensor_Conveyor_S2_to_D_Stop",
        # S2→D 支线东移 0.20 m: 停止区 (-0.69,-2.45), 物料触发点
        # x≈-0.77; D 基座 (-0.20,-2.45,0.45), 抬升/放置均可达.
        "center": (-1.11, -2.45),
        "yaw": 0.0,
        "design_length": base.CONVEYOR_DESIGN_LENGTH,
        "source": "S2",
        "destination": "D",
    },
    "line_ce": {
        "conveyor": "Conveyor_C_to_E",
        "entry": "Conveyor_C_to_E_Entry",
        "stop": "Conveyor_C_to_E_Stop",
        "sensor": "Sensor_Conveyor_C_to_E_Stop",
        "center": (-0.85, 1.95),
        "yaw": math.pi / 2.0,
        "design_length": base.CONVEYOR_DESIGN_LENGTH,
        "source": "C",
        "destination": "E",
    },
}

LINE_IDS = tuple(LINE_DEFS)

# 固定衔接 A→C: 复用 A 工位置物台作为交接台; 单独传感器确认交接物到位.
HANDOFF_AC = {
    "id": "handoff_ac",
    "alias": s3cfg.ALIASES["place_target_a"],
    "table_alias": base.ALIASES["station"],
    "sensor": "Sensor_Handoff_AC",
    "position": UNIT_DEFS["A"]["station_pos"],
    "source_island": "A",
    "destination_island": "C",
}

# E 岛两个输入缓冲 (位于 E 岛台面上, 不需要额外台体).
E_BUFFERS = {
    "buffer_e_c": {
        "alias": "E_Buffer_C",
        "sensor": "Sensor_E_Buffer_C",
        "position": (-0.42, 2.60),
        "accepts": "SEMI_CE",
    },
    "buffer_e_d": {
        "alias": "E_Buffer_D",
        "sensor": "Sensor_E_Buffer_D",
        "position": (0.02, 2.60),
        "accepts": "PART_DE",
    },
}

# 成品输出点 (E 岛台面后缘; 逻辑装配生成, 不由机械臂搬运).
E_OUTPUT = {
    "alias": "E_Output",
    "position": (-0.20, 2.72),
    "material_z": base.ISLAND_TOP_Z + base.MATERIAL_HALF_HEIGHT + 0.01,
}

# 仓库/来源
SOURCE_DEFS = {
    "S1": {
        "warehouse": base.ALIASES["warehouse"],       # Warehouse_Raw
        "outlets": {
            "s1a": {"line": "line_s1a", "alias": base.ALIASES["conveyor_entry"]},
            "s1b": {"line": "line_s1b", "alias": "Source_S1_B"},
        },
    },
    "S2": {
        "warehouse": "Warehouse_S2",
        "outlets": {
            "s2d": {"line": "line_s2d", "alias": "Source_S2_D"},
        },
    },
}

WAREHOUSE_S2_POS = (-2.30, -2.45)
WAREHOUSE_S2_SIZE = base.WAREHOUSE_SIZE

# ═══════════════════════════════════════════════════════════════
# 别名清单
# ═══════════════════════════════════════════════════════════════

ALIASES = {
    "units": UNIT_ALIASES,
    "lines": {line_id: {
        "conveyor": spec["conveyor"], "entry": spec["entry"],
        "stop": spec["stop"], "sensor": spec["sensor"],
    } for line_id, spec in LINE_DEFS.items()},
    "handoff_ac": HANDOFF_AC,
    "e_buffers": {key: {"alias": spec["alias"], "sensor": spec["sensor"]}
                  for key, spec in E_BUFFERS.items()},
    "e_output": E_OUTPUT,
    "dock_sensor": {
        "A": s3cfg.ALIASES["dock_sensor"]["A"],
        "B": s3cfg.ALIASES["dock_sensor"]["B"],
        "D": "Sensor_AGV_Dock_D",
        "E": "Sensor_AGV_Dock_E",
    },
    "warehouse_s2": "Warehouse_S2",
    "source_s1_b": "Source_S1_B",
    "source_s2_d": "Source_S2_D",
}

REQUIRED_ALIASES = [
    *s3cfg.REQUIRED_ALIASES,
    "Island_C", "Island_D", "Island_E",
    "Robot_UR5_03", "Robot_UR5_04", "Robot_UR5_05",
    "SuctionPad_03", "SuctionPad_04", "SuctionPad_05",
    "SuctionPad_03_TCP", "SuctionPad_04_TCP", "SuctionPad_05_TCP",
    "Sensor_SuctionPad_03", "Sensor_SuctionPad_04", "Sensor_SuctionPad_05",
    "IK_Target_03", "IK_Target_04", "IK_Target_05",
    "Conveyor_S1_to_B", "Conveyor_S1_to_B_Entry", "Conveyor_S1_to_B_Stop",
    "Sensor_Conveyor_S1_to_B_Stop",
    "Conveyor_S2_to_D", "Conveyor_S2_to_D_Entry", "Conveyor_S2_to_D_Stop",
    "Sensor_Conveyor_S2_to_D_Stop",
    "Conveyor_C_to_E", "Conveyor_C_to_E_Entry", "Conveyor_C_to_E_Stop",
    "Sensor_Conveyor_C_to_E_Stop",
    "Sensor_AGV_Dock_D", "Sensor_AGV_Dock_E",
    "Sensor_Handoff_AC", "E_Buffer_C", "E_Buffer_D", "E_Output",
    "Sensor_E_Buffer_C", "Sensor_E_Buffer_D",
    "Warehouse_S2", "Source_S1_B", "Source_S2_D",
]

# 构建时必须存在且唯一的阶段四 target dummy (A→C 交接, C→E 放置, AGV D 放置).
TARGET_DUMMIES = {
    "handoff_ac": s3cfg.ALIASES["place_target_a"],
    "c_to_e_place": "PlaceTarget_Conveyor_C_to_E",
    "agv_dock_d": "PlaceTarget_AGV_Dock_D",
    "buffer_e_c": "E_Buffer_C",
    "buffer_e_d": "E_Buffer_D",
    "e_output": "E_Output",
}

# ═══════════════════════════════════════════════════════════════
# 物料类型、谱系与区域容量
# ═══════════════════════════════════════════════════════════════

ITEM_TYPE_RAW_A = "RAW_A"
ITEM_TYPE_RAW_B = "RAW_B"
ITEM_TYPE_SEMI_AB = "SEMI_AB"
ITEM_TYPE_SEMI_AC = "SEMI_AC"
ITEM_TYPE_SEMI_CE = "SEMI_CE"
ITEM_TYPE_RAW_D = "RAW_D"
ITEM_TYPE_PART_DE = "PART_DE"
ITEM_TYPE_FINAL = "FINAL"

ITEM_TYPES = (
    ITEM_TYPE_RAW_A, ITEM_TYPE_RAW_B, ITEM_TYPE_SEMI_AB,
    ITEM_TYPE_SEMI_AC, ITEM_TYPE_SEMI_CE, ITEM_TYPE_RAW_D,
    ITEM_TYPE_PART_DE, ITEM_TYPE_FINAL,
)

# 物料类型颜色 (按类型着色, 便于现场诊断; 不参与识别逻辑).
ITEM_COLORS = {
    ITEM_TYPE_RAW_A: (0.30, 0.70, 1.00),
    ITEM_TYPE_RAW_B: (1.00, 0.65, 0.25),
    ITEM_TYPE_SEMI_AB: (0.90, 0.85, 0.30),
    ITEM_TYPE_SEMI_AC: (0.55, 0.85, 0.45),
    ITEM_TYPE_SEMI_CE: (0.40, 0.90, 0.80),
    ITEM_TYPE_RAW_D: (0.80, 0.45, 0.90),
    ITEM_TYPE_PART_DE: (0.60, 0.30, 0.80),
    ITEM_TYPE_FINAL: (1.00, 0.80, 0.20),
}

# 工序转换: (工位, 输入类型) → 输出类型. 转换只在放置证据确认后提交,
# 是逻辑加工, 不宣称几何切削/装配.
PROCESS_TRANSITIONS = {
    ("A", ITEM_TYPE_RAW_A): ITEM_TYPE_SEMI_AC,
    ("A", ITEM_TYPE_SEMI_AB): ITEM_TYPE_SEMI_AC,
    ("B", ITEM_TYPE_RAW_B): ITEM_TYPE_SEMI_AB,
    ("C", ITEM_TYPE_SEMI_AC): ITEM_TYPE_SEMI_CE,
    ("D", ITEM_TYPE_RAW_D): ITEM_TYPE_PART_DE,
}

# 来源生成: 每种原料的入口线与初始类型.
SOURCE_ITEMS = {
    "s1a": {"line": "line_s1a", "position": base.MATERIAL_SPAWN_POS,
            "item_type": ITEM_TYPE_RAW_A},
    "s1b": {"line": "line_s1b",
            "position": (-0.75, -1.467,
                         base.CONVEYOR_SURFACE_Z
                         + base.MATERIAL_HALF_HEIGHT + base.MATERIAL_SPAWN_GAP),
            "item_type": ITEM_TYPE_RAW_B},
    "s2d": {"line": "line_s2d",
            "position": (-1.507, -2.45,
                         base.CONVEYOR_SURFACE_Z
                         + base.MATERIAL_HALF_HEIGHT + base.MATERIAL_SPAWN_GAP),
            "item_type": ITEM_TYPE_RAW_D},
}

# 区域容量 (WIP 上限). 每条线、每个交接/缓冲/载台一个占位.
ZONE_CAPACITY = {
    "line_s1a": 1, "line_s1b": 1, "line_s2d": 1, "line_ce": 1,
    "handoff_ac": 1,
    "buffer_e_c": 1, "buffer_e_d": 1,
    "agv": 1,
    "output_e": 4,
}

# E 岛配对契约: SEMI_CE + PART_DE → 1 FINAL.
JOIN_INPUTS = (ITEM_TYPE_SEMI_CE, ITEM_TYPE_PART_DE)
JOIN_OUTPUT = ITEM_TYPE_FINAL
JOIN_BUFFER_OF = {ITEM_TYPE_SEMI_CE: "buffer_e_c",
                  ITEM_TYPE_PART_DE: "buffer_e_d"}
JOIN_DUPLICATE_POLICY = "reject_replay_same_parent_pair"

# ═══════════════════════════════════════════════════════════════
# 资源表 (固定顺序; 全部一次性申请, 禁止持有后等待)
# ═══════════════════════════════════════════════════════════════

RESOURCE_ORDER = (
    "line_s1a",
    "line_s1b",
    "line_s2d",
    "line_ce",
    "handoff_ac",
    "buffer_e_c",
    "buffer_e_d",
    "dock_A",
    "dock_B",
    "dock_D",
    "dock_E",
    "agv_lane",
    "zone_ab",
    "zone_ac",
    "zone_ce",
    "arm_A",
    "arm_B",
    "arm_C",
    "arm_D",
    "arm_E",
)

RESOURCE_ROLES = {
    "line_s1a": "S1→A 传送带与取料区",
    "line_s1b": "S1→B 传送带与取料区",
    "line_s2d": "S2→D 传送带与取料区",
    "line_ce": "C→E 传送带 (C 放置 + E 取料)",
    "handoff_ac": "A→C 固定交接台 (A 放置 + C 取料)",
    "buffer_e_c": "E 岛 C 线输入缓冲",
    "buffer_e_d": "E 岛 D 线输入缓冲",
    "dock_A": "dock A 装卸窗口",
    "dock_B": "dock B 装卸窗口",
    "dock_D": "dock D 装卸窗口",
    "dock_E": "dock E 装卸窗口",
    "agv_lane": "AGV 直线通道 x=0.40, y∈[-2.45,2.45]",
    "zone_ab": "A/B 臂包络互锁区 (基座间距 < 2×reach)",
    "zone_ac": "A/C 臂包络互锁区",
    "zone_ce": "C/E 臂包络互锁区",
    "arm_A": "A 臂运动包络",
    "arm_B": "B 臂运动包络",
    "arm_C": "C 臂运动包络",
    "arm_D": "D 臂运动包络",
    "arm_E": "E 臂运动包络",
}

# 臂间互锁: 只有基座间距 < ADJACENCY_DISTANCE 的臂需要串行.
ARM_ADJACENCY_DISTANCE = 1.6
ARM_ADJACENCY_ZONES = {
    "zone_ab": ("A", "B"),
    "zone_ac": ("A", "C"),
    "zone_ce": ("C", "E"),
}

# AGV 路线要求全部 dock 空闲: 长距离直线穿过所有 dock, 任一臂在 dock 装卸
# 时都不允许车辆通过. 这不是碰撞证明的替代, 而是调度层的空间互斥.
AGV_ROUTE_REQUIRES_ALL_DOCKS = True

# ═══════════════════════════════════════════════════════════════
# 任务流、优先级与超时
# ═══════════════════════════════════════════════════════════════

TASK_KIND_A_CONVEYOR = "a_conveyor"
TASK_KIND_A_AGV = "a_agv"
TASK_KIND_B = "b_conveyor"
TASK_KIND_C = "c_handoff"
TASK_KIND_D = "d_conveyor"
TASK_KIND_E_CONVEYOR = "e_conveyor"
TASK_KIND_E_AGV = "e_agv"
TASK_KIND_TRANSPORT_BA = "transport_ba"
TASK_KIND_TRANSPORT_DE = "transport_de"
TASK_KIND_REPOSITION = "reposition"

TASK_KINDS = (
    TASK_KIND_A_CONVEYOR, TASK_KIND_A_AGV, TASK_KIND_B, TASK_KIND_C,
    TASK_KIND_D, TASK_KIND_E_CONVEYOR, TASK_KIND_E_AGV,
    TASK_KIND_TRANSPORT_BA, TASK_KIND_TRANSPORT_DE, TASK_KIND_REPOSITION,
)

# 数值越小优先级越高. 只有 AGV 调度读取 priority; 岛内派工使用 FIFO,
# 但 A/E 的 AGV 卸载任务优先于传送带/交接任务 (避免 AGV 被占死).
TASK_PRIORITY = {
    TASK_KIND_TRANSPORT_BA: 0,
    TASK_KIND_TRANSPORT_DE: 1,
    TASK_KIND_A_AGV: 0,
    TASK_KIND_E_AGV: 0,
    TASK_KIND_A_CONVEYOR: 1,
    TASK_KIND_B: 1,
    TASK_KIND_C: 1,
    TASK_KIND_D: 1,
    TASK_KIND_E_CONVEYOR: 1,
    TASK_KIND_REPOSITION: 2,
}

# D→E 低优先级等待超过该仿真时间后升级到与 B→A 相同; 升级只影响下一次
# 派车选择, 不抢占运行中的任务.
D_E_STARVATION_UPGRADE_S = 15.0

TASK_FLOW = (
    "S1→A: conveyor raw → A → handoff_ac",
    "S1→B: conveyor raw → B → AGV@dock_B",
    "B→A: AGV loaded dock_B → dock_A → A → handoff_ac",
    "A→C: handoff_ac → C → line_ce",
    "C→E: line_ce → E → buffer_e_c",
    "S2→D: conveyor raw → D → AGV@dock_D",
    "D→E: AGV loaded dock_D → dock_E → E → buffer_e_d",
    "E join: buffer_e_c + buffer_e_d → FINAL@E_Output",
)

TASK_TIMEOUT = 240.0                # 单任务仿真时间上限 (s)
TASK_WALL_BUDGET = 1200.0           # 单任务墙钟上限 (s)
TASK_MAX_ATTEMPTS = 1               # 阶段四不自动重试
QUEUE_CAPACITY = 32
DUPLICATE_REQUEST_POLICY = "reject_active_same_kind_uid"

# ═══════════════════════════════════════════════════════════════
# AGV 路线与停靠 (复用阶段三轮速驱动)
# ═══════════════════════════════════════════════════════════════

AGV_INITIAL_DOCK = "A"
AGV_ROUTE_X = s3cfg.AGV_ROUTE_X                 # 0.40
AGV_INITIAL_YAW = s3cfg.AGV_INITIAL_YAW         # -pi/2
AGV_ROUTE_SPEED = s3cfg.AGV_ROUTE_SPEED         # 0.12 m/s
AGV_ROUTE_RESUME_SPEED = s3cfg.AGV_ROUTE_RESUME_SPEED
AGV_ROUTE_ACCEL = s3cfg.AGV_ROUTE_ACCEL
AGV_ROUTE_DECEL = s3cfg.AGV_ROUTE_DECEL
AGV_ROUTE_HEADING_KP = s3cfg.AGV_ROUTE_HEADING_KP
AGV_ROUTE_MAX_YAW_RATE = s3cfg.AGV_ROUTE_MAX_YAW_RATE
AGV_ROUTE_ARRIVE_TOL = s3cfg.AGV_ROUTE_ARRIVE_TOL
AGV_ROUTE_HEADING_TOL = s3cfg.AGV_ROUTE_HEADING_TOL
AGV_ROUTE_STOP_VEL_TOL = s3cfg.AGV_ROUTE_STOP_VEL_TOL
AGV_ROUTE_SETTLE_STEPS = s3cfg.AGV_ROUTE_SETTLE_STEPS
AGV_ROUTE_SPEED_WINDOW_TOL = s3cfg.AGV_ROUTE_SPEED_WINDOW_TOL
AGV_ROUTE_BLOCK_TIMEOUT = s3cfg.AGV_ROUTE_BLOCK_TIMEOUT
AGV_ROUTE_BLOCK_DISPLACEMENT = s3cfg.AGV_ROUTE_BLOCK_DISPLACEMENT
AGV_ROUTE_SLIP_TOL = s3cfg.AGV_ROUTE_SLIP_TOL
AGV_ROUTE_RETRY_LIMIT = 0
# D→E 直线 4.90 m / 0.12 m/s ≈ 41 s; 计入加减速、停稳与一次
# AGV_BLOCK_CLEAR_AFTER_S 阻塞清除, 冻结 150 s. 该值由
# AGVRouteController4 显式传入阶段三路线控制器 (不能沿用其 45 s 默认).
AGV_ROUTE_TIMEOUT = 150.0
AGV_BLOCK_CLEAR_AFTER_S = s3cfg.AGV_BLOCK_CLEAR_AFTER_S
AGV_BLOCK_OBSTACLE_SIZE = s3cfg.AGV_BLOCK_OBSTACLE_SIZE
AGV_BLOCK_OBSTACLE_ALIAS = "Fault_Stage4_AGV_Obstacle"
AGV_BLOCK_OBSTACLE_POS = (AGV_ROUTE_X, 0.0, 0.15)

# 通道扫掠矩形余量 (布局静态筛选).
AGV_ROUTE_CLEARANCE = s3cfg.AGV_ROUTE_CLEARANCE
AGV_FOOTPRINT_AT_DOCK = s3cfg.AGV_FOOTPRINT_AT_DOCK
AGV_ROUTE_HALF_WIDTH = s3cfg.AGV_ROUTE_HALF_WIDTH

# ═══════════════════════════════════════════════════════════════
# 带速、放置与停带
# ═══════════════════════════════════════════════════════════════

LINE_SPEED = base.CONVEYOR_SPEED                # 0.20 m/s
LINE_ACCEL = base.CONVEYOR_ACCEL                # 2.0 m/s^2
LINE_STOP_TIMEOUT = 3.0                         # 放料前等待带面停止 (s)
LINE_STOP_VEL_TOL = 0.005                       # m/s
LINE_STOP_CONFIRM_STEPS = 2

# 取料区域 (只能由传感器命中触发停带): 停止区上游 0.20 m 到下游 0.03 m,
# 横向 ±0.04 m; 与阶段一步骤 1.1 B 组冻结口径一致.
PICK_REGION_UPSTREAM = 0.20
PICK_REGION_DOWNSTREAM = 0.03
PICK_REGION_LATERAL = 0.04
PICK_REGION_Z_TOL = 0.02

# 放置位置容差
MATERIAL_PLACE_TOL = base.MATERIAL_PLACE_TOL    # 0.03 m
AGV_TRAY_XY_TOL = base.AGV_TRAY_XY_TOL
AGV_TRAY_Z_TOL = base.AGV_TRAY_Z_TOL

# ═══════════════════════════════════════════════════════════════
# 有限批次、来源生成与背压
# ═══════════════════════════════════════════════════════════════

SOURCE_SPAWN_START_S = 0.0
SOURCE_SPAWN_INTERVAL_S = 0.5       # 同一来源两次生成的最小间隔
SOURCE_MAX_IN_FLIGHT = 1            # 每条入口线一次只放一件

# 场景批次: 每种原料的有限数量. 阶段四不运行无限供料.
SCENARIO_BATCH = {
    "full_flow": {"RAW_A": 1, "RAW_B": 1, "RAW_D": 1},
    "no_pair": {"RAW_A": 1, "RAW_B": 0, "RAW_D": 1},
    "agv_priority": {"RAW_A": 1, "RAW_B": 1, "RAW_D": 1},
    "agv_blocked": {"RAW_A": 1, "RAW_B": 1, "RAW_D": 1},
    "conveyor_fault": {"RAW_A": 1, "RAW_B": 0, "RAW_D": 1},
}

# 背压: 下游缓冲/交接/线占用时, 上游任务等待; 不丢弃 UID.
BACKPRESSURE_REASONS = {
    "handoff_ac": "A→C 交接台占用",
    "line_ce": "C→E 传送带占用",
    "buffer_e_c": "E 岛 C 线缓冲占用",
    "buffer_e_d": "E 岛 D 线缓冲占用",
    "dock_A": "dock A 占用",
    "dock_B": "dock B 占用",
    "dock_D": "dock D 占用",
    "dock_E": "dock E 占用",
    "agv": "AGV 载台占用",
}
BACKPRESSURE_MAX_WAIT_S = TASK_TIMEOUT

# ═══════════════════════════════════════════════════════════════
# 场景与故障注入
# ═══════════════════════════════════════════════════════════════

SCENARIO_FULL_FLOW = "full_flow"
SCENARIO_NO_PAIR = "no_pair"
SCENARIO_AGV_PRIORITY = "agv_priority"
SCENARIO_AGV_BLOCKED = "agv_blocked"
SCENARIO_CONVEYOR_FAULT = "conveyor_fault"

STAGE4_SCENARIOS = (
    SCENARIO_FULL_FLOW,
    SCENARIO_NO_PAIR,
    SCENARIO_AGV_PRIORITY,
    SCENARIO_AGV_BLOCKED,
    SCENARIO_CONVEYOR_FAULT,
)

FAULT_NONE = "none"
FAULT_AGV_BLOCKED = "agv_blocked"
FAULT_CONVEYOR_STOP = "conveyor_stop"
STAGE4_FAULT_CHOICES = (FAULT_NONE, FAULT_AGV_BLOCKED, FAULT_CONVEYOR_STOP)

SCENARIO_FAULT = {
    SCENARIO_FULL_FLOW: FAULT_NONE,
    SCENARIO_NO_PAIR: FAULT_NONE,
    SCENARIO_AGV_PRIORITY: FAULT_NONE,
    SCENARIO_AGV_BLOCKED: FAULT_AGV_BLOCKED,
    SCENARIO_CONVEYOR_FAULT: FAULT_CONVEYOR_STOP,
}

# 传送带故障: 在 C 第一次放置完成后的第一个观测步停带并保持零速.
CONVEYOR_FAULT_LINE = "line_ce"
CONVEYOR_FAULT_AFTER_TASK = TASK_KIND_C
CONVEYOR_FAULT_SETTLE_STEPS = 20

# 锁存后继续推进的步数 (沿用阶段三口径).
SAFETY_STOP_SETTLE_STEPS = s3cfg.SAFETY_STOP_SETTLE_STEPS

# 运行上限
DEFAULT_MAX_SIM_TIME = 900.0

# ═══════════════════════════════════════════════════════════════
# 可观测性
# ═══════════════════════════════════════════════════════════════

METRICS_WARMUP_S = 0.0              # 有限批次不做预热剔除, 记录即可
METRICS_WINDOW = "from_first_spawn_to_last_final"
EVENT_FIELDS = ("sim_time", "kind", "task_id", "uid", "item_type",
                "source", "destination", "device", "evidence_id")
EVIDENCE_TRACE_LIMIT = 2000

DEFAULT_LOG_FILE = "stage4_sim.log"
