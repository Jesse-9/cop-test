"""
factory_types.py — 工厂仿真类型定义与物料工具函数
==================================================
本模块定义岛式车间柔性生产仿真中所有枚举、数据类和共享常量，
以及物料的创建/删除工具函数。

对应架构层: Python Control Layer — 数据类型基础
参考文档:   SKILL.md §类型定义, references/scheduling.md §物料生命周期

Exports:
  Enum:       ItemType, AGVState, AGVTask, IslandState
  Dataclass:  Material, AGVJob, IslandBeatState
  Constants:  ITEM_COLOR_MAP
  Functions:  lerp_2d(), spawn_material(), remove_material()
"""

import math
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

import factory_config as cfg


# ═══════════════════════════════════════════════════════════════
# 类型定义 — Type Definitions
# ═══════════════════════════════════════════════════════════════

class ItemType(Enum):
    """物料类型 — 对应场景中不同工序的工件。

    Each value represents a distinct material stage in the production pipeline,
    from raw input (RAW_A/B/D) through intermediate semi-finished states
    (SEMI_AB/AC/CE) to the final assembled product (FINAL).
    """
    RAW_A   = auto()   # S1→A 原料 — raw material from warehouse S1 to island A
    RAW_B   = auto()   # S1→B 原料 — raw material from warehouse S1 to island B
    SEMI_AB = auto()   # B→A 半成品 — semi-finished, transferred B→A via AGV
    SEMI_AC = auto()   # A→C 半成品 — semi-finished, rigid transfer A→C
    RAW_D   = auto()   # S2→D 零部件 — auxiliary parts from warehouse S2 to island D
    SEMI_CE = auto()   # C→E 半成品 — semi-finished, conveyor C→E
    PART_DE = auto()   # D→E 专用件 — special part, AGV transport D→E
    FINAL   = auto()   # E 岛装配完成的最终成品


class AGVState(Enum):
    """AGV 柔性调度状态机 — AGV Flexible Scheduling State Machine.

    States correspond to the AGV state diagram in SKILL.md §AGV State Machine.
    The single shared AGV handles both B→A (high priority) and D→E (low priority,
    escalatable) transport tasks.
    """
    IDLE          = auto()   # 空闲，等待任务分配
    MOVING_B_TO_A = auto()   # 执行 B→A 高优先级转运 (可抢占 D→E)
    MOVING_D_TO_E = auto()   # 执行 D→E 低优先级转运 (可被 B→A 抢占)
    BLOCKED       = auto()   # 路径被占用，等待恢复或重规划


class AGVTask(Enum):
    """AGV 任务类型 — AGV Transport Task Types.

    B_TO_A: Cross-island replenishment, higher base priority (priority=0).
            Cannot be preempted once started.
    D_TO_E: Special path for custom/rework parts, lower base priority (priority=1).
            Can be preempted by B_TO_A. Escalates to priority=0 after
            D_E_STARVATION_THRESHOLD seconds of waiting.
    """
    B_TO_A = auto()   # 高优先级 — 跨岛补料 (priority=0, non-preemptable)
    D_TO_E = auto()   # 低优先级 — 特殊路径 (priority=1, preemptable, escalatable)


class IslandState(Enum):
    """工作岛状态机 — Work Island State Machine.

    Normal flow: IDLE → PROCESSING → COMPLETE → WAITING_FOR_AGV → (pickup) → IDLE
    Anomaly:     PROCESSING → TIMEOUT → (recovery) → IDLE
    Emergency:   any → OFFLINE
    """
    IDLE             = auto()   # 空闲，等待物料到达
    PROCESSING       = auto()   # 正在加工中
    COMPLETE         = auto()   # 加工完成，等待下游取料
    WAITING_FOR_AGV  = auto()   # 加工完成，等待AGV取货（新增状态）
    TIMEOUT          = auto()   # 节拍超时 — 上游供料暂停，自动降级
    OFFLINE          = auto()   # 手动或紧急停止，不接受任何物料


# ═══════════════════════════════════════════════════════════════
# 数据类 — Dataclasses
# ═══════════════════════════════════════════════════════════════

@dataclass
class Material:
    """工厂中的物料/工件 — A material/workpiece in the factory.

    Tracks the complete lifecycle of one item through the production pipeline:
    SPAWNED → ON_CONVEYOR → AT_ISLAND → PROCESSED → ON_AGV → ... → ASSEMBLED.

    Attributes:
        uid: 全局唯一标识符 (auto-increment).
        item_type: 当前物料类型 (随工序转换而改变).
        handle: CoppeliaSim 场景中的物体句柄 (-1 表示已删除).
        pos: 物料在场景中的当前位置 [x, y, z].
        on_conveyor: 所在传送带名称 (如 "S1_to_A"), None 表示不在传送带上.
        conv_progress: 在传送带上的进度 (0.0 → 1.0).
        on_agv: 所在 AGV 名称, None 表示不在 AGV 上.
        agv_progress: 在 AGV 运输路径上的进度 (0.0 → 1.0).
        at_island: 所在工作岛名称 (如 "A"), None 表示不在岛上.
        processing_remaining: 在岛上剩余的加工时间 (秒).
        delivered: True 表示物料已完成使命 (被消耗/装配/移除).
        history: 物料履历列表 [(sim_time, event_description), ...].
    """
    uid:       int
    item_type: ItemType
    handle:    int = -1
    pos:       list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])

    # 位置追踪 — Location tracking
    on_conveyor: Optional[str] = None
    conv_progress: float = 0.0
    on_agv:       Optional[str] = None
    agv_progress: float = 0.0
    at_island:    Optional[str] = None
    processing_remaining: float = 0.0
    delivered:    bool = False

    # 物料履历 — Event history for traceability
    history: list[tuple[float, str]] = field(default_factory=list)


@dataclass
class AGVJob:
    """AGV 调度任务 — A scheduled AGV transport job.

    Attributes:
        task: 任务类型 (B_TO_A 或 D_TO_E).
        priority: 优先级 (0=最高), 数值越大优先级越低.
        material_uid: 待运输的物料 UID.
        wait_start: 任务排队的仿真时间 (用于饥饿升级判断).
        preemptable: True 表示该任务可被更高优先级任务抢占.
    """
    task: AGVTask
    priority: int              # 0=最高, 越大越低
    material_uid: int
    wait_start: float = 0.0    # 排队开始时间 (simTime)
    preemptable: bool = True


@dataclass
class IslandBeatState:
    """单个工作岛的节拍追踪状态 — Per-island beat tracking state.

    Used by BeatTracker to monitor whether each island's actual processing
    time matches its TAKT_TIME target. Drift triggers warnings; sustained
    excess triggers auto-degradation.

    Attributes:
        island: 工作岛名称 (A/B/C/D/E).
        takt_target: 当前目标加工时间 (秒), 超时后会被自动延长.
        cycle_start: 当前加工周期开始的仿真时间.
        cycle_elapsed: 当前周期已累计的加工时间.
        deviation: 当前偏差 (elapsed - target, 可正可负).
        anomaly: True 表示当前处于超时/降级状态.
        normal_cycles: 连续正常周期计数 (用于自动恢复, 达到3则恢复).
    """
    island: str
    takt_target: float
    cycle_start: float = 0.0
    cycle_elapsed: float = 0.0
    deviation: float = 0.0
    anomaly: bool = False
    normal_cycles: int = 0


# ═══════════════════════════════════════════════════════════════
# 物料颜色映射 — Material Color Lookup Table
# ═══════════════════════════════════════════════════════════════
# 每种物料类型对应不同的 RGB 颜色，便于在场景中视觉追踪.
# See: references/scene-building.md §Material Color Coding

ITEM_COLOR_MAP = {
    ItemType.RAW_A:    cfg.MATERIAL_COLORS["raw_A"],
    ItemType.RAW_B:    cfg.MATERIAL_COLORS["raw_B"],
    ItemType.SEMI_AB:  cfg.MATERIAL_COLORS["semi_AB"],
    ItemType.SEMI_AC:  cfg.MATERIAL_COLORS["semi_AC"],
    ItemType.RAW_D:    cfg.MATERIAL_COLORS["raw_D"],
    ItemType.SEMI_CE:  cfg.MATERIAL_COLORS["semi_CE"],
    ItemType.PART_DE:  cfg.MATERIAL_COLORS["part_DE"],
    ItemType.FINAL:    cfg.MATERIAL_COLORS["final_E"],
}


# ═══════════════════════════════════════════════════════════════
# 工具函数 — Utility Functions
# ═══════════════════════════════════════════════════════════════

def lerp_2d(p1: tuple, p2: tuple, t: float) -> list[float]:
    """两点线性插值 — Linear interpolation between two 2D points.

    Args:
        p1: 起点 (x, y).
        p2: 终点 (x, y).
        t:  插值参数, 0.0=p1, 1.0=p2.

    Returns:
        [x, y] 插值结果.
    """
    return [p1[0] + (p2[0] - p1[0]) * t,
            p1[1] + (p2[1] - p1[1]) * t]


# 全局物料 UID 计数器 — Global material UID counter
_uid_counter = 0


def spawn_material(sim, item_type: ItemType,
                   position: list[float],
                   sim_time: float) -> Material:
    """在场景中创建物料方块并返回 Material 对象。

    Creates a colored cuboid in CoppeliaSim at the given position and wraps
    it in a Material dataclass for lifecycle tracking.

    Args:
        sim:       CoppeliaSim Remote API object (sim).
        item_type: 物料类型 (决定颜色和生命周期).
        position:  世界坐标 [x, y, z].
        sim_time:  当前仿真时间 (用于履历记录).

    Returns:
        Material 对象，已记录 SPAWNED 事件.
    """
    global _uid_counter
    _uid_counter += 1
    sx, sy, sz = cfg.MATERIAL_SIZE
    color = ITEM_COLOR_MAP.get(item_type, [1, 1, 1])
    handle = sim.createPrimitiveShape(sim.primitiveshape_cuboid, [sx, sy, sz])
    sim.setObjectPosition(handle, position, sim.handle_world)
    sim.setObjectColor(handle, 0, sim.colorcomponent_ambient_diffuse, list(color))
    mat = Material(uid=_uid_counter, item_type=item_type,
                   handle=handle, pos=position)
    mat.history.append((sim_time, f"SPAWNED as {item_type.name}"))
    return mat


def remove_material(sim, mat: Material):
    """从场景中删除物料 — Remove a material's visual from CoppeliaSim.

    Args:
        sim: CoppeliaSim Remote API object.
        mat: 待删除的 Material (其 handle 会被置为 -1).
    """
    if mat.handle != -1:
        sim.removeObject(mat.handle)
        mat.handle = -1
