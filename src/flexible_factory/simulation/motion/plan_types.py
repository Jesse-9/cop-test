"""plan_types.py — 计划状态、预算与结果数据结构.

从 :mod:`path_planning` 拆出的纯数据类型; 旧模块保留 re-export.
本模块不启动仿真、不调用规划器, 拆分不改变任何算法或阈值.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .. import config_motion as cfg
from .collisions import QueryResult
from .contact import ContactEvaluation, LocalBox


class PlanStatus(str, Enum):
    OK = "OK"
    DIRECT_OK = "DIRECT_OK"
    AVOIDED = "AVOIDED"
    START_CONFIG_INVALID = "START_CONFIG_INVALID"
    GOAL_CONFIG_INVALID = "GOAL_CONFIG_INVALID"
    IK_FAILED = "IK_FAILED"
    LIMITS_EXCEEDED = "LIMITS_EXCEEDED"
    COLLISION_AT_GOAL = "COLLISION_AT_GOAL"
    PATH_COLLISION = "PATH_COLLISION"
    PATH_DEVIATION = "PATH_DEVIATION"
    NEAR_COLLISION = "NEAR_COLLISION"
    TARGET_CONTACT_VIOLATION = "TARGET_CONTACT_VIOLATION"
    QUERY_UNKNOWN = "QUERY_UNKNOWN"
    LOCAL_AVOIDANCE_FAILED = "LOCAL_AVOIDANCE_FAILED"
    PLAN_BUDGET_EXCEEDED = "PLAN_BUDGET_EXCEEDED"
    WRIST_BRANCH_MISMATCH = "WRIST_BRANCH_MISMATCH"
    WRIST_SINGULARITY = "WRIST_SINGULARITY"
    JOINT_LOCK_UNAVAILABLE = "JOINT_LOCK_UNAVAILABLE"


_OK_STATUSES = frozenset({PlanStatus.OK, PlanStatus.DIRECT_OK,
                          PlanStatus.AVOIDED})
_FAILURE_PRIORITY = {
    PlanStatus.QUERY_UNKNOWN: 100,
    PlanStatus.TARGET_CONTACT_VIOLATION: 90,
    PlanStatus.PATH_COLLISION: 80,
    PlanStatus.PATH_DEVIATION: 78,
    PlanStatus.GOAL_CONFIG_INVALID: 75,
    PlanStatus.START_CONFIG_INVALID: 70,
    PlanStatus.WRIST_SINGULARITY: 68,
    PlanStatus.WRIST_BRANCH_MISMATCH: 65,
    PlanStatus.NEAR_COLLISION: 60,
    PlanStatus.LIMITS_EXCEEDED: 55,
    PlanStatus.JOINT_LOCK_UNAVAILABLE: 52,
    PlanStatus.IK_FAILED: 50,
    PlanStatus.PLAN_BUDGET_EXCEEDED: 40,
    PlanStatus.LOCAL_AVOIDANCE_FAILED: 30,
}


def failure_priority(status: PlanStatus) -> int:
    """失败状态优先级 (多路径变体失败时保留信息量最大的一条)."""
    return _FAILURE_PRIORITY.get(status, 0)


class PlanBudgetExceeded(RuntimeError):
    pass


class PlanBudget:
    """采样数与墙钟预算; 超限必须返回“验证未完成”, 不能截断当通过."""

    def __init__(self, wall_s: float = cfg.PLAN_WALL_BUDGET_S,
                 max_samples: int = cfg.PLAN_MAX_TOTAL_SAMPLES):
        self.started = time.monotonic()
        self.wall_s = float(wall_s)
        self.max_samples = int(max_samples)
        self.samples = 0
        self.configs = 0

    def tick(self, configs: int = 1) -> None:
        self.configs += int(configs)
        self.samples += int(configs)
        if self.samples > self.max_samples:
            raise PlanBudgetExceeded(
                f"采样数 {self.samples} > {self.max_samples}")
        if time.monotonic() - self.started > self.wall_s:
            raise PlanBudgetExceeded(
                f"墙钟 {time.monotonic() - self.started:.2f}s > {self.wall_s}s")

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started


# ═══════════════════════════════════════════════════════════════
# 上下文与结果
# ═══════════════════════════════════════════════════════════════

@dataclass
class MotionContext:
    phase: str
    target_handle: int = -1
    material_pose: Optional[list[float]] = None
    material_box: Optional[LocalBox] = None
    carrying: bool = False
    cup_radius: float = cfg.CUP_RADIUS_DESIGN
    snapshot: str = ""
    goal_pose: Optional[list[float]] = None
    allow_target_contact: bool = False
    final_contact: bool = False
    # 抬升起点: 已吸附物料仍与承载面正常接触, 只允许该起点跳过载荷检查
    allow_start_load_contact: bool = False
    allow_final_load_contact: bool = False
    # 阶段 2.01: 腕部分支锚点; 所有候选/段/执行构型必须同分支且远离奇异
    branch: Optional[str] = None
    branch_margin: float = cfg.WRIST_BRANCH_MARGIN

    def with_flags(self, **kwargs) -> "MotionContext":
        data = {
            "phase": self.phase,
            "target_handle": self.target_handle,
            "material_pose": self.material_pose,
            "material_box": self.material_box,
            "carrying": self.carrying,
            "cup_radius": self.cup_radius,
            "snapshot": self.snapshot,
            "goal_pose": self.goal_pose,
            "allow_target_contact": self.allow_target_contact,
            "final_contact": self.final_contact,
            "allow_start_load_contact": self.allow_start_load_contact,
            "allow_final_load_contact": self.allow_final_load_contact,
            "branch": self.branch,
            "branch_margin": self.branch_margin,
        }
        data.update(kwargs)
        return MotionContext(**data)

    def as_dict(self) -> dict:
        return {
            "phase": self.phase,
            "target_handle": self.target_handle,
            "carrying": self.carrying,
            "snapshot": self.snapshot,
            "branch": self.branch,
        }


@dataclass
class Goal:
    name: str
    pose: list[float]
    cartesian: bool = False
    allow_target_contact: bool = False
    final_contact: bool = False
    strict_orientation: bool = False
    # 最终放置点: 载荷底面与承载面正常接触, 只允许该端点跳过载荷检查
    allow_final_load_contact: bool = False
    # 阶段 2.01: 段内固定关节 (值取段起始构型); None 表示自由
    lock_joint: Optional[int] = None
    lock_tol: float = cfg.Q0_LOCK_TOL
    # 强制本段使用指定 IK 构型 (调用方须已完成该构型的下游链复核);
    # None 表示按候选搜索. 规划仍逐段做同一碰撞/分支/姿态检查.
    forced_q: Optional[list[float]] = None


@dataclass
class ConfigCheck:
    status: PlanStatus
    q: list[float]
    tcp: Optional[list[float]] = None
    link_poses: list[list[float]] = field(default_factory=list)
    results: list[QueryResult] = field(default_factory=list)
    contact: Optional[ContactEvaluation] = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status in _OK_STATUSES

    def as_dict(self) -> dict:
        return {
            "status": self.status.value,
            "q": [round(float(v), 6) for v in self.q],
            "tcp": None if self.tcp is None
            else [round(float(v), 6) for v in self.tcp[:3]],
            "results": [r.as_dict() for r in self.results],
            "contact": self.contact.as_dict() if self.contact else None,
            "message": self.message,
        }


@dataclass
class SegmentCheck:
    status: PlanStatus
    q_start: list[float]
    q_end: list[float]
    samples: int = 0
    first_failure: Optional[ConfigCheck] = None
    message: str = ""
    min_distance: Optional[float] = None

    @property
    def ok(self) -> bool:
        return self.status in _OK_STATUSES

    def as_dict(self) -> dict:
        return {
            "status": self.status.value,
            "samples": self.samples,
            "first_failure": (self.first_failure.as_dict()
                              if self.first_failure else None),
            "message": self.message,
            "min_distance": None if self.min_distance is None
            else round(self.min_distance, 6),
        }


@dataclass
class MotionSegment:
    name: str
    q_start: list[float]
    q_end: list[float]
    cartesian: bool = False
    samples: int = 0
    # 阶段 2.01: 该段新增的路径点数 (不含段起点), 用于执行器分段统计.
    waypoint_count: int = 1
    target_pose: Optional[list[float]] = None
    lock_joint: Optional[int] = None
    lock_value: Optional[float] = None
    lock_tol: Optional[float] = None
    verify_samples: int = 0
    check_status: str = ""
    min_distance: Optional[float] = None

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "cartesian": self.cartesian,
            "q_start": [round(float(v), 6) for v in self.q_start],
            "q_end": [round(float(v), 6) for v in self.q_end],
            "samples": self.samples,
            "waypoint_count": self.waypoint_count,
            "lock_joint": self.lock_joint,
            "lock_value": (None if self.lock_value is None
                           else round(float(self.lock_value), 6)),
            "lock_tol": self.lock_tol,
            "verify_samples": self.verify_samples,
            "check_status": self.check_status,
            "min_distance": (None if self.min_distance is None
                             else round(float(self.min_distance), 6)),
        }


@dataclass
class MotionPlan:
    status: PlanStatus
    waypoints: list[list[float]] = field(default_factory=list)
    segments: list[MotionSegment] = field(default_factory=list)
    contexts: list[MotionContext] = field(default_factory=list)
    message: str = ""
    samples: int = 0
    configs: int = 0
    wall_s: float = 0.0
    attempts: list[dict] = field(default_factory=list)
    goal_pose: Optional[list[float]] = None
    final_contact: bool = False
    allow_final_load_contact: bool = False
    strict_orientation: bool = False
    strategy: str = ""
    branch_anchor: Optional[str] = None
    lock_policy: str = ""
    locked_segments: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    compact: dict = field(default_factory=dict)
    # 阶段 2.02 (06-10 §8): 转运路径类型、整链搜索、平滑与逐点诊断.
    transfer_path: str = ""
    transfer_variants: list[dict] = field(default_factory=list)
    chain_search: dict = field(default_factory=dict)
    smoothing: dict = field(default_factory=dict)
    diagnostics: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in _OK_STATUSES

    def as_dict(self) -> dict:
        return {
            "status": self.status.value,
            "strategy": self.strategy,
            "branch_anchor": self.branch_anchor,
            "lock_policy": self.lock_policy,
            "locked_segments": list(self.locked_segments),
            "transfer_path": self.transfer_path,
            "transfer_variants": self.transfer_variants,
            "chain_search": self.chain_search,
            "smoothing": self.smoothing,
            "diagnostics": self.diagnostics,
            "waypoints": [[round(float(v), 6) for v in q]
                          for q in self.waypoints],
            "segments": [s.as_dict() for s in self.segments],
            "message": self.message,
            "samples": self.samples,
            "configs": self.configs,
            "wall_s": round(self.wall_s, 3),
            "attempts": self.attempts,
            "goal_pose": (None if self.goal_pose is None
                          else [round(float(v), 6) for v in self.goal_pose]),
            "final_contact": self.final_contact,
            "strict_orientation": self.strict_orientation,
            "metrics": self.metrics,
            "compact": self.compact,
        }

    def summary_dict(self) -> dict:
        """证据用摘要: 保留逐段统计, 不重复完整路径点."""
        data = self.as_dict()
        data.pop("waypoints", None)
        return data

    def segment_spans(self) -> list[tuple[int, int, MotionSegment]]:
        """返回每个段的 ``(起点下标, 终点下标, 段)``."""
        spans = []
        start = 0
        for segment in self.segments:
            count = max(0, int(segment.waypoint_count))
            end = min(start + count, len(self.waypoints) - 1)
            spans.append((start, end, segment))
            start = end
        return spans

