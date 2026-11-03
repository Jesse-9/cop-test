"""rules.py — v1 领域规则常量 (纯 Python, 不导入 sim).

从已验收阶段四配置中提取 v1 实际使用的物料、任务、资源和指标规则。
数值与判据在迁移期间冻结, 修改必须伴随对照实验和版本递增.
"""

from __future__ import annotations

from ..spec import (MATERIAL_MODEL_VERSION, METRICS_VERSION,
                    RESOURCE_POLICY_VERSION, TASK_FLOW_VERSION)

# ═══════════════════════════════════════════════════════════════
# 物料类型、工序与区域容量
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

PROCESS_TRANSITIONS = {
    ("A", ITEM_TYPE_RAW_A): ITEM_TYPE_SEMI_AC,
    ("A", ITEM_TYPE_SEMI_AB): ITEM_TYPE_SEMI_AC,
    ("B", ITEM_TYPE_RAW_B): ITEM_TYPE_SEMI_AB,
    ("C", ITEM_TYPE_SEMI_AC): ITEM_TYPE_SEMI_CE,
    ("D", ITEM_TYPE_RAW_D): ITEM_TYPE_PART_DE,
}

# 五个工位 ID 与来源入口的稳定映射. 来源的物理生成位置属于场景配置,
# 不放进 domain; 这里只保留工艺校验需要的线/来料类型.
UNIT_IDS = ("A", "B", "C", "D", "E")

SOURCE_SPECS = {
    "s1a": {"line": "line_s1a", "item_type": ITEM_TYPE_RAW_A},
    "s1b": {"line": "line_s1b", "item_type": ITEM_TYPE_RAW_B},
    "s2d": {"line": "line_s2d", "item_type": ITEM_TYPE_RAW_D},
}

ZONE_CAPACITY = {
    "line_s1a": 1, "line_s1b": 1, "line_s2d": 1, "line_ce": 1,
    "handoff_ac": 1,
    "buffer_e_c": 1, "buffer_e_d": 1,
    "agv": 1,
    "output_e": 4,
}

JOIN_INPUTS = (ITEM_TYPE_SEMI_CE, ITEM_TYPE_PART_DE)
JOIN_OUTPUT = ITEM_TYPE_FINAL
JOIN_BUFFER_OF = {ITEM_TYPE_SEMI_CE: "buffer_e_c",
                  ITEM_TYPE_PART_DE: "buffer_e_d"}
JOIN_DUPLICATE_POLICY = "reject_replay_same_parent_pair"


# ═══════════════════════════════════════════════════════════════
# 资源表 (固定顺序; 全部一次性申请)
# ═══════════════════════════════════════════════════════════════

RESOURCE_ORDER = (
    "line_s1a",
    "line_s1b",
    "line_s2d",
    "line_ce",
    "handoff_ac",
    "buffer_e_c",
    "buffer_e_d",
    "agv_lane_west",
    "agv_lane_south",
    "agv_lane_east",
    "dock_A",
    "dock_B",
    "dock_D",
    "dock_E",
    "zone_ab",
    "zone_ac",
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
    "agv_lane_west": "AGV 西直道 x=0.40, y∈[-2.10,0.70]",
    "agv_lane_south": "AGV 南横道 y=-2.10, x∈[0.40,2.90]",
    "agv_lane_east": "AGV 东直道 x=2.90, y∈[-2.10,1.35]",
    "dock_A": "dock A 装卸窗口",
    "dock_B": "dock B 装卸窗口",
    "dock_D": "dock D 装卸窗口",
    "dock_E": "dock E 装卸窗口",
    "zone_ab": "A/B 臂包络互锁区 (基座间距 < 2×reach)",
    "zone_ac": "A/C 臂包络互锁区",
    "arm_A": "A 臂运动包络",
    "arm_B": "B 臂运动包络",
    "arm_C": "C 臂运动包络",
    "arm_D": "D 臂运动包络",
    "arm_E": "E 臂运动包络",
}


# ═══════════════════════════════════════════════════════════════
# 任务流、优先级与去重
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

TASK_TIMEOUT = 300.0
TASK_WALL_BUDGET = 1500.0
TASK_MAX_ATTEMPTS = 1
QUEUE_CAPACITY = 32
DUPLICATE_REQUEST_POLICY = "reject_active_same_kind_uid"


# ═══════════════════════════════════════════════════════════════
# 指标
# ═══════════════════════════════════════════════════════════════

METRICS_WARMUP_S = 0.0
METRICS_WINDOW = "from_first_spawn_to_last_final"
